# Analysis - credits first

ArcGIS Online credits (Esri credit table): batch or stored geocoding 40 credits / 1,000 addresses (0.04 each);
single unstored geocodes, reverse geocodes and suggestions are free; spatial analysis is billed per feature
processed and reports its cost with `estimate=True`. Enterprise with its own locators/servers spends nothing.

## Geocoding - `portal_geocode`
- `geocoders` lists the org's locators. `single` (free unless `for_storage`), `reverse` (`location=[lon, lat]`),
  `suggest`.
- `batch`: `addresses=[...]` or `csv_path` + `address_field` (+ `out_csv`). Credits are estimated first and the
  call refuses without confirm=True; above `ARCGIS_SALAH_CREDIT_LIMIT` (100) it refuses outright.
- Store results only with `for_storage=True` (Esri's terms).

## Spatial analysis - `portal_analysis`
`list` -> `describe function=...` -> `estimate` -> `run` (confirm, `params.output_name` required so the result
is saved as a hosted layer you own). Item ids and layer URLs in `params` become real layers.
Functions: create_buffers, aggregate_points, summarize_within, join_features, overlay_layers, find_hot_spots,
create_drive_time_areas, find_nearest, enrich_layer, ... (see `list`).

## GP services
`gp_describe gp_url=<GPServer url or item id>` lists the tasks and signatures (`import_toolbox`);
`gp_run function=... params={...}` runs one (confirm).
