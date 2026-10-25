"""Web maps (JSON, fast) and web scenes (arcgis.map.Scene).

Web maps are written as plain web-map JSON through ``Folder.add(text=...)``: no ``arcgis.map`` import
(~11 s cold), nothing version-sensitive, and every layer, popup and renderer is explicit. Basemaps
come from the org itself: its default basemap, or a title from its basemap gallery group.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .. import __version__
from .._result import err, guard, ok
from . import _imports, connections, safety, serialize
from ._common import ID_RE, folder as _folder, get_item
from .layers import build_renderer

BASEMAP_ALIASES = {
    "topo-vector": "topographic", "topo": "topographic", "streets-vector": "streets", "streets": "streets",
    "satellite": "imagery", "imagery": "imagery", "hybrid": "imagery hybrid", "gray-vector": "light gray canvas",
    "gray": "light gray canvas", "dark-gray-vector": "dark gray canvas", "dark-gray": "dark gray canvas",
    "oceans": "oceans", "osm": "openstreetmap", "terrain": "terrain with labels", "navigation": "navigation",
    "streets-night-vector": "streets (night)", "national-geographic": "national geographic style map",
}
_BASEMAP_CACHE: dict[tuple[str, str], dict] = {}
_SYSTEM_FIELDS = {"objectid", "fid", "globalid", "shape", "shape__area", "shape__length", "shape_area",
                  "shape_length", "created_user", "created_date", "last_edited_user", "last_edited_date",
                  "creationdate", "creator", "editdate", "editor"}


def _basemap(gis: Any, name: str | None) -> dict:
    key = (str(gis.url), (name or "default").lower())
    if key in _BASEMAP_CACHE:
        return _BASEMAP_CACHE[key]
    props = gis.properties
    if not name or name.lower() == "default":
        bm = dict(props.get("defaultBasemap") or {})
        if not bm.get("baseMapLayers"):
            raise LookupError("The org has no default basemap; pass basemap= a title from its basemap gallery.")
        out = {"title": bm.get("title", "Basemap"), "baseMapLayers": bm["baseMapLayers"]}
        _BASEMAP_CACHE[key] = out
        return out
    wanted = BASEMAP_ALIASES.get(name.lower(), name.lower())
    query = props.get("vectorBasemapGalleryGroupQuery") if props.get("useVectorBasemaps") else None
    query = query or props.get("basemapGalleryGroupQuery")
    titles: list[str] = []
    for grp in gis.groups.search(query, outside_org=True, max_groups=3) if query else []:
        for it in grp.content(max_items=200):
            if it.type != "Web Map":
                continue
            titles.append(it.title)
            if it.title.lower() == wanted:
                data = it.get_data() or {}
                bm = data.get("baseMap") or {}
                out = {"title": bm.get("title", it.title), "baseMapLayers": bm.get("baseMapLayers", [])}
                _BASEMAP_CACHE[key] = out
                return out
    raise LookupError(f"Basemap '{name}' not in the org's gallery. Available: {', '.join(sorted(set(titles))) or 'none'}"
                      " (or omit basemap for the org default).")


def _popup(props: Any, title_field: str | None = None, fields: list[str] | None = None) -> dict:
    infos = []
    for f in props.get("fields") or []:
        fname = f.get("name")
        visible = fname in fields if fields else fname.lower() not in _SYSTEM_FIELDS
        infos.append({"fieldName": fname, "label": f.get("alias") or fname, "visible": visible})
    title = title_field or props.get("displayField") or (fields[0] if fields else None)
    return {"title": "{" + title + "}" if title else props.get("name", ""), "fieldInfos": infos,
            "popupElements": [{"type": "fields"}]}


def _layers_from(gis: Any, ref: str, popups: bool) -> tuple[list[dict], list | None]:
    """Operational-layer JSON for an item id or a layer URL -> (layers, wgs84 extent or None)."""
    from arcgis.features import FeatureLayer  # type: ignore

    ref = str(ref).strip()
    if not ID_RE.match(ref):
        lyr = FeatureLayer(ref, gis=gis)
        props = lyr.properties
        out = {"id": f"lyr_{abs(hash(ref)) % 10 ** 8}", "title": props.get("name"), "url": ref,
               "layerType": "ArcGISFeatureLayer", "visibility": True, "opacity": 1}
        if popups:
            out["popupInfo"] = _popup(props)
        return [out], None
    it = get_item(gis, ref)
    ext = it.get("extent") or None
    if it.type == "Feature Service":
        subs = list(getattr(it, "layers", None) or []) + list(getattr(it, "tables", None) or [])
        out = []
        for i, lyr in enumerate(subs):
            props = lyr.properties
            entry = {"id": f"{it.id[:8]}_{i}", "title": props.get("name") if len(subs) > 1 else it.title,
                     "url": lyr.url, "itemId": it.id, "visibility": True, "opacity": 1,
                     "layerType": "ArcGISFeatureLayer"}
            if popups:
                entry["popupInfo"] = _popup(props)
            out.append(entry)
        return out, ext
    kinds = {"Map Service": "ArcGISMapServiceLayer", "Image Service": "ArcGISImageServiceLayer",
             "Vector Tile Service": "VectorTileLayer", "WMS": "WMS"}
    if it.type not in kinds:
        raise ValueError(f"{it.type} items cannot be added to a web map here (feature, map, image, vector tile).")
    entry = {"id": it.id[:12], "title": it.title, "itemId": it.id, "layerType": kinds[it.type], "visibility": True,
             "opacity": 1}
    if it.type == "Vector Tile Service":
        entry["styleUrl"] = f"{it.url}/resources/styles/root.json"
    else:
        entry["url"] = it.url
    return [entry], ext


def _union(exts: list) -> list | None:
    exts = [e for e in exts if e and len(e) == 2]
    if not exts:
        return None
    return [[min(e[0][0] for e in exts), min(e[0][1] for e in exts)],
            [max(e[1][0] for e in exts), max(e[1][1] for e in exts)]]


def _find(layers: list[dict], ref: Any) -> int:
    if isinstance(ref, int) or str(ref).isdigit():
        idx = int(ref)
        if 0 <= idx < len(layers):
            return idx
        raise IndexError(f"layer index {idx} out of range ({len(layers)} layers).")
    for i, lyr in enumerate(layers):
        if str(ref).lower() in (str(lyr.get("title", "")).lower(), str(lyr.get("id", "")).lower(),
                                str(lyr.get("url", "")).lower()):
            return i
    raise LookupError(f"No layer '{ref}' in the map: " + ", ".join(str(lyr.get("title")) for lyr in layers))


def _apply_renderer(gis: Any, layer_json: dict, spec: dict) -> None:
    from arcgis.features import FeatureLayer  # type: ignore

    renderer = build_renderer(FeatureLayer(layer_json["url"], gis=gis), spec)
    layer_json.setdefault("layerDefinition", {}).setdefault("drawingInfo", {})["renderer"] = renderer


def _summary(data: dict) -> dict:
    layers = []
    for lyr in data.get("operationalLayers") or []:
        drawing = (lyr.get("layerDefinition") or {}).get("drawingInfo") or {}
        layers.append({"id": lyr.get("id"), "title": lyr.get("title"), "type": lyr.get("layerType"),
                       "url": lyr.get("url"), "itemId": lyr.get("itemId"), "visible": lyr.get("visibility"),
                       "popup": bool(lyr.get("popupInfo")) and not lyr.get("disablePopup"),
                       "renderer": (drawing.get("renderer") or {}).get("type"),
                       "definitionExpression": (lyr.get("layerDefinition") or {}).get("definitionExpression")})
    return {"basemap": (data.get("baseMap") or {}).get("title"), "layers": layers,
            "tables": [{"title": t.get("title"), "url": t.get("url")} for t in data.get("tables") or []]}


@guard
def webmap(action: str = "describe", item_id: str | None = None, title: str | None = None,
           layers: list[str | int] | None = None, basemap: str | None = None, tags: list[str] | None = None,
           folder: str | None = None, snippet: str | None = None, popups: bool = True,
           renderer: dict | None = None, layer: str | int | None = None, popup_fields: list[str] | None = None,
           popup_title: str | None = None, definition_expression: str | None = None,
           connection: str = "default") -> dict:
    action = (action or "describe").lower()
    gis = connections.get(connection)

    if action in ("create", "create_scene"):
        if not (title and layers):
            return err("title and layers (item ids or layer URLs) are required.", code="bad_argument")
        if action == "create_scene":
            return _create_scene(gis, title, layers, tags, folder, snippet, connection)
        started = time.perf_counter()
        op_layers, exts = [], []
        for ref in layers:
            built, ext = _layers_from(gis, ref, popups)
            op_layers.extend(built)
            exts.append(ext)
        if renderer:
            target = op_layers[_find(op_layers, layer if layer is not None else 0)]
            _apply_renderer(gis, target, renderer)
        if definition_expression:
            target = op_layers[_find(op_layers, layer if layer is not None else 0)]
            target.setdefault("layerDefinition", {})["definitionExpression"] = definition_expression
        data = {"operationalLayers": op_layers, "baseMap": _basemap(gis, basemap),
                "spatialReference": {"wkid": 102100, "latestWkid": 3857}, "version": "2.31",
                "authoringApp": "ArcGIS Pro Salah MCP", "authoringAppVersion": __version__}
        from arcgis.gis import ItemProperties  # type: ignore

        extent = _union(exts)
        props = ItemProperties(title=title, item_type="Web Map", tags=tags or ["arcgis-pro-salah-mcp"],
                               snippet=snippet or "Created by ArcGIS Pro Salah MCP",
                               extent=",".join(str(v) for pair in extent for v in pair) if extent else None,
                               type_keywords=["ArcGIS Online", "Explorer Web Map", "Map", "Online Map", "Web Map"])
        new = _folder(gis, folder, create=True).add(props, text=json.dumps(data)).result()
        saved = new.get_data() or {}
        safety.audit("portal_map", "create", connection=connection, target=new.id,
                     ok=len(saved.get("operationalLayers") or []) == len(op_layers))
        return ok({"webmap": serialize.item(new, gis), **_summary(saved),
                   "seconds": round(time.perf_counter() - started, 1)})

    if not item_id:
        return err("item_id (the web map) is required.", code="bad_argument")
    it = get_item(gis, item_id)
    if it.type not in ("Web Map", "Web Scene"):
        return err(f"Item is a {it.type}, not a Web Map.", code="bad_argument")
    data = it.get_data() or {}
    if action == "describe":
        return ok({"webmap": serialize.item(it, gis), **_summary(data)})

    ops = data.setdefault("operationalLayers", [])
    if action == "add_layers":
        if not layers:
            return err("layers (item ids or layer URLs) is required.", code="bad_argument")
        for ref in layers:
            ops.extend(_layers_from(gis, ref, popups)[0])
        if renderer:
            _apply_renderer(gis, ops[_find(ops, layer if layer is not None else len(ops) - 1)], renderer)
    elif action == "remove_layers":
        if not layers:
            return err("layers (titles, ids, urls or indexes to remove) is required.", code="bad_argument")
        for idx in sorted({_find(ops, ref) for ref in layers}, reverse=True):  # resolve first, then pop
            ops.pop(idx)
    elif action == "popup":
        if layer is None:
            return err("layer (title, id or index) is required.", code="bad_argument")
        from arcgis.features import FeatureLayer  # type: ignore

        target = ops[_find(ops, layer)]
        target["popupInfo"] = _popup(FeatureLayer(target["url"], gis=gis).properties, popup_title, popup_fields)
        target.pop("disablePopup", None)
    elif action == "renderer":
        if layer is None or not renderer:
            return err("layer and renderer are required.", code="bad_argument")
        _apply_renderer(gis, ops[_find(ops, layer)], renderer)
    elif action == "filter":
        if layer is None:
            return err("layer is required (definition_expression empty clears the filter).", code="bad_argument")
        target = ops[_find(ops, layer)]
        if definition_expression:
            target.setdefault("layerDefinition", {})["definitionExpression"] = definition_expression
        else:
            (target.get("layerDefinition") or {}).pop("definitionExpression", None)
    elif action == "basemap":
        data["baseMap"] = _basemap(gis, basemap)
    else:
        return err("action must be create | create_scene | describe | add_layers | remove_layers | popup | "
                   "renderer | filter | basemap.", code="bad_action")
    done = it.update(data=json.dumps(data))
    saved = gis.content.get(it.id).get_data() or {}
    safety.audit("portal_map", action, connection=connection, target=it.id, ok=bool(done))
    return ok({"webmap": serialize.item(it, gis), "updated": bool(done), **_summary(saved)})


def _create_scene(gis: Any, title: str, layers: list[str], tags: list[str] | None, folder: str | None,
                  snippet: str | None, connection: str) -> dict:
    Scene = _imports.load("arcgis.map").Scene

    started = time.perf_counter()
    scene = Scene(gis=gis)
    for ref in layers:
        scene.content.add(get_item(gis, ref) if ID_RE.match(str(ref)) else ref)
    if folder:
        _folder(gis, folder, create=True)
    new = scene.save(item_properties={"title": title, "tags": ",".join(tags or ["arcgis-pro-salah-mcp"]),
                                      "snippet": snippet or "Created by ArcGIS Pro Salah MCP"}, folder=folder)
    safety.audit("portal_map", "create_scene", connection=connection, target=getattr(new, "id", None),
                 ok=new is not None)
    return ok({"webscene": serialize.item(new, gis), "seconds": round(time.perf_counter() - started, 1)})
