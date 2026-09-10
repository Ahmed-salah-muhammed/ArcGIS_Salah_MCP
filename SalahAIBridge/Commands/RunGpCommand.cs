using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Core.Geoprocessing;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>run_gp</c> — params <c>{ tool, args[], kwargs{} }</c> →
    /// <c>{ tool, outputs[] }</c>. Runs a geoprocessing tool and adds its output to
    /// the active map so the user watches it appear. <c>kwargs</c> are passed as GP
    /// environment overrides (e.g. <c>{"outputCoordinateSystem": "..."}</c>).
    ///
    /// Note: <c>Geoprocessing.ExecuteToolAsync</c> manages its own threading — do
    /// NOT wrap it in <c>QueuedTask.Run</c> — so this handler awaits it directly.
    /// </summary>
    public sealed class RunGpCommand : ICommand
    {
        public async Task<object> ExecuteAsync(JsonElement @params)
        {
            string tool = ParamUtil.GetString(@params, "tool")
                ?? throw new ArgumentException("run_gp requires 'tool'.");
            object[] args = ParamUtil.GetObjectArray(@params, "args");
            var environments = ParamUtil.GetStringMap(@params, "kwargs").ToList();

            var valueArray = Geoprocessing.MakeValueArray(args);
            var flags = GPExecuteToolFlags.AddToHistory | GPExecuteToolFlags.AddOutputsToMap;

            IGPResult result = await Geoprocessing.ExecuteToolAsync(
                tool,
                valueArray,
                environments.Count > 0 ? environments : null,
                null,
                flags);

            if (result.IsFailed)
            {
                var errors = string.Join("; ",
                    result.Messages
                        .Where(m => m.Type == GPMessageType.Error)
                        .Select(m => m.Text));
                throw new InvalidOperationException(
                    $"GP tool '{tool}' failed: {(string.IsNullOrWhiteSpace(errors) ? "see Pro history" : errors)}");
            }

            var outputs = result.Values?.ToList() ?? new List<string>();
            return new Dictionary<string, object>
            {
                ["tool"] = tool,
                ["outputs"] = outputs,
            };
        }
    }
}
