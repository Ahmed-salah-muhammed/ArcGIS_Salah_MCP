"""Users, roles, user types (licenses), groups and CSV onboarding.

The onboarding logic is ported from the user's ``bulk_add_users.py`` (tested against arcgis 2.4.x). The
rules it encodes - all enforced here:

1. Resolve role names to IDs before ``users.create`` - an unknown role silently falls back to the org
   default. Built-ins: org_admin, org_publisher, org_user, Viewer iAAAAAAAAAAAAAAA, Data Editor
   iBBBBBBBBBBBBBBB (``roles.all()`` does not return the three org_* roles).
2. Resolve user types to IDs (viewerUT, editorUT, fieldWorkerUT, creatorUT, GISProfessionalStdUT ...).
3. Set the user type BEFORE the role (the role must be allowed for the type).
4. Nobody is created directly as org_admin: create as org_user, then update_role.
5. Re-read after every create/update; ``create()`` can return None on Enterprise even on success.
6. ArcGIS Online without a password sends an email invitation; Enterprise built-in accounts need one.
7. Validate every row before changing anything; the report never contains passwords.

The pure helpers (headers, CSV loading, validation, lookups) take plain data so tests need no portal.
"""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .._result import err, guard, ok
from . import connections, safety, serialize
from ._common import require_group, resolve_group

# Preset custom roles created on demand when a CSV row (or roles action=create preset=...) uses them.
CUSTOM_ROLES: dict[str, dict] = {
    "GIS Specialist": {
        "description": "Reads org data in ArcGIS Pro and keeps authoritative feature layers up to date.",
        "privileges": [
            "portal:user:joinGroup", "portal:user:viewOrgGroups", "portal:user:viewOrgItems",
            "portal:user:viewOrgUsers", "portal:user:createItem", "portal:user:shareToGroup", "features:user:edit",
        ],
    },
}
BUILTIN_ROLES = {
    "administrator": "org_admin", "admin": "org_admin", "org_admin": "org_admin",
    "publisher": "org_publisher", "org_publisher": "org_publisher",
    "user": "org_user", "org_user": "org_user",
    "viewer": "iAAAAAAAAAAAAAAA", "data editor": "iBBBBBBBBBBBBBBB",
}
BUILTIN_ROLE_NAMES = {"org_admin": "Administrator", "org_publisher": "Publisher", "org_user": "User",
                      "iAAAAAAAAAAAAAAA": "Viewer", "iBBBBBBBBBBBBBBB": "Data Editor"}
USER_TYPE_ALIASES = {
    "viewer": "viewerUT", "contributor": "editorUT", "editor": "editorUT", "mobile worker": "fieldWorkerUT",
    "field worker": "fieldWorkerUT", "creator": "creatorUT", "storyteller": "storytellerUT",
    "professional": "GISProfessionalStdUT", "professional plus": "GISProfessionalAdvUT",
    "gis professional basic": "GISProfessionalBasicUT", "gis professional standard": "GISProfessionalStdUT",
    "gis professional advanced": "GISProfessionalAdvUT",
}
HEADER_ALIASES = {"user_name": "username", "first_name": "firstname", "last_name": "lastname", "e_mail": "email",
                  "usertype": "user_type", "user_license_type": "user_type", "license_type": "user_type",
                  "group": "groups"}
REQUIRED_COLUMNS = ["username", "firstname", "lastname", "email", "user_type", "role"]
TEMPLATE_HEADER = ["username", "password", "firstname", "lastname", "email", "user_type", "role", "groups",
                   "credits", "description"]


@dataclass
class RowPlan:
    line: int
    row: dict
    user_type_id: str | None = None
    role_id: str | None = None
    role_label: str = ""
    groups: list = field(default_factory=list)
    credits: float | None = None
    errors: list[str] = field(default_factory=list)
    status: str = ""
    message: str = ""

    @property
    def username(self) -> str:
        return self.row.get("username", "")


# --- pure helpers (unit-tested without a portal) -----------------------------

def normalize_header(h: str) -> str:
    h = (h or "").strip().lower().replace(" ", "_").replace("-", "_")
    return HEADER_ALIASES.get(h, h)


def load_csv(path: str | Path) -> list[RowPlan]:
    """utf-8-sig strips the BOM Excel writes, so Arabic names survive."""
    p = Path(path)
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = [normalize_header(h) for h in next(reader)]
        except StopIteration:
            raise ValueError(f"{p} is empty.") from None
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"CSV is missing column(s): {', '.join(missing)}")
        plans = []
        for line_no, values in enumerate(reader, start=2):
            if not any(v.strip() for v in values):
                continue
            row = {h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(header)}
            plans.append(RowPlan(line=line_no, row=row))
    return plans


def user_type_lookup(types: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for ut in types or []:
        lookup[str(ut["id"]).lower()] = ut["id"]
        if ut.get("name"):
            lookup[str(ut["name"]).lower()] = ut["id"]
    for alias, ut_id in USER_TYPE_ALIASES.items():
        if not types or ut_id.lower() in lookup:  # no list (old portal / no admin rights) -> trust the aliases
            lookup.setdefault(alias, ut_id)
            lookup.setdefault(ut_id.lower(), ut_id)
    return lookup


def role_lookup(roles: list[tuple[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """roles: [(role_id, name)] from roles.all() -> (lowercase name/id -> id, id -> display name)."""
    lookup = dict(BUILTIN_ROLES)
    names = dict(BUILTIN_ROLE_NAMES)
    for rid, name in roles:
        lookup[name.lower()] = rid
        lookup[rid.lower()] = rid
        names[rid] = name
    return lookup, names


def validate_rows(plans: list[RowPlan], ut_lookup: dict, r_lookup: dict, r_names: dict, is_online: bool,
                  find_group: Callable[[str], Any] | None = None) -> list[RowPlan]:
    """Fill each plan's ids and errors; returns the plans that have errors."""
    r_lookup = dict(r_lookup)
    r_names = dict(r_names)
    for name in CUSTOM_ROLES:  # presets this run would create count as valid
        r_lookup.setdefault(name.lower(), f"<new:{name}>")
        r_names.setdefault(f"<new:{name}>", name)
    seen: dict[str, int] = {}
    for p in plans:
        r = p.row
        for col in REQUIRED_COLUMNS:
            if not r.get(col):
                p.errors.append(f"'{col}' is empty")
        uname = r.get("username", "")
        if uname:
            if len(uname) < 6 or re.search(r"\s", uname):
                p.errors.append("username must be at least 6 characters with no spaces")
            if uname.lower() in seen:
                p.errors.append(f"duplicate username (also on line {seen[uname.lower()]})")
            seen.setdefault(uname.lower(), p.line)
        if r.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", r["email"]):
            p.errors.append("email looks invalid")
        pwd = r.get("password", "")
        if pwd and len(pwd) < 8:
            p.errors.append("password must be at least 8 characters")
        if not pwd and not is_online:
            p.errors.append("password is required on ArcGIS Enterprise")
        if r.get("user_type"):
            p.user_type_id = ut_lookup.get(r["user_type"].lower())
            if not p.user_type_id:
                p.errors.append(f"unknown user type '{r['user_type']}' (portal_roles action=user_types lists them)")
        if r.get("role"):
            p.role_id = r_lookup.get(r["role"].lower())
            if not p.role_id:
                p.errors.append(f"unknown role '{r['role']}' (portal_roles action=list shows the org's roles)")
            else:
                p.role_label = r_names.get(p.role_id, r["role"])
        for ref in [g for g in r.get("groups", "").split(";") if g.strip()]:
            if find_group is None:
                p.groups.append(ref.strip())
                continue
            try:
                grp = find_group(ref)
            except ValueError as exc:
                p.errors.append(str(exc))
                continue
            if grp is None:
                p.errors.append(f"group '{ref.strip()}' not found")
            else:
                p.groups.append(grp)
        if r.get("credits"):
            try:
                p.credits = float(r["credits"])
            except ValueError:
                p.errors.append(f"credits '{r['credits']}' is not a number")
    return [p for p in plans if p.errors]


def role_matches(user: Any, role_id: str) -> bool:
    if role_id.startswith("org_"):
        return serialize._get(user, "role") == role_id and serialize._get(user, "roleId") in (None, "", role_id)
    return serialize._get(user, "roleId") == role_id


def _group_title(g: Any) -> str:
    return g if isinstance(g, str) else str(getattr(g, "title", g))


def write_report(csv_path: Path, plans: list[RowPlan], out_path: str | None = None) -> Path:
    out = Path(out_path) if out_path else csv_path.with_name(
        f"{csv_path.stem}_report_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["line", "username", "status", "user_type", "role", "groups", "message"])
        for p in plans:
            w.writerow([p.line, p.username, p.status, p.user_type_id or "", p.role_label,
                        ";".join(_group_title(g) for g in p.groups), p.message or "; ".join(p.errors)])
    return out


# --- portal lookups ------------------------------------------------------------

def _license_types(gis: Any) -> tuple[list[dict], str | None]:
    try:
        return list(gis.users.license_types or []), None
    except Exception as exc:  # noqa: BLE001 - non-admins get 403 here (measured)
        return [], f"user types not readable ({safety.scrub(str(exc)).splitlines()[0][:120]}); using known aliases"


def _roles(gis: Any) -> tuple[list[Any], str | None]:
    try:
        return list(gis.users.roles.all()), None
    except Exception as exc:  # noqa: BLE001
        return [], f"custom roles not readable ({safety.scrub(str(exc)).splitlines()[0][:120]})"


def _find_role(gis: Any, name: str):
    for r in _roles(gis)[0]:
        if r.name.lower() == name.lower():
            return r
    return None


def _lookups(gis: Any) -> tuple[dict, dict, dict, list[str]]:
    types, w1 = _license_types(gis)
    roles, w2 = _roles(gis)
    r_lookup, r_names = role_lookup([(r.role_id, r.name) for r in roles])
    return user_type_lookup(types), r_lookup, r_names, [w for w in (w1, w2) if w]


def _is_admin(gis: Any) -> bool:
    me = gis.users.me
    if me is None:
        return False
    privileges = me.get("privileges") or []
    return me.get("role") == "org_admin" or any(p.startswith("portal:admin:") for p in privileges)


def _enforce_access(gis: Any, user: Any, p: RowPlan) -> list[str]:
    """User type first, then role, then groups. Returns what changed."""
    notes = []
    if p.user_type_id and serialize._get(user, "userLicenseTypeId") != p.user_type_id:
        res = user.update_license_type(p.user_type_id)
        if res is not True:
            raise RuntimeError(f"could not set user type {p.user_type_id}: {res}")
        notes.append(f"user type -> {p.user_type_id}")
    if p.role_id and not role_matches(user, p.role_id):
        if not user.update_role(p.role_id):
            raise RuntimeError(f"could not set role '{p.role_label}' (is it allowed for this user type?)")
        notes.append(f"role -> {p.role_label}")
    if p.groups:
        current = {g.id for g in (user.groups or [])}
        for grp in p.groups:
            if grp.id in current:
                continue
            res = grp.add_users([user.username]) or {}
            if user.username in (res.get("notAdded") or []):
                raise RuntimeError(f"could not add to group '{grp.title}'")
            notes.append(f"+group {grp.title}")
    return notes


def _verify(gis: Any, p: RowPlan) -> list[str]:
    """Re-read the account and report drift from the plan."""
    fresh = gis.users.get(p.username)
    if fresh is None:
        return ["account not found on re-read"]
    drift = []
    if p.user_type_id and serialize._get(fresh, "userLicenseTypeId") != p.user_type_id:
        drift.append(f"user type is {serialize._get(fresh, 'userLicenseTypeId')}")
    if p.role_id and not p.role_id.startswith("<new:") and not role_matches(fresh, p.role_id):
        drift.append(f"role is {serialize._get(fresh, 'roleId') or serialize._get(fresh, 'role')}")
    have = {g.id for g in (fresh.groups or [])}
    missing = [g.title for g in p.groups if not isinstance(g, str) and g.id not in have]
    if missing:
        drift.append(f"not in group(s) {', '.join(missing)}")
    return drift


def _create(gis: Any, p: RowPlan):
    r = p.row
    password = r.get("password") or None
    safety.register_secret(password)
    create_role = "org_user" if p.role_id == "org_admin" else p.role_id
    gis.users.create(username=r["username"], password=password, firstname=r["firstname"], lastname=r["lastname"],
                     email=r["email"], role=create_role, user_type=p.user_type_id,
                     description=r.get("description") or None,
                     credits=p.credits if p.credits is not None else -1, groups=p.groups or None)
    user = gis.users.get(r["username"])
    if user is None:
        raise RuntimeError("the portal did not create the account (check available licenses for this user type)")
    return user


def _ensure_custom_roles(gis: Any, used: set[str], sync: bool, dry_run: bool) -> list[str]:
    notes = []
    for name, cfg in CUSTOM_ROLES.items():
        if name.lower() not in used:
            continue
        wanted = set(cfg["privileges"])
        role = _find_role(gis, name)
        if role is None:
            if dry_run:
                notes.append(f"would create role '{name}' with {len(wanted)} privileges")
                continue
            role = gis.users.roles.create(name=name, description=cfg["description"], privileges=sorted(wanted))
            if role is None:
                raise RuntimeError(f"could not create role '{name}' (can your account manage roles?)")
            actual = set(role.privileges or [])
            missing = wanted - actual
            notes.append(f"created role '{name}' ({role.role_id})"
                         + (f"; the portal did not accept: {', '.join(sorted(missing))}" if missing else ""))
            continue
        actual = set(role.privileges or [])
        if actual == wanted:
            notes.append(f"role '{name}' exists and matches the preset")
        elif sync and not dry_run:
            role.privileges = sorted(wanted)
            notes.append(f"role '{name}' privileges updated to the preset")
        else:
            notes.append(f"role '{name}' differs from the preset (missing {sorted(wanted - actual)}, extra "
                         f"{sorted(actual - wanted)}); sync_roles=True overwrites")
    return notes


# --- tools -------------------------------------------------------------------------

@guard
def users(action: str = "search", username: str | None = None, query: str | None = None, max_users: int = 50,
          firstname: str | None = None, lastname: str | None = None, email: str | None = None,
          user_type: str | None = None, role: str | None = None, groups: list[str] | None = None,
          password_env: str | None = None, description: str | None = None, credits: float | None = None,
          reassign_to: str | None = None, confirm: bool = False, connection: str = "default") -> dict:
    action = (action or "search").lower()
    gis = connections.get(connection)
    if action == "search":
        found = gis.users.search(query=query or None, max_users=max(1, min(int(max_users or 50), 1000)),
                                 role=role, user_type=user_type)
        return ok({"count": len(found), "users": [serialize.user(u) for u in found]})
    if not username:
        return err("username is required.", code="bad_argument")
    if action == "get":
        u = gis.users.get(username)
        if u is None:
            return err(f"User '{username}' not found.", code="not_found")
        out = serialize.user(u)
        out["groups"] = [{"id": g.id, "title": g.title} for g in (u.groups or [])]
        out["privileges"] = list(u.get("privileges") or [])
        return ok(out)

    if action in ("create", "update_access"):
        ut_lookup, r_lookup, r_names, warnings = _lookups(gis)
        row = {"username": username, "firstname": firstname or "", "lastname": lastname or "", "email": email or "",
               "user_type": user_type or "", "role": role or "", "groups": ";".join(groups or []),
               "credits": "" if credits is None else str(credits), "description": description or ""}
        password = None
        if password_env:
            password, problem = safety.secret_from_env(password_env)
            if problem:
                return problem
            row["password"] = password
        plan = RowPlan(line=1, row=row)
        cache: dict = {}
        if action == "update_access":
            existing = gis.users.get(username)
            if existing is None:
                return err(f"User '{username}' not found.", code="not_found")
            for k, v in (("firstname", "x"), ("lastname", "x"), ("email", "x@x.xx")):
                row[k] = row[k] or v  # identity fields are not touched by update_access
            row["user_type"] = row["user_type"] or existing.get("userLicenseTypeId") or ""
            row["role"] = row["role"] or existing.get("roleId") or existing.get("role") or ""
            row["password"] = row.get("password") or "unchanged-placeholder"
        validate_rows([plan], ut_lookup, r_lookup, r_names, connections.is_online(gis),
                      lambda ref: resolve_group(gis, ref, cache))
        if plan.errors:
            return err("Invalid request: " + "; ".join(plan.errors), code="invalid", warnings=warnings or None)
        preview = {"username": username, "user_type": plan.user_type_id, "role": plan.role_label,
                   "role_id": plan.role_id, "groups": [g.title for g in plan.groups],
                   "grants_admin": plan.role_id == "org_admin",
                   "invitation_email": action == "create" and password is None and connections.is_online(gis)}
        if not confirm:
            return safety.confirm_required(f"{'Creating' if action == 'create' else 'Changing access of'} user "
                                           f"'{username}'", preview,
                                           hint="This grants ADMINISTRATOR." if preview["grants_admin"] else None)
        if plan.role_id and plan.role_id.startswith("<new:"):
            _ensure_custom_roles(gis, {plan.role_label.lower()}, sync=False, dry_run=False)
            plan.role_id = _find_role(gis, plan.role_label).role_id
        if action == "create":
            if gis.users.get(username) is not None:
                return err(f"User '{username}' already exists; use action=update_access.", code="exists")
            user = _create(gis, plan)
        else:
            user = gis.users.get(username)
        notes = _enforce_access(gis, user, plan)
        drift = _verify(gis, plan)
        safety.audit("portal_users", action, connection=connection, target=username, ok=not drift,
                     after=preview, details={"changes": notes, "drift": drift})
        return ok({"username": username, "changes": notes, "verified": not drift, "drift": drift or None,
                   "user": serialize.user(gis.users.get(username))})

    u = gis.users.get(username)
    if u is None:
        return err(f"User '{username}' not found.", code="not_found")
    preview = serialize.user(u)
    if action in ("disable", "enable"):
        if not confirm:
            return safety.confirm_required(f"{action.title()} user '{username}'", preview)
        done = u.disable() if action == "disable" else u.enable()
        state = gis.users.get(username).get("disabled")
        safety.audit("portal_users", action, connection=connection, target=username,
                     ok=state == (action == "disable"))
        return ok({"username": username, "disabled": state, "result": done})
    if action == "delete":
        owned = 0
        try:
            owned = len(list(u.items(max_items=1000)))
        except Exception:  # noqa: BLE001
            pass
        preview.update(items_in_root=owned)
        if owned and not reassign_to:
            return err(f"'{username}' owns content; pass reassign_to=<username> (or prefer action=disable).",
                       code="owns_content", preview=preview)
        if not confirm:
            return safety.confirm_required(f"Deleting user '{username}'", preview,
                                           hint="Prefer action=disable: it is reversible.")
        done = u.delete(reassign_to=reassign_to) if reassign_to else u.delete()
        gone = gis.users.get(username) is None
        safety.audit("portal_users", "delete", connection=connection, target=username, ok=gone, before=preview,
                     details={"reassign_to": reassign_to})
        return ok({"username": username, "deleted": gone, "result": done})
    if action == "transfer_content":
        if not reassign_to:
            return err("reassign_to (the receiving username) is required.", code="bad_argument")
        if not confirm:
            return safety.confirm_required(f"Moving all content of '{username}' to '{reassign_to}'", preview)
        done = u.reassign_to(reassign_to)
        safety.audit("portal_users", "transfer_content", connection=connection, target=username, ok=bool(done),
                     details={"to": reassign_to})
        return ok({"from": username, "to": reassign_to, "result": done})
    return err("action must be search | get | create | update_access | disable | enable | delete | "
               "transfer_content.", code="bad_action")


@guard
def onboard(action: str = "validate", csv_path: str | None = None, out_path: str | None = None,
            update_existing: bool = False, sync_roles: bool = False, confirm: bool = False,
            connection: str = "default") -> dict:
    action = (action or "validate").lower()
    if action == "template":
        target = Path(out_path or csv_path or "users_template.csv")
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(TEMPLATE_HEADER)
            w.writerow(["gis.jdoe01", "", "Jane", "Doe", "jane.doe@example.com", "Creator",
                        "GIS Specialist", "Field Editors;QA Team", "", "GIS Specialist - Project A"])
            w.writerow(["gis.rsmith03", "", "Robin", "Smith", "robin.smith@example.com", "Viewer", "Viewer",
                        "Dashboard Viewers", "", "Read-only stakeholder"])
        return ok({"path": str(target.resolve()), "columns": TEMPLATE_HEADER,
                   "notes": ["password: blank on ArcGIS Online sends an email invitation; required on Enterprise",
                             "groups: semicolon-separated titles or ids", "role: built-in, custom or role id",
                             "user_type: name or id (Creator / creatorUT, Viewer, Professional ...)"]})
    gis = connections.get(connection)
    if action == "export_users":
        target = Path(out_path or f"org_users_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        found = gis.users.search(query=None, max_users=10000)
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["username", "firstname", "lastname", "email", "user_type", "role", "groups", "disabled",
                        "last_login", "created"])
            for u in found:
                w.writerow([u.username, u.get("firstName", ""), u.get("lastName", ""), u.get("email", ""),
                            u.get("userLicenseTypeId", ""), u.get("roleId") or u.get("role", ""),
                            ";".join(g.title for g in (u.groups or [])), u.get("disabled"),
                            serialize.iso(u.get("lastLogin")), serialize.iso(u.get("created"))])
        return ok({"path": str(target.resolve()), "users": len(found)})
    if action not in ("validate", "run"):
        return err("action must be template | validate | run | export_users.", code="bad_action")
    if not csv_path:
        return err("csv_path is required.", code="bad_argument")
    path = Path(csv_path)
    plans = load_csv(path)
    if not plans:
        return err("No user rows in the CSV.", code="empty")
    if len(plans) > safety.max_users():
        return err(f"{len(plans)} rows exceed ARCGIS_SALAH_MAX_USERS ({safety.max_users()}); split the file.",
                   code="too_many")
    for p in plans:
        safety.register_secret(p.row.get("password"))
    ut_lookup, r_lookup, r_names, warnings = _lookups(gis)
    cache: dict = {}
    bad = validate_rows(plans, ut_lookup, r_lookup, r_names, connections.is_online(gis),
                        lambda ref: resolve_group(gis, ref, cache))
    if not _is_admin(gis):
        warnings.append("the signed-in account is not an org administrator; creating users will fail")
    existing = []
    for p in plans:
        if not p.errors and gis.users.get(p.username, outside_org=False) is not None:
            existing.append(p.username)
    rows = [{"line": p.line, "username": p.username, "user_type": p.user_type_id, "role": p.role_label,
             "groups": [_group_title(g) for g in p.groups], "errors": p.errors or None,
             "exists": p.username in existing} for p in plans]
    if bad:
        for p in plans:
            p.status = "invalid" if p.errors else "valid"
        report = write_report(path, plans, out_path)
        return err(f"{len(bad)} row(s) have problems - nothing was created. Fix them and validate again.",
                   code="invalid_rows", rows=rows, report=str(report), warnings=warnings or None)
    used_roles = {p.role_label.lower() for p in plans}
    role_notes = _ensure_custom_roles(gis, used_roles, sync_roles, dry_run=True)
    admins = [p.username for p in plans if p.role_id == "org_admin"]
    summary = {"rows": len(plans), "new": len(plans) - len(existing), "existing": existing,
               "existing_policy": "update type/role/groups" if update_existing else "skip",
               "roles": role_notes, "grants_admin_to": admins or None}
    if action == "validate" or not confirm:
        result = {"valid": True, "summary": summary, "rows": rows, "warnings": warnings or None}
        if action == "run":
            return safety.confirm_required(f"Onboarding {len(plans)} user(s)", result,
                                           hint="Grants ADMINISTRATOR to some rows." if admins else None)
        return ok(result)

    role_notes = _ensure_custom_roles(gis, used_roles, sync_roles, dry_run=False)
    for p in plans:
        if p.role_id and p.role_id.startswith("<new:"):
            role_obj = _find_role(gis, p.role_label)
            if role_obj is None:
                return err(f"Role '{p.role_label}' was not created - stopped before adding users.",
                           code="role_missing")
            p.role_id = role_obj.role_id
    started = time.perf_counter()
    for p in plans:
        try:
            current = gis.users.get(p.username)
            if current is not None:
                if not update_existing:
                    p.status, p.message = "skipped", "already exists (update_existing=True applies type/role/groups)"
                    continue
                notes = _enforce_access(gis, current, p)
                p.status, p.message = "updated", "; ".join(notes) or "already correct"
            else:
                user = _create(gis, p)
                notes = _enforce_access(gis, user, p)
                p.status = "created"
                p.message = f"{p.user_type_id} / {p.role_label}" + (f" (adjusted: {'; '.join(notes)})" if notes else "")
            drift = _verify(gis, p)
            if drift:
                p.status, p.message = "drift", "; ".join(drift)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
            p.status, p.message = "failed", safety.scrub(str(exc).strip().splitlines()[0][:300])
    counts: dict[str, int] = {}
    for p in plans:
        counts[p.status] = counts.get(p.status, 0) + 1
    report = write_report(path, plans, out_path)
    safety.audit("portal_onboard", "run", connection=connection, target=str(path), ok=not counts.get("failed"),
                 details={"counts": counts, "roles": role_notes})
    return ok({"counts": counts, "report": str(report), "roles": role_notes,
               "rows": [{"username": p.username, "status": p.status, "message": p.message} for p in plans],
               "seconds": round(time.perf_counter() - started, 1)})


@guard
def groups(action: str = "search", group: str | None = None, query: str | None = None, title: str | None = None,
           tags: list[str] | None = None, description: str | None = None, access: str | None = None,
           is_invitation_only: bool = True, is_view_only: bool = False, shared_update: bool = False,
           users: list[str] | None = None, max_groups: int = 50, confirm: bool = False,
           connection: str = "default") -> dict:
    action = (action or "search").lower()
    gis = connections.get(connection)
    if action == "search":
        found = gis.groups.search(query or "", max_groups=max(1, min(int(max_groups or 50), 1000)))
        return ok({"count": len(found), "groups": [serialize.group(g) for g in found]})
    if action == "create":
        if not title:
            return err("title is required.", code="bad_argument")
        access = access or "private"
        if access not in ("private", "org", "public"):
            return err("access must be private | org | public.", code="bad_argument")
        clash = [g for g in gis.groups.search(f'title:"{title}"', max_groups=20) if g.title.lower() == title.lower()]
        if access == "public" and not confirm:
            return safety.confirm_required(f"Creating PUBLIC group '{title}'", {"title": title})
        g = gis.groups.create(title=title, tags=tags or ["arcgis-pro-salah-mcp"], description=description,
                              access=access, is_invitation_only=is_invitation_only, is_view_only=is_view_only,
                              users_update_items=shared_update)
        if g is None:
            return err("The portal did not create the group (shared-update groups need the "
                       "portal:admin:createUpdateCapableGroup privilege).", code="not_created")
        safety.audit("portal_groups", "create", connection=connection, target=g.id, ok=True,
                     after=serialize.group(g))
        return ok({"group": serialize.group(g),
                   "warning": f"another group already has this title ({clash[0].id}); use ids" if clash else None,
                   "note": "shared-update can only be set when a group is created" if shared_update else None})
    if not group:
        return err("group (id or exact title) is required.", code="bad_argument")
    g = require_group(gis, group)
    if action == "get":
        out = serialize.group(g)
        members = g.get_members() or {}
        out["members"] = {k: members.get(k) for k in ("owner", "admins", "users")}
        try:
            out["items"] = [serialize.item(i) for i in g.content(max_items=100)]
        except Exception:  # noqa: BLE001
            out["items"] = None
        return ok(out)
    if action in ("add_users", "remove_users"):
        if not users:
            return err("users (usernames) is required.", code="bad_argument")
        res = g.add_users(users) if action == "add_users" else g.remove_users(users)
        res = res or {}
        failed = res.get("notAdded") if action == "add_users" else res.get("notRemoved")
        safety.audit("portal_groups", action, connection=connection, target=g.id, ok=not failed,
                     details={"users": users, "failed": failed})
        return ok({"group": g.title, "requested": users, "failed": failed or None, "all_ok": not failed})
    if action == "update":
        changes = {k: v for k, v in (("title", title), ("tags", tags), ("description", description),
                                     ("access", access)) if v}
        if changes.get("access") not in (None, "private", "org", "public"):
            return err("access must be private | org | public.", code="bad_argument")
        if not changes:
            return err("Nothing to update (title, tags, description, access).", code="nothing_to_do")
        if changes.get("access") == "public" and not confirm:
            return safety.confirm_required(f"Making group '{g.title}' PUBLIC", changes)
        done = g.update(**changes)
        safety.audit("portal_groups", "update", connection=connection, target=g.id, ok=bool(done), after=changes)
        return ok({"group": serialize.group(require_group(gis, g.id)), "updated": bool(done)})
    if action == "delete":
        preview = serialize.group(g)
        if not confirm:
            return safety.confirm_required(f"Deleting group '{g.title}'", preview,
                                           hint="Items stay; only the group and its sharing go.")
        gid, title = preview["id"], preview["title"]  # a Group hydrates lazily: reading g.id after delete -> 400
        done = g.delete()
        safety.audit("portal_groups", "delete", connection=connection, target=gid, ok=bool(done), before=preview)
        return ok({"group": title, "id": gid, "deleted": bool(done)})
    return err("action must be search | get | create | update | add_users | remove_users | delete.",
               code="bad_action")


@guard
def roles(action: str = "list", role: str | None = None, name: str | None = None, description: str | None = None,
          privileges: list[str] | None = None, preset: str | None = None, confirm: bool = False,
          connection: str = "default") -> dict:
    action = (action or "list").lower()
    gis = connections.get(connection)
    if action == "list":
        found, warning = _roles(gis)
        builtin = [{"id": rid, "name": n, "builtin": True} for rid, n in BUILTIN_ROLE_NAMES.items()]
        return ok({"roles": builtin + [{**serialize.role(r), "builtin": False} for r in found],
                   "presets": {k: v["privileges"] for k, v in CUSTOM_ROLES.items()}, "warning": warning})
    if action == "user_types":
        types, warning = _license_types(gis)
        out = [{"id": t.get("id"), "name": t.get("name"), "maxUsers": t.get("maxUsers"),
                "currentUsers": t.get("currentUsers")} for t in types]
        return ok({"user_types": out, "aliases": USER_TYPE_ALIASES, "warning": warning})
    if action == "license_usage":
        try:
            counts = gis.users.counts("user_type", as_df=False)
        except Exception as exc:  # noqa: BLE001
            return err(f"Not readable with this account: {safety.scrub(str(exc)).splitlines()[0]}", code="forbidden")
        return ok({"by_user_type": serialize.jsonable(counts)})
    if action == "privileges_catalog":
        seen: dict[str, set] = {}
        me = gis.users.me
        for p in (me.get("privileges") or []) if me is not None else []:
            seen.setdefault(p, set()).add("you")
        for r in _roles(gis)[0]:
            for p in (r.privileges or []):
                seen.setdefault(p, set()).add(r.name)
        for n, cfg in CUSTOM_ROLES.items():
            for p in cfg["privileges"]:
                seen.setdefault(p, set()).add(f"preset {n}")
        grouped: dict[str, list] = {}
        for p in sorted(seen):
            grouped.setdefault(":".join(p.split(":")[:2]), []).append({"privilege": p, "seen_in": sorted(seen[p])})
        return ok({"privileges": grouped, "reference": "https://developers.arcgis.com/rest/users-groups-and-items/"
                                                       "privileges/", "admin_prefix": "portal:admin:"})
    if action == "create":
        if preset:
            cfg = CUSTOM_ROLES.get(preset) or {k.lower().replace(" ", "_"): v for k, v in CUSTOM_ROLES.items()}.get(
                preset.lower().replace(" ", "_"))
            if cfg is None:
                return err(f"Unknown preset; presets: {', '.join(CUSTOM_ROLES)}.", code="bad_argument")
            name = name or next(k for k, v in CUSTOM_ROLES.items() if v is cfg)
            privileges = privileges or cfg["privileges"]
            description = description or cfg["description"]
        if not (name and privileges):
            return err("name and privileges (or preset) are required.", code="bad_argument")
        admin_privs = [p for p in privileges if p.startswith("portal:admin:")]
        if _find_role(gis, name) is not None:
            return err(f"Role '{name}' already exists; use action=set_privileges.", code="exists")
        preview = {"name": name, "privileges": sorted(privileges), "admin_privileges": admin_privs or None}
        if not confirm:
            return safety.confirm_required(f"Creating role '{name}'", preview,
                                           hint="It includes ADMIN privileges." if admin_privs else None)
        r = gis.users.roles.create(name=name, description=description or name, privileges=sorted(privileges))
        if r is None:
            return err("The portal did not create the role.", code="not_created")
        rejected = sorted(set(privileges) - set(r.privileges or []))
        safety.audit("portal_roles", "create", connection=connection, target=r.role_id, ok=not rejected,
                     after=preview)
        return ok({"role": serialize.role(r), "rejected_privileges": rejected or None})
    if not role:
        return err("role (name or id) is required.", code="bad_argument")
    r = next((x for x in _roles(gis)[0] if role.lower() in (x.name.lower(), x.role_id.lower())), None)
    if r is None:
        return err(f"Role '{role}' not found among custom roles.", code="not_found")
    if action == "get":
        return ok(serialize.role(r))
    if action == "set_privileges":
        if not privileges:
            return err("privileges is required.", code="bad_argument")
        before = sorted(r.privileges or [])
        preview = {"role": r.name, "add": sorted(set(privileges) - set(before)),
                   "remove": sorted(set(before) - set(privileges))}
        if not confirm:
            return safety.confirm_required(f"Changing privileges of role '{r.name}'", preview)
        r.privileges = sorted(privileges)
        after = sorted(_find_role(gis, r.name).privileges or [])
        safety.audit("portal_roles", "set_privileges", connection=connection, target=r.role_id,
                     ok=set(after) == set(privileges), before=before, after=after)
        return ok({"role": r.name, "privileges": after, "rejected": sorted(set(privileges) - set(after)) or None})
    if action == "delete":
        if not confirm:
            return safety.confirm_required(f"Deleting role '{r.name}'", serialize.role(r))
        done = r.delete()
        safety.audit("portal_roles", "delete", connection=connection, target=r.role_id, ok=bool(done))
        return ok({"role": r.name, "deleted": bool(done)})
    return err("action must be list | get | create | set_privileges | delete | user_types | license_usage | "
               "privileges_catalog.", code="bad_action")
