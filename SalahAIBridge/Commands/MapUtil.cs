using System;
using System.Collections.Generic;
using System.Linq;

using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// Shared map/layer lookups for the command handlers. Every method here reads
    /// the application object model, so callers MUST already be on the Main CIM
    /// Thread (inside <c>QueuedTask.Run</c>).
    /// </summary>
    internal static class MapUtil
    {
        /// <summary>The named map, or the active view's map when <paramref name="mapName"/> is blank.</summary>
        public static Map ResolveMap(string mapName)
        {
            if (string.IsNullOrWhiteSpace(mapName))
            {
                var active = MapView.Active?.Map;
                if (active == null)
                    throw new InvalidOperationException("No active map view.");
                return active;
            }

            var item = Project.Current?
                .GetItems<MapProjectItem>()
                .FirstOrDefault(m => string.Equals(m.Name, mapName, StringComparison.OrdinalIgnoreCase));
            if (item == null)
                throw new InvalidOperationException($"Map '{mapName}' not found.");
            return item.GetMap();
        }

        public static Layer FindLayer(Map map, string name)
        {
            var layer = map.GetLayersAsFlattenedList()
                .FirstOrDefault(l => string.Equals(l.Name, name, StringComparison.OrdinalIgnoreCase));
            if (layer == null)
                throw new InvalidOperationException($"Layer '{name}' not found in map '{map.Name}'.");
            return layer;
        }

        public static FeatureLayer FindFeatureLayer(Map map, string name)
        {
            if (FindLayer(map, name) is FeatureLayer fl)
                return fl;
            throw new InvalidOperationException($"Layer '{name}' is not a feature layer.");
        }

        /// <summary>Serialize an envelope to the {xmin,ymin,xmax,ymax,wkid} shape used in replies.</summary>
        public static Dictionary<string, object> ExtentDict(Envelope e) => new()
        {
            ["xmin"] = e.XMin,
            ["ymin"] = e.YMin,
            ["xmax"] = e.XMax,
            ["ymax"] = e.YMax,
            ["wkid"] = e.SpatialReference?.Wkid,
        };
    }
}
