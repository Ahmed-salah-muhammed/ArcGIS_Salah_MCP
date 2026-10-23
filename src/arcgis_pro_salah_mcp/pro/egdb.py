"""Enterprise geodatabase toolset: create -> users -> versioning -> archiving -> maintenance -> replication.

Every function wraps the matching ArcPy tool and returns the standard ``ok``/``err`` envelope.

Passwords are NEVER passed as plain arguments. Each function takes the *name* of an environment
variable (e.g. ``password_env="EGDB_SDE_PWD"``) and reads the secret from it, so secrets never
appear in the chat transcript or in tool-call logs.
"""
from __future__ import annotations

import os
from typing import Any

from .._result import err, guard, ok
from .ops import _arcpy


def _secret(env_name: str | None) -> str | None:
    if not env_name:
        return None
    val = os.environ.get(env_name)
    if val is None:
        raise RuntimeError(f"Environment variable '{env_name}' is not set (it should hold the password).")
    return val


def _outputs(result: Any) -> list:
    try:
        return [result[i] for i in range(result.outputCount)]
    except Exception:
        return [str(result)]


def _messages(result: Any) -> str:
    try:
        return result.getMessages()
    except Exception:
        return ""


# ------------------------------------------------------------------ create / connect
@guard
def create_gdb(database_platform: str, instance: str, database_name: str, authorization_file: str,
               dba_user: str, dba_password_env: str, gdb_admin_password_env: str,
               gdb_admin_name: str = "sde", account_authentication: str = "DATABASE_AUTH",
               schema_type: str = "SDE_SCHEMA", tablespace: str | None = None,
               spatial_type: str | None = None) -> dict:
    """Create Enterprise Geodatabase (self-managed Oracle / PostgreSQL / SQL Server)."""
    arcpy = _arcpy()
    kw = dict(database_platform=database_platform, instance_name=instance, database_name=database_name,
              account_authentication=account_authentication, database_admin=dba_user,
              database_admin_password=_secret(dba_password_env), sde_schema=schema_type,
              gdb_admin_name=gdb_admin_name, gdb_admin_password=_secret(gdb_admin_password_env),
              tablespace_name=tablespace, authorization_file=authorization_file)
    if spatial_type:
        kw["spatial_type"] = spatial_type
    r = arcpy.management.CreateEnterpriseGeodatabase(**{k: v for k, v in kw.items() if v is not None})
    return ok({"database": database_name, "messages": _messages(r)})


@guard
def enable_gdb(admin_connection: str, authorization_file: str) -> dict:
    """Enable Enterprise Geodatabase on an existing database (Db2, SAP HANA, cloud DBaaS, ...)."""
    r = _arcpy().management.EnableEnterpriseGeodatabase(admin_connection, authorization_file)
    return ok({"connection": admin_connection, "messages": _messages(r)})


@guard
def create_connection(out_folder: str, out_name: str, database_platform: str, instance: str,
                      user: str | None = None, password_env: str | None = None, database: str | None = None,
                      account_authentication: str = "DATABASE_AUTH", version: str | None = None,
                      version_type: str | None = None) -> dict:
    """Create a .sde connection file (optionally pinned to a version)."""
    arcpy = _arcpy()
    kw = dict(out_folder_path=out_folder, out_name=out_name, database_platform=database_platform,
              instance=instance, account_authentication=account_authentication, username=user,
              password=_secret(password_env), save_user_pass="SAVE_USERNAME", database=database,
              version_type=version_type, version=version)
    r = arcpy.management.CreateDatabaseConnection(**{k: v for k, v in kw.items() if v is not None})
    return ok({"connection": _outputs(r)[0]})


@guard
def describe(connection: str) -> dict:
    """Workspace facts: type, DBMS, release, current user/version, upgrade status."""
    arcpy = _arcpy()
    d = arcpy.Describe(connection)
    cp = d.connectionProperties
    info = {"workspaceType": d.workspaceType, "release": getattr(d, "release", None),
            "currentRelease": getattr(d, "currentRelease", None),
            "user": getattr(cp, "user", None), "database": getattr(cp, "database", None),
            "instance": getattr(cp, "instance", None), "version": getattr(cp, "version", None),
            "branch_name": getattr(cp, "branch_name", None)}
    arcpy.env.workspace = connection
    info["datasets"] = arcpy.ListDatasets() or []
    info["feature_classes"] = arcpy.ListFeatureClasses() or []
    info["tables"] = arcpy.ListTables() or []
    return ok(info)


# ------------------------------------------------------------------ users, roles, privileges
@guard
def create_user(admin_connection: str, user_name: str, password_env: str | None = None,
                role: str | None = None, tablespace: str | None = None,
                os_authenticated: bool = False) -> dict:
    """Create Database User (can own data). Optionally add it to an existing role."""
    arcpy = _arcpy()
    r = arcpy.management.CreateDatabaseUser(
        admin_connection, "OPERATING_SYSTEM_USER" if os_authenticated else "DATABASE_USER",
        user_name, None if os_authenticated else _secret(password_env), role, tablespace)
    return ok({"user": user_name, "messages": _messages(r)})


@guard
def create_role(admin_connection: str, role: str, users: list[str] | None = None,
                grant: bool = True) -> dict:
    """Create Role and grant (or revoke) it to users."""
    r = _arcpy().management.CreateRole(admin_connection, role, "GRANT" if grant else "REVOKE",
                                       ",".join(users or []) or None)
    return ok({"role": role, "users": users or [], "messages": _messages(r)})


@guard
def change_privileges(datasets: list[str], user_or_role: str, view: str = "GRANT",
                      edit: str | None = None) -> dict:
    """Change Privileges. view/edit: GRANT | REVOKE | AS_IS (edit=None -> AS_IS)."""
    arcpy = _arcpy()
    done = []
    for ds in datasets:
        arcpy.management.ChangePrivileges(ds, user_or_role, view, edit or "AS_IS")
        done.append(ds)
    return ok({"user_or_role": user_or_role, "view": view, "edit": edit or "AS_IS", "datasets": done})


# ------------------------------------------------------------------ data preparation
@guard
def prepare_dataset(dataset: str, global_ids: bool = True, editor_tracking: bool = False,
                    attachments: bool = False, register: bool = False) -> dict:
    """Prepare a dataset for multiuser work: register with gdb, GlobalIDs, editor tracking, attachments."""
    arcpy = _arcpy()
    steps = []
    if register:
        arcpy.management.RegisterWithGeodatabase(dataset); steps.append("RegisterWithGeodatabase")
    fields = {f.type for f in arcpy.ListFields(dataset)}
    if global_ids and "GlobalID" not in fields:
        arcpy.management.AddGlobalIDs(dataset); steps.append("AddGlobalIDs")
    if editor_tracking:
        arcpy.management.EnableEditorTracking(dataset, "created_user", "created_date", "last_edited_user",
                                              "last_edited_date", "ADD_FIELDS", "UTC")
        steps.append("EnableEditorTracking")
    if attachments:
        arcpy.management.EnableAttachments(dataset); steps.append("EnableAttachments")
    return ok({"dataset": dataset, "steps": steps})


# ------------------------------------------------------------------ versioning
@guard
def register_versioned(dataset: str, move_edits_to_base: bool = False) -> dict:
    """Register As Versioned. Over a direct connection = traditional; over a feature-service
    (service-based) data store with branch registration = branch."""
    arcpy = _arcpy()
    arcpy.management.RegisterAsVersioned(dataset, "EDITS_TO_BASE" if move_edits_to_base else "NO_EDITS_TO_BASE")
    d = arcpy.Describe(dataset)
    return ok({"dataset": dataset, "isVersioned": getattr(d, "isVersioned", None),
               "move_edits_to_base": move_edits_to_base})


@guard
def unregister_versioned(dataset: str, compress_default: bool = True) -> dict:
    """Unregister As Versioned (DESTRUCTIVE for pending version edits — confirm with the user first)."""
    _arcpy().management.UnregisterAsVersioned(dataset, "KEEP_EDIT",
                                               "COMPRESS_DEFAULT" if compress_default else "NO_COMPRESS_DEFAULT")
    return ok({"dataset": dataset})


@guard
def create_version(connection: str, name: str, parent: str = "sde.DEFAULT", access: str = "PUBLIC") -> dict:
    """Create Version. access: PUBLIC | PROTECTED | PRIVATE."""
    _arcpy().management.CreateVersion(connection, parent, name, access.upper())
    return ok({"version": name, "parent": parent, "access": access.upper()})


@guard
def alter_version(connection: str, version: str, new_name: str | None = None, description: str | None = None,
                  access: str | None = None) -> dict:
    """Alter Version (rename, description, access). Use access='PROTECTED' to protect DEFAULT."""
    _arcpy().management.AlterVersion(connection, version, new_name, description, access.upper() if access else None)
    return ok({"version": new_name or version, "access": access})


@guard
def delete_version(connection: str, version: str) -> dict:
    """Delete Version (DESTRUCTIVE — confirm with the user first)."""
    _arcpy().management.DeleteVersion(connection, version)
    return ok({"deleted": version})


@guard
def list_versions(connection: str) -> dict:
    """List versions with access, parent, children and dates."""
    arcpy = _arcpy()
    rows = []
    for v in arcpy.da.ListVersions(connection):
        rows.append({"name": v.name, "access": v.access, "parent": v.parentVersionName or None, "children": list(v.children or []),
                     "isOwner": v.isOwner, "created": str(v.created), "modified": str(v.lastModified),
                     "description": v.description})
    return ok({"count": len(rows), "versions": rows})


@guard
def reconcile_post(connection: str, edit_versions: list[str] | None = None, target_version: str = "sde.DEFAULT",
                   post: bool = True, conflict_definition: str = "BY_OBJECT",
                   conflict_resolution: str = "FAVOR_TARGET_VERSION", delete_versions: bool = False,
                   log_file: str | None = None, all_versions: bool = False) -> dict:
    """Reconcile Versions (+ optional post). Returns the log text so conflicts can be reported."""
    arcpy = _arcpy()
    r = arcpy.management.ReconcileVersions(
        connection, "ALL_VERSIONS" if all_versions or not edit_versions else "BLOCKING_VERSIONS",
        target_version, edit_versions or None, "LOCK_ACQUIRED", "NO_ABORT", conflict_definition,
        conflict_resolution, "POST" if post else "NO_POST", "DELETE_VERSION" if delete_versions else "KEEP_VERSION",
        log_file)
    log = ""
    if log_file and os.path.exists(log_file):
        with open(log_file, encoding="utf-8", errors="ignore") as fh:
            log = fh.read()[-6000:]
    return ok({"target": target_version, "edit_versions": edit_versions or "ALL", "posted": post,
               "log": log, "messages": _messages(r)})


# ------------------------------------------------------------------ archiving
@guard
def enable_archiving(dataset: str, enable: bool = True) -> dict:
    """Enable (or disable) geodatabase archiving on a dataset."""
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
    users = [{"id": u.ID, "name": u.Name, "client": u.ClientName, "connected": str(u.ConnectionTime),
              "direct": u.IsDirectConnection} for u in _arcpy().ListUsers(admin_connection)]
    return ok({"count": len(users), "users": users})


@guard
def maintenance(admin_connection: str, compress: bool = True, analyze: bool = True,
                rebuild_indexes: bool = True, datasets: list[str] | None = None) -> dict:
    """Compress (traditional) + Analyze Datasets + Rebuild Indexes, incl. system tables."""
    arcpy = _arcpy()
    done = []
    if compress:
        arcpy.management.Compress(admin_connection); done.append("Compress")
    if analyze:
        arcpy.management.AnalyzeDatasets(admin_connection, "SYSTEM", datasets, "ANALYZE_BASE",
                                         "ANALYZE_DELTA", "ANALYZE_ARCHIVE"); done.append("AnalyzeDatasets")
    if rebuild_indexes:
        arcpy.management.RebuildIndexes(admin_connection, "SYSTEM", datasets, "ALL"); done.append("RebuildIndexes")
    return ok({"steps": done})


@guard
def accept_connections(admin_connection: str, accept: bool) -> dict:
    """Allow or block new connections (before upgrades/maintenance)."""
    _arcpy().AcceptConnections(admin_connection, accept)
    return ok({"accept_connections": accept})


# ------------------------------------------------------------------ replication
@guard
def create_replica(datasets: list[str], child_workspace: str, replica_name: str,
                   replica_type: str = "ONE_WAY_REPLICA", access_type: str = "FULL",
                   initial_data_sender: str = "PARENT_DATA_SENDER", expand: str = "USE_DEFAULTS",
                   reuse_schema: str = "DO_NOT_REUSE", get_related: str = "GET_RELATED",
                   geometry: str | None = None, archiving: str = "DO_NOT_USE_ARCHIVING") -> dict:
    """Create Replica. replica_type: CHECK_OUT | ONE_WAY_REPLICA | TWO_WAY_REPLICA | ONE_WAY_CHILD_TO_PARENT_REPLICA.
    TWO_WAY needs an enterprise child; a file-gdb child supports CHECK_OUT / ONE_WAY only."""
    arcpy = _arcpy()
    child_type = arcpy.Describe(child_workspace).workspaceType
    if replica_type.upper().startswith("TWO_WAY") and child_type != "RemoteDatabase":
        return err("Two-way replicas need an enterprise child geodatabase; use ONE_WAY_REPLICA or CHECK_OUT "
                   "for a file geodatabase.", child_workspace_type=child_type)
    r = arcpy.management.CreateReplica(datasets, replica_type.upper(), child_workspace, replica_name, access_type,
                                       initial_data_sender, expand, reuse_schema, get_related, geometry, archiving)
    return ok({"replica": replica_name, "type": replica_type.upper(), "child": child_workspace,
               "messages": _messages(r)})


@guard
def list_replicas(connection: str) -> dict:
    """List replicas (name, type, role, datasets, last send/receive)."""
    rows = []
    for rp in _arcpy().da.ListReplicas(connection):
        rows.append({"name": rp.name, "type": rp.type, "role": rp.role, "owner": rp.owner,
                     "version": rp.version, "datasets": [d.name for d in rp.datasets],
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
def unregister_replica(geodatabase: str, replica_name: str) -> dict:
    """Unregister Replica (DESTRUCTIVE — confirm with the user first)."""
    _arcpy().management.UnregisterReplica(geodatabase, replica_name)
    return ok({"unregistered": replica_name})
