"""ArcPy discovery, self-healing re-exec, and background warm-up.

ArcPy ships only with ArcGIS Pro and lives in its bundled conda environment
(``arcgispro-py3``). If the MCP server is started with a *different* Python
(a generic venv, ``uvx``, …) then ``import arcpy`` fails, so this module can
re-execute the process under the right interpreter.

Why the warm-up exists
----------------------
``import arcpy`` was measured at **~25 seconds** on a normal ArcGIS Pro 3.x
install. Left alone, the agent's *first* ``pro_*`` call pays that entire cost,
which is what makes "simple" requests feel slow. :func:`start_warmup` imports
arcpy on a daemon thread the moment the server starts, so the import overlaps
with the user typing their first message and the first real call finds arcpy
already in ``sys.modules``.

Discovery order for the interpreter:
    1. CLI_ANYTHING_ARCGIS_PYTHON / ARCGIS_PRO_PYTHON (explicit override)
    2. Common install paths on Windows
    3. The Windows registry key  SOFTWARE\\ESRI\\ArcGISPro -> InstallDir
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

_COMMON_INSTALL_ROOTS = [
    r"C:\Program Files\ArcGIS\Pro",
    r"C:\Program Files (x86)\ArcGIS\Pro",
]

_REL_PY = r"bin\Python\envs\arcgispro-py3\python.exe"
_ENV_DIR_NAME = "arcgispro-py3"

# Filled in by the warm-up thread so pro_ping / pro_warmup_status can report
# progress without ever blocking on the import themselves.
_WARMUP: dict[str, object] = {
    "state": "idle",  # idle | importing | ready | failed | skipped
    "seconds": None,
    "error": None,
}
_WARMUP_LOCK = threading.Lock()
_WARMUP_THREAD: threading.Thread | None = None


# --- Interpreter discovery -------------------------------------------------

def running_under_arcgis_python() -> bool:
    """Cheap check: are we already inside ``arcgispro-py3``?

    Deliberately path-based rather than attempting ``import arcpy`` — the import
    costs ~25 s, so using it as a probe would defeat the whole point.
    """
    return _ENV_DIR_NAME in Path(sys.executable).parts


def _from_registry() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\ESRI\ArcGISPro") as key:
                    install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
            except OSError:
                continue
            candidate = Path(install_dir) / _REL_PY
            if candidate.exists():
                return str(candidate)
    except Exception:  # noqa: BLE001 - discovery must never raise
        return None
    return None


def find_arcgis_python() -> str | None:
    """Locate ArcGIS Pro's ``python.exe``, or return None."""
    for var in ("CLI_ANYTHING_ARCGIS_PYTHON", "ARCGIS_PRO_PYTHON"):
        env = os.environ.get(var)
        if env and Path(env).exists():
            return env

    for root in _COMMON_INSTALL_ROOTS:
        candidate = Path(root) / _REL_PY
        if candidate.exists():
            return str(candidate)

    return _from_registry()


def arcpy_available() -> bool:
    """True if ``arcpy`` imports here. NOTE: costs ~25 s on a cold interpreter."""
    try:
        import arcpy  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# --- Warm-up ---------------------------------------------------------------

def warmup_status() -> dict:
    """Snapshot of the background import — never blocks."""
    with _WARMUP_LOCK:
        return dict(_WARMUP)


def _warm() -> None:
    with _WARMUP_LOCK:
        _WARMUP["state"] = "importing"
    started = time.perf_counter()
    try:
        import arcpy  # noqa: F401

        # Touching the geoprocessor once forces the rest of the lazy machinery
        # to initialise, so the first real tool call is a pure dict lookup.
        try:
            arcpy.env.overwriteOutput = arcpy.env.overwriteOutput
        except Exception:  # noqa: BLE001 - best effort only
            pass
        elapsed = time.perf_counter() - started
        with _WARMUP_LOCK:
            _WARMUP.update(state="ready", seconds=round(elapsed, 2), error=None)
    except Exception as exc:  # noqa: BLE001 - a missing ArcGIS is not fatal
        elapsed = time.perf_counter() - started
        with _WARMUP_LOCK:
            _WARMUP.update(state="failed", seconds=round(elapsed, 2), error=str(exc))


def start_warmup() -> threading.Thread | None:
    """Import arcpy on a daemon thread. Idempotent; never raises."""
    global _WARMUP_THREAD
    with _WARMUP_LOCK:
        if _WARMUP_THREAD is not None:
            return _WARMUP_THREAD
        if "arcpy" in sys.modules:
            _WARMUP.update(state="ready", seconds=0.0, error=None)
            return None
        _WARMUP_THREAD = threading.Thread(
            target=_warm, name="arcpy-warmup", daemon=True
        )
    _WARMUP_THREAD.start()
    return _WARMUP_THREAD


# --- Startup orchestration -------------------------------------------------

def ensure_arcpy() -> None:
    """Re-execute under ArcGIS Pro's interpreter when this one lacks ArcPy.

    Uses the *cheap* path check rather than importing arcpy, and is a no-op when
    Pro cannot be found — the server still starts and every ``pro_*`` tool then
    returns a clean ``err(...)`` explaining what to install, which is far more
    useful than refusing to boot.
    """
    if running_under_arcgis_python():
        return
    if os.environ.get("_ARCGIS_SALAH_REEXEC") == "1":
        return  # already tried once; don't loop

    target = find_arcgis_python()
    if not target:
        return

    os.environ["_ARCGIS_SALAH_REEXEC"] = "1"
    os.execv(target, [target, "-m", "arcgis_pro_salah_mcp.server", *sys.argv[1:]])


def prepare() -> None:
    """Called once from ``server.main()`` before the MCP loop starts.

    Re-exec under ``arcgispro-py3`` if needed, then kick off the arcpy warm-up.
    Both steps are non-blocking (the re-exec either happens immediately or not
    at all), so the MCP client's ``initialize`` handshake is never delayed.
    """
    if os.environ.get("ARCGIS_SALAH_NO_REEXEC", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        ensure_arcpy()

    if os.environ.get("ARCGIS_SALAH_NO_WARMUP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        with _WARMUP_LOCK:
            _WARMUP["state"] = "skipped"
        return
    start_warmup()
