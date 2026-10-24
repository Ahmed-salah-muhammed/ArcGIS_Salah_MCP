using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>run_gp</c> — params <c>{ tool, args[], kwargs{} }</c> →
    /// <c>{ tool, outputs[], via, steps[] }</c>. Runs a geoprocessing tool in the open session and adds
    /// its output to the active map. <c>kwargs</c> are GP environment overrides
    /// (e.g. <c>{"outputCoordinateSystem": "..."}</c>).
    ///
    /// The first version called <c>Geoprocessing.MakeValueArray</c> and <c>ExecuteToolAsync</c> straight
    /// from the HttpListener callback (a plain worker thread, neither the UI thread nor the MCT). In a
    /// live Pro 3.7 that failed with a NullReferenceException for EVERY tool, instantly and before the
    /// tool ran. Now the arguments are built on the MCT (<c>QueuedTask.Run</c>, as Esri's samples do) and
    /// the tool is started from the UI thread, like an add-in button; if adding outputs to the map is
    /// what fails, it is retried without that flag. Every attempt is reported in <c>steps</c>.
    /// </summary>
    public sealed class RunGpCommand : ICommand
    {
        public async Task<object> ExecuteAsync(JsonElement @params)
        {
            string tool = ParamUtil.GetString(@params, "tool")
                ?? throw new ArgumentException("run_gp requires 'tool'.");
            object[] args = ParamUtil.GetObjectArray(@params, "args");
            var environments = ParamUtil.GetStringMap(@params, "kwargs").ToList();

            var steps = new List<Dictionary<string, object>>();
            void Step(string name, bool ok, string detail = null)
            {
                var d = new Dictionary<string, object> { ["step"] = name, ["ok"] = ok };
                if (detail != null) d["detail"] = detail;
                steps.Add(d);
            }

            IReadOnlyList<string> valueArray;
            try
            {
                valueArray = await QueuedTask.Run(() => Geoprocessing.MakeValueArray(args));
                Step("MakeValueArray on the MCT", true);
            }
            catch (Exception ex)
            {
                Step("MakeValueArray on the MCT", false, Describe(ex));
                throw new InvalidOperationException("run_gp could not build the tool arguments: " + Describe(ex));
            }

            var attempts = new[]
            {
                ("ExecuteToolAsync on the UI thread (outputs added to the map)",
                    GPExecuteToolFlags.AddToHistory | GPExecuteToolFlags.AddOutputsToMap),
                ("ExecuteToolAsync on the UI thread (outputs not added to the map)",
                    GPExecuteToolFlags.AddToHistory),
            };

            IGPResult result = null;
            string via = null;
            foreach (var (label, flags) in attempts)
            {
                try
                {
                    var started = FrameworkApplication.Current.Dispatcher.InvokeAsync(() =>
                        Geoprocessing.ExecuteToolAsync(tool, valueArray,
                            environments.Count > 0 ? environments : null, null, flags));
                    var running = await started.Task;
                    result = await running;
                    if (result == null)
                        throw new InvalidOperationException("ExecuteToolAsync returned no result.");
                    Step(label, true);
                    via = label;
                    break;
                }
                catch (Exception ex)
                {
                    Step(label, false, Describe(ex));
                    result = null;
                }
            }

            if (result == null)
                throw new InvalidOperationException("run_gp could not run '" + tool + "': " +
                    string.Join(" | ", steps.Where(s => !(bool)s["ok"]).Select(s => s["step"] + ": " + s["detail"])));

            if (result.IsFailed)
            {
                var errors = string.Join("; ",
                    (result.Messages ?? Enumerable.Empty<IGPMessage>())
                        .Where(m => m.Type == GPMessageType.Error)
                        .Select(m => m.Text));
                throw new InvalidOperationException(
                    $"GP tool '{tool}' failed: {(string.IsNullOrWhiteSpace(errors) ? "see Pro history" : errors)}");
            }

            return new Dictionary<string, object>
            {
                ["tool"] = tool,
                ["outputs"] = result.Values?.ToList() ?? new List<string>(),
                ["via"] = via,
                ["steps"] = steps,
            };
        }

        private static string Describe(Exception ex)
        {
            while (ex is AggregateException agg && agg.InnerException != null) ex = agg.InnerException;
            var line = (ex.StackTrace ?? "").Split(new[] { '\n' }).FirstOrDefault(l => l.Contains(" at "));
            return ex.GetType().Name + ": " + ex.Message + (line == null ? "" : " @" + line.Trim());
        }
    }
}
