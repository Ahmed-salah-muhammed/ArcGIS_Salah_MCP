"""Named ArcGIS API for Python connections, signing in through ArcGIS Pro whenever Pro is signed in.

``source="auto"`` (the default) tries, in order:

1. **Pro** - ``GIS("pro")`` reuses the portal ArcGIS Pro is signed in to (ArcGIS Online or Enterprise,
   SSO/SAML included). Measured 1.0 s, and it worked from a thread other than the one that imported
   arcpy. The token comes from Pro, so it refreshes as long as Pro stays signed in.
2. **profile** - a stored arcgis profile (``profile`` or ``ARCGIS_PROFILE``; credentials in the OS keyring).
3. **credentials** - ``url`` + ``username`` + ``password_env`` (the NAME of an env var, never the password).
4. **api_key** - ``api_key_env`` (env var NAME).
5. **anonymous** - public content on ``ARCGIS_PORTAL_URL``.

Several connections can be open at once (``default``, ``source``, ``target`` ...) so content can move
between orgs. Every portal tool takes ``connection`` and the ``default`` one connects itself on first use.
"""
from __future__ import annotations

import importlib
import re
import sys
import threading
import time
from typing import Any

from .._result import err, guard, ok
from ..config import CONFIG
from . import _imports, safety

_CONNS: dict[str, dict] = {}
_LOCK = threading.RLock()
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
SOURCES = ("auto", "pro", "profile", "credentials", "api_key", "anonymous")


def _gis_class():
    try:
        _imports.ensure_base()
        from arcgis.gis import GIS  # type: ignore
        return GIS
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError('ArcGIS API for Python is not installed. Run: pip install "arcgis-pro-salah-mcp[portal]" '
                           "(it ships with ArcGIS Pro's arcgispro-py3).") from exc


def _ensure_arcpy_on_its_thread() -> None:
    """GIS("pro") imports arcpy. Import it on THE arcpy thread first, or this worker would become the
    importing thread and PostgreSQL calls on the arcpy thread would fail (see arcpy_thread)."""
    if "arcpy" in sys.modules:
        return
    from .. import arcpy_thread

    arcpy_thread.run(importlib.import_module, "arcpy")


def _pro_gis(GIS: Any) -> Any:
    _ensure_arcpy_on_its_thread()
    return GIS("pro")


def _open(source: str, url: str | None, profile: str | None, username: str | None, password_env: str | None,
          api_key_env: str | None, verify_cert: bool) -> tuple[Any, str, list[str]]:
    """Open a GIS for ``source``; returns (gis, source actually used, notes about skipped sources)."""
    GIS = _gis_class()
    notes: list[str] = []
    kwargs = {} if verify_cert else {"verify_cert": False}
    if source in ("auto", "pro"):
        try:
            return _pro_gis(GIS), "pro", notes
        except Exception as exc:  # noqa: BLE001
            if source == "pro":
                raise RuntimeError(f"ArcGIS Pro sign-in could not be used ({safety.scrub(str(exc))[:200]}). "
                                   "Open ArcGIS Pro and sign in (Settings > Portals), or pick another source.") from exc
            notes.append(f"pro: not signed in / unavailable ({type(exc).__name__})")
    profile = profile or (CONFIG.portal_profile if source == "auto" else None)
    if source in ("auto", "profile") and profile:
        return (GIS(url, profile=profile, **kwargs) if url else GIS(profile=profile, **kwargs)), "profile", notes
    if source == "profile":
        raise ValueError("source='profile' needs profile= (or ARCGIS_PROFILE).")
    if source in ("auto", "credentials") and username and password_env:
        password, problem = safety.secret_from_env(password_env)
        if problem:
            raise ValueError(problem["error"])
        return GIS(url or CONFIG.portal_url, username, password, **kwargs), "credentials", notes
    if source == "credentials":
        raise ValueError("source='credentials' needs username and password_env (the NAME of an env var).")
    if source in ("auto", "api_key") and api_key_env:
        key, problem = safety.secret_from_env(api_key_env, "API key")
        if problem:
            raise ValueError(problem["error"])
        return GIS(url or CONFIG.portal_url, api_key=key, **kwargs), "api_key", notes
    if source == "api_key":
        raise ValueError("source='api_key' needs api_key_env (the NAME of an env var).")
    return GIS(url or CONFIG.portal_url, **kwargs), "anonymous", notes


def _describe(name: str) -> dict:
    entry = _CONNS[name]
    gis = entry["gis"]
    props = gis.properties
    me = gis.users.me
    out = {
        "connection": name, "source": entry["source"], "url": str(gis.url),
        "platform": "ArcGIS Enterprise" if props.get("isPortal") else "ArcGIS Online",
        "org": props.get("name"), "org_id": props.get("id"), "portal_version": getattr(gis, "version", None),
        "anonymous": me is None, "connected_at": entry["connected_at"],
    }
    if me is not None:
        privileges = list(me.get("privileges") or [])
        out.update({
            "username": me.username, "fullName": me.get("fullName"), "role": me.get("role"),
            "roleId": me.get("roleId"), "user_type": me.get("userLicenseTypeId"),
            "is_admin": me.get("role") == "org_admin" or any(p.startswith("portal:admin:") for p in privileges),
            "privileges_count": len(privileges),
            "can": {
                "create_items": "portal:user:createItem" in privileges,
                "publish_features": "portal:publisher:publishFeatures" in privileges,
                "share_public": "portal:user:shareToPublic" in privileges,
                "create_groups": "portal:user:createGroup" in privileges,
                "geocode": "premium:user:geocode" in privileges,
                "spatial_analysis": "premium:user:spatialanalysis" in privileges,
                "manage_users": any(p.startswith("portal:admin:") and "User" in p for p in privileges),
            },
        })
    if not props.get("isPortal"):
        out["available_credits"] = props.get("availableCredits")
    if entry.get("notes"):
        out["notes"] = entry["notes"]
    return out


def get(name: str = "default"):
    """The GIS for ``name``. ``default`` signs itself in on first use (Pro first, see the module doc).

    Every portal op starts here, so it is also where the base arcgis modules are guaranteed fully imported
    before any op touches them (see _imports).
    """
    with _LOCK:
        entry = _CONNS.get(name)
        if entry is not None:
            _imports.ensure_base()
            return entry["gis"]
        if name != "default":
            raise RuntimeError(f"No portal connection named '{name}'. Call portal_connect(name='{name}', ...) first.")
        gis, used, notes = _open("auto", None, None, None, None, None, True)
        _CONNS[name] = {"gis": gis, "source": used, "recipe": {"source": "auto"}, "notes": notes,
                        "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        return gis


def is_online(gis: Any) -> bool:
    try:
        return not gis.properties.get("isPortal", False)
    except Exception:  # noqa: BLE001
        return "arcgis.com" in str(getattr(gis, "url", ""))


def _looks_expired(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("498", "499", "invalid token", "token required", "token expired"))


def call(name: str, fn, *args, **kwargs):
    """Run ``fn(gis, ...)``; on an expired token (498/499) reconnect once with the same recipe and retry."""
    gis = get(name)
    try:
        return fn(gis, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        if not _looks_expired(exc):
            raise
        with _LOCK:
            recipe = dict((_CONNS.get(name) or {}).get("recipe") or {"source": "auto"})
            _CONNS.pop(name, None)
        reconnect(name, recipe)
        return fn(get(name), *args, **kwargs)


def reconnect(name: str, recipe: dict) -> None:
    gis, used, notes = _open(recipe.get("source", "auto"), recipe.get("url"), recipe.get("profile"),
                             recipe.get("username"), recipe.get("password_env"), recipe.get("api_key_env"),
                             recipe.get("verify_cert", True))
    with _LOCK:
        _CONNS[name] = {"gis": gis, "source": used, "recipe": recipe, "notes": notes,
                        "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def status() -> dict:
    """Cheap snapshot for pro_server(status): which connections are open (no network)."""
    with _LOCK:
        return {n: {"source": e["source"], "url": str(getattr(e["gis"], "url", "")),
                    "connected_at": e["connected_at"]} for n, e in _CONNS.items()}


@guard
def connect(action: str = "connect", name: str = "default", source: str = "auto", url: str | None = None,
            profile: str | None = None, username: str | None = None, password_env: str | None = None,
            api_key_env: str | None = None, verify_cert: bool = True) -> dict:
    action = (action or "connect").lower()
    if action == "list":
        with _LOCK:
            names = list(_CONNS)
        return ok({"connections": [_describe(n) for n in names] or [],
                   "hint": None if names else "Nothing open yet; any portal tool opens 'default' (Pro sign-in first)."})
    if not _NAME.match(name or ""):
        return err("name must start with a letter and use letters, digits, '_' or '-' (max 32).", code="bad_name")
    if action == "disconnect":
        with _LOCK:
            dropped = _CONNS.pop(name, None) is not None
        return ok({"connection": name, "disconnected": dropped})
    if action == "whoami":
        get(name)
        return ok(_describe(name))
    if action != "connect":
        return err("action must be connect | whoami | list | disconnect.", code="bad_action")
    source = (source or "auto").lower()
    if source not in SOURCES:
        return err(f"source must be one of {', '.join(SOURCES)}.", code="bad_source")
    recipe = {"source": source, "url": url, "profile": profile, "username": username, "password_env": password_env,
              "api_key_env": api_key_env, "verify_cert": verify_cert}
    started = time.perf_counter()
    reconnect(name, recipe)
    out = _describe(name)
    out["seconds"] = round(time.perf_counter() - started, 2)
    return ok(out)
