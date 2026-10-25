# The open ArcGIS Pro session (live_*)

ArcPy cannot attach to a running Pro, so `live_*` tools POST to the SalahAIBridge add-in (127.0.0.1:2026).

- `live_ping` - project path, maps, layouts, add-in version. "CURRENT" in pro_* tools resolves through it.
- `live_map action=layers|query|zoom|add_layer`.
- `live_run_gp` - runs a GP tool inside Pro, output added to the map; needs confirm=True (add-in 0.1.6 fixed
  the NullReferenceException of earlier versions).
- `live_export_layout`, `live_get_request` (what the user queued from the ribbon), `live_add_to_project`
  (.sde/.gdb -> Databases, .atbx/.tbx/.pyt -> Toolboxes).
- `ARCGIS_BRIDGE_READONLY=true` blocks every mutating command before it is sent.
- If calls time out right after adding a .pyt: Pro is showing its "trust" dialog - the user must click Trust.
- After reinstalling the add-in Pro must be closed first; after server code changes the MCP CLIENT must restart.
