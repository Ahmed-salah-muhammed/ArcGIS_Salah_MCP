"""Live-session operations (Layer 1b).

Thin forwarders to the .NET bridge over loopback HTTP. Each function maps 1:1 to
a command in ``docs/PROTOCOL.md`` and just posts it through :mod:`live.client`;
the add-in does the real work inside ArcGIS Pro on the Main CIM Thread. The
``@guard`` decorator keeps the ``{"ok": ...}`` envelope contract even if
something throws locally before the request is sent.
"""
from __future__ import annotations

from typing import Any

from .._result import guard
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
