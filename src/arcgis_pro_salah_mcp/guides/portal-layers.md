# Hosted feature layers: data, schema, views

`target` = an item id (plus `layer` = index or name) or a `.../FeatureServer/N` URL.

## Read - `portal_layer_query`
- `describe`: fields (with domains), capabilities, counts, objectId/globalId fields, `is_hosted`, `is_view`,
  `viewDefinitionQuery`, `supportsAppend`, editor tracking.
- `query`: paged (`limit` <= 2000, `offset`, `next_offset`), `fields`, `order_by`, `return_geometry`.
  The library's default `return_all_records=True` would fetch EVERY row - this tool always pages.
- `count`, `stats` (`["sum:POP", "avg:INCOME"]` + `group_by`).
- Dates come back as ISO 8601 UTC; the service stores epoch milliseconds.

## Write - `portal_layer_edit`
- `edit`: `adds` / `updates` (each needs the object-id field, or the global id with `use_global_ids`) /
  `deletes` (ids, or `where`). Batches of `batch_size` (500); every result's `success` is checked; updates and
  deletes retry on 429/5xx, adds never (not idempotent). `dry_run=true` previews a before/after diff.
  Deletes need confirm. Limit: `ARCGIS_SALAH_MAX_EDITS` (5000) per call - use `append` for bulk loads.
- `calculate`: server-side, `expressions=[{"field": "STATUS", "value": "Done"}]` or
  `[{"field": "DENSITY", "sqlExpression": "POP / AREA"}]`; confirm (bulk).
- `append`: from an uploaded item (`source_item_id`, `upload_format` csv | shapefile | filegdb | geojson |
  excel), optional upsert on a matching field; needs `supportsAppend`; confirm.
- `truncate`: hosted only, deletes all rows, confirm. Export first.

## Schema - `portal_layer_schema`
- `add_field` (`[{"name","type":"TEXT|SHORT|LONG|BIGINT|DOUBLE|FLOAT|DATE|DATEONLY|GUID","length","alias"}]`),
  `delete_field` (confirm) - **hosted only, and never on a view: change the source layer**.
- `capabilities` (`"Query,Create,Update,Delete,Editing,Sync,Extract"`), `definition` (raw update_definition, confirm).
- `renderer`: Esri renderer JSON, or a spec read from the data: `{"type":"unique","field":"X"}`,
  `{"type":"class_breaks","field":"POP","classes":5}` (quantiles), `{"type":"simple","color":[0,112,255]}`.
- `labels`: `label_field` or an Arcade `label_expression`. Renderer and labels are merged into the current
  drawingInfo (update_definition replaces drawingInfo as a whole).
- `create_view`: `view_name`, `view_where` (filter), `view_fields` (visible fields), `updateable=false` for a
  read-only copy - the standard way to feed dashboards and public sharing without exposing the source.
- `overwrite`: replace all data keeping the item id (confirm).

Only HOSTED services support these admin operations (`typeKeywords` contains "Hosted Service").
