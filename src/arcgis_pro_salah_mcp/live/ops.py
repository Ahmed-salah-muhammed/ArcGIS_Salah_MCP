"""Live-session operations (Layer 1b).

Thin forwarders to the .NET bridge over loopback HTTP. Each function maps 1:1 to
a command in ``docs/PROTOCOL.md`` and just posts it through :mod:`live.client`;
the add-in does the real work inside ArcGIS Pro on the Main CIM Thread. The
``@guard`` decorator keeps the ``{"ok": ...}`` envelope contract even if
something throws locally before the request is sent.
"""
from __future__ import annotations

from typing import Any

from .._result import err, guard
from . import client, policy


@guard
def live_ping() -> dict:
    """Liveness + context of the open Pro session (project, maps, layouts)."""
    return client.post("ping")


@guard
def live_list_layers(map_name: str | None = None) -> dict:
    """List layers in the active (or named) map of the open session."""
    return client.post("list_layers", {"map": map_name})


@guard
def live_zoom_to(layer: str, selection: bool = False) -> dict:
    """Zoom the active view to a layer (or its current selection)."""
    return client.post("zoom_to", {"layer": layer, "selection": selection})


@guard
def live_query(
    layer: str,
    where: str | None = None,
    fields: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """Read attribute rows from a layer in the open session."""
    return client.post(
        "query",
        {"layer": layer, "where": where, "fields": fields, "limit": limit},
    )


@guard
def live_run_gp(
    tool: str,
    args: list[Any] | None = None,
    kwargs: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Run a geoprocessing tool live; the output is added to the active map.

    Destructive: blocked in read-only mode and requires ``confirm=True``.
    """
    blocked = policy.check("run_gp", confirm)
    if blocked:
        return blocked
    return client.post(
        "run_gp",
        {"tool": tool, "args": args or [], "kwargs": kwargs or {}},
        timeout=900,  # a geoprocessing tool can run far longer than the bridge's 30 s default
    )


@guard
def live_add_layer(path: str) -> dict:
    """Add a dataset from disk to the active map of the open session."""
    blocked = policy.check("add_layer")
    if blocked:
        return blocked
    return client.post("add_layer", {"path": path})


@guard
def live_export_layout(layout: str, out_path: str, dpi: int = 300) -> dict:
    """Export a layout from the open session to a PDF."""
    blocked = policy.check("export_layout")
    if blocked:
        return blocked
    return client.post(
        "export_layout",
        {"layout": layout, "out_path": out_path, "dpi": dpi},
    )


@guard
def live_get_request(clear: bool = False) -> dict:
    """Fetch the request the user queued from the Salah MCP ribbon.

    Returns ``{pending, kind, text, created_at}`` where ``kind`` is ``web_app`` or
    ``publish`` and ``text`` is what the user typed / the publish details. Pass
    ``clear=True`` to consume it once handled.
    """
    return client.post("get_request", {"clear": clear})


@guard
def live_add_toolbox(path: str) -> dict:
    """Add an existing ``.atbx`` / ``.tbx`` / ``.pyt`` to the OPEN project's Toolboxes.

    Uses the public SDK route (``ItemFactory.Create`` + ``Project.AddItem``) and VERIFIES the
    result, so a model written by ``pro_create_model`` shows up in Catalog > Toolboxes and the
    Geoprocessing pane. Needs add-in 0.1.4+; ``steps`` lists what was tried.
    """
    blocked = policy.check("add_toolbox")
    if blocked:
        return blocked
    return client.post("add_toolbox", {"path": path})


@guard
def live_add_database(path: str) -> dict:
    """Add an existing ``.sde`` connection file (or ``.gdb``) to the OPEN project's Databases.

    Public SDK route (``ItemFactory.Create`` + ``Project.AddItem``), verified against the project's
    database items. Needs add-in 0.1.5+; ``steps`` lists what was tried.
    """
    blocked = policy.check("add_database")
    if blocked:
        return blocked
    return client.post("add_database", {"path": path})



# --- add-in 0.1.7: the selection, a layer's display, and edits through Pro's own edit session ------------

@guard
def live_selection(action: str = "get", layer: str | None = None, where: str | None = None, method: str = "new",
                   fields: list[str] | None = None, limit: int = 50, map_name: str | None = None) -> dict:
    """What is selected in the open map (get: counts + ids per layer, rows of ``layer``), select by ``where``
    in ``layer`` (method new|add|remove|and), or clear (one layer or the map). Changes no data."""
    if action not in ("get", "select", "clear"):
        return err("action must be get | select | clear.", code="bad_action")
    if action == "select" and not layer:
        return err("select needs layer (and where; an empty where selects every feature).", code="bad_argument")
    return client.post("selection", {"action": action, "layer": layer, "where": where, "method": method,
                                     "fields": fields or [], "limit": limit, "map": map_name})


@guard
def live_layer_info(layer: str, map_name: str | None = None) -> dict:
    """A layer of the open map: geometry, source, counts, fields, definition query, renderer, labels."""
    return client.post("layer_info", {"layer": layer, "map": map_name})


@guard
def live_layer_set(layer: str, definition_query: str | None = None, visible: bool | None = None,
                   transparency: float | None = None, labels: dict | None = None, renderer: dict | None = None,
                   name: str | None = None, map_name: str | None = None, remove: bool = False) -> dict:
    """Change how a layer of the OPEN map is shown (never its data): definition query ("" clears it),
    visibility, transparency 0-100, labels {visible, expression (Arcade)}, renderer {type simple|unique|
    graduated, field, color [r,g,b], to_color, classes, size}, name; or remove it from the map."""
    blocked = policy.check("layer_set")
    if blocked:
        return blocked
    if remove:
        return client.post("layer_set", {"layer": layer, "map": map_name, "remove": True})
    params: dict = {"layer": layer, "map": map_name}
    if definition_query is not None:
        params["definition_query"] = str(definition_query)
    if visible is not None:
        params["visible"] = bool(visible)
    if transparency is not None:
        if not 0 <= float(transparency) <= 100:
            return err("transparency is 0 (opaque) to 100 (invisible).", code="bad_argument")
        params["transparency"] = float(transparency)
    if labels is not None:
        params["labels"] = labels
    if renderer is not None:
        kind = str(renderer.get("type", "simple")).lower()
        if kind not in ("simple", "unique", "graduated"):
            return err("renderer type must be simple | unique | graduated.", code="bad_argument")
        if kind != "simple" and not renderer.get("field"):
            return err(f"a {kind} renderer needs field.", code="bad_argument")
        params["renderer"] = {**renderer, "type": kind}
    if name:
        params["name"] = name
    if len(params) == 2:
        return err("give definition_query, visible, transparency, labels, renderer or name.", code="bad_argument")
    return client.post("layer_set", params)


@guard
def live_edit_features(layer: str, adds: list[dict] | None = None, updates: list[dict] | None = None,
                       deletes: list[int] | None = None, save: bool = False, confirm: bool = False,
                       map_name: str | None = None, name: str | None = None) -> dict:
    """Add / update / delete rows of a layer of the open map in ONE Pro edit operation (undoable, inside the
    user's edit session; the layer stays in the Contents pane). Deleting rows, and ``save`` (which saves the
    WHOLE edit session), need confirm=True."""
    blocked = policy.check("edit_features")
    if blocked:
        return blocked
    adds, updates, deletes = adds or [], updates or [], deletes or []
    if not (adds or updates or deletes):
        return err("give adds, updates or deletes.", code="bad_argument")
    for u in updates:
        if not isinstance(u, dict) or not isinstance(u.get("object_id"), int):
            return err("every update needs object_id (a number) plus attributes and/or geometry.", code="bad_argument")
    if deletes:
        refused = policy.needs_confirm(
            f"This deletes {len(deletes)} row(s) of '{layer}' in the open ArcGIS Pro session", confirm)
        if refused:
            return refused
    if save:
        refused = policy.needs_confirm("save=True saves EVERY unsaved edit of the open ArcGIS Pro session, "
                                       "the user's own included", confirm)
        if refused:
            return refused
    return client.post("edit_features", {"layer": layer, "map": map_name, "adds": adds, "updates": updates,
                                         "deletes": deletes, "save": save, "name": name}, timeout=300)


@guard
def live_edit_session(action: str = "status", confirm: bool = False, map_name: str | None = None) -> dict:
    """The open project's edit session: status | undo | redo | save | discard. save and discard act on every
    unsaved edit (the user's too) and need confirm=True."""
    if action not in ("status", "undo", "redo", "save", "discard"):
        return err("action must be status | undo | redo | save | discard.", code="bad_action")
    if action != "status":
        blocked = policy.check("edit_session")
        if blocked:
            return blocked
    if action in ("save", "discard"):
        what = "saves" if action == "save" else "throws away"
        refused = policy.needs_confirm(f"This {what} EVERY unsaved edit of the open ArcGIS Pro session, "
                                       "the user's own included", confirm)
        if refused:
            return refused
    return client.post("edit_session", {"action": action, "map": map_name}, timeout=300)
