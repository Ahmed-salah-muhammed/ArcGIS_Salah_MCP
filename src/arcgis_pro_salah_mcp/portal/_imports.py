"""One lock for every ``arcgis`` import.

arcgis has circular imports inside the package, so two threads importing it at once hand each other
half-initialised modules. Measured over real stdio: a portal_publish that arrived while the background
warm-up was importing arcgis failed with "cannot import name 'FeatureCollection' from partially initialized
module 'arcgis.features.feature'", and a portal_search right after with "partially initialized module
'arcgis' has no attribute 'auth'". Every import of an arcgis module in this server goes through ``load``.
"""
from __future__ import annotations

import importlib
import threading
from types import ModuleType

_LOCK = threading.RLock()
BASE = ("arcgis", "arcgis.gis", "arcgis.features")
_BASE_READY = False


def load(name: str) -> ModuleType:
    """Import ``name`` (e.g. "arcgis.geocoding") under the shared lock; a no-op lookup once it is loaded."""
    with _LOCK:
        return importlib.import_module(name)


def ensure_base() -> None:
    """arcgis, arcgis.gis and arcgis.features - what every portal tool needs - fully imported, once."""
    global _BASE_READY
    if _BASE_READY:
        return
    with _LOCK:
        for name in BASE:
            importlib.import_module(name)
        _BASE_READY = True
