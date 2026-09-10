using System;
using System.Net.Http;
using System.Threading.Tasks;

using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace SalahAIBridge
{
    /// <summary>
    /// Salah_MCP_PingBtn — liveness check against the running bridge: GET /health on
    /// the loopback listener and show the response. Loopback-only, no token.
    /// </summary>
    internal class PingButton : Button
    {
        private static readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };

        protected override async void OnClick()
        {
            string url = $"http://127.0.0.1:{ReadPort()}/health";
            try
            {
                using var resp = await _http.GetAsync(url);
                string body = await resp.Content.ReadAsStringAsync();
                MessageBox.Show(
                    $"GET {url}\n\nHTTP {(int)resp.StatusCode} {resp.StatusCode}\n{body}",
                    "Salah MCP — Ping");
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Bridge unreachable at {url}\n{ex.Message}\n\nIs the server started (Start Server button)?",
                    "Salah MCP — Ping");
            }
        }

        private static int ReadPort() =>
            int.TryParse(Environment.GetEnvironmentVariable("ARCGIS_BRIDGE_PORT"), out var p) && p > 0 ? p : 2026;
    }
}
