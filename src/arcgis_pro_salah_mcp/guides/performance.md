# Performance: how to split a big task and finish it fast

Measured on this machine: `import arcpy` 25 s (hidden by the startup warm-up), `import arcgis` 17 s after
arcpy (also warmed up in the background when a portal toolset is on), `GIS("pro")` 1.0 s, a portal search
0.2 s, a cached `pro_query action=summary` 1 ms. The expensive thing is the **round trip**: every tool call is a full
model inference. So:

1. **Read once.** `pro_context` returns maps, layers, schemas, counts and sample rows in one call.
2. **Chain geoprocessing.** `pro_pipeline(steps=[...])` runs N GP tools server-side; `{{prev}}` / `{{stepN}}`
   pass outputs along without coming back to you.
3. **Batch anything.** `pro_batch(calls=[{"tool": "...", "arguments": {...}}, ...])` runs several tools (any
   layer: pro_, portal_, live_, webapp_) in one round trip.
   - Reference an earlier result: `"{{call1.data.published_item.id}}"` (1-based; dotted path into its envelope).
     A string that is exactly one placeholder keeps the original type (list, dict, number).
   - `parallel=true` runs independent non-ArcPy calls concurrently (portal reads, live queries). ArcPy calls
     always run one at a time on the single arcpy thread.
4. **Go background for anything long.** `pro_batch(..., background=true)` or `pro_pipeline(..., background=true)`
   returns a `job_id` at once; poll `pro_server(action="job", job_id=...)`. Use it for: creating geodatabases,
   maintenance, replicas, training models, big edits, exports, clones. The client's tool timeout is about a
   minute; background jobs are not bound by it.
5. **Prune the schema.** `ARCGIS_SALAH_TOOLSETS=core,portal` keeps only the groups a session needs
   (`pro_server action=toolsets` shows counts).

## Decomposing a request

- Write the plan as stages that depend on each other; inside a stage, batch the independent calls.
- Reads first (context, describe, search) in one batch; then writes; then verification in one batch.
- Ask the user once, up front, for everything a write needs (confirmations, credentials env names).
