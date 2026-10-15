using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.GeoProcessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>add_toolbox</c> — params <c>{ path }</c> → <c>{ path, registered, already_registered, steps[] }</c>.
    /// Adds an existing <c>.atbx</c> / <c>.tbx</c> / <c>.pyt</c> to the OPEN project's Toolboxes so a
    /// model generated on disk (<c>pro_create_model</c>) shows up in the Catalog pane and the
    /// Geoprocessing pane without the manual "Add Toolbox" step.
    ///
    /// Unlike notebooks this uses only PUBLIC SDK calls: <c>ItemFactory.Create</c> +
    /// <c>Project.AddItem</c>, the documented route for toolboxes. Success is VERIFIED against
    /// <c>Project.GetItems&lt;GeoprocessingProjectItem&gt;()</c> and every attempt is reported.
    /// </summary>
    public sealed class AddToolboxCommand : ICommand
    {
        private static readonly string[] Extensions = { ".atbx", ".tbx", ".pyt" };

        public async Task<object> ExecuteAsync(JsonElement @params)
        {
            string path = ParamUtil.GetString(@params, "path")
                ?? throw new ArgumentException("add_toolbox requires 'path'.");
            path = Path.GetFullPath(path);
            if (!Extensions.Any(e => path.EndsWith(e, StringComparison.OrdinalIgnoreCase)))
                throw new ArgumentException($"'{path}' is not a toolbox ({string.Join(", ", Extensions)}).");
            if (!File.Exists(path))
                throw new FileNotFoundException($"Toolbox not found: {path}");

            var steps = new List<Dictionary<string, object>>();
            void Step(string name, bool ok, string detail = null)
            {
                var d = new Dictionary<string, object> { ["step"] = name, ["ok"] = ok };
                if (detail != null) d["detail"] = detail;
                steps.Add(d);
            }

            bool already = false;
            try { already = await IsRegisteredAsync(path); }
            catch (Exception ex) { Step("Project.GetItems<GeoprocessingProjectItem> (pre-check)", false, Describe(ex)); }
            bool registered = already;

            if (!registered)
            {
                try
                {
                    bool added = await QueuedTask.Run(() =>
                    {
                        var item = ItemFactory.Instance.Create(path);
                        if (item is IProjectItem pi) return Project.Current.AddItem(pi);
                        throw new InvalidOperationException(
                            "ItemFactory returned " + (item?.GetType().FullName ?? "null") + ", which is not an IProjectItem.");
                    });
                    registered = await IsRegisteredAsync(path);
                    Step("ItemFactory.Create + Project.AddItem", registered,
                        registered ? null : "AddItem returned " + added + " but the toolbox is NOT in the project's toolboxes");
                }
                catch (Exception ex)
                {
                    Step("ItemFactory.Create + Project.AddItem", false, Describe(ex));
                }
            }

            return new Dictionary<string, object>
            {
                ["path"] = path,
                ["registered"] = registered,
                ["already_registered"] = already,
                ["steps"] = steps,
            };
        }

        private static Task<bool> IsRegisteredAsync(string path) =>
            QueuedTask.Run(() =>
            {
                var items = Project.Current?.GetItems<GeoprocessingProjectItem>();
                if (items == null) return false;
                foreach (var it in items)
                {
                    string p = it?.Path;
                    if (string.IsNullOrEmpty(p)) continue;
                    if (string.Equals(Path.GetFullPath(p), path, StringComparison.OrdinalIgnoreCase)) return true;
                }
                return false;
            });

        private static string Describe(Exception ex)
        {
            var line = (ex.StackTrace ?? "").Split(new[] { '\n' }).FirstOrDefault(l => l.Contains(" at "));
            return ex.GetType().Name + ": " + ex.Message + (line == null ? "" : " @" + line.Trim());
        }
    }
}
