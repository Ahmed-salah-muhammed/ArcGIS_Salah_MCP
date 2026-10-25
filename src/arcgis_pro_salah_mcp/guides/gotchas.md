# Cross-cutting gotchas (ArcGIS API for Python)

- **Return values are inconsistent** (None / bool / dict / Job / Future). The tools re-read after every write;
  do the same in portal_python.
- **Asynchronous calls**: `Folder.add` returns a Job (`.result()`), `publish(future=True)`, GP jobs and `export`
  are asynchronous. Long ones: `pro_batch(background=true)`.
- **Paging**: item and user searches cap results; layer queries stop at `maxRecordCount`. Page explicitly.
- **Credits** (ArcGIS Online): analysis, geocoding, enrichment, notebooks and storage. Estimate first.
- **IDs over names**: titles, role names and group titles repeat. Resolve to ids; ambiguity is an error.
- **Online vs Enterprise**: `isPortal` false = Online. Some APIs exist on only one.
- **Hosted vs referenced services**: `manager` admin operations work only on hosted ones.
- **Views**: change the schema on the source layer; share the view.
- **Encoding**: UTF-8, `utf-8-sig` for Excel CSVs - Arabic must round-trip.
- **Large edits**: batches, `rollback_on_failure`, retry 429/5xx with backoff, never blindly retry creates.
- **Serialization**: tools return small dicts, never raw arcgis objects.
- **Non-admin accounts**: `gis.users.license_types` and `roles.all()` can return 403 (measured on a publisher
  account); tools fall back to known aliases and say so.
- **ArcPy and arcgis together**: arcpy only works reliably on the thread that imported it (PostgreSQL errors
  87395/87413 elsewhere); the server keeps arcpy on one thread and signs in with `GIS("pro")` after arcpy is
  imported there.
