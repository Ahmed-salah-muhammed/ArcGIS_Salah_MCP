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

156 tools in total, grouped into toolsets (see **Performance** below).

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
  `return_box_only` untried). `pro_geoai_detect_by_text` still exists, but do not
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
  Verified over real stdio: `pro_server_status` answered in 0.0 s during a 15 s call. Module-level tool
  functions are untouched; `_ORIGINAL_FN` keeps them for `pro_run_background` (any `pro_*` tool as a job,
  polled with `pro_job_status`). The first arcpy call after start waits for the import (~47 s measured with a
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
  `pro_egdb_create_connection` calls it after writing the file (MCP wrapper default). Compiles; it has not
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
| one uncached `pro_describe_layer` | ~430 ms |
| full tool schema, sent every request | 156 tools = **27.0k tokens** |

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
4. **`pro_pipeline` / `pro_job_submit`** — chain N geoprocessing steps in one
   call (`{{prev}}`, `{{stepN}}` placeholders), or run them as a job on the arcpy thread
   and poll with `pro_job_status` (`pro_run_background` does the same for any `pro_*` tool).

**Toolset gating.** `ARCGIS_SALAH_TOOLSETS` prunes tool groups at startup:
`core,portal,webapp` takes 156 tools -> 57 and 27.0k -> 8.2k tokens (`core,egdb`: 80, ~13.0k). Groups:
`core` (always kept), `geoai`, `unet`, `schema`, `validate`, `authoring`,
`egdb`, `live`, `portal`, `webapp`. `pro_toolsets` reports what is active.

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
| `ARCGIS_EGDB_AUTHORIZATION_FILE` | — | `pro/egdb.py`: the user's OWN keycodes file (never committed) |

The last two are *not* interchangeable despite naming the same thing — set both
if you keep Pro somewhere non-standard.

## Tests
`tests/test_core.py` must stay backend-free and green with **no ArcGIS installed**
(117 tests). It covers the envelope/guard behavior, graceful ArcPy- and
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
