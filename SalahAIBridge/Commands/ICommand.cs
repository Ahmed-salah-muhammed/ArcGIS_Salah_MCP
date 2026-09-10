using System.Text.Json;
using System.Threading.Tasks;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// One live-session bridge command. Implementations receive the request's <c>params</c>
    /// JSON element and return the payload that becomes the envelope's <c>data</c>.
    /// Throw on failure — <see cref="BridgeServer"/> converts the exception into
    /// <c>{"ok": false, "error": ..., "type": ...}</c>.
    ///
    /// CRITICAL: any access to the project, maps, layouts or views MUST run inside
    /// <c>QueuedTask.Run(...)</c> (the Main CIM Thread). The HTTP handler runs on a
    /// worker thread; never touch CIM/MapView/Project off the MCT. See
    /// docs/PROTOCOL.md "Execution rule".
    /// </summary>
    public interface ICommand
    {
        Task<object> ExecuteAsync(JsonElement @params);
    }
}
