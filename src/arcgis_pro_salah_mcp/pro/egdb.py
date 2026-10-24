"""Enterprise geodatabase toolset: create -> users -> versioning -> archiving -> maintenance -> replication.

Every function wraps the matching ArcPy tool and returns the standard ``ok``/``err`` envelope.

Passwords are NEVER passed as plain arguments. Each function takes the *name* of an environment
variable (e.g. ``password_env="EGDB_SDE_PWD"``) and reads the secret from it, so secrets never
appear in the chat transcript or in tool-call logs.

Verified end to end on ArcGIS Pro 3.7 against SQL Server Express (a throwaway geodatabase created by
``create_gdb`` itself) - see CLAUDE.md "Enterprise geodatabase". Things that testing changed here:

* ``Version.children`` holds Version OBJECTS and ``Replica.datasets`` holds STRINGS (the docs do not
  say), so both are normalised before they reach the JSON transport.
* Reconcile mode ``BLOCKING_VERSIONS`` only reconciles versions that block a compress; given an edit
  version it reconciled NOTHING ("The specified target version has no edit versions to reconcile
  with") while the old wrapper still answered ``posted: true``. Listed edit versions now use
  ``ALL_VERSIONS`` and the log is always captured and checked.
* The DEFAULT version is ``dbo.DEFAULT`` in a DBO-schema SQL Server geodatabase, so it is looked up
  instead of assuming ``sde.DEFAULT``.
* ReconcileVersions matches version names CASE-SENSITIVELY and silently drops the rest: with
  ``dbo.c`` for the stored ``DBO.c`` it reconciled nothing; with the stored names it reconciled and
  posted (the parent gained the edited row). Names are therefore resolved against ListVersions.
* On SQL Server, Enable Enterprise Geodatabase must switch on READ_COMMITTED_SNAPSHOT, which needs
  the database to itself; with it already ON (set by a DBA) enabling succeeded.

Arguments are validated BEFORE ArcPy is imported (a bad call fails in microseconds, and the checks
are unit-tested without ArcGIS). Irreversible operations need ``confirm=True``, the same
``confirm_required`` rail the live bridge uses.
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Any

from .._result import err, guard, ok
from . import register as _register
from .ops import _arcpy

# Script keywords (the tool dialogs show friendlier labels). Read from Esri's tool reference.
_PLATFORMS = {"SQL_SERVER": "SQL_Server", "SQLSERVER": "SQL_Server", "POSTGRESQL": "PostgreSQL",
              "ORACLE": "Oracle"}
_CONNECTION_PLATFORMS = {"SQL_SERVER", "POSTGRESQL", "ORACLE", "DB2", "SAP HANA", "BIGQUERY", "DAMENG",
                         "REDSHIFT", "SNOWFLAKE", "TERADATA"}
_AUTH = {"DATABASE_AUTH", "OPERATING_SYSTEM_AUTH"}
_VERSION_TYPES = {"TRANSACTIONAL", "HISTORICAL", "POINT_IN_TIME", "BRANCH"}
_ACCESS = {"PUBLIC", "PROTECTED", "PRIVATE"}
_PRIV = {"GRANT", "REVOKE", "AS_IS"}
_REPLICA_TYPES = {"CHECK_OUT", "ONE_WAY_REPLICA", "TWO_WAY_REPLICA", "ONE_WAY_CHILD_TO_PARENT_REPLICA"}
# "For two-way and one-way child-to-parent replicas, the child geodatabase must be an enterprise
# geodatabase" and "for two-way and both types of one-way replicas, all datasets must have a GlobalID
# column" - Create Replica tool reference.
_ENTERPRISE_CHILD = {"TWO_WAY_REPLICA", "ONE_WAY_CHILD_TO_PARENT_REPLICA"}
_NEEDS_GLOBALID = {"TWO_WAY_REPLICA", "ONE_WAY_REPLICA", "ONE_WAY_CHILD_TO_PARENT_REPLICA"}
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIST_CAP = 200
# Each user brings their OWN ArcGIS Server / Enterprise keycodes file: pass authorization_file, or
# set this variable in the MCP server's environment. Nothing license-related ships with the code.
AUTHORIZATION_ENV = "ARCGIS_EGDB_AUTHORIZATION_FILE"
_TABLE_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}
# The elevated role the sde login needs while it creates or upgrades a geodatabase in a PostgreSQL
# cloud service - "Privileges for geodatabases in PostgreSQL" (Esri). Revocable afterwards.
_PG_CLOUD_ROLE = {"RDS": "rds_superuser", "AURORA": "rds_superuser", "AZURE": "azure_pg_admin",
                  "CLOUD_SQL": "cloudsqlsuperuser"}
_SQLSERVER_CLOUD = {"AZURE_SQL_DATABASE", "AZURE_SQL_MANAGED_INSTANCE", "RDS", "CLOUD_SQL"}


def _confirm(what: str, confirm: bool) -> dict | None:
    if confirm:
        return None
    return err(f"{what} Ask the user first, then call again with confirm=True.", code="confirm_required")


def _secret(env_name: str | None, what: str) -> tuple[str | None, dict | None]:
    """(value, None), or (None, error envelope) when the variable is not set."""
    if not env_name:
        return None, None
    value = os.environ.get(env_name)
    if value is None:
        return None, err(f"Environment variable '{env_name}' ({what}) is not set in the MCP server's "
                         f"environment. Set it there; never paste the password into the chat.",
                         code="missing_secret")
    return value, None


def _one_of(value: str | None, allowed: set[str], label: str) -> tuple[str | None, dict | None]:
    if value is None:
        return None, None
    key = str(value).strip().upper()
    if key not in allowed:
        return None, err(f"{label} must be one of {sorted(allowed)}, got {value!r}.", code="bad_argument")
    return key, None


def _workspace(arcpy: Any, connection: str) -> dict | None:
    """None when ``connection`` opens as an enterprise geodatabase, else a plain-language error.

    Without this a dead server or a connection file without saved credentials surfaced as
    ``TypeError: <the path>`` or ``Method connectionProperties does not exist``.
    """
    if str(connection).lower().startswith(("http://", "https://")):
        return None  # a feature service with version management (branch versioning); Esri validates it
    if not os.path.exists(connection):
        return err(f"Connection file not found: {connection}", code="not_found")
    try:
        kind = arcpy.Describe(connection).workspaceType
    except Exception as exc:  # noqa: BLE001
        kind = f"error: {exc}"
    if kind != "RemoteDatabase":
        return err(f"Could not open {connection} as an enterprise geodatabase ({kind}). Check that the "
                   f"database server is running and that the connection file has valid, saved "
                   f"credentials.", code="connection_failed")
    return None


def _default_version(arcpy: Any, connection: str) -> str:
    """The DEFAULT version's real name: sde.DEFAULT, or dbo.DEFAULT in a DBO-schema geodatabase."""
    for v in _versions(arcpy, connection):
        if not v.parentVersionName:
            return v.name
    return "sde.DEFAULT"


def _versions(arcpy: Any, connection: str) -> list:
    """arcpy.da.ListVersions, with a readable error for a database that is not a geodatabase."""
    try:
        return list(arcpy.da.ListVersions(connection))
    except TypeError as exc:  # it raised TypeError(<the path>) on a plain, non-geodatabase database
        raise RuntimeError(f"Could not read the versions of {connection}. Is it a geodatabase (see "
                           f"pro_egdb_enable), and can this user read its version tables?") from exc


def _resolve_versions(arcpy: Any, connection: str, names: list[str]) -> tuple[list[str], list[str]]:
    """Map names to the stored spelling (case-insensitively); returns (resolved, unknown)."""
    stored = {v.name.lower(): v.name for v in _versions(arcpy, connection)}
    resolved = [stored[n.lower()] for n in names if n.lower() in stored]
    unknown = [n for n in names if n.lower() not in stored]
    return resolved, unknown


def _authorization(path: str | None) -> tuple[str | None, dict | None]:
    """The caller's keycodes file: the argument, else $ARCGIS_EGDB_AUTHORIZATION_FILE."""
    path = path or os.environ.get(AUTHORIZATION_ENV)
    if not path:
        return None, err(f"Pass authorization_file (your ArcGIS Server/Enterprise keycodes file) or set "
                         f"{AUTHORIZATION_ENV} in the MCP server's environment.", code="missing_authorization")
    if not os.path.isfile(path):
        return None, err(f"Authorization (keycodes) file not found: {path}", code="not_found")
    return path, None


def _dbms(arcpy: Any, connection: str) -> str:
    """sqlserver | postgresql | oracle | other, from the connection's instance string (sde:sqlserver:...)."""
    instance = str(getattr(arcpy.Describe(connection).connectionProperties, "instance", "")).lower()
    for name in ("sqlserver", "postgresql", "oracle"):
        if name in instance:
            return name
    return "other"


def _run_sql(arcpy: Any, connection: str, statements: list[str], shown: list[str] | None = None) -> dict:
    """Run statements in order through ArcSDESQLExecute, stopping at the first failure.

    ``shown`` is what the caller may see (passwords masked); the real statements never leave here.
    """
    shown = shown or statements
    runner = arcpy.ArcSDESQLExecute(connection)
    done = []
    for real, visible in zip(statements, shown):
        try:
            runner.execute(real)
        except Exception as exc:  # noqa: BLE001
            return err(f"SQL failed at: {visible} -> {exc}", code="sql_failed", executed=done)
        done.append(visible)
    return ok({"executed": done})


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_name(arcpy: Any, dataset: str) -> tuple[str | None, dict | None]:
    """owner.table for SQL, from the dataset's geodatabase name (db.owner.table or owner.table)."""
    parts = str(arcpy.Describe(dataset).name).split(".")
    parts = parts[-2:] if len(parts) >= 2 else parts
    if not all(_NAME.match(x) for x in parts):
        return None, err(f"Unsupported table name {'.'.join(parts)!r} for SQL.", code="bad_argument")
    return ".".join(parts), None


def _outputs(result: Any) -> list:
    try:
        return [result[i] for i in range(result.outputCount)]
    except Exception:  # noqa: BLE001
        return [str(result)]


def _messages(result: Any) -> str:
    try:
        return result.getMessages()
    except Exception:  # noqa: BLE001
        return ""


def _name(item: Any) -> str:
    return item if isinstance(item, str) else str(getattr(item, "name", item))


# ------------------------------------------------------------------ create / connect
@guard
def create_gdb(database_platform: str, instance: str, database_name: str, authorization_file: str | None = None,
               dba_user: str | None = None, dba_password_env: str | None = None,
               gdb_admin_password_env: str | None = None, gdb_admin_name: str = "sde",
               account_authentication: str = "DATABASE_AUTH", schema_type: str = "SDE_SCHEMA",
               tablespace: str | None = None, spatial_type: str | None = None) -> dict:
    """Create Enterprise Geodatabase (self-managed Oracle / PostgreSQL / SQL Server).

    Secrets are only demanded when the platform needs them: a DBA password with DATABASE_AUTH, a
    geodatabase-administrator password unless it is a SQL Server DBO_SCHEMA geodatabase.
    """
    platform = _PLATFORMS.get(str(database_platform).strip().upper().replace(" ", "_"))
    if platform is None:
        return err("database_platform must be SQL_SERVER, POSTGRESQL or ORACLE (cloud DBaaS: create the "
                   "database and sde user there, then use pro_egdb_enable).", code="bad_argument")
    auth, problem = _one_of(account_authentication, _AUTH, "account_authentication")
    if problem:
        return problem
    schema, problem = _one_of(schema_type, {"SDE_SCHEMA", "DBO_SCHEMA"}, "schema_type")
    if problem:
        return problem
    if schema == "DBO_SCHEMA" and platform != "SQL_Server":
        return err("DBO_SCHEMA exists only for SQL Server.", code="bad_argument")
    if spatial_type and (platform != "PostgreSQL" or spatial_type.upper() not in {"ST_GEOMETRY", "POSTGIS"}):
        return err("spatial_type is PostgreSQL-only: ST_GEOMETRY or POSTGIS.", code="bad_argument")
    authorization_file, problem = _authorization(authorization_file)
    if problem:
        return problem
    if auth == "DATABASE_AUTH" and not (dba_user and dba_password_env):
        return err("DATABASE_AUTH needs dba_user and dba_password_env.", code="bad_argument")
    dba_password, problem = _secret(dba_password_env if auth == "DATABASE_AUTH" else None, "DBA password")
    if problem:
        return problem
    needs_admin = not (platform == "SQL_Server" and schema == "DBO_SCHEMA")
    if needs_admin and not gdb_admin_password_env:
        return err(f"A {platform} {schema} geodatabase needs gdb_admin_password_env (the '{gdb_admin_name}' "
                   f"password).", code="bad_argument")
    admin_password, problem = _secret(gdb_admin_password_env if needs_admin else None,
                                      "geodatabase administrator password")
    if problem:
        return problem

    kw = dict(database_platform=platform, instance_name=instance, database_name=database_name,
              account_authentication=auth, database_admin=dba_user if auth == "DATABASE_AUTH" else None,
              database_admin_password=dba_password, sde_schema=schema,
              gdb_admin_name=gdb_admin_name if needs_admin else None, gdb_admin_password=admin_password,
              tablespace_name=tablespace, authorization_file=authorization_file,
              spatial_type=spatial_type.upper() if spatial_type else None)
    r = _arcpy().management.CreateEnterpriseGeodatabase(**{k: v for k, v in kw.items() if v is not None})
    return ok({"database": database_name, "platform": platform, "schema": schema, "messages": _messages(r)})


@guard
def enable_gdb(admin_connection: str, authorization_file: str | None = None) -> dict:
    """Enable Enterprise Geodatabase on an existing database (Db2, SAP HANA, cloud DBaaS, ...)."""
    authorization_file, problem = _authorization(authorization_file)
    if problem:
        return problem
    arcpy = _arcpy()
    if not os.path.exists(admin_connection):
        return err(f"Connection file not found: {admin_connection}", code="not_found")
    # On SQL Server enabling switches on READ_COMMITTED_SNAPSHOT, which needs the database to itself.
    # This process's own cached connection (e.g. from creating the .sde file) was enough to fail it:
    # "Active connections found ... Close all other connections to the database".
    arcpy.management.ClearWorkspaceCache()
    try:
        r = arcpy.management.EnableEnterpriseGeodatabase(admin_connection, authorization_file)
    except Exception as exc:  # noqa: BLE001
        if "READ_COMMITTED_SNAPSHOT" not in str(exc):
            raise
        database = getattr(arcpy.Describe(admin_connection).connectionProperties, "database", "<database>")
        return err("SQL Server needs READ_COMMITTED_SNAPSHOT on, and could not switch it on because other "
                   "connections are open (ArcGIS Pro's included). Have a DBA run, then call again: "
                   f"ALTER DATABASE [{database}] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;",
                   code="needs_read_committed_snapshot", detail=str(exc)[:500])
    return ok({"connection": admin_connection, "messages": _messages(r)})


@guard
def create_connection(out_folder: str, out_name: str, database_platform: str, instance: str,
                      user: str | None = None, password_env: str | None = None, database: str | None = None,
                      account_authentication: str = "DATABASE_AUTH", version: str | None = None,
                      version_type: str | None = None, save_credentials: bool = True, date: str | None = None,
                      register_in_project: bool = False) -> dict:
    """Create a .sde connection file (optionally pinned to a version or, for archived data, a moment).

    HISTORICAL + version=<historical marker> or POINT_IN_TIME + date="YYYY-MM-DD HH:MM:SS" opens the
    archive as it was. ``register_in_project`` also adds the file to the OPEN project's Databases.
    """
    platform, problem = _one_of(database_platform.replace("SQLSERVER", "SQL_SERVER"), _CONNECTION_PLATFORMS,
                                "database_platform")
    if problem:
        return problem
    auth, problem = _one_of(account_authentication, _AUTH, "account_authentication")
    if problem:
        return problem
    vtype, problem = _one_of(version_type, _VERSION_TYPES, "version_type")
    if problem:
        return problem
    if auth == "DATABASE_AUTH" and not user:
        return err("DATABASE_AUTH needs a user (and password_env).", code="bad_argument")
    if vtype == "POINT_IN_TIME" and not date:
        return err("POINT_IN_TIME needs date (e.g. '2026-10-24 04:00:00').", code="bad_argument")
    if date and vtype != "POINT_IN_TIME":
        return err("date is only used with version_type POINT_IN_TIME.", code="bad_argument")
    password, problem = _secret(password_env if auth == "DATABASE_AUTH" else None, "database password")
    if problem:
        return problem
    if not out_name.lower().endswith(".sde"):
        out_name += ".sde"
    kw = dict(out_folder_path=out_folder, out_name=out_name, database_platform=platform, instance=instance,
              account_authentication=auth, username=user if auth == "DATABASE_AUTH" else None,
              password=password, save_user_pass="SAVE_USERNAME" if save_credentials else "DO_NOT_SAVE_USERNAME",
              database=database, version_type=vtype, version=version, date=date)
    r = _arcpy().management.CreateDatabaseConnection(**{k: v for k, v in kw.items() if v is not None})
    out = {"connection": _outputs(r)[0]}
    if vtype == "BRANCH" and version:
        out["note"] = "A BRANCH connection always opens the default branch version; 'version' is ignored."
    if vtype == "POINT_IN_TIME":
        # Both verified on SQL Server (Pro 3.7): the same instant had to be given in UTC for a
        # non-versioned archive but in server-local time for a traditional-versioned one, and
        # GetCount returned the CURRENT row count through this connection while SearchCursor
        # returned the historical rows.
        out["note"] = ("date is read as UTC for non-versioned archives and as the database server's local "
                       "time for traditional-versioned archives. Read rows with a cursor: GetCount ignores "
                       "the historical moment and returns the current count.")
    return _register.attach(ok(out), out["connection"], register_in_project, kind="database")


@guard
def describe(connection: str) -> dict:
    """Workspace facts: type, DBMS, release, current user/version, and its contents (capped)."""
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    d = arcpy.Describe(connection)
    cp = d.connectionProperties
    info: dict[str, Any] = {
        "workspaceType": d.workspaceType, "release": getattr(d, "release", None),
        "currentRelease": getattr(d, "currentRelease", None), "user": getattr(cp, "user", None),
        "authentication": getattr(cp, "authentication_mode", None), "database": getattr(cp, "database", None),
        "instance": getattr(cp, "instance", None), "version": getattr(cp, "version", None),
        "branch_name": getattr(cp, "branch_name", None)}
    previous = arcpy.env.workspace
    try:
        arcpy.env.workspace = connection  # restored below: other tools rely on env.workspace
        datasets = arcpy.ListDatasets() or []
        fcs = list(arcpy.ListFeatureClasses() or [])
        for ds in datasets[:_LIST_CAP]:
            fcs += [f"{ds}/{fc}" for fc in arcpy.ListFeatureClasses(feature_dataset=ds) or []]
        tables = arcpy.ListTables() or []
    finally:
        arcpy.env.workspace = previous
    for key, items in (("datasets", datasets), ("feature_classes", fcs), ("tables", tables)):
        info[key] = items[:_LIST_CAP]
        if len(items) > _LIST_CAP:
            info[f"{key}_total"] = len(items)
    return ok(info)


# ------------------------------------------------------------------ users, roles, privileges
@guard
def create_user(admin_connection: str, user_name: str, password_env: str | None = None,
                role: str | None = None, tablespace: str | None = None,
                os_authenticated: bool = False) -> dict:
    """Create Database User (can own data). Optionally add it to an existing role."""
    if not os_authenticated and not _NAME.match(user_name or ""):
        return err("user_name must start with a letter or underscore and contain only letters, digits and "
                   "underscores.", code="bad_argument")
    if not os_authenticated and not password_env:
        return err("A database-authenticated user needs password_env.", code="bad_argument")
    password, problem = _secret(None if os_authenticated else password_env, f"password for {user_name}")
    if problem:
        return problem
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    r = arcpy.management.CreateDatabaseUser(
        admin_connection, "OPERATING_SYSTEM_USER" if os_authenticated else "DATABASE_USER",
        user_name, password, role, tablespace)
    return ok({"user": user_name, "role": role, "messages": _messages(r)})


@guard
def create_role(admin_connection: str, role: str, users: list[str] | None = None,
                grant: bool = True) -> dict:
    """Create Role and grant (or revoke) it to users."""
    if not _NAME.match(role or ""):
        return err("role must start with a letter or underscore and contain only letters, digits and "
                   "underscores.", code="bad_argument")
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    r = arcpy.management.CreateRole(admin_connection, role, "GRANT" if grant else "REVOKE",
                                    ",".join(users or []) or None)
    return ok({"role": role, "users": users or [], "granted": grant, "messages": _messages(r)})


@guard
def change_privileges(datasets: list[str], user_or_role: str, view: str = "GRANT",
                      edit: str | None = None) -> dict:
    """Change Privileges. view = SELECT; edit = INSERT+UPDATE+DELETE together. GRANT | REVOKE | AS_IS."""
    view_kw, problem = _one_of(view or "AS_IS", _PRIV, "view")
    if problem:
        return problem
    edit_kw, problem = _one_of(edit or "AS_IS", _PRIV, "edit")
    if problem:
        return problem
    if not datasets:
        return err("Give at least one dataset.", code="bad_argument")
    # One call: the tool takes a multivalue list, so there is no half-applied state to explain.
    _arcpy().management.ChangePrivileges(list(datasets), user_or_role, view_kw, edit_kw)
    return ok({"user_or_role": user_or_role, "view": view_kw, "edit": edit_kw, "datasets": list(datasets)})


# ------------------------------------------------------------------ data preparation
@guard
def prepare_dataset(dataset: str, global_ids: bool = True, editor_tracking: bool = False,
                    attachments: bool = False, register: bool = False) -> dict:
    """Prepare a dataset for multiuser work: register with gdb, GlobalIDs, editor tracking, attachments."""
    arcpy = _arcpy()
    steps = []
    if register:
        arcpy.management.RegisterWithGeodatabase(dataset)
        steps.append("RegisterWithGeodatabase")
    fields = {f.type for f in arcpy.ListFields(dataset)}
    if global_ids and "GlobalID" not in fields:
        arcpy.management.AddGlobalIDs(dataset)
        steps.append("AddGlobalIDs")
    if editor_tracking:
        arcpy.management.EnableEditorTracking(dataset, "created_user", "created_date", "last_edited_user",
                                              "last_edited_date", "ADD_FIELDS", "UTC")
        steps.append("EnableEditorTracking")
    if attachments:
        arcpy.management.EnableAttachments(dataset)
        steps.append("EnableAttachments")
    return ok({"dataset": dataset, "steps": steps})


# ------------------------------------------------------------------ versioning
@guard
def register_versioned(dataset: str, move_edits_to_base: bool = False) -> dict:
    """Register As Versioned. The connection decides the model: a TRANSACTIONAL connection registers
    traditional versioning, a BRANCH connection registers branch versioning."""
    arcpy = _arcpy()
    arcpy.management.RegisterAsVersioned(dataset, "EDITS_TO_BASE" if move_edits_to_base else "NO_EDITS_TO_BASE")
    d = arcpy.Describe(dataset)
    return ok({"dataset": dataset, "isVersioned": getattr(d, "isVersioned", None),
               "move_edits_to_base": move_edits_to_base})


@guard
def unregister_versioned(dataset: str, compress_default: bool = True, confirm: bool = False) -> dict:
    """Unregister As Versioned. Irreversible for the version history -> needs confirm=True."""
    blocked = _confirm(f"Unregistering {dataset} as versioned removes its versioning.", confirm)
    if blocked:
        return blocked
    _arcpy().management.UnregisterAsVersioned(dataset, "KEEP_EDIT",
                                               "COMPRESS_DEFAULT" if compress_default else "NO_COMPRESS_DEFAULT")
    return ok({"dataset": dataset})


@guard
def create_version(connection: str, name: str, parent: str | None = None, access: str = "PUBLIC") -> dict:
    """Create Version. parent defaults to the geodatabase's DEFAULT (sde.DEFAULT or dbo.DEFAULT)."""
    access_kw, problem = _one_of(access, _ACCESS, "access")
    if problem:
        return problem
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    parent = parent or _default_version(arcpy, connection)
    arcpy.management.CreateVersion(connection, parent, name, access_kw)
    return ok({"version": name, "parent": parent, "access": access_kw})


@guard
def alter_version(connection: str, version: str, new_name: str | None = None, description: str | None = None,
                  access: str | None = None) -> dict:
    """Alter Version (rename, description, access). Use access='PROTECTED' to protect DEFAULT."""
    access_kw, problem = _one_of(access, _ACCESS, "access")
    if problem:
        return problem
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    arcpy.management.AlterVersion(connection, version, new_name, description, access_kw)
    return ok({"version": new_name or version, "description": description, "access": access_kw})


@guard
def delete_version(connection: str, version: str, confirm: bool = False) -> dict:
    """Delete Version. Its unposted edits are lost -> needs confirm=True."""
    blocked = _confirm(f"Deleting version {version} discards its unposted edits.", confirm)
    if blocked:
        return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    arcpy.management.DeleteVersion(connection, version)
    return ok({"deleted": version})


@guard
def list_versions(connection: str) -> dict:
    """List versions with access, parent, children and dates."""
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    rows = []
    for v in _versions(arcpy, connection):
        rows.append({"name": v.name, "access": v.access, "parent": v.parentVersionName or None,
                     # Version objects, not names: they would break the JSON transport.
                     "children": [_name(c) for c in (v.children or [])],
                     "isOwner": v.isOwner, "created": str(v.created), "modified": str(v.lastModified),
                     "description": v.description})
    return ok({"count": len(rows), "versions": rows})


@guard
def reconcile_post(connection: str, edit_versions: list[str] | None = None, target_version: str | None = None,
                   post: bool = True, conflict_definition: str = "BY_OBJECT",
                   conflict_resolution: str = "FAVOR_TARGET_VERSION", delete_versions: bool = False,
                   log_file: str | None = None, blocking_only: bool = False, confirm: bool = False) -> dict:
    """Reconcile Versions (+ optional post). Returns the reconcile log so conflicts can be reported.

    edit_versions defaults to every version except the target. blocking_only=True switches to the
    compress-oriented BLOCKING_VERSIONS mode. delete_versions=True needs confirm=True.
    """
    if delete_versions:
        blocked = _confirm("delete_versions=True deletes the reconciled versions.", confirm)
        if blocked:
            return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    target = target_version or _default_version(arcpy, connection)
    # ReconcileVersions compares names case-sensitively and silently skips a mismatch.
    resolved, unknown = _resolve_versions(arcpy, connection, [target])
    if unknown:
        return err(f"Target version {target!r} does not exist.", code="unknown_version",
                   versions=[v.name for v in _versions(arcpy, connection)])
    target = resolved[0]
    if edit_versions:
        edit_versions, unknown = _resolve_versions(arcpy, connection, list(edit_versions))
        if unknown:
            return err(f"These versions do not exist: {unknown}.", code="unknown_version",
                       versions=[v.name for v in _versions(arcpy, connection)])
    else:
        edit_versions = [v.name for v in _versions(arcpy, connection) if v.name.lower() != target.lower()]
    if not edit_versions:
        return ok({"target": target, "edit_versions": [], "reconciled": False, "posted": False,
                   "log": "", "note": "There are no other versions to reconcile."})
    log_path = log_file or os.path.join(tempfile.gettempdir(), f"egdb_reconcile_{os.getpid()}.log")
    r = arcpy.management.ReconcileVersions(
        connection, "BLOCKING_VERSIONS" if blocking_only else "ALL_VERSIONS", target, edit_versions,
        "LOCK_ACQUIRED", "NO_ABORT", conflict_definition, conflict_resolution, "POST" if post else "NO_POST",
        "DELETE_VERSION" if delete_versions else "KEEP_VERSION", log_path)
    log = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="ignore") as fh:
            log = fh.read()[-6000:]
        if not log_file:
            os.remove(log_path)
    # The tool "succeeds" even when it reconciled nothing; the log is the only honest signal.
    skipped = "reconcile process was not performed" in log.lower()
    return ok({"target": target, "edit_versions": edit_versions, "reconciled": not skipped,
               "posted": post and not skipped, "conflicts": "conflict" in log.lower() and not skipped,
               "log": log, "messages": _messages(r)})


# ------------------------------------------------------------------ archiving
@guard
def enable_archiving(dataset: str, enable: bool = True) -> dict:
    """Enable (or disable, preserving the history table) geodatabase archiving on a dataset."""
    arcpy = _arcpy()
    if enable:
        arcpy.management.EnableArchiving(dataset)
    else:
        arcpy.management.DisableArchiving(dataset, "PRESERVE")
    return ok({"dataset": dataset, "archiving": enable})


# ------------------------------------------------------------------ administration / maintenance
@guard
def connected_users(admin_connection: str) -> dict:
    """List users currently connected to the geodatabase."""
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    users = [{"id": u.ID, "name": u.Name, "client": u.ClientName, "connected": str(u.ConnectionTime),
              "direct": u.IsDirectConnection} for u in arcpy.ListUsers(admin_connection)]
    return ok({"count": len(users), "users": users})


@guard
def maintenance(admin_connection: str, compress: bool = True, analyze: bool = True,
                rebuild_indexes: bool = True, datasets: list[str] | None = None) -> dict:
    """Compress (traditional) + Analyze Datasets + Rebuild Indexes, incl. system tables."""
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    done = []
    if compress:
        arcpy.management.Compress(admin_connection)
        done.append("Compress")
    if analyze:
        arcpy.management.AnalyzeDatasets(admin_connection, "SYSTEM", datasets, "ANALYZE_BASE",
                                         "ANALYZE_DELTA", "ANALYZE_ARCHIVE")
        done.append("AnalyzeDatasets")
    if rebuild_indexes:
        arcpy.management.RebuildIndexes(admin_connection, "SYSTEM", datasets, "ALL")
        done.append("RebuildIndexes")
    return ok({"steps": done})


@guard
def accept_connections(admin_connection: str, accept: bool, confirm: bool = False) -> dict:
    """Allow or block new connections (before upgrades/maintenance). Blocking needs confirm=True."""
    if not accept:
        blocked = _confirm("Blocking connections locks every non-admin user out until it is re-enabled.",
                           confirm)
        if blocked:
            return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    arcpy.AcceptConnections(admin_connection, accept)
    return ok({"accept_connections": accept})


# ------------------------------------------------------------------ replication
@guard
def create_replica(datasets: list[str], child_workspace: str, replica_name: str,
                   replica_type: str = "ONE_WAY_REPLICA", access_type: str = "FULL",
                   initial_data_sender: str = "PARENT_DATA_SENDER", expand: str = "USE_DEFAULTS",
                   reuse_schema: str = "DO_NOT_REUSE", get_related: str = "GET_RELATED",
                   geometry: str | None = None, archiving: str = "DO_NOT_USE_ARCHIVING") -> dict:
    """Create Replica. replica_type: CHECK_OUT | ONE_WAY_REPLICA | TWO_WAY_REPLICA | ONE_WAY_CHILD_TO_PARENT_REPLICA.

    Checked before the tool runs, with the reason in plain words: the child type (two-way and
    child-to-parent need an enterprise child) and that every dataset is versioned with GlobalIDs.
    """
    rtype, problem = _one_of(replica_type, _REPLICA_TYPES, "replica_type")
    if problem:
        return problem
    if not datasets:
        return err("Give at least one dataset.", code="bad_argument")
    arcpy = _arcpy()
    child_type = arcpy.Describe(child_workspace).workspaceType
    if rtype in _ENTERPRISE_CHILD and child_type != "RemoteDatabase":
        return err(f"{rtype} needs an enterprise child geodatabase; use ONE_WAY_REPLICA or CHECK_OUT for a "
                   f"file geodatabase.", code="bad_argument", child_workspace_type=child_type)
    not_ready = []
    for ds in datasets:
        d = arcpy.Describe(ds)
        if not getattr(d, "isVersioned", False) and archiving.upper() != "ARCHIVING":
            not_ready.append(f"{ds}: not registered as versioned")
        if rtype in _NEEDS_GLOBALID and not getattr(d, "hasGlobalID", False):
            not_ready.append(f"{ds}: has no GlobalID field (pro_egdb_prepare_dataset adds it)")
    if not_ready:
        return err("Datasets are not ready for replication: " + "; ".join(not_ready), code="not_ready")
    r = arcpy.management.CreateReplica(datasets, rtype, child_workspace, replica_name, access_type,
                                       initial_data_sender, expand, reuse_schema, get_related, geometry, archiving)
    return ok({"replica": replica_name, "type": rtype, "child": child_workspace, "messages": _messages(r)})


@guard
def list_replicas(connection: str) -> dict:
    """List replicas (name, type, role, datasets, last send/receive)."""
    rows = []
    for rp in _arcpy().da.ListReplicas(connection):
        rows.append({"name": rp.name, "type": rp.type, "role": rp.role, "owner": rp.owner,
                     "version": rp.version,
                     # strings on Pro 3.7 (verified); objects are tolerated too
                     "datasets": [_name(d) for d in (rp.datasets or [])],
                     "lastSend": str(rp.lastSend), "lastReceive": str(rp.lastReceive),
                     "hasConflicts": rp.hasConflicts, "isSender": rp.isSender})
    return ok({"count": len(rows), "replicas": rows})


@guard
def synchronize(geodatabase_1: str, replica_name: str, geodatabase_2: str,
                direction: str = "FROM_GEODATABASE1_TO_2", conflict_policy: str = "IN_FAVOR_OF_GDB1",
                conflict_definition: str = "BY_OBJECT", reconcile: str = "DO_NOT_RECONCILE") -> dict:
    """Synchronize Changes (connected). direction: BOTH_DIRECTIONS | FROM_GEODATABASE1_TO_2 | FROM_GEODATABASE2_TO_1."""
    r = _arcpy().management.SynchronizeChanges(geodatabase_1, replica_name, geodatabase_2, direction,
                                                conflict_policy, conflict_definition, reconcile)
    return ok({"replica": replica_name, "direction": direction, "messages": _messages(r)})


@guard
def export_changes(geodatabase: str, out_file: str, replica_name: str,
                   switch_to_receiver: str = "DO_NOT_SWITCH", include_unacknowledged: str = "UNACKNOWLEDGED",
                   include_new: str = "NEW_CHANGES") -> dict:
    """Disconnected sync, step 1: Export Data Change Message (.xml/.gdb)."""
    _arcpy().management.ExportDataChangeMessage(geodatabase, out_file, replica_name, switch_to_receiver,
                                                include_unacknowledged, include_new)
    return ok({"message_file": out_file})


@guard
def import_message(geodatabase: str, message_file: str, out_acknowledgement: str | None = None,
                   conflict_policy: str = "IN_FAVOR_OF_IMPORTED_CHANGES",
                   conflict_definition: str = "BY_OBJECT") -> dict:
    """Disconnected sync, step 2: Import Message (data changes or acknowledgment)."""
    _arcpy().management.ImportMessage(geodatabase, message_file, out_acknowledgement, conflict_policy,
                                      conflict_definition)
    return ok({"imported": message_file, "acknowledgement": out_acknowledgement})


@guard
def export_acknowledgement(geodatabase: str, out_file: str, replica_name: str) -> dict:
    """Disconnected sync, step 3: Export Acknowledgement Message."""
    _arcpy().management.ExportAcknowledgementMessage(geodatabase, out_file, replica_name)
    return ok({"acknowledgement_file": out_file})


@guard
def unregister_replica(geodatabase: str, replica_name: str, confirm: bool = False) -> dict:
    """Unregister Replica. The replicas can no longer synchronize -> needs confirm=True."""
    blocked = _confirm(f"Unregistering {replica_name} stops it from ever synchronizing again.", confirm)
    if blocked:
        return blocked
    _arcpy().management.UnregisterReplica(geodatabase, replica_name)
    return ok({"unregistered": replica_name})


# ------------------------------------------------------------------ upgrade / health / sessions
@guard
def upgrade_gdb(admin_connection: str, upgrade: bool = False, confirm: bool = False) -> dict:
    """Check (default) or run the geodatabase upgrade. Upgrading cannot be undone -> confirm=True.

    Back up the database first; connect as the geodatabase administrator.
    """
    if upgrade:
        blocked = _confirm("Upgrading a geodatabase cannot be undone; back the database up first.", confirm)
        if blocked:
            return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    before = arcpy.Describe(admin_connection)
    if getattr(before, "currentRelease", False):
        # UpgradeGDB does not report this, it FAILS: "ERROR 001046: The geodatabase is already at the
        # current release and cannot be upgraded" (seen on SQL Server and PostgreSQL).
        return ok({"upgraded": False, "up_to_date": True, "release": getattr(before, "release", None)})
    r = arcpy.management.UpgradeGDB(admin_connection, "PREREQUISITE_CHECK", "UPGRADE" if upgrade else "NO_UPGRADE")
    after = arcpy.Describe(admin_connection) if upgrade else before
    return ok({"upgraded": upgrade, "up_to_date": False, "release_before": getattr(before, "release", None),
               "release_after": getattr(after, "release", None),
               "current_release": getattr(after, "currentRelease", None), "messages": _messages(r)})


@guard
def disconnect_users(admin_connection: str, users: list[int] | None = None, all_users: bool = False,
                     confirm: bool = False) -> dict:
    """Disconnect sessions by ID (see pro_egdb_connected_users) or all of them. Needs confirm=True."""
    if not all_users and not users:
        return err("Give session IDs in users, or all_users=True.", code="bad_argument")
    if users and not all(isinstance(u, int) or str(u).isdigit() for u in users):
        return err("users are session IDs (integers) from pro_egdb_connected_users.", code="bad_argument")
    blocked = _confirm("Disconnecting sessions drops their unsaved edits.", confirm)
    if blocked:
        return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    target = "ALL" if all_users else [int(u) for u in users]
    arcpy.DisconnectUser(admin_connection, target)
    left = [{"id": u.ID, "name": u.Name} for u in arcpy.ListUsers(admin_connection)]
    return ok({"disconnected": target, "still_connected": left})


@guard
def version_health(connection: str, action: str = "diagnose", scope: str = "tables",
                   target_version: str | None = None, tables: list[str] | None = None,
                   confirm: bool = False) -> dict:
    """Diagnose (read-only) or repair the version tables or version metadata; returns the tool log."""
    action_kw, problem = _one_of(action, {"DIAGNOSE", "REPAIR"}, "action")
    if problem:
        return problem
    scope_kw, problem = _one_of(scope, {"TABLES", "METADATA"}, "scope")
    if problem:
        return problem
    if action_kw == "REPAIR":
        blocked = _confirm("Repair rewrites geodatabase version system data; back up first.", confirm)
        if blocked:
            return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    log = os.path.join(tempfile.gettempdir(), f"egdb_{action_kw.lower()}_{scope_kw.lower()}_{os.getpid()}.log")
    tool = getattr(arcpy.management, ("Diagnose" if action_kw == "DIAGNOSE" else "Repair")
                   + ("VersionTables" if scope_kw == "TABLES" else "VersionMetadata"))
    if scope_kw == "TABLES":
        r = tool(connection, log, target_version, tables)
    else:
        r = tool(connection, log)
    text = ""
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()[-6000:]
        os.remove(log)
    return ok({"action": action_kw.lower(), "scope": scope_kw.lower(), "log": text, "messages": _messages(r)})


# ------------------------------------------------------------------ users and privileges through SQL
@guard
def manage_principal(admin_connection: str, action: str, name: str, password_env: str | None = None,
                     confirm: bool = False) -> dict:
    """drop_user | drop_role | set_password - the account chores that have no geoprocessing tool.

    Never cascades: a user who still owns data, or a role still holding privileges, is refused by the
    database and the error is returned. Passwords come from an environment variable only.
    """
    action_kw, problem = _one_of(action, {"DROP_USER", "DROP_ROLE", "SET_PASSWORD"}, "action")
    if problem:
        return problem
    if not _NAME.match(name or ""):
        return err("name must start with a letter or underscore and contain only letters, digits and "
                   "underscores.", code="bad_argument")
    if name.lower() in {"sde", "dbo", "sa", "postgres", "sys", "system", "public"}:
        return err(f"Refusing to {action_kw.lower()} the built-in account {name!r}.", code="bad_argument")
    password = None
    if action_kw == "SET_PASSWORD":
        if not password_env:
            return err("set_password needs password_env.", code="bad_argument")
        password, problem = _secret(password_env, f"new password for {name}")
        if problem:
            return problem
    else:
        blocked = _confirm(f"{action_kw.lower()} {name} cannot be undone.", confirm)
        if blocked:
            return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    dbms = _dbms(arcpy, admin_connection)
    if dbms == "other":
        return err("manage_principal supports SQL Server, PostgreSQL and Oracle.", code="unsupported_dbms")
    if dbms == "sqlserver" and action_kw == "DROP_USER":
        # SQL Server refuses DROP LOGIN while the login has a session ("Could not drop login ... as the
        # user is currently logged in"), after the schema and user were already gone. Check first so a
        # refusal leaves nothing half-dropped.
        runner = arcpy.ArcSDESQLExecute(admin_connection)
        rows = runner.execute(f"SELECT session_id FROM sys.dm_exec_sessions WHERE login_name = '{name}'")
        ids = [int(r[0]) for r in rows] if isinstance(rows, list) else ([int(rows)] if rows not in (None, True) else [])
        if ids:
            # These are DATABASE sessions; pooled ODBC ones never appear in pro_egdb_connected_users
            # (arcpy.ListUsers), so disconnect_users cannot reach them. And ArcSDESQLExecute cannot end
            # them either: it wraps every statement in a transaction and SQL Server answers "KILL command
            # cannot be used inside user transactions" (tried four ways). A DBA runs the KILLs.
            return err(f"{name} has {len(ids)} open database session(s); nothing was dropped. A DBA can end them "
                       f"(SSMS or sqlcmd) with the statements in kill_sql, then call drop_user again.",
                       code="user_connected", sessions=ids, kill_sql=[f"KILL {i};" for i in ids])
    if dbms == "postgresql" and action_kw in {"DROP_USER", "DROP_ROLE"}:
        # Owned objects outside the role's own (empty) schema - DROP OWNED would delete them.
        owned = arcpy.ArcSDESQLExecute(admin_connection).execute(
            f"SELECT (SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
            f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE r.rolname = '{name}' AND n.nspname <> '{name}') "
            f"+ (SELECT count(*) FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner WHERE r.rolname = '{name}')")
        owned = owned[0][0] if isinstance(owned, list) else owned
        if owned not in (0, "0", None, True):
            return err(f"{name} still owns {owned} database object(s); move or delete that data first "
                       f"(nothing was dropped).", code="owns_data")
    if action_kw == "SET_PASSWORD":
        if dbms == "oracle" and '"' in password:
            return err('An Oracle password cannot contain a double quote.', code="bad_argument")
        def statement(secret_sql: str) -> str:
            return {"sqlserver": f"ALTER LOGIN [{name}] WITH PASSWORD = N{secret_sql}",
                    "postgresql": f"ALTER ROLE {name} WITH PASSWORD {secret_sql}",
                    "oracle": f"ALTER USER {name} IDENTIFIED BY {secret_sql}"}[dbms]

        literal = f'"{password}"' if dbms == "oracle" else _sql_literal(password)
        masked = '"********"' if dbms == "oracle" else "'********'"
        # Build the visible statement separately: replacing the raw password would miss its escaped
        # form (a quote is doubled inside the SQL literal) and leak it.
        result = _run_sql(arcpy, admin_connection, [statement(literal)], [statement(masked)])
    elif action_kw == "DROP_USER":
        # Create Database User makes a login, a user and a schema of the same name; drop all three
        # (a schema that still holds data makes the database refuse, which is the point).
        statements = {
            "sqlserver": [f"IF SCHEMA_ID('{name}') IS NOT NULL DROP SCHEMA [{name}]",
                          f"IF DATABASE_PRINCIPAL_ID('{name}') IS NOT NULL DROP USER [{name}]",
                          f"IF SUSER_ID('{name}') IS NOT NULL DROP LOGIN [{name}]"],
            "postgresql": [f"DROP SCHEMA IF EXISTS {name}", f"DROP OWNED BY {name}", f"DROP ROLE IF EXISTS {name}"],
            "oracle": [f"DROP USER {name}"],
        }[dbms]
        result = _run_sql(arcpy, admin_connection, statements)
    else:  # DROP_ROLE
        statements = []
        if dbms == "sqlserver":  # SQL Server refuses to drop a role that still has members
            members = arcpy.ArcSDESQLExecute(admin_connection).execute(
                "SELECT m.name FROM sys.database_role_members rm "
                "JOIN sys.database_principals r ON rm.role_principal_id = r.principal_id "
                f"JOIN sys.database_principals m ON rm.member_principal_id = m.principal_id WHERE r.name = '{name}'")
            rows = members if isinstance(members, list) else ([[members]] if members not in (None, True) else [])
            statements += [f"ALTER ROLE [{name}] DROP MEMBER [{row[0]}]" for row in rows if row and row[0]]
            statements.append(f"IF DATABASE_PRINCIPAL_ID('{name}') IS NOT NULL DROP ROLE [{name}]")
        elif dbms == "postgresql":
            statements += [f"DROP OWNED BY {name}", f"DROP ROLE IF EXISTS {name}"]
        else:
            statements.append(f"DROP ROLE {name}")
        result = _run_sql(arcpy, admin_connection, statements)
    if result.get("ok"):
        result["data"].update({"action": action_kw.lower(), "name": name, "dbms": dbms})
    return result


@guard
def table_privileges(connection: str, datasets: list[str], principal: str, grant: list[str] | None = None,
                     revoke: list[str] | None = None) -> dict:
    """Grant/revoke SELECT, INSERT, UPDATE, DELETE one by one on simple (non-versioned) tables.

    Esri: "Privileges on datasets in geodatabases must be granted or revoked using ArcGIS" - because a
    versioned, archived or attachment-enabled dataset is several tables and views. Those are refused
    here; use pro_egdb_change_privileges for them. Connect as the data owner.
    """
    grant = [g.upper() for g in grant or []]
    revoke = [r.upper() for r in revoke or []]
    bad = [x for x in grant + revoke if x not in _TABLE_PRIVILEGES]
    if bad or not (grant or revoke):
        return err(f"grant/revoke take {sorted(_TABLE_PRIVILEGES)}; got {bad or 'nothing'}.", code="bad_argument")
    if not _NAME.match(principal or ""):
        return err("principal must be a plain user or role name.", code="bad_argument")
    if not datasets:
        return err("Give at least one dataset.", code="bad_argument")
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    statements, refused = [], []
    for ds in datasets:
        d = arcpy.Describe(ds)
        why = [label for label, flag in (("versioned", getattr(d, "isVersioned", False)),
                                         ("archived", getattr(d, "isArchived", False)),
                                         ("has attachments", any("ATTACHREL" in str(r).upper() for r in
                                                                 getattr(d, "relationshipClassNames", []) or [])))
               if flag]
        if why:
            refused.append(f"{ds}: {', '.join(why)}")
            continue
        table, problem = _table_name(arcpy, ds)
        if problem:
            return problem
        if grant:
            statements.append(f"GRANT {', '.join(grant)} ON {table} TO {principal}")
        if revoke:
            statements.append(f"REVOKE {', '.join(revoke)} ON {table} FROM {principal}")
    if refused:
        return err("Use pro_egdb_change_privileges for these (Esri requires ArcGIS for datasets that are "
                   "several tables): " + "; ".join(refused), code="use_change_privileges")
    result = _run_sql(arcpy, connection, statements)
    if result.get("ok"):
        result["data"].update({"principal": principal, "granted": grant, "revoked": revoke})
    return result


# ------------------------------------------------------------------ cloud databases (DBaaS)
@guard
def prepare_cloud_database(platform: str, service: str, admin_connection: str | None = None,
                           sde_password_env: str | None = None, execute: bool = False,
                           confirm: bool = False) -> dict:
    """The database-side setup Esri requires before pro_egdb_enable in a cloud service.

    Returns the statements (password masked). With execute=True and confirm=True it runs them through
    ``admin_connection`` (the service's master/admin user, connected to the target database).
    PostgreSQL services: RDS, AURORA, AZURE, CLOUD_SQL. SQL Server services: AZURE_SQL_DATABASE,
    AZURE_SQL_MANAGED_INSTANCE, RDS, CLOUD_SQL.
    """
    plat = str(platform).strip().upper().replace(" ", "_")
    svc = str(service).strip().upper().replace(" ", "_")
    if plat == "POSTGRESQL":
        if svc not in _PG_CLOUD_ROLE:
            return err(f"PostgreSQL service must be one of {sorted(_PG_CLOUD_ROLE)}.", code="bad_argument")
    elif plat in {"SQL_SERVER", "SQLSERVER"}:
        plat = "SQL_SERVER"
        if svc not in _SQLSERVER_CLOUD:
            return err(f"SQL Server service must be one of {sorted(_SQLSERVER_CLOUD)}.", code="bad_argument")
    else:
        return err("platform must be POSTGRESQL or SQL_SERVER.", code="bad_argument")
    if execute and not (admin_connection and sde_password_env):
        return err("execute=True needs admin_connection and sde_password_env.", code="bad_argument")
    password, problem = _secret(sde_password_env, "sde password") if sde_password_env else ("<sde password>", None)
    if problem:
        return problem
    pw = _sql_literal(password)
    if plat == "POSTGRESQL":
        statements = [f"CREATE ROLE sde LOGIN PASSWORD {pw}",
                      f"GRANT {_PG_CLOUD_ROLE[svc]} TO sde",
                      "CREATE SCHEMA sde AUTHORIZATION sde"]
        after = [f"After the geodatabase is created/upgraded you may REVOKE {_PG_CLOUD_ROLE[svc]} FROM sde.",
                 "Grant sde every role that will own data (e.g. GRANT gisdata1 TO sde) before upgrades.",
                 "Login names must be lowercase."]
    else:
        login = ([f"CREATE USER sde WITH PASSWORD = N{pw}"] if svc == "AZURE_SQL_DATABASE" else
                 [f"CREATE LOGIN sde WITH PASSWORD = N{pw}", "CREATE USER sde FOR LOGIN sde"])
        statements = login + [
            "CREATE SCHEMA sde AUTHORIZATION sde",
            "ALTER USER sde WITH DEFAULT_SCHEMA = sde",
            "GRANT CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE FUNCTION TO sde",
            "GRANT CONTROL ON SCHEMA::sde TO sde",
        ]
        if svc == "AZURE_SQL_DATABASE":
            statements += ["GRANT VIEW DATABASE STATE TO sde", "GRANT KILL DATABASE CONNECTION TO sde"]
        elif svc in {"RDS", "CLOUD_SQL"}:
            statements += ["ALTER SERVER ROLE processadmin ADD MEMBER sde"]
        after = ["If READ_COMMITTED_SNAPSHOT and ALLOW_SNAPSHOT_ISOLATION are not ON, either grant sde ALTER "
                 "or have the admin switch them on before enabling."]
    shown = [x.replace(pw, "'********'") for x in statements]
    plan = {"platform": plat, "service": svc, "statements": shown, "after": after,
            "next": ["pro_egdb_create_connection as sde (password_env, save_credentials=True)",
                     "pro_egdb_enable with that connection and your keycodes file"],
            "source": "Esri: 'Create a geodatabase in a cloud-based database service' + 'Privileges for "
                      "geodatabases in PostgreSQL / SQL Server'. Not executed against a cloud service here."}
    if not execute:
        return ok({**plan, "executed": False})
    blocked = _confirm("This creates the sde login and grants it elevated rights in the cloud database.", confirm)
    if blocked:
        return blocked
    arcpy = _arcpy()
    problem = _workspace(arcpy, admin_connection)
    if problem:
        return problem
    result = _run_sql(arcpy, admin_connection, statements, shown)
    if result.get("ok"):
        result["data"] = {**plan, "executed": True}
    return result


# ------------------------------------------------------------------ views, keywords, replica schema
@guard
def create_view(connection: str, view_name: str, definition: str) -> dict:
    """Create Database View from a SQL SELECT (runs as the connected user, who owns the view)."""
    if not _NAME.match(view_name or ""):
        return err("view_name must start with a letter or underscore (letters, digits, underscores).",
                   code="bad_argument")
    if not str(definition).strip().lower().startswith("select"):
        return err("definition must be a SELECT statement.", code="bad_argument")
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    r = arcpy.management.CreateDatabaseView(connection, view_name, definition)
    return ok({"view": _outputs(r)[0], "messages": _messages(r)})


@guard
def config_keywords(connection: str, action: str, file: str) -> dict:
    """export | import the geodatabase configuration keywords (storage settings) to/from a text file."""
    action_kw, problem = _one_of(action, {"EXPORT", "IMPORT"}, "action")
    if problem:
        return problem
    if action_kw == "IMPORT" and not os.path.isfile(file):
        return err(f"Keyword file not found: {file}", code="not_found")
    arcpy = _arcpy()
    problem = _workspace(arcpy, connection)
    if problem:
        return problem
    if action_kw == "EXPORT":
        arcpy.management.ExportGeodatabaseConfigurationKeywords(connection, file)
    else:
        arcpy.management.ImportGeodatabaseConfigurationKeywords(connection, file)
    return ok({"action": action_kw.lower(), "file": file})


@guard
def replica_schema(geodatabase: str, action: str, file: str, replica_name: str | None = None,
                   out_file: str | None = None) -> dict:
    """export (needs replica_name) | compare (needs out_file for the changes) | import a replica's schema."""
    action_kw, problem = _one_of(action, {"EXPORT", "COMPARE", "IMPORT"}, "action")
    if problem:
        return problem
    arcpy = _arcpy()
    if action_kw == "EXPORT":
        if not replica_name:
            return err("export needs replica_name.", code="bad_argument")
        arcpy.management.ExportReplicaSchema(geodatabase, file, replica_name)
        return ok({"action": "export", "schema_file": file})
    if not os.path.isfile(file):
        return err(f"Schema file not found: {file}", code="not_found")
    if action_kw == "COMPARE":
        if not out_file:
            return err("compare needs out_file (the schema-changes file to write).", code="bad_argument")
        arcpy.management.CompareReplicaSchema(geodatabase, file, out_file)
        return ok({"action": "compare", "changes_file": out_file})
    arcpy.management.ImportReplicaSchema(geodatabase, file)
    return ok({"action": "import", "imported": file})
