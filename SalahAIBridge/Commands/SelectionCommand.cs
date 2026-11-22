using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Core.Data;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>selection</c> (add-in 0.1.7) — what the user has selected in the open map, and changing it.
    /// params <c>{ action: get|select|clear, layer?, where?, method?, fields?, limit?, map? }</c>.
    /// <list type="bullet">
    /// <item><c>get</c> → <c>{ map, total, layers: [{ layer, count, object_ids[] }] }</c>; with <c>layer</c>
    /// also <c>fields</c> + <c>rows[]</c> of that layer's selected features (up to <c>limit</c>).</item>
    /// <item><c>select</c> → selects by <c>where</c> in <c>layer</c>; <c>method</c> is new (default) | add |
    /// remove | and. Returns <c>{ layer, selected }</c>.</item>
    /// <item><c>clear</c> → clears <c>layer</c>'s selection, or the whole map's.</item>
    /// </list>
    /// A selection changes no data, so it is allowed in read-only mode (like zoom_to).
    /// </summary>
    public sealed class SelectionCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string action = (ParamUtil.GetString(@params, "action") ?? "get").ToLowerInvariant();
                string layerName = ParamUtil.GetString(@params, "layer");
                int limit = Math.Max(1, ParamUtil.GetInt(@params, "limit", 50));
                var map = MapUtil.ResolveMap(ParamUtil.GetString(@params, "map"));

                if (action == "select")
                {
                    if (string.IsNullOrWhiteSpace(layerName))
                        throw new ArgumentException("selection select requires 'layer'.");
                    var fl = MapUtil.FindFeatureLayer(map, layerName);
                    var method = (ParamUtil.GetString(@params, "method") ?? "new").ToLowerInvariant() switch
                    {
                        "new" => SelectionCombinationMethod.New,
                        "add" => SelectionCombinationMethod.Add,
                        "remove" => SelectionCombinationMethod.Subtract,
                        "and" => SelectionCombinationMethod.And,
                        var m => throw new ArgumentException($"method must be new | add | remove | and, not '{m}'."),
                    };
                    var qf = new QueryFilter { WhereClause = ParamUtil.GetString(@params, "where") ?? "" };
                    using var selection = fl.Select(qf, method);
                    return new Dictionary<string, object>
                    {
                        ["layer"] = fl.Name,
                        ["selected"] = selection.GetCount(),
                    };
                }

                if (action == "clear")
                {
                    if (string.IsNullOrWhiteSpace(layerName))
                    {
                        map.ClearSelection();
                        return new Dictionary<string, object> { ["cleared"] = "every layer of " + map.Name };
                    }
                    MapUtil.FindFeatureLayer(map, layerName).ClearSelection();
                    return new Dictionary<string, object> { ["cleared"] = layerName };
                }

                if (action != "get")
                    throw new ArgumentException($"action must be get | select | clear, not '{action}'.");

                var layers = new List<Dictionary<string, object>>();
                long total = 0;
                foreach (var pair in map.GetSelection().ToDictionary())
                {
                    total += pair.Value.Count;
                    layers.Add(new Dictionary<string, object>
                    {
                        ["layer"] = pair.Key.Name,
                        ["count"] = pair.Value.Count,
                        ["object_ids"] = pair.Value.Take(limit).ToList(),
                    });
                }
                var result = new Dictionary<string, object>
                {
                    ["map"] = map.Name,
                    ["total"] = total,
                    ["layers"] = layers,
                };

                if (!string.IsNullOrWhiteSpace(layerName))
                {
                    var fl = MapUtil.FindFeatureLayer(map, layerName);
                    var wanted = ParamUtil.GetStringList(@params, "fields");
                    List<string> outFields = null;
                    var rows = new List<Dictionary<string, object>>();
                    using (var selection = fl.GetSelection())
                    {
                        var ids = selection.GetObjectIDs().Take(limit).ToList();
                        if (ids.Count > 0)
                        {
                            var qf = new QueryFilter { ObjectIDs = ids };
                            if (wanted.Count > 0) qf.SubFields = string.Join(",", wanted);
                            using var cursor = fl.Search(qf);
                            while (cursor.MoveNext())
                                using (var row = cursor.Current)
                                {
                                    outFields ??= row.GetFields()
                                        .Where(f => f.FieldType != FieldType.Geometry &&
                                                    f.FieldType != FieldType.Blob &&
                                                    f.FieldType != FieldType.Raster)
                                        .Select(f => f.Name)
                                        .Where(n => wanted.Count == 0 || wanted.Contains(n, StringComparer.OrdinalIgnoreCase))
                                        .ToList();
                                    var rec = new Dictionary<string, object>();
                                    foreach (var name in outFields) rec[name] = row[name];
                                    rows.Add(rec);
                                }
                        }
                    }
                    result["fields"] = outFields ?? new List<string>();
                    result["rows"] = rows;
                }
                return result;
            });
    }
}
