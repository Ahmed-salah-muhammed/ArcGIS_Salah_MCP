using System;
using System.Diagnostics;
using System.IO;
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
    /// Salah_MCP_CreateAppBtn ("Deploy Web App") — a dark-themed wizard that pushes
    /// the locally generated static web app to GitHub and (optionally) enables
    /// GitHub Pages for a live URL.
    ///
    /// It shells out to Pro's Python running the SAME pipeline exposed as the MCP
    /// tool <c>webapp_github_pipeline</c> (arcgis_pro_salah_mcp.webapp.github), with
    /// the token passed on stdin (never argv). Note: the MCP server is stdio and the
    /// add-in is itself the loopback server, so there is no localhost endpoint to
    /// POST to — the synchronous shell-out is what returns the live URL here.
    /// </summary>
    internal class CreateWebAppButton : ArcGIS.Desktop.Framework.Contracts.Button
    {
        private const string DefaultProPython =
            @"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe";

        // palette
        private static Brush BgDark => Hex("#252526");
        private static Brush InputBg => Hex("#1e1e1e");
        private static Brush Accent => Hex("#007acc");
        private static Brush BtnBg => Hex("#3e3e42");
        private static Brush Gray => Hex("#aaaaaa");
        private static readonly FontFamily Seg = new("Segoe UI");

        protected override async void OnClick()
        {
            try
            {
                // sensible default web-app folder (env override → <project>\webapp-build → MyDocuments)
                string defaultDir = Environment.GetEnvironmentVariable("ARCGIS_WEBAPP_OUT");
                if (string.IsNullOrWhiteSpace(defaultDir))
                {
                    string projHome = await QueuedTask.Run(() => Project.Current?.HomeFolderPath);
                    defaultDir = !string.IsNullOrEmpty(projHome)
                        ? Path.Combine(projHome, "webapp-build")
                        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "webapp-build");
                }

                var input = ShowWizard(defaultDir);
                if (input == null) return;

                var (win, label) = ShowProgress("Creating repository and pushing files…");
                DeployResult res;
                try { res = await DeployAsync(input); }
                finally { win.Close(); }

                if (res.Ok)
                {
                    string msg = $"✓ Repository:\n{res.RepositoryUrl}\n\nFiles pushed: {res.FilesPushed}";
                    if (!string.IsNullOrEmpty(res.ProductionUrl))
                        msg += $"\n\n🌐 Live site (give Pages ~1 min to build):\n{res.ProductionUrl}";
                    var open = MessageBox.Show(msg + "\n\nOpen in the browser now?",
                        "Salah MCP — Deploy Web App", MessageBoxButton.YesNo, MessageBoxImage.Information);
                    if (open == MessageBoxResult.Yes)
                        OpenUrl(res.ProductionUrl ?? res.RepositoryUrl);
                }
                else
                {
                    MessageBox.Show("Deploy failed:\n\n" + res.Error, "Salah MCP — Deploy Web App",
                        MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Deploy error: {ex.Message}", "Salah MCP — Deploy Web App");
            }
        }

        // ---------------------------------------------------------------------
        //  Wizard (dark theme, code-behind only)
        // ---------------------------------------------------------------------
        private static Input ShowWizard(string defaultDir)
        {
            var panel = new StackPanel { Background = BgDark };

            panel.Children.Add(Label("Deploy the generated web app to GitHub.", Gray, 12,
                new Thickness(0, 0, 0, 14)));

            // Repository name
            panel.Children.Add(Label("Repository / app name", null, 12, new Thickness(0, 0, 0, 4)));
            var repoBox = TextInput("my-arcgis-app");
            panel.Children.Add(repoBox);

            // GitHub token + help link
            panel.Children.Add(Label("GitHub Personal Access Token", null, 12, new Thickness(0, 12, 0, 4)));
            var tokenBox = new PasswordBox
            {
                Background = InputBg, Foreground = Brushes.White, BorderBrush = BtnBg,
                BorderThickness = new Thickness(1), Padding = new Thickness(6, 4, 6, 4),
                FontSize = 13, FontFamily = Seg, CaretBrush = Brushes.White,
            };
            panel.Children.Add(tokenBox);

            var run = new Run("How to get a token?  Click here to generate one →");
            var link = new Hyperlink(run) { Foreground = Accent };
            link.Click += (_, _) => OpenUrl("https://github.com/settings/tokens");
            panel.Children.Add(new TextBlock(link)
            {
                FontSize = 12, FontFamily = Seg, Margin = new Thickness(0, 5, 0, 0), Cursor = System.Windows.Input.Cursors.Hand,
            });

            // Web app folder
            panel.Children.Add(Label("Web app folder", null, 12, new Thickness(0, 14, 0, 4)));
            var dirBox = TextInput(defaultDir);
            panel.Children.Add(dirBox);

            // Deploy mode
            panel.Children.Add(Label("Deployment mode", null, 12, new Thickness(0, 14, 0, 6)));
            var optUpload = new RadioButton
            {
                Content = "Just upload code (create repo + push files)",
                GroupName = "mode", IsChecked = true, Foreground = Brushes.White, FontFamily = Seg,
                FontSize = 13, Margin = new Thickness(0, 0, 0, 4),
            };
            var optLive = new RadioButton
            {
                Content = "Upload code & deploy live website (enable GitHub Pages)",
                GroupName = "mode", Foreground = Brushes.White, FontFamily = Seg, FontSize = 13,
            };
            panel.Children.Add(optUpload);
            panel.Children.Add(optLive);

            // Buttons
            var deploy = PillButton.Create("Deploy", 110, 30, Accent, Brushes.White, isDefault: true);
            deploy.Margin = new Thickness(0, 0, 10, 0);

            var cancel = PillButton.Create("Cancel", 90, 30, BtnBg, Brushes.White, isCancel: true);
            var btnRow = new StackPanel
            {
                Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 18, 0, 0),
            };
            btnRow.Children.Add(deploy);
            btnRow.Children.Add(cancel);
            panel.Children.Add(btnRow);

            var window = new Window
            {
                Title = "Salah MCP — Deploy Web App",
                Content = new Border { Background = BgDark, Padding = new Thickness(18), Child = panel },
                Width = 480, SizeToContent = SizeToContent.Height, ResizeMode = ResizeMode.NoResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = BgDark,
            };

            Input result = null;
            deploy.Click += (_, _) =>
            {
                string repo = repoBox.Text?.Trim();
                string token = tokenBox.Password;
                if (string.IsNullOrWhiteSpace(repo) || string.IsNullOrWhiteSpace(token))
                {
                    MessageBox.Show("Repository name and GitHub token are both required.",
                        "Salah MCP — Deploy Web App");
                    return;
                }
                result = new Input
                {
                    RepoName = repo,
                    Token = token,
                    AppDir = dirBox.Text?.Trim(),
                    DeployMode = optLive.IsChecked == true ? "live" : "upload",
                };
                window.DialogResult = true;
            };

            window.ShowDialog();
            return result;
        }

        // ---------------------------------------------------------------------
        //  Deploy via Pro's Python (the webapp.github pipeline; token on stdin)
        // ---------------------------------------------------------------------
        private static async Task<DeployResult> DeployAsync(Input input)
        {
            string py = ResolveProPython();
            if (py == null)
                return new DeployResult { Ok = false,
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
            psi.ArgumentList.Add("arcgis_pro_salah_mcp.webapp.github");

            using var proc = new Process { StartInfo = psi };
            proc.Start();

            string payload = JsonSerializer.Serialize(new
            {
                repo_name = input.RepoName,
                token = input.Token,
                deploy_mode = input.DeployMode,
                app_dir = input.AppDir,
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
                return new DeployResult { Ok = false, Error = "pipeline failed: " + detail.Trim() };
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
                return new DeployResult { Ok = false, Error = e };
            }

            var data = root.GetProperty("data");
            return new DeployResult
            {
                Ok = true,
                RepositoryUrl = data.TryGetProperty("repository_url", out var ru) ? ru.GetString() : null,
                ProductionUrl = data.TryGetProperty("production_url", out var pu) && pu.ValueKind == JsonValueKind.String
                                ? pu.GetString() : null,
                FilesPushed = data.TryGetProperty("files_pushed", out var fp) && fp.ValueKind == JsonValueKind.Number
                              ? fp.GetInt32() : 0,
            };
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

            // Explicit "Hide" — tucks the window to the taskbar so you can go do
            // something else while the deploy keeps running in the background.
            // Restore it any time from the taskbar; it closes itself when done.
            var hide = PillButton.Create("Hide", 90, 28, BtnBg, Brushes.White);
            hide.HorizontalAlignment = HorizontalAlignment.Right;
            hide.Margin = new Thickness(0, 16, 0, 0);
            panel.Children.Add(hide);

            // Modeless + a normal title bar so you can minimize / move / resize it
            // and keep working in ArcGIS Pro while the deploy runs in the background.
            // Not Topmost, and shown in the taskbar so it can be restored after hiding.
            var win = new Window
            {
                Title = "Salah MCP — Deploying (running in background)",
                Content = new Border { Background = BgDark, Padding = new Thickness(20), Child = panel },
                Width = 420, Height = 175, MinWidth = 320, MinHeight = 140,
                SizeToContent = SizeToContent.Manual, ResizeMode = ResizeMode.CanResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = BgDark,
                WindowStyle = WindowStyle.SingleBorderWindow, ShowInTaskbar = true, Topmost = false,
            };
            hide.Click += (_, _) => win.WindowState = WindowState.Minimized;
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
            public string RepoName, Token, AppDir, DeployMode;
        }

        private sealed class DeployResult
        {
            public bool Ok;
            public string RepositoryUrl, ProductionUrl, Error;
            public int FilesPushed;
        }
    }
}
