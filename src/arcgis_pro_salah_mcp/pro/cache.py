"""Process-local schema cache for the ``pro_*`` layer.

Why
---
Measured on ArcGIS Pro 3.x, per dataset:

    arcpy.Describe                  ~73 ms
    arcpy.ListFields               ~172 ms
    arcpy.management.GetCount      ~189 ms   (a real GP tool invocation)

so one ``pro_describe_layer`` pays ~430 ms of backend work — and an agent
conversation asks about the same handful of datasets over and over. Caching that
triple turns every repeat into a dict lookup.

Invalidation
------------
Two mechanisms, because neither is sufficient alone:

* **Stamp check** — for a *file* dataset (shapefile, GeoTIFF) the file's own
  mtime is a reliable "has this changed?" signal, so it is used.

  For a dataset **inside a geodatabase it is deliberately NOT used**. The
  obvious implementation — mtime of the ``.gdb`` folder — was measured and is
  actively wrong: ArcGIS writes and removes ``.lock`` files in that folder on
  every *read*, so the folder mtime changes constantly and the cache never
  hits (measured: 0 hits, 2 misses across two identical calls). Geodatabase
  entries therefore rely on explicit invalidation plus the TTL below.

* **TTL** — every entry expires after :data:`TTL_SECONDS` regardless. This is
  the safety net for a geodatabase edited by someone else while the server is
  running: stale data can survive at most one TTL window, never the whole
  session.
* **Explicit invalidation** — every write op in ``ops.py`` calls
  :func:`invalidate` on the datasets it touched. A geodatabase write does not
  reliably bump an mtime we can observe, so we never rely on the stamp for
  changes we made ourselves.

The cache is per-process and deliberately unbounded-but-small: it holds one
small dict per dataset the agent has looked at, which is bounded by the size of
the conversation, not the size of the data.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

# How long a cached entry may live before it is reloaded regardless of its
# stamp. Long enough that a burst of agent questions about one dataset is served
# from memory, short enough that an external edit surfaces quickly. Override
# with ARCGIS_SALAH_CACHE_TTL (seconds; 0 disables caching entirely).
try:
    TTL_SECONDS = float(os.environ.get("ARCGIS_SALAH_CACHE_TTL", "300"))
except ValueError:
    TTL_SECONDS = 300.0

_LOCK = threading.Lock()
_ENTRIES: dict[str, tuple[Any, float, dict]] = {}
_HITS = 0
_MISSES = 0

# A dataset path points *inside* a container (``…/roads.gdb/streets``) whose own
# mtime moves when the data changes. These are the container suffixes we know
# how to walk up to.
_CONTAINERS = (".gdb", ".sde", ".gpkg")


def key_for(dataset: str) -> str:
    """Normalise a dataset path into a stable cache key."""
    return os.path.normcase(os.path.abspath(str(dataset).strip()))


def _container(dataset: str) -> Path | None:
    """The geodatabase this dataset lives in, or ``None`` for a plain file."""
    p = Path(dataset)
    for parent in (p, *p.parents):
        if parent.suffix.lower() in _CONTAINERS:
            return parent
    return None


def stamp(dataset: str) -> Any:
    """Cheap change-detection stamp, or ``None`` when it cannot be determined.

    ``None`` is not an error — it means freshness cannot be proven from the
    filesystem, so the entry falls back to explicit invalidation and the TTL.
    Returns ``None`` for anything inside a geodatabase: ArcGIS churns ``.lock``
    files there on every read, so the container mtime is pure noise.
    """
    try:
        p = Path(dataset)
        if _container(dataset) is not None:
            return None  # inside a .gdb/.sde/.gpkg — see the module docstring
        if not p.exists():
            return None
        return p.stat().st_mtime_ns
    except Exception:  # noqa: BLE001 - a stamp must never break a lookup
        return None


def get_or_load(dataset: str, loader: Callable[[], dict]) -> dict:
    """Return the cached payload for ``dataset``, computing it via ``loader`` once."""
    global _HITS, _MISSES
    if TTL_SECONDS <= 0:
        return loader()  # caching disabled

    k = key_for(dataset)
    current = stamp(dataset)
    now = time.monotonic()

    with _LOCK:
        hit = _ENTRIES.get(k)
        if hit is not None:
            cached_stamp, stored_at, payload = hit
            fresh = (now - stored_at) < TTL_SECONDS
            unchanged = cached_stamp == current
            if fresh and unchanged:
                _HITS += 1
                return payload
            _ENTRIES.pop(k, None)

    # Load outside the lock: the loader calls into arcpy and can take ~0.5 s,
    # and holding the lock across it would serialise unrelated datasets.
    payload = loader()

    with _LOCK:
        _MISSES += 1
        _ENTRIES[k] = (current, time.monotonic(), payload)
    return payload


def invalidate(*datasets: str) -> int:
    """Drop cached entries for the given datasets. Returns how many were removed."""
    removed = 0
    with _LOCK:
        for d in datasets:
            if d and _ENTRIES.pop(key_for(d), None) is not None:
                removed += 1
    return removed


def invalidate_all() -> int:
    """Drop everything (used after ``run_gp`` / ``execute_code``, which can touch
    datasets we cannot enumerate)."""
    with _LOCK:
        n = len(_ENTRIES)
        _ENTRIES.clear()
    return n


def stats() -> dict:
    """Observability: is the cache actually earning its keep?"""
    with _LOCK:
        total = _HITS + _MISSES
        return {
            "entries": len(_ENTRIES),
            "hits": _HITS,
            "misses": _MISSES,
            "hit_rate": round(_HITS / total, 3) if total else None,
            "ttl_seconds": TTL_SECONDS,
        }
