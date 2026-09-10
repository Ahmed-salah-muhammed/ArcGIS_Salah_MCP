using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>ping</c> — cheap health + context read of the open session: the project
    /// name/path, its maps and layouts, and the active view. Safe to call often.
    /// All Pro-SDK reads run on the Main CIM Thread.
    /// </summary>
    public sealed class PingCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params)
        {
            return QueuedTask.Run<object>(() =>
            {
                var project = Project.Current;

                var maps = project?
                    .GetItems<MapProjectItem>()
                    .Select(m => m.Name)
                    .ToList() ?? new List<string>();

                var layouts = project?
                    .GetItems<LayoutProjectItem>()
                    .Select(l => l.Name)
                    .ToList() ?? new List<string>();

                // Prefer the active map view's map name; fall back to the active
                // pane's caption (e.g. when a layout view is in front).
                string activeView = MapView.Active?.Map?.Name
                                    ?? FrameworkApplication.Panes.ActivePane?.Caption;

                return new Dictionary<string, object>
                {
                    ["protocol_version"] = BridgeServer.ProtocolVersion,
                    ["project"] = project?.Name,
                    ["project_path"] = project?.Path,
                    ["maps"] = maps,
                    ["layouts"] = layouts,
                    ["active_view"] = activeView,
                };
            });
        }
    }
}
