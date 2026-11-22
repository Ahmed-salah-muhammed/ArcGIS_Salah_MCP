using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>layer_info</c> (add-in 0.1.7) — params <c>{ layer, map? }</c> → what a layer of the open map is and
    /// how it is shown: type, geometry, source, counts, fields, definition query, renderer, labels, transparency.
    /// </summary>
    public sealed class LayerInfoCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string layerName = ParamUtil.GetString(@params, "layer")
                    ?? throw new ArgumentException("layer_info requires 'layer'.");
                var map = MapUtil.ResolveMap(ParamUtil.GetString(@params, "map"));
                return LayerCommands.Describe(MapUtil.FindLayer(map, layerName));
            });
    }

    /// <summary>
    /// <c>layer_set</c> (add-in 0.1.7) — changes how a layer of the OPEN map is shown; nothing is written to
    /// the data. params <c>{ layer, map?, definition_query?, visible?, transparency?, name?,
    /// labels?: { visible?, expression? (Arcade) }, renderer?: { type: simple|unique|graduated, field?,
    /// color?: [r,g,b], to_color?: [r,g,b], classes?, size? }, remove? }</c> → <c>{ changed[], layer: layer_info }</c>.
    /// <c>remove: true</c> takes the layer out of the map (its data stays on disk).
    /// The headless tools (pro_symbology ...) edit the .aprx file, which the open session overwrites on save;
    /// this is the way to style the session the user is looking at.
    /// </summary>
    public sealed class LayerSetCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string layerName = ParamUtil.GetString(@params, "layer")
                    ?? throw new ArgumentException("layer_set requires 'layer'.");
                var map = MapUtil.ResolveMap(ParamUtil.GetString(@params, "map"));
                var layer = MapUtil.FindLayer(map, layerName);
                var changed = new List<string>();

                if (ParamUtil.GetBool(@params, "remove"))
                {
                    map.RemoveLayer(layer);
                    return new Dictionary<string, object>
                    {
                        ["changed"] = new List<string> { "removed" },
                        ["removed"] = layerName,
                        ["map"] = map.Name,
                    };
                }

                if (@params.TryGetProperty("definition_query", out var dq) &&
                    (dq.ValueKind == JsonValueKind.String || dq.ValueKind == JsonValueKind.Null))
                {
                    var fl = layer as FeatureLayer
                        ?? throw new InvalidOperationException($"'{layer.Name}' is not a feature layer: no definition query.");
                    fl.SetDefinitionQuery(dq.ValueKind == JsonValueKind.Null ? "" : dq.GetString());
                    changed.Add("definition_query");
                }
                if (@params.TryGetProperty("visible", out var vis) &&
                    (vis.ValueKind == JsonValueKind.True || vis.ValueKind == JsonValueKind.False))
                {
                    layer.SetVisibility(vis.GetBoolean());
                    changed.Add("visible");
                }
                if (@params.TryGetProperty("transparency", out var tr) && tr.ValueKind == JsonValueKind.Number)
                {
                    layer.SetTransparency(Math.Min(100, Math.Max(0, tr.GetDouble())));
                    changed.Add("transparency");
                }
                if (@params.TryGetProperty("labels", out var labels) && labels.ValueKind == JsonValueKind.Object)
                {
                    var fl = layer as FeatureLayer
                        ?? throw new InvalidOperationException($"'{layer.Name}' is not a feature layer: no labels.");
                    LayerCommands.SetLabels(fl, labels);
                    changed.Add("labels");
                }
                if (@params.TryGetProperty("renderer", out var rend) && rend.ValueKind == JsonValueKind.Object)
                {
                    var fl = layer as FeatureLayer
                        ?? throw new InvalidOperationException($"'{layer.Name}' is not a feature layer: no renderer.");
                    LayerCommands.SetRenderer(fl, rend);
                    changed.Add("renderer");
                }
                // last: later lookups would not find the layer by its old name
                string newName = ParamUtil.GetString(@params, "name");
                if (!string.IsNullOrWhiteSpace(newName))
                {
                    layer.SetName(newName);
                    changed.Add("name");
                }
                if (changed.Count == 0)
                    throw new ArgumentException("layer_set: give definition_query, visible, transparency, labels, renderer or name.");

                return new Dictionary<string, object>
                {
                    ["changed"] = changed,
                    ["layer"] = LayerCommands.Describe(layer),
                };
            });
    }

    internal static class LayerCommands
    {
        public static Dictionary<string, object> Describe(Layer layer)
        {
            var d = new Dictionary<string, object>
            {
                ["name"] = layer.Name,
                ["type"] = layer.GetType().Name,
                ["visible"] = layer.IsVisible,
                ["transparency"] = layer.Transparency,
            };
            if (layer is not FeatureLayer fl) return d;

            d["geometry"] = fl.ShapeType.ToString().Replace("esriGeometry", "");
            d["definition_query"] = fl.DefinitionQuery ?? "";
            d["selected"] = fl.SelectionCount;
            d["renderer"] = fl.GetRenderer()?.GetType().Name.Replace("CIM", "");
            try
            {
                using var table = fl.GetTable();
                d["count"] = table.GetCount();
                d["count_shown"] = string.IsNullOrWhiteSpace(fl.DefinitionQuery)
                    ? table.GetCount()
                    : table.GetCount(new ArcGIS.Core.Data.QueryFilter { WhereClause = fl.DefinitionQuery });
                using var store = table.GetDatastore();
                d["source"] = store.GetPath()?.LocalPath;
                d["dataset"] = table.GetName();
                using var def = table.GetDefinition();
                d["fields"] = def.GetFields()
                    .Select(f => new Dictionary<string, object>
                    {
                        ["name"] = f.Name,
                        ["type"] = f.FieldType.ToString(),
                        ["alias"] = f.AliasName,
                    }).ToList();
            }
            catch (Exception ex)
            {
                d["source_error"] = ex.Message;   // a broken data source still has a layer to describe
            }
            if (fl.GetDefinition() is CIMFeatureLayer cim)
            {
                var lc = cim.LabelClasses?.FirstOrDefault();
                d["labels"] = new Dictionary<string, object>
                {
                    ["visible"] = cim.LabelVisibility,
                    ["expression"] = lc?.Expression,
                    ["engine"] = lc?.ExpressionEngine.ToString(),
                };
            }
            return d;
        }

        public static void SetLabels(FeatureLayer fl, JsonElement labels)
        {
            var cim = fl.GetDefinition() as CIMFeatureLayer
                ?? throw new InvalidOperationException("The layer has no feature-layer definition.");
            string expression = ParamUtil.GetString(labels, "expression");
            if (expression != null)
            {
                var lc = cim.LabelClasses?.FirstOrDefault()
                    ?? throw new InvalidOperationException(
                        $"'{fl.Name}' has no label class to set; add one in Pro (Labeling > Label Class) first.");
                lc.Expression = expression;
                lc.ExpressionEngine = LabelExpressionEngine.Arcade;
                lc.Visibility = true;
            }
            bool visible = !labels.TryGetProperty("visible", out var v) || v.ValueKind != JsonValueKind.False;
            cim.LabelVisibility = visible;
            fl.SetDefinition(cim);
        }

        private static CIMColor Color(JsonElement spec, string key, CIMColor fallback)
        {
            if (spec.TryGetProperty(key, out var c) && c.ValueKind == JsonValueKind.Array && c.GetArrayLength() >= 3)
            {
                var v = c.EnumerateArray().Select(x => x.GetDouble()).ToArray();
                return ColorFactory.Instance.CreateRGBColor(v[0], v[1], v[2]);
            }
            return fallback;
        }

        private static CIMSymbolReference Symbol(FeatureLayer fl, CIMColor color, double size)
        {
            switch (fl.ShapeType)
            {
                case esriGeometryType.esriGeometryPoint:
                case esriGeometryType.esriGeometryMultipoint:
                    return SymbolFactory.Instance.ConstructPointSymbol(color, size > 0 ? size : 8).MakeSymbolReference();
                case esriGeometryType.esriGeometryPolyline:
                    return SymbolFactory.Instance.ConstructLineSymbol(color, size > 0 ? size : 1.5).MakeSymbolReference();
                case esriGeometryType.esriGeometryPolygon:
                    return SymbolFactory.Instance.ConstructPolygonSymbol(color).MakeSymbolReference();
                default:
                    throw new InvalidOperationException($"'{fl.Name}' has geometry {fl.ShapeType}: no symbol for it here.");
            }
        }

        public static void SetRenderer(FeatureLayer fl, JsonElement spec)
        {
            string type = (ParamUtil.GetString(spec, "type") ?? "simple").ToLowerInvariant();
            string field = ParamUtil.GetString(spec, "field");
            double size = spec.TryGetProperty("size", out var s) && s.ValueKind == JsonValueKind.Number ? s.GetDouble() : 0;
            var color = Color(spec, "color", ColorFactory.Instance.CreateRGBColor(0, 112, 255));
            var symbol = Symbol(fl, color, size);

            if (type != "simple")
            {
                if (string.IsNullOrWhiteSpace(field))
                    throw new ArgumentException($"renderer type '{type}' requires 'field'.");
                using var table = fl.GetTable();
                using var def = table.GetDefinition();
                if (def.FindField(field) < 0)
                    throw new ArgumentException($"'{fl.Name}' has no field '{field}'. Fields: " +
                        string.Join(", ", def.GetFields().Select(f => f.Name)));
            }

            RendererDefinition definition;
            switch (type)
            {
                case "simple":
                    definition = new SimpleRendererDefinition { SymbolTemplate = symbol };
                    break;
                case "unique":
                    definition = new UniqueValueRendererDefinition
                    {
                        ValueFields = new List<string> { field },
                        SymbolTemplate = symbol,
                        ColorRamp = new CIMRandomHSVColorRamp
                        {
                            ColorSpace = new CIMICCColorSpace { URL = "Default RGB" },
                            MinH = 0, MaxH = 360, MinS = 45, MaxS = 75, MinV = 70, MaxV = 95,
                            MinAlpha = 100, MaxAlpha = 100, Seed = 7,
                        },
                        ValuesLimit = 250,
                    };
                    break;
                case "graduated":
                    int classes = Math.Min(10, Math.Max(2, ParamUtil.GetInt(spec, "classes", 5)));
                    definition = new GraduatedColorsRendererDefinition
                    {
                        ClassificationField = field,
                        ClassificationMethod = ClassificationMethod.NaturalBreaks,
                        BreakCount = classes,
                        SymbolTemplate = symbol,
                        ColorRamp = new CIMLinearContinuousColorRamp
                        {
                            ColorSpace = new CIMICCColorSpace { URL = "Default RGB" },
                            FromColor = Color(spec, "color", ColorFactory.Instance.CreateRGBColor(255, 245, 235)),
                            ToColor = Color(spec, "to_color", ColorFactory.Instance.CreateRGBColor(165, 15, 21)),
                        },
                    };
                    break;
                default:
                    throw new ArgumentException($"renderer type must be simple | unique | graduated, not '{type}'.");
            }
            fl.SetRenderer(fl.CreateRenderer(definition));
        }
    }
}
