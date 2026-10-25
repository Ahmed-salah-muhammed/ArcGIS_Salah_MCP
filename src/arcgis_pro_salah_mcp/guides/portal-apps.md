# Apps: Survey123, Dashboards, StoryMaps, dependencies

`portal_apps`:
- `survey_info` / `survey_export` (`export_format` CSV | Shapefile | File Geodatabase | GeoJson | Excel) - the
  data lives in the survey's hosted layer; `SurveyManager(gis).get(form_id).download(...)`.
- `dashboard_clone` - clones a dashboard and repoints it with `item_mapping={old_layer_item: new_layer_item}`
  (`clone_items`); items it uses that are not mapped are cloned too. Confirm.
- `storymap_create` - `blocks=[{"text": "..."}, {"map": "<webmap id>", "caption": "..."}]`, saved private.
- `dependency_graph` - what an item requires / is required by (`create_dependency_graph`); run it before deleting
  or migrating anything.

Static dashboards / web apps without a portal item: `webapp_create(kind="dashboard"|"app")`.
Experience Builder (`arcgis.apps.expbuilder.WebExperience`), Workforce (`arcgis.apps.workforce`), Hub and
Tracker: use `portal_api` to read the installed API, then `portal_python`.
