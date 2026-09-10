using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace SalahAIBridge
{
    /// <summary>
    /// Add-in module lifecycle. On load it starts <see cref="BridgeServer"/> — a
    /// loopback HTTP listener the MCP server's live_* tools POST commands to — and
    /// stops it on unload. The listener port is read from the environment
    /// (ARCGIS_BRIDGE_PORT, default 2026); loopback-only, no token — local use.
    /// </summary>
    internal class Module1 : Module
    {
        private static Module1 _this;
        private BridgeServer _server;

        /// <summary>Singleton accessor used by the Framework and the ribbon buttons.</summary>
        public static Module1 Current =>
            _this ??= (Module1)FrameworkApplication.FindModule("SalahAIBridge_Module");

        /// <summary>True while the loopback listener is accepting requests.</summary>
        public bool IsRunning => _server is { IsListening: true };

        /// <summary>Called by the Framework when the module loads (autoLoad="true").</summary>
        protected override bool Initialize()
        {
            StartBridge();
            return base.Initialize();
        }

        /// <summary>Called by the Framework when the module unloads / Pro shuts down.</summary>
        protected override void Uninitialize()
        {
            StopBridge();
            base.Uninitialize();
        }

        /// <summary>Start the listener if it isn't already running. Safe to call twice.</summary>
        public void StartBridge()
        {
            if (IsRunning) return;

            int port = ReadPort();
            try
            {
                _server = new BridgeServer(port);
                _server.Start();
            }
            catch (Exception ex)
            {
                _server = null;
                // Never let a listener problem crash Pro startup; the user can
                // retry from the ribbon button after fixing the port.
                System.Diagnostics.Debug.WriteLine($"[SalahAIBridge] failed to start: {ex.Message}");
            }
        }

        /// <summary>Stop the listener if running. Best effort; never throws.</summary>
        public void StopBridge()
        {
            try { _server?.Stop(); }
            catch { /* best effort on shutdown */ }
            finally { _server = null; }
        }

        private static int ReadPort()
        {
            var raw = Environment.GetEnvironmentVariable("ARCGIS_BRIDGE_PORT");
            return int.TryParse(raw, out var p) && p > 0 ? p : 2026;
        }

        // Only allow Pro to unload us after we've released the listener.
        protected override bool CanUnload() => true;
    }
}
