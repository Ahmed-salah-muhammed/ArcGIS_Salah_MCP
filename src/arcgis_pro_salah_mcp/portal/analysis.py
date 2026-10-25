"""Geocoding, hosted spatial analysis and GP services - with credit estimates before anything is spent.

ArcGIS Online prices (Esri's credit table): batch / stored geocoding 40 credits per 1,000 addresses
(0.04 each); single, unstored geocodes, reverse geocodes and suggestions are free. Spatial-analysis
functions accept ``estimate=True`` and report the credits they would use.
"""
from __future__ import annotations

import csv
import inspect
import time
from pathlib import Path
from typing import Any

from .._result import err, guard, ok
from . import _imports, connections, safety, serialize
from ._common import ID_RE

GEOCODE_CREDITS_EACH = 0.04
FEATURE_ANALYSIS = (
    "aggregate_points", "calculate_density", "choose_best_facilities", "connect_origins_to_destinations",
    "create_buffers", "create_drive_time_areas", "create_route_layers", "create_viewshed", "create_watersheds",
    "derive_new_locations", "dissolve_boundaries", "enrich_layer", "extract_data", "find_centroids",
    "find_existing_locations", "find_hot_spots", "find_nearest", "find_outliers", "find_point_clusters",
    "find_similar_locations", "generate_tessellation", "interpolate_points", "join_features", "merge_layers",
    "overlay_layers", "plan_routes", "summarize_center_and_dispersion", "summarize_nearby", "summarize_within",
    "trace_downstream",
)


def _geocoder(gis: Any, url: str | None):
    geo = _imports.load("arcgis.geocoding")
    Geocoder, get_geocoders = geo.Geocoder, geo.get_geocoders

    if url:
        return Geocoder(url, gis=gis)
    found = get_geocoders(gis)
    if not found:
        raise LookupError("The portal has no geocoder configured.")
    return found[0]


def _is_esri_world(geocoder: Any) -> bool:
    return "geocode.arcgis.com" in str(getattr(geocoder, "url", "")) or "World/GeocodeServer" in str(
        getattr(geocoder, "url", ""))


def _candidate(c: dict) -> dict:
    attrs = c.get("attributes") or {}
    loc = c.get("location") or {}
    return {"address": c.get("address"), "score": c.get("score"), "x": loc.get("x"), "y": loc.get("y"),
            "match": attrs.get("Addr_type"), "city": attrs.get("City"), "country": attrs.get("CntryName")}


@guard
def geocode(action: str = "single", address: str | None = None, addresses: list[str] | None = None,
            csv_path: str | None = None, address_field: str | None = None, out_csv: str | None = None,
            location: list[float] | None = None, geocoder_url: str | None = None, max_locations: int = 1,
            for_storage: bool = False, country: str | None = None, confirm: bool = False,
            connection: str = "default") -> dict:
    geocoding = _imports.load("arcgis.geocoding")

    action = (action or "single").lower()
    gis = connections.get(connection)
    if action == "geocoders":
        get_geocoders = geocoding.get_geocoders

        out = []
        for g in get_geocoders(gis):
            props = g.properties
            out.append({"url": g.url, "max_batch": (props.get("locatorProperties") or {}).get("MaxBatchSize"),
                        "esri_world": _is_esri_world(g)})
        return ok({"geocoders": out})
    geocoder = _geocoder(gis, geocoder_url)
    online_paid = connections.is_online(gis) and _is_esri_world(geocoder)
    if action == "single":
        if not address:
            return err("address is required.", code="bad_argument")
        gate = safety.credit_gate(GEOCODE_CREDITS_EACH if for_storage else 0, confirm, "Stored geocode",
                                  online_paid)
        if gate:
            return gate
        res = geocoding.geocode(address, max_locations=max(1, int(max_locations)), for_storage=for_storage,
                                geocoder=geocoder, source_country=country)
        return ok({"address": address, "candidates": [_candidate(c) for c in res or []]})
    if action == "reverse":
        if not location or len(location) != 2:
            return err("location [x, y] (WGS84 lon, lat) is required.", code="bad_argument")
        res = geocoding.reverse_geocode({"x": location[0], "y": location[1], "spatialReference": {"wkid": 4326}},
                                        geocoder=geocoder)
        return ok({"location": location, "address": (res or {}).get("address")})
    if action == "suggest":
        if not address:
            return err("address (partial text) is required.", code="bad_argument")
        res = geocoding.suggest(address, geocoder=geocoder)
        return ok({"suggestions": [s.get("text") for s in (res or {}).get("suggestions", [])]})
    if action != "batch":
        return err("action must be geocoders | single | reverse | suggest | batch.", code="bad_action")

    rows: list[dict] = []
    if csv_path:
        if not address_field:
            return err("address_field (the CSV column holding addresses) is required with csv_path.",
                       code="bad_argument")
        with Path(csv_path).open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        if rows and address_field not in rows[0]:
            return err(f"Column '{address_field}' not in the CSV ({', '.join(rows[0])}).", code="bad_argument")
        values = [r.get(address_field, "") for r in rows]
    else:
        values = list(addresses or [])
        rows = [{"address": v} for v in values]
    if not values:
        return err("addresses or csv_path is required.", code="bad_argument")
    estimate = round(len(values) * GEOCODE_CREDITS_EACH, 2) if online_paid else 0
    gate = safety.credit_gate(estimate, confirm, f"Batch geocoding {len(values)} addresses", online_paid)
    if gate:
        return gate
    started = time.perf_counter()
    batch = int((geocoder.properties.get("locatorProperties") or {}).get("MaxBatchSize") or 100)
    results: list[dict] = []
    for i in range(0, len(values), batch):
        chunk = values[i:i + batch]
        res = geocoding.batch_geocode(chunk, geocoder=geocoder, source_country=country) or []
        results.extend(_candidate(c) for c in res)
    merged = []
    for row, cand in zip(rows, results):
        merged.append({**row, "match_address": cand["address"], "score": cand["score"], "x": cand["x"],
                       "y": cand["y"]})
    if out_csv and merged:
        with Path(out_csv).open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(merged[0]))
            w.writeheader()
            w.writerows(merged)
    matched = sum(1 for m in merged if (m.get("score") or 0) >= 80)
    safety.audit("portal_geocode", "batch", connection=connection, target=csv_path or f"{len(values)} addresses",
                 ok=True, details={"estimated_credits": estimate, "matched": matched})
    return ok({"count": len(merged), "matched_80plus": matched, "estimated_credits": estimate,
               "out_csv": out_csv, "rows": merged[:50] if not out_csv else merged[:10],
               "seconds": round(time.perf_counter() - started, 1)})


def _convert(gis: Any, value: Any) -> Any:
    """Item ids and layer URLs -> Item / FeatureLayer, so analysis functions get real layers."""
    if isinstance(value, str):
        v = value.strip()
        if ID_RE.match(v):
            it = gis.content.get(v)
            return it if it is not None else value
        if "/FeatureServer/" in v or "/MapServer/" in v:
            from arcgis.features import FeatureLayer  # type: ignore

            return FeatureLayer(v, gis=gis)
    if isinstance(value, list):
        return [_convert(gis, x) for x in value]
    return value


@guard
def analysis(action: str = "list", function: str | None = None, params: dict | None = None,
             gp_url: str | None = None, confirm: bool = False, connection: str = "default") -> dict:
    action = (action or "list").lower()
    gis = connections.get(connection)
    if action == "list":
        fa = _imports.load("arcgis.features.analysis")

        return ok({"functions": {n: (inspect.getdoc(getattr(fa, n)) or "").split("\n")[0][:160]
                                 for n in FEATURE_ANALYSIS if hasattr(fa, n)},
                   "note": "hosted spatial analysis spends ArcGIS Online credits; estimate first"})
    if action in ("gp_describe", "gp_run"):
        if not gp_url:
            return err("gp_url (a GPServer URL or a geoprocessing item id) is required.", code="bad_argument")
        import_toolbox = _imports.load("arcgis.geoprocessing").import_toolbox

        tbx = import_toolbox(gis.content.get(gp_url) if ID_RE.match(gp_url) else gp_url, gis=gis)
        names = [n for n in dir(tbx) if not n.startswith("_") and callable(getattr(tbx, n))]
        if action == "gp_describe":
            return ok({"tools": {n: str(inspect.signature(getattr(tbx, n)))[:400] for n in names}})
        if function not in names:
            return err(f"function must be one of {', '.join(names)}.", code="bad_argument")
        if not confirm:
            return safety.confirm_required(f"Running GP tool {function}", {"params": params},
                                           hint="Esri-hosted GP services may spend credits.")
        started = time.perf_counter()
        res = getattr(tbx, function)(**{k: _convert(gis, v) for k, v in (params or {}).items()})
        safety.audit("portal_analysis", "gp_run", connection=connection, target=f"{gp_url}:{function}", ok=True)
        return ok({"result": serialize.jsonable(res), "seconds": round(time.perf_counter() - started, 1)})

    if function not in FEATURE_ANALYSIS:
        return err(f"function must be one of the hosted analysis functions: {', '.join(FEATURE_ANALYSIS)}.",
                   code="bad_argument")
    fa = _imports.load("arcgis.features.analysis")

    fn = getattr(fa, function)
    if action == "describe":
        return ok({"function": function, "signature": str(inspect.signature(fn)),
                   "doc": serialize.text(inspect.getdoc(fn) or "", 4000)})
    kwargs = {k: _convert(gis, v) for k, v in (params or {}).items()}
    kwargs["gis"] = gis
    estimate = fn(**kwargs, estimate=True) if connections.is_online(gis) else 0
    try:
        estimate = float(estimate)
    except (TypeError, ValueError):
        estimate = None
    if action == "estimate":
        return ok({"function": function, "estimated_credits": estimate})
    if action != "run":
        return err("action must be list | describe | estimate | run | gp_describe | gp_run.", code="bad_action")
    if not params or not params.get("output_name"):
        return err("params.output_name is required so the result is saved as a hosted layer you own.",
                   code="bad_argument")
    gate = safety.credit_gate(estimate or 0.0001, confirm, f"Running {function}", connections.is_online(gis))
    if gate:
        return gate
    started = time.perf_counter()
    res = fn(**kwargs)
    safety.audit("portal_analysis", "run", connection=connection, target=function, ok=res is not None,
                 details={"estimated_credits": estimate})
    return ok({"function": function, "result": serialize.jsonable(res), "estimated_credits": estimate,
               "seconds": round(time.perf_counter() - started, 1)})
