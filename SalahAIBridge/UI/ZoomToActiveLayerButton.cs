using System;
using System.Linq;

using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge
{
    /// <summary>
    /// Salah_MCP_ZoomToActiveLayerBtn — User-role tool. Zooms to the full extent
    /// of the first visible layer in the active map. A quick, read-only "orient
    /// yourself" action — no editing, no publishing, matching what the User ribbon
    /// group is meant for.
    /// </summary>
    internal class ZoomToActiveLayerButton : Button
    {
        protected override async void OnClick()
        {
            try
            {
                await QueuedTask.Run(() =>
                {
                    var mapView = MapView.Active
                        ?? throw new InvalidOperationException("No active map view.");
                    var layer = mapView.Map.GetLayersAsFlattenedList()
                        .OfType<BasicFeatureLayer>()
                        .FirstOrDefault(l => l.IsVisible)
                        ?? throw new InvalidOperationException("No visible feature layer in the active map.");

                    mapView.ZoomTo(layer.QueryExtent(), TimeSpan.FromSeconds(0.8));
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Couldn't zoom: {ex.Message}", "Salah MCP");
            }
        }
    }
}
