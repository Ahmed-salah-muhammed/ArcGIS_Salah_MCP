using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;

using SalahAIBridge.Commands;

namespace SalahAIBridge
{
    /// <summary>
    /// Loopback-only HTTP listener that exposes the open ArcGIS Pro session to the
    /// MCP server. One endpoint, <c>POST /cmd</c>, plus a <c>GET /health</c> for
    /// liveness. Loopback-only (127.0.0.1), no token — local use. This class only
    /// does transport, JSON (de)serialization and routing; each <see cref="ICommand"/>
    /// handler runs its Pro-SDK work on the Main CIM Thread via <c>QueuedTask.Run(...)</c>.
    ///
    /// Wire contract: docs/PROTOCOL.md. Every reply is the universal envelope
    /// <c>{"ok": true, "data": ...}</c> or <c>{"ok": false, "error": ...}</c>, so
    /// the agent sees one contract across all layers.
    /// </summary>
    public sealed class BridgeServer
    {
        /// <summary>Bumped on breaking protocol changes; surfaced by <c>ping</c>.</summary>
        public const string ProtocolVersion = "1";

        private readonly int _port;
        private readonly Dictionary<string, ICommand> _commands;

        private HttpListener _listener;
        private CancellationTokenSource _cts;

        private static readonly JsonSerializerOptions JsonOpts = new()
        {
            // Payload keys are authored snake_case already — don't rename them.
            PropertyNamingPolicy = null,
            DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        };

        public BridgeServer(int port)
        {
            _port = port;
            _commands = new Dictionary<string, ICommand>(StringComparer.OrdinalIgnoreCase)
            {
                ["ping"]          = new PingCommand(),
                ["list_layers"]   = new ListLayersCommand(),
                ["zoom_to"]       = new ZoomToCommand(),
                ["query"]         = new QueryCommand(),
                ["run_gp"]        = new RunGpCommand(),
                ["add_layer"]     = new AddLayerCommand(),
                ["export_layout"] = new ExportLayoutCommand(),
                ["get_request"]   = new GetRequestCommand(),
            };
        }

        public bool IsListening => _listener?.IsListening == true;

        /// <summary>Bind the loopback prefix and start the accept loop.</summary>
        public void Start()
        {
            if (IsListening) return;

            _listener = new HttpListener();
            // Loopback only — never a routable interface. Binding a concrete
            // 127.0.0.1 host (not '+'/'*') avoids needing an admin URL ACL.
            _listener.Prefixes.Add($"http://127.0.0.1:{_port}/");
            _listener.Start();

            _cts = new CancellationTokenSource();
            _ = Task.Run(() => AcceptLoopAsync(_cts.Token));
        }

        /// <summary>Stop accepting and release the listener. Best effort.</summary>
        public void Stop()
        {
            try { _cts?.Cancel(); } catch { /* ignore */ }
            try { _listener?.Stop(); } catch { /* ignore */ }
            try { _listener?.Close(); } catch { /* ignore */ }
            _listener = null;
        }

        private async Task AcceptLoopAsync(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested && _listener is { IsListening: true })
            {
                HttpListenerContext ctx;
                try
                {
                    ctx = await _listener.GetContextAsync().ConfigureAwait(false);
                }
                catch
                {
                    // Listener stopped/disposed -> leave the loop.
                    break;
                }

                // Fan out so one slow handler can't block accepting the next request.
                _ = Task.Run(() => HandleAsync(ctx));
            }
        }

        private async Task HandleAsync(HttpListenerContext ctx)
        {
            try
            {
                var path = ctx.Request.Url?.AbsolutePath ?? "/";

                // --- GET /health : liveness -----------------------------------
                if (ctx.Request.HttpMethod == "GET" &&
                    path.Equals("/health", StringComparison.OrdinalIgnoreCase))
                {
                    WriteJson(ctx, 200, Ok("alive"));
                    return;
                }

                // --- only POST /cmd beyond this point -------------------------
                if (ctx.Request.HttpMethod != "POST" ||
                    !path.Equals("/cmd", StringComparison.OrdinalIgnoreCase))
                {
                    WriteJson(ctx, 404, Err("not found"));
                    return;
                }

                string body;
                using (var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
                    body = await reader.ReadToEndAsync().ConfigureAwait(false);

                JsonElement root;
                try
                {
                    using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
                    // Clone so the element stays valid after the document is disposed.
                    root = doc.RootElement.Clone();
                }
                catch (JsonException jx)
                {
                    WriteJson(ctx, 400, Err($"bad request: {jx.Message}"));
                    return;
                }

                string command = root.TryGetProperty("command", out var c) ? c.GetString() : null;
                if (string.IsNullOrWhiteSpace(command))
                {
                    WriteJson(ctx, 400, Err("bad request: missing 'command'"));
                    return;
                }

                JsonElement @params = root.TryGetProperty("params", out var p) ? p : default;

                if (!_commands.TryGetValue(command, out var handler))
                {
                    WriteJson(ctx, 200, Err($"unknown command '{command}'"));
                    return;
                }

                try
                {
                    object data = await handler.ExecuteAsync(@params).ConfigureAwait(false);
                    WriteJson(ctx, 200, Ok(data));
                }
                catch (Exception hx)
                {
                    // Handler threw (e.g. "Layer 'cities' not found.") -> app-level
                    // error envelope, still HTTP 200 per the protocol.
                    WriteJson(ctx, 200, Err(hx.Message, hx.GetType().Name));
                }
            }
            catch (Exception ex)
            {
                try { WriteJson(ctx, 500, Err(ex.Message, ex.GetType().Name)); }
                catch { /* client went away */ }
            }
        }

        // --- envelope builders ---------------------------------------------------

        private static Dictionary<string, object> Ok(object data) =>
            new() { ["ok"] = true, ["data"] = data };

        private static Dictionary<string, object> Err(string message, string type = null)
        {
            var d = new Dictionary<string, object> { ["ok"] = false, ["error"] = message };
            if (type != null) d["type"] = type;
            return d;
        }

        private static void WriteJson(HttpListenerContext ctx, int status, object envelope)
        {
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(envelope, JsonOpts);
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "application/json; charset=utf-8";
            ctx.Response.ContentLength64 = bytes.Length;
            using var os = ctx.Response.OutputStream;
            os.Write(bytes, 0, bytes.Length);
        }
    }
}
