# Roadmap

Legend: ✅ done · 🟡 partial / stub · ⬜ planned

## Phase 1 — Headless ArcPy MCP (Layer 1)  ✅ core
- ✅ Project / layer management (`project_info`, `list_layers`, `add_layer`, …)
- ✅ Features & attributes (`get_features`, `select_by_expression`, `add_field`, `calculate_field`, `field_statistics`)
- ✅ Spatial analysis (`buffer`, `clip`, `spatial_join`, `dissolve`, `merge`, `reproject`, `repair_geometry`, `extract`)
- ✅ Export (`export_layer`, `export_image`, `export_pdf`, `export_map_series`)
- ✅ Generic `run_gp` + `execute_code` escape hatch
- ✅ Self-healing `arcgispro-py3` discovery (`bootstrap.py`)
- ✅ **Symbology authoring** (`apply_categorized_symbology`, `apply_graduated_symbology`)
  — `lyr.symbology` UniqueValue / GraduatedColors renderers, color-ramp lookup,
  numeric-field guard. Pending live verification against ArcGIS Pro.
- ⬜ `e2e` tests against a real ArcGIS Pro install.

## Phase 2 — Portal / ArcGIS Online (Layer 2)  ✅ core
- ✅ `connect` (AGOL default, profile auth), `whoami`
- ✅ `publish_layer` → hosted feature layer, returns item id
- ✅ Ribbon **Publish** button: mixed **Feature / Tile / Vector Tile** publishing
  with data-derived metadata + optional Web Map (vector tiles via
  `arcpy.sharing.Publish`, others via Stage/Upload service definition).
- ✅ `search_items`, `get_item`
- ✅ `create_webmap` from hosted layers
- ✅ `set_layer_symbology`, `set_layer_labeling` (renderer/label JSON)
- ✅ `share_item`
- ⬜ **ArcGIS Enterprise Portal** support (URL is already configurable; needs
  auth testing + token handling).
- ⬜ Helper builders that turn a field + ramp into renderer JSON (so the agent
  doesn't have to hand-write Esri renderer dicts).

## Phase 3 — Web app generator (Layer 3)  ✅ core
- ✅ Static app from a `webmap_id` (keeps server-side symbology/labeling)
- ✅ Static app from a list of `layer_item_ids` over a basemap
- ✅ Widgets: legend, layerList, search, basemapGallery, home
- ✅ **ArcGIS Maps SDK for JavaScript 5.0** — single CDN module bundle with the
  Calcite Design System + `<arcgis-*>` map components; core classes via
  `$arcgis.import()` (no AMD `require`).
- ✅ **Interactive dashboard generator** (`webapp_create_dashboard` + the
  **Create Dashboard** ribbon button): Calcite shell with live indicator cards
  (count + sums), a category breakdown, and a "features in view" list — all
  recomputed for the map's current extent on zoom/pan — plus a Light/Dark toggle.
  Indicators are data-driven (fields introspected at runtime).
- ✅ **GitHub deploy pipeline** (`webapp_github_pipeline` + the **Deploy Web App**
  ribbon button): create repo, push files via the contents API, optional Pages.
- ⬜ Client-side renderer/labeling overrides (apply symbology in JS too)
- ⬜ Optional **Vite** project output (npm build) in addition to plain static.
- ⬜ Optional **ASP.NET MVC host** output (matches the author's MVC skill;
  adds OAuth/token-proxy for Enterprise) — see note below.

## Phase 4 — Live .NET bridge (like the QGIS "live" mode)
- ✅ ArcGIS Pro SDK for .NET add-in running inside the open session
  (`SalahAIBridge/`): `BridgeServer` (loopback HttpListener, no token, JSON
  envelope, routing) + autoLoad `Module1` + a **7-button Salah MCP** ribbon
  (Start Server, Ping, Publish, Create Web App, Create Dashboard, Deploy Web App;
  add-in **v0.1.2**, Calcite/Octicons icons, hideable progress windows). Targets
  ArcGIS Pro 3.x / .NET 10.
- ✅ Loopback HTTP exposed as `live_*` MCP tools (`live_ping`, `live_list_layers`,
  `live_zoom_to`, `live_query`, `live_run_gp`, `live_add_layer`,
  `live_export_layout`, `live_get_request`) — Python client + tools done; all
  handlers implemented, each on the MCT via `QueuedTask` (`run_gp` awaits
  `ExecuteToolAsync`). `get_request` lets the agent pick up Create Web App /
  Publish requests the user queues from the ribbon.
- ✅ Client-side hardening: **read-only mode** (`ARCGIS_BRIDGE_READONLY`) blocks
  mutating commands, and **destructive ops require `confirm=True`** (`run_gp`).
  Enforced in `live/policy.py` before any request reaches the bridge; covered by
  backend-free tests.
- ⬜ Manual round-trip verification of each command against a running ArcGIS Pro
  ("watch it work").
- ⬜ Server-side defense-in-depth: enforce read-only / confirm inside the add-in
  too (not just the Python client).

## Phase 5 — Polish & publish
- ⬜ Demo video, registry listing (CLI-Anything / Glama MCP)
- ⬜ Security: read-only mode + confirm-before-destructive are done client-side;
  server-side enforcement inside the add-in is the remaining piece. (The bridge is
  loopback-only with no token by design — local use.)
- ⬜ Choose final license.

---

### Note on the ASP.NET MVC host (future)
v0.1 generates a **static** JS app, targeting **ArcGIS Online**. When Enterprise
support lands, an MVC host becomes valuable as the place for OAuth + a token
proxy so browser clients never see credentials. The static generator and an MVC
generator can coexist as two `webapp_create` output modes.
