using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>export_layout</c> — params <c>{ layout, out_path, dpi? }</c> →
    /// <c>{ output, dpi }</c>. Exports a named layout from the open project to PDF.
    /// </summary>
    public sealed class ExportLayoutCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params) =>
            QueuedTask.Run<object>(() =>
            {
                string layoutName = ParamUtil.GetString(@params, "layout")
                    ?? throw new ArgumentException("export_layout requires 'layout'.");
                string outPath = ParamUtil.GetString(@params, "out_path")
                    ?? throw new ArgumentException("export_layout requires 'out_path'.");
                int dpi = ParamUtil.GetInt(@params, "dpi", 300);

                var item = Project.Current?
                    .GetItems<LayoutProjectItem>()
                    .FirstOrDefault(l => string.Equals(l.Name, layoutName, StringComparison.OrdinalIgnoreCase))
                    ?? throw new InvalidOperationException($"Layout '{layoutName}' not found.");

                Layout layout = item.GetLayout()
                    ?? throw new InvalidOperationException($"Could not open layout '{layoutName}'.");

                var pdf = new PDFFormat
                {
                    OutputFileName = outPath,
                    Resolution = dpi,
                };
                layout.Export(pdf);

                return new Dictionary<string, object>
                {
                    ["output"] = outPath,
                    ["dpi"] = dpi,
                };
            });
    }
}
