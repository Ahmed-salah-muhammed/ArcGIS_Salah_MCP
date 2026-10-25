"""Teach the model the INSTALLED ArcGIS API for Python: symbol search, docs, members, version, module map.

The index is built from the installed ``arcgis`` package with pkgutil + inspect (never from memory),
cached on disk keyed by the arcgis version, and searched in-process. Deprecation is read from the
library's own marker: its ``deprecated`` decorator writes ``.. deprecated::`` into the docstring.
``arcgis.learn`` (138 s cold import, measured) is skipped unless asked for.
"""
from __future__ import annotations

import importlib.metadata as md
import inspect
import json
import os
import pkgutil
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .._result import err, guard, ok
from . import _imports

# Measured import cost: arcgis.learn 138 s; arcgis.map.dataclasses (pydantic models of the web-map spec,
# not an API anyone calls directly) 37 s of a 50 s build. Skipped unless include_heavy=True.
HEAVY = ("arcgis.learn", "arcgis.map.dataclasses")
REPLACEMENTS = {
    "arcgis.gis.ContentManager.add": "gis.content.folders.get(<folder>).add(ItemProperties(title=..., "
                                     "item_type=...), file=path).result()  (Folder.add returns a Job)",
    "arcgis.gis.Item.share": "item.sharing.sharing_level = 'PRIVATE'|'ORGANIZATION'|'EVERYONE'; "
                             "item.sharing.groups.add(group)",
    "arcgis.gis.Item.unshare": "item.sharing.groups.remove(group) / item.sharing.sharing_level = 'PRIVATE'",
    "arcgis.gis.ContentManager.create_folder": "gis.content.folders.create(name, exist_ok=True)",
    "arcgis.gis.ContentManager.delete_folder": "gis.content.folders.get(name).delete()",
    "arcgis.gis.ContentManager.rename_folder": "gis.content.folders.get(name).rename(new_name)",
    "arcgis.mapping.WebMap": "arcgis.map.Map (package arcgis-mapping)",
    "arcgis.mapping.WebScene": "arcgis.map.Scene (package arcgis-mapping)",
}
MODULE_MAP = [
    ("arcgis.gis", ["GIS", "Item", "User", "Group", "ContentManager", "UserManager", "GroupManager", "RoleManager",
                    "ItemProperties", "ItemTypeEnum", "SharingManager"], "Content, users, groups, sharing, search"),
    ("arcgis.gis.admin", ["AGOLAdminManager", "PortalAdminManager", "AGOLUsageReports", "CreditManager",
                          "LicenseManager", "WebhookManager"], "Org administration (gis.admin)"),
    ("arcgis.gis.server", ["Server", "ServerManager", "ServiceManager", "LogManager"], "ArcGIS Server admin"),
    ("arcgis.gis.tasks", ["TaskManager"], "Scheduled notebook tasks (user.tasks)"),
    ("arcgis.notebook", ["execute_notebook", "list_runtimes"], "Run portal notebooks"),
    ("arcgis.features", ["FeatureLayer", "Table", "FeatureLayerCollection", "FeatureSet", "Feature", "GeoAccessor"],
     "Query/edit data, schema, views, SEDF (df.spatial)"),
    ("arcgis.features.managers", ["FeatureLayerCollectionManager", "FeatureLayerManager", "SyncManager",
                                  "AttachmentManager"], "Service definition: fields, capabilities, views"),
    ("arcgis.features.analysis", ["create_buffers", "aggregate_points", "summarize_within", "find_hot_spots",
                                  "join_features", "enrich_layer"], "Hosted spatial analysis (credits on AGOL)"),
    ("arcgis.map", ["Map", "Scene"], "Web maps / web scenes (package arcgis-mapping)"),
    ("arcgis.layers", ["VectorTileLayer", "MapImageLayer", "SceneLayer"], "Non-feature layers"),
    ("arcgis.geometry", ["Geometry", "Point", "Polygon", "project", "filters"], "Geometry ops and filters"),
    ("arcgis.geocoding", ["get_geocoders", "geocode", "batch_geocode", "reverse_geocode", "suggest"],
     "Geocoding (credits on AGOL)"),
    ("arcgis.geoprocessing", ["import_toolbox"], "GP services as Python functions"),
    ("arcgis.network", ["analysis"], "Routing / service areas / OD (credits)"),
    ("arcgis.raster", ["ImageryLayer", "analytics"], "Imagery and raster analytics"),
    ("arcgis.geoenrichment", ["enrich", "Country"], "Demographic enrichment (credits)"),
    ("arcgis.apps.dashboards", ["DashboardManager"], "Dashboards"),
    ("arcgis.apps.storymap", ["StoryMap", "Briefing", "Collection"], "StoryMaps"),
    ("arcgis.apps.survey123", ["SurveyManager", "Survey"], "Survey123"),
    ("arcgis.apps.expbuilder", ["WebExperience"], "Experience Builder"),
    ("arcgis.apps.workforce", ["Project", "create_project"], "Workforce"),
    ("arcgis.apps.itemgraph", ["create_dependency_graph", "ItemGraph"], "Item dependency graph"),
    ("arcgis.auth", ["EsriSession"], "Advanced auth"),
]

_INDEX: list[dict] | None = None
_STATUS: dict[str, Any] = {"state": "idle", "seconds": None, "modules": {}, "source": None}
_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()


def _version(dist: str) -> str | None:
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return None


def _cache_file(version: str) -> Path:
    base = os.environ.get("ARCGIS_SALAH_CACHE_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache"), "arcgis_pro_salah_mcp")
    return Path(base) / f"arcgis_api_index_{version}.json"


def _summary(obj: Any) -> tuple[str, str | None]:
    doc = inspect.getdoc(obj) or ""
    first = next((ln.strip() for ln in doc.splitlines() if ln.strip() and not ln.strip().startswith("..")), "")
    dep = None
    m = re.search(r"\.\. deprecated::\s*([^\n]*(?:\n\s+[^\n]+)*)", doc)
    if m:
        dep = " ".join(m.group(1).split())[:200]
    return first[:200], dep


def _sig(obj: Any) -> str:
    try:
        return str(inspect.signature(obj))[:400]
    except (TypeError, ValueError):
        return ""


def _entry(qname: str, kind: str, obj: Any) -> dict:
    summary, dep = _summary(obj)
    out = {"q": qname, "k": kind, "s": _sig(obj) if kind in ("function", "method", "class") else "", "d": summary}
    if dep or qname in REPLACEMENTS:
        out["dep"] = dep or "deprecated"
    return out


def public_modules(root: str, paths: list[str], include_heavy: bool = False) -> tuple[list[str], list[str]]:
    """Public module names under a package WITHOUT importing anything (pkgutil.walk_packages imports every
    package to recurse - that pulled in arcgis.learn and made the first build take 256 s). -> (names, skipped)"""
    names, skipped = [root], []
    stack = [(root, list(paths))]
    while stack:
        pkg, pkg_paths = stack.pop()
        for info in pkgutil.iter_modules(pkg_paths, pkg + "."):
            leaf = info.name.rsplit(".", 1)[1]
            if leaf.startswith("_") or leaf in ("tests", "test"):
                continue
            if info.name.startswith(HEAVY) and not include_heavy:
                skipped.append(info.name)
                continue
            names.append(info.name)
            if info.ispkg:
                sub = [os.path.join(p, leaf) for p in pkg_paths if os.path.isdir(os.path.join(p, leaf))]
                stack.append((info.name, sub))
    return sorted(set(names), key=lambda n: (n.count("."), n)), skipped


def build_index(include_heavy: bool = False) -> list[dict]:
    """Walk the installed arcgis package. Public modules only; each object indexed once (shallowest path)."""
    arcgis = _imports.load("arcgis")

    started = time.perf_counter()
    statuses: dict[str, str] = {}
    public, skipped = public_modules("arcgis", list(arcgis.__path__), include_heavy)
    for name in skipped:
        statuses[name] = "skipped (heavy)"
    entries: list[dict] = []
    seen: set[int] = set()
    slow: list[tuple[float, str]] = []
    for name in public:
        t0 = time.perf_counter()
        try:
            mod = _imports.load(name)  # one module at a time under the shared import lock
        except Exception as exc:  # noqa: BLE001
            statuses[name] = f"failed: {type(exc).__name__}"
            continue
        took = time.perf_counter() - t0
        if took > 1:
            slow.append((round(took, 1), name))
        statuses[name] = "ok"
        entries.append({"q": name, "k": "module", "s": "", "d": _summary(mod)[0]})
        for attr, obj in list(vars(mod).items()):
            if attr.startswith("_") or id(obj) in seen:
                continue
            if not str(getattr(obj, "__module__", "") or "").startswith("arcgis"):
                continue
            if inspect.isclass(obj):
                seen.add(id(obj))
                entries.append(_entry(f"{name}.{attr}", "class", obj))
                for mname, member in list(vars(obj).items()):
                    if mname.startswith("_"):
                        continue
                    if isinstance(member, property):
                        if member.fget is not None:
                            entries.append(_entry(f"{name}.{attr}.{mname}", "property", member.fget))
                    elif isinstance(member, (staticmethod, classmethod)):
                        entries.append(_entry(f"{name}.{attr}.{mname}", "method", member.__func__))
                    elif inspect.isfunction(member):
                        entries.append(_entry(f"{name}.{attr}.{mname}", "method", member))
            elif inspect.isfunction(obj):
                seen.add(id(obj))
                entries.append(_entry(f"{name}.{attr}", "function", obj))
    with _LOCK:
        _STATUS.update(modules=statuses, seconds=round(time.perf_counter() - started, 1),
                       slowest_imports=sorted(slow, reverse=True)[:8])
    return entries


def index(include_heavy: bool = False) -> list[dict]:
    """The cached index (memory -> disk -> build). Built once per arcgis version; a second caller (the
    warm-up and a search arriving together) waits for the first build instead of starting another."""
    global _INDEX
    if _INDEX is not None and not include_heavy:
        return _INDEX
    with _BUILD_LOCK:
        if _INDEX is not None and not include_heavy:
            return _INDEX
        version = _version("arcgis") or "unknown"
        path = _cache_file(version)
        if path.exists() and not include_heavy:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                _INDEX = data["entries"]
                with _LOCK:
                    _STATUS.update(state="ready", source=str(path), modules=data.get("modules", {}))
                return _INDEX
            except Exception:  # noqa: BLE001 - corrupt cache: rebuild
                pass
        with _LOCK:
            _STATUS["state"] = "building"
        entries = build_index(include_heavy)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": version, "modules": _STATUS["modules"], "entries": entries}),
                            encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        if not include_heavy:
            _INDEX = entries
        with _LOCK:
            _STATUS.update(state="ready", source="built")
        return entries


def status() -> dict:
    with _LOCK:
        return {k: v for k, v in _STATUS.items() if k != "modules"} | {
            "entries": len(_INDEX) if _INDEX is not None else None}


def search_entries(entries: list[dict], query: str, module: str | None = None, limit: int = 15) -> list[dict]:
    tokens = [t for t in re.split(r"[\s._/]+", query.lower()) if t]
    if not tokens:
        return []
    joined = "_".join(tokens)
    scored = []
    for e in entries:
        q = e["q"]
        if module and not q.startswith(module):
            continue
        ql = q.lower()
        name = ql.rsplit(".", 1)[-1]
        doc = e.get("d", "").lower()
        score = 0
        for t in tokens:
            if t == name:
                s = 10
            elif name.startswith(t):
                s = 6
            elif t in name:
                s = 4
            elif t in ql:
                s = 2
            elif t in doc:
                s = 1
            else:
                s = 0
                break
            score += s
        if not score:
            continue
        if len(tokens) > 1 and joined == name:   # "create view" -> create_view
            score += 12
        elif len(tokens) > 1 and joined in name:
            score += 6
        if e.get("dep"):
            score -= 3
        if e["k"] in ("class", "function"):
            score += 1
        scored.append((score, -len(q), e))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [{"qualified_name": e["q"], "kind": e["k"], "signature": e.get("s") or None, "summary": e.get("d"),
             "deprecated": e.get("dep"), "use_instead": REPLACEMENTS.get(e["q"])} for _, _, e in scored[:limit]]


def _resolve(symbol: str) -> tuple[Any, str]:
    sym = symbol.strip()
    if not sym.startswith("arcgis"):
        hits = [e["q"] for e in index() if e["q"].endswith("." + sym)]
        if len(hits) == 1:
            sym = hits[0]
        elif len(hits) > 1:
            raise LookupError(f"'{symbol}' is ambiguous: " + ", ".join(sorted(hits, key=len)[:10]))
        else:
            raise LookupError(f"'{symbol}' not found; search with action=search first.")
    parts = sym.split(".")
    obj = None
    rest: list[str] = []
    for i in range(len(parts), 0, -1):
        try:
            obj = _imports.load(".".join(parts[:i]))
            rest = parts[i:]
            break
        except Exception:  # noqa: BLE001
            continue
    if obj is None:
        raise LookupError(f"Cannot import any prefix of '{sym}'.")
    for r in rest:
        obj = getattr(obj, r)
    return obj, sym


def _section(doc: str, section: str | None) -> str:
    if not section:
        return doc
    low = doc.lower()
    marks = {
        "parameters": [r"\*\*parameter\*\*", r"^\s*:param", r"^\s*parameters?\s*$", r"^\s*argument"],
        "returns": [r"^\s*:returns?:", r"^\s*returns?\s*:?\s*$", r"^\s*:return:"],
        "examples": [r"\.\. code-block::", r"^\s*>>>", r"usage example"],
    }.get(section.lower())
    if not marks:
        return doc
    starts = [m.start() for pat in marks for m in re.finditer(pat, low, re.M)]
    if not starts:
        return f"(no '{section}' section found)\n\n" + doc[:1500]
    start = min(starts)
    if section.lower() == "examples":
        return doc[start:]
    stop_marks = [m.start() for m in re.finditer(r"\.\. code-block::|^\s*:returns?:|^\s*returns?\s*$", low, re.M)
                  if m.start() > start + 10]
    return doc[start:min(stop_marks) if stop_marks else len(doc)]


@guard
def api(action: str = "search", query: str | None = None, symbol: str | None = None, module: str | None = None,
        section: str | None = None, limit: int = 15, max_chars: int = 4000, include_heavy: bool = False) -> dict:
    action = (action or "search").lower()
    if action == "version":
        mods = dict(_STATUS.get("modules") or {})
        return ok({"python": sys.version.split()[0], "arcgis": _version("arcgis"),
                   "arcgis_mapping": _version("arcgis-mapping"), "pandas": _version("pandas"),
                   "numpy": _version("numpy"), "index": status(),
                   "subpackages_failed": {k: v for k, v in mods.items() if v != "ok"} or None,
                   "subpackages_ok": sum(1 for v in mods.values() if v == "ok") or None})
    if action == "module_map":
        rows = []
        for modname, keys, purpose in MODULE_MAP:
            try:
                mod = _imports.load(modname)
                present = [k for k in keys if hasattr(mod, k)]
                rows.append({"module": modname, "entry_points": present,
                             "missing": [k for k in keys if k not in present] or None, "used_for": purpose})
            except Exception as exc:  # noqa: BLE001
                rows.append({"module": modname, "import_error": type(exc).__name__, "used_for": purpose})
        return ok({"arcgis": _version("arcgis"), "modules": rows,
                   "replacements": REPLACEMENTS})
    if action == "search":
        if not query:
            return err("query is required, e.g. 'create view', 'sharing level', 'append upsert'.",
                       code="bad_argument")
        started = time.perf_counter()
        hits = search_entries(index(include_heavy), query, module, max(1, min(int(limit or 15), 50)))
        return ok({"query": query, "results": hits, "index_entries": len(index()),
                   "seconds": round(time.perf_counter() - started, 2),
                   "next": "portal_api action=doc symbol=<qualified_name> before you call it"})
    if not symbol:
        return err("symbol is required (a qualified name from action=search, or e.g. 'Item.export').",
                   code="bad_argument")
    obj, qname = _resolve(symbol)
    target = obj.fget if isinstance(obj, property) else obj
    if action == "doc":
        doc = inspect.getdoc(target) or ""
        summary, dep = _summary(target)
        source = None
        try:
            source = f"{inspect.getsourcefile(target)}:{inspect.getsourcelines(target)[1]}"
        except Exception:  # noqa: BLE001
            pass
        text = _section(doc, section)
        return ok({"qualified_name": qname, "kind": type(obj).__name__, "signature": _sig(target) or None,
                   "deprecated": dep, "use_instead": REPLACEMENTS.get(qname), "source": source,
                   "doc": text[:max_chars] + (f"... [{len(text) - max_chars} more chars]" if len(text) > max_chars
                                              else "")})
    if action == "members":
        out = []
        for n in sorted(dir(target)):
            if n.startswith("_"):
                continue
            try:
                member = inspect.getattr_static(target, n)
            except AttributeError:
                continue
            kind = "property" if isinstance(member, property) else (
                "method" if callable(member) or isinstance(member, (staticmethod, classmethod)) else "attribute")
            fn = member.fget if isinstance(member, property) else getattr(member, "__func__", member)
            first, dep = _summary(fn) if callable(fn) else ("", None)
            out.append({"name": n, "kind": kind, "summary": first or None, "deprecated": dep})
        return ok({"qualified_name": qname, "members": out[:300]})
    return err("action must be search | doc | members | version | module_map.", code="bad_action")
