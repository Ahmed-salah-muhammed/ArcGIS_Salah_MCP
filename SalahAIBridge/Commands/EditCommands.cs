using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Editing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>edit_features</c> (add-in 0.1.7) — adds, updates and deletes rows of a layer (or standalone table)
    /// of the open map in ONE <see cref="EditOperation"/>: it lands on Pro's undo stack and in the user's edit
    /// session, exactly like an edit made by hand, so the layer can stay in the Contents pane.
    /// params <c>{ layer, map?, adds?: [{ attributes?, geometry? }], updates?: [{ object_id, attributes?,
    /// geometry? }], deletes?: [object ids], save?: bool, name? }</c>.
    /// <c>geometry</c> is <c>{ x, y, wkid? }</c> (wkid 4326 by default) or Esri geometry JSON; it is projected
    /// to the layer's spatial reference. Dates are ISO strings.
    /// → <c>{ layer, added, updated, deleted, added_object_ids[], saved, has_unsaved_edits, steps[] }</c>.
    /// Without <c>save</c> the edits stay unsaved (undoable); with it the whole edit session is saved.
    /// </summary>
    public sealed class EditFeaturesCommand : ICommand
    {
        public async Task<object> ExecuteAsync(JsonElement @params)
        {
            string layerName = ParamUtil.GetString(@params, "layer")
                ?? throw new ArgumentException("edit_features requires 'layer'.");
            bool save = ParamUtil.GetBool(@params, "save");
            var steps = new List<string>();

            if (Project.Current == null) throw new InvalidOperationException("No project is open.");
            if (!Project.Current.IsEditingEnabled)
            {
                // Pro's option "Enable and disable editing from the Edit tab": turn editing on like the button does.
                bool enabled = await Project.Current.SetIsEditingEnabledAsync(true);
                if (!enabled) throw new InvalidOperationException("Editing is disabled in this project (Edit tab > Edit).");
                steps.Add("editing was disabled in the project: enabled it");
            }

            var result = await QueuedTask.Run(() =>
            {
                var map = MapUtil.ResolveMap(ParamUtil.GetString(@params, "map"));
                MapMember member = EditUtil.FindMember(map, layerName);
                using var table = EditUtil.TableOf(member);
                using var def = table.GetDefinition();
                var fields = def.GetFields().ToDictionary(f => f.Name, f => f, StringComparer.OrdinalIgnoreCase);
                string shapeField = (def as FeatureClassDefinition)?.GetShapeField();
                SpatialReference layerSr = (def as FeatureClassDefinition)?.GetSpatialReference();

                var op = new EditOperation
                {
                    Name = ParamUtil.GetString(@params, "name") ?? "Salah MCP edit",
                    SelectNewFeatures = false,
                    SelectModifiedFeatures = false,
                };
                var tokens = new List<RowToken>();
                int updated = 0;
                var deletes = new List<long>();

                if (@params.TryGetProperty("adds", out var adds) && adds.ValueKind == JsonValueKind.Array)
                    foreach (var add in adds.EnumerateArray())
                        tokens.Add(op.Create(member, EditUtil.Values(add, fields, shapeField, layerSr, member.Name)));

                if (@params.TryGetProperty("updates", out var updates) && updates.ValueKind == JsonValueKind.Array)
                    foreach (var update in updates.EnumerateArray())
                    {
                        if (!update.TryGetProperty("object_id", out var oid) || !oid.TryGetInt64(out long id))
                            throw new ArgumentException("every update needs 'object_id'.");
                        var values = EditUtil.Values(update, fields, shapeField, layerSr, member.Name);
                        if (values.Count == 0) throw new ArgumentException($"update of {id}: nothing to change.");
                        op.Modify(member, id, values);
                        updated++;
                    }

                if (@params.TryGetProperty("deletes", out var dels) && dels.ValueKind == JsonValueKind.Array)
                {
                    deletes.AddRange(dels.EnumerateArray().Select(d => d.GetInt64()));
                    if (deletes.Count > 0) op.Delete(member, deletes);
                }

                if (tokens.Count == 0 && updated == 0 && deletes.Count == 0)
                    throw new ArgumentException("edit_features: give adds, updates or deletes.");

                if (!op.Execute())
                    throw new InvalidOperationException("The edit was not applied: " +
                        (string.IsNullOrWhiteSpace(op.ErrorMessage) ? "Pro gave no reason" : op.ErrorMessage));

                return new Dictionary<string, object>
                {
                    ["layer"] = member.Name,
                    ["added"] = tokens.Count,
                    ["updated"] = updated,
                    ["deleted"] = deletes.Count,
                    ["added_object_ids"] = tokens.Select(t => t.ObjectID).Where(i => i.HasValue).Select(i => i.Value).ToList(),
                };
            });

            if (save)
            {
                await Project.Current.SaveEditsAsync();
                steps.Add("saved the edit session");
            }
            result["saved"] = save;
            result["has_unsaved_edits"] = Project.Current.HasEdits;
            result["steps"] = steps;
            return result;
        }
    }

    /// <summary>
    /// <c>edit_session</c> (add-in 0.1.7) — the open project's edit session.
    /// params <c>{ action: status|save|discard|undo|redo, map? }</c> →
    /// <c>{ action, has_unsaved_edits, editing_enabled, can_undo, can_redo, undo_stack[] }</c>.
    /// <c>save</c> and <c>discard</c> act on EVERY unsaved edit of the session, the user's own included.
    /// </summary>
    public sealed class EditSessionCommand : ICommand
    {
        public async Task<object> ExecuteAsync(JsonElement @params)
        {
            string action = (ParamUtil.GetString(@params, "action") ?? "status").ToLowerInvariant();
            if (Project.Current == null) throw new InvalidOperationException("No project is open.");
            string mapName = ParamUtil.GetString(@params, "map");

            switch (action)
            {
                case "status": break;
                case "save": await Project.Current.SaveEditsAsync(); break;
                case "discard": await Project.Current.DiscardEditsAsync(); break;
                case "undo":
                case "redo":
                    var manager = await QueuedTask.Run(() => MapUtil.ResolveMap(mapName).OperationManager);
                    if (action == "undo")
                    {
                        if (!manager.CanUndo) throw new InvalidOperationException("Nothing to undo in this map.");
                        await manager.UndoAsync();
                    }
                    else
                    {
                        if (!manager.CanRedo) throw new InvalidOperationException("Nothing to redo in this map.");
                        await manager.RedoAsync();
                    }
                    break;
                default:
                    throw new ArgumentException($"action must be status | save | discard | undo | redo, not '{action}'.");
            }

            return await QueuedTask.Run<object>(() =>
            {
                var d = new Dictionary<string, object>
                {
                    ["action"] = action,
                    ["has_unsaved_edits"] = Project.Current.HasEdits,
                    ["editing_enabled"] = Project.Current.IsEditingEnabled,
                };
                var map = string.IsNullOrWhiteSpace(mapName) ? MapUtil.ActiveView()?.Map : MapUtil.ResolveMap(mapName);
                if (map != null)
                {
                    var om = map.OperationManager;
                    d["can_undo"] = om.CanUndo;
                    d["can_redo"] = om.CanRedo;
                    d["undo_stack"] = om.FindUndoOperations(_ => true).Select(o => o.Name).Take(20).ToList();
                }
                return d;
            });
        }
    }

    internal static class EditUtil
    {
        /// <summary>A feature layer or a standalone table of the map, by name.</summary>
        public static MapMember FindMember(Map map, string name)
        {
            MapMember member = map.GetLayersAsFlattenedList().OfType<BasicFeatureLayer>()
                .FirstOrDefault(l => string.Equals(l.Name, name, StringComparison.OrdinalIgnoreCase));
            member ??= map.GetStandaloneTablesAsFlattenedList()
                .FirstOrDefault(t => string.Equals(t.Name, name, StringComparison.OrdinalIgnoreCase));
            if (member == null)
                throw new InvalidOperationException($"No feature layer or table '{name}' in map '{map.Name}'.");
            return member;
        }

        public static Table TableOf(MapMember member) => member switch
        {
            BasicFeatureLayer fl => fl.GetTable(),
            StandaloneTable st => st.GetTable(),
            _ => throw new InvalidOperationException($"'{member.Name}' has no table."),
        };

        /// <summary>{ attributes, geometry } of one add/update -> the field/value dictionary an EditOperation takes.</summary>
        public static Dictionary<string, object> Values(JsonElement item, Dictionary<string, Field> fields,
            string shapeField, SpatialReference layerSr, string layerName)
        {
            var values = new Dictionary<string, object>();
            if (item.TryGetProperty("attributes", out var attrs) && attrs.ValueKind == JsonValueKind.Object)
                foreach (var prop in attrs.EnumerateObject())
                {
                    if (!fields.TryGetValue(prop.Name, out var field))
                        throw new ArgumentException($"'{layerName}' has no field '{prop.Name}'. Fields: " +
                            string.Join(", ", fields.Keys));
                    if (!field.IsEditable)
                        throw new ArgumentException($"Field '{field.Name}' of '{layerName}' is not editable.");
                    values[field.Name] = Convert(prop.Value, field);
                }
            if (item.TryGetProperty("geometry", out var geom) && geom.ValueKind == JsonValueKind.Object)
            {
                if (shapeField == null) throw new ArgumentException($"'{layerName}' is a table: it has no geometry.");
                values[shapeField] = Geometry(geom, layerSr);
            }
            return values;
        }

        private static object Convert(JsonElement v, Field field)
        {
            switch (v.ValueKind)
            {
                case JsonValueKind.Null: return null;
                case JsonValueKind.True: return true;
                case JsonValueKind.False: return false;
                case JsonValueKind.Number: return v.TryGetInt64(out long l) ? l : v.GetDouble();
                case JsonValueKind.String:
                    string s = v.GetString();
                    if (field.FieldType == FieldType.Date)
                        return DateTime.Parse(s, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);
                    if (field.FieldType == FieldType.GUID) return Guid.Parse(s);
                    return s;
                default:
                    throw new ArgumentException($"Field '{field.Name}': a value must be text, a number, true/false or null.");
            }
        }

        private static Geometry Geometry(JsonElement g, SpatialReference layerSr)
        {
            Geometry geometry;
            if (g.TryGetProperty("x", out var x) && g.TryGetProperty("y", out var y) &&
                !g.TryGetProperty("spatialReference", out _))
            {
                int wkid = g.TryGetProperty("wkid", out var w) && w.TryGetInt32(out int id) ? id : 4326;
                geometry = MapPointBuilderEx.CreateMapPoint(x.GetDouble(), y.GetDouble(),
                    SpatialReferenceBuilder.CreateSpatialReference(wkid));
            }
            else
            {
                geometry = GeometryEngine.Instance.ImportFromJson(JsonImportFlags.JsonImportDefaults, g.GetRawText());
            }
            if (layerSr != null && geometry.SpatialReference != null && !geometry.SpatialReference.IsEqual(layerSr))
                geometry = GeometryEngine.Instance.Project(geometry, layerSr);
            return geometry;
        }
    }
}
