# Claude Code kickoff prompt — Phase B (live bridge)

> **HISTORICAL (kept for reference).** This is the original prompt used to kick
> off the live bridge. The project has since moved on: the add-in lives in
> **`SalahAIBridge/`** (not `live-bridge/`), the bridge is **loopback-only with
> no token**, the default port is **2026**, and the ribbon is the 4-button
> **Salah MCP** tab (Start Server · Ping · Publish · Create Web App). For the
> current contract see `docs/PROTOCOL.md` and `CLAUDE.md` — ignore the token /
> port-8770 / `live-bridge/` details below; they reflect the original plan.

Paste the block below into Claude Code, opened in the repo
`E:\iti-9month\MCP\ArcGIS Pro Salah MCP`. It implements the **live_*** tool
group and scaffolds the .NET add-in. Build the add-in afterwards in
**Visual Studio 2026 (18.3.2+)** — it targets **.NET 10** for ArcGIS Pro 3.7.

> Workflow note: the .NET add-in is a **separate VS project inside this repo**
> (`live-bridge/`). Recommended: generate the initial skeleton from the
> **ArcGIS Pro SDK "Module Add-in" template** in VS 2026 (it wires `Config.daml`,
> assembly attributes and `.esriAddinX` packaging correctly), then let Claude
> Code fill in `BridgeServer.cs` and the command handlers. The Python package
> and the C# add-in build independently.

---

```
You are working in the repo at: E:\iti-9month\MCP\ArcGIS Pro Salah MCP

This is "ArcGIS Pro Salah MCP" — a single FastMCP server that drives the whole
ArcGIS stack. Three Python tool groups already exist and work: pro_* (headless
ArcPy), portal_* (ArcGIS API for Python), webapp_* (static Maps SDK for JS
generator). I now want to add the fourth group: live_* — driving the OPEN
ArcGIS Pro session via a .NET add-in.

STEP 0 — Read these before writing any code:
- docs/ARCHITECTURE.md   (system design + phased plan; you implement Phase B)
- docs/PROTOCOL.md       (the MCP-server <-> add-in HTTP/JSON contract)
- CLAUDE.md              (repo conventions: result envelope, lazy imports,
                          tool naming, entry-point)
- src/arcgis_pro_salah_mcp/server.py and pro/ops.py (match the existing style)

TASK — Implement Phase B: the live bridge skeleton, end to end.

PART 1 — Python side (you can fully build and test this):
1. Create src/arcgis_pro_salah_mcp/live/__init__.py
2. Create src/arcgis_pro_salah_mcp/live/client.py:
      - reads ARCGIS_BRIDGE_PORT (default 8770) and ARCGIS_BRIDGE_TOKEN from
        config.py (add these fields to Config in config.py)
      - post(command: str, params: dict) -> dict : POST to
        http://127.0.0.1:<port>/cmd with header X-Bridge-Token, JSON body
        {"command":..., "params":...}; parse and RETURN the envelope as-is.
      - On connection failure return {"ok": false,
        "error": "bridge unreachable: <detail>"} (never raise).
      - Use only the Python stdlib (urllib) — no new dependencies.
3. Create src/arcgis_pro_salah_mcp/live/ops.py with @guard-wrapped functions
    that just forward to the client: live_ping, live_list_layers, live_zoom_to,
    live_query, live_run_gp, live_add_layer, live_export_layout — params per
    PROTOCOL.md.
4. Register matching @mcp.tool() wrappers in server.py under a new
    "Layer 1b — Live session" section, prefixed live_.
5. Add backend-free tests in tests/test_core.py proving live_ping returns a
    graceful {"ok": false, ...} when no bridge is running (do NOT require a
    real ArcGIS Pro). Keep all existing tests passing.

PART 2 — .NET add-in source (scaffold the files; I will build them in
Visual Studio 2026):
Create under live-bridge/SalahAIBridge/ , targeting net10.0-windows for
ArcGIS Pro 3.7 (.NET 10):
  - SalahAIBridge.csproj  (TargetFramework net10.0-windows; references to
    ArcGIS.Desktop.Framework / ArcGIS.Core as Esri Pro SDK assemblies;
    add-in output)
  - Config.daml            (AddInInfo + a Module; optional ribbon button to
    Start/Stop the bridge)
  - Module1.cs             (Module lifecycle: start BridgeServer on
    Initialize, stop on Uninitialize; read port+token from env)
  - BridgeServer.cs        (System.Net.HttpListener bound to 127.0.0.1:<port>;
    validate X-Bridge-Token; parse JSON with System.Text.Json; route
    {command, params} to an ICommand; ALWAYS return the
    {"ok":..,"data"|"error":..} envelope; GET /health = no-auth liveness)
  - Commands/ICommand.cs   (Task<object> ExecuteAsync(JsonElement @params))
  - Commands/PingCommand.cs (return project name, maps, layouts, active view;
    include protocol_version) — wrap ALL Pro-SDK access in
    QueuedTask.Run(...) per docs/PROTOCOL.md "Execution rule".
Add a Commands stub file for ListLayers/ZoomTo/Query/RunGp/ExportLayout with
TODO bodies (Phase C). Add live-bridge/README.md explaining: open in VS 2026,
build, install the .esriAddinX, set ARCGIS_BRIDGE_TOKEN/PORT.

CONSTRAINTS:
- Follow CLAUDE.md exactly: every op returns the {"ok":...} envelope; use
  @guard; lazy-import any backend; tools are thin wrappers with one-line
  docstrings.
- Do not add Python dependencies beyond the stdlib for the live client.
- Do not break `PYTHONPATH=src python -m pytest tests/test_core.py`.
- Loopback only; token required; never log the token.

ACCEPTANCE:
- test_core passes with no ArcGIS installed.
- live_ping returns a clean "bridge unreachable" envelope when Pro/add-in is
  not running.
- The C# files are coherent and ready to open and build in Visual Studio 2026;
  PingCommand uses QueuedTask and returns the envelope.

When done, summarize what you created and give me the exact steps to build the
add-in in Visual Studio 2026 and test live_ping against a running ArcGIS Pro.
```

---

## After Phase B → Phase C

Once `live_ping` round-trips, reuse the same prompt shape for **Phase C**:
implement the remaining command handlers in the add-in
(`ListLayers`, `ZoomTo`, `Query`, `RunGp`, `ExportLayout`) — each wrapped in
`QueuedTask.Run` — and confirm each `live_*` tool against a running ArcGIS Pro.

## Environment reminders
- ArcGIS Pro **3.7** → **.NET 10**; build with **Visual Studio 2026 (18.3.2+)**.
- `.NET 9/10` removed `BinaryFormatter`; we use `System.Text.Json` only, so this
doesn't affect us.
- Set `ARCGIS_BRIDGE_TOKEN` (shared secret) and optionally `ARCGIS_BRIDGE_PORT`
in the environment for both the add-in and the MCP server.
