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
    /// <c>query</c> — params <c>{ layer, where?, fields?, limit? }</c> →
    /// <c>{ fields, rows[], returned }</c>. Reads attribute rows from a feature
    /// layer in the active map. Geometry / blob / raster fields are skipped so the
    /// payload stays JSON-serializable.
    /// </summary>
    public sealed class QueryCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string layerName = ParamUtil.GetString(@params, "layer")
                    ?? throw new ArgumentException("query requires 'layer'.");
                string where = ParamUtil.GetString(@params, "where");
                var fields = ParamUtil.GetStringList(@params, "fields");
                int limit = ParamUtil.GetInt(@params, "limit", 10);

                var map = MapView.Active?.Map
                    ?? throw new InvalidOperationException("No active map view.");
                var fl = MapUtil.FindFeatureLayer(map, layerName);

                var qf = new QueryFilter();
                if (!string.IsNullOrWhiteSpace(where)) qf.WhereClause = where;
                if (fields.Count > 0) qf.SubFields = string.Join(",", fields);

                List<string> outFields = null;
                var rows = new List<Dictionary<string, object>>();

                using (var cursor = fl.Search(qf))
                {
                    while (rows.Count < limit && cursor.MoveNext())
                        using (var row = cursor.Current)
                        {
                            // Determine the readable (non-geometry) field set once.
                            outFields ??= row.GetFields()
                                .Where(f => f.FieldType != FieldType.Geometry &&
                                            f.FieldType != FieldType.Blob &&
                                            f.FieldType != FieldType.Raster)
                                .Select(f => f.Name)
                                .ToList();

                            var rec = new Dictionary<string, object>();
                            foreach (var name in outFields)
                                rec[name] = row[name];
                            rows.Add(rec);
                        }
                }

                return new Dictionary<string, object>
                {
                    ["fields"] = outFields ?? new List<string>(),
                    ["rows"] = rows,
                    ["returned"] = rows.Count,
                };
            });
    }
}
