# When no curated tool fits

Order of work - always:
1. A curated tool (portal_item, portal_layer_*, portal_users, ...) if one fits.
2. **Look the API up in the INSTALLED library**: `portal_api action=search query="editor tracking"`, then
   `portal_api action=doc symbol=<qualified_name>` (signature, docstring, deprecation + replacement).
   If you are not 100% sure of an arcgis API, read its doc before using it.
3. **Inspect in read mode**: `portal_python code="result = ..."` - `gis` is signed in; also `arcgis`, `pd`,
   `json`, `ItemProperties`, `ItemTypeEnum`, `FeatureLayer`, `FeatureLayerCollection`, `Table`, `GeoAccessor`,
   `gis_<name>` per connection, `Map`/`Scene` when the code uses them. Assign `result`; stdout is captured.
   `session_id` keeps variables between calls.
4. **Change in write mode** only after showing the user what will happen: `mode="write", confirm=true`.
   The full code goes to the audit log.

Read mode refuses code that calls add/update/delete/publish/share/create/edit_features/append/calculate/
truncate/*_definition/update_role/clone_items/... or assigns `sharing_level`/`privileges`/`title`... It is a
seatbelt, not a sandbox.

## Raw REST - `portal_rest`
`path` relative to `<portal>/sharing/rest/` (e.g. `portals/self`, `content/users/<me>`, `search?q=...`) or an
absolute URL on the portal / its services. GET is free; POST needs confirm unless it is a read operation
(query, search, generateRenderer, ...). The token is added by the session and never leaves the portal's hosts.

Example "10 largest hosted feature layers by storage": search `owner:me type:"Feature Service"` with
`portal_search`, then `portal_python` reading `item.size` for each and sorting.
