# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
A four-layer MCP server (FastMCP, stdio) that drives ArcGIS end to end, plus the
ArcGIS Pro .NET add-in that pairs with it. Each layer is a tool prefix backed by
a different Esri technology:
- `pro_*`    → ArcPy (headless geoprocessing on `.aprx` projects & `.gdb` files)
- `live_*`   → loopback HTTP to the SalahAIBridge .NET add-in inside the OPEN Pro session
- `portal_*` → ArcGIS API for Python 2.4.3 (ArcGIS Online / Enterprise), signed in through ArcGIS Pro first
- `webapp_*` → static ArcGIS Maps SDK for JS **5.0** generator (no backend):
  `webapp_create` (`kind=app|dashboard`), `webapp_github_pipeline` (deploy to GitHub + Pages)

72 tools (was 156: related operations were merged into one tool with an `action` argument), 23 MCP
resources + 1 template (playbooks `arcgis://guide/<topic>`, live project, my content, one item) and 11 MCP
prompts, grouped into toolsets (see
**Performance** below).

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

# Regenerate llms.txt + llms-full.txt (tool reference read from the code) after changing any tool
"...\arcgispro-py3\python.exe" scripts/build_llms.py

# Build a sample.gdb to exercise the Pro tools (needs arcgispro-py3)
"...\arcgispro-py3\python.exe" demos/setup_sample.py
```
```powershell
# The .NET add-in (net10.0-windows, WPF; references Pro SDK DLLs by absolute path)
dotnet build SalahAIBridge\SalahAIBridge.csproj -c Release
# → bin\Release\net10.0-windows\SalahAIBridge.esriAddinX ; double-click to install.
# ArcGIS Pro MUST be closed before reinstalling, or the old add-in stays loaded.
# Pro installs it to  …\Documents\ArcGIS\AddIns\ArcGISPro\{6f3a1c2e-…}\  — check the
# version in Add-In Manager (Config.daml `version`, currently 0.1.6) to confirm a reload.
```

## Architecture
Two-file pattern per layer: **`ops.py` does the work, `server.py` exposes it.**
- `server.py` — the only place `@mcp.tool()` wrappers, `@mcp.resource` and `@mcp.prompt` live. Each tool is a
  thin pass-through to `@guard`-ed ops; **its docstring is the entire spec the model sees**, so keep docstrings
  accurate and self-contained. Related operations share ONE tool with an `action` (or `kind`/`what`/`task`/
  `operation`) argument, routed by `_dispatch(tool, action, {name: lambda: ...})` (unknown action ->
  `code="bad_action"` listing the valid ones); `_need(arg=value, ...)` reports missing arguments before any
  backend import. When adding an operation, prefer a new action on an existing tool over a new tool.
- `pro_batch` (server.py): N tool calls of any layer in one round trip; `_ORIGINAL_FN` holds every tool's sync
  function; `{{callN.<path>}}` / `{{prev...}}` resolve into earlier envelopes (a string that is exactly one
  placeholder keeps the original type); arcpy tools go through `arcpy_thread.run`, others run in place or in a
  thread pool (`parallel=true`); `background=true` runs the batch on `pipeline._JOB_POOL` (NOT the arcpy thread,
  which would block arcpy while waiting on the network). Jobs are polled with `pro_server(action="job")`.
- Resources: every `guides/*.md` file is registered as a static resource `arcgis://guide/<stem>` (a static
  resource function must take no parameters, hence `_guide_reader`'s closure), plus `arcgis://guides`,
  `arcgis://live/project`, `arcgis://portal/my-content` and the TEMPLATE `arcgis://portal/item/{item_id}`
  (`content.item_context`). Resources are only CONTEXT worth attaching without a tool call; status, toolsets
  and the module map were removed as resources because they duplicated `pro_server` / `portal_api`.
  Network-touching resources are `async` + `asyncio.to_thread`: FastMCP awaits a coroutine resource but calls
  a plain function on the event loop. Claude Desktop only shows a resource to the model when the USER
  attaches it, so the playbooks are also served by `pro_server(action="guide", topic=...)`, and the prompts
  point there, not at the URIs.
  A test fails if a playbook names a tool that does not exist.
- `stdio_guard.py` — `main()` replaces `sys.stdout` with a router whose `.buffer` is the real stdout (FastMCP's
  stdio transport wraps that) and whose text writes go to a per-thread capture buffer or stderr, so a library
  `print()` can never corrupt the JSON-RPC stream. `portal_python` captures through it.
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
- `pro/dl_notebooks.py` — **pure Python** deep-learning *notebook* authoring, exposed as
  ONE tool, `pro_dl_notebook` (every tool schema is sent on every request, so four tools
  became one: ~1,180 -> ~660 tokens). Flow: called with only a `goal` it returns
  `status=ask_user` (recommended model + the interview questions) or `status=choose`
  (plain "classification" is ambiguous: pixel / object / detect); the agent ASKS the user;
  called again with the answers it writes the `.ipynb` (`status=created`) into the open
  Pro project's folder, found through the live bridge. It keeps asking until
  `training_data`, `batch_size` and `epochs` exist, and `create_training_notebook`
  refuses with `needs_answers`, so the interview cannot be skipped
  (`data_path_later=True` waives only the path, and only when the user said so).
  Routing used to send EVERY classification request to Mask R-CNN (found by testing it);
  object classification is `FeatureClassifier` (formats Labeled_Tiles / MultiLabeled_Tiles
  / Imagenet, metric `plot_confusion_matrix` only, applied with
  `ClassifyObjectsUsingDeepLearning`). Every constructor/`fit` keyword it emits was read
  from the installed `arcgis.learn` (API 2.4.3) with `inspect.signature`;
  `MODELS[...]["init_args"]` is that record, so add a model only after re-reading its
  signature. Other facts that shaped it: `import arcgis.learn` took **138 s** cold;
  `prepare_data` defaults to `batch_size=64`, `chip_size=224`, `val_split_pct=0.1`;
  SamLoRA/MMSegmentation have no metric method; MMDetection/MMSegmentation need a
  mandatory `model` name (left out). GroundingDINO was dropped from the notebooks:
  `arcpy.ia.DetectObjectsUsingText` returned zero-size boxes at the raster's top-left
  corner in two CPU runs (1024x1024/4 tiles, and a clean 512x512 single tile) and one
  detection for a scene with dozens of buildings; cause unknown (GPU / tile size /
  `return_box_only` untried). `pro_geoai_text` still exists, but do not
  present its output as locations.
  Change detection has TWO routes and plain "change" asks which: the ready-made Esri
  "Building Change Detection" DLPK (item `a779106c...`, 93 MB) via
  `create_pretrained_notebook` — no training, so NO batch size/epochs questions; per its
  item page: 10-20 cm aligned imagery for both dates, continuous change-magnitude output,
  not fine-tunable, flagged 'Requires Subscription' (Esri Master License) — or a trainable
  `ChangeDetector` (data exported with `in_raster2` -> folders images/images2/labels,
  which `prepare_data` auto-detects as ChangeDetection; metric `precision_recall_score`;
  `attention_type` PAM|BAM). Both are applied with `DetectChangeUsingDeepLearning`. Add a
  new ready-made model only after reading its item page the same way (`PRETRAINED`).
- Notebooks: Pro writes **nbformat 4.4**, kernelspec `{"name": "python3",
  "display_name": "ArcGISPro"}`, and the `ArcGISPro` kernelspec folder has no
  `kernel.json`, so headless execution (`nbclient`, kernel `python3`) works only when
  launched from Pro's own Python. A `.ipynb` in the project folder is **not**
  registered: the `.aprx` is a zip whose `GISProject.json` holds a `CIMProjectItem`
  (`itemType: "Notebook"`, relative `catalogPath`, absolute `pathHint`) per notebook.
  The SDK's public `Geoprocessing.OpenNotebook` takes no path, so we cannot open one in
  the live session; the user adds it via Catalog > Notebooks > Add and Open Notebook.
- `pro/egdb.py` — the `egdb` toolset (35 `pro_egdb_*` tools): enterprise geodatabase create/enable,
  connections, users/roles/privileges, versioning, archiving, administration, replication, cloud
  preparation. Each user brings their OWN keycodes (`authorization_file` or env
  `ARCGIS_EGDB_AUTHORIZATION_FILE`) - never put a license in the repo. Secrets are env-var NAMES. Arguments
  are validated before `_arcpy()`; irreversible ops need `confirm=True` (`confirm_required`). The account
  chores with no GP tool (drop user/role, set password, per-privilege GRANT/REVOKE) go through
  `arcpy.ArcSDESQLExecute` with names validated by `_NAME` and passwords as escaped literals; the
  statement shown to the caller is built separately with the password masked (replacing the raw
  password missed its escaped form and leaked it - caught by a test). See **Enterprise geodatabase -
  verified facts** below.
- `pro/symbology.py` — AM/FM utility symbology (`ESRI AMFM Water` glyph font) built on
  CIM. A hand-built `CIMUniqueValueRenderer` inspected as valid yet drew nothing; the
  working approach lets `lyr.symbology.updateRenderer` build the renderer and only
  swaps each class's symbol. ArcPy locks an `.aprx` for the life of the process, so
  open it once per call and run render steps in a fresh process.
- `pro/toolbox.py` (`.pyt`) and `pro/modelbuilder.py` (`.atbx`) — **pure Python,
  no arcpy**, so both are fully unit-tested with no ArcGIS installed. See
  **Toolbox authoring** below.
- `pro/metadata.py` — **backend-free** (no arcpy/arcgis) theme inference: turns a
  layer's name + field names + geometry into an AGOL item summary/description/tags.
  Keyword *prefix* matching, not substring ("age" must not match "acreage").
  Because it is pure it is unit-tested in `test_core.py` and also runs inside the
  publish script the ribbon shells out to.
- `arcpy_thread.py` — **one thread owns arcpy** (the Python counterpart of Pro's MCT). Measured: with a
  PostgreSQL gdb, `arcpy.ListVersions` (ERROR 87395), `ArcSDESQLExecute` (ERROR 87413) and
  `arcpy.da.ListVersions` (TypeError(path)) fail on ANY thread other than the one that imported arcpy -
  main thread included, every retry; SQL Server was fine. `bootstrap.start_warmup` submits the import to that
  thread, `pipeline` jobs run there, and `server._route_tools()` re-points every registered sync tool: arcpy
  `pro_*` tools -> `arcpy_thread.make_async` (single thread, serialised), the rest (`_NOT_ARCPY`, `live_*`,
  `portal_*`, `webapp_*`) -> `asyncio.to_thread`. Reason for the second half: FastMCP calls a sync tool
  directly on the event loop (`func_metadata.call_fn_with_arg_validation`), so one long call froze the server
  and the calls behind it hit the client's ~1 min timeout (reported from a real Claude Desktop session).
  Verified over real stdio: `pro_server action=status` answered in 0.0 s during a 15 s call. Module-level tool
  functions are untouched; `_ORIGINAL_FN` keeps them for `pro_batch` (any tools, `background=true` = a job,
  polled with `pro_server action=job`). The first arcpy call after start waits for the import (~47 s measured with a
  PostgreSQL connection).
- `"CURRENT"`: `pro.ops._resolve_current` maps it to the open project's `.aprx` via the bridge's `ping`;
  `execute_code` rewrites `ArcGISProject("CURRENT")` and returns a note (saves from here are overwritten when Pro
  saves). After code changes the MCP CLIENT must be restarted: a real test ran a server started before the
  egdb branch was checked out, so none of the new tools existed in it.
- `bootstrap.py` — ArcPy discovery, `os.execv` re-exec under `arcgispro-py3`,
  and the **arcpy warm-up**. `prepare()` is called from `main()`.
  `running_under_arcgis_python()` is a *path* check on purpose: probing with
  `import arcpy` would cost the very 25 s the warm-up exists to hide.

State & backend notes:
- `portal/` — the ArcGIS API for Python layer (see **Portal - verified facts**). `connections.py` keeps NAMED
  `GIS` connections; `get("default")` opens itself on first use (`source="auto"`: `GIS("pro")` -> profile ->
  url+username+`password_env` -> `api_key_env` -> anonymous) and reconnects once on 498/499. `content.py`
  (search, items, folders, publish, share, clone), `access.py` (users, groups, roles, CSV onboarding ported
  from the user's `bulk_add_users.py`; its validation helpers are pure and unit-tested), `layers.py`, `maps.py`
  (web-map JSON written directly, no `arcgis.map` import), `apps.py`, `analysis.py`, `admin.py`,
  `knowledge.py` (index of the installed library), `pyexec.py` / `rest.py` (escape hatches), `safety.py`
  (confirm, secrets, audit log, limits, credits), `serialize.py` (never return raw arcgis objects),
  `_common.py` (ids over names: items, groups, folders), `_imports.py` (one lock for every arcgis import).
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
- **Registering a notebook in the open project was tried and REMOVED — do not retry the same way.**
  `arcpy` has no notebook API (`ArcGISProject` has no `getDefinition`) and the public SDK cannot add one
  by path. Reading `ArcGIS.Desktop.Catalog.dll` metadata found `GeoDatabaseModule.AddNotebookProjectItem
  (notebookFilePath, bAddToFavorite)` (`internal static Task<Item>`); called by reflection on Pro 3.7 it
  returns WITHOUT an item, and the public `ItemFactory.Create` + `Project.AddItem` returns `false`, so
  nothing registered (note `Project.GetNotebooks()` is null while a project has no notebooks). An untried
  lead: a notebook file's context menu has the generic `esri_core_addToProject` command
  (`Resources/.../Extensions/Catalog/ADGeoDatabase.daml`). Until then the user adds the notebook by hand.
- **Tests must never reach a real bridge**: with the add-in running they could register temp files in the
  developer's project, so the autouse fixture in `tests/test_core.py` blocks `client.post` and the bridge
  probe for the DL and toolbox-registration tests.
- **`add_database`** (`Commands/AddDatabaseCommand.cs`, add-in 0.1.5): same public route as add_toolbox for
  `.sde` files / `.gdb` folders, verified against `GetItems<GDBProjectItem>()` (`ArcGIS.Desktop.Catalog`).
  `pro_egdb_connection action=create` calls it after writing the file (MCP wrapper default). Compiles; it has not
  been run in a live Pro yet (Pro was closed when it was built).
- **`add_toolbox`** (`Commands/AddToolboxCommand.cs`, add-in 0.1.4): registers an `.atbx`/`.tbx`/`.pyt`
  in the open project's Toolboxes via the PUBLIC `ItemFactory.Create` + `Project.AddItem`, verified
  against `GetItems<GeoprocessingProjectItem>()` (type lives in `ArcGIS.Desktop.GeoProcessing`).
  **Verified live** (Pro 3.7, add-in 0.1.4): a generated `.pyt` was registered in the open project and
  confirmed by `GetItems<GeoprocessingProjectItem>`. Trap: the FIRST time a `.pyt` is loaded Pro shows a
  "trust the code source" dialog on the UI thread; every bridge command (even `ping`) then times out until
  the user clicks Trust, after which the pending call completes.
- **`run_gp`** (`Commands/RunGpCommand.cs`, fixed in add-in 0.1.6): calling `Geoprocessing.MakeValueArray` /
  `ExecuteToolAsync` straight from the HttpListener thread gave a NullReferenceException for EVERY tool in a
  live Pro 3.7 (reproduced with CreateFolder; the tool never ran). Now: arguments via `QueuedTask.Run`, the
  tool started on the UI dispatcher, retried without `AddOutputsToMap`, every attempt in `steps`. Built but not
  yet run live (Pro was open; reinstalling needs it closed). `BridgeServer` errors now carry `where` (first
  stack frame).
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
| one uncached describe (`pro_query action=describe`) | ~430 ms |
| `import arcgis` | 17 s after arcpy is loaded; 33-60 s cold |
| `GIS("pro")` / a portal search | 1.0 s / 0.2-0.6 s |
| arcgis API index build | 13 s once per arcgis version (256 s before the walker stopped importing `arcgis.learn` and skipped `arcgis.map.dataclasses`); disk cache 0.1 s |
| full tool schema, sent every request | 72 tools = **~23.6k tokens** (chars/4; the old 156 tools were ~22.8k the same way) |

The dominant cost is **round trips**, not ArcPy: every extra tool call is a full
model inference. Four mechanisms address that:

1. **Warm-up** (`bootstrap.start_warmup`) — imports arcpy on THE arcpy thread at
   startup so the user's first call does not pay the 25 s (see `arcpy_thread.py`). Disable with
   `ARCGIS_SALAH_NO_WARMUP=1`.
2. **`pro_context`** — project + maps + layers + schemas + counts + sample rows
   in ONE call. Measured 5.4 s cold / 1.2 s warm for 11 datasets.
3. **`pro/cache.py`** — caches the Describe+ListFields+GetCount triple; measured
   0.354 s -> 0.001 s on a repeat. **Do not** stamp geodatabase datasets by
   container mtime: ArcGIS writes `.lock` files there on every *read*, so the
   cache then never hits (verified: 0 hits over two identical calls). Gdb entries
   use explicit invalidation plus a TTL (`ARCGIS_SALAH_CACHE_TTL`, default 300 s;
   0 disables). Plain files (shapefile, GeoTIFF) do use their own mtime.
4. **`pro_pipeline` / `pro_batch`** — chain N geoprocessing steps in one
   call (`{{prev}}`, `{{stepN}}` placeholders), or run them as a job on the arcpy thread
   and poll with `pro_server action=job` (`pro_batch` runs any tools of any layer in one call, with
   `parallel` and `background`).

**Toolset gating.** `ARCGIS_SALAH_TOOLSETS` prunes tool groups at startup: `core,egdb` = 22 tools ~6.9k,
`core,portal` = 36 tools ~11.3k, `core,geoai` = 19 tools ~5.5k (chars/4). Groups: `core` (always kept),
`geoai`, `unet`, `schema`, `validate`, `authoring`, `egdb`, `live`, `portal_base`, `portal_data`, `portal_maps`,
`portal_admin`, `portal_analysis`, `webapp`; the alias `portal` (`TOOLSET_ALIASES`) turns on every portal group.
`_toolset_of` returns the FIRST matching group, so the specific portal groups precede `portal_base` ("portal_").
`pro_server action=toolsets` reports what is active.

**Portal warm-up** (`bootstrap.start_portal_warmup`, only when a portal toolset is on): after the arcpy warm-up
Future resolves, it imports the base arcgis modules under `_imports._LOCK`, tries `connections.connect(source="pro")`
and loads the API index. `ARCGIS_SALAH_NO_PORTAL_WARMUP=1` disables it.

**Where notebook creation time actually goes** (measured, `-X importtime` + timers):
writing a 32-cell notebook is **~5 ms** (compile + save) — not the bottleneck. The costs
are: server start **~6 s warm / ~16 s cold**, of which the MCP SDK alone is ~14 s cold
(`mcp.client.session` ~4 s, pulled in by `mcp.server.fastmcp`; not our code); the live
bridge's project lookup **120-1300 ms** (so `pro_dl_notebook` only does a ~1 ms socket
probe while interviewing and defers the real lookup to the moment it writes); and the
tool schema (hence one DL tool, not four). The generated notebook's own first run is
dominated by `import arcgis.learn` (~2 min) — unavoidable.

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
- **Which path to use for "workflow -> tool with parameters":** the `.pyt` (`pro_create_python_toolbox`).
  It was verified end to end (Clip -> Intersect -> Export on synthetic data, output equal to an
  independent arcpy computation, in-memory intermediates cleaned up, registered in the open project via
  `add_toolbox`). `create_model` is guarded: `_unsupported()` refuses (code `unsupported_model`) a
  multi-input tool (`_MULTI_INPUT`: Intersect/Union/PairwiseIntersect/Merge/Append/...) whose input is a
  `{{ref}}`, and any step output / `extra_outputs` that is not a feature-class intermediate
  (`_VERIFIED_INTERMEDIATE`), because those generated models fail Pro's up-front validation.
  `allow_unverified=True` bypasses it. `.pyt` steps take `kwargs` (preferred) or `args`; `{{memory}}`
  makes an in-memory intermediate deleted in a `finally`; `{{stepN}}` is the earlier output PATH
  (`_first_output(result)`); list values are resolved element by element. Both generators take
  `register_in_project` (the MCP wrappers default it to True; the pure functions to False so tests never
  reach a bridge) and `pro/register.py` turns a bridge timeout on a `.pyt` into `pending` + the
  Trust-dialog hint instead of a bare error.
- No `tool.model.diagram.xml` is written — ModelBuilder auto-lays-out the canvas,
  which is far safer than hand-generating that format. Canvas **Groups** (the yellow boxes) live
  in that diagram file, not in `tool.model`, so the generator cannot make them.
- **Pro's own convention for a tool-output variable** (read from a Pro-authored model): `intermediate`,
  `derived:"true"`, `connection_type:"Parameter"`, a `datatype` and a design-time `value`. Steps may list
  `extra_outputs` (outputs a tool DERIVES, e.g. a trainer's `.dlpk`) and read them as `{{stepN.param}}`.
- **Unsolved (now refused by the guard, not silently generated) — an Export -> Train -> Detect (Image Analyst) chain does not validate.** Running the
  generated model with `arcpy` fails up front with `Parameters are not valid`. Measured on 1 real run
  each: wiring Train's `in_folder` to a model-PARAMETER variable crashes Esri's own validator
  (`self.params[0].values`: "Parameter has no attribute 'values'"; `in_folder` is multivalue); wiring it to
  an intermediate removes the crash but leaves `Input Training Data: Value is required`; a literal path
  validates the trainer; and the derived `.dlpk` only stops failing `Model Definition: Value is
  required` when it has an explicit design-time `value`. The remaining `Input Training Data` / `Output
  Folder` errors were not resolved. A Pro-authored model of the same chain is the missing reference.
  Esri docs (https://doc.esri.com/en/arcgis-pro/latest/arcpy/geoprocessing_and_python/): a validator can
  call `isInputValueDerived()` ("tool runs inside a model and its input comes from another tool's output").

## Enterprise geodatabase — verified facts
Tested on ArcGIS Pro 3.7 against SQL Server Express (throwaway geodatabases created by
`create_gdb`, then dropped) and PostgreSQL 18 (throwaway objects in an existing geodatabase, removed).
Harness scripts were run outside the repo; `tests/test_core.py` holds the backend-free versions.

- `Version.children` are Version OBJECTS and `Replica.datasets` are STRINGS (docs silent) - both were
  normalised; the originals broke JSON serialisation / raised AttributeError.
- `ReconcileVersions` compares names case-sensitively and silently drops mismatches ("target version has
  no edit versions to reconcile with") while returning success; `BLOCKING_VERSIONS` is a compress mode,
  not "these versions". Names are resolved via ListVersions and the LOG decides `reconciled`/`posted`.
- DEFAULT is `dbo.DEFAULT` in a DBO-schema SQL Server gdb. `DBO_SCHEMA` was used for test gdbs on purpose:
  `SDE_SCHEMA` would reuse the server-wide `sde` login that the developer's other gdbs depend on.
- `EnableEnterpriseGeodatabase` on SQL Server must switch on READ_COMMITTED_SNAPSHOT; this process's own
  cached connection was enough to block it even after `ClearWorkspaceCache`. With RCSI set beforehand by a
  DBA it succeeds; the error now returns that statement.
- POINT_IN_TIME: `GetCount` ignores the historical moment (current count); SearchCursor honours it. The
  date is UTC for non-versioned archives (gdb_from_date is stored UTC) and server-local time for
  traditional-versioned archives. Any date format worked (`historical_timestamp` parsed all four tried).
- `UpgradeGDB` raises ERROR 001046 on a current gdb; `upgrade_gdb` checks `currentRelease` first.
- `arcpy.ListFields` hides branch system fields; check INFORMATION_SCHEMA to prove branch registration.
- SQL Server cannot DROP LOGIN while it has sessions; pooled ODBC sessions are invisible to `ListUsers`
  (so `DisconnectUser` cannot reach them) and `ArcSDESQLExecute` cannot `KILL` (every statement runs in a
  transaction; tried 4 ways). `manage_principal` checks `sys.dm_exec_sessions` first and returns `kill_sql`.
- PostgreSQL: Create Database User grants privileges (PostGIS tables, the database) that block DROP ROLE;
  `DROP OWNED BY` is run only after proving the role owns no relations/functions outside its own schema.
- Esri: "Privileges on datasets in geodatabases must be granted or revoked using ArcGIS" - so
  `table_privileges` refuses versioned / archived / attachment datasets (several tables + views).
- The expired evaluation keycodes still passed "Validated authorization file" for creating/enabling.

## Portal - verified facts
Discovered with `inspect` on the installed arcgis 2.4.3 (arcgis-mapping 5.0.0, Python 3.13, pandas 3) and tested
live on the user's ArcGIS Online org (Publisher account, signed in through Pro) with throwaway `mcp_test_*`
items in the user's own content, kept private and deleted (checked gone). Admin operations (create users/roles,
org reports, Enterprise server admin) could NOT be run live - no admin rights - and are covered by mocked tests.

- `GIS("pro")` works from a thread other than the one that imported arcpy (1.0 s, search 0.2 s). It imports
  arcpy itself, so `connections._ensure_arcpy_on_its_thread` imports arcpy on THE arcpy thread first; otherwise
  that worker would become the importing thread and PostgreSQL calls on the arcpy thread would fail.
- **Import race:** two threads importing arcgis at once (the warm-up and a tool call) failed with "cannot import
  name 'FeatureCollection' from partially initialized module 'arcgis.features.feature'" and "partially
  initialized module 'arcgis' has no attribute 'auth'". Every arcgis import goes through `_imports.load` /
  `_imports.ensure_base` (one RLock); `connections.get` calls `ensure_base` first, and every portal op starts
  with `connections.get`. Do not add a bare `import arcgis...` in portal code.
- Deprecated (decorator writes `.. deprecated::` into the docstring; removed in 3.0): `ContentManager.add`
  (-> `Folder.add(ItemProperties, file=...)`, returns a Job: `.result()`), `Item.share`/`unshare` (->
  `item.sharing.sharing_level` = `SharingLevel.PRIVATE/ORG/EVERYONE`, `item.sharing.groups.add/remove`),
  `ContentManager.create_folder`/`rename_folder`/`delete_folder` (-> `content.folders.create/get().rename/delete`).
  The knowledge index flags 66 deprecated symbols.
- `create_view` has two bugs in 2.4.3, both hit live and both left a view item behind: `view_layers` is
  annotated `list[int]` but each entry needs `.properties` (pass layer objects), and `visible_fields`/`query`
  make it read `item.layers[0]` of the brand-new view before its layers exist ("list index out of range").
  `layers.schema(create_view)` creates the view plain, waits up to 15 s for its layer, applies
  `viewDefinitionQuery` + field visibility itself (object-id field kept visible), and on failure returns
  `possibly_created` ids.
- A `Group` hydrates lazily: `group.id` after `group.delete()` raises 400 - read ids before deleting.
- Non-admins get 403 on `gis.users.license_types` (and may on `roles.all()`): `access._lookups` falls back to
  `USER_TYPE_ALIASES` and returns a warning.
- `update_definition` replaces `drawingInfo` as a whole: renderer and labels are merged into the current one
  (`layers._merge_drawing`). `FeatureLayer.query` defaults to `return_all_records=True` - always page.
- Deleting a hosted feature layer while a view exists fails ("has a related Service item"): delete views first.
- Credits (Esri table): batch/stored geocoding 0.04 each; single unstored geocode, reverse, suggest free;
  `features.analysis.*(estimate=True)` returns credits (measured 0.012 for a 12-point buffer). `safety.credit_gate`.
- `knowledge.public_modules` walks packages with `pkgutil.iter_modules` WITHOUT importing (walk_packages imports
  every package to recurse and so pulled in `arcgis.learn`: 256 s); `HEAVY` skips `arcgis.learn` (138 s) and
  `arcgis.map.dataclasses` (pydantic web-map models, 37 s). Build 13 s, cached as
  `%LOCALAPPDATA%\arcgis_pro_salah_mcp\arcgis_api_index_<version>.json`; `_BUILD_LOCK` stops a second build.
- `portal_python` runs in-process on a worker thread (a subprocess would pay the 17-60 s arcgis import and a new
  sign-in each call); a timed-out call keeps running. Read mode is an AST seatbelt (mutating calls, attribute
  assignments like `sharing_level`/`privileges`, `os.remove`, `open(...,'w')`, `eval`...), not a sandbox.
- `portal_rest` sends the session token only to the portal host, its helper-service hosts, `*.arcgis.com` on
  ArcGIS Online, and `ARCGIS_SALAH_REST_HOSTS`.
- Confirm rail: irreversible / bulk / public / admin / credit-spending writes return `confirm_required` with a
  `preview`; low-risk additive writes (upload, add field, folder, private group, view, web map) run directly.
  Every write calls `safety.audit` (JSON lines, scrubbed of registered secrets and token/password patterns).

## Conventions (follow when adding tools)
1. **Result envelope.** Return `ok(data)` / `err(msg)` from `_result`; decorate
   the op with `@guard`.
2. **Lazy backend imports.** Never import `arcpy`/`arcgis` at module top level —
   import inside `_arcpy()` / `_arcgis()` so the package imports (and
   `test_core.py` runs) on machines without ArcGIS.
3. **Tool naming.** Prefix by layer: `pro_`, `live_`, `portal_`, `webapp_`.
4. **Adding a tool:** write the `@guard` op in the relevant module, then either a new action on an existing
   `@mcp.tool()` (preferred: `_dispatch` + `_need`) or a thin new wrapper in `server.py` with a precise
   docstring. A `pro_*` tool that needs no arcpy goes in `_NOT_ARCPY`. Re-run `scripts/build_llms.py`.
5. **Portal writes:** `safety.confirm_required` for irreversible/bulk/public/admin/credit operations,
   `safety.audit` for every write, secrets only via `safety.secret_from_env`, re-read after writing,
   `serialize.*` for anything returned, arcgis imports via `_imports`.

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
| `ARCGIS_EGDB_AUTHORIZATION_FILE` | — | `pro/egdb.py`: the user's OWN keycodes file (never committed) |
| `ARCGIS_SALAH_AUDIT_LOG` | `%LOCALAPPDATA%\arcgis_pro_salah_mcp\audit.jsonl` | `portal/safety.py` (`off` disables) |
| `ARCGIS_SALAH_MAX_EDITS` / `_MAX_USERS` / `_CREDIT_LIMIT` | 5000 / 200 / 100 | `portal/safety.py` limits |
| `ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC` | `true` | `portal/pyexec.py` write mode |
| `ARCGIS_SALAH_REST_HOSTS` | — | `portal/rest.py` extra allowed hosts |
| `ARCGIS_SALAH_NO_PORTAL_WARMUP` | `0` | `bootstrap.py`: skip the arcgis import + Pro sign-in warm-up |
| `ARCGIS_SALAH_CACHE_DIR` | `%LOCALAPPDATA%\arcgis_pro_salah_mcp` | `portal/knowledge.py` index cache |

The last two are *not* interchangeable despite naming the same thing — set both
if you keep Pro somewhere non-standard.

## Tests
`tests/test_core.py` must stay backend-free and green with **no ArcGIS installed**
(139 tests; they also pass with `arcgis`/`arcpy` blocked from import). It covers the envelope/guard behavior, graceful ArcPy- and
bridge-absent degradation, the `live/policy.py` rails, `pro/metadata.py` theme
inference, both webapp generators, the cache (including the gdb-lock-file case
and the TTL), pipeline placeholder substitution, the `.pyt` and `.atbx`
generators, toolset gating, the argument validation that deliberately runs
*before* any backend import, `pro_batch`, and the portal layer against fake GIS objects (every onboarding
rule, safety rails, the read-mode guard, REST allowlist, layer edits/renderers, knowledge index,
resources and prompts). Live checks (real stdio, real org) live outside the repo. Anything needing a real ArcGIS install belongs in a
separate `test_full_e2e.py` (planned), not here.

**Validate before importing the backend.** Ops such as `schema.add_attribute_rule`
and `unet.trace` check their arguments first and only then call `_arcpy()`. A bad
call then fails in microseconds instead of after a 25 s import — and the
validation stays unit-testable in this file.

## Where the open work is
- `pro_symbology kind=categorized|graduated` build
  `lyr.symbology` renderers (UniqueValue / GraduatedColors) — implemented.
- Web app + dashboard generators (Maps SDK 5.0), mixed Feature/Tile/Vector-Tile
  publishing, and GitHub deploy are implemented (ribbon **v0.1.2**).
- `ROADMAP.md` has the rest: Enterprise auth, renderer-JSON builders, Vite output,
  client-side symbology overrides, live-bridge round-trip verification against a
  running Pro, and server-side (add-in) enforcement of the policy rails as
  defense-in-depth.
