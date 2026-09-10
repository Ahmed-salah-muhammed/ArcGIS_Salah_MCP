using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Framework.Threading.Tasks;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>list_layers</c> — params <c>{ map? }</c> → <c>{ map, layers:
    /// [{name, type, visible}] }</c>. Lists the active (or named) map's layers.
    /// </summary>
    public sealed class ListLayersCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                var map = MapUtil.ResolveMap(ParamUtil.GetString(@params, "map"));

                var layers = map.GetLayersAsFlattenedList()
                    .Select(l => new Dictionary<string, object>
                    {
                        ["name"] = l.Name,
                        ["type"] = l.GetType().Name,   // e.g. FeatureLayer, RasterLayer, GroupLayer
                        ["visible"] = l.IsVisible,
                    })
                    .ToList();

                return new Dictionary<string, object>
                {
                    ["map"] = map.Name,
                    ["layers"] = layers,
                };
            });
    }
}
