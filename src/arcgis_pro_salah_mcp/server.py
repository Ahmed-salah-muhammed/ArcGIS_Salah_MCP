"""FastMCP server — wires the tool layers to any MCP client (Claude, Google Antigravity, …).

Speaks the standard MCP stdio transport, so any MCP-capable client can drive it.

Run via the installed entry point (recommended), NOT by passing this file to
python directly (package-relative imports would break):

    arcgis-pro-salah-mcp

Tool naming convention:
    pro_*     -> Layer 1,  ArcGIS Pro / ArcPy (desktop, .aprx & geodatabases)
    live_*    -> Layer 1b, the OPEN ArcGIS Pro session via the .NET bridge
    portal_*  -> Layer 2,  ArcGIS API for Python (ArcGIS Online / Enterprise)
    webapp_*  -> Layer 3,  ArcGIS Maps SDK for JavaScript app generator
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from ._result import ok as _ok, err as _err

from . import __version__, arcpy_thread, bootstrap
from .pro import ops as pro
from .pro import context as pro_context_mod
from .pro import pipeline as pro_pipeline_mod
from .pro import cache as pro_cache
from .pro import schema as pro_schema
from .pro import validate as pro_validate
from .pro import geoai as pro_geoai
from .pro import unet as pro_unet
from .pro import toolbox as pro_toolbox
from .pro import modelbuilder as pro_modelbuilder
from .pro import symbology as pro_symbology
from .pro import dl_notebooks as pro_dl
from .pro import egdb as pro_egdb
from .live import ops as live
from .portal import ops as portal
from .webapp import generator as webapp
from .webapp import dashboard as webapp_dashboard
from .webapp import github as webapp_github

INSTRUCTIONS = """\
Drive the full ArcGIS stack end to end. A typical workflow is:
  1. pro_*    — prepare/analyze data in ArcGIS Pro with ArcPy (buffer, clip, ...)
  2. portal_* — publish the result to ArcGIS Online/Portal and build a web map
  3. webapp_* — generate a static ArcGIS Maps SDK for JS app showing the layers
Every tool returns {"ok": true, "data": ...} or {"ok": false, "error": ...}.
Long calls (creating a geodatabase, maintenance, replicas, many versions/users) can outlive the
client's tool timeout: run them with pro_run_background and poll pro_job_status instead.
"CURRENT" as a project path means the project open in ArcGIS Pro (resolved through the live bridge);
ArcPy here runs outside Pro and cannot attach to it.
"""

mcp = FastMCP("ArcGIS_Pro_Salah_MCP", instructions=INSTRUCTIONS)


# =====================================================================
# Layer 1 — ArcGIS Pro / ArcPy
# =====================================================================


@mcp.tool()
def pro_ping() -> dict:
    """Check that ArcPy is reachable in the running interpreter."""
    return pro.ping()


@mcp.tool()
def pro_get_info() -> dict:
    """ArcGIS Pro / ArcPy version, build and license level."""
    return pro.get_info()


@mcp.tool()
def pro_project_info(aprx_path: str) -> dict:
    """Read an .aprx project: maps, layouts, default geodatabase, CRS."""
    return pro.project_info(aprx_path)


@mcp.tool()
def pro_save_project(aprx_path: str, copy_to: str | None = None) -> dict:
    """Save the project, optionally to a new path (saveACopy)."""
    return pro.save_project(aprx_path, copy_to)


@mcp.tool()
def pro_list_layers(aprx_path: str, map_name: str | None = None) -> dict:
    """List layers in a map (name, type, CRS, visibility)."""
    return pro.list_layers(aprx_path, map_name)


@mcp.tool()
def pro_add_layer(aprx_path: str, data_path: str, map_name: str | None = None) -> dict:
    """Add a vector or raster dataset to a map (addDataFromPath)."""
    return pro.add_layer(aprx_path, data_path, map_name)


@mcp.tool()
def pro_remove_layer(
    aprx_path: str, layer_name: str, map_name: str | None = None
) -> dict:
    """Remove a layer from a map."""
    return pro.remove_layer(aprx_path, layer_name, map_name)


@mcp.tool()
def pro_rename_layer(
    aprx_path: str, layer_name: str, new_name: str, map_name: str | None = None
) -> dict:
    """Rename a layer."""
    return pro.rename_layer(aprx_path, layer_name, new_name, map_name)


@mcp.tool()
def pro_set_visibility(
    aprx_path: str, layer_name: str, visible: bool, map_name: str | None = None
) -> dict:
    """Show or hide a layer."""
    return pro.set_visibility(aprx_path, layer_name, visible, map_name)


@mcp.tool()
def pro_layer_summary(dataset: str) -> dict:
    """Feature count, fields, geometry type and CRS for a dataset."""
    return pro.layer_summary(dataset)


@mcp.tool()
def pro_describe_layer(dataset: str, layer_name: str | None = None) -> dict:
    """Detect what a layer is from its fields/geometry; returns a smart item summary, description and tags."""
    return pro.describe_layer(dataset, layer_name)


@mcp.tool()
def pro_get_features(
    dataset: str, fields: list[str] | None = None, limit: int = 10
) -> dict:
    """Return attribute rows from a feature class/table (SearchCursor)."""
    return pro.get_features(dataset, fields, limit)


@mcp.tool()
def pro_select_by_expression(layer: str, where_clause: str) -> dict:
    """Select features matching a SQL where-clause (SelectLayerByAttribute)."""
    return pro.select_by_expression(layer, where_clause)


@mcp.tool()
def pro_add_field(dataset: str, field_name: str, field_type: str = "TEXT") -> dict:
    """Add an attribute field (TEXT/SHORT/LONG/DOUBLE/DATE)."""
    return pro.add_field(dataset, field_name, field_type)


@mcp.tool()
def pro_calculate_field(dataset: str, field: str, expression: str) -> dict:
    """Compute a field's values with a Python expression (CalculateField)."""
    return pro.calculate_field(dataset, field, expression)


@mcp.tool()
def pro_field_statistics(dataset: str, field_name: str) -> dict:
    """count/sum/mean/median/std/min/max for a numeric field."""
    return pro.field_statistics(dataset, field_name)


@mcp.tool()
def pro_buffer(dataset: str, out: str, distance: str) -> dict:
    """Buffer features by a distance, e.g. '100 Meters' (analysis.Buffer)."""
    return pro.buffer(dataset, out, distance)


@mcp.tool()
def pro_clip(dataset: str, mask: str, out: str) -> dict:
    """Clip a layer to the boundary of another (analysis.Clip)."""
    return pro.clip(dataset, mask, out)


@mcp.tool()
def pro_spatial_join(target: str, join: str, out: str) -> dict:
    """Join attributes by spatial intersection (analysis.SpatialJoin)."""
    return pro.spatial_join(target, join, out)


@mcp.tool()
def pro_dissolve(dataset: str, out: str, field: str | None = None) -> dict:
    """Merge features, optionally grouped by a field (management.Dissolve)."""
    return pro.dissolve(dataset, out, field)


@mcp.tool()
def pro_merge(datasets: list[str], out: str) -> dict:
    """Combine multiple layers of the same geometry type (management.Merge)."""
    return pro.merge(datasets, out)


@mcp.tool()
def pro_reproject(dataset: str, out: str, target_crs: str) -> dict:
    """Reproject to a CRS, e.g. 'EPSG:4326' (management.Project)."""
    return pro.reproject(dataset, out, target_crs)


@mcp.tool()
def pro_repair_geometry(dataset: str) -> dict:
    """Fix invalid geometries in place (management.RepairGeometry)."""
    return pro.repair_geometry(dataset)


@mcp.tool()
def pro_extract(dataset: str, where_clause: str, out: str) -> dict:
    """Extract matching features into a new dataset (analysis.Select)."""
    return pro.extract(dataset, where_clause, out)


@mcp.tool()
def pro_apply_categorized_symbology(
    aprx_path: str, layer_name: str, field_name: str, map_name: str | None = None
) -> dict:
    """Unique-value symbology: a distinct colour per field value."""
    return pro.apply_categorized_symbology(aprx_path, layer_name, field_name, map_name)


@mcp.tool()
def pro_apply_graduated_symbology(
    aprx_path: str,
    layer_name: str,
    field_name: str,
    classes: int = 5,
    color_ramp: str | None = None,
    map_name: str | None = None,
) -> dict:
    """Graduated (choropleth) symbology on a numeric field."""
    return pro.apply_graduated_symbology(
        aprx_path, layer_name, field_name, classes, color_ramp, map_name
    )


@mcp.tool()
def pro_set_opacity(
    aprx_path: str, layer_name: str, opacity: float, map_name: str | None = None
) -> dict:
    """Set layer opacity 0.0-1.0 (converted to ArcPy transparency 0-100)."""
    return pro.set_opacity(aprx_path, layer_name, opacity, map_name)


@mcp.tool()
def pro_export_layer(dataset: str, out_path: str) -> dict:
    """Export a layer/feature class to a file (format from extension)."""
    return pro.export_layer(dataset, out_path)


@mcp.tool()
def pro_export_image(
    aprx_path: str, layout_name: str, out_path: str, dpi: int = 150
) -> dict:
    """Export a layout to PNG/JPG."""
    return pro.export_image(aprx_path, layout_name, out_path, dpi)


@mcp.tool()
def pro_export_pdf(
    aprx_path: str, layout_name: str, out_path: str, dpi: int = 300
) -> dict:
    """Export a layout to PDF."""
    return pro.export_pdf(aprx_path, layout_name, out_path, dpi)


@mcp.tool()
def pro_export_map_series(aprx_path: str, layout_name: str, out_path: str) -> dict:
    """Export a layout's Map Series / map book to a multi-page PDF."""
    return pro.export_map_series(aprx_path, layout_name, out_path)


@mcp.tool()
def pro_run_gp(
    tool: str, args: list[Any] | None = None, kwargs: dict | None = None
) -> dict:
    """Run ANY geoprocessing tool by name, e.g. 'analysis.Buffer'."""
    return pro.run_gp(tool, args, kwargs)


@mcp.tool()
def pro_execute_code(code: str) -> dict:
    """Escape hatch: run arbitrary ArcPy Python. `arcpy` is pre-imported."""
    return pro.execute_code(code)



# =====================================================================
# Enterprise geodatabase (pro_egdb_*): create -> users -> versioning -> archiving -> maintenance -> replication
# Passwords are passed as ENV VAR NAMES (e.g. "EGDB_SDE_PWD"), never as plain text.
# Irreversible operations refuse with code="confirm_required" until called with confirm=True.
# =====================================================================


@mcp.tool()
def pro_egdb_create(database_platform: str, instance: str, database_name: str,
                    authorization_file: str | None = None, dba_user: str | None = None, dba_password_env: str | None = None,
                    gdb_admin_password_env: str | None = None, gdb_admin_name: str = "sde",
                    account_authentication: str = "DATABASE_AUTH", schema_type: str = "SDE_SCHEMA",
                    tablespace: str | None = None, spatial_type: str | None = None) -> dict:
    """Create an enterprise geodatabase on self-managed SQL_SERVER / POSTGRESQL / ORACLE (DBA rights). authorization_file = the user's own keycodes (or env ARCGIS_EGDB_AUTHORIZATION_FILE). Passwords as env-var names; OS auth needs no DBA password, SQL Server DBO_SCHEMA no gdb-admin password."""
    return pro_egdb.create_gdb(database_platform, instance, database_name, authorization_file, dba_user,
                               dba_password_env, gdb_admin_password_env, gdb_admin_name, account_authentication,
                               schema_type, tablespace, spatial_type)


@mcp.tool()
def pro_egdb_enable(admin_connection: str, authorization_file: str | None = None) -> dict:
    """Enable geodatabase functionality in an existing database (cloud DBaaS, Db2, SAP HANA, or a plain SQL Server/PostgreSQL database) through an sde/dbo .sde connection; keycodes as in pro_egdb_create."""
    return pro_egdb.enable_gdb(admin_connection, authorization_file)


@mcp.tool()
def pro_egdb_create_connection(out_folder: str, out_name: str, database_platform: str, instance: str,
                               user: str | None = None, password_env: str | None = None,
                               database: str | None = None, account_authentication: str = "DATABASE_AUTH",
                               version: str | None = None, version_type: str | None = None,
                               save_credentials: bool = True, date: str | None = None,
                               register_in_project: bool = True) -> dict:
    """Create a .sde connection file and add it to the open Pro project's Databases. version_type TRANSACTIONAL|HISTORICAL|POINT_IN_TIME|BRANCH (+version: a version or historical marker; POINT_IN_TIME needs date 'YYYY-MM-DD HH:MM:SS': UTC for non-versioned archives, server-local for versioned; count with a cursor, GetCount ignores the moment)."""
    return pro_egdb.create_connection(out_folder, out_name, database_platform, instance, user, password_env,
                                      database, account_authentication, version, version_type, save_credentials,
                                      date, register_in_project)


@mcp.tool()
def pro_egdb_describe(connection: str) -> dict:
    """Describe an enterprise gdb connection: DBMS, release, user, version and its datasets (capped at 200 each)."""
    return pro_egdb.describe(connection)


@mcp.tool()
def pro_egdb_create_user(admin_connection: str, user_name: str, password_env: str | None = None,
                         role: str | None = None, tablespace: str | None = None,
                         os_authenticated: bool = False) -> dict:
    """Create a database user (data owner / editor / viewer), optionally adding it to a role."""
    return pro_egdb.create_user(admin_connection, user_name, password_env, role, tablespace, os_authenticated)


@mcp.tool()
def pro_egdb_create_role(admin_connection: str, role: str, users: list[str] | None = None,
                         grant: bool = True) -> dict:
    """Create a role and grant (or revoke) it to users."""
    return pro_egdb.create_role(admin_connection, role, users, grant)


@mcp.tool()
def pro_egdb_change_privileges(datasets: list[str], user_or_role: str, view: str = "GRANT",
                               edit: str | None = None) -> dict:
    """Grant/revoke privileges on datasets (connect as the data owner): view=SELECT, edit=INSERT+UPDATE+DELETE together; GRANT|REVOKE|AS_IS."""
    return pro_egdb.change_privileges(datasets, user_or_role, view, edit)


@mcp.tool()
def pro_egdb_prepare_dataset(dataset: str, global_ids: bool = True, editor_tracking: bool = False,
                             attachments: bool = False, register: bool = False) -> dict:
    """Prepare data for multiuser work: Register With Geodatabase, Global IDs (needed for replication), editor tracking, attachments."""
    return pro_egdb.prepare_dataset(dataset, global_ids, editor_tracking, attachments, register)


@mcp.tool()
def pro_egdb_register_versioned(dataset: str, move_edits_to_base: bool = False) -> dict:
    """Register a dataset as versioned: traditional through a TRANSACTIONAL connection, branch through a BRANCH one. Replication needs move_edits_to_base=False."""
    return pro_egdb.register_versioned(dataset, move_edits_to_base)


@mcp.tool()
def pro_egdb_unregister_versioned(dataset: str, compress_default: bool = True, confirm: bool = False) -> dict:
    """Irreversible: unregister a dataset as versioned. Ask the user, then pass confirm=True."""
    return pro_egdb.unregister_versioned(dataset, compress_default, confirm)


@mcp.tool()
def pro_egdb_create_version(connection: str, name: str, parent: str | None = None,
                            access: str = "PUBLIC") -> dict:
    """Create a named version (access PUBLIC|PROTECTED|PRIVATE); parent defaults to the gdb's DEFAULT (sde.DEFAULT or dbo.DEFAULT)."""
    return pro_egdb.create_version(connection, name, parent, access)


@mcp.tool()
def pro_egdb_alter_version(connection: str, version: str, new_name: str | None = None,
                           description: str | None = None, access: str | None = None) -> dict:
    """Rename a version or change its description/access, e.g. protect DEFAULT (connect as the gdb admin)."""
    return pro_egdb.alter_version(connection, version, new_name, description, access)


@mcp.tool()
def pro_egdb_delete_version(connection: str, version: str, confirm: bool = False) -> dict:
    """Irreversible: delete a version and its unposted edits. Ask the user, then pass confirm=True."""
    return pro_egdb.delete_version(connection, version, confirm)


@mcp.tool()
def pro_egdb_list_versions(connection: str) -> dict:
    """List versions (name, access, parent, children names, dates)."""
    return pro_egdb.list_versions(connection)


@mcp.tool()
def pro_egdb_reconcile_post(connection: str, edit_versions: list[str] | None = None,
                            target_version: str | None = None, post: bool = True,
                            conflict_definition: str = "BY_OBJECT",
                            conflict_resolution: str = "FAVOR_TARGET_VERSION", delete_versions: bool = False,
                            log_file: str | None = None, blocking_only: bool = False,
                            confirm: bool = False) -> dict:
    """Reconcile versions with a target (default DEFAULT) and optionally post; edit_versions defaults to all others. Returns reconciled/posted/conflicts read from the log; delete_versions needs confirm=True."""
    return pro_egdb.reconcile_post(connection, edit_versions, target_version, post, conflict_definition,
                                   conflict_resolution, delete_versions, log_file, blocking_only, confirm)


@mcp.tool()
def pro_egdb_enable_archiving(dataset: str, enable: bool = True) -> dict:
    """Enable or disable geodatabase archiving (feature history; disabling keeps the history table)."""
    return pro_egdb.enable_archiving(dataset, enable)


@mcp.tool()
def pro_egdb_connected_users(admin_connection: str) -> dict:
    """List users connected to the geodatabase (connect as the gdb admin)."""
    return pro_egdb.connected_users(admin_connection)


@mcp.tool()
def pro_egdb_maintenance(admin_connection: str, compress: bool = True, analyze: bool = True,
                         rebuild_indexes: bool = True, datasets: list[str] | None = None) -> dict:
    """Compress + Analyze Datasets + Rebuild Indexes (system tables included). Connect as the gdb admin."""
    return pro_egdb.maintenance(admin_connection, compress, analyze, rebuild_indexes, datasets)


@mcp.tool()
def pro_egdb_accept_connections(admin_connection: str, accept: bool, confirm: bool = False) -> dict:
    """Allow or block new connections (e.g. before an upgrade). Blocking (accept=False) needs confirm=True."""
    return pro_egdb.accept_connections(admin_connection, accept, confirm)


@mcp.tool()
def pro_egdb_create_replica(datasets: list[str], child_workspace: str, replica_name: str,
                            replica_type: str = "ONE_WAY_REPLICA", access_type: str = "FULL",
                            initial_data_sender: str = "PARENT_DATA_SENDER", expand: str = "USE_DEFAULTS",
                            reuse_schema: str = "DO_NOT_REUSE", get_related: str = "GET_RELATED",
                            geometry: str | None = None, archiving: str = "DO_NOT_USE_ARCHIVING") -> dict:
    """Create a replica (CHECK_OUT|ONE_WAY_REPLICA|TWO_WAY_REPLICA|ONE_WAY_CHILD_TO_PARENT_REPLICA). Checks first: versioned data with GlobalIDs; two-way and child-to-parent need an enterprise child."""
    return pro_egdb.create_replica(datasets, child_workspace, replica_name, replica_type, access_type,
                                   initial_data_sender, expand, reuse_schema, get_related, geometry, archiving)


@mcp.tool()
def pro_egdb_list_replicas(connection: str) -> dict:
    """List replicas with type, role, datasets and last sync times."""
    return pro_egdb.list_replicas(connection)


@mcp.tool()
def pro_egdb_synchronize(geodatabase_1: str, replica_name: str, geodatabase_2: str,
                         direction: str = "FROM_GEODATABASE1_TO_2", conflict_policy: str = "IN_FAVOR_OF_GDB1",
                         conflict_definition: str = "BY_OBJECT", reconcile: str = "DO_NOT_RECONCILE") -> dict:
    """Synchronize replica changes between two connected geodatabases."""
    return pro_egdb.synchronize(geodatabase_1, replica_name, geodatabase_2, direction, conflict_policy,
                                conflict_definition, reconcile)


@mcp.tool()
def pro_egdb_export_changes(geodatabase: str, out_file: str, replica_name: str,
                            switch_to_receiver: str = "DO_NOT_SWITCH",
                            include_unacknowledged: str = "UNACKNOWLEDGED",
                            include_new: str = "NEW_CHANGES") -> dict:
    """Disconnected sync step 1: export a data-change message file from the sender."""
    return pro_egdb.export_changes(geodatabase, out_file, replica_name, switch_to_receiver,
                                   include_unacknowledged, include_new)


@mcp.tool()
def pro_egdb_import_message(geodatabase: str, message_file: str, out_acknowledgement: str | None = None,
                            conflict_policy: str = "IN_FAVOR_OF_IMPORTED_CHANGES",
                            conflict_definition: str = "BY_OBJECT") -> dict:
    """Disconnected sync step 2: import a data-change or acknowledgment message."""
    return pro_egdb.import_message(geodatabase, message_file, out_acknowledgement, conflict_policy,
                                   conflict_definition)


@mcp.tool()
def pro_egdb_export_acknowledgement(geodatabase: str, out_file: str, replica_name: str) -> dict:
    """Disconnected sync step 3: export an acknowledgment message back to the sender."""
    return pro_egdb.export_acknowledgement(geodatabase, out_file, replica_name)


@mcp.tool()
def pro_egdb_unregister_replica(geodatabase: str, replica_name: str, confirm: bool = False) -> dict:
    """Irreversible: unregister a replica (it can never synchronize again). Ask the user, then pass confirm=True."""
    return pro_egdb.unregister_replica(geodatabase, replica_name, confirm)


@mcp.tool()
def pro_egdb_upgrade(admin_connection: str, upgrade: bool = False, confirm: bool = False) -> dict:
    """Check whether the geodatabase can/should be upgraded (default), or upgrade it (upgrade=True, irreversible: back up, then confirm=True)."""
    return pro_egdb.upgrade_gdb(admin_connection, upgrade, confirm)


@mcp.tool()
def pro_egdb_disconnect_users(admin_connection: str, users: list[int] | None = None, all_users: bool = False,
                              confirm: bool = False) -> dict:
    """Disconnect sessions by ID (from pro_egdb_connected_users) or all_users=True; needs confirm=True."""
    return pro_egdb.disconnect_users(admin_connection, users, all_users, confirm)


@mcp.tool()
def pro_egdb_version_health(connection: str, action: str = "diagnose", scope: str = "tables",
                            target_version: str | None = None, tables: list[str] | None = None,
                            confirm: bool = False) -> dict:
    """Diagnose (read-only) or repair (confirm=True) the version tables or version metadata; returns the log."""
    return pro_egdb.version_health(connection, action, scope, target_version, tables, confirm)


@mcp.tool()
def pro_egdb_manage_principal(admin_connection: str, action: str, name: str, password_env: str | None = None,
                              confirm: bool = False) -> dict:
    """drop_user | drop_role (confirm=True; never cascades; a logged-in SQL Server user is refused with the KILL statements a DBA must run) | set_password (password_env). SQL Server, PostgreSQL, Oracle."""
    return pro_egdb.manage_principal(admin_connection, action, name, password_env, confirm)


@mcp.tool()
def pro_egdb_table_privileges(connection: str, datasets: list[str], principal: str,
                              grant: list[str] | None = None, revoke: list[str] | None = None) -> dict:
    """Grant/revoke SELECT, INSERT, UPDATE, DELETE individually on simple tables; versioned/archived/attachment datasets are refused (use pro_egdb_change_privileges)."""
    return pro_egdb.table_privileges(connection, datasets, principal, grant, revoke)


@mcp.tool()
def pro_egdb_prepare_cloud_database(platform: str, service: str, admin_connection: str | None = None,
                                    sde_password_env: str | None = None, execute: bool = False,
                                    confirm: bool = False) -> dict:
    """Esri's database-side setup before pro_egdb_enable in a cloud service (POSTGRESQL: RDS|AURORA|AZURE|CLOUD_SQL; SQL_SERVER: AZURE_SQL_DATABASE|AZURE_SQL_MANAGED_INSTANCE|RDS|CLOUD_SQL). Returns the SQL; execute=True+confirm runs it."""
    return pro_egdb.prepare_cloud_database(platform, service, admin_connection, sde_password_env, execute, confirm)


@mcp.tool()
def pro_egdb_create_view(connection: str, view_name: str, definition: str) -> dict:
    """Create a database view from a SQL SELECT, owned by the connected user."""
    return pro_egdb.create_view(connection, view_name, definition)


@mcp.tool()
def pro_egdb_config_keywords(connection: str, action: str, file: str) -> dict:
    """export | import the geodatabase configuration keywords (storage settings) to/from a text file."""
    return pro_egdb.config_keywords(connection, action, file)


@mcp.tool()
def pro_egdb_replica_schema(geodatabase: str, action: str, file: str, replica_name: str | None = None,
                            out_file: str | None = None) -> dict:
    """Replica schema: export (replica_name) | compare (out_file gets the changes) | import, to keep replicas in step after schema edits."""
    return pro_egdb.replica_schema(geodatabase, action, file, replica_name, out_file)


# =====================================================================
# Layer 1b — Live session (the OPEN ArcGIS Pro session via the .NET bridge)
# =====================================================================
# These tools POST commands over loopback HTTP to the SalahAIBridge add-in
# running inside ArcGIS Pro (see SalahAIBridge/ and docs/PROTOCOL.md). If Pro or
# the add-in isn't running they return a clean {"ok": false, "error": "bridge
# unreachable: ..."} envelope rather than failing.


@mcp.tool()
def live_ping() -> dict:
    """Liveness + context of the OPEN ArcGIS Pro session (project, maps, layouts)."""
    return live.live_ping()


@mcp.tool()
def live_list_layers(map_name: str | None = None) -> dict:
    """List layers in the active (or named) map of the open Pro session."""
    return live.live_list_layers(map_name)


@mcp.tool()
def live_zoom_to(layer: str, selection: bool = False) -> dict:
    """Zoom the active view to a layer (or its current selection)."""
    return live.live_zoom_to(layer, selection)


@mcp.tool()
def live_query(
    layer: str,
    where: str | None = None,
    fields: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """Read attribute rows from a layer in the open Pro session."""
    return live.live_query(layer, where, fields, limit)


@mcp.tool()
def live_run_gp(
    tool: str,
    args: list[Any] | None = None,
    kwargs: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Run a geoprocessing tool LIVE; output is added to the active map. Destructive: pass confirm=True (refused in read-only mode)."""
    return live.live_run_gp(tool, args, kwargs, confirm)


@mcp.tool()
def live_add_layer(path: str) -> dict:
    """Add a dataset from disk to the active map of the open Pro session (refused in read-only mode)."""
    return live.live_add_layer(path)


@mcp.tool()
def live_export_layout(layout: str, out_path: str, dpi: int = 300) -> dict:
    """Export a layout from the open Pro session to a PDF (refused in read-only mode)."""
    return live.live_export_layout(layout, out_path, dpi)


@mcp.tool()
def live_get_request(clear: bool = False) -> dict:
    """Fetch what the user queued from the Salah MCP ribbon (Create Web App prompt / Publish). clear=True consumes it."""
    return live.live_get_request(clear)


# =====================================================================
# Layer 2 — Portal / ArcGIS Online (ArcGIS API for Python)
# =====================================================================


@mcp.tool()
def live_add_database(path: str) -> dict:
    """Add an existing .sde connection file (or .gdb) to the OPEN ArcGIS Pro project's Databases (Catalog). Needs add-in 0.1.5+."""
    return live.live_add_database(path)


@mcp.tool()
def live_add_toolbox(path: str) -> dict:
    """Add an existing .atbx/.tbx/.pyt (e.g. a model from pro_create_model) to the OPEN ArcGIS Pro project's Toolboxes so it appears in Catalog and the Geoprocessing pane. Needs add-in 0.1.4+."""
    return live.live_add_toolbox(path)


@mcp.tool()
def portal_connect(
    portal_url: str | None = None,
    profile: str | None = None,
    username: str | None = None,
) -> dict:
    """Connect to ArcGIS Online/Portal. Prefer a stored profile over username."""
    return portal.connect(portal_url, profile, username)


@mcp.tool()
def portal_whoami() -> dict:
    """Return the signed-in user, org and portal URL."""
    return portal.whoami()


@mcp.tool()
def portal_publish_layer(
    source: str, title: str, tags: list[str] | None = None, folder: str | None = None
) -> dict:
    """Publish a local dataset (shp/gpkg/feature class/CSV) as a hosted feature layer. Returns the item id."""
    return portal.publish_layer(source, title, tags, folder)


@mcp.tool()
def portal_search_items(
    query: str, item_type: str | None = None, max_items: int = 20
) -> dict:
    """Search the portal for items."""
    return portal.search_items(query, item_type, max_items)


@mcp.tool()
def portal_get_item(item_id: str) -> dict:
    """Get metadata for a portal item by id."""
    return portal.get_item(item_id)


@mcp.tool()
def portal_create_webmap(
    title: str,
    layer_item_ids: list[str],
    basemap: str = "topo-vector",
    tags: list[str] | None = None,
) -> dict:
    """Create a Web Map from one or more hosted layer item ids. Returns the webmap item id."""
    return portal.create_webmap(title, layer_item_ids, basemap, tags)


@mcp.tool()
def portal_set_layer_symbology(
    item_id: str, renderer: dict, layer_index: int = 0
) -> dict:
    """Apply a renderer (symbology) to a hosted feature layer's drawing info."""
    return portal.set_layer_symbology(item_id, renderer, layer_index)


@mcp.tool()
def portal_set_layer_labeling(
    item_id: str, label_field: str, layer_index: int = 0
) -> dict:
    """Enable labeling on a hosted feature layer using a field."""
    return portal.set_layer_labeling(item_id, label_field, layer_index)


@mcp.tool()
def portal_share_item(
    item_id: str,
    everyone: bool = False,
    org: bool = False,
    groups: list[str] | None = None,
) -> dict:
    """Share a portal item with everyone / the org / specific groups."""
    return portal.share_item(item_id, everyone, org, groups)


# =====================================================================
# Layer 3 — Web app generator (ArcGIS Maps SDK for JavaScript, static)
# =====================================================================


@mcp.tool()
def webapp_create(
    title: str,
    webmap_id: str | None = None,
    layer_item_ids: list[str] | None = None,
    out_dir: str | None = None,
    basemap: str = "topo-vector",
    widgets: list[str] | None = None,
) -> dict:
    """Generate a static ArcGIS Maps SDK for JS app.

    Provide either a `webmap_id` (loads a saved Web Map with its symbology &
    labeling) or a list of `layer_item_ids` (added as feature layers).
    `widgets` may include: legend, layerList, search, basemapGallery, home.
    """
    return webapp.create_web_app(
        title, webmap_id, layer_item_ids, out_dir, basemap, widgets
    )


@mcp.tool()
def webapp_create_dashboard(
    title: str,
    webmap_id: str | None = None,
    layer_item_ids: list[str] | None = None,
    out_dir: str | None = None,
    basemap: str = "topo-vector",
    widgets: list[str] | None = None,
    category_field: str | None = None,
    value_fields: list[str] | None = None,
) -> dict:
    """Generate a static Esri-Dashboard-style web app (map + live indicators + attribute list).

    Provide either a `webmap_id` (its first feature layer drives the stats) or
    `layer_item_ids` (the first is the primary stats layer). Indicators (count &
    sums), a category breakdown and a "features in view" list are derived from the
    data at runtime and recompute as you zoom/pan. `category_field` / `value_fields`
    optionally override the auto-picked fields.
    """
    return webapp_dashboard.create_dashboard(
        title,
        webmap_id,
        layer_item_ids,
        out_dir,
        basemap,
        widgets,
        None,
        category_field,
        value_fields,
    )


@mcp.tool()
def webapp_github_pipeline(
    repo_name: str, token: str, deploy_mode: str = "upload"
) -> dict:
    """Create a public GitHub repo, push the generated static web app, and (deploy_mode='live') enable GitHub Pages."""
    return webapp_github.github_pipeline(repo_name, token, deploy_mode)

# =====================================================================
# Layer 1c — Fast context, batching and jobs
#
# These exist to kill ROUND TRIPS. Measured: the arcpy work behind one call is
# only 0.1-0.5 s, but every extra tool call costs a full model inference. One
# pro_context beats list_layers + describe_layer + get_features every time.
# =====================================================================


@mcp.tool()
def pro_context(
    aprx_path: str | None = None,
    workspace: str | None = None,
    datasets: list[str] | None = None,
    sample_rows: int = 3,
    max_datasets: int = 25,
    map_name: str | None = None,
) -> dict:
    """START HERE. Everything about a project/workspace in ONE call: maps, layers, schemas, row counts, sample rows and inferred themes. Give an .aprx, a .gdb workspace, or explicit datasets."""
    return pro_context_mod.context(
        aprx_path, workspace, datasets, sample_rows, max_datasets, map_name
    )


@mcp.tool()
def pro_find_layers(
    query: str,
    aprx_path: str | None = None,
    workspace: str | None = None,
    limit: int = 10,
) -> dict:
    """Search layers by meaning ("which layer has population data?") without reading every schema."""
    return pro_context_mod.find_layers(query, aprx_path, workspace, limit)


@mcp.tool()
def pro_sql(
    dataset: str,
    where_clause: str,
    fields: list[str] | None = None,
    limit: int = 50,
    order_by: str | None = None,
) -> dict:
    """Attribute query returning the matching ROWS (pro_select_by_expression only returns a count)."""
    return pro.sql_query(dataset, where_clause, fields, limit, order_by)


@mcp.tool()
def pro_pipeline(steps: list[dict], stop_on_error: bool = True) -> dict:
    """Run several GP tools in ONE call, chaining outputs. steps: [{"tool":"analysis.Buffer","args":[...],"kwargs":{...}}]. Use {{prev}} or {{step2}} inside args to reference an earlier step's output."""
    return pro_pipeline_mod.pipeline(steps, stop_on_error)


@mcp.tool()
def pro_job_submit(
    steps: list[dict], stop_on_error: bool = True, label: str | None = None
) -> dict:
    """Start a long pipeline on a worker thread and get a job_id back immediately, so the agent stays responsive."""
    return pro_pipeline_mod.submit(steps, stop_on_error, label)


@mcp.tool()
def pro_job_status(job_id: str | None = None) -> dict:
    """Poll a background job by id, or list all jobs when job_id is omitted."""
    return pro_pipeline_mod.job_status(job_id)


@mcp.tool()
def pro_run_background(tool: str, arguments: dict | None = None) -> dict:
    """Run any pro_* tool as a background job (returns job_id at once) so a long call never hits the client's tool timeout; poll pro_job_status(job_id)."""
    fn = _ORIGINAL_FN.get(tool)
    if fn is None or not tool.startswith("pro_") or tool in {"pro_run_background", "pro_job_status"}:
        return _err(f"Unknown or unsupported tool {tool!r}; give a pro_* tool name.", code="unknown_tool")
    return pro_pipeline_mod.submit_call(tool, fn, arguments or {})


@mcp.tool()
def pro_server_status() -> dict:
    """Server health: arcpy warm-up, what the arcpy thread is busy with (and for how long), schema-cache hit rate."""
    return _ok(
        {"arcpy_warmup": bootstrap.warmup_status(), "arcpy_thread": arcpy_thread.status(),
         "schema_cache": pro_cache.stats()}
    )


# =====================================================================
# Layer 1d — Geodatabase schema: domains, subtypes, attribute rules
# =====================================================================


@mcp.tool()
def pro_list_domains(workspace: str) -> dict:
    """Every domain in a geodatabase with its coded values or range."""
    return pro_schema.list_domains(workspace)


@mcp.tool()
def pro_create_domain(
    workspace: str,
    domain_name: str,
    field_type: str = "TEXT",
    domain_type: str = "CODED",
    description: str | None = None,
    coded_values: dict | None = None,
    range_min: float | None = None,
    range_max: float | None = None,
) -> dict:
    """Create a CODED or RANGE domain and populate it in one call. coded_values is {code: description}."""
    return pro_schema.create_domain(
        workspace, domain_name, field_type, domain_type, description,
        coded_values, range_min, range_max,
    )


@mcp.tool()
def pro_assign_domain(
    table: str, field_name: str, domain_name: str, subtype_codes: list | None = None
) -> dict:
    """Attach a domain to a field, optionally only for specific subtypes."""
    return pro_schema.assign_domain(table, field_name, domain_name, subtype_codes)


@mcp.tool()
def pro_delete_domain(workspace: str, domain_name: str) -> dict:
    """Delete a domain (fails while a field still uses it)."""
    return pro_schema.delete_domain(workspace, domain_name)


@mcp.tool()
def pro_describe_subtypes(table: str) -> dict:
    """The subtype field, all subtype codes, and per-subtype field defaults/domains."""
    return pro_schema.describe_subtypes(table)


@mcp.tool()
def pro_set_subtypes(
    table: str, subtype_field: str, subtypes: dict, default_code: int | None = None
) -> dict:
    """Make a field the subtype field and create every subtype at once. subtypes is {code: description}."""
    return pro_schema.set_subtypes(table, subtype_field, subtypes, default_code)


@mcp.tool()
def pro_describe_attribute_rules(table: str) -> dict:
    """Every attribute rule on a table, with its Arcade expression."""
    return pro_schema.describe_attribute_rules(table)


@mcp.tool()
def pro_add_attribute_rule(
    table: str,
    name: str,
    rule_type: str,
    script_expression: str,
    field: str | None = None,
    triggering_events: list[str] | None = None,
    error_number: int | None = None,
    error_message: str | None = None,
    description: str | None = None,
    subtype: str | None = None,
    is_editable: bool = True,
    batch: bool = False,
) -> dict:
    """Add a CALCULATION / CONSTRAINT / VALIDATION Arcade rule. CALCULATION needs `field` + triggering_events; CONSTRAINT/VALIDATION need error_number + error_message."""
    return pro_schema.add_attribute_rule(
        table, name, rule_type, script_expression, field, triggering_events,
        error_number, error_message, description, subtype, is_editable, False, batch,
    )


@mcp.tool()
def pro_evaluate_rules(
    workspace: str, evaluation_types: list[str] | None = None, extent: str | None = None
) -> dict:
    """Run batch CALCULATION/VALIDATION rules — VALIDATION rules produce no errors until evaluated."""
    return pro_schema.evaluate_rules(workspace, evaluation_types, extent)


@mcp.tool()
def pro_toggle_attribute_rules(
    table: str, rule_names: list[str], enable: bool = True
) -> dict:
    """Enable or disable named attribute rules (e.g. before a bulk load)."""
    return pro_schema.toggle_attribute_rules(table, rule_names, enable)


@mcp.tool()
def pro_delete_attribute_rule(
    table: str, rule_names: list[str], rule_type: str | None = None
) -> dict:
    """Remove attribute rules from a table."""
    return pro_schema.delete_attribute_rule(table, rule_names, rule_type)


@mcp.tool()
def pro_apply_schema(workspace: str, schema: dict, stop_on_error: bool = True) -> dict:
    """Apply a WHOLE schema in ONE call: {"domains":[...], "tables":[{"table","field_domains","subtype_field","subtypes","rules"}]}. Replaces dozens of separate calls."""
    return pro_schema.apply_schema(workspace, schema, stop_on_error)


# =====================================================================
# Layer 1e — Validation: topology + geometry
# =====================================================================


@mcp.tool()
def pro_topology_rules(geometry: str | None = None) -> dict:
    """The valid topology rule strings, grouped by geometry pairing (area, line-area, point-line...). Call this before pro_create_topology."""
    return pro_validate.describe_topology_rules(geometry)


@mcp.tool()
def pro_describe_topology(topology: str) -> dict:
    """Inspect a topology: its feature classes, rules and cluster tolerance."""
    return pro_validate.describe_topology(topology)


@mcp.tool()
def pro_create_topology(
    feature_dataset: str,
    name: str,
    feature_classes: list[str],
    rules: list[dict] | None = None,
    cluster_tolerance: float = 0,
    validate: bool = True,
) -> dict:
    """Create a topology, add feature classes and rules, and validate — in ONE call. rules: [{"rule_type","origin","destination"}]."""
    return pro_validate.create_topology(
        feature_dataset, name, feature_classes, rules, cluster_tolerance, validate
    )


@mcp.tool()
def pro_validate_topology(
    topology: str, visible_extent_only: bool = False, count_errors: bool = True
) -> dict:
    """Validate a topology and report how many errors of each geometry type exist, with samples."""
    return pro_validate.validate_topology(topology, visible_extent_only, count_errors)


@mcp.tool()
def pro_export_topology_errors(
    topology: str, out_workspace: str, out_base_name: str
) -> dict:
    """Export topology errors to feature classes you can open and inspect."""
    return pro_validate.export_topology_errors(topology, out_workspace, out_base_name)


@mcp.tool()
def pro_check_geometry(datasets: list[str], out_table: str) -> dict:
    """Find geometry problems (self-intersections, null geometry, bad rings) and summarise them by type."""
    return pro_validate.check_geometry(datasets, out_table)


@mcp.tool()
def pro_validate_all(
    datasets: list[str] | None = None,
    topologies: list[str] | None = None,
    scratch_workspace: str | None = None,
) -> dict:
    """"Is my data clean?" in ONE call: geometry checks + topology validation + attribute-rule inventory, consolidated into one report."""
    return pro_validate.validate_all(datasets, topologies, scratch_workspace)


# =====================================================================
# Layer 1f — GeoAI: deep learning, vision-language models, foundation models
# =====================================================================


@mcp.tool()
def pro_geoai_check_environment() -> dict:
    """What GeoAI is actually available here: Image Analyst licence, arcpy.geoai, torch, CUDA GPU. Call BEFORE any long run."""
    return pro_geoai.check_environment()


@mcp.tool()
def pro_geoai_describe_model(model_definition: str) -> dict:
    """Read a .dlpk/.emd: the classes it predicts, its chip size and framework."""
    return pro_geoai.describe_model(model_definition)


@mcp.tool()
def pro_geoai_model_types(task: str | None = None) -> dict:
    """Valid training model_type values by task (object_detection, pixel_classification, ...) with the training format each requires."""
    return pro_geoai.list_model_types(task)


@mcp.tool()
def pro_geoai_detect_by_text(
    in_raster: str,
    class_name: str,
    box_threshold: float = 0.3,
    text_threshold: float = 0.3,
    tile_size: int = 512,
    use_gpu: bool | None = None,
    extent: str | None = None,
) -> dict:
    """VLM open-vocabulary detection: find objects described in PLAIN TEXT ("solar panel", "swimming pool", "flooded road") — no model, no training. Returns labels + confidence scores. SLOW without a GPU (measured 731 s for one 1024x1024 image on CPU) — check pro_geoai_check_environment first, and consider pro_job_submit."""
    return pro_geoai.detect_objects_by_text(
        in_raster, class_name, None, box_threshold, text_threshold,
        tile_size, 0, use_gpu, extent,
    )


@mcp.tool()
def pro_geoai_text_to_layer(
    in_raster: str,
    prompt: str,
    out_features: str,
    box_threshold: float = 0.3,
    text_threshold: float = 0.3,
    apply_nms: bool = True,
) -> dict:
    """Imagery + a sentence -> a finished, de-duplicated feature layer in ONE call (detect by text, then non-maximum suppression)."""
    return pro_geoai.workflow_text_to_layer(
        in_raster, prompt, out_features, box_threshold, text_threshold, apply_nms
    )


@mcp.tool()
def pro_geoai_detect_objects(
    in_raster: str,
    out_features: str,
    model_definition: str,
    arguments: dict | None = None,
    run_nms: bool = True,
    processor_type: str | None = None,
) -> dict:
    """Run a trained detection model (.dlpk/.emd) over imagery. NMS is on by default to drop tile-boundary duplicates."""
    return pro_geoai.detect_objects(
        in_raster, out_features, model_definition, arguments, run_nms,
        "Confidence", "Class", 0.0, "PROCESS_AS_MOSAICKED_IMAGE", processor_type,
    )


@mcp.tool()
def pro_geoai_classify_pixels(
    in_raster: str,
    model_definition: str,
    out_raster: str | None = None,
    out_features: str | None = None,
    arguments: dict | None = None,
    processor_type: str | None = None,
) -> dict:
    """Semantic segmentation: classify every pixel (land cover, roads, damage)."""
    return pro_geoai.classify_pixels(
        in_raster, model_definition, out_raster, out_features, arguments,
        "PROCESS_AS_MOSAICKED_IMAGE", processor_type,
    )


@mcp.tool()
def pro_geoai_classify_objects(
    in_raster: str,
    out_features: str,
    model_definition: str,
    in_features: str | None = None,
    class_label_field: str | None = None,
) -> dict:
    """Label EXISTING features from imagery (e.g. classify each building's roof type)."""
    return pro_geoai.classify_objects(
        in_raster, out_features, model_definition, in_features, class_label_field
    )


@mcp.tool()
def pro_geoai_detect_change(
    from_raster: str, to_raster: str, model_definition: str, out_raster: str
) -> dict:
    """Bi-temporal change detection between two co-registered rasters."""
    return pro_geoai.detect_change(from_raster, to_raster, model_definition, out_raster)


@mcp.tool()
def pro_geoai_generate_embeddings(
    in_data: str,
    out_embeddings: str,
    model_definition: str,
    arguments: dict | None = None,
) -> dict:
    """Embed imagery/features with a vision FOUNDATION MODEL (DINOv3-class, SAM). Builds the index that pro_geoai_find_similar searches — the "image-context" workflow."""
    return pro_geoai.generate_embeddings(
        in_data, out_embeddings, model_definition, arguments
    )


@mcp.tool()
def pro_geoai_find_similar(
    embedding_features: str,
    query_features: str,
    out_features: str,
    threshold: float = 0.8,
) -> dict:
    """Retrieve everything that LOOKS LIKE your example: label ONE feature, get the rest. No training, no class list."""
    return pro_geoai.find_similar_features(
        embedding_features, query_features, out_features, threshold
    )


@mcp.tool()
def pro_geoai_extract_with_foundation_models(
    in_raster: str,
    out_location: str,
    out_prefix: str,
    pretrained_models: list[str] | None = None,
    area_of_interest: str | None = None,
    confidence_threshold: float | None = None,
) -> dict:
    """Run Esri's pretrained foundation models (buildings, roads, trees) end to end — infer AND post-process (regularise footprints, connect centrelines)."""
    return pro_geoai.extract_features_with_foundation_models(
        in_raster, out_location, out_prefix, pretrained_models,
        area_of_interest, confidence_threshold,
    )


@mcp.tool()
def pro_geoai_export_training_data(
    in_raster: str,
    out_folder: str,
    in_class_data: str,
    task: str = "object_detection",
    tile_size: int = 256,
    class_value_field: str | None = None,
) -> dict:
    """Cut imagery + labels into training chips. The metadata format is derived from `task`, so it cannot mismatch what you later train."""
    return pro_geoai.export_training_data(
        in_raster, out_folder, in_class_data, task, None, "TIFF",
        tile_size, None, class_value_field,
    )


@mcp.tool()
def pro_geoai_train_model(
    in_folder: str,
    out_folder: str,
    model_type: str,
    max_epochs: int = 20,
    batch_size: int = 4,
    backbone_model: str | None = None,
    arguments: dict | None = None,
) -> dict:
    """Train a model from exported chips. Long-running — consider pro_job_submit."""
    return pro_geoai.train_model(
        in_folder, out_folder, model_type, max_epochs, batch_size,
        arguments, None, backbone_model,
    )


@mcp.tool()
def pro_geoai_compute_accuracy(
    detected_features: str,
    ground_truth_features: str,
    out_accuracy_table: str,
    out_report: str | None = None,
    min_iou: float = 0.5,
) -> dict:
    """Score detections against ground truth (mAP / precision / recall)."""
    return pro_geoai.compute_accuracy(
        detected_features, ground_truth_features, out_accuracy_table,
        out_report, None, None, min_iou,
    )


@mcp.tool()
def pro_geoai_train_and_detect(
    in_raster: str,
    training_labels: str,
    work_folder: str,
    out_features: str,
    task: str = "object_detection",
    model_type: str = "FASTERRCNN",
    max_epochs: int = 20,
) -> dict:
    """Labels -> chips -> trained model -> detections, in ONE call. Genuinely long; submit via pro_job_submit to stay responsive."""
    return pro_geoai.workflow_train_and_detect(
        in_raster, training_labels, work_folder, out_features,
        task, model_type, max_epochs,
    )


# =====================================================================
# Layer 1g — Utility Network
# =====================================================================


# =====================================================================
# Layer 1f (cont.) — Deep-learning NOTEBOOKS: interview first, then a script
# One tool on purpose: every tool schema is sent on every request.
# =====================================================================


@mcp.tool()
def pro_dl_notebook(
    goal: str = "detect buildings",
    out_path: str | None = None,
    model: str | None = None,
    training_data: str | None = None,
    batch_size: int | None = None,
    epochs: int | None = None,
    learning_rate: float | None = None,
    chip_size: int | None = None,
    val_split_pct: float | None = None,
    options: dict | None = None,
    mixed_precision: bool = False,
    early_stopping: bool = False,
    model_name: str | None = None,
    input_raster: str | None = None,
    input_features: str | None = None,
    second_raster: str | None = None,
    data_path_later: bool = False,
    overwrite: bool = False,
) -> dict:
    """Make an arcgis.learn TRAINING notebook for ArcGIS Pro (detect, segment, classify pixels/objects, change detection) to run one cell at a time. Change detection can instead use Esri's ready-made Building Change Detection (no training, needs 10-20 cm before/after images: pass input_raster=before, second_raster=after). CALL FIRST with only `goal`: it replies status=ask_user (recommended model + the questions: batch size, epochs, learning rate...) or status=choose (plain "classification" is ambiguous: pixel / object / detect). ASK the user, never guess, then call again with the answers to get status=created. `options` = model-specific args e.g. {"backbone":"resnet34","pointrend":true}. out_path defaults to the open Pro project's folder. data_path_later=true ONLY if the user said they will type the data path. Add the file in Pro via Catalog > Notebooks > Add and Open Notebook."""
    return pro_dl.notebook(
        goal, out_path, model, training_data, batch_size, epochs, learning_rate,
        chip_size, val_split_pct, options, mixed_precision, early_stopping,
        model_name, input_raster, input_features, second_raster,
        data_path_later=data_path_later, overwrite=overwrite,
    )


@mcp.tool()
def pro_un_describe(utility_network: str, include_rules: bool = False) -> dict:
    """The whole utility-network model in ONE read: domain networks, tiers, terminals, network attributes, categories. Start here."""
    return pro_unet.describe(utility_network, include_rules)


@mcp.tool()
def pro_un_trace_types() -> dict:
    """The 10 trace types and the question each answers (UPSTREAM = what feeds this, ISOLATION = which valves to close, ...)."""
    return pro_unet.list_trace_types()


@mcp.tool()
def pro_un_build(feature_dataset: str, spec: dict, enable: bool = True) -> dict:
    """Author a WHOLE utility network from one spec, in the order the geodatabase demands (create -> domain networks -> tiers -> rules -> enable topology)."""
    return pro_unet.build(feature_dataset, spec, enable)


@mcp.tool()
def pro_un_create(
    feature_dataset: str, name: str, service_territory: str, version: str = "CURRENT"
) -> dict:
    """Create an empty utility network inside a feature dataset."""
    return pro_unet.create(feature_dataset, name, service_territory, version)


@mcp.tool()
def pro_un_add_domain_network(
    utility_network: str,
    name: str,
    tier_definition: str = "HIERARCHICAL",
    subnetwork_controller_type: str = "SOURCE",
) -> dict:
    """Add a domain network (Electric, Water, Gas). SOURCE networks are fed from a source; SINK networks drain to one."""
    return pro_unet.add_domain_network(
        utility_network, name, tier_definition, subnetwork_controller_type
    )


@mcp.tool()
def pro_un_add_tier(
    utility_network: str,
    domain_network: str,
    name: str,
    rank: int,
    topology_type: str = "RADIAL",
    subnetwork_field: str | None = None,
) -> dict:
    """Add a tier (rank 1 = most upstream). RADIAL = one path from the source; MESH = multiple paths."""
    return pro_unet.add_tier(
        utility_network, domain_network, name, rank, topology_type, None,
        subnetwork_field,
    )


@mcp.tool()
def pro_un_add_rule(
    utility_network: str,
    rule_type: str,
    from_class: str,
    from_assetgroup: str,
    from_assettype: str,
    to_class: str,
    to_assetgroup: str,
    to_assettype: str,
    from_terminal: str | None = None,
    to_terminal: str | None = None,
) -> dict:
    """Add a connectivity/containment/attachment rule — what makes the network refuse invalid edits."""
    return pro_unet.add_rule(
        utility_network, rule_type, from_class, from_assetgroup, from_assettype,
        to_class, to_assetgroup, to_assettype, from_terminal, to_terminal,
    )


@mcp.tool()
def pro_un_add_network_attribute(
    utility_network: str, name: str, field_type: str = "LONG", inline: bool = True
) -> dict:
    """Add a network attribute — the values traces can filter and sum on."""
    return pro_unet.add_network_attribute(utility_network, name, field_type, inline)


@mcp.tool()
def pro_un_add_category(utility_network: str, name: str) -> dict:
    """Add a network category (traces use these as barriers/filters)."""
    return pro_unet.add_category(utility_network, name)


@mcp.tool()
def pro_un_add_terminal_configuration(
    utility_network: str, name: str, terminals: list[str], directional: bool = True
) -> dict:
    """Define how a device's terminals connect (e.g. a transformer's HIGH/LOW)."""
    return pro_unet.add_terminal_configuration(
        utility_network, name, terminals, directional
    )


@mcp.tool()
def pro_un_topology(
    utility_network: str, action: str = "validate", extent: str | None = None
) -> dict:
    """Network topology control. action: enable | disable | validate | verify | repair | analyze. Topology must be DISABLED to change the schema and ENABLED+validated to trace."""
    fn = {
        "enable": lambda: pro_unet.enable_topology(utility_network),
        "disable": lambda: pro_unet.disable_topology(utility_network),
        "validate": lambda: pro_unet.validate_topology(utility_network, extent),
        "verify": lambda: pro_unet.verify_topology(utility_network),
        "repair": lambda: pro_unet.repair_topology(utility_network, extent),
        "analyze": lambda: pro_unet.analyze(utility_network),
    }.get(action.lower())
    if fn is None:
        return _err(
            "action must be one of: enable, disable, validate, verify, repair, analyze"
        )
    return fn()


@mcp.tool()
def pro_un_add_trace_locations(
    utility_network: str,
    out_feature_class: str,
    from_selection: bool = True,
    clear_existing: bool = True,
    as_barrier: bool = False,
) -> dict:
    """Build the starting-points table a trace requires. A trace REJECTS a plain feature-class copy (ERROR 001911) — select your start features first, run this, then pass its output as pro_un_trace's starting_points."""
    return pro_unet.add_trace_locations(
        utility_network, out_feature_class, from_selection, clear_existing, as_barrier
    )


@mcp.tool()
def pro_un_trace(
    utility_network: str,
    trace_type: str,
    starting_points: str | None = None,
    barriers: str | None = None,
    domain_network: str | None = None,
    tier: str | None = None,
    subnetwork_name: str | None = None,
    shortest_path_attribute: str | None = None,
    result_types: list[str] | None = None,
    out_lines: str | None = None,
    out_points: str | None = None,
    out_json: str | None = None,
) -> dict:
    """Run a utility-network trace. trace_type: CONNECTED, SUBNETWORK, SUBNETWORK_CONTROLLERS, UPSTREAM, DOWNSTREAM, LOOPS, SHORTEST_PATH, ISOLATION, PATH, CIRCUIT. Needs an enabled, validated topology. result_types defaults to ["SELECTION"]; use ["AGGREGATED_GEOMETRY"] with out_lines/out_points to write the result to disk."""
    return pro_unet.trace(
        utility_network, trace_type, starting_points, barriers, domain_network,
        tier, None, subnetwork_name, shortest_path_attribute,
        False, False, False, True, True, None, None, None,
        result_types, out_points, out_lines, None, out_json,
    )


@mcp.tool()
def pro_un_subnetwork(
    utility_network: str,
    domain_network: str,
    tier: str,
    action: str = "update",
    subnetwork_name: str | None = None,
    all_in_tier: bool = False,
    out_json: str | None = None,
) -> dict:
    """Subnetwork operations. action: update (recompute so SUBNETWORK traces are current) | export (write the subnetwork to JSON)."""
    if action.lower() == "update":
        return pro_unet.update_subnetwork(
            utility_network, domain_network, tier, subnetwork_name, all_in_tier
        )
    if action.lower() == "export":
        if not (subnetwork_name and out_json):
            return _err("export needs subnetwork_name and out_json.")
        return pro_unet.export_subnetwork(
            utility_network, domain_network, tier, subnetwork_name, out_json
        )
    return _err("action must be 'update' or 'export'.")


# =====================================================================
# Layer 1h — Toolbox authoring: Python toolboxes (.pyt) and ModelBuilder (.atbx)
# =====================================================================


@mcp.tool()
def pro_symbols(font: str = "water") -> dict:
    """The classic Esri AM/FM utility glyphs available for symbology (valve = filled bowtie, hydrant = circle with nozzles, meter = M in a box), plus the standard water-utility colour names."""
    return pro_symbology.list_symbols(font)


@mcp.tool()
def pro_apply_utility_symbology(
    aprx_path: str,
    layer_name: str,
    field: str,
    classes: list[dict],
    map_name: str | None = None,
) -> dict:
    """Symbolise a layer by field value using Esri AM/FM utility glyphs. classes: [{"value":10,"label":"Valve","symbol":"valve","color":"valve_black","size":14}] for points; use "width"/"dash" for lines. Call pro_symbols for the symbol and colour names."""
    return pro_symbology.apply_unique_value_symbology(
        aprx_path, layer_name, field, classes, map_name
    )


@mcp.tool()
def pro_apply_water_utility_symbology(
    aprx_path: str,
    device_layer: str = "Water Device",
    line_layer: str = "Water Line",
    territory_layer: str | None = "ServiceTerritory",
    map_name: str | None = None,
) -> dict:
    """Apply the classic AM/FM water-utility look to a utility network in ONE call: valves as filled bowties, hydrants red circles-with-nozzles, meters an M in a box, mains thick blue, service laterals thin dashed blue."""
    return pro_symbology.apply_water_utility_symbology(
        aprx_path, device_layer, line_layer, territory_layer, map_name
    )


@mcp.tool()
def pro_create_python_toolbox(
    out_path: str,
    label: str,
    tools: list[dict],
    alias: str | None = None,
    description: str | None = None,
    register_in_project: bool = True,
) -> dict:
    """PREFERRED way to turn a workflow into a toolbox tool: writes a working .pyt (handles any tool, multi-input like Intersect, any intermediate) and adds it to the open Pro project. tools: [{"name","label","parameters":[{"name","label","datatype","direction","default"}], "steps":[{"tool":"analysis.Clip","kwargs":{"in_features":"{{param}}","out_feature_class":"{{memory}}"}}] or "execute_code"}]; {{stepN}}=earlier output, {{memory}}=temp. First load shows Pro's 'trust' dialog. See pro_toolbox_datatypes."""
    return pro_toolbox.create_python_toolbox(
        out_path, label, tools, alias, description, register_in_project=register_in_project
    )


@mcp.tool()
def pro_toolbox_datatypes() -> dict:
    """Valid parameter datatypes, parameter types and directions for pro_create_python_toolbox and pro_create_model."""
    return pro_toolbox.list_datatypes()


@mcp.tool()
def pro_create_model(
    out_path: str,
    toolbox_label: str,
    model_name: str,
    steps: list[dict],
    parameters: list[dict] | None = None,
    model_label: str | None = None,
    description: str | None = None,
    allow_unverified: bool = False,
    register_in_project: bool = True,
) -> dict:
    """Write an editable ModelBuilder model (.atbx) - ONLY linear chains of feature-class tools are verified; multi-input tools (Intersect/Union/Merge) and folder/file/layer intermediates are refused (use pro_create_python_toolbox). steps: [{"tool":"analysis.Buffer","params":{"in_features":"{{roads}}"},"output_param":"out_feature_class","output":"{{result}}"}]. {{param}}=declared parameter, {{stepN}}=step N's output. Adds it to the open project."""
    return pro_modelbuilder.create_model(
        out_path, toolbox_label, model_name, steps, parameters, model_label, description,
        allow_unverified=allow_unverified, register_in_project=register_in_project,
    )


@mcp.tool()
def pro_inspect_model(atbx_path: str) -> dict:
    """Read back any .atbx — its tools, parameters and full model graph. Works on Esri's own toolboxes too."""
    return pro_modelbuilder.inspect_model(atbx_path)



# =====================================================================
# Toolset gating
#
# Every registered tool's schema is sent to the model on EVERY request. The full
# set is 112 tools ~= 17.7k tokens, which is real latency on each turn. Most
# sessions never touch the Utility Network or GeoAI, so ARCGIS_SALAH_TOOLSETS
# lets a user keep only what they need:
#
#     ARCGIS_SALAH_TOOLSETS=core,portal,webapp     # ~= 8k tokens
#     ARCGIS_SALAH_TOOLSETS=core,geoai             # imagery work only
#
# Unset (the default) keeps everything, so behaviour is unchanged unless asked.
# =====================================================================

TOOLSETS: dict[str, tuple[str, ...]] = {
    # Always kept: the fast-context tools plus the everyday ArcPy surface. These
    # carry no prefix rule because "core" is defined as whatever is left over.
    "geoai": ("pro_geoai_", "pro_dl_"),
    "unet": ("pro_un_",),
    "schema": (
        "pro_list_domains", "pro_create_domain", "pro_assign_domain",
        "pro_delete_domain", "pro_describe_subtypes", "pro_set_subtypes",
        "pro_describe_attribute_rules", "pro_add_attribute_rule",
        "pro_evaluate_rules", "pro_toggle_attribute_rules",
        "pro_delete_attribute_rule", "pro_apply_schema",
    ),
    "validate": (
        "pro_topology_rules", "pro_describe_topology", "pro_create_topology",
        "pro_validate_topology", "pro_export_topology_errors",
        "pro_check_geometry", "pro_validate_all",
    ),
    "authoring": (
        "pro_create_python_toolbox", "pro_toolbox_datatypes",
        "pro_create_model", "pro_inspect_model",
    ),
    "egdb": ("pro_egdb_",),
    "live": ("live_",),
    "portal": ("portal_",),
    "webapp": ("webapp_",),
}


def _toolset_of(name: str) -> str:
    """Which toolset a tool belongs to; 'core' when it matches no group."""
    for group, patterns in TOOLSETS.items():
        for pattern in patterns:
            if (pattern.endswith("_") and name.startswith(pattern)) or name == pattern:
                return group
    return "core"


def apply_toolset_filter(enabled: str | None = None) -> dict:
    """Prune tools outside the enabled toolsets. 'core' is always kept."""
    raw = enabled if enabled is not None else os.environ.get("ARCGIS_SALAH_TOOLSETS", "")
    if not raw.strip():
        return {"filtered": False, "enabled": ["all"], "tools": len(mcp._tool_manager._tools)}

    keep = {g.strip().lower() for g in raw.split(",") if g.strip()} | {"core"}
    unknown = keep - set(TOOLSETS) - {"core"}
    removed = []
    for name in list(mcp._tool_manager._tools):
        if _toolset_of(name) not in keep:
            mcp._tool_manager.remove_tool(name)
            removed.append(name)
    return {
        "filtered": True,
        "enabled": sorted(keep),
        "unknown_toolsets": sorted(unknown),
        "removed": len(removed),
        "tools": len(mcp._tool_manager._tools),
    }


@mcp.tool()
def pro_toolsets() -> dict:
    """Which tool groups exist and which are active. Set ARCGIS_SALAH_TOOLSETS (e.g. "core,portal,webapp") to shrink the ~17.7k-token tool schema sent every request."""
    counts: dict[str, int] = {}
    for name in mcp._tool_manager._tools:
        group = _toolset_of(name)
        counts[group] = counts.get(group, 0) + 1
    return _ok(
        {
            "active_tools": len(mcp._tool_manager._tools),
            "by_toolset": counts,
            "available_toolsets": ["core"] + sorted(TOOLSETS),
            "env_var": "ARCGIS_SALAH_TOOLSETS",
            "current_setting": os.environ.get("ARCGIS_SALAH_TOOLSETS") or "(unset - all enabled)",
        }
    )



# ---------------------------------------------------------------------------
# Where each tool runs.
#
# FastMCP calls a sync tool function directly on the asyncio event loop, so one long call froze the
# whole server: requests behind it hung, and the client's ~1 minute tool timeout hit them too (reported
# from a real session). Every sync tool is therefore re-pointed at an async runner:
#   * arcpy-backed pro_* tools -> the single arcpy thread (arcpy only works reliably on the thread that
#     imported it - PostgreSQL calls failed with ERROR 87395/87413 elsewhere; see arcpy_thread.py);
#   * everything else (live_*, portal_*, webapp_*, pure generators) -> a regular worker thread.
# The module-level functions are untouched (tests and callers use them directly); only the registered
# Tool objects change. _ORIGINAL_FN keeps the sync functions for pro_run_background.
# ---------------------------------------------------------------------------
_NOT_ARCPY = {"pro_dl_notebook", "pro_create_python_toolbox", "pro_toolbox_datatypes", "pro_create_model",
              "pro_inspect_model", "pro_toolsets", "pro_server_status", "pro_job_status", "pro_run_background"}
_ORIGINAL_FN: dict[str, Any] = {}


def _on_worker_thread(fn: Any) -> Any:
    import asyncio
    import functools

    @functools.wraps(fn)
    async def runner(**kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, **kwargs)

    return runner


def _route_tools() -> None:
    for name, tool in mcp._tool_manager._tools.items():
        if tool.is_async:
            continue
        _ORIGINAL_FN[name] = tool.fn
        if name.startswith("pro_") and name not in _NOT_ARCPY:
            tool.fn = arcpy_thread.make_async(tool.fn, name)
        else:
            tool.fn = _on_worker_thread(tool.fn)
        tool.is_async = True


_route_tools()


def main() -> None:
    """Console-script entry point.

    ``bootstrap.prepare()`` runs first: it re-executes under ``arcgispro-py3``
    when this interpreter has no ArcPy, then starts importing arcpy on a
    background thread. That import was measured at ~25 s, and without the
    warm-up the user's FIRST request pays all of it — which is the single
    biggest reason simple requests used to feel slow. Neither step blocks, so
    the MCP ``initialize`` handshake is unaffected.
    """
    bootstrap.prepare()
    apply_toolset_filter()
    mcp.run()


if __name__ == "__main__":
    main()
