"""Hosted feature layers: describe, query, edit, calculate, append, truncate, schema, views, renderers.

Facts this module is built on (see the portal-layers guide):
* Only HOSTED services support ``manager`` admin operations; a view's schema is changed on its source.
* ``query`` defaults to ``return_all_records=True`` (auto-paging every row) - always page explicitly here.
* Updates need the object-id (or global-id) field; every ``edit_features`` result is checked for success.
* Dates are epoch milliseconds in UTC; rows are returned with ISO strings for readability.
* ``drawingInfo`` is replaced as a whole by ``update_definition``, so renderer and labels are merged
  with the current drawingInfo instead of overwriting each other.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .._result import err, guard, ok
from . import connections, safety, serialize
from ._common import ID_RE, get_item

_LAYER_URL = re.compile(r"/(FeatureServer|MapServer)/\d+/?$", re.IGNORECASE)
_SERVICE_URL = re.compile(r"/FeatureServer/?$", re.IGNORECASE)
FIELD_TYPES = {
    "TEXT": "esriFieldTypeString", "STRING": "esriFieldTypeString", "SHORT": "esriFieldTypeSmallInteger",
    "SMALLINT": "esriFieldTypeSmallInteger", "LONG": "esriFieldTypeInteger", "INTEGER": "esriFieldTypeInteger",
    "BIGINT": "esriFieldTypeBigInteger", "DOUBLE": "esriFieldTypeDouble", "FLOAT": "esriFieldTypeSingle",
    "DATE": "esriFieldTypeDate", "DATEONLY": "esriFieldTypeDateOnly", "GUID": "esriFieldTypeGUID",
}
CATEGORICAL = [[31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40], [148, 103, 189], [140, 86, 75],
               [227, 119, 194], [127, 127, 127], [188, 189, 34], [23, 190, 207], [174, 199, 232], [255, 187, 120]]
SEQUENTIAL = [[255, 255, 178], [254, 217, 118], [254, 178, 76], [253, 141, 60], [252, 78, 42], [227, 26, 28],
              [177, 0, 38]]


# --- opening layers ------------------------------------------------------------

def open_layer(gis: Any, target: str, layer: int | str | None = 0):
    """-> (layer, item or None, FeatureLayerCollection). target = item id or a layer/service URL."""
    from arcgis.features import FeatureLayer, FeatureLayerCollection, Table  # type: ignore

    target = str(target).strip()
    item = None
    if ID_RE.match(target):
        item = get_item(gis, target)
        if not item.url:
            raise ValueError(f"Item {target} ({item.type}) has no service URL.")
        flc = FeatureLayerCollection.fromitem(item)
        candidates = list(getattr(item, "layers", None) or []) + list(getattr(item, "tables", None) or [])
    elif _LAYER_URL.search(target):
        lyr = FeatureLayer(target, gis=gis)
        if lyr.properties.get("type") == "Table":
            lyr = Table(target, gis=gis)
        service_url = target.rstrip("/").rsplit("/", 1)[0]
        return lyr, None, FeatureLayerCollection(service_url, gis=gis)
    elif _SERVICE_URL.search(target):
        flc = FeatureLayerCollection(target.rstrip("/"), gis=gis)
        candidates = list(flc.layers or []) + list(flc.tables or [])
    else:
        raise ValueError("target must be an item id or a .../FeatureServer[/N] URL.")
    if not candidates:
        raise ValueError("The service has no layers or tables.")
    if layer is None:
        layer = 0
    if isinstance(layer, int) or str(layer).isdigit():
        idx = int(layer)
        if not 0 <= idx < len(candidates):
            raise IndexError(f"layer {idx} out of range; the service has {len(candidates)} layer(s)/table(s): "
                             + ", ".join(c.properties.get("name", "?") for c in candidates))
        return candidates[idx], item, flc
    for c in candidates:
        if str(c.properties.get("name", "")).lower() == str(layer).lower():
            return c, item, flc
    raise LookupError(f"No layer named '{layer}'.")


def _hosted_view(item: Any, flc: Any) -> tuple[bool, bool]:
    kw = list(item.get("typeKeywords") or []) if item is not None else []
    url = str(getattr(flc, "url", ""))
    hosted = "Hosted Service" in kw or "/Hosted/" in url or bool(re.search(r"services\d*\.arcgis\.com", url))
    view = "View Service" in kw
    try:
        view = view or bool(flc.properties.get("isView"))
    except Exception:  # noqa: BLE001
        pass
    return hosted, view


def _require_hosted(item: Any, flc: Any, what: str, schema_change: bool = False) -> dict | None:
    hosted, view = _hosted_view(item, flc)
    if not hosted:
        return err(f"{what} works only on HOSTED feature services (this one is not).", code="not_hosted")
    if schema_change and view:
        return err(f"{what}: this is a VIEW - change the schema on its source layer.", code="is_view")
    return None


def _fields(props: Any) -> list[dict]:
    out = []
    for f in props.get("fields") or []:
        dom = f.get("domain") or None
        out.append({"name": f.get("name"), "type": str(f.get("type", "")).replace("esriFieldType", ""),
                    "alias": f.get("alias"), "nullable": f.get("nullable"), "editable": f.get("editable"),
                    "length": f.get("length"),
                    "domain": None if not dom else {"name": dom.get("name"), "type": dom.get("type"),
                                                    "codes": len(dom.get("codedValues") or []) or None,
                                                    "range": dom.get("range")}})
    return out


def _date_fields(props: Any) -> set[str]:
    return {f["name"] for f in (props.get("fields") or []) if f.get("type") == "esriFieldTypeDate"}


def _rows(features: list, dates: set[str], with_geometry: bool) -> list[dict]:
    rows = []
    for f in features:
        attrs = dict(getattr(f, "attributes", None) or (f.get("attributes") if isinstance(f, dict) else {}) or {})
        for k in dates & attrs.keys():
            attrs[k] = serialize.iso(attrs[k])
        if with_geometry:
            geom = getattr(f, "geometry", None) if not isinstance(f, dict) else f.get("geometry")
            text = json.dumps(geom, default=str) if geom else ""
            attrs["_geometry"] = geom if len(text) <= 4000 else {"note": f"geometry of {len(text)} chars omitted"}
        rows.append(attrs)
    return rows


# --- read ------------------------------------------------------------------------

@guard
def query(action: str = "query", target: str = "", layer: int | str | None = 0, where: str = "1=1",
          fields: list[str] | None = None, limit: int = 50, offset: int = 0, order_by: str | None = None,
          return_geometry: bool = False, stats: list | None = None, group_by: str | None = None,
          distinct: bool = False, out_sr: int | None = None, connection: str = "default") -> dict:
    action = (action or "query").lower()
    gis = connections.get(connection)
    lyr, item, flc = open_layer(gis, target, layer)
    props = lyr.properties
    if action == "describe":
        hosted, view = _hosted_view(item, flc)
        out = {
            "name": props.get("name"), "type": props.get("type"), "url": lyr.url,
            "geometryType": props.get("geometryType"), "objectIdField": props.get("objectIdField"),
            "globalIdField": props.get("globalIdField"), "displayField": props.get("displayField"),
            "count": lyr.query(where="1=1", return_count_only=True),
            "capabilities": props.get("capabilities"), "maxRecordCount": props.get("maxRecordCount"),
            "supportsAppend": (flc.properties.get("supportsAppend") if flc is not None else None),
            "hasAttachments": props.get("hasAttachments"), "editorTracking": props.get("editFieldsInfo"),
            "spatialReference": (props.get("extent") or {}).get("spatialReference"),
            "is_hosted": hosted, "is_view": view, "fields": _fields(props),
            "relationships": [{"id": r.get("id"), "name": r.get("name")} for r in props.get("relationships") or []],
        }
        if view and props.get("viewDefinitionQuery"):
            out["viewDefinitionQuery"] = props.get("viewDefinitionQuery")
        if item is not None:
            out["item"] = serialize.item(item, gis)
            out["service_layers"] = [{"index": i, "name": c.properties.get("name")}
                                     for i, c in enumerate(list(getattr(item, "layers", None) or [])
                                                           + list(getattr(item, "tables", None) or []))]
        return ok(out)
    if action == "count":
        return ok({"where": where, "count": lyr.query(where=where, return_count_only=True)})
    if action == "stats":
        if not stats:
            return err('stats is required, e.g. ["sum:POP", "avg:INCOME", "count:OBJECTID"] or raw '
                       'outStatistics dicts.', code="bad_argument")
        out_stats = []
        for s in stats:
            if isinstance(s, dict):
                out_stats.append(s)
                continue
            kind, _, fld = str(s).partition(":")
            kind = {"avg": "avg", "mean": "avg", "sum": "sum", "min": "min", "max": "max", "count": "count",
                    "stddev": "stddev", "var": "var"}.get(kind.lower())
            if not (kind and fld):
                return err(f"Bad stat '{s}'; use kind:field (sum|avg|min|max|count|stddev|var).",
                           code="bad_argument")
            out_stats.append({"statisticType": kind, "onStatisticField": fld,
                              "outStatisticFieldName": f"{kind}_{fld}"[:30]})
        fs = lyr.query(where=where, out_statistics=out_stats, group_by_fields_for_statistics=group_by,
                       return_geometry=False, return_all_records=False, order_by_fields=order_by)
        return ok({"where": where, "group_by": group_by, "rows": _rows(fs.features[:1000], set(), False)})
    if action != "query":
        return err("action must be describe | query | count | stats.", code="bad_action")
    limit = max(1, min(int(limit or 50), 2000))
    fs = lyr.query(where=where, out_fields=",".join(fields) if fields else "*", return_geometry=return_geometry,
                   result_offset=int(offset or 0), result_record_count=limit, order_by_fields=order_by,
                   return_all_records=False, return_distinct_values=distinct,
                   out_sr=out_sr if out_sr else None)
    rows = _rows(fs.features, _date_fields(props), return_geometry)
    more = len(rows) >= limit or bool(getattr(fs, "exceeded_transfer_limit", False))
    return ok({"where": where, "count": len(rows), "offset": int(offset or 0),
               "next_offset": int(offset or 0) + len(rows) if more else None, "rows": rows,
               "dates": "ISO 8601 UTC (stored as epoch ms)" if _date_fields(props) else None})


# --- write -----------------------------------------------------------------------

def _as_features(rows: list | None) -> list[dict]:
    out = []
    for r in rows or []:
        out.append(r if isinstance(r, dict) and ("attributes" in r or "geometry" in r) else {"attributes": r})
    return out


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _transient(exc: Exception) -> bool:
    text = str(exc)
    return any(code in text for code in ("429", "500", "502", "503", "504", "timed out", "Timeout"))


def _apply(lyr: Any, kind: str, chunk: list, use_global_ids: bool, rollback: bool) -> dict:
    """One edit_features call; updates/deletes are retried on 429/5xx (idempotent), adds never are."""
    attempts = 3 if kind != "adds" else 1
    for attempt in range(attempts):
        try:
            return lyr.edit_features(**{kind: chunk}, use_global_ids=use_global_ids, rollback_on_failure=rollback)
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts - 1 or not _transient(exc):
                raise
            time.sleep(2 ** attempt)
    return {}


@guard
def edit(action: str = "edit", target: str = "", layer: int | str | None = 0, adds: list | None = None,
         updates: list | None = None, deletes: list | None = None, where: str | None = None,
         expressions: list | dict | None = None, source_item_id: str | None = None,
         upload_format: str | None = None, upsert: bool = False, upsert_matching_field: str | None = None,
         field_mappings: list | None = None, use_global_ids: bool = False, rollback_on_failure: bool = True,
         batch_size: int = 500, dry_run: bool = False, confirm: bool = False, connection: str = "default") -> dict:
    action = (action or "edit").lower()
    gis = connections.get(connection)
    lyr, item, flc = open_layer(gis, target, layer)
    props = lyr.properties
    name = props.get("name")

    if action == "edit":
        adds_f, upd_f = _as_features(adds), _as_features(updates)
        oid = props.get("objectIdField") or "OBJECTID"
        key = props.get("globalIdField") if use_global_ids else oid
        missing_key = [i for i, f in enumerate(upd_f) if key not in (f.get("attributes") or {})]
        if missing_key:
            return err(f"Every update needs its '{key}' attribute (rows {missing_key[:10]} lack it).",
                       code="bad_argument")
        delete_ids: list = list(deletes or [])
        if where and not delete_ids:
            delete_ids = list(lyr.query(where=where, return_ids_only=True).get("objectIds") or [])
        total = len(adds_f) + len(upd_f) + len(delete_ids)
        if not total:
            return err("Nothing to edit: give adds, updates, deletes (ids) or where (deletes matches).",
                       code="nothing_to_do")
        if total > safety.max_edits():
            return err(f"{total} edits exceed ARCGIS_SALAH_MAX_EDITS ({safety.max_edits()}); split the job or use "
                       "action=append for bulk loads.", code="too_many")
        preview: dict = {"layer": name, "adds": len(adds_f), "updates": len(upd_f), "deletes": len(delete_ids)}
        if upd_f:
            ids = [f["attributes"][key] for f in upd_f[:20]]
            current = {}
            if not use_global_ids:
                fs = lyr.query(object_ids=",".join(str(i) for i in ids), out_fields="*", return_geometry=False,
                               return_all_records=False)
                current = {f.attributes.get(oid): f.attributes for f in fs.features}
            preview["update_diff"] = [
                {"id": f["attributes"][key],
                 "changes": {k: {"from": (current.get(f["attributes"][key]) or {}).get(k), "to": v}
                             for k, v in f["attributes"].items() if k != key}} for f in upd_f[:20]]
        if dry_run:
            return ok({"dry_run": True, "preview": preview})
        if delete_ids and not confirm:
            return safety.confirm_required(f"Deleting {len(delete_ids)} feature(s) from '{name}'", preview)
        started = time.perf_counter()
        summary = {"adds": [0, 0], "updates": [0, 0], "deletes": [0, 0]}
        failures: list = []
        size = max(1, min(int(batch_size or 500), 2000))
        for kind, rows in (("adds", adds_f), ("updates", upd_f),
                           ("deletes", [",".join(str(i) for i in c) for c in _chunks(delete_ids, size)])):
            batches = list(_chunks(rows, size)) if kind != "deletes" else [[r] for r in rows]
            for chunk in batches:
                payload = chunk if kind != "deletes" else chunk[0]
                res = _apply(lyr, kind, payload, use_global_ids, rollback_on_failure) or {}
                for r in res.get(f"{kind[:-1]}Results", []):
                    if r.get("success"):
                        summary[kind][0] += 1
                    else:
                        summary[kind][1] += 1
                        if len(failures) < 20:
                            failures.append({"kind": kind, "id": r.get("objectId"), "error": r.get("error")})
        result = {k: {"succeeded": v[0], "failed": v[1]} for k, v in summary.items() if v != [0, 0]}
        all_ok = not failures and all(v[1] == 0 for v in summary.values())
        safety.audit("portal_layer_edit", "edit", connection=connection, target=lyr.url, ok=all_ok,
                     details={"result": result, "failures": failures[:5]})
        return ok({"layer": name, "result": result, "all_ok": all_ok, "failures": failures or None,
                   "seconds": round(time.perf_counter() - started, 1)})

    if action == "calculate":
        if not (where and expressions):
            return err('where and expressions are required, e.g. expressions=[{"field":"STATUS","value":"Done"}] '
                       'or [{"field":"DENSITY","sqlExpression":"POP / AREA"}].', code="bad_argument")
        exprs = [expressions] if isinstance(expressions, dict) else list(expressions)
        affected = lyr.query(where=where, return_count_only=True)
        preview = {"layer": name, "where": where, "rows_affected": affected, "expressions": exprs}
        if dry_run or not confirm:
            return ok({"dry_run": True, "preview": preview}) if dry_run else \
                safety.confirm_required(f"Calculating {len(exprs)} field(s) on {affected} row(s) of '{name}'", preview)
        res = lyr.calculate(where=where, calc_expression=exprs)
        good = bool((res or {}).get("success"))
        safety.audit("portal_layer_edit", "calculate", connection=connection, target=lyr.url, ok=good, details=preview)
        return ok({"layer": name, "result": res, "rows_affected": (res or {}).get("updatedFeatureCount", affected)})

    if action == "append":
        if not (source_item_id and upload_format):
            return err("source_item_id and upload_format (csv | shapefile | filegdb | geojson | excel | "
                       "featureCollection) are required.", code="bad_argument")
        if not flc.properties.get("supportsAppend", False):
            return err("This service does not support append (supportsAppend is false).", code="unsupported")
        before = lyr.query(where="1=1", return_count_only=True)
        preview = {"layer": name, "rows_now": before, "source_item": source_item_id, "upsert": upsert,
                   "match_field": upsert_matching_field}
        if not confirm:
            return safety.confirm_required(f"Appending into '{name}'", preview)
        res = lyr.append(item_id=source_item_id, upload_format=upload_format, upsert=upsert,
                         upsert_matching_field=upsert_matching_field, field_mappings=field_mappings)
        after = lyr.query(where="1=1", return_count_only=True)
        safety.audit("portal_layer_edit", "append", connection=connection, target=lyr.url, ok=bool(res),
                     details={**preview, "rows_after": after})
        return ok({"layer": name, "result": res, "rows_before": before, "rows_after": after})

    if action == "truncate":
        problem = _require_hosted(item, flc, "truncate")
        if problem:
            return problem
        before = lyr.query(where="1=1", return_count_only=True)
        if not confirm:
            return safety.confirm_required(f"TRUNCATE '{name}' (deletes all {before} rows)", {"rows": before},
                                           hint="Irreversible. Export first (portal_item action=export).")
        res = lyr.manager.truncate()
        after = lyr.query(where="1=1", return_count_only=True)
        safety.audit("portal_layer_edit", "truncate", connection=connection, target=lyr.url, ok=after == 0,
                     before=before, after=after)
        return ok({"layer": name, "rows_before": before, "rows_after": after, "result": res})
    return err("action must be edit | calculate | append | truncate.", code="bad_action")


# --- renderers ---------------------------------------------------------------------

def _symbol(geometry: str | None, color: list[int]) -> dict:
    rgba = list(color[:3]) + [230]
    if geometry == "esriGeometryPolyline":
        return {"type": "esriSLS", "style": "esriSLSSolid", "color": rgba, "width": 2}
    if geometry == "esriGeometryPolygon":
        return {"type": "esriSFS", "style": "esriSFSSolid", "color": list(color[:3]) + [170],
                "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [110, 110, 110, 255], "width": 0.7}}
    return {"type": "esriSMS", "style": "esriSMSCircle", "color": rgba, "size": 8,
            "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [255, 255, 255, 255], "width": 0.7}}


def build_renderer(lyr: Any, spec: dict) -> dict:
    """Esri renderer JSON from a spec. {"type":"unique","field"} | {"type":"class_breaks","field","classes"}
    | {"type":"simple","color":[r,g,b]} - values/breaks are read from the layer's data."""
    if spec.get("type") not in ("unique", "class_breaks", "simple"):
        return spec  # already renderer JSON
    geom = lyr.properties.get("geometryType")
    kind = spec["type"]
    if kind == "simple":
        return {"type": "simple", "symbol": _symbol(geom, spec.get("color") or CATEGORICAL[0])}
    fld = spec.get("field")
    if not fld:
        raise ValueError(f"a {kind} renderer needs 'field'.")
    if kind == "unique":
        fs = lyr.query(where=f"{fld} IS NOT NULL", out_statistics=[
            {"statisticType": "count", "onStatisticField": fld, "outStatisticFieldName": "n"}],
            group_by_fields_for_statistics=fld, return_geometry=False, return_all_records=False)
        counts = sorted(((f.attributes.get(fld), f.attributes.get("n") or 0) for f in fs.features),
                        key=lambda t: -t[1])
        top = counts[:len(CATEGORICAL)]
        return {"type": "uniqueValue", "field1": fld,
                "uniqueValueInfos": [{"value": str(v), "label": str(v), "symbol": _symbol(geom, CATEGORICAL[i])}
                                     for i, (v, _) in enumerate(top)],
                "defaultSymbol": _symbol(geom, [200, 200, 200]) if len(counts) > len(top) else None,
                "defaultLabel": "Other" if len(counts) > len(top) else None}
    classes = max(2, min(int(spec.get("classes") or 5), len(SEQUENTIAL)))
    fs = lyr.query(where=f"{fld} IS NOT NULL", out_fields=fld, return_geometry=False, result_record_count=5000,
                   return_all_records=False)
    values = sorted(float(f.attributes[fld]) for f in fs.features if f.attributes.get(fld) is not None)
    if not values:
        raise ValueError(f"field '{fld}' has no values to classify.")
    breaks = []
    for i in range(1, classes + 1):  # quantile breaks
        breaks.append(values[min(len(values) - 1, round(i * len(values) / classes) - 1)])
    breaks = sorted(set(breaks))
    ramp = [SEQUENTIAL[round(i * (len(SEQUENTIAL) - 1) / max(1, len(breaks) - 1))] for i in range(len(breaks))]
    infos, low = [], values[0]
    for b, color in zip(breaks, ramp):
        infos.append({"classMaxValue": b, "label": f"{low:g} - {b:g}", "symbol": _symbol(geom, color)})
        low = b
    return {"type": "classBreaks", "field": fld, "minValue": values[0], "classificationMethod":
            "esriClassifyQuantile", "classBreakInfos": infos}


def _merge_drawing(lyr: Any, **parts: Any) -> dict:
    current = dict(lyr.properties.get("drawingInfo") or {})
    current.update({k: v for k, v in parts.items() if v is not None})
    return current


@guard
def schema(action: str, target: str, layer: int | str | None = 0, fields: list | None = None,
           field_names: list[str] | None = None, capabilities: str | None = None, renderer: dict | None = None,
           label_field: str | None = None, label_expression: str | None = None, definition: dict | None = None,
           level: str = "layer", view_name: str | None = None, view_where: str | None = None,
           view_fields: list[str] | None = None, updateable: bool = False, folder: str | None = None,
           data_file: str | None = None, confirm: bool = False, connection: str = "default") -> dict:
    from arcgis.features import FeatureLayer  # type: ignore

    action = (action or "").lower()
    gis = connections.get(connection)
    lyr, item, flc = open_layer(gis, target, layer)
    name = lyr.properties.get("name")

    def fresh_props() -> Any:
        return FeatureLayer(lyr.url, gis=gis).properties

    if action == "add_field":
        problem = _require_hosted(item, flc, "add_field", schema_change=True)
        if problem:
            return problem
        if not fields:
            return err('fields is required: [{"name":"STATUS","type":"TEXT","length":50,"alias":"Status"}]. '
                       f"Types: {', '.join(FIELD_TYPES)}.", code="bad_argument")
        have = {f.get("name", "").lower() for f in lyr.properties.get("fields") or []}
        specs, skipped = [], []
        for f in fields:
            fname = f.get("name")
            ftype = FIELD_TYPES.get(str(f.get("type", "TEXT")).upper(), f.get("type"))
            if not fname or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", fname):
                return err(f"Bad field name '{fname}' (letters, digits, underscore; start with a letter).",
                           code="bad_argument")
            if fname.lower() in have:
                skipped.append(fname)
                continue
            spec = {"name": fname, "type": ftype, "alias": f.get("alias") or fname,
                    "nullable": f.get("nullable", True), "editable": True}
            if ftype == "esriFieldTypeString":
                spec["length"] = int(f.get("length") or 256)
            specs.append(spec)
        if specs:
            lyr.manager.add_to_definition({"fields": specs})
        now = {f.get("name", "").lower() for f in fresh_props().get("fields") or []}
        missing = [s["name"] for s in specs if s["name"].lower() not in now]
        safety.audit("portal_layer_schema", "add_field", connection=connection, target=lyr.url, ok=not missing,
                     after=[s["name"] for s in specs])
        return ok({"layer": name, "added": [s["name"] for s in specs if s["name"] not in missing],
                   "already_existed": skipped or None, "not_added": missing or None})

    if action == "delete_field":
        problem = _require_hosted(item, flc, "delete_field", schema_change=True)
        if problem:
            return problem
        if not field_names:
            return err("field_names is required.", code="bad_argument")
        if not confirm:
            return safety.confirm_required(f"Deleting field(s) {field_names} from '{name}' (their data is lost)",
                                           {"layer": name, "fields": field_names})
        lyr.manager.delete_from_definition({"fields": [{"name": n} for n in field_names]})
        now = {f.get("name", "").lower() for f in fresh_props().get("fields") or []}
        still = [n for n in field_names if n.lower() in now]
        safety.audit("portal_layer_schema", "delete_field", connection=connection, target=lyr.url, ok=not still,
                     before=field_names)
        return ok({"layer": name, "deleted": [n for n in field_names if n not in still], "still_there": still or None})

    if action == "capabilities":
        problem = _require_hosted(item, flc, "capabilities")
        if problem:
            return problem
        if not capabilities:
            return err('capabilities is required, e.g. "Query,Create,Update,Delete,Editing,Sync,Extract".',
                       code="bad_argument")
        before = flc.properties.get("capabilities")
        flc.manager.update_definition({"capabilities": capabilities})
        from arcgis.features import FeatureLayerCollection  # type: ignore

        after = FeatureLayerCollection(flc.url, gis=gis).properties.get("capabilities")
        safety.audit("portal_layer_schema", "capabilities", connection=connection, target=flc.url,
                     ok=set(after.split(",")) == set(capabilities.split(",")), before=before, after=after)
        return ok({"service": flc.url, "before": before, "after": after})

    if action == "renderer":
        if not renderer:
            return err('renderer is required: Esri renderer JSON, or {"type":"unique","field":"X"} / '
                       '{"type":"class_breaks","field":"POP","classes":5} / {"type":"simple","color":[0,112,255]}.',
                       code="bad_argument")
        built = build_renderer(lyr, renderer)
        lyr.manager.update_definition({"drawingInfo": _merge_drawing(lyr, renderer=built)})
        safety.audit("portal_layer_schema", "renderer", connection=connection, target=lyr.url, ok=True,
                     details={"type": built.get("type")})
        return ok({"layer": name, "renderer_type": built.get("type"),
                   "classes": len(built.get("uniqueValueInfos") or built.get("classBreakInfos") or []) or None})

    if action == "labels":
        if not (label_field or label_expression):
            return err("label_field or label_expression (Arcade) is required.", code="bad_argument")
        label_class = {
            "labelExpressionInfo": {"expression": label_expression or f"$feature.{label_field}"},
            "symbol": {"type": "esriTS", "color": [0, 0, 0, 255], "haloColor": [255, 255, 255, 255], "haloSize": 1,
                       "font": {"size": 10, "family": "Arial"}},
            "labelPlacement": {"esriGeometryPolyline": "esriServerLinePlacementAboveAlong",
                               "esriGeometryPolygon": "esriServerPolygonPlacementAlwaysHorizontal"}.get(
                lyr.properties.get("geometryType"), "esriServerPointLabelPlacementAboveRight"),
        }
        lyr.manager.update_definition({"drawingInfo": _merge_drawing(lyr, labelingInfo=[label_class])})
        safety.audit("portal_layer_schema", "labels", connection=connection, target=lyr.url, ok=True)
        return ok({"layer": name, "labels": label_class["labelExpressionInfo"]["expression"]})

    if action == "definition":
        if not definition:
            return err("definition (JSON for update_definition) is required.", code="bad_argument")
        problem = _require_hosted(item, flc, "definition")
        if problem:
            return problem
        if not confirm:
            return safety.confirm_required(f"Updating the {level} definition of '{name}'",
                                           {"level": level, "definition": definition})
        target_mgr = flc.manager if level == "service" else lyr.manager
        res = target_mgr.update_definition(definition)
        safety.audit("portal_layer_schema", "definition", connection=connection, target=lyr.url,
                     ok=bool((res or {}).get("success", res)), details={"level": level, "keys": list(definition)})
        return ok({"layer": name, "level": level, "result": res})

    if action == "create_view":
        problem = _require_hosted(item, flc, "create_view")
        if problem:
            return problem
        if not view_name:
            return err("view_name is required.", code="bad_argument")
        # Two arcgis 2.4.3 traps, both measured on ArcGIS Online (each left a view item behind):
        # * view_layers is annotated list[int] but create_view reads `.properties` from each entry -> pass layers;
        # * visible_fields/query make create_view read item.layers[0] of the brand-new view before its layers
        #   exist -> "list index out of range". So the view is created plain, and the filter and visible fields
        #   are applied here to the view layer once it shows up.
        multi = len(list(flc.layers or [])) + len(list(flc.tables or [])) > 1
        safe_name = re.sub(r"\W", "_", view_name)
        started = time.perf_counter()
        me = gis.users.me.username if gis.users.me is not None else None

        def leftovers() -> list[str]:
            return [i.id for i in gis.content.search(f'title:"{safe_name}" AND owner:{me}', max_items=5)
                    if i.title == safe_name]

        try:
            view_item = flc.manager.create_view(name=safe_name, updateable=updateable,
                                                capabilities="Query" if not updateable else "Query,Update",
                                                view_layers=[lyr] if multi else None, folder=folder)
        except Exception as exc:  # noqa: BLE001 - the view item can exist even though the call failed
            left = leftovers()
            return err(f"create_view failed: {safety.scrub(str(exc))[:300]}", code="create_view_failed",
                       possibly_created=left or None,
                       hint="Delete possibly_created items with portal_item action=delete before retrying."
                       if left else None)
        from arcgis.features import FeatureLayerCollection  # type: ignore

        v_layer = None
        for _ in range(15):
            found = FeatureLayerCollection(gis.content.get(view_item.id).url, gis=gis).layers
            if found:
                v_layer = found[0]
                break
            time.sleep(1)
        out: dict = {"view": serialize.item(view_item, gis), "where": view_where, "fields": view_fields,
                     "updateable": updateable}
        if v_layer is None:
            return err("The view was created but its layer did not appear within 15 s; filter not applied.",
                       code="view_incomplete", possibly_created=[view_item.id])
        if view_where or view_fields:
            values: dict = {}
            if view_fields:
                keep = {f.lower() for f in view_fields} | {str(v_layer.properties.get("objectIdField", "")).lower()}
                values["fields"] = [{"name": f["name"], "visible": f["name"].lower() in keep}
                                    for f in v_layer.properties.get("fields") or []]
            if view_where:
                values["viewDefinitionQuery"] = view_where
            v_layer.manager.update_definition(values)
        fresh = FeatureLayerCollection(gis.content.get(view_item.id).url, gis=gis).layers[0]
        out["view_count"] = fresh.query(where="1=1", return_count_only=True)
        out["view_definition_query"] = fresh.properties.get("viewDefinitionQuery")
        out["visible_fields"] = [f["name"] for f in fresh.properties.get("fields") or []]
        out["seconds"] = round(time.perf_counter() - started, 1)
        safety.audit("portal_layer_schema", "create_view", connection=connection, target=flc.url, ok=True,
                     after=out["view"]["id"])
        return ok(out)

    if action == "overwrite":
        problem = _require_hosted(item, flc, "overwrite")
        if problem:
            return problem
        if not data_file:
            return err("data_file is required (same schema as the original upload).", code="bad_argument")
        if not confirm:
            return safety.confirm_required(f"Overwriting all data of '{flc.url}' (item id is kept)",
                                           {"data_file": data_file})
        res = flc.manager.overwrite(data_file)
        safety.audit("portal_layer_schema", "overwrite", connection=connection, target=flc.url,
                     ok=bool((res or {}).get("success", res)))
        return ok({"service": flc.url, "result": res})
    return err("action must be add_field | delete_field | capabilities | renderer | labels | definition | "
               "create_view | overwrite.", code="bad_action")
