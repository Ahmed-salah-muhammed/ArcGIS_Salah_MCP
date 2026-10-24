# Live Bridge Protocol

The contract between the Python MCP server (`live_*` tools) and the .NET add-in
running inside ArcGIS Pro. Keep this file authoritative — both sides implement
against it.

## Transport

- **HTTP/1.1 over loopback only:** `http://127.0.0.1:<port>`. Default port
  `2026` (override with env `ARCGIS_BRIDGE_PORT`). Never bind a routable IP.
- **One endpoint:** `POST /cmd`. (Optionally `GET /health` for a no-auth liveness
  check returning `{"ok":true,"data":"alive"}`.)
- **Content-Type:** `application/json; charset=utf-8`.

## Authentication

None. The listener binds `127.0.0.1` only (never a routable interface), so it is
reachable solely from processes on the same machine. This is a local developer
tool — there is no token; `POST /cmd` is accepted from any loopback client.

## Envelope

Identical to the rest of the system, so the agent sees one contract everywhere.

Request body:
```json
{ "command": "zoom_to", "params": { "layer": "cities" } }
```

Response body:
```json
{ "ok": true,  "data": { "...": "..." } }
{ "ok": false, "error": "Layer 'cities' not found." }
```

HTTP status is `200` for both ok/err application results; non-200 only for
transport failures (400 bad request / 404 / 500).

## Execution rule

Every handler that touches the project, maps, layouts or views runs its work
inside `QueuedTask.Run(() => { ... })` (the Main CIM Thread). The HTTP worker
thread awaits that task, then serializes the result.

## Command catalog (v1)

| `command` | `params` | `data` returned |
| --- | --- | --- |
| `ping` | — | `{ project, maps[], layouts[], active_view }` |
| `list_layers` | `{ map? }` | `{ map, layers: [{name, type, visible}] }` |
| `zoom_to` | `{ layer, selection? }` | `{ layer, extent }` |
| `query` | `{ layer, where?, fields?, limit? }` | `{ fields, rows[], returned }` |
| `run_gp` | `{ tool, args[], kwargs{} }` | `{ tool, outputs[] }` (outputs added to active map) |
| `add_layer` | `{ path }` | `{ added, map }` |
| `export_layout` | `{ layout, out_path, dpi? }` | `{ output, dpi }` |
| `add_toolbox` | `{ path }` | `{ path, registered, already_registered, steps[] }` |
| `add_database` | `{ path }` | `{ path, registered, already_registered, steps[] }` |
| `get_request` | `{ clear? }` | `{ pending, kind, text, created_at }` (what the user queued from the ribbon) |

### Notes per command
- **`ping`** — cheap health/context read; safe to call often.
- **`run_gp`** — builds the arguments with `Geoprocessing.MakeValueArray` inside `QueuedTask.Run`, starts
  `Geoprocessing.ExecuteToolAsync` on the UI dispatcher (add-in 0.1.6; calling both from the listener thread
  failed with NullReferenceException for every tool), retries without adding outputs to the map, and
  returns `{ tool, outputs[], via, steps[] }`.
- **Errors** from any handler are `{ ok:false, error, type, where }`; `where` is the first stack frame.
- **`zoom_to`** — `MapView.Active.ZoomToAsync(layer)` (or to the layer's
  selection when `selection: true`).
- **`export_layout`** — resolve the layout by name from the active project,
  `layout.ExportAsync(new PDFFormat{ Resolution = dpi, OutputFileName = out_path })`.
- **`add_toolbox`** (add-in 0.1.4+) — registers an existing `.atbx`/`.tbx`/`.pyt` in the open project's
  Toolboxes with the public `ItemFactory.Create` + `Project.AddItem`, verified against
  `Project.GetItems<GeoprocessingProjectItem>()`; attempts are returned in `steps`.
- **`add_database`** (add-in 0.1.5+) — adds an existing `.sde` connection file (or a `.gdb`) to the open
  project's Databases with the same public `ItemFactory.Create` + `Project.AddItem` route, verified against
  `Project.GetItems<GDBProjectItem>()`. `pro_egdb_create_connection` calls it after writing the file.
- **`get_request`** — returns the request the user parked from the ribbon (Create
  Web App prompt / Publish); `kind` is `web_app` or `publish`, `text` is what they
  typed. Pass `{"clear": true}` to consume it. Lets the agent pick up the user's intent.

## Client-side safety policy

Because these commands act on a *live* session a human is using, the Python
client (`live/policy.py`) enforces two guard rails **before** sending a request,
so a refused command never reaches the add-in:

- **Read-only mode** — set env `ARCGIS_BRIDGE_READONLY` (truthy: `1/true/yes/on`)
  to refuse every mutating command (`run_gp`, `add_layer`, `export_layout`, `add_toolbox`, `add_database`).
  Reads and navigation (`ping`, `list_layers`, `query`, `zoom_to`) stay allowed.
  Refusal envelope: `{"ok": false, "error": "...", "code": "readonly"}`.
- **Destructive confirmation** — `run_gp` can overwrite/delete data, so it
  requires an explicit `confirm=true`. Without it:
  `{"ok": false, "error": "...", "code": "confirm_required"}`.

These are agent-facing safeguards, not transport security; the loopback bind is
the trust boundary. (Server-side enforcement in the add-in is a planned
defense-in-depth follow-up.)

## Error model

| Situation | Response |
| --- | --- |
| Malformed JSON | HTTP 400, `{"ok":false,"error":"bad request: ..."}` |
| Unknown command | HTTP 200, `{"ok":false,"error":"unknown command 'x'"}` |
| Handler threw | HTTP 200, `{"ok":false,"error":"<message>","type":"<ExceptionType>"}` |
| No open project / no active view | HTTP 200, `{"ok":false,"error":"no active project/view"}` |

## Python client (`live/client.py`) responsibilities

- Read `ARCGIS_BRIDGE_PORT` from config (default 2026).
- `post(command, params) -> dict`: send the request, parse JSON, and return the
  envelope as-is (so `live_*` tools just forward it).
- Surface connection errors as `{"ok": false, "error": "bridge unreachable: ..."}`
  so the agent gets a clean message when ArcGIS Pro / the add-in isn't running.

## Versioning

Add a `protocol_version` to the `ping` `data`. Bump it on breaking changes;
the client can warn on mismatch.
