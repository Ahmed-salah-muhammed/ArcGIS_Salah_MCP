"""Data validation: geodatabase topology, geometry integrity and rule evaluation.

Three independent things get called "validation" in ArcGIS, and an agent needs
to know which one it wants:

1. **Topology** — spatial integrity between feature classes inside a feature
   dataset ("parcels must not overlap", "roads must not have dangles"). Rules
   are declared once, then :func:`validate_topology` marks violations as *error
   features* you can export and inspect.
2. **Geometry integrity** — self-intersections, null geometry, short segments,
   incorrect ring ordering. ``CheckGeometry`` finds them, ``RepairGeometry``
   fixes them. Nothing to declare; it just runs.
3. **Attribute rules** — Arcade CONSTRAINT/VALIDATION rules, evaluated via
   :mod:`schema`. Covered there, not here.

:func:`validate_all` runs 1 and 2 together and returns a single consolidated
health report, which is what an agent asked "is this data clean?" actually
wants — one call, one answer.
"""
from __future__ import annotations

import os
from typing import Any

from .._result import err, guard, ok
from . import cache, ops

# The 31 rule types AddRuleToTopology accepts, grouped by the geometry pairing
# they apply to. Exposed through describe_topology_rules so the agent can pick a
# valid rule without a documentation round trip — and so a typo is caught here
# rather than 30 seconds into a geoprocessing run.
TOPOLOGY_RULES: dict[str, list[str]] = {
    "area": [
        "Must Not Have Gaps (Area)",
        "Must Not Overlap (Area)",
    ],
    "area-area": [
        "Must Be Covered By Feature Class Of (Area-Area)",
        "Must Cover Each Other (Area-Area)",
        "Must Be Covered By (Area-Area)",
        "Must Not Overlap With (Area-Area)",
        "Boundary Must Be Covered By Boundary Of (Area-Area)",
    ],
    "line": [
        "Must Not Overlap (Line)",
        "Must Not Intersect (Line)",
        "Must Not Have Dangles (Line)",
        "Must Not Have Pseudo-Nodes (Line)",
        "Must Not Self-Overlap (Line)",
        "Must Not Self-Intersect (Line)",
        "Must Not Intersect Or Touch Interior (Line)",
        "Must Be Single Part (Line)",
    ],
    "line-line": [
        "Must Be Covered By Feature Class Of (Line-Line)",
        "Must Not Overlap With (Line-Line)",
        "Must Not Intersect With (Line-Line)",
        "Must Not Intersect or Touch Interior With (Line-Line)",
    ],
    "line-area": [
        "Must Be Covered By Boundary Of (Line-Area)",
        "Must Be Inside (Line-Area)",
    ],
    "area-line": [
        "Boundary Must Be Covered By (Area-Line)",
    ],
    "point-area": [
        "Must Be Covered By Boundary Of (Point-Area)",
        "Must Be Properly Inside (Point-Area)",
    ],
    "area-point": [
        "Contains Point (Area-Point)",
        "Contains One Point (Area-Point)",
    ],
    "point-line": [
        "Must Be Covered By (Point-Line)",
        "Must Be Covered By Endpoint Of (Point-Line)",
    ],
    "line-point": [
        "Endpoint Must Be Covered By (Line-Point)",
    ],
    "point": [
        "Must Be Disjoint (Point)",
    ],
    "point-point": [
        "Must Coincide With (Point-Point)",
    ],
}

_ALL_RULES = {r for group in TOPOLOGY_RULES.values() for r in group}


@guard
def describe_topology_rules(geometry: str | None = None) -> dict:
    """The valid topology rule strings, optionally filtered to one pairing.

    Pure lookup — no ArcPy, no disk. Lets the agent pick a rule name that will
    actually be accepted instead of inventing a plausible-sounding one.
    """
    if geometry:
        key = geometry.strip().lower()
        if key not in TOPOLOGY_RULES:
            return err(
                f"Unknown pairing '{geometry}'. Valid: {sorted(TOPOLOGY_RULES)}",
                valid=sorted(TOPOLOGY_RULES),
            )
        return ok({"pairing": key, "rules": TOPOLOGY_RULES[key]})
    return ok({"rules_by_pairing": TOPOLOGY_RULES, "total": len(_ALL_RULES)})


@guard
def describe_topology(topology: str) -> dict:
    """Inspect an existing topology: its feature classes, rules and state."""
    arcpy = ops._arcpy()
    desc = arcpy.Describe(topology)
    rules = []
    for r in getattr(desc, "topologyRules", None) or []:
        rules.append(
            {
                "name": getattr(r, "ruleName", None),
                "type": getattr(r, "topologyRuleType", None),
                "origin_class": getattr(r, "originClassName", None),
                "origin_subtype": getattr(r, "originSubtypeCode", None),
                "destination_class": getattr(r, "destinationClassName", None),
                "destination_subtype": getattr(r, "destinationSubtypeCode", None),
            }
        )
    return ok(
        {
            "topology": topology,
            "name": getattr(desc, "name", None),
            "cluster_tolerance": getattr(desc, "clusterTolerance", None),
            "feature_classes": list(getattr(desc, "featureClassNames", None) or []),
            "rules": rules,
            "rule_count": len(rules),
            "maximum_generated_error_count": getattr(
                desc, "maximumGeneratedErrorCount", None
            ),
        }
    )


@guard
def create_topology(
    feature_dataset: str,
    name: str,
    feature_classes: list[str],
    rules: list[dict] | None = None,
    cluster_tolerance: float = 0,
    validate: bool = True,
) -> dict:
    """Create a topology, add its feature classes and rules, and validate it.

    One call for what is normally four geoprocessing tools. ``rules`` entries are
    ``{"rule_type": "...", "origin": "Parcels", "destination": "Blocks"}`` —
    origin/destination are feature class NAMES already added to the topology.
    """
    if not feature_classes:
        return err("A topology needs at least one feature class.")

    unknown = [
        r.get("rule_type")
        for r in (rules or [])
        if r.get("rule_type") not in _ALL_RULES
    ]
    if unknown:
        return err(
            f"Unknown topology rule(s): {unknown}. Call pro_topology_rules to list "
            f"the valid strings.",
            code="unknown_rule",
            unknown=unknown,
        )

    arcpy = ops._arcpy()
    topo = arcpy.management.CreateTopology(feature_dataset, name, cluster_tolerance)
    topo_path = str(topo)

    added = []
    for fc in feature_classes:
        # A bare name is resolved against the feature dataset the topology lives
        # in, which is how people naturally refer to them.
        fc_path = fc if os.path.sep in str(fc) or "/" in str(fc) else os.path.join(feature_dataset, fc)
        arcpy.management.AddFeatureClassToTopology(topo_path, fc_path)
        added.append(fc_path)

    def _resolve(name):
        """AddRuleToTopology wants a full dataset path, not a bare class name."""
        if not name:
            return None
        text = str(name)
        if os.path.sep in text or "/" in text:
            return text
        return os.path.join(feature_dataset, text)

    applied, failed = [], []
    for r in rules or []:
        try:
            arcpy.management.AddRuleToTopology(
                topo_path,
                r["rule_type"],
                _resolve(r.get("origin")),
                r.get("origin_subtype"),
                _resolve(r.get("destination")),
                r.get("destination_subtype"),
            )
            applied.append(r["rule_type"])
        except Exception as exc:  # noqa: BLE001 - report which rule, not just "failed"
            failed.append(
                {"rule_type": r.get("rule_type"),
                 "origin": r.get("origin"),
                 "error": str(exc).strip().splitlines()[0][:160]}
            )

    result: dict[str, Any] = {
        "topology": topo_path,
        "feature_classes": added,
        "rules_added": applied,
        "rules_failed": failed,
        "cluster_tolerance": cluster_tolerance,
    }

    if validate:
        arcpy.management.ValidateTopology(topo_path)
        result["validated"] = True
    if failed:
        return {"ok": False,
                "error": f"{len(failed)} topology rule(s) could not be added.",
                "data": result}
    return ok(result)


@guard
def validate_topology(
    topology: str,
    visible_extent_only: bool = False,
    count_errors: bool = True,
) -> dict:
    """Validate a topology and report how many errors of each type exist.

    Counting is done by exporting the topology's errors to a scratch location
    and counting those, which is the only unambiguous method: the geodatabase's
    ``GDB_Validation*Errors`` classes are SHARED between topology errors and
    attribute-rule VALIDATION errors (verified — a rule's error 8002 lands in
    the same table), so reading them directly would conflate the two.

    Pass ``count_errors=False`` to skip the export on a large topology where you
    only want the validation itself to run.
    """
    arcpy = ops._arcpy()
    arcpy.management.ValidateTopology(
        topology, "Visible_Extent" if visible_extent_only else "Full_Extent"
    )

    payload: dict[str, Any] = {"topology": str(topology), "validated": True}
    if not count_errors:
        return ok(payload)

    counts: dict[str, int] = {}
    samples: list[dict] = []
    base = f"topoerr_{abs(hash(str(topology))) % 100000}"
    scratch = arcpy.env.scratchGDB
    try:
        for suffix in ("point", "line", "poly"):
            stale = os.path.join(scratch, f"{base}_{suffix}")
            if arcpy.Exists(stale):
                arcpy.management.Delete(stale)
        arcpy.management.ExportTopologyErrors(topology, scratch, base)
        for suffix in ("point", "line", "poly"):
            candidate = os.path.join(scratch, f"{base}_{suffix}")
            if not arcpy.Exists(candidate):
                continue
            n = int(arcpy.management.GetCount(candidate)[0])
            if n:
                counts[suffix] = n
            fields = [f.name for f in arcpy.ListFields(candidate)]
            wanted = [f for f in ("RuleType", "OriginObjectClassName",
                                  "DestinationObjectClassName", "isException")
                      if f in fields]
            if wanted and n:
                with arcpy.da.SearchCursor(candidate, wanted) as cur:
                    for i, row in enumerate(cur):
                        if i >= 10:
                            break
                        samples.append(dict(zip(wanted, [ops._scalar(v) for v in row])))
    except Exception as exc:  # noqa: BLE001 - the validation itself still stands
        payload["count_error"] = str(exc).strip().splitlines()[0][:200]

    total = sum(counts.values())
    payload.update(
        {
            "error_counts": counts,
            "total_errors": total,
            "clean": total == 0,
            "sample_errors": samples,
        }
    )
    return ok(payload)


@guard
def export_topology_errors(topology: str, out_workspace: str, out_base_name: str) -> dict:
    """Export topology errors to feature classes you can open and inspect."""
    arcpy = ops._arcpy()
    arcpy.management.ExportTopologyErrors(topology, out_workspace, out_base_name)
    produced = {}
    for suffix in ("point", "line", "poly"):
        candidate = os.path.join(out_workspace, f"{out_base_name}_{suffix}")
        try:
            if arcpy.Exists(candidate):
                produced[suffix] = {
                    "dataset": candidate,
                    "count": int(arcpy.management.GetCount(candidate)[0]),
                }
        except Exception:  # noqa: BLE001
            continue
    return ok({"topology": str(topology), "exported": produced})


@guard
def check_geometry(datasets: list[str], out_table: str) -> dict:
    """Find geometry problems (self-intersection, null geometry, bad rings)."""
    arcpy = ops._arcpy()
    arcpy.management.CheckGeometry(datasets, out_table)

    problems: list[dict] = []
    try:
        fields = ["CLASS", "FEATURE_ID", "PROBLEM"]
        with arcpy.da.SearchCursor(out_table, fields) as cur:
            for i, row in enumerate(cur):
                if i >= 200:  # a report, not a data dump
                    break
                problems.append(dict(zip(fields, [ops._scalar(v) for v in row])))
    except Exception:  # noqa: BLE001 - schema differs across versions
        pass

    total = 0
    try:
        total = int(arcpy.management.GetCount(out_table)[0])
    except Exception:  # noqa: BLE001
        pass

    by_problem: dict[str, int] = {}
    for p in problems:
        key = str(p.get("PROBLEM"))
        by_problem[key] = by_problem.get(key, 0) + 1

    return ok(
        {
            "datasets": datasets,
            "out_table": out_table,
            "total_problems": total,
            "by_problem": by_problem,
            "sample": problems[:50],
            "clean": total == 0,
        }
    )


@guard
def repair_geometry(dataset: str, delete_null: bool = True) -> dict:
    """Repair geometry problems in place."""
    arcpy = ops._arcpy()
    arcpy.management.RepairGeometry(
        dataset, "DELETE_NULL" if delete_null else "KEEP_NULL"
    )
    cache.invalidate(dataset)
    return ok({"dataset": dataset, "repaired": True, "deleted_null": delete_null})


@guard
def validate_all(
    datasets: list[str] | None = None,
    topologies: list[str] | None = None,
    scratch_workspace: str | None = None,
) -> dict:
    """One-call health report: geometry + topology + attribute-rule inventory.

    This is the tool to reach for when the user asks "is my data clean?" — it
    answers in a single round trip instead of a dozen.
    """
    arcpy = ops._arcpy()
    report: dict[str, Any] = {"geometry": [], "topology": [], "attribute_rules": []}

    scratch = scratch_workspace or arcpy.env.scratchGDB
    for i, ds in enumerate(datasets or []):
        out_table = os.path.join(scratch, f"chk_geom_{i}")
        try:
            if arcpy.Exists(out_table):
                arcpy.management.Delete(out_table)
            envelope = check_geometry([ds], out_table)
            payload = envelope.get("data", {}) if envelope.get("ok") else {}
            report["geometry"].append(
                {
                    "dataset": ds,
                    "ok": envelope.get("ok", False),
                    "total_problems": payload.get("total_problems"),
                    "by_problem": payload.get("by_problem"),
                    "error": None if envelope.get("ok") else envelope.get("error"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            report["geometry"].append({"dataset": ds, "ok": False, "error": str(exc)})

        try:
            desc = arcpy.Describe(ds)
            rules = getattr(desc, "attributeRules", None) or []
            if rules:
                report["attribute_rules"].append(
                    {
                        "dataset": ds,
                        "rule_count": len(rules),
                        "types": sorted({getattr(r, "type", "?") for r in rules}),
                    }
                )
        except Exception:  # noqa: BLE001
            pass

    for topo in topologies or []:
        envelope = validate_topology(topo)
        payload = envelope.get("data", {}) if envelope.get("ok") else {}
        report["topology"].append(
            {
                "topology": topo,
                "ok": envelope.get("ok", False),
                "total_errors": payload.get("total_errors"),
                "error_counts": payload.get("error_counts"),
                "error": None if envelope.get("ok") else envelope.get("error"),
            }
        )

    geom_bad = sum(g.get("total_problems") or 0 for g in report["geometry"])
    topo_bad = sum(t.get("total_errors") or 0 for t in report["topology"])
    report["summary"] = {
        "geometry_problems": geom_bad,
        "topology_errors": topo_bad,
        "clean": geom_bad == 0 and topo_bad == 0,
        "datasets_checked": len(report["geometry"]),
        "topologies_checked": len(report["topology"]),
    }
    return ok(report)
