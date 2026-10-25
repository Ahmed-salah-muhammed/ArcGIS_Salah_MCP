# ArcGIS Pro Salah MCP

**Drive the whole ArcGIS stack with natural language through any MCP agent
(Claude **or** Google Antigravity) — or with one click from an ArcGIS Pro ribbon.**

A four-layer [Model Context Protocol](https://modelcontextprotocol.io) server.
You describe what you want, and your agent calls the right ArcGIS operations — from
desktop geoprocessing, to publishing on ArcGIS Online, to a finished web app or
an interactive dashboard. Because it speaks the standard MCP stdio transport it is
**client-agnostic** — use it from Claude, Google Antigravity, or any other MCP
client. The same building blocks are also wired to a **Salah MCP ribbon** inside
ArcGIS Pro, so you can publish and build apps with no agent at all.

---

## About us

**ArcGIS Pro Salah MCP** is an independent integration that connects Esri's
ArcGIS platform to AI agents through the open Model Context Protocol. It is the
ArcGIS Pro sibling of [`qgis_salah_mcp`](https://github.com/Ahmed-salah-muhammed/qgis_salah_mcp):
same goal — let an assistant operate a full GIS end to end — but for Esri's
commercial stack instead of QGIS.

The vision is **one brain, four hands**: a single server that can analyze data in
ArcGIS Pro, publish it to ArcGIS Online, and turn it into a shareable web app or
dashboard — in one conversation, or one ribbon click.

- **Author:** Ahmed Salah Muhammed — [github.com/Ahmed-salah-muhammed](https://github.com/Ahmed-salah-muhammed)
- **License:** MIT
- **Not affiliated with Esri.** ArcGIS, ArcGIS Pro and ArcPy are trademarks of Esri.

---

## The four layers

| Layer | Prefix | Backend | What it does |
| --- | --- | --- | --- |
| **1 · Pro** | `pro_*` | ArcPy | Geoprocessing, one-call project context, geodatabase schema (domains/subtypes/attribute rules), topology validation, **GeoAI**, **Utility Network**, `.pyt` + ModelBuilder authoring |
| **1b · Live** | `live_*` | .NET add-in (loopback HTTP) | Drive the **open** ArcGIS Pro session (list/zoom/query/run GP/export) |
| **2 · Portal** | `portal_*` | ArcGIS API for Python | Everything in your **ArcGIS Online / Enterprise** org, signed in through ArcGIS Pro: items, publishing, sharing, hosted-layer data and schema, views, web maps, apps, users and **CSV onboarding**, groups, roles, geocoding, analysis, admin — plus the installed-library knowledge tool and Python/REST escape hatches |
| **3 · WebApp** | `webapp_*` | ArcGIS Maps SDK for JS **5.0** | Generate a static **web app** *or* an interactive **dashboard**, then deploy to GitHub |

**The signature workflow:** `Pro (analyze)` → `Portal (publish)` → `WebApp / Dashboard (visualize)` → `GitHub (deploy)`.

---

## Two ways to use it

1. **From an AI agent** — chat in natural language and the agent chains the
   `pro_*` / `portal_*` / `webapp_*` tools. Works with **any MCP client** —
   [Claude](https://claude.ai/download) **and [Google Antigravity](https://antigravity.google)**
   are both supported (the server speaks the standard MCP stdio transport, so it
   is client-agnostic). Best for analysis + automation.
2. **From the ArcGIS Pro ribbon** — the **Salah MCP** tab gives you one-click
   **Publish**, **Create Web App**, **Create Dashboard** and **Deploy Web App**
   buttons. No agent required; the buttons shell out to the same Python.

> **Status (v0.1):** Pro geoprocessing, project/layer & symbology tools, portal
> publishing (feature / **tile** / **vector tile**), web-map and **web-app +
> dashboard** generation (ArcGIS Maps SDK for JS **5.0** with Calcite + map
> components), and a one-click **GitHub deploy** are all implemented, plus a live
> `.NET` bridge (loopback, no token) with a **7-button Salah MCP ribbon**
> (add-in **v0.1.2**). Default portal is **ArcGIS Online**; the portal URL is
> configurable, so **Enterprise Portal** is a small next step.

---

## Why ArcGIS Pro needs a different design than QGIS

QGIS is open-source, so `qgis_salah_mcp` runs a Python socket server *inside* the
live QGIS session. ArcGIS Pro does **not** allow that: ArcPy works on
`.aprx`/`.gdb` files on disk and **cannot attach to a running Pro session** (an
Esri limitation). Driving the *live* session requires a **.NET add-in** built
with the ArcGIS Pro SDK. So this project pairs the **headless ArcPy** path
(`pro_*`) with a **live `.NET` bridge** (`live_*`): a loopback HTTP add-in that
runs inside the open Pro session. See
[`SalahAIBridge/`](SalahAIBridge/) and [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

---

## Architecture

```
You (natural language)                ArcGIS Pro ribbon (Salah MCP tab)
        │                                        │
        ▼                                        ▼
   Claude Desktop                        one-click buttons shell out
        │  stdio                                 │ to arcgispro-py3
        ▼                                        ▼
   MCP Server (FastMCP)                 ← src/arcgis_pro_salah_mcp/server.py
        ├── pro_*     → ArcPy            (arcgispro-py3 interpreter, files on disk)
        ├── live_*    → HTTP (loopback)  → .NET add-in inside the OPEN Pro session
        ├── portal_*  → arcgis (Py API)  → ArcGIS Online / Portal
        └── webapp_*  → static generator → Maps SDK for JS 5.0 app / dashboard
                                              └─ optional → GitHub repo + Pages
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

---

## Getting started (tutorial)

### Prerequisites

- **ArcGIS Pro 3.x** (provides ArcPy via its bundled `arcgispro-py3` Python).
- For publishing: be **signed in** to ArcGIS Online / your portal in ArcGIS Pro.
- For the agent workflow: an **MCP client** — **Claude Desktop** *or* **Google
  Antigravity** (or any other MCP-capable client).
- For the ribbon buttons: build & install the **SalahAIBridge** add-in (below).

### Step 1 — Install the package into ArcGIS Pro's Python

The `pro_*`, `webapp_*` and the ribbon buttons run inside ArcGIS Pro's bundled
interpreter, so install the package there:

```bat
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" -m pip install -e .
```

> The web-app and dashboard generators only need the package importable (no heavy
> deps). If pip can't write to `Program Files` without admin, it falls back to your
> **user site**, which `arcgispro-py3` reads automatically — that's fine. If you
> only want the generators (and not the full MCP server deps), use
> `… -m pip install -e . --no-deps`.

For the Portal layer, add the optional `arcgis` dependency:

```bat
"...\arcgispro-py3\python.exe" -m pip install -e ".[portal]"
```

> **Self-healing:** if the MCP server starts under the wrong Python, it locates
> `arcgispro-py3` (env override → common paths → registry) and re-executes itself
> there. See `bootstrap.py`.

### Step 2 — Install the Salah MCP ribbon

Build the add-in and install it so the **Salah MCP** tab appears in ArcGIS Pro:

1. Open `SalahAIBridge/SalahAIBridge.slnx` in **Visual Studio 2026** (with the
   ArcGIS Pro SDK), or build from a shell:
   ```bat
   dotnet build SalahAIBridge\SalahAIBridge.csproj
   ```
2. **Close ArcGIS Pro**, then double-click the built
   `SalahAIBridge\bin\Debug\net10.0-windows\SalahAIBridge.esriAddinX`.
3. Reopen ArcGIS Pro → the **Salah MCP** tab is on the ribbon.

> Verify the loaded build in **Project ▸ Add-In Manager** — the version should
> read **0.1.2**. Bump `Config.daml`'s `version` whenever you change the add-in.

### Step 3a — Use it from the ArcGIS Pro ribbon (no agent)

With a project open, layers in the map, and you signed in:

1. **Publish** → pick feature layers from the checklist, choose a type per layer
   (**Feature / Tile / Vector Tile**), optionally "Create a Web Map", and publish
   straight to your portal into a new folder (with data-derived metadata).
2. **Create Web App** → generate a static Maps SDK for JS app from a Web Map id
   (or layer ids) and preview it on `http://localhost`.
3. **Create Dashboard** → generate an interactive dashboard (map + live
   indicators + breakdown + a "features in view" list, with a Light/Dark toggle)
   and preview it.
4. **Deploy Web App** → push the previewed folder to a new GitHub repo and
   (optionally) enable GitHub Pages for a live URL.

### Step 3b — Use it from an MCP client (Claude **or** Antigravity)

The server speaks the **standard MCP stdio transport**, so the registration is the
same everywhere: point an `mcpServers` entry at the installed entry point. Pick
your client below — the JSON is identical, only the file it goes in differs.

**Claude Desktop** — edit `claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
"mcpServers": {
    "arcgis-pro-salah": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_salah_mcp.server"]
    }
  }
```

**Google Antigravity** — open the Agent panel → **MCP servers** → **Edit raw
config** (`mcp_config.json`) and add the **same** block:

```json
{
  "mcpServers": {
    "arcgis_pro_salah": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": [
        "-m",
        "arcgis_pro_salah_mcp.server"
      ],
      "env": {
        "PYTHONPATH": "your\\path\\MCP\\ArcGIS Pro Salah MCP\\src" // enter your path
      }
    }
  }
}
```

Save, then reload the MCP servers in Antigravity — the `pro_*`, `live_*`,
`portal_*` and `webapp_*` tools appear in the agent's tool list, exactly as in
Claude. (Any [environment variable](#configuration-environment-variables) can go in
the `env` block; it's shown here on Antigravity but works for either client.)

> Always point `command` at the installed **entry point**
> (`arcgis-pro-salah-mcp.exe`), not at `server.py` — package-relative imports only
> resolve when the package is installed. The path is the same regardless of client.

Then build a sample geodatabase and try the end-to-end story (works the same from
Claude or Antigravity):

```bat
"...\arcgispro-py3\python.exe" demos\setup_sample.py
```

> **You:** "Buffer `sample.gdb/cities` by 50 km, publish the result to my ArcGIS
> Online as 'City Service Areas', build a web map, then generate a dashboard."

The agent (Claude or Antigravity) chains:

1. `pro_analysis(operation="buffer", dataset="sample.gdb/cities", out="sample.gdb/cities_buf", distance="50 Kilometers")`
2. one `pro_batch`: `portal_publish(path="…/sample.gdb/cities_buf", title="City Service Areas")` →
   `portal_map(action="create", title="City Service Areas", layers=["{{call1.data.published_item.id}}"])`
   (signed in through ArcGIS Pro automatically — no `portal_connect` needed)
3. `webapp_create("City Service Areas", kind="dashboard", webmap_id=…)` → static dashboard

Serve any generated folder locally:

```bat
cd webapp-build && python -m http.server 5500
```

---

## Two ways to build an app: static vs. custom

Once your data is in the portal, there are **two routes** to a finished
application — pick by how much control you want. Both reference your published
data by its **Web Map / layer item‑id**, so the app inherits the symbology,
labeling and popups you published.

### 1) Static apps (generated) — the quick path

The `webapp_create` tool (`kind=app` or `kind=dashboard`; and the **Create Web App**
/ **Create Dashboard** ribbon buttons) emit an opinionated, no‑build static site
(ArcGIS Maps SDK for JS 5.0 + Calcite + map components). Give them a **Web Map
item‑id** (or layer item‑ids) and they wire up the map, widgets, indicators and
interactions for you. Fast, consistent, zero hand‑coding.

### 2) Custom apps (built with Claude) — your design & workflow

When you want **your own design, layout, branding, workflow and concept**, skip
the generator and just **describe the app to Claude**. Claude hand‑writes a
bespoke ArcGIS Maps SDK for JS 5.0 application around your published **item‑id** —
custom Calcite panels, charts, tools and interaction logic, exactly to your spec.

**Typical flow:**

1. **Publish** your data / web map (ribbon **Publish**, or `portal_publish`
   + `portal_map action=create`) and copy the **item‑id** from your portal.
2. In Claude, describe the app and hand it the item‑id, e.g.:

   > **You:** "Build a single‑page flood‑response app. Left: a Calcite panel with
   > a date slider and a 'shelters only' filter. Center: the map from Web Map
   > item‑id `a1b2c3…`. Right: a live count of affected buildings and a bar chart
   > by district that updates with the map extent. Dark theme, our logo in the
   > header. Use ArcGIS Maps SDK for JS 5.0."

3. Claude generates the full app (and can drop the files into a folder for you).
4. Preview it locally (`python -m http.server`), then deploy with **Deploy Web
   App** / `webapp_github_pipeline`.

> The static generators are the quick path; the custom route is the creative one —
> same published data (referenced by **item‑id**), unlimited UI/UX. You can also
> start from a generated app and ask Claude to restyle or extend it.

---

## The Salah MCP ribbon

Once the **SalahAIBridge** add-in is installed, a **Salah MCP** tab appears on
the ArcGIS Pro ribbon. It groups the seven buttons into three task areas — **Live
Bridge**, **ArcGIS Online**, and **Web App** — so the whole *analyze → publish →
visualize → deploy* workflow is one click away, no agent required. Every button
is always enabled: this is a local developer tool, so there is no sign-in,
API key or licensing gate in front of the ribbon.

![The Salah MCP ribbon tab in ArcGIS Pro](docs/images/ribbon.png)

| Button | Icon | Group | What it does |
| --- | --- | --- | --- |
| **Start / Stop Server** | green play | Live Bridge | Start/stop the loopback bridge so `live_*` tools can reach the open session |
| **Ping** | cyan gauge | Live Bridge | `GET /health` on the bridge and show the response |
| **Zoom to Layer** | amber magnifier | Live Bridge | Read-only: zoom the active view to the first visible feature layer's full extent |
| **Publish** | blue upload | ArcGIS Online | Publish chosen layers (Feature/Tile/Vector Tile) to your portal + optional Web Map |
| **Create Web App** | orange browser | Web App | Generate a static Maps SDK for JS app and preview it on localhost |
| **Create Dashboard** | indigo bar-chart | Web App | Generate an interactive dashboard and preview it on localhost |
| **Deploy Web App** | GitHub mark | Web App | Push the generated folder to GitHub and (optionally) enable Pages |

Icons are generated from the official [Calcite Design System](https://developers.arcgis.com/calcite-design-system/icons/)
set (plus GitHub's Octicons for the Deploy mark) — see `SalahAIBridge/Images/make_icons.py`.
(The **Zoom to Layer** icon ships as-is and is not produced by that script.)

The **Publish** and **Deploy** progress windows are minimizable / hideable (a
**Hide** button + taskbar), so long uploads can run in the background while you
keep working in Pro.

### Live Bridge — Start Server, Ping & Zoom to Layer

The **Start Server** button toggles the loopback HTTP bridge (port `2026`, no
token) that runs *inside* the open Pro session. Once it's up, the `live_*` tools
— and Claude / Antigravity over MCP — can list layers, zoom, query and run
geoprocessing against the map you're actually looking at.

| Start the bridge | Confirm it's alive (Ping) |
| --- | --- |
| ![Start Server confirmation: MCP Live Bridge started on http://127.0.0.1:2026](docs/images/start-server.png) | ![Ping result: GET /health → HTTP 200 OK, {"ok":true,"data":"alive"}](docs/images/ping.png) |

- **Start Server** flips to **Stop Server** while running and shows the bridge URL
  (`http://127.0.0.1:2026`). From here you "talk to Claude / Antigravity over MCP."
- **Ping** issues `GET /health` and prints the raw response — `HTTP 200 OK` with
  `{"ok":true,"data":"alive"}` means the bridge is reachable and the `live_*`
  layer is ready.
- **Zoom to Layer** is a quick, read-only "orient yourself" action: it zooms the
  active view to the full extent of the first visible feature layer in the map.
  It runs entirely in-process — no bridge, no MCP round-trip needed.

### ArcGIS Online — Publish

**Publish** uploads the layers you pick straight to the portal you're signed into
inside ArcGIS Pro — into a brand-new folder, with metadata derived from the data
itself.

![Publish layers dialog: folder name, layer checklist with per-layer type, layer-type radios, and Create Web Map option](docs/images/publish.png)

- **New folder** — names the portal folder the items land in (e.g. `Salah MCP`).
- **Layer checklist** — tick the layers to publish (or **Select all**); set a
  **per-layer type** with the dropdown, or flip every layer at once with the
  **Layer Type** radios — **Feature**, **Tile**, or **Vector Tile**.
- **Create a Web Map of these layers** — optionally assembles a Web Map in the
  same folder, ready to feed straight into **Create Web App** / **Create Dashboard**.
- **Start Publishing** runs the upload; the progress window can be hidden so it
  keeps going in the background.

### Web App — Create Web App

**Create Web App** generates an opinionated, no-build static site (ArcGIS Maps SDK
for JS 5.0 + Calcite) from a Web Map item-id (or hosted layer ids) and previews it
locally before you deploy.

![Create Web App dialog: app title, Web Map item ID, basemap, widget checkboxes, output folder and preview port](docs/images/create-web-app.png)

- **App title** — the header shown in the generated app.
- **Web Map item ID** (from Publish, or any Web Map) — the data source; or paste
  one or more **hosted layer item IDs** instead.
- **Basemap** — the base layer (e.g. `topo-vector`).
- **Widgets** — toggle **legend**, **layerList**, **search**, **basemapGallery**,
  and **home**.
- **Output folder** + **Preview port** — where the site is written and the
  `localhost` port it's served on. **Create & Preview** builds it and opens it in
  your browser.

### Web App — Create Dashboard

**Create Dashboard** builds an interactive dashboard — a map plus live indicators,
a category breakdown, and a "features in view" list — whose statistics are driven
by a feature layer. Fields are introspected in the browser, so it auto-picks
sensible defaults when you leave the optional fields blank.

![Create Dashboard dialog: title, Web Map item ID, basemap, optional category and value fields, map widgets, output and port](docs/images/create-dashboard.png)

- **Dashboard title** — header for the dashboard.
- **Web Map item ID** — its **first feature layer drives the stats** (or paste
  hosted layer ids; the first is primary).
- **Category field** *(optional)* — what the breakdown groups by; **auto-picked**
  if left blank.
- **Value fields for the indicators** *(optional)* — comma-separated fields to
  surface as live indicators.
- **Map widgets**, **Output folder**, **Preview port** — same controls as Create
  Web App. **Create & Preview** generates and opens the dashboard.

### Web App — Deploy Web App

**Deploy Web App** pushes a folder you've already generated to a new GitHub repo,
and can flip on GitHub Pages for a live public URL.

![Deploy Web App dialog: repository name, GitHub token, web app folder, and deployment-mode radios](docs/images/deploy-web-app.png)

- **Repository / app name** — the new repo to create (e.g. `my-arcgis-app`).
- **GitHub Personal Access Token** — needs rights to **create repos** and **write
  files**; the dialog links to *how to generate one*. See
  [Deploy to GitHub](#deploy-to-github) for the exact scopes.
- **Web app folder** — the generated folder to upload (defaults to your last
  Create Web App / Dashboard output).
- **Deployment mode** — **Just upload code** (create repo + push files) or
  **Upload code & deploy live website** (also enables GitHub Pages). **Deploy**
  runs it; the progress window is hideable.

---

## Authentication (Portal layer)

**Nothing to set up if you are signed in to ArcGIS Pro.** Every `portal_*` tool signs in by itself, in this
order:

1. **ArcGIS Pro's sign-in** (`GIS("pro")`) — whatever portal Pro uses, ArcGIS Online or Enterprise, SSO/SAML
   included. Measured: 1.0 s. At server start it happens in the background, so the first portal call is instant.
2. A stored **profile** (`ARCGIS_PROFILE`, or `portal_connect(source="profile", profile=...)`) — credentials
   live in the OS keyring. Create one once from any Python with `arcgis`:
   `GIS("https://www.arcgis.com", username="you", password="…", profile="agol")`.
3. `url` + `username` + `password_env` — the **name** of an environment variable holding the password. A
   password is never a tool argument.
4. `api_key_env`, then anonymous (public content only).

`portal_connect(action="whoami")` shows who you are, your role and user type, and what the account can do
(`can.publish_features`, `can.share_public`, `can.manage_users`, …). Named connections
(`portal_connect(name="target", ...)`, then `connection="target"` on any tool) let content move between orgs.
The ribbon's **Publish** button uses the same Pro sign-in.

---

## Deploy to GitHub

The **Deploy Web App** button (and the `webapp_github_pipeline` tool) create a
public repo, push the static files via the contents API, and optionally enable
GitHub Pages. Your token must be able to **create repos** and **write files**:

- **Classic token (simplest):** the **`repo`** scope covers everything.
- **Fine-grained token:** Repository access **All repositories**, with
  **Administration + Contents + Pages = Read and write**. A token scoped to "only
  select repositories" cannot create new repos.

Not sure what your token can do? Run the bundled checker (it never reveals your
token):

```bat
python demos\check_github_token.py --try-create
```

You want to see **✓ CREATE OK** and **✓ PUSH OK**.

---

## Tools

**72 tools in 14 groups, plus 23 resources + 1 resource template and 11 prompts.** Related operations share one tool
with an `action` argument — `pro_layer action=add|remove|rename|visibility|opacity`, `portal_item
action=get|update|move|delete|export|…` — so the model chooses between fewer, clearer tools. A wrong action
returns `code: "bad_action"` with the valid list; a missing argument is reported before ArcPy or arcgis is
touched. `ARCGIS_SALAH_TOOLSETS` keeps only the groups you need — see [Performance](#performance).

### What's new — the ArcGIS Online / Enterprise layer, merged tools, batches, playbooks

| What | Status | What you can now ask for |
| --- | --- | --- |
| **Portal layer** (22 tools, ArcGIS API for Python 2.4) | **New** | Anything in your org: search and manage items and folders, publish files / `.gdb` / feature classes / CSVs, share, clone between orgs, query and edit hosted layers, add fields, create filtered views, renderers and labels, web maps and scenes, Survey123 export, dashboard clone, StoryMaps, users / groups / roles, **CSV onboarding**, geocoding and analysis with credit estimates, org audit. |
| **Sign-in through ArcGIS Pro** | **New** | Nothing to configure: if Pro is signed in (Online or Enterprise), the portal tools use it. |
| **`portal_api` + `portal_python` + `portal_rest`** | **New** | *"Do X"* where no tool fits: the agent searches the **installed** arcgis library (signatures, docs, 66 deprecated symbols flagged with their replacements), inspects in read mode, then writes with your confirmation. |
| **`pro_batch`** | **New** | Several tools of any layer in **one** round trip, results passed along (`{{call1.data.published_item.id}}`), in parallel or in the background. |
| **Merged tools** | **Changed** | 156 → 72 tools. Non-portal schema 21.7k → 15.3k tokens (chars/4) with the same features. Old → new names are in the table below. |
| **Playbooks, resources & prompts** | **New** | Playbooks via `pro_server action=guide` or `arcgis://guide/<topic>`; attach an item as context with `arcgis://portal/item/{item_id}`; prompts such as *plan_big_task*, *onboard_users_from_csv*, *org_audit*, *migrate_items_between_orgs*. |
| **Safety rails** | **New** | `confirm_required` previews for anything irreversible, bulk, public, admin or credit-spending; an audit log of every portal write; secrets only as env-var names, masked everywhere. |

**Playbooks, resources and prompts.** The 20 playbooks are reachable two ways: by the model itself with
`pro_server(action="guide", topic="portal-users")` (no topic lists them — works in every client), and as
MCP resources `arcgis://guide/<topic>` you can attach. The other resources are context worth attaching
without a tool call: `arcgis://live/project` (the project open in Pro), `arcgis://portal/my-content` (your 50
most recent items) and the template **`arcgis://portal/item/{item_id}`** (one item: card, sharing, every
layer's fields, or a web map's layers). In Claude Code: `@arcgis-pro-salah:arcgis://portal/item/<id>`; Claude
Code also gives the model tools to read resources. In Claude Desktop a resource reaches the chat when you
attach it. Server status, toolsets and the arcgis module map are tools (`pro_server`, `portal_api`), not
resources. Prompts (`/arcgis-pro-salah:<prompt>` in Claude Code) are saved workflows you pick; everything
they do can also be asked for in plain words.

<details>
<summary><b>Old tool names → new</b> (click)</summary>

| Old | New |
| --- | --- |
| `pro_ping`, `pro_get_info`, `pro_server_status`, `pro_toolsets`, `pro_job_status` | `pro_server action=ping\|info\|status\|toolsets\|job` |
| `pro_run_background`, `pro_job_submit` | `pro_batch background=true`, `pro_pipeline background=true` |
| `pro_project_info`, `pro_list_layers`, `pro_save_project` | `pro_project action=info\|layers\|save` |
| `pro_add_layer`, `pro_remove_layer`, `pro_rename_layer`, `pro_set_visibility`, `pro_set_opacity` | `pro_layer action=add\|remove\|rename\|visibility\|opacity` |
| `pro_get_features`, `pro_sql`, `pro_select_by_expression`, `pro_layer_summary`, `pro_describe_layer`, `pro_field_statistics` | `pro_query action=rows\|count\|summary\|describe\|stats` |
| `pro_add_field`, `pro_calculate_field` | `pro_field action=add\|calculate` |
| `pro_buffer`, `pro_clip`, `pro_spatial_join`, `pro_dissolve`, `pro_merge`, `pro_reproject`, `pro_repair_geometry`, `pro_extract` | `pro_analysis operation=…` |
| `pro_export_layer`, `pro_export_image`, `pro_export_pdf`, `pro_export_map_series` | `pro_export` (format from the extension) |
| `pro_apply_categorized/graduated/utility/water_utility_symbology`, `pro_symbols` | `pro_symbology kind=…` |
| 12 domain / subtype / attribute-rule tools | `pro_domain`, `pro_subtypes`, `pro_attribute_rules` (+ `pro_apply_schema`) |
| 5 topology tools | `pro_topology action=rules\|describe\|create\|validate\|export_errors` |
| 16 GeoAI tools | `pro_geoai_info`, `pro_geoai_text`, `pro_geoai_infer task=…`, `pro_geoai_train action=…` (+ `pro_dl_notebook`) |
| 16 utility-network tools | `pro_un_describe`, `pro_un_author action=…`, `pro_un_topology`, `pro_un_trace`, `pro_un_subnetwork` |
| `pro_toolbox_datatypes`, `pro_inspect_model` | `pro_toolbox_info what=datatypes\|model` |
| 35 `pro_egdb_*` tools | `pro_egdb_setup`, `_connection`, `_principals`, `_privileges`, `_dataset`, `_versions`, `_admin`, `_replica` |
| `live_list_layers`, `live_query`, `live_zoom_to`, `live_add_layer` | `live_map action=layers\|query\|zoom\|add_layer` |
| `live_add_database`, `live_add_toolbox` | `live_add_to_project` (by extension) |
| `portal_whoami`, `portal_search_items`, `portal_get_item`, `portal_publish_layer`, `portal_share_item`, `portal_create_webmap`, `portal_set_layer_symbology/labeling` | `portal_connect action=whoami`, `portal_search`, `portal_item action=get`, `portal_publish`, `portal_share`, `portal_map action=create`, `portal_layer_schema action=renderer\|labels` |
| `webapp_create_dashboard` | `webapp_create kind=dashboard` |

</details>

### Core — analysis & speed (`core`, always on)

| Tool | What it does |
| --- | --- |
| **`pro_context`** | Whole project/workspace in ONE call (maps, schemas, counts, sample rows, themes) — start here |
| `pro_find_layers` | "Which layer has population data?" without reading every schema |
| `pro_project` | info / layers / save of an `.aprx` (`"CURRENT"` = the project open in Pro) |
| `pro_layer` | add / remove / rename / visibility / opacity |
| `pro_query` | rows / count / summary / describe / stats of a dataset |
| `pro_field` | add / calculate a field |
| `pro_analysis` | buffer, clip, spatial join, dissolve, merge, reproject, repair, extract |
| `pro_symbology` | categorized, graduated, AM/FM utility glyphs, the whole water-utility look |
| `pro_export` | a dataset to a file, a layout to PDF/PNG/JPG, a map series to one PDF |
| `pro_run_gp` / `pro_execute_code` | any GP tool by name / arbitrary ArcPy |
| **`pro_pipeline`** | N GP tools in one call, outputs chained (`{{prev}}`, `{{stepN}}`); `background=true` |
| **`pro_batch`** | N tools of ANY layer in one call; `parallel`, `background`, `{{callN.data…}}` references |
| **`pro_server`** | status (answers instantly, even during a long call) / job polling / toolsets / info / ping |

### Schema, validation, GeoAI, Utility Network, authoring

| Group | Tools |
| --- | --- |
| `schema` | `pro_domain` (list/create/assign/delete), `pro_subtypes` (describe/set), `pro_attribute_rules` (describe/add/evaluate/enable/disable/delete), **`pro_apply_schema`** (a whole schema in one call) |
| `validate` | `pro_topology` (rules/describe/create/validate/export_errors), `pro_check_geometry`, **`pro_validate_all`** ("is my data clean?") |
| `geoai` | `pro_geoai_info` (environment/model/model_types — call first), `pro_geoai_text` (detect from a sentence), `pro_geoai_infer` (detect, classify pixels/objects, change, embeddings, similar, foundation models), `pro_geoai_train` (export chips, train, accuracy, train-and-detect), `pro_dl_notebook` |
| `unet` | `pro_un_describe` (model / trace types), `pro_un_author` (build from a spec, or one piece at a time), `pro_un_topology`, `pro_un_trace` (add_locations / all 10 trace types), `pro_un_subnetwork` |
| `authoring` | **`pro_create_python_toolbox`** (workflow → `.pyt` tool, preferred), `pro_create_model` (ModelBuilder, linear feature-class chains only), `pro_toolbox_info` |

### Deep-learning notebooks — interview first, then a script (`geoai` toolset)

One tool: **`pro_dl_notebook`**. You say what you want; it does **not** guess batch size or
epochs. It replies with a recommended model and the questions to ask, you answer, and it
writes an `.ipynb` whose cells follow the same steps as a hand-written `arcgis.learn`
notebook — one step per cell, so you run it one cell at a time inside ArcGIS Pro.

1. `pro_dl_notebook(goal="a model that classifies objects")` → `status=ask_user`: the
   recommended model plus the questions (batch size, epochs, learning rate, chip size,
   backbone, mixed precision, early stopping…). Plain *"classification"* is ambiguous, so
   it returns `status=choose` (pixel / object / detect) instead of guessing.
2. The agent asks you in chat.
3. Same call again with your answers → `status=created`, saved in the **open Pro
   project's folder** (found through the live bridge). It refuses until `training_data`,
   `batch_size` and `epochs` are given (`data_path_later=true` waives only the path, and
   only when you said you will type it yourself).

| What you say | Model | Export format |
| --- | --- | --- |
| detect / segment buildings, each its own shape | Mask R-CNN | `RCNN_Masks` |
| boxes or counts | Faster R-CNN, RetinaNet, SSD, YOLOv3, DETReg, RT-DETR v2 | `PASCAL_VOC_rectangles` / `KITTI_rectangles` |
| classify **every pixel** (land cover) | U-Net, DeepLab, PSPNet, SAM + LoRA | `Classified_Tiles` |
| classify **each object** (roof type, damaged/intact) | Feature Classifier | `Labeled_Tiles` / `MultiLabeled_Tiles` / `Imagenet` |
| just "classification" | asks which of the three | — |
| **change detection** | asks: Esri's ready-made model, or train your own | — |
| change detection, ready-made ("from Esri") | **Esri Building Change Detection** — no training | two aligned 10-20 cm images |
| change detection, your own labels | Change Detector | `Classified_Tiles` exported with `in_raster2` |

**Esri's ready-made Building Change Detection** is a different kind of notebook: nothing is
trained, so it asks only for the *before* and *after* images (no batch size or epochs), then
downloads the model from ArcGIS Online, runs `DetectChangeUsingDeepLearning`, saves the
result and adds it to the map. Read its limits first (taken from the item page): it needs
**10-20 cm** aerial/drone imagery for both dates, accurately aligned; it outputs a
continuous change-magnitude raster, not a binary map; it works best in less densely
populated urban areas; Esri says it **cannot be fine-tuned**; and the item is flagged
**Requires Subscription** under the Esri Master License Agreement. The notebook prints the
licence before it downloads anything. (It is the only ready-made model wired up so far;
Esri publishes 170 deep-learning packages.)

Cell order of the training notebooks (mirrors the course notebook): environment check → imports → parameters →
`prepare_data` → `data.classes` → `show_batch` → model → `supported_datasets` →
`lr_find` → `fit` → `plot_losses` → metrics → `show_results` → `save` → optional
inference (`DetectObjectsUsingDeepLearning`, `ClassifyPixelsUsingDeepLearning` or
`ClassifyObjectsUsingDeepLearning`, matching the model). The metric cell depends on what
the class really has: detectors `average_precision_score`, pixel models `accuracy` /
`per_class_metrics` / `mIOU`, Feature Classifier `plot_confusion_matrix`, SAM + LoRA none.

Every constructor and `fit` argument the generator emits was read from the installed
`arcgis.learn` with `inspect.signature`; model-specific ones go in `options`
(e.g. `{"pointrend": true}`) and anything the class does not accept is refused.

**Adding the notebook to your project.** A `.ipynb` dropped into the project folder is
**not** picked up: Pro records notebooks as `projectItems` inside the `.aprx`, and neither `arcpy`
nor the public SDK can add one from outside (an attempt through the add-in was tried and removed).
Add it by hand: Catalog pane, right-click **Notebooks → Add and Open Notebook**, choose the file.

> What was verified: the generated cells compile, the files pass `nbformat.validate`,
> and the environment-check, import and parameter cells were executed on Pro's
> interpreter (the `prepare_data` cell then stops at a missing data path, as it should).
> What was **not**: the training and inference cells have never run on real labelled
> data here — there is no labelled-buildings dataset and no CUDA GPU on this machine.


### Toolbox authoring (`authoring` toolset)

**Turning a workflow into a tool with parameters** — use **`pro_create_python_toolbox`**. It writes a
working `.pyt` (parameters, validation, messages) and adds it to the open project. It handles any
tool, multi-input tools such as Intersect, and any intermediate type. Steps use keyword arguments;
`{{param}}` is a parameter, `{{stepN}}` an earlier output, `{{memory}}` an in-memory intermediate that
is deleted when the tool ends. Verified: a Clip -> Intersect -> Export `.pyt` ran on real data and its
output matched an independent arcpy computation (3 rows, area 9000.0). The first time Pro loads a
`.pyt` it shows a *trust the code source* dialog; until you click Trust, `live_*` calls time out.

**`pro_create_model`** writes an editable **ModelBuilder** model into a `.atbx`, but only **linear
chains of feature-class tools** are verified to run. It refuses (`code="unsupported_model"`) what
did not: multi-input tools (Intersect/Union/Merge...) fed from a reference, and folder/file/layer
intermediates such as an Export -> Train -> Detect deep-learning chain. `allow_unverified=True`
overrides it if you hold a Pro-authored model of the same chain to match. Canvas Groups cannot be
generated. `pro_toolbox_info what=model` reads any `.atbx` back (Esri's own included);
`pro_toolbox_info what=datatypes` lists valid parameter datatypes.

### Enterprise geodatabase (`egdb` toolset)

8 tools (each with an `action`) for the whole life of an enterprise geodatabase on **SQL Server, PostgreSQL and Oracle**:
create it, connect to it from Pro, manage users and privileges, version, archive, replicate, and
keep it healthy.

| Stage | Tool (`action`) | What you can ask for |
| --- | --- | --- |
| Create | `pro_egdb_setup` (create / enable / prepare_cloud / upgrade) | *"Create an enterprise geodatabase called GIS on my SQL Server"*, *"enable a geodatabase in this Azure SQL / RDS PostgreSQL database"* (prepare_cloud returns Esri's required SQL and can run it), *"can it be upgraded?"* |
| Connect | `pro_egdb_connection` (create / describe), `live_add_to_project` | A `.sde` file, added to the open project's **Databases**; pinned to a version, a historical marker, a moment in time, or a branch connection. |
| Users & privileges | `pro_egdb_principals` (create_user / create_role / drop_user / drop_role / set_password), `pro_egdb_privileges` | Create users/roles; give view (SELECT) or edit privileges; or grant SELECT / INSERT / UPDATE / DELETE **one by one** on simple tables (`grant` / `revoke` lists); change a password; drop a user or role. |
| Prepare data | `pro_egdb_dataset` (prepare / register_versioned / unregister_versioned / archiving) | Register with the geodatabase, Global IDs, editor tracking, attachments; traditional or branch versioning; feature history on/off. |
| Versioning | `pro_egdb_versions` (list / create / alter / delete / reconcile_post / health) | Traditional versioning end to end; diagnose/repair version tables. |
| Administration | `pro_egdb_admin` (connected_users / disconnect / accept_connections / block_connections / maintenance / create_view / config_keywords) | Who is connected, kick sessions, block connections before maintenance, compress/analyze/rebuild, database views, storage keywords. |
| Replication | `pro_egdb_replica` (create / list / synchronize / export_changes / import_message / export_acknowledgement / unregister / schema_export / schema_compare / schema_import) | One-way / two-way / check-out replicas, connected and disconnected (message-file) sync, keeping replica schemas in step. |

**Bring your own license and passwords.** Nothing license-related ships with the code. Pass your own
ArcGIS Server/Enterprise keycodes file as `authorization_file`, or set it once in the MCP server's
environment. Passwords are never typed into the chat: tools take the *name* of an environment
variable. For Claude Desktop, put both in the server entry's `env` block:

```json
"env": {
  "ARCGIS_EGDB_AUTHORIZATION_FILE": "C:\\path\\to\\your\\keycodes",
  "EGDB_SDE_PWD": "...",
  "EGDB_DBA_PWD": "..."
}
```

With SQL Server and Windows authentication (`account_authentication="OPERATING_SYSTEM_AUTH"`,
`schema_type="DBO_SCHEMA"`) no password is needed at all.

**Irreversible or disruptive operations need `confirm=True`** (they refuse with `confirm_required`
first): deleting versions or replicas, unregistering versioned data, blocking or ending connections,
upgrading, repairing version tables, dropping users/roles, reconcile with `delete_versions`.

**What was verified** (ArcGIS Pro 3.7). On SQL Server Express, in throwaway geodatabases that
`pro_egdb_setup action=create` itself created and that were dropped afterwards: every tool above ran, including a
real edit in a child version reconciled and posted into its parent, one-way replication with connected
and disconnected sync, replica-schema export/compare/import, a point-in-time connection returning the
older rows, branch registration, enabling a plain database, granular grants checked in
`sys.database_permissions`, and a password change proven by logging in with the new password. On
PostgreSQL (throwaway objects in an existing geodatabase, removed afterwards): users, roles, password
change, granular grants checked in `information_schema`, views, upgrade check, version diagnosis.
Oracle, cloud services and creating a PostgreSQL geodatabase were **not** run here; the cloud SQL was
checked for syntax only (SQL Server `SET PARSEONLY`, PostgreSQL inside a rolled-back transaction).

**Things testing uncovered, which the tools now handle:**

- `ReconcileVersions` matches version names **case-sensitively** and silently skips a mismatch
  (`dbo.child` vs the stored `DBO.child`) while still "succeeding". Names are resolved to their stored
  spelling, and the result says `reconciled` / `posted` from the log, not from the tool's status.
- The DEFAULT version is `dbo.DEFAULT` in a DBO-schema SQL Server geodatabase; it is looked up.
- Enabling a SQL Server database needs `READ_COMMITTED_SNAPSHOT`; if other connections block it, the
  error carries the exact `ALTER DATABASE` statement for a DBA.
- On a point-in-time connection, **`GetCount` ignores the moment** (returns today's count) while a
  cursor returns the historical rows; and the moment is read as **UTC** for non-versioned archives but
  as **server-local time** for traditional-versioned ones.
- `UpgradeGDB` *fails* on a current geodatabase (`ERROR 001046`); `pro_egdb_setup action=upgrade` reports
  `up_to_date` instead.
- A SQL Server login that still has sessions cannot be dropped, and those sessions may be pooled
  connections that `connected_users` never shows. `ArcSDESQLExecute` cannot end them (it wraps every
  statement in a transaction and `KILL` is not allowed there), so the tool refuses before dropping
  anything and returns the `KILL` statements for a DBA.
- PostgreSQL refuses to drop a role that still holds privileges (Create Database User grants some on
  PostGIS objects); after proving the role owns nothing, `DROP OWNED BY` revokes them first.
- Branch registration adds `GDB_BRANCH_ID`, `GDB_FROM_DATE`, … to the table, but `arcpy.ListFields`
  hides them.

**Not covered:** updating an expired geodatabase license (deliberately left out), editing and
reconciling branch versions (that needs a feature service from ArcGIS Enterprise; the version tools
accept a feature-service URL but it was not tested), historical markers (Pro creates them in its
Geodatabase Connection Properties), and database-level backup/restore.

### Live (`live_*`) — the OPEN Pro session via the .NET bridge

`live_ping`, `live_map` (layers / query / zoom / add_layer), `live_run_gp` (confirm; add-in 0.1.6+),
`live_export_layout`, `live_get_request` (what you queued from the ribbon), **`live_add_to_project`** (a `.sde`
/ `.gdb` into the open project's Databases — add-in 0.1.5+; an `.atbx` / `.tbx` / `.pyt` into its Toolboxes —
add-in 0.1.4+, verified on Pro 3.7).

### Portal (`portal_*`) — ArcGIS Online / Enterprise through the ArcGIS API for Python

Signs in through ArcGIS Pro by itself (see [Authentication](#authentication-portal-layer)). Built on the
installed arcgis **2.4.3**, every signature read with `inspect`, no deprecated calls (`Folder.add` instead of
`content.add`, `item.sharing` instead of `item.share`, `folders.create` instead of `create_folder`).

| Group | Tools | What you can ask for |
| --- | --- | --- |
| `portal_base` | `portal_connect` (connect/whoami/list/disconnect), `portal_search`, `portal_item` (get, data, update, move, copy, protect, delete, dependencies, download, **export** → downloaded, reassign, usage), `portal_folders`, **`portal_publish`**, `portal_share`, `portal_clone` | *"Publish this CSV / shapefile / gdb / feature class"*, *"export my layer to a file geodatabase"*, *"share these with the QA group"*, *"copy these items into the other org"* |
| `portal_data` | `portal_layer_query` (describe, paged query, count, stats), `portal_layer_edit` (edit with dry-run diff, calculate, append/upsert, truncate), `portal_layer_schema` (add/delete field, capabilities, **renderer from the data**, labels, raw definition, **filtered read-only view**, overwrite) | *"Add a STATUS field and set it to 'new' where it is empty"*, *"make a read-only view of POP > 50 with only NAME and POP"* |
| `portal_maps` | `portal_map` (create from items/URLs with the org basemap, auto popups and a renderer; describe; add/remove layers; popup; renderer; filter; basemap; web scene), `portal_apps` (Survey123 info/export, dashboard clone-and-remap, StoryMap, dependency graph) | *"A web map of that view coloured by population"*, *"export my survey's data to CSV"* |
| `portal_admin` | `portal_users` (search/get/create/update_access/disable/enable/delete/transfer), **`portal_onboard`** (CSV template → validate → run → report), `portal_groups`, `portal_roles` (roles, presets like *GIS Specialist*, user types, privileges), `portal_admin` (org summary, credits, usage, audit CSV, licenses, history, Enterprise servers/services/logs, notebooks, this server's audit log) | *"Onboard these 50 users from the CSV"*, *"who never logged in?"* |
| `portal_analysis` | `portal_geocode` (single / reverse / suggest free; **batch** with a credit estimate), `portal_analysis` (hosted spatial analysis: list, describe, estimate, run; any GP service) | *"Geocode this address table"* — it tells you the credits first |
| knowledge + escape hatches | `portal_api` (search / doc / members of the INSTALLED library, deprecations + replacements, version, module map), `portal_python` (read mode by default; write mode needs confirm and is audited; sessions), `portal_rest` (raw REST on the portal's own hosts) | Anything no tool covers |

**CSV onboarding** follows the rules of the `bulk_add_users.py` script it was ported from: every row validated
first (one bad row blocks the run); role names and user types resolved to IDs; user type set **before** the role;
nobody created directly as Administrator (created as User, then promoted, flagged in the preview); each account
re-read after the write; preset roles created only when the CSV uses them; ArcGIS Online without a password sends
an email invitation; the report CSV never contains passwords; UTF-8 with BOM so Arabic names survive Excel.

**Confirm first:** deleting items/folders/groups/users/roles, sharing publicly, bulk edits (deletes, calculate,
append, truncate), schema deletes, user/role/admin changes, onboarding runs, clones, notebook runs, service
start/stop, credit-spending geocoding/analysis, `portal_python` write mode and non-read REST POSTs all return
`code: "confirm_required"` with a preview first. Low-risk additive writes (upload, add field, folder, private
group, view, web map) run directly. Every write lands in the audit log
(`%LOCALAPPDATA%\arcgis_pro_salah_mcp\audit.jsonl`).

**What was verified live** (a real ArcGIS Online organization, a non-admin Publisher account signed in
through Pro; throwaway
`mcp_test_*` items in its own content, kept private, deleted afterwards and checked gone): sign-in through Pro,
search, folders, roles, user types (403 → aliases), get user, `portal_api` search/doc, `portal_python` read +
write refused, `portal_rest`, org summary, single geocode (free), batch geocode refused without confirm,
parallel batch; then publish a CSV → describe → query → stats → add field → dry-run edit → updates + adds →
delete refused, then confirmed → calculate → unique renderer → labels → **filtered view** (7 of 12 rows, 2
visible fields) → web map from the view with class breaks → add/remove layer, popup, basemap by alias →
dependency graph → update / protect / delete refused / unprotect → **export to File Geodatabase and download** →
private group share/unshare → public sharing refused → clone preview → analysis credit estimate → batch with a
reference → onboarding template and validation of the sample CSV. **Not run live** (no admin rights / would
spend credits): creating users and roles, admin reports, Enterprise server tools, paid geocoding and analysis —
those are covered by mocked unit tests.

**Two arcgis 2.4.3 bugs found and worked around:** `create_view(view_layers=[0])` (typed `list[int]`) crashes
reading `.properties` from the int, and `create_view(visible_fields=…, query=…)` reads the new view's layers
before they exist — both after the view item was created. The tool creates the view plain, waits for its layer,
then applies the filter and visible fields itself; if anything fails it returns the ids of what was created.

### WebApp (`webapp_*`)

`webapp_create` (`kind=app` — map + widgets; `kind=dashboard` — live indicators, category breakdown and a
features-in-view list), `webapp_github_pipeline` (deploy to GitHub + Pages).

---

## Performance

Measured on a real ArcGIS Pro 3.7 install:

| Operation | Cost |
| --- | --- |
| `import arcpy` | **25.1 s**, once per process — done in the background at start-up |
| `import arcgis` (ArcGIS API for Python) | 17 s after arcpy, 33-60 s cold — background too, when a portal toolset is on |
| Sign-in through Pro (`GIS("pro")`) | **1.0 s** |
| Portal search | 0.2-0.6 s |
| Index of the installed arcgis library | 13 s, once per arcgis version (was 256 s before `arcgis.learn` / `arcgis.map.dataclasses` were skipped); then a disk cache, **0.1 s** |
| `Describe` + `ListFields` + `GetCount` | ~430 ms; cached repeat **1 ms** |
| `pro_context` over an 11-layer geodatabase | 5.4 s cold, 1.2 s warm |
| Tool schema, every request | 72 tools ≈ 23.6k tokens (chars/4) |

The biggest cost is the **round trip** — every tool call is a full model inference. So: `pro_context` reads a
whole project in one call, `pro_pipeline` chains GP tools, and **`pro_batch`** runs several tools of any layer in
one call (in parallel when they are independent). Long work goes `background=true` and is polled with
`pro_server(action="job")`, so nothing hits the client's ~1 minute tool timeout. `pro_server(action="status")`
always answers instantly.

**Trim the tool list** when you don't need everything:

```bat
set ARCGIS_SALAH_TOOLSETS=core,portal
```

| Setting | Tools | Tokens (chars/4) | Before the merge |
| --- | --- | --- | --- |
| unset (everything) | 72 | ~23.6k | 156 tools, ~22.8k (old portal had 9 tools) |
| `core` | 14 | ~2.9k | 45 tools |
| `core,portal` | 36 | ~11.3k | — |
| `core,portal_base` | 24 | ~6.0k | — |
| `core,geoai` | 19 | ~5.5k | 62 tools, ~8.4k |
| `core,egdb` | 22 | ~6.9k | 80 tools, ~10.9k |

Groups: `core` (always kept), `geoai`, `unet`, `schema`, `validate`, `authoring`, `egdb`, `live`, `portal` (=
`portal_base` + `portal_data` + `portal_maps` + `portal_admin` + `portal_analysis`), `webapp`.
`pro_server action=toolsets` shows what is active.

---

## When something hangs or fails

These came out of a real session (an agent driving the server from Claude Desktop) and are now handled:

| Symptom | Cause (measured) | What to do now |
| --- | --- | --- |
| A call ran past about a minute, came back as a timeout, and the calls after it hung | The MCP client stops waiting after roughly a minute, while the server ran every tool **on its event loop**, so one long call froze it | Tools no longer block the server: `pro_server action=status` answers instantly and says what is running. For long work (creating a geodatabase, maintenance, replicas, many versions, exports) use **`pro_batch background=true`** (or `pro_pipeline background=true`) and poll **`pro_server action=job`**. The first ArcPy call after the server starts waits for the ~25 s arcpy import (measured ~47 s with a PostgreSQL connection). |
| `arcpy.ListVersions` → `ERROR 87395`, `ArcSDESQLExecute` → `ERROR 87413`, while the connection itself works | With **PostgreSQL**, ArcPy only works on the thread that imported it; the server imported it on a warm-up thread and ran tools on another (SQL Server was unaffected) | Every ArcPy tool now runs on one dedicated thread that also does the import - the Python counterpart of Pro's Main CIM Thread. |
| `ArcGISProject("CURRENT")` fails | The server's Python runs outside ArcGIS Pro | `"CURRENT"` is now resolved to the project open in Pro through the live bridge (`pro_execute_code` and every tool that takes a project path). Edits saved to it from outside are overwritten when Pro saves - change the open session with `live_*` tools. |
| `live_run_gp` → `NullReferenceException` for every tool | The add-in started the tool from its HTTP thread, which is neither Pro's UI thread nor the MCT | Add-in **0.1.6** builds the arguments with `QueuedTask.Run` and starts the tool from the UI thread, reporting each attempt in `steps`; bridge errors now include the failing stack frame (`where`). |
| New tools are missing after an update | The MCP client keeps the server process it started; `pip install -e` only takes effect in a new process | Fully quit and reopen the MCP client (Claude Desktop: quit from the tray). |

---

## How to run the Utility Network tools

A utility network has hard preconditions. These were all hit while building a
real one, so the tools now either handle them or say plainly what is missing.

**Before you start:** the feature dataset's classes must be **Z- and M-enabled**
(`ERROR 160527` otherwise), and the utility network needs a service-territory
polygon.

```
1. pro_un_author(action="create", feature_dataset=..., name="WaterUtilityNetwork",
                 service_territory=...)
      -> ArcGIS auto-creates WaterDevice / WaterLine / WaterJunction /
         WaterAssembly / WaterSubnetLine and the Structure* classes.

2. pro_un_author(action="domain_network", utility_network=un, name="Water",
                 tier_definition="HIERARCHICAL", subnetwork_controller_type="SOURCE")

3. pro_un_author(action="tier", utility_network=un, domain_network="Water",
                 name="Distribution", rank=1, topology_type="MESH")
      -> A hierarchical network needs a tier GROUP and a SUBNETWORK FIELD; both
         are created for you. Two constraints ArcGIS enforces:
         * rank is per tier group, so with one tier per group rank is always 1;
         * every tier in the network must share the same topology type.

4. Asset groups are SUBTYPES on the UN classes, and asset types are coded values
   in ASSETTYPE. Create them with pro_subtypes(action="set") /
   pro_domain(action="create").
      -> enable_topology refuses until EVERY asset group of EVERY network class
         has an ASSETTYPE domain (ERROR 003077), and each domain must include
         the field's existing default value.

5. pro_un_author(action="rule", utility_network=un, rule_type="JUNCTION_EDGE_CONNECTIVITY",
                 from_class="WaterDevice", from_assetgroup="Valve", from_assettype="Gate",
                 to_class="WaterLine", to_assetgroup="Main", to_assettype="PVC")
      -> The topology will not enable with no rules at all (ERROR 002673).

6. pro_un_topology(un, action="enable")   then   pro_un_topology(un, action="validate")

7. Load features INSIDE an edit session (arcpy.da.Editor) — UN classes reject
   edits outside one.

8. Trace:
     pro_un_trace(un, action="add_locations", out_feature_class=out_fc)  # select start features first
     pro_un_trace(un, trace_type="CONNECTED", starting_points=out_fc,
                  result_types=["AGGREGATED_GEOMETRY"], out_lines=...)
      -> A trace REJECTS a plain feature-class copy of your start points
         (ERROR 001911); it needs the purpose-built locations table.
```

**Changing the schema?** Run `pro_un_topology(un, action="disable")` first — ArcGIS
refuses schema edits while the topology is enabled.

---

## How to run the GeoAI tools

**Always start with `pro_geoai_info` (what="environment").** Deep-learning failures in
ArcGIS arrive late and opaque; this reports the Image Analyst licence, whether
`arcpy.geoai` is present, the torch version and whether a CUDA GPU exists.

### Three ways to work, from least to most effort

**1. Describe what you want, in words** — no model, no training, no class list:

```
pro_geoai_text(
    in_raster = "imagery/scene.tif",
    prompt = "swimming pool",           # or "solar panel", "flooded road"
    box_threshold = 0.25,
    text_threshold = 0.25)
```

Returns each detection's label and confidence. A grounded vision-language model
(GroundingDINO + BERT) does the work. Raise the thresholds if you get false
positives, lower them if you get nothing.

> **Caveats, measured on this CPU-only machine:** 731 s for one 1024×1024 image and
> 156 s for one 512×512 tile. In **both** runs the returned boxes were zero-size at the
> raster's top-left corner, and a scene with dozens of buildings produced a single
> detection — so do not rely on it to *locate* things yet. The cause was not found (a GPU
> and other tile sizes were not tried). On CPU, run it through `pro_batch(..., background=true)`.

**2. "More like this"** — label ONE example, find the rest, still no training:

```
pro_geoai_infer(task="embeddings", in_data=..., out_embeddings=..., model_definition=...)
pro_geoai_infer(task="similar", embedding_features=..., query_features=...,
                out_features=..., threshold=0.8)
```

A vision foundation model (DINOv3-class, SAM) turns the imagery into embedding
vectors; similarity search then replaces training entirely.

**3. Train your own** — when you need a specific, repeatable classifier:

```
pro_geoai_train(action="export", in_raster=..., out_folder=..., in_class_data=...,
                task="object_detection")
pro_geoai_train(action="train", in_folder=..., out_folder=..., model_type="FASTERRCNN", max_epochs=20)
pro_geoai_infer(task="detect", in_raster=..., out_features=..., model_definition=...)
```

`pro_geoai_train(action="train_and_detect")` chains all three. `pro_geoai_info(what="model_types")` lists the
valid model types per task, and the training format is derived from `task` so it
cannot mismatch what you later train — the classic "trained for two hours, then
it failed" bug.

### Getting imagery

Any georeferenced raster works. For a quick test you can pull real satellite
imagery from Esri's World Imagery service:

```
https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/
    MapServer/export?bbox=<xmin,ymin,xmax,ymax>&bboxSR=3857&imageSR=3857
    &size=1024,1024&format=tiff&f=image
```

Write a matching `.tfw` world file so the result is georeferenced.

---

## Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARCGIS_PORTAL_URL` | `https://www.arcgis.com` | Portal target (AGOL or Enterprise) |
| `ARCGIS_PROFILE` | — | Stored ArcGIS API for Python profile (used when Pro is not signed in) |
| `ARCGIS_JS_SDK_VERSION` | `5.0` | Maps SDK for JS version pinned in generated apps |
| `ARCGIS_WEBAPP_OUT` | `webapp-build` | Default output folder for generated apps |
| `ARCGIS_BRIDGE_PORT` | `2026` | Live bridge loopback port (no token) |
| `ARCGIS_BRIDGE_READONLY` | `false` | Truthy ⇒ `live_*` refuses commands that change the session |
| `CLI_ANYTHING_ARCGIS_PYTHON` / `ARCGIS_PRO_PYTHON` | — | Explicit path to `arcgispro-py3` python.exe |
| `ARCGIS_SALAH_TOOLSETS` | all | Comma list of tool groups to keep (see [Performance](#performance)) |
| `ARCGIS_SALAH_CACHE_TTL` | `300` | Schema-cache TTL in seconds; `0` disables caching |
| `ARCGIS_SALAH_NO_WARMUP` | `0` | Truthy skips the background arcpy import |
| `ARCGIS_SALAH_NO_REEXEC` | `0` | Truthy skips the `arcgispro-py3` re-exec |
| `ARCGIS_EGDB_AUTHORIZATION_FILE` | — | Your own keycodes file for `pro_egdb_setup` (create / enable) |
| `ARCGIS_SALAH_AUDIT_LOG` | `%LOCALAPPDATA%\arcgis_pro_salah_mcp\audit.jsonl` | Audit log of every portal write (`off` disables) |
| `ARCGIS_SALAH_MAX_EDITS` | `5000` | Feature edits per `portal_layer_edit` call |
| `ARCGIS_SALAH_MAX_USERS` | `200` | Rows per `portal_onboard` run |
| `ARCGIS_SALAH_CREDIT_LIMIT` | `100` | Most credits one confirmed call may spend |
| `ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC` | `true` | `false` disables `portal_python mode=write` |
| `ARCGIS_SALAH_REST_HOSTS` | — | Extra hosts `portal_rest` may call |
| `ARCGIS_SALAH_NO_PORTAL_WARMUP` | `0` | Truthy skips the background arcgis import and Pro sign-in |
| `ARCGIS_SALAH_CACHE_DIR` | `%LOCALAPPDATA%\arcgis_pro_salah_mcp` | Where the arcgis API index is cached |

---

## Develop & test

Core tests run without any ArcGIS install (CI runs this):

```bash
PYTHONPATH=src python -m pytest tests/test_core.py
```

139 tests covering the result envelope/guard behaviour, graceful ArcPy- and
bridge-absent degradation, the metadata inference, the schema cache, pipeline
placeholder substitution, the `.pyt` and `.atbx` generators, toolset gating, the
argument validation that deliberately runs *before* any backend import, `pro_batch`
(references, parallel, background), and the portal layer against fake GIS objects:
every CSV-onboarding rule, secrets/audit/credit rails, the `portal_python` read-mode
guard, the REST host allowlist, layer edit rules and renderers, the knowledge index,
resources and prompts. They also pass with `arcgis` and `arcpy` blocked from import.

### Regenerating the ribbon icons

The icons are rasterized from the official Calcite/Octicons SVGs by
`SalahAIBridge/Images/make_icons.py` (dev-only; needs `matplotlib`,
`svgpath2mpl`, `Pillow`):

```bat
pip install matplotlib svgpath2mpl pillow
python SalahAIBridge\Images\make_icons.py
```

---

## Repository layout

```
src/arcgis_pro_salah_mcp/
  server.py          FastMCP server — every pro_/live_/portal_/webapp_ tool, the resources
                     and the prompts; pro_batch; toolset gating; thread routing
  bootstrap.py       self-healing arcgispro-py3 discovery, arcpy + arcgis warm-ups
  arcpy_thread.py    the one thread that imports and runs ArcPy
  stdio_guard.py     keeps library print() output off the MCP stdio channel
  config.py          env-driven config (portal URL, profile, JS SDK version, bridge port)
  _result.py         {"ok": ...} envelope + guard decorator
  guides/            the playbooks (pro_server action=guide, and arcgis://guide/<topic>)
  pro/               Layer 1  — ArcPy ops, one-call context, cache, pipeline/jobs,
                     schema (domains/subtypes/rules), validate (topology),
                     geoai, unet, toolbox (.pyt), modelbuilder (.atbx), egdb
  live/              Layer 1b — client/ops/policy for the live .NET bridge
  portal/            Layer 2  — ArcGIS API for Python: connections (Pro sign-in),
                     content, access (users/groups/roles/CSV onboarding), layers,
                     maps, apps, analysis, admin, knowledge (installed-API index),
                     pyexec + rest (escape hatches), safety (confirm/audit/secrets),
                     serialize, _imports (one lock for every arcgis import)
  webapp/            Layer 3  — generator.py (web app), dashboard.py (dashboard),
                     github.py (deploy), templates/ (index.html, app.js,
                     dashboard.html, dashboard.js)
SalahAIBridge/      the ArcGIS Pro .NET add-in (live bridge + Salah MCP ribbon)
tests/test_core.py   backend-free tests
demos/               setup_sample.py (build a sample.gdb), check_github_token.py
docs/                ARCHITECTURE.md, PROTOCOL.md
```

---

## Troubleshooting

- **"No module named 'arcgis_pro_salah_mcp'"** when a ribbon button runs → install
  the package into `arcgispro-py3` (Step 1).
- **Ribbon button shows old behavior / Add-In Manager shows the wrong version** →
  close Pro, reinstall the rebuilt `.esriAddinX`, confirm the version in Add-In
  Manager. Pro reads it from `…\Documents\ArcGIS\AddIns\ArcGISPro\{guid}\`.
- **Publish: vector tile fails** → vector tiles publish via `arcpy.sharing.Publish`
  and can't use `overwriteExistingService`; a re-publish removes the prior
  same-named item first. Caching finishes server-side after the item appears.
- **Deploy: HTTP 403 "Resource not accessible by personal access token"** → token
  permissions; see [Deploy to GitHub](#deploy-to-github) and run
  `demos/check_github_token.py --try-create`.

---

ArcGIS, ArcGIS Pro and ArcPy are trademarks of Esri; this is an independent
integration and is not affiliated with Esri.
