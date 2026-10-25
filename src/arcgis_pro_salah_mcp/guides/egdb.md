# Enterprise geodatabase (pro_egdb_*)

Order of work: `pro_egdb_setup` (create / enable / prepare_cloud / upgrade) -> `pro_egdb_connection` (.sde
files, added to the open Pro project) -> `pro_egdb_principals` (users, roles, drop, set_password) ->
`pro_egdb_privileges` -> `pro_egdb_dataset` (register, GlobalIDs, editor tracking, attachments, versioning,
archiving) -> `pro_egdb_versions` (create, alter, reconcile/post, health) -> `pro_egdb_admin` (connected users,
disconnect, block connections, compress/analyze/rebuild, views, config keywords) -> `pro_egdb_replica`.

Rules:
- Keycodes are the user's OWN: `authorization_file` or env `ARCGIS_EGDB_AUTHORIZATION_FILE`. Never in code.
- Passwords are env-var NAMES (`dba_password_env`, `password_env`, `gdb_admin_password_env`).
- Irreversible operations need confirm=True (unregister versioned, delete version, unregister replica, drop
  user/role, disconnect users, block connections, upgrade, reconcile with delete_versions).
- Long operations (create, maintenance, replicas): `pro_batch(background=true)` and poll.

Verified facts (SQL Server Express + PostgreSQL 18, Pro 3.7):
- DEFAULT is `dbo.DEFAULT` in a DBO-schema SQL Server gdb; the tools look it up instead of assuming `sde.`.
- `ReconcileVersions` compares names case-sensitively and silently skips mismatches while reporting success;
  the tool resolves names via ListVersions and reads the LOG to decide `reconciled` / `posted` / `conflicts`.
- Enabling a SQL Server gdb needs READ_COMMITTED_SNAPSHOT; if blocked, the error returns the ALTER statement a
  DBA must run.
- POINT_IN_TIME connections: GetCount ignores the moment (use a cursor); the date is UTC for non-versioned
  archives and server-local time for traditional-versioned ones.
- SQL Server cannot drop a login with open sessions; pooled sessions are invisible to ListUsers, so
  `drop_user` returns the KILL statements instead.
- Privileges on versioned / archived / attachment datasets must go through ArcGIS (`view`/`edit` mode).
