# Workflow: Pro (analyze) -> Portal (publish) -> WebApp (visualize)

| Stage | Tools | Notes |
|---|---|---|
| Understand the data | `pro_context` (ONE call: maps, layers, schemas, counts, samples), `pro_find_layers` | Start here. `"CURRENT"` = the project open in Pro (via the live bridge). |
| Analyze locally | `pro_analysis`, `pro_pipeline` (chain N GP tools in one call), `pro_run_gp`, `pro_query`, `pro_field` | ArcPy runs outside Pro on files on disk. |
| Drive the open Pro session | `live_*` | Needs the SalahAIBridge add-in running in Pro. |
| Publish | `portal_publish` (file / .gdb / feature class / CSV -> hosted layer), `portal_share` | Signs in through ArcGIS Pro automatically. |
| Shape the service | `portal_layer_schema` (fields, renderer, labels, views), `portal_layer_edit` | Hosted services only. |
| Map it | `portal_map action=create` (web map JSON, org basemap, popups, smart renderer) | |
| Visualize | `webapp_create` (app or dashboard, Maps SDK for JS 5.0), `webapp_github_pipeline` | Static, no backend. |

## A typical end-to-end run (4 calls)

1. `pro_context(aprx_path="CURRENT")`
2. `pro_pipeline(steps=[buffer, clip, dissolve])`
3. `pro_batch(calls=[portal_publish(...), portal_map(create, layers=["{{call1.data.published_item.id}}"])])`
4. `webapp_create(kind="dashboard", webmap_id=...)`

Prefer ONE call that does several things (`pro_batch`, `pro_pipeline`, `pro_context`) over many small calls:
every extra tool call is a full model round trip, and that - not ArcPy - is the dominant cost.
