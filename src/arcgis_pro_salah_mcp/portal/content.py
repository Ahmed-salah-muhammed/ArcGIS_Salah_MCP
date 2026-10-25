"""Content: search, items, folders, publishing, sharing and cloning (arcgis 2.4 APIs, no deprecated calls).

Deprecated in 2.3 and removed in 3.0, so NOT used here: ``ContentManager.add`` (-> ``Folder.add``, which
returns a Job), ``Item.share`` (-> ``Item.sharing``), ``ContentManager.create_folder`` (-> ``folders.create``).
Every write re-reads the object afterwards: arcgis return values (None / bool / dict / Job) are not proof.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from .._result import err, guard, ok
from . import connections, safety, serialize
from ._common import folder as _folder, get_item, require_group, sharing_snapshot, username

TOOL = "portal_item"

# extension -> (item type, publish file_type)
_TYPES = {
    ".csv": ("CSV", "csv"), ".txt": ("CSV", "csv"), ".xlsx": ("Microsoft Excel", "excel"),
    ".xls": ("Microsoft Excel", "excel"), ".gpkg": ("GeoPackage", "geopackage"),
    ".geojson": ("GeoJson", "geojson"), ".json": ("GeoJson", "geojson"), ".sd": ("Service Definition", None),
    ".kml": ("KML", None), ".kmz": ("KML", None), ".pdf": ("PDF", None), ".png": ("Image", None),
    ".jpg": ("Image", None), ".jpeg": ("Image", None), ".ipynb": ("Notebook", None),
}
_PUBLISHABLE = {"CSV", "Microsoft Excel", "Shapefile", "File Geodatabase", "GeoPackage", "GeoJson",
                "Service Definition"}
EXPORT_FORMATS = ("File Geodatabase", "Shapefile", "CSV", "GeoJson", "Excel", "KML", "Feature Collection",
                  "Scene Package", "Vector Tile Package", "geoPackage")
_LEVELS = {"private": "PRIVATE", "org": "ORGANIZATION", "organization": "ORGANIZATION", "everyone": "EVERYONE",
           "public": "EVERYONE"}
_UPDATABLE = {"title", "snippet", "description", "tags", "accessInformation", "licenseInfo", "typeKeywords",
              "categories", "extent", "spatialReference", "culture", "url"}


# --- search ------------------------------------------------------------------

@guard
def search(query: str = "", item_type: str | None = None, owner: str | None = None, tags: list[str] | None = None,
           max_items: int = 20, start: int = 1, sort_field: str | None = None, sort_order: str = "asc",
           outside_org: bool = False, connection: str = "default") -> dict:
    gis = connections.get(connection)
    parts = [query.strip()] if query and query.strip() else []
    if owner:
        parts.append(f"owner:{username(gis) if owner.lower() == 'me' else owner}")
    for t in tags or []:
        parts.append(f'tags:"{t}"')
    if not outside_org and connections.is_online(gis) and gis.properties.get("id") and "orgid:" not in query:
        parts.append(f"orgid:{gis.properties['id']}")
    q = " AND ".join(f"({p})" if " OR " in p else p for p in parts) or "*"
    max_items = max(1, min(int(max_items or 20), 1000))
    res = gis.content.advanced_search(query=q, max_items=max_items, start=max(1, int(start or 1)),
                                      sort_field=sort_field or "modified",
                                      sort_order=sort_order or ("desc" if not sort_field else "asc"),
                                      filter=f'type:"{item_type}"' if item_type else None, as_dict=True)
    results = res.get("results", []) if isinstance(res, dict) else list(res or [])
    total = res.get("total") if isinstance(res, dict) else len(results)
    next_start = res.get("nextStart") if isinstance(res, dict) else None
    return ok({"query": q, "total": total, "count": len(results),
               "next_start": next_start if next_start and next_start > 0 else None,
               "items": [serialize.item(r, gis) for r in results]})


# --- one item ----------------------------------------------------------------

def _layers_of(it: Any) -> list[dict]:
    out = []
    for kind in ("layers", "tables"):
        try:
            for i, lyr in enumerate(getattr(it, kind, None) or []):
                props = lyr.properties
                out.append({"kind": kind[:-1], "index": i, "id": props.get("id"), "name": props.get("name"),
                            "geometryType": props.get("geometryType"), "url": lyr.url})
        except Exception:  # noqa: BLE001 - not a layer item / no access
            continue
    return out


def _get(gis: Any, it: Any) -> dict:
    out = serialize.item(it, gis, full=True)
    kw = out.get("typeKeywords") or []
    out["is_hosted"] = "Hosted Service" in kw
    out["is_view"] = "View Service" in kw
    out["sharing"] = sharing_snapshot(it)
    if it.type in ("Feature Service", "Map Service", "Vector Tile Service", "Scene Service", "Image Service"):
        out["layers"] = _layers_of(it)
    return out


def _clean_props(properties: dict) -> tuple[dict, list[str]]:
    props, rejected = {}, []
    for k, v in (properties or {}).items():
        if k not in _UPDATABLE:
            rejected.append(k)
            continue
        if k in ("tags", "typeKeywords", "categories") and isinstance(v, str):
            v = [s.strip() for s in v.split(",") if s.strip()]
        props[k] = v
    return props, rejected


@guard
def item(action: str, item_id: str, properties: dict | None = None, data: Any = None, thumbnail: str | None = None,
         folder: str | None = None, title: str | None = None, export_format: str | None = None,
         export_parameters: dict | None = None, out_folder: str | None = None, target_owner: str | None = None,
         permanent: bool = False, keep_export: bool = False, max_chars: int = 20000, confirm: bool = False,
         connection: str = "default") -> dict:
    action = (action or "get").lower()
    gis = connections.get(connection)
    it = get_item(gis, item_id)

    if action == "get":
        return ok(_get(gis, it))

    if action == "data":
        raw = it.get_data()
        text = json.dumps(raw, default=str, ensure_ascii=False) if not isinstance(raw, str) else raw
        return ok({"item_id": it.id, "type": it.type, "chars": len(text),
                   "data": raw if len(text) <= max_chars else serialize.text(text, max_chars)})

    if action == "update":
        props, rejected = _clean_props(properties or {})
        if not props and data is None and not thumbnail:
            return err("Nothing to update: give properties (title, snippet, description, tags, ...), data or "
                       "thumbnail.", code="nothing_to_do", rejected=rejected)
        before = {k: it.get(k) for k in props}
        payload = data
        if isinstance(data, (dict, list)):
            payload = json.dumps(data)
        result = it.update(item_properties=props or None, data=payload, thumbnail=thumbnail)
        fresh = gis.content.get(it.id)
        after = {k: fresh.get(k) for k in props}
        drift = [k for k in props if after.get(k) != props[k] and k != "tags"]
        safety.audit(TOOL, "update", connection=connection, target=it.id, ok=bool(result) and not drift,
                     before=before, after=after)
        return ok({"item_id": it.id, "updated": bool(result), "before": before, "after": after,
                   "not_applied": drift or None, "rejected_keys": rejected or None,
                   "data_updated": data is not None, "thumbnail_updated": bool(thumbnail)})

    if action == "move":
        target = _folder(gis, folder)
        before = it.get("ownerFolder")
        it.move(target)
        after = gis.content.get(it.id).get("ownerFolder")
        safety.audit(TOOL, "move", connection=connection, target=it.id, ok=True, before=before, after=after)
        return ok({"item_id": it.id, "folder": target.name, "folder_id_before": before, "folder_id_after": after})

    if action == "copy":
        new = it.copy_item(title=title, folder=folder)
        safety.audit(TOOL, "copy", connection=connection, target=it.id, ok=new is not None,
                     after=getattr(new, "id", None))
        return ok({"source_id": it.id, "copy": serialize.item(new, gis)})

    if action in ("protect", "unprotect"):
        it.protect(action == "protect")
        state = gis.content.get(it.id).get("protected")
        safety.audit(TOOL, action, connection=connection, target=it.id, ok=state == (action == "protect"))
        return ok({"item_id": it.id, "protected": state})

    if action == "delete":
        dependents = []
        try:
            dependents = (it.dependent_to() or {}).get("list", [])
        except Exception:  # noqa: BLE001
            pass
        preview = {"item": serialize.item(it, gis), "protected": it.get("protected"),
                   "items_that_use_it": len(dependents), "permanent": permanent}
        if it.get("protected"):
            return err("Item is delete-protected; unprotect it first (portal_item action=unprotect) if the user "
                       "really wants it gone.", code="protected", preview=preview)
        if not confirm:
            return safety.confirm_required(f"Deleting {it.type} '{it.title}'", preview,
                                           hint="Items that use it will break." if dependents else None)
        result = it.delete(permanent=permanent)
        gone = gis.content.get(it.id) is None
        safety.audit(TOOL, "delete", connection=connection, target=it.id, ok=gone, before=preview["item"])
        return ok({"item_id": it.id, "deleted": gone, "result": result}) if gone else \
            err("The portal did not delete the item.", code="not_deleted", result=result)

    if action == "dependencies":
        upon = it.dependent_upon() or {}
        to = it.dependent_to() or {}
        return ok({"item_id": it.id, "uses": upon.get("list", upon), "used_by": to.get("list", to)})

    if action == "download":
        out_folder = out_folder or tempfile.mkdtemp(prefix="portal_dl_")
        path = it.download(save_path=out_folder)
        return ok({"item_id": it.id, "path": path})

    if action == "export":
        if not export_format:
            return err(f"export_format is required: one of {', '.join(EXPORT_FORMATS)}.", code="bad_argument")
        out_folder = out_folder or tempfile.mkdtemp(prefix="portal_export_")
        started = time.perf_counter()
        exported = it.export(title or f"{it.title}_export_{int(time.time())}", export_format,
                             parameters=json.dumps(export_parameters) if export_parameters else None, wait=True)
        path = exported.download(save_path=out_folder)
        export_id = exported.id
        removed = False
        if not keep_export:
            removed = bool(exported.delete(permanent=True))
        safety.audit(TOOL, "export", connection=connection, target=it.id, ok=bool(path),
                     details={"format": export_format, "export_item": export_id, "export_item_deleted": removed})
        return ok({"item_id": it.id, "format": export_format, "path": path, "export_item_id": export_id,
                   "export_item_deleted": removed, "seconds": round(time.perf_counter() - started, 1)})

    if action == "reassign":
        if not target_owner:
            return err("target_owner is required.", code="bad_argument")
        preview = {"item": serialize.item(it, gis), "to_owner": target_owner, "to_folder": folder}
        if not confirm:
            return safety.confirm_required("Reassigning an item to another owner", preview)
        it.reassign_to(target_owner, folder)
        owner = gis.content.get(it.id).owner
        safety.audit(TOOL, "reassign", connection=connection, target=it.id, ok=owner == target_owner,
                     before=preview["item"]["owner"], after=owner)
        return ok({"item_id": it.id, "owner": owner})

    if action == "usage":
        return ok({"item_id": it.id, "usage": serialize.jsonable(it.usage(date_range="30D", as_df=False))})

    return err("action must be get | data | update | move | copy | protect | unprotect | delete | dependencies | "
               "download | export | reassign | usage.", code="bad_action")


@guard
def item_context(item_id: str, connection: str = "default") -> dict:
    """One item as conversation context (backs the arcgis://portal/item/{item_id} resource): its card and
    sharing, plus every layer's fields for a feature service, or the layers of a web map/scene."""
    gis = connections.get(connection)
    it = get_item(gis, item_id)
    out = serialize.item(it, gis, full=True)
    kw = out.get("typeKeywords") or []
    out.update(is_hosted="Hosted Service" in kw, is_view="View Service" in kw, sharing=sharing_snapshot(it))
    if it.type == "Feature Service":
        layers = []
        for kind in ("layers", "tables"):
            for i, lyr in enumerate(list(getattr(it, kind, None) or [])[:10]):
                props = lyr.properties
                layers.append({
                    "kind": kind[:-1], "index": i, "name": props.get("name"), "url": lyr.url,
                    "geometryType": props.get("geometryType"), "objectIdField": props.get("objectIdField"),
                    "fields": [{"name": f.get("name"), "type": str(f.get("type", "")).replace("esriFieldType", ""),
                                "alias": f.get("alias")} for f in props.get("fields") or []]})
        out["layers"] = layers
    elif it.type in ("Web Map", "Web Scene"):
        from .maps import _summary

        out["map"] = _summary(it.get_data() or {})
    return ok(out)


# --- folders -----------------------------------------------------------------

@guard
def folders(action: str = "list", name: str | None = None, new_name: str | None = None, owner: str | None = None,
            confirm: bool = False, connection: str = "default") -> dict:
    action = (action or "list").lower()
    gis = connections.get(connection)
    fm = gis.content.folders
    if action == "list":
        found = list(fm.list(owner=owner) if owner else fm.list())
        return ok({"folders": [{"name": f.name, "id": (f.properties or {}).get("id")} for f in found]})
    if not name:
        return err("name is required.", code="bad_argument")
    if action == "create":
        existing = fm.get(name, owner=owner) if owner else fm.get(name)
        f = existing or (fm.create(name, owner=owner, exist_ok=True) if owner else fm.create(name, exist_ok=True))
        if existing is None:
            safety.audit("portal_folders", "create", connection=connection, target=name, ok=f is not None)
        return ok({"name": f.name, "id": (f.properties or {}).get("id"), "created": existing is None})
    f = _folder(gis, name, owner=owner)
    if action == "rename":
        if not new_name:
            return err("new_name is required.", code="bad_argument")
        done = f.rename(new_name)
        safety.audit("portal_folders", "rename", connection=connection, target=name, ok=bool(done), after=new_name)
        return ok({"renamed": bool(done), "name": new_name})
    if action == "delete":
        contents = list(f.list())
        preview = {"folder": f.name, "items_inside": len(contents),
                   "sample": [c.get("title") for c in contents[:10]]}
        if not confirm:
            return safety.confirm_required(f"Deleting folder '{f.name}'", preview,
                                           hint="Items inside are deleted too." if contents else None)
        done = f.delete()
        safety.audit("portal_folders", "delete", connection=connection, target=name, ok=bool(done), before=preview)
        return ok({"deleted": bool(done), "folder": name})
    return err("action must be list | create | rename | delete.", code="bad_action")


# --- publish -----------------------------------------------------------------

def _zip_dir(src: Path, suffix: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="portal_up_"))
    out = tmp / f"{src.stem}{suffix}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file() and not p.name.endswith(".lock"):
                zf.write(p, Path(src.name) / p.relative_to(src))
    return out


def _zip_shapefile(shp: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="portal_up_"))
    out = tmp / f"{shp.stem}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in shp.parent.glob(shp.stem + ".*"):
            if p.suffix.lower() != ".lock":
                zf.write(p, p.name)
    return out


def _export_feature_class(fc: str) -> Path:
    """A dataset inside a .gdb/.sde -> a zipped file geodatabase (via arcpy on the arcpy thread)."""
    from .. import arcpy_thread

    def work() -> Path:
        import arcpy  # type: ignore

        tmp = Path(tempfile.mkdtemp(prefix="portal_fc_"))
        name = re.sub(r"\W", "_", Path(fc).name.split(".")[-1]) or "data"
        gdb = arcpy.management.CreateFileGDB(str(tmp), f"{name}.gdb")[0]
        arcpy.conversion.ExportFeatures(fc, os.path.join(gdb, name))
        return _zip_dir(Path(gdb), "")

    return arcpy_thread.run(work)


def _prepare_upload(path: str) -> tuple[Path, str, str | None, str | None]:
    """-> (file to upload, item type, publish file_type, note)."""
    p = Path(path)
    lower = str(p).lower()
    if p.is_dir() and p.suffix.lower() == ".gdb":
        return _zip_dir(p, ""), "File Geodatabase", "fileGeodatabase", "zipped the .gdb folder"
    if ".gdb" + os.sep in lower or ".gdb/" in lower or ".sde" + os.sep in lower or ".sde/" in lower:
        return _export_feature_class(path), "File Geodatabase", "fileGeodatabase", \
            "exported the feature class to a zipped file geodatabase"
    if not p.exists():
        raise FileNotFoundError(f"{path} does not exist.")
    ext = p.suffix.lower()
    if ext == ".shp":
        return _zip_shapefile(p), "Shapefile", "shapefile", "zipped the shapefile and its sidecar files"
    if ext == ".zip":
        with zipfile.ZipFile(p) as zf:
            names = [n.lower() for n in zf.namelist()]
        if any(".gdb/" in n for n in names):
            return p, "File Geodatabase", "fileGeodatabase", None
        if any(n.endswith(".shp") for n in names):
            return p, "Shapefile", "shapefile", None
        raise ValueError("The zip holds neither a .gdb nor a .shp; give item_type explicitly.")
    if ext in _TYPES:
        item_type, file_type = _TYPES[ext]
        return p, item_type, file_type, None
    raise ValueError(f"Cannot infer the item type of '{p.name}'; pass item_type (e.g. 'CSV', 'Shapefile').")


def _count_rows(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except Exception:  # noqa: BLE001
        return None


def _service_name(gis: Any, title: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", title).strip("_")[:80] or "layer"
    name = base
    for i in range(2, 50):
        try:
            if gis.content.is_service_name_available(name, "featureService"):
                return name
        except Exception:  # noqa: BLE001
            return name
        name = f"{base}_{i}"
    return f"{base}_{int(time.time())}"


@guard
def publish(path: str, title: str, item_type: str | None = None, tags: list[str] | None = None,
            folder: str | None = None, snippet: str | None = None, description: str | None = None,
            publish: bool = True, latitude_field: str | None = None, longitude_field: str | None = None,
            address_fields: dict | None = None, publish_parameters: dict | None = None, confirm: bool = False,
            connection: str = "default") -> dict:
    from arcgis.gis import ItemProperties  # type: ignore

    gis = connections.get(connection)
    started = time.perf_counter()
    upload, inferred_type, file_type, note = _prepare_upload(path)
    item_type = item_type or inferred_type
    warnings: list[str] = [note] if note else []
    me = username(gis)
    try:
        clash = gis.content.search(f'title:"{title}" AND owner:{me}', max_items=5)
        same = [c.id for c in clash if c.title == title]
        if same:
            warnings.append(f"you already own item(s) titled '{title}': {', '.join(same)} (titles are not unique)")
    except Exception:  # noqa: BLE001
        pass

    pp = dict(publish_parameters or {})
    estimate = None
    if publish and item_type in ("CSV", "Microsoft Excel"):
        analyzed = gis.content.analyze(file_path=str(upload), file_type=file_type) or {}
        pp = {**(analyzed.get("publishParameters") or {}), **pp}
        if latitude_field and longitude_field:
            pp.update(locationType="coordinates", latitudeFieldName=latitude_field,
                      longitudeFieldName=longitude_field)
        elif address_fields:
            pp.update(locationType="address", addressFields=address_fields)
        if pp.get("locationType") == "address" and connections.is_online(gis):
            rows = _count_rows(upload) or 0
            estimate = round(rows * 0.04, 2)  # ArcGIS Online: 40 credits per 1,000 geocoded rows
            gate = safety.credit_gate(estimate, confirm, f"Publishing {rows} rows by address (geocoding)", True)
            if gate:
                return gate
        pp["name"] = _service_name(gis, title)

    props = ItemProperties(title=title, item_type=item_type, tags=tags or ["arcgis-pro-salah-mcp"],
                           snippet=snippet or "Uploaded by ArcGIS Pro Salah MCP", description=description)
    target = _folder(gis, folder, create=True)
    src = target.add(props, file=str(upload)).result()
    out: dict = {"source_item": serialize.item(src, gis), "warnings": warnings or None}
    if publish:
        if item_type not in _PUBLISHABLE:
            out["published"] = None
            out["warnings"] = (warnings or []) + [f"{item_type} items are uploaded, not published."]
        else:
            kwargs: dict = {}
            if pp:
                kwargs["publish_parameters"] = pp
            if file_type and item_type in ("CSV", "Microsoft Excel"):
                kwargs["file_type"] = file_type
            published = src.publish(**kwargs)
            out["published_item"] = serialize.item(published, gis)
            out["layers"] = _layers_of(published)
            if estimate:
                out["estimated_credits"] = estimate
    out["seconds"] = round(time.perf_counter() - started, 1)
    safety.audit("portal_publish", "publish" if publish else "upload", connection=connection, target=str(path),
                 ok=True, after={"source": src.id, "published": (out.get("published_item") or {}).get("id")})
    return ok(out)


# --- sharing -----------------------------------------------------------------

@guard
def share(item_ids: list[str], level: str | None = None, add_groups: list[str] | None = None,
          remove_groups: list[str] | None = None, confirm: bool = False, connection: str = "default") -> dict:
    if isinstance(item_ids, str):
        item_ids = [item_ids]
    if not item_ids:
        return err("item_ids is required.", code="bad_argument")
    target_level = None
    if level:
        target_level = _LEVELS.get(level.lower())
        if target_level is None:
            return err("level must be private | org | everyone.", code="bad_argument")
    gis = connections.get(connection)
    cache: dict = {}
    add = [require_group(gis, g, cache) for g in (add_groups or [])]
    remove = [require_group(gis, g, cache) for g in (remove_groups or [])]
    items = [get_item(gis, i) for i in item_ids]
    if target_level == "EVERYONE" and not confirm:
        return safety.confirm_required(f"Sharing {len(items)} item(s) with EVERYONE (public)",
                                       {"items": [serialize.item(i) for i in items]},
                                       hint="Anyone on the internet will be able to open them.")
    from arcgis.gis import SharingLevel  # type: ignore

    results = []
    for it in items:
        before = sharing_snapshot(it)
        sm = it.sharing
        if target_level:
            sm.sharing_level = {"PRIVATE": SharingLevel.PRIVATE, "ORGANIZATION": SharingLevel.ORG,
                                "EVERYONE": SharingLevel.EVERYONE}[target_level]
        group_errors = []
        for g in add:
            if not sm.groups.add(g):
                group_errors.append(f"could not share with '{g.title}'")
        for g in remove:
            if not sm.groups.remove(g):
                group_errors.append(f"could not unshare from '{g.title}'")
        after = sharing_snapshot(gis.content.get(it.id))
        ok_level = target_level is None or after["level"] == target_level
        results.append({"item_id": it.id, "title": it.title, "before": before, "after": after,
                        "ok": ok_level and not group_errors, "errors": group_errors or None})
        safety.audit("portal_share", "share", connection=connection, target=it.id, ok=results[-1]["ok"],
                     before=before, after=after)
    shared_update = [g.title for g in add if "updateitemcontrol" in (g.get("capabilities") or [])]
    return ok({"results": results, "all_ok": all(r["ok"] for r in results),
               "shared_update_groups": shared_update or None})


# --- clone -------------------------------------------------------------------

@guard
def clone(item_ids: list[str], folder: str | None = None, target_connection: str = "default",
          search_existing_items: bool = True, copy_data: bool = True, item_mapping: dict | None = None,
          confirm: bool = False, connection: str = "default") -> dict:
    if isinstance(item_ids, str):
        item_ids = [item_ids]
    src_gis = connections.get(connection)
    dst_gis = connections.get(target_connection)
    items = [get_item(src_gis, i) for i in item_ids]
    deps = {}
    for it in items:
        try:
            deps[it.id] = [d.get("id") or d.get("url") for d in (it.dependent_upon() or {}).get("list", [])]
        except Exception:  # noqa: BLE001
            deps[it.id] = []
    preview = {"from": str(src_gis.url), "to": str(dst_gis.url), "folder": folder,
               "items": [serialize.item(i) for i in items], "they_use": deps,
               "search_existing_items": search_existing_items, "copy_data": copy_data}
    if not confirm:
        return safety.confirm_required(f"Cloning {len(items)} item(s) (dependencies come along)", preview)
    started = time.perf_counter()
    created = dst_gis.content.clone_items(items, folder=folder, search_existing_items=search_existing_items,
                                          copy_data=copy_data, item_mapping=item_mapping)
    out = [serialize.item(c, dst_gis) for c in (created or [])]
    safety.audit("portal_clone", "clone", connection=f"{connection}->{target_connection}", target=item_ids,
                 ok=bool(out), after=[o["id"] for o in out])
    return ok({"created": out, "count": len(out), "seconds": round(time.perf_counter() - started, 1),
               "note": None if out else "Nothing new: with search_existing_items=True, items already cloned "
                                        "before are reused."})
