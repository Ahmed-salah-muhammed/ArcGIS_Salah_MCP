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
| `ping` | — | `{ protocol_version, addin_version, project, project_path, maps[], layouts[], active_view }` |
| `list_layers` | `{ map? }` | `{ map, layers: [{name, type, visible}] }` |
| `zoom_to` | `{ layer, selection? }` | `{ layer, extent }` |
| `query` | `{ layer, where?, fields?, limit? }` | `{ fields, rows[], returned }` |
| `run_gp` | `{ tool, args[], kwargs{} }` | `{ tool, outputs[] }` (outputs added to active map) |
| `add_layer` | `{ path }` | `{ added, map }` |
| `export_layout` | `{ layout, out_path, dpi? }` | `{ output, dpi }` |
| `add_toolbox` | `{ path }` | `{ path, registered, already_registered, steps[] }` |
| `add_database` | `{ path }` | `{ path, registered, already_registered, steps[] }` |
| `get_request` | `{ clear? }` | `{ pending, kind, text, created_at }` (what the user queued from the ribbon) |
| `selection` | `{ action: get\|select\|clear, layer?, where?, method?, fields?, limit?, map? }` | get: `{ map, total, layers: [{layer, count, object_ids[]}], fields?, rows[]? }`; select: `{ layer, selected }`; clear: `{ cleared }` |
| `layer_info` | `{ layer, map? }` | `{ name, type, visible, transparency, geometry, definition_query, selected, renderer, count, count_shown, source, dataset, fields[], labels }` |
| `layer_set` | `{ layer, map?, definition_query?, visible?, transparency?, labels?, renderer?, name?, remove? }` | `{ changed[], layer: layer_info }` (or `{ changed: ["removed"], removed, map }`) |
| `edit_features` | `{ layer, map?, adds?, updates?, deletes?, save?, name? }` | `{ layer, added, updated, deleted, added_object_ids[], saved, has_unsaved_edits, steps[] }` |
| `edit_session` | `{ action: status\|undo\|redo\|save\|discard, map? }` | `{ action, has_unsaved_edits, editing_enabled, can_undo, can_redo, undo_stack[] }` |

### Notes per command
- **`ping`** — cheap health/context read; safe to call often.
- **`run_gp`** — builds the arguments with `Geoprocessing.MakeValueArray` inside `QueuedTask.Run` and starts
  `Geoprocessing.ExecuteToolAsync(tool, values, env, flags: ...)` on the UI dispatcher. Until add-in 0.1.7 the
  call bound to the overload that takes a `CancelableProgressor`, with null, and threw NullReferenceException
  for every tool. Fallbacks: `CancelableProgressor.None`, then without adding outputs to the map. Returns
  `{ tool, outputs[], via, steps[] }`. `tool` is `toolbox.Tool` (`management.AddField`); a layer name of the
  active map is a valid input. Verified live.
- **`selection`** (0.1.7) — `get` reads `Map.GetSelection()`; with `layer` it also returns that layer's
  selected rows (only the `fields` asked for). `select` is `FeatureLayer.Select(where, method)` with `method`
  new | add | remove | and. `clear` clears one layer or the whole map. It changes no data.
- **`layer_info` / `layer_set`** (0.1.7) — `layer_set` changes how the layer is shown in the open map, never
  its data: `definition_query` (`""` clears), `visible`, `transparency` 0-100, `labels`
  `{ visible?, expression? }` (Arcade, written to the layer's first label class), `renderer`
  `{ type: simple|unique|graduated, field?, color?: [r,g,b], to_color?, classes?, size? }`, `name`, or
  `remove: true` (out of the map; the data stays). `count_shown` applies the definition query.
- **`edit_features`** (0.1.7) — one `EditOperation` (on Pro's undo stack, inside the user's edit session):
  `adds: [{ attributes?, geometry? }]`, `updates: [{ object_id, attributes?, geometry? }]`,
  `deletes: [object ids]`. `geometry` is `{ x, y, wkid? }` (4326 by default) or Esri geometry JSON, projected
  to the layer's spatial reference; dates are ISO strings; unknown or read-only fields are refused before
  anything is changed. Works on feature layers and standalone tables. Unsaved unless `save: true`, which
  saves the whole edit session. Editing is switched on first if the project has it off.
- **`edit_session`** (0.1.7) — `status`, `undo` / `redo` (the map's operation manager), `save` / `discard`
  (`Project.SaveEditsAsync` / `DiscardEditsAsync`: EVERY unsaved edit, the user's included).
- **No active view** — commands that take `map?` use the active map view's map, or the project's first map
  when no map view is active (after a geoprocessing tool the focus can be on another pane).
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
  `Project.GetItems<GDBProjectItem>()`. `pro_egdb_connection action=create` calls it after writing the file.
- **`get_request`** — returns the request the user parked from the ribbon (Create
  Web App prompt / Publish); `kind` is `web_app` or `publish`, `text` is what they
  typed. Pass `{"clear": true}` to consume it. Lets the agent pick up the user's intent.

## Client-side safety policy

Because these commands act on a *live* session a human is using, the Python
client (`live/policy.py`) enforces two guard rails **before** sending a request,
so a refused command never reaches the add-in:

- **Read-only mode** — set env `ARCGIS_BRIDGE_READONLY` (truthy: `1/true/yes/on`)
  to refuse every mutating command (`run_gp`, `add_layer`, `export_layout`, `add_toolbox`, `add_database`,
  `layer_set`, `edit_features`, and `edit_session` except `status`).
  Reads and navigation (`ping`, `list_layers`, `query`, `zoom_to`, `selection`, `layer_info`) stay allowed.
  Refusal envelope: `{"ok": false, "error": "...", "code": "readonly"}`.
- **Destructive confirmation** — `run_gp` can overwrite/delete data, so it
  requires an explicit `confirm=true`; so do `deletes` in `edit_features`, `save: true`, and `edit_session`
  `save` / `discard` (they act on the user's own unsaved edits too). Without it:
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
