"""Turn arcgis objects into small JSON dicts. Never hand a raw arcgis object to the transport."""
from __future__ import annotations

import datetime as _dt
from typing import Any

MAX_STR = 2000


def iso(ms: Any) -> str | None:
    """Epoch milliseconds (UTC, as the portal stores dates) -> ISO 8601, or None."""
    if ms in (None, "", -1, 0):
        return None
    try:
        return _dt.datetime.fromtimestamp(float(ms) / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a property from an arcgis Item/User/Group (dict-like) without raising."""
    try:
        val = obj[key]
        return default if val is None else val
    except Exception:  # noqa: BLE001
        try:
            val = getattr(obj, key)
            return default if val is None else val
        except Exception:  # noqa: BLE001
            return default


def item_url(gis: Any, item_id: str) -> str:
    base = str(getattr(gis, "url", "") or "").rstrip("/")
    return f"{base}/home/item.html?id={item_id}"


def item(it: Any, gis: Any = None, full: bool = False) -> dict:
    out = {
        "id": _get(it, "id"), "title": _get(it, "title"), "type": _get(it, "type"),
        "owner": _get(it, "owner"), "access": _get(it, "access"), "url": _get(it, "url"),
        "modified": iso(_get(it, "modified")),
    }
    if gis is not None and out["id"]:
        out["item_url"] = item_url(gis, out["id"])
    if full:
        out.update({
            "snippet": _get(it, "snippet"), "description": text(_get(it, "description"), 1500),
            "tags": list(_get(it, "tags", []) or []), "typeKeywords": list(_get(it, "typeKeywords", []) or []),
            "created": iso(_get(it, "created")), "size_bytes": _get(it, "size"),
            "numViews": _get(it, "numViews"), "folder": _get(it, "ownerFolder"),
            "protected": _get(it, "protected"), "extent": _get(it, "extent"),
        })
    return out


def user(u: Any) -> dict:
    return {
        "username": _get(u, "username"), "fullName": _get(u, "fullName"), "email": _get(u, "email"),
        "role": _get(u, "role"), "roleId": _get(u, "roleId"), "userLicenseTypeId": _get(u, "userLicenseTypeId"),
        "level": _get(u, "level"), "disabled": _get(u, "disabled"), "provider": _get(u, "provider"),
        "lastLogin": iso(_get(u, "lastLogin")), "created": iso(_get(u, "created")),
    }


def group(g: Any) -> dict:
    caps = _get(g, "capabilities", []) or []
    return {
        "id": _get(g, "id"), "title": _get(g, "title"), "owner": _get(g, "owner"), "access": _get(g, "access"),
        "isInvitationOnly": _get(g, "isInvitationOnly"), "isViewOnly": _get(g, "isViewOnly"),
        "shared_update": "updateitemcontrol" in caps, "tags": list(_get(g, "tags", []) or []),
        "created": iso(_get(g, "created")),
    }


def role(r: Any) -> dict:
    privileges = []
    try:
        privileges = list(r.privileges or [])
    except Exception:  # noqa: BLE001
        pass
    return {"id": _get(r, "role_id") or _get(r, "id"), "name": _get(r, "name"),
            "description": _get(r, "description"), "privileges": privileges}


def text(value: Any, limit: int = MAX_STR) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... [{len(value) - limit} more chars]"
    return value


def jsonable(value: Any, depth: int = 0, max_items: int = 50, max_depth: int = 6) -> Any:
    """Best-effort conversion of anything (arcgis objects, DataFrames, numpy) to small JSON."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return text(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if depth >= max_depth:
        return text(repr(value), 200)
    mod = type(value).__module__ or ""
    name = type(value).__name__
    if mod.startswith("pandas") and name == "DataFrame":
        head = value.head(max_items)
        try:
            records = head.drop(columns=[c for c in ("SHAPE",) if c in head.columns]).to_dict("records")
        except Exception:  # noqa: BLE001
            records = head.astype(str).to_dict("records")
        return {"type": "DataFrame", "shape": list(value.shape), "columns": [str(c) for c in value.columns],
                "rows": jsonable(records, depth + 1, max_items)}
    if mod.startswith("pandas") and name == "Series":
        return jsonable(value.head(max_items).to_dict(), depth + 1, max_items)
    if mod.startswith("numpy"):
        try:
            return jsonable(value.tolist(), depth + 1, max_items)
        except Exception:  # noqa: BLE001
            return text(repr(value), 200)
    if mod.startswith("arcgis"):
        if name == "Item":
            return item(value)
        if name == "User":
            return user(value)
        if name == "Group":
            return group(value)
        if name == "Role":
            return role(value)
        if name == "FeatureSet":
            try:
                feats = [f.attributes for f in value.features[:max_items]]
                return {"type": "FeatureSet", "count": len(value.features), "rows": jsonable(feats, depth + 1)}
            except Exception:  # noqa: BLE001
                pass
        if name == "Feature":
            return jsonable(getattr(value, "attributes", {}), depth + 1, max_items)
        props = getattr(value, "properties", None)
        url = getattr(value, "url", None)
        if props is not None:
            try:
                return {"type": name, "url": url, "properties": jsonable(dict(props), depth + 1, max_items)}
            except Exception:  # noqa: BLE001
                pass
        return {"type": name, "repr": text(repr(value), 300), **({"url": url} if url else {})}
    if isinstance(value, dict) or hasattr(value, "items"):
        try:
            items = list(value.items())
        except Exception:  # noqa: BLE001
            return text(repr(value), 300)
        out = {str(k): jsonable(v, depth + 1, max_items) for k, v in items[:max_items * 4]}
        if len(items) > max_items * 4:
            out["..."] = f"{len(items) - max_items * 4} more keys"
        return out
    if isinstance(value, (list, tuple, set)) or hasattr(value, "__iter__") and not hasattr(value, "read"):
        try:
            seq = list(value) if not isinstance(value, (list, tuple)) else value
        except Exception:  # noqa: BLE001
            return text(repr(value), 300)
        out = [jsonable(v, depth + 1, max_items) for v in seq[:max_items]]
        if len(seq) > max_items:
            out.append(f"... {len(seq) - max_items} more")
        return out
    return text(repr(value), 300)
