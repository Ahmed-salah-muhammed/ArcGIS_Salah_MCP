using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>add_layer</c> — params <c>{ path }</c> → <c>{ added, map }</c>. Adds a
    /// dataset from disk (feature class, shapefile, raster, layer file, service
    /// URL …) to the active map.
    /// </summary>
    public sealed class AddLayerCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string path = ParamUtil.GetString(@params, "path")
                    ?? throw new ArgumentException("add_layer requires 'path'.");

                var map = MapView.Active?.Map
                    ?? throw new InvalidOperationException("No active map view.");

                var uri = new Uri(path);
                var layer = LayerFactory.Instance.CreateLayer(uri, map);

                return new Dictionary<string, object>
                {
                    ["added"] = layer?.Name,
                    ["map"] = map.Name,
                };
            });
    }
}
