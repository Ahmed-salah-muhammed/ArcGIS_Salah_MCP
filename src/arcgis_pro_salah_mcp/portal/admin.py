"""Org and server administration - read-only reports, plus service start/stop and notebook runs (confirm).

Not curated on purpose: federation, security configuration, portal mode, identity providers and license
changes. Those go through portal_python in write mode with confirm=True, if ever.
Most of this needs an administrator; a 403 comes back as code="forbidden" with the reason, never retried.
"""
from __future__ import annotations

import csv
import datetime as _dt
import time
from pathlib import Path
from typing import Any

from .._result import err, guard, ok
from . import _imports, connections, safety, serialize
from ._common import get_item


def _forbidden(exc: Exception, what: str, gis: Any = None) -> dict:
    text = safety.scrub(str(exc)).strip().splitlines()[0][:200] if str(exc) else type(exc).__name__
    role = None
    try:
        role = gis.users.me.get("role") if gis is not None and gis.users.me is not None else None
    except Exception:  # noqa: BLE001
        pass
    if "403" in text or "permission" in text.lower() or (role and role != "org_admin"):
        return err(f"{what}: {text}", code="forbidden",
                   hint=f"Needs an org administrator (you are {role})." if role and role != "org_admin" else None)
    return err(f"{what}: {text}", code="failed")


def _admin(gis: Any):
    mgr = gis.admin
    if mgr is None:
        raise PermissionError("403: org administration is not available to this account.")
    return mgr


@guard
def admin(action: str = "org", days: int = 7, hours: float | None = None, out_path: str | None = None,
          include_items: bool = False,
          server_index: int = 0, service: str | None = None, folder: str | None = None, level: str = "WARNING",
          max_records: int = 200, notebook_item_id: str | None = None, timeout_min: int = 30,
          confirm: bool = False, connection: str = "default") -> dict:
    action = (action or "org").lower()
    gis = connections.get(connection)
    online = connections.is_online(gis)
    props = gis.properties

    if action == "org":
        out = {"name": props.get("name"), "id": props.get("id"), "url": str(gis.url),
               "platform": "ArcGIS Online" if online else "ArcGIS Enterprise", "version": getattr(gis, "version", None),
               "region": props.get("region"), "culture": props.get("culture"),
               "default_basemap": (props.get("defaultBasemap") or {}).get("title"),
               "helper_services": sorted((props.get("helperServices") or {}).keys())}
        if online:
            out["available_credits"] = props.get("availableCredits")
            out["subscription"] = {k: (props.get("subscriptionInfo") or {}).get(k)
                                   for k in ("type", "state", "expDate", "maxUsers")}
        try:
            out["users_by_type"] = serialize.jsonable(gis.users.counts("user_type", as_df=False))
        except Exception as exc:  # noqa: BLE001
            out["users_by_type"] = f"not readable ({type(exc).__name__})"
        return ok(out)

    if action == "credits":
        if not online:
            return err("Credits exist only on ArcGIS Online.", code="unsupported")
        try:
            cm = _admin(gis).credits
            start = _dt.datetime.now() - _dt.timedelta(days=days)
            usage = cm.credit_usage(start_time=start, end_time=_dt.datetime.now(),
                                    time_frame="week" if days <= 7 else "month")
            return ok({"available": cm.credits, "budgeting_enabled": cm.is_enabled,
                       "usage": serialize.jsonable(usage)})
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, "credits", gis)

    if action == "usage":
        if not online:
            return err("Usage reports here are ArcGIS Online's; on Enterprise use action=logs or servers.",
                       code="unsupported")
        try:
            reports = _admin(gis).usage_reports
            frame = "week" if days <= 7 else ("month" if days <= 31 else "year")
            start = _dt.datetime.now() - _dt.timedelta(days=days)
            data = {"credits": reports.credit(start_time=start, time_frame=frame),
                    "users": reports.users(start_time=start, time_frame=frame)}
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, "usage reports", gis)
        return ok({"days": days, "report": serialize.jsonable(data, max_items=100)})

    if action == "audit_csv":
        target = Path(out_path or f"org_audit_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        try:
            found = gis.users.search(query=None, max_users=10000)
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, "listing users", gis)
        started = time.perf_counter()
        with target.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["username", "full_name", "email", "user_type", "role", "level", "disabled", "last_login",
                        "created", "groups", "items"])
            for u in found:
                items = ""
                if include_items:
                    try:
                        items = len(list(u.items(max_items=10000)))
                    except Exception:  # noqa: BLE001
                        items = "?"
                w.writerow([u.username, u.get("fullName"), u.get("email"), u.get("userLicenseTypeId"),
                            u.get("roleId") or u.get("role"), u.get("level"), u.get("disabled"),
                            serialize.iso(u.get("lastLogin")), serialize.iso(u.get("created")),
                            ";".join(g.title for g in (u.groups or [])), items])
        never = sum(1 for u in found if not u.get("lastLogin") or u.get("lastLogin") == -1)
        return ok({"path": str(target.resolve()), "users": len(found), "never_logged_in": never,
                   "seconds": round(time.perf_counter() - started, 1)})

    if action == "license":
        try:
            lic = _admin(gis).license
            if online:
                return ok({"licenses": [serialize.jsonable(dict(lc.properties)) for lc in lic.all()][:50]})
            return ok({"license": serialize.jsonable(dict(lic.properties))})
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, "licenses", gis)

    if action == "history":
        if not online:
            return err("history here is ArcGIS Online's activity log.", code="unsupported")
        try:
            start = _dt.datetime.now() - _dt.timedelta(days=days)
            data = _admin(gis).history(start_date=start, num=min(max_records, 1000), data_format="raw")
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, "history", gis)
        return ok({"days": days, "events": serialize.jsonable(data, max_items=max_records)})

    if action in ("servers", "services", "service_start", "service_stop", "logs"):
        if online:
            return err(f"{action} is ArcGIS Enterprise administration (federated servers).", code="unsupported")
        try:
            mgr = _admin(gis)
            if action == "logs":
                start = _dt.datetime.now() - _dt.timedelta(hours=hours or days * 24)
                data = mgr.logs.query(start_time=start, level=level, page_size=min(max_records, 1000))
                return ok({"level": level, "since": start.isoformat(), "logs": serialize.jsonable(data, max_items=max_records)})
            servers = mgr.servers.list()
            if action == "servers":
                return ok({"servers": [{"index": i, "url": s.url} for i, s in enumerate(servers)]})
            srv = servers[server_index]
            if action == "services":
                services = srv.services.list(folder=folder)
                out = []
                for s in services[:max_records]:
                    p = s.properties
                    status = None
                    try:
                        status = (s.status or {}).get("realTimeState")
                    except Exception:  # noqa: BLE001
                        pass
                    out.append({"name": p.get("serviceName"), "type": p.get("type"), "status": status})
                return ok({"server": srv.url, "folder": folder, "services": out,
                           "folders": srv.services.folders})
            if not service:
                return err("service (its name) is required.", code="bad_argument")
            target = next((s for s in srv.services.list(folder=folder)
                           if s.properties.get("serviceName") == service), None)
            if target is None:
                return err(f"Service '{service}' not found in folder {folder or 'root'}.", code="not_found")
            if not confirm:
                return safety.confirm_required(f"{action.split('_')[1].title()} service '{service}'",
                                               {"server": srv.url, "folder": folder, "service": service})
            done = target.start() if action == "service_start" else target.stop()
            safety.audit("portal_admin", action, connection=connection, target=f"{srv.url}/{folder}/{service}",
                         ok=bool(done))
            return ok({"service": service, "result": done, "status": (target.status or {}).get("realTimeState")})
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, action, gis)

    if action == "notebooks":
        try:
            found = gis.content.search(f"type:Notebook AND owner:{gis.users.me.username}", max_items=100)
        except Exception as exc:  # noqa: BLE001
            return _forbidden(exc, "notebooks", gis)
        tasks = []
        try:
            tasks = [serialize.jsonable(dict(t.properties)) for t in gis.users.me.tasks.all][:50]
        except Exception:  # noqa: BLE001
            pass
        return ok({"notebooks": [serialize.item(i, gis) for i in found], "scheduled_tasks": tasks})

    if action == "notebook_run":
        nb = get_item(gis, notebook_item_id)
        if nb.type != "Notebook":
            return err(f"Item is a {nb.type}, not a Notebook.", code="bad_argument")
        if not confirm:
            return safety.confirm_required(f"Running notebook '{nb.title}' on the portal",
                                           {"notebook": serialize.item(nb)},
                                           hint="Advanced notebook runtimes spend credits on ArcGIS Online.")
        execute_notebook = _imports.load("arcgis.notebook").execute_notebook

        started = time.perf_counter()
        res = execute_notebook(nb, timeout=timeout_min, gis=gis)
        safety.audit("portal_admin", "notebook_run", connection=connection, target=nb.id, ok=True)
        return ok({"notebook": nb.title, "result": serialize.jsonable(res),
                   "seconds": round(time.perf_counter() - started, 1)})

    if action == "audit_log":
        return ok({"path": str(safety.audit_path()), "entries": safety.read_audit(max_records)})
    return err("action must be org | credits | usage | audit_csv | license | history | servers | services | "
               "service_start | service_stop | logs | notebooks | notebook_run | audit_log.", code="bad_action")
