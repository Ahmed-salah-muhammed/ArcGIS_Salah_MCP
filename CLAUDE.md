# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
A four-layer MCP server (FastMCP, stdio) that drives ArcGIS end to end, plus the
ArcGIS Pro .NET add-in that pairs with it. Each layer is a tool prefix backed by
a different Esri technology:
- `pro_*`    → ArcPy (headless geoprocessing on `.aprx` projects & `.gdb` files)
- `live_*`   → loopback HTTP to the SalahAIBridge .NET add-in inside the OPEN Pro session
- `portal_*` → ArcGIS API for Python (ArcGIS Online / Enterprise Portal)
- `webapp_*` → static ArcGIS Maps SDK for JS **5.0** generator (no backend):
  `webapp_create` (web app), `webapp_create_dashboard` (dashboard),
  `webapp_github_pipeline` (deploy to GitHub + Pages)

113 tools in total, grouped into toolsets (see **Performance** below).

Canonical agent workflow: **Pro (analyze) → Portal (publish) → WebApp (visualize)**.

## Commands
```bash
# Tests — must pass with NO ArcGIS installed (this is the whole CI surface)
PYTHONPATH=src python -m pytest tests/test_core.py
PYTHONPATH=src python -m pytest tests/test_core.py::test_webapp_generator_with_webmap
```
```powershell
# PowerShell equivalent (this repo is Windows-only in practice; `&&` is not valid in PS 5.1)
$env:PYTHONPATH = "src"; python -m pytest tests/test_core.py -q
```
```bash
# Install into ArcGIS Pro's bundled interpreter (the only one that has ArcPy)
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" -m pip install -e .
# portal_* dependency (heavy, optional):
"...\arcgispro-py3\python.exe" -m pip install -e ".[portal]"

# Run the server — ALWAYS via the entry point or -m, never `python server.py`
arcgis-pro-salah-mcp                                  # package-relative imports need the install
"...\arcgispro-py3\python.exe" -m arcgis_pro_salah_mcp.server

# Build a sample.gdb to exercise the Pro tools (needs arcgispro-py3)
"...\arcgispro-py3\python.exe" demos/setup_sample.py
```
```powershell
# The .NET add-in (net10.0-windows, WPF; references Pro SDK DLLs by absolute path)
dotnet build SalahAIBridge\SalahAIBridge.csproj -c Release
# → bin\Release\net10.0-windows\SalahAIBridge.esriAddinX ; double-click to install.
# ArcGIS Pro MUST be closed before reinstalling, or the old add-in stays loaded.
# Pro installs it to  …\Documents\ArcGIS\AddIns\ArcGISPro\{6f3a1c2e-…}\  — check the
# version in Add-In Manager (Config.daml `version`, currently 0.1.2) to confirm a reload.
```

## Architecture
Two-file pattern per layer: **`ops.py` does the work, `server.py` exposes it.**
- `server.py` — the only place `@mcp.tool()` wrappers live. Each is a thin
  pass-through to an op; **its one-line docstring is the entire spec the model
  sees**, so keep docstrings accurate and self-contained.
- `pro/ops.py`, `live/ops.py`, `portal/ops.py`, `webapp/{generator,dashboard,github}.py`
  — the implementations, every public function `@guard`-decorated.
- `_result.py` — the `ok()` / `err()` envelope and the `guard` decorator. `guard`
  catches all exceptions into `err(...)` envelopes (so a tool never breaks the
  MCP transport), maps `NotImplementedError` → `code="not_implemented"`, and
  accepts either a raw payload (auto-wrapped in `ok()`) or a full envelope.
- `config.py` — `CONFIG` singleton, all env-driven (table below). The same build
  targets AGOL or Enterprise; `CONFIG.is_enterprise()` just checks the URL.
- `pro/cache.py`, `pro/context.py`, `pro/pipeline.py` — the latency layer; see
  **Performance** below.
- `pro/schema.py` (domains/subtypes/attribute rules), `pro/validate.py`
  (topology + geometry), `pro/geoai.py` (deep learning / VLM / foundation
  models), `pro/unet.py` (Utility Network) — each follows the same
  `@guard`-per-op convention.
- `pro/toolbox.py` (`.pyt`) and `pro/modelbuilder.py` (`.atbx`) — **pure Python,
  no arcpy**, so both are fully unit-tested with no ArcGIS installed. See
  **Toolbox authoring** below.
- `pro/metadata.py` — **backend-free** (no arcpy/arcgis) theme inference: turns a
  layer's name + field names + geometry into an AGOL item summary/description/tags.
  Keyword *prefix* matching, not substring ("age" must not match "acreage").
  Because it is pure it is unit-tested in `test_core.py` and also runs inside the
  publish script the ribbon shells out to.
- `bootstrap.py` — ArcPy discovery, `os.execv` re-exec under `arcgispro-py3`,
  and the **arcpy warm-up**. `prepare()` is called from `main()`.
  `running_under_arcgis_python()` is a *path* check on purpose: probing with
  `import arcpy` would cost the very 25 s the warm-up exists to hide.

State & backend notes:
- `portal/ops.py` holds the active `GIS` connection in a **module-level `_GIS`**.
  The agent calls `portal_connect` once; every other `portal_*` op requires it.
- `webapp/` has **no ArcGIS dependency** — `generator.py` / `dashboard.py`
  string-replace `__JS_SDK_VERSION__` / `__APP_TITLE__` in their template
  (`templates/index.html` / `dashboard.html`), copy the JS verbatim (`app.js` /
  `dashboard.js`), and emit `config.js` (`window.APP_CONFIG`). Built on Maps SDK
  for JS **5.0** (single CDN module bundle, Calcite + `<arcgis-*>` components,
  `$arcgis.import()` — no AMD `require`). The dashboard introspects fields in the
  browser, which is *why* the generator stays ArcGIS-free and testable.
  Valid `widgets`: `legend, layerList, search, basemapGallery, home`.
- Escape hatches in `pro/ops.py`: `run_gp(tool, args, kwargs)` runs any GP tool by
  dotted name (`"analysis.Buffer"`); `execute_code(code)` execs arbitrary ArcPy.

## The live bridge (`live_*` ⇄ SalahAIBridge)
`docs/PROTOCOL.md` is authoritative for the wire contract — update it whenever
you touch either side. `live/client.py` is stdlib-`urllib` only and **never
raises**: transport failures come back as `err("bridge unreachable: …")`.

- **`live/policy.py` gates every mutating command client-side, before the HTTP
  request is sent.** Two rails: read-only mode (`ARCGIS_BRIDGE_READONLY` blocks
  `run_gp`/`add_layer`/`export_layout`, code `readonly`) and destructive
  confirmation (`run_gp` needs `confirm=True`, code `confirm_required`). Adding a
  new `live_*` op means classifying its command in `READ_ONLY` / `MUTATING` /
  `DESTRUCTIVE` and calling `policy.check(...)` first. Names there are *protocol*
  commands (`run_gp`), not tool names (`live_run_gp`).
- **Reverse channel.** MCP is client→server only, so the add-in cannot call the
  agent. Instead a ribbon button parks the user's intent in the C# static
  `AppRequest` (`AppRequest.cs`) and the agent polls it with `live_get_request`
  (`{pending, kind, text, created_at}`; `kind` is `web_app` or `publish`).
- C# handlers must do all project/map/layout work inside `QueuedTask.Run(...)`
  (Main CIM Thread); the HttpListener callback is a worker thread.

## The C# ⇄ Python shell-out contract
The ribbon's Publish / Create Web App / Create Dashboard / Deploy buttons do NOT
go through MCP or the bridge — they spawn `arcgispro-py3\python.exe -m
arcgis_pro_salah_mcp.webapp.{generator,dashboard,github}`, write a JSON payload
to **stdin** (secrets like the GitHub token go here, never argv), and scan stdout
for a single `SALAH_RESULT:<envelope-json>` line. Each of those modules has a
`_main()` implementing that contract; keep the two sides in step.

**Naming trap in `SalahAIBridge/UI/`:** the filenames do not match the buttons.
`CreateWebAppButton.cs` is the **Deploy Web App** button (`Salah_MCP_CreateAppBtn`);
`PreviewWebAppButton.cs` is the **Create Web App** button (`Salah_MCP_PreviewAppBtn`).
`Config.daml` is the source of truth for which class backs which caption.

Other add-in notes:
- **No licensing/auth gate.** The ribbon has seven buttons across three groups,
  all always enabled — `Config.daml` declares no `<conditions>` and no button
  carries a `condition=` attribute. (An API-key/plan-tier system was removed; do
  not reintroduce `condition=` gating without also restoring a manager for it.)
  The only sign-in anywhere is ArcGIS Pro's own portal login, which
  `PublishButton` checks before publishing.
- `UI/PillButton.cs` is the shared button factory (rounded "pill" chrome via a
  small `ControlTemplate`, since WPF's default Button ignores `CornerRadius`).
  Use it for new dialog buttons so they match.
- `ZoomToActiveLayerButton` runs fully in-process (`QueuedTask` + `MapView.Active`)
  — it does not go through the bridge. Its icon is not produced by
  `Images/make_icons.py`, which only generates the other six.

## Performance — the numbers that drive the design
Measured on this repo's machine (ArcGIS Pro 3.x). Re-measure before "optimising"
anything here; several obvious ideas turned out to be wrong.

| Operation | Cost |
| --- | --- |
| `import arcpy` | **25.1 s** (once per process) |
| Opening an `.aprx` | 0.15 s cold, 0.01 s warm — **not** a bottleneck |
| `Describe` / `ListFields` / `GetCount` | 73 / 172 / 189 ms |
| one uncached `pro_describe_layer` | ~430 ms |
| full tool schema, sent every request | 113 tools = **17.7k tokens** |

The dominant cost is **round trips**, not ArcPy: every extra tool call is a full
model inference. Four mechanisms address that:

1. **Warm-up** (`bootstrap.start_warmup`) — imports arcpy on a daemon thread at
   startup so the user's first call does not pay the 25 s. Disable with
   `ARCGIS_SALAH_NO_WARMUP=1`.
2. **`pro_context`** — project + maps + layers + schemas + counts + sample rows
   in ONE call. Measured 5.4 s cold / 1.2 s warm for 11 datasets.
3. **`pro/cache.py`** — caches the Describe+ListFields+GetCount triple; measured
   0.354 s -> 0.001 s on a repeat. **Do not** stamp geodatabase datasets by
   container mtime: ArcGIS writes `.lock` files there on every *read*, so the
   cache then never hits (verified: 0 hits over two identical calls). Gdb entries
   use explicit invalidation plus a TTL (`ARCGIS_SALAH_CACHE_TTL`, default 300 s;
   0 disables). Plain files (shapefile, GeoTIFF) do use their own mtime.
4. **`pro_pipeline` / `pro_job_submit`** — chain N geoprocessing steps in one
   call (`{{prev}}`, `{{stepN}}` placeholders), or run them on a worker thread
   and poll with `pro_job_status`.

**Toolset gating.** `ARCGIS_SALAH_TOOLSETS` prunes tool groups at startup:
`core,portal,webapp` takes 113 tools -> 53 and 17.7k -> 7.4k tokens. Groups:
`core` (always kept), `geoai`, `unet`, `schema`, `validate`, `authoring`,
`live`, `portal`, `webapp`. `pro_toolsets` reports what is active.

Also: `field_statistics` uses `TableToNumPyArray` + numpy (329 ms -> 184 ms on
13.5k rows) with a cursor fallback for non-numeric fields, and `get_features`
excludes Geometry/Blob/Raster fields unless `include_geometry=True` — a
stringified polygon is slow and costs hundreds of tokens for nothing.

## Toolbox authoring — verified facts
Both generators are pure Python, and both were validated by generating a
toolbox, importing it with `arcpy.ImportToolbox`, and **running it on real data**.

- **`.pyt`** is a plain Python module. `pro/toolbox.py` renders it and
  `compile()`s the source before writing, so a malformed `execute_code` snippet
  fails at generation time rather than silently at load.
- **`.atbx` is a ZIP of JSON** (verified against Pro's own
  `Resources/ArcToolBox/Services/PrintingTools.atbx`): `toolbox.content`,
  `toolbox.content.rc`, and per tool `<Name>.tool/{tool.content, tool.content.rc,
  tool.model, tool.model.diagram}`. `tool.model` holds `variables` (the canvas
  data elements) and `processes` (the tool boxes); chaining is exactly process A
  writing to variable *n* and process B reading `element_id` *n*.
- **Two non-obvious requirements for a chained model**, both found empirically:
  an intermediate variable needs BOTH a `value` (without it the wrapped tool
  fails "Output Feature Class: Value is required") AND a `datatype` (without it
  the *next* step fails validation with "The value is not a Feature Class").
  The value uses an inline `%scratchgdb%` variable that ModelBuilder resolves at
  run time, so the generated model stays portable.
- No `tool.model.diagram.xml` is written — ModelBuilder auto-lays-out the canvas,
  which is far safer than hand-generating that format.

## Conventions (follow when adding tools)
1. **Result envelope.** Return `ok(data)` / `err(msg)` from `_result`; decorate
   the op with `@guard`.
2. **Lazy backend imports.** Never import `arcpy`/`arcgis` at module top level —
   import inside `_arcpy()` / `_arcgis()` so the package imports (and
   `test_core.py` runs) on machines without ArcGIS.
3. **Tool naming.** Prefix by layer: `pro_`, `live_`, `portal_`, `webapp_`.
4. **Adding a tool:** write the `@guard` op in the relevant `ops.py`, then a thin
   `@mcp.tool()` wrapper in `server.py` with a one-line docstring.

## Key constraint
ArcPy **cannot attach to a running ArcGIS Pro session** (Esri limitation), so the
`pro_*` tools operate on `.aprx`/`.gdb` files on disk. To drive the *live* session
the `live_*` tools POST to the **SalahAIBridge** .NET add-in — a loopback HTTP
listener (port 2026, no token) running inside Pro.

## Configuration
| Variable | Default | Read by |
| --- | --- | --- |
| `ARCGIS_PORTAL_URL` | `https://www.arcgis.com` | `config.py` (AGOL or Enterprise) |
| `ARCGIS_PROFILE` / `ARCGIS_USERNAME` | — | `config.py` → `portal_connect` |
| `ARCGIS_JS_SDK_VERSION` | `5.0` | `config.py` → generated `index.html` |
| `ARCGIS_WEBAPP_OUT` | `webapp-build` | `config.py`, and the C# buttons |
| `ARCGIS_BRIDGE_PORT` | `2026` | `config.py` **and** the add-in — both sides |
| `ARCGIS_BRIDGE_READONLY` | `false` | `live/policy.py` |
| `CLI_ANYTHING_ARCGIS_PYTHON` | — | **Python only** (`config.py`, `bootstrap.py`) |
| `ARCGIS_PRO_PYTHON` | — | **C# only** (the UI buttons' interpreter lookup) |
| `ARCGIS_SALAH_TOOLSETS` | all | Comma list of tool groups to keep (see Performance) |
| `ARCGIS_SALAH_CACHE_TTL` | `300` | Schema-cache TTL in seconds; `0` disables it |
| `ARCGIS_SALAH_NO_WARMUP` | `0` | Truthy skips the background arcpy import |
| `ARCGIS_SALAH_NO_REEXEC` | `0` | Truthy skips the `arcgispro-py3` re-exec |

The last two are *not* interchangeable despite naming the same thing — set both
if you keep Pro somewhere non-standard.

## Tests
`tests/test_core.py` must stay backend-free and green with **no ArcGIS installed**
(53 tests). It covers the envelope/guard behavior, graceful ArcPy- and
bridge-absent degradation, the `live/policy.py` rails, `pro/metadata.py` theme
inference, both webapp generators, the cache (including the gdb-lock-file case
and the TTL), pipeline placeholder substitution, the `.pyt` and `.atbx`
generators, toolset gating, and the argument validation that deliberately runs
*before* any backend import. Anything needing a real ArcGIS install belongs in a
separate `test_full_e2e.py` (planned), not here.

**Validate before importing the backend.** Ops such as `schema.add_attribute_rule`
and `unet.trace` check their arguments first and only then call `_arcpy()`. A bad
call then fails in microseconds instead of after a 25 s import — and the
validation stays unit-testable in this file.

## Where the open work is
- `pro_apply_categorized_symbology` / `pro_apply_graduated_symbology` build
  `lyr.symbology` renderers (UniqueValue / GraduatedColors) — implemented.
- Web app + dashboard generators (Maps SDK 5.0), mixed Feature/Tile/Vector-Tile
  publishing, and GitHub deploy are implemented (ribbon **v0.1.2**).
- `ROADMAP.md` has the rest: Enterprise auth, renderer-JSON builders, Vite output,
  client-side symbology overrides, live-bridge round-trip verification against a
  running Pro, and server-side (add-in) enforcement of the policy rails as
  defense-in-depth.
