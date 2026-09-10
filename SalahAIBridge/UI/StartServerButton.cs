using System;

using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace SalahAIBridge
{
    /// <summary>
    /// Salah_MCP_StartServerBtn — start / stop the live bridge listener (the real
    /// <see cref="Module1"/> / <see cref="BridgeServer"/>). Loopback-only, no token.
    /// This is the entry point: start the server, then talk to Claude / Antigravity
    /// over MCP.
    /// </summary>
    internal class StartServerButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                var module = Module1.Current;

                if (module.IsRunning)
                {
                    module.StopBridge();
                    MessageBox.Show("MCP Live Bridge stopped.", "Salah MCP");
                }
                else
                {
                    module.StartBridge();
                    MessageBox.Show(
                        module.IsRunning
                            ? $"MCP Live Bridge started on http://127.0.0.1:{ReadPort()}\n\n" +
                              "Now talk to Claude / Antigravity over MCP."
                            : "Bridge failed to start — check ARCGIS_BRIDGE_PORT and the Debug output.",
                        "Salah MCP");
                }

                Caption = module.IsRunning ? "Stop Server" : "Start Server";
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Start/Stop failed: {ex.Message}", "Salah MCP");
            }
        }

        private static int ReadPort() =>
            int.TryParse(Environment.GetEnvironmentVariable("ARCGIS_BRIDGE_PORT"), out var p) && p > 0 ? p : 2026;
    }
}
