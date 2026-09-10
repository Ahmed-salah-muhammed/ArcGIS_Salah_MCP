using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;

using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;

namespace SalahAIBridge
{
    /// <summary>
    /// Salah_MCP_DashboardBtn ("Create Dashboard") — generates a static, interactive
    /// Esri-Dashboard-style web app (map + live indicator cards that recompute as you
    /// zoom/pan + a category breakdown + a "features in view" list) and previews it in
    /// the browser on localhost. Same flow as "Create Web App", but it shells out to
    /// the dashboard generator (arcgis_pro_salah_mcp.webapp.dashboard). Deploy the
    /// generated folder with the "Deploy Web App" button.
    /// </summary>
    internal class PreviewDashboardButton : ArcGIS.Desktop.Framework.Contracts.Button
    {
        private const string DefaultProPython =
            @"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe";

        private static Process _previewProc;

        private static Brush BgDark => Hex("#252526");
        private static Brush InputBg => Hex("#1e1e1e");
        private static Brush Accent => Hex("#5856d6");   // indigo — matches the ribbon icon
        private static Brush BtnBg => Hex("#3e3e42");
        private static Brush Gray => Hex("#aaaaaa");
        private static readonly FontFamily Seg = new("Segoe UI");

        private static readonly string[] Basemaps =
        {
            "topo-vector", "streets-vector", "satellite", "hybrid",
            "gray-vector", "dark-gray-vector", "osm",
        };

        protected override async void OnClick()
        {
            try
            {
                string defaultDir = Environment.GetEnvironmentVariable("ARCGIS_WEBAPP_OUT");
                if (string.IsNullOrWhiteSpace(defaultDir))
                {
                    string projHome = await QueuedTask.Run(() => Project.Current?.HomeFolderPath);
                    defaultDir = !string.IsNullOrEmpty(projHome)
                        ? Path.Combine(projHome, "dashboard-build")
                        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "dashboard-build");
                }

                var input = ShowWizard(defaultDir);
                if (input == null) return;

                var (win, _) = ShowProgress("Generating the dashboard…");
                GenResult res;
                try { res = await GenerateAsync(input); }
                finally { win.Close(); }

                if (!res.Ok)
                {
                    MessageBox.Show("Could not generate the dashboard:\n\n" + res.Error,
                        "Salah MCP — Create Dashboard", MessageBoxButton.OK, MessageBoxImage.Error);
                    return;
                }

                string url;
                try { url = StartPreview(res.OutputDir, input.Port); }
                catch (Exception ex)
                {
                    MessageBox.Show($"Dashboard generated at:\n{res.OutputDir}\n\nbut the preview server failed to start:\n{ex.Message}",
                        "Salah MCP — Create Dashboard");
                    return;
                }

                OpenUrl(url);
                ShowPreviewWindow(url, res.OutputDir);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Create Dashboard error: {ex.Message}", "Salah MCP — Create Dashboard");
            }
        }

        // ---------------------------------------------------------------------
        //  Wizard
        // ---------------------------------------------------------------------
        private static Input ShowWizard(string defaultDir)
        {
            var panel = new StackPanel { Background = BgDark };

            panel.Children.Add(Label("Build an interactive dashboard (map + live indicators) and preview it locally.",
                Gray, 12, new Thickness(0, 0, 0, 14)));

            panel.Children.Add(Label("Dashboard title", null, 12, new Thickness(0, 0, 0, 4)));
            var titleBox = TextInput("ArcGIS Dashboard");
            panel.Children.Add(titleBox);

            panel.Children.Add(Label("Web Map item ID (its first feature layer drives the stats)", null, 12, new Thickness(0, 12, 0, 4)));
            var webmapBox = TextInput("");
            panel.Children.Add(webmapBox);

            panel.Children.Add(Label("…or hosted layer item IDs (comma-separated; the first is primary)", Gray, 11, new Thickness(0, 6, 0, 4)));
            var layersBox = TextInput("");
            panel.Children.Add(layersBox);

            panel.Children.Add(Label("Basemap", null, 12, new Thickness(0, 12, 0, 4)));
            var basemapBox = new ComboBox
            {
                Background = InputBg, Foreground = Brushes.White, FontFamily = Seg, FontSize = 13,
                BorderBrush = BtnBg, BorderThickness = new Thickness(1),
            };
            foreach (var b in Basemaps) basemapBox.Items.Add(b);
            basemapBox.SelectedIndex = 0;
            panel.Children.Add(basemapBox);

            panel.Children.Add(Label("Category field (optional — auto-picked if blank)", Gray, 11, new Thickness(0, 12, 0, 4)));
            var catBox = TextInput("");
            panel.Children.Add(catBox);

            panel.Children.Add(Label("Value fields for the indicators (optional, comma-separated)", Gray, 11, new Thickness(0, 8, 0, 4)));
            var valBox = TextInput("");
            panel.Children.Add(valBox);

            // Widgets
            panel.Children.Add(Label("Map widgets", null, 12, new Thickness(0, 12, 0, 6)));
            var widgetDefs = new (string id, bool on)[]
            {
                ("legend", true), ("layerList", true), ("search", true),
                ("basemapGallery", false), ("home", true),
            };
            var widgetBoxes = new List<CheckBox>();
            var widgetRow = new WrapPanel();
            foreach (var (id, isOn) in widgetDefs)
            {
                var cb = new CheckBox
                {
                    Content = id, IsChecked = isOn, Foreground = Brushes.White, FontFamily = Seg,
                    FontSize = 13, Margin = new Thickness(0, 0, 14, 4), Tag = id,
                };
                widgetBoxes.Add(cb);
                widgetRow.Children.Add(cb);
            }
            panel.Children.Add(widgetRow);

            panel.Children.Add(Label("Output folder", null, 12, new Thickness(0, 12, 0, 4)));
            var dirBox = TextInput(defaultDir);
            panel.Children.Add(dirBox);

            panel.Children.Add(Label("Preview port", null, 12, new Thickness(0, 12, 0, 4)));
            var portBox = TextInput("5501");
            panel.Children.Add(portBox);

            var create = PillButton.Create("Create & Preview", 160, 30, Accent, Brushes.White, isDefault: true);
            create.Margin = new Thickness(0, 0, 10, 0);

            var cancel = PillButton.Create("Cancel", 90, 30, BtnBg, Brushes.White, isCancel: true);
            var btnRow = new StackPanel
            {
                Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 18, 0, 0),
            };
            btnRow.Children.Add(create);
            btnRow.Children.Add(cancel);
            panel.Children.Add(btnRow);

            var window = new Window
            {
                Title = "Salah MCP — Create Dashboard",
                Content = new Border { Background = BgDark, Padding = new Thickness(18),
                    Child = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = panel } },
                Width = 480, MaxHeight = 760, SizeToContent = SizeToContent.Height,
                ResizeMode = ResizeMode.NoResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = BgDark,
            };

            Input result = null;
            create.Click += (_, _) =>
            {
                string title = titleBox.Text?.Trim();
                string webmap = webmapBox.Text?.Trim();
                var layerIds = (layersBox.Text ?? "")
                    .Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(s => s.Trim()).Where(s => s.Length > 0).ToList();
                var valueFields = (valBox.Text ?? "")
                    .Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(s => s.Trim()).Where(s => s.Length > 0).ToList();

                if (string.IsNullOrWhiteSpace(title))
                {
                    MessageBox.Show("Give the dashboard a title.", "Salah MCP — Create Dashboard");
                    return;
                }
                if (string.IsNullOrWhiteSpace(webmap) && layerIds.Count == 0)
                {
                    MessageBox.Show("Provide a Web Map item ID or at least one layer item ID.",
                        "Salah MCP — Create Dashboard");
                    return;
                }
                if (!int.TryParse(portBox.Text?.Trim(), out int port) || port < 1 || port > 65535)
                    port = 5501;

                result = new Input
                {
                    Title = title,
                    WebmapId = string.IsNullOrWhiteSpace(webmap) ? null : webmap,
                    LayerItemIds = layerIds,
                    Basemap = basemapBox.SelectedItem as string ?? "topo-vector",
                    Widgets = widgetBoxes.Where(b => b.IsChecked == true).Select(b => (string)b.Tag).ToList(),
                    CategoryField = string.IsNullOrWhiteSpace(catBox.Text) ? null : catBox.Text.Trim(),
                    ValueFields = valueFields,
                    OutDir = dirBox.Text?.Trim(),
                    Port = port,
                };
                window.DialogResult = true;
            };

            window.ShowDialog();
            return result;
        }

        // ---------------------------------------------------------------------
        //  Generate via Pro's Python (the dashboard stdin runner)
        // ---------------------------------------------------------------------
        private static async Task<GenResult> GenerateAsync(Input input)
        {
            string py = ResolveProPython();
            if (py == null)
                return new GenResult { Ok = false,
                    Error = "Could not locate arcgispro-py3 python.exe. Set ARCGIS_PRO_PYTHON to its full path." };

            var psi = new ProcessStartInfo
            {
                FileName = py,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.ArgumentList.Add("-m");
            psi.ArgumentList.Add("arcgis_pro_salah_mcp.webapp.dashboard");

            using var proc = new Process { StartInfo = psi };
            proc.Start();

            string payload = JsonSerializer.Serialize(new
            {
                title = input.Title,
                webmap_id = input.WebmapId,
                layer_item_ids = input.LayerItemIds,
                out_dir = input.OutDir,
                basemap = input.Basemap,
                widgets = input.Widgets,
                category_field = input.CategoryField,
                value_fields = input.ValueFields,
            });
            await proc.StandardInput.WriteAsync(payload);
            proc.StandardInput.Close();

            var outTask = proc.StandardOutput.ReadToEndAsync();
            var errTask = proc.StandardError.ReadToEndAsync();
            await Task.WhenAll(outTask, errTask);
            await proc.WaitForExitAsync();
            string stdout = outTask.Result, stderr = errTask.Result;

            const string marker = "SALAH_RESULT:";
            int idx = stdout.LastIndexOf(marker, StringComparison.Ordinal);
            if (idx < 0)
            {
                string detail = string.IsNullOrWhiteSpace(stderr) ? stdout : stderr;
                if (detail.Contains("No module named", StringComparison.OrdinalIgnoreCase))
                    detail += "\n\nInstall the package into arcgispro-py3:\n" +
                              "\"…\\arcgispro-py3\\python.exe\" -m pip install -e .";
                return new GenResult { Ok = false, Error = "generator failed: " + detail.Trim() };
            }

            string json = stdout[(idx + marker.Length)..].Trim();
            int nl = json.IndexOfAny(new[] { '\r', '\n' });
            if (nl >= 0) json = json[..nl].Trim();

            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            bool ok = root.TryGetProperty("ok", out var okEl) && okEl.GetBoolean();
            if (!ok)
            {
                string e = root.TryGetProperty("error", out var ee) ? ee.GetString() : "unknown error";
                return new GenResult { Ok = false, Error = e };
            }

            var data = root.GetProperty("data");
            return new GenResult
            {
                Ok = true,
                OutputDir = data.TryGetProperty("output_dir", out var od) ? od.GetString() : input.OutDir,
            };
        }

        // ---------------------------------------------------------------------
        //  Local preview server (python -m http.server, one at a time)
        // ---------------------------------------------------------------------
        private static string StartPreview(string outputDir, int port)
        {
            StopPreview();

            string py = ResolveProPython();
            var psi = new ProcessStartInfo
            {
                FileName = py,
                WorkingDirectory = outputDir,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.ArgumentList.Add("-m");
            psi.ArgumentList.Add("http.server");
            psi.ArgumentList.Add(port.ToString());
            psi.ArgumentList.Add("--bind");
            psi.ArgumentList.Add("127.0.0.1");
            psi.ArgumentList.Add("--directory");
            psi.ArgumentList.Add(outputDir);

            _previewProc = Process.Start(psi);
            return $"http://localhost:{port}/";
        }

        private static void StopPreview()
        {
            try
            {
                if (_previewProc != null && !_previewProc.HasExited)
                    _previewProc.Kill(entireProcessTree: true);
            }
            catch { /* ignore */ }
            finally { _previewProc = null; }
        }

        private static void ShowPreviewWindow(string url, string outputDir)
        {
            var panel = new StackPanel { Background = BgDark };
            panel.Children.Add(Label("✓ Dashboard generated and previewing locally.", Brushes.White, 13,
                new Thickness(0, 0, 0, 10)));

            var run = new Run(url);
            var link = new Hyperlink(run) { Foreground = Accent };
            link.Click += (_, _) => OpenUrl(url);
            panel.Children.Add(new TextBlock(link)
            {
                FontSize = 13, FontFamily = Seg, Margin = new Thickness(0, 0, 0, 6),
                Cursor = System.Windows.Input.Cursors.Hand,
            });
            panel.Children.Add(Label(outputDir, Gray, 11, new Thickness(0, 0, 0, 6)));
            panel.Children.Add(Label("Happy with it? Click \"Deploy Web App\" and point it at this folder to push to GitHub.",
                Gray, 12, new Thickness(0, 0, 0, 14)));

            var open = PillButton.Create("Open in browser", 150, 30, Accent, Brushes.White, isDefault: true);
            open.Margin = new Thickness(0, 0, 10, 0);

            var stop = PillButton.Create("Stop preview", 120, 30, BtnBg, Brushes.White);

            var close = PillButton.Create("Close", 80, 30, BtnBg, Brushes.White, isCancel: true);
            close.Margin = new Thickness(10, 0, 0, 0);
            var btnRow = new StackPanel
            {
                Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
            };
            btnRow.Children.Add(open);
            btnRow.Children.Add(stop);
            btnRow.Children.Add(close);
            panel.Children.Add(btnRow);

            var window = new Window
            {
                Title = "Salah MCP — Dashboard Preview",
                Content = new Border { Background = BgDark, Padding = new Thickness(18), Child = panel },
                Width = 470, SizeToContent = SizeToContent.Height, ResizeMode = ResizeMode.NoResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = BgDark,
            };
            open.Click += (_, _) => OpenUrl(url);
            stop.Click += (_, _) => { StopPreview(); window.Close(); };
            window.ShowDialog();
        }

        // ---------------------------------------------------------------------
        //  helpers
        // ---------------------------------------------------------------------
        private static (Window window, TextBlock label) ShowProgress(string message)
        {
            var label = new TextBlock
            {
                Text = message, Foreground = Brushes.White, FontFamily = Seg, FontSize = 13,
                TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14),
            };
            var panel = new StackPanel();
            panel.Children.Add(label);
            panel.Children.Add(new ProgressBar
            {
                IsIndeterminate = true, Height = 10, Foreground = Accent, Background = InputBg,
                BorderThickness = new Thickness(0),
            });
            var win = new Window
            {
                Title = "Salah MCP — Working",
                Content = new Border { Background = BgDark, Padding = new Thickness(20), Child = panel },
                Width = 420, SizeToContent = SizeToContent.Height, ResizeMode = ResizeMode.NoResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = BgDark,
                WindowStyle = WindowStyle.None, Topmost = true,
            };
            win.Show();
            return (win, label);
        }

        private static TextBox TextInput(string text) => new()
        {
            Text = text ?? "", Background = InputBg, Foreground = Brushes.White, BorderBrush = BtnBg,
            BorderThickness = new Thickness(1), Padding = new Thickness(6, 4, 6, 4),
            FontSize = 13, FontFamily = Seg, CaretBrush = Brushes.White,
        };

        private static TextBlock Label(string text, Brush fg, double size, Thickness margin) => new()
        {
            Text = text, Foreground = fg ?? Brushes.White, FontFamily = Seg, FontSize = size,
            TextWrapping = TextWrapping.Wrap, Margin = margin,
        };

        private static void OpenUrl(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
            catch { /* ignore */ }
        }

        private static string ResolveProPython()
        {
            string env = Environment.GetEnvironmentVariable("ARCGIS_PRO_PYTHON")
                         ?? Environment.GetEnvironmentVariable("CLI_ANYTHING_ARCGIS_PYTHON");
            if (!string.IsNullOrWhiteSpace(env) && File.Exists(env))
                return env;
            try
            {
                string exe = Process.GetCurrentProcess().MainModule?.FileName;
                if (!string.IsNullOrEmpty(exe))
                {
                    string candidate = Path.Combine(Path.GetDirectoryName(exe), "Python", "envs", "arcgispro-py3", "python.exe");
                    if (File.Exists(candidate)) return candidate;
                }
            }
            catch { /* ignore */ }
            return File.Exists(DefaultProPython) ? DefaultProPython : null;
        }

        private static SolidColorBrush Hex(string hex) => new((Color)ColorConverter.ConvertFromString(hex));

        private sealed class Input
        {
            public string Title, WebmapId, Basemap, OutDir, CategoryField;
            public List<string> LayerItemIds = new();
            public List<string> Widgets = new();
            public List<string> ValueFields = new();
            public int Port;
        }

        private sealed class GenResult
        {
            public bool Ok;
            public string OutputDir, Error;
        }
    }
}
