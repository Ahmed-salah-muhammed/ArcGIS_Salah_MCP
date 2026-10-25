"""Small lookups every portal module needs: items, groups, folders - always resolved to IDs."""
from __future__ import annotations

import re
from typing import Any

ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
ROOT_NAMES = {"", "/", "root", "root folder", "home"}


def get_item(gis: Any, item_id: str):
    if not item_id or not ID_RE.match(str(item_id).strip()):
        raise ValueError(f"'{item_id}' is not an item id (32 hex characters). Titles are not unique: "
                         "find the id with portal_search first.")
    it = gis.content.get(str(item_id).strip())
    if it is None:
        raise LookupError(f"Item {item_id} not found, or you cannot see it.")
    return it


def resolve_group(gis: Any, ref: str, cache: dict | None = None):
    """Group by id or EXACT title (case-insensitive). Ambiguous titles raise - use the id."""
    key = str(ref).strip().lower()
    if cache is not None and key in cache:
        return cache[key]
    grp = gis.groups.get(str(ref).strip()) if ID_RE.match(key) else None
    if grp is None:
        hits = gis.groups.search(f'title:"{str(ref).strip()}"', max_groups=100)
        exact = [g for g in hits if str(g.title).lower() == key]
        if len(exact) > 1:
            raise ValueError(f"group '{ref}' is ambiguous ({len(exact)} groups share that title) - use its id: "
                             + ", ".join(f"{g.id} (owner {g.owner})" for g in exact[:5]))
        grp = exact[0] if exact else None
    if cache is not None:
        cache[key] = grp
    return grp


def require_group(gis: Any, ref: str, cache: dict | None = None):
    grp = resolve_group(gis, ref, cache)
    if grp is None:
        raise LookupError(f"group '{ref}' not found (search with portal_groups action=search).")
    return grp


def folder(gis: Any, name: str | None, create: bool = False, owner: str | None = None):
    """The Folder object for ``name`` (None/'root' -> the root folder)."""
    folders = gis.content.folders
    if name is None or str(name).strip().lower() in ROOT_NAMES:
        return folders.get(owner=owner) if owner else folders.get()
    found = folders.get(str(name).strip(), owner=owner) if owner else folders.get(str(name).strip())
    if found is None and create:
        found = folders.create(str(name).strip(), owner=owner, exist_ok=True) if owner else \
            folders.create(str(name).strip(), exist_ok=True)
    if found is None:
        raise LookupError(f"folder '{name}' not found (portal_folders action=list shows them).")
    return found


def username(gis: Any) -> str | None:
    me = gis.users.me
    return me.username if me is not None else None


def sharing_snapshot(it: Any) -> dict:
    level = None
    groups: list[dict] = []
    try:
        sm = it.sharing
        level = str(getattr(sm.sharing_level, "value", sm.sharing_level))
        groups = [{"id": g.id, "title": g.title} for g in (sm.groups.list() or [])]
    except Exception:  # noqa: BLE001 - fall back to the item's access field
        level = {"private": "PRIVATE", "shared": "PRIVATE", "org": "ORGANIZATION", "public": "EVERYONE"}.get(
            str(it.get("access", "")).lower(), it.get("access"))
    return {"level": level, "groups": groups}
