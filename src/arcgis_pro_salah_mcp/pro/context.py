"""One-shot workspace context — the antidote to round-trip latency.

The problem this solves
-----------------------
Answering "how many schools are in this project?" used to cost the agent three
separate tool calls: ``pro_list_layers`` -> ``pro_describe_layer`` ->
``pro_get_features``. Each one is a full model inference (seconds) on top of the
ArcPy work (~0.4 s), so the *round trips* dominate, not the geoprocessing.

``context()`` returns everything an agent normally needs to answer a first
question about a workspace in a single call: the project, its maps, every
layer's schema, row counts, a few sample rows, and an inferred theme. The agent
can then answer immediately, or aim its next call precisely.

Everything here goes through :mod:`cache`, so a second ``context()`` call in the
same conversation is nearly free.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .._result import err, guard, ok
from . import cache, metadata, ops


def _sample_rows(arcpy, dataset: str, fields: list[str], limit: int) -> list[dict]:
    """A few rows, geometry excluded, failures swallowed.

    A locked or corrupt table must not sink the whole context call — the point
    of this tool is to always give the agent *something* to work with.
    """
    if limit <= 0 or not fields:
        return []
    rows: list[dict] = []
    try:
        with arcpy.da.SearchCursor(dataset, fields) as cur:
            for i, row in enumerate(cur):
                if i >= limit:
                    break
                rows.append(dict(zip(fields, [ops._scalar(v) for v in row])))
    except Exception:  # noqa: BLE001 - sampling is best-effort by design
        return []
    return rows


def _describe_dataset(arcpy, dataset: str, sample_rows: int) -> dict:
    summary = cache.get_or_load(dataset, lambda: ops._load_summary(dataset))
    field_names = [f["name"] for f in summary.get("fields", [])]
    light = [
        f["name"]
        for f in summary.get("fields", [])
        if f.get("type") not in ops._HEAVY_FIELD_TYPES
    ]
    theme = metadata.infer_theme(
        os.path.basename(str(dataset)), field_names
    )
    return {
        "dataset": str(dataset),
        "name": os.path.basename(str(dataset)),
        "geometry_type": summary.get("geometry_type"),
        "feature_count": summary.get("feature_count"),
        "crs": summary.get("crs"),
        "extent": summary.get("extent"),
        "fields": summary.get("fields"),
        "theme": theme,
        "sample": _sample_rows(arcpy, dataset, light[:12], sample_rows),
    }


def _walk_workspace(arcpy, workspace: str) -> list[str]:
    """Every feature class and table in a geodatabase, including feature datasets."""
    found: list[str] = []
    prev = arcpy.env.workspace
    try:
        arcpy.env.workspace = workspace
        for fc in arcpy.ListFeatureClasses() or []:
            found.append(os.path.join(workspace, fc))
        for tbl in arcpy.ListTables() or []:
            found.append(os.path.join(workspace, tbl))
        for fds in arcpy.ListDatasets("", "Feature") or []:
            arcpy.env.workspace = os.path.join(workspace, fds)
            for fc in arcpy.ListFeatureClasses() or []:
                found.append(os.path.join(workspace, fds, fc))
    except Exception:  # noqa: BLE001
        pass
    finally:
        arcpy.env.workspace = prev
    return found


@guard
def context(
    aprx_path: str | None = None,
    workspace: str | None = None,
    datasets: list[str] | None = None,
    sample_rows: int = 3,
    max_datasets: int = 25,
    map_name: str | None = None,
) -> dict:
    """Everything about a project/workspace in ONE call.

    Give it any one of: an ``.aprx``, a geodatabase ``workspace``, or an explicit
    list of ``datasets``. With an ``.aprx`` it also reports maps and layouts and
    resolves each layer's data source.
    """
    arcpy = ops._arcpy()
    started = time.perf_counter()

    if not (aprx_path or workspace or datasets):
        return err(
            "Provide one of: aprx_path, workspace (a .gdb) or datasets[].",
            code="no_source",
        )

    out: dict[str, Any] = {"project": None, "maps": [], "layers": [], "warnings": []}
    targets: list[str] = []

    if aprx_path:
        aprx = arcpy.mp.ArcGISProject(aprx_path)
        out["project"] = {
            "path": aprx.filePath,
            "default_gdb": aprx.defaultGeodatabase,
            "layouts": [lay.name for lay in aprx.listLayouts()],
        }
        for m in aprx.listMaps():
            if map_name and m.name != map_name:
                continue
            layers_meta = []
            for lyr in m.listLayers():
                entry = {
                    "name": lyr.name,
                    "visible": getattr(lyr, "visible", None),
                    "is_feature": getattr(lyr, "isFeatureLayer", False),
                    "is_raster": getattr(lyr, "isRasterLayer", False),
                    "broken": getattr(lyr, "isBroken", False),
                    "source": None,
                }
                try:
                    if getattr(lyr, "supports", lambda *_: False)("DATASOURCE"):
                        entry["source"] = lyr.dataSource
                except Exception:  # noqa: BLE001
                    pass
                layers_meta.append(entry)
                if entry["source"] and entry["is_feature"] and not entry["broken"]:
                    targets.append(entry["source"])
            out["maps"].append({"name": m.name, "layers": layers_meta})

        if not targets and aprx.defaultGeodatabase:
            targets = _walk_workspace(arcpy, aprx.defaultGeodatabase)

    if workspace:
        targets.extend(_walk_workspace(arcpy, workspace))
    if datasets:
        targets.extend(str(d) for d in datasets)

    # De-duplicate while preserving order — an .aprx often has the same source
    # in several maps.
    seen: set[str] = set()
    unique: list[str] = []
    for t in targets:
        k = cache.key_for(t)
        if k not in seen:
            seen.add(k)
            unique.append(t)

    if len(unique) > max_datasets:
        out["warnings"].append(
            f"{len(unique)} datasets found; describing the first {max_datasets}. "
            f"Raise max_datasets or pass datasets[] to target specific ones."
        )
        unique = unique[:max_datasets]

    for ds in unique:
        try:
            out["layers"].append(_describe_dataset(arcpy, ds, sample_rows))
        except Exception as exc:  # noqa: BLE001 - one bad layer must not fail the call
            out["warnings"].append(f"{ds}: {exc}")

    out["dataset_count"] = len(out["layers"])
    out["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    out["cache"] = cache.stats()
    return ok(out)


@guard
def find_layers(
    query: str,
    aprx_path: str | None = None,
    workspace: str | None = None,
    limit: int = 10,
) -> dict:
    """Semantic-ish layer search: "which layer has population data?".

    Scores each dataset by reusing :mod:`metadata`'s theme inference over its
    name and field names, so the agent does not have to pull every schema into
    its context window just to pick one layer.
    """
    arcpy = ops._arcpy()
    targets: list[str] = []
    if workspace:
        targets = _walk_workspace(arcpy, workspace)
    elif aprx_path:
        aprx = arcpy.mp.ArcGISProject(aprx_path)
        if aprx.defaultGeodatabase:
            targets = _walk_workspace(arcpy, aprx.defaultGeodatabase)
    if not targets:
        return err("Provide a workspace (.gdb) or an aprx_path with a default gdb.")

    terms = {t for t in metadata._tokens(query) if len(t) > 2}
    scored = []
    for ds in targets:
        try:
            summary = cache.get_or_load(ds, lambda d=ds: ops._load_summary(d))
        except Exception:  # noqa: BLE001
            continue
        name = os.path.basename(str(ds))
        field_names = [f["name"] for f in summary.get("fields", [])]
        theme = metadata.infer_theme(name, field_names)

        haystack = set(metadata._tokens(name)) | {
            t for f in field_names for t in metadata._tokens(f)
        }
        # Direct term overlap is the strongest signal; the inferred theme label
        # and its matched keywords broaden it to synonyms the user didn't type.
        direct = len(terms & haystack)
        theme_hit = len(terms & set(metadata._tokens(theme.get("label", ""))))
        kw_hit = len(terms & set(theme.get("matched", [])))
        score = direct * 3 + kw_hit * 2 + theme_hit
        if score:
            scored.append(
                {
                    "dataset": str(ds),
                    "name": name,
                    "score": score,
                    "theme": theme.get("label"),
                    "geometry_type": summary.get("geometry_type"),
                    "feature_count": summary.get("feature_count"),
                    "matched_fields": [
                        f for f in field_names if terms & set(metadata._tokens(f))
                    ][:8],
                }
            )

    scored.sort(key=lambda d: -d["score"])
    return ok({"query": query, "searched": len(targets), "matches": scored[:limit]})
