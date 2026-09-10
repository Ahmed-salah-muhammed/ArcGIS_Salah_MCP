using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>zoom_to</c> — params <c>{ layer, selection? }</c> → <c>{ layer, extent }</c>.
    /// Zooms the active view to a layer, or to that layer's current selection when
    /// <c>selection</c> is true.
    /// </summary>
    public sealed class ZoomToCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string layerName = ParamUtil.GetString(@params, "layer")
                    ?? throw new ArgumentException("zoom_to requires 'layer'.");
                bool selection = ParamUtil.GetBool(@params, "selection");

                var mapView = MapView.Active
                    ?? throw new InvalidOperationException("No active map view.");
                var layer = MapUtil.FindLayer(mapView.Map, layerName);

                Envelope extent;
                if (selection)
                {
                    var fl = layer as FeatureLayer
                        ?? throw new InvalidOperationException($"Layer '{layerName}' is not a feature layer.");
                    extent = SelectionExtent(fl, layerName);
                }
                else if (layer is BasicFeatureLayer bfl)
                {
                    extent = bfl.QueryExtent();
                }
                else
                {
                    throw new InvalidOperationException(
                        $"zoom_to supports feature layers; '{layerName}' is a {layer.GetType().Name}.");
                }

                mapView.ZoomTo(extent, TimeSpan.FromSeconds(1.0));

                return new Dictionary<string, object>
                {
                    ["layer"] = layer.Name,
                    ["extent"] = MapUtil.ExtentDict(extent),
                };
            });

        /// <summary>Union the geometries of the layer's currently selected features.</summary>
        private static Envelope SelectionExtent(FeatureLayer fl, string layerName)
        {
            var sel = fl.GetSelection();
            if (sel == null || sel.GetCount() == 0)
                throw new InvalidOperationException($"Layer '{layerName}' has no selected features.");

            var qf = new QueryFilter { ObjectIDs = sel.GetObjectIDs() };
            Envelope env = null;
            using (var cursor = fl.Search(qf))
            {
                while (cursor.MoveNext())
                    using (var feat = cursor.Current as Feature)
                    {
                        var shape = feat?.GetShape();
                        if (shape == null) continue;
                        env = env == null ? shape.Extent : env.Union(shape.Extent);
                    }
            }

            return env ?? throw new InvalidOperationException("Could not compute the selection extent.");
        }
    }
}
