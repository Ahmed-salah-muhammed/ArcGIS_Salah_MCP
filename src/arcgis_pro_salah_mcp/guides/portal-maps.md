# Web maps and web scenes

`portal_map` writes web-map JSON directly (`Folder.add(text=...)`): fast (no `arcgis.map` import, ~11 s cold),
explicit and version-proof.

- `create`: `title`, `layers` (item ids and/or layer URLs; a feature service adds all its layers),
  `basemap` (omit = the org's default basemap; or a title / alias from the org gallery: topo-vector,
  streets-vector, satellite, hybrid, gray-vector, dark-gray-vector, oceans, osm), `popups` (auto popups from
  the fields, system fields hidden), `renderer` (same specs as portal_layer_schema, applied in the map only),
  `definition_expression` (filter in the map only), `folder`.
- `describe`: basemap, layers (type, url, popup on/off, renderer type, filter).
- `add_layers`, `remove_layers` (by title / id / url / index), `popup` (`popup_fields`, `popup_title`),
  `renderer`, `filter`, `basemap`.
- `create_scene`: uses `arcgis.map.Scene` (heavier).

Styling the SERVICE (everyone sees it) = `portal_layer_schema action=renderer`; styling only THIS MAP =
`portal_map renderer=...`. Low-level fallback for anything else: `portal_item action=data`, edit the JSON,
`portal_item action=update data=...`.
