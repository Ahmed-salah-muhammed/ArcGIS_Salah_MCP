using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>add_database</c> — params <c>{ path }</c> → <c>{ path, registered, already_registered, steps[] }</c>.
    /// Adds an existing <c>.sde</c> database connection file (or a file geodatabase folder) to the OPEN
    /// project's Databases, so a connection made on disk by <c>pro_egdb_create_connection</c> shows up in
    /// the Catalog pane without the manual "Add Database" step.
    ///
    /// Same public route as <c>add_toolbox</c>: <c>ItemFactory.Create</c> + <c>Project.AddItem</c>, then
    /// VERIFIED against <c>Project.GetItems&lt;GDBProjectItem&gt;()</c>; every attempt is reported.
    /// </summary>
    public sealed class AddDatabaseCommand : ICommand
    {
        public async Task<object> ExecuteAsync(JsonElement @params)
        {
            string path = ParamUtil.GetString(@params, "path")
                ?? throw new ArgumentException("add_database requires 'path'.");
            path = Path.GetFullPath(path).TrimEnd('\\', '/');
            bool isSde = path.EndsWith(".sde", StringComparison.OrdinalIgnoreCase);
            bool isGdb = path.EndsWith(".gdb", StringComparison.OrdinalIgnoreCase);
            if (!isSde && !isGdb)
                throw new ArgumentException($"'{path}' is not a database connection (.sde) or a file geodatabase (.gdb).");
            if (isSde ? !File.Exists(path) : !Directory.Exists(path))
                throw new FileNotFoundException($"Not found: {path}");

            var steps = new List<Dictionary<string, object>>();
            void Step(string name, bool ok, string detail = null)
            {
                var d = new Dictionary<string, object> { ["step"] = name, ["ok"] = ok };
                if (detail != null) d["detail"] = detail;
                steps.Add(d);
            }

            bool already = false;
            try { already = await IsRegisteredAsync(path); }
            catch (Exception ex) { Step("Project.GetItems<GDBProjectItem> (pre-check)", false, Describe(ex)); }
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
                        registered ? null : "AddItem returned " + added + " but the database is NOT in the project's databases");
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
                var items = Project.Current?.GetItems<GDBProjectItem>();
                if (items == null) return false;
                foreach (var it in items)
                {
                    string p = it?.Path;
                    if (string.IsNullOrEmpty(p)) continue;
                    if (string.Equals(Path.GetFullPath(p).TrimEnd('\\', '/'), path, StringComparison.OrdinalIgnoreCase)) return true;
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
