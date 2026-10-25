# Content, folders and items

## Search (`portal_search`)
- Query syntax: `title:"Parcels" AND type:"Feature Service"`, `tags:roads`, `id:<itemid>`, `owner:me`.
- Exact type filter: `item_type="Feature Service"` (uses the search `filter`, so it matches exactly).
- Results are org-only on ArcGIS Online unless `outside_org=true`. Page with `start` / `next_start`.
- **Titles are not unique** - always continue with the item id.

## Items (`portal_item`)
| action | what it does |
|---|---|
| get | item + sharing + layers + is_hosted / is_view |
| data | the item's JSON (web map, dashboard, app) |
| update | `properties` (title, snippet, description, tags, ...), `data`, `thumbnail`; re-read and compared |
| move / copy | to a folder (`folder=None` = root) |
| protect / unprotect | delete protection |
| delete | confirm=True; refuses protected items; reports items that use it |
| dependencies | what it uses / what uses it |
| export | `export_format` = File Geodatabase / Shapefile / CSV / GeoJson / Excel / KML ...; downloads, then deletes the temporary export item unless `keep_export` |
| download | file items |
| reassign | to another owner (admin, confirm) |
| usage | 30-day usage |

## Publishing (`portal_publish`)
Accepts a CSV / Excel / GeoJSON / GeoPackage / zip / shapefile (.shp is zipped with its sidecars) / a `.gdb`
folder (zipped) / a feature class inside a .gdb or .sde (exported to a zipped file gdb on the arcpy thread).
CSV/Excel go through `gis.content.analyze` for `publishParameters`; pass `latitude_field`+`longitude_field`, or
`address_fields` (geocoding: 40 credits per 1,000 rows on ArcGIS Online, confirm required).
Upload uses `Folder.add(ItemProperties(...), file=...)`, which returns a Job (`.result()`).

## Deprecated - never write new code with these (removed in arcgis 3.0)
| Old | New |
|---|---|
| `gis.content.add()` | `gis.content.folders.get(name).add(ItemProperties(...), file=...).result()` |
| `item.share()` / `unshare()` | `item.sharing.sharing_level = "PRIVATE"/"ORGANIZATION"/"EVERYONE"`, `item.sharing.groups.add(g)` |
| `gis.content.create_folder()` | `gis.content.folders.create(name, exist_ok=True)` |
| `arcgis.mapping.WebMap` | `arcgis.map.Map` (package arcgis-mapping) |
