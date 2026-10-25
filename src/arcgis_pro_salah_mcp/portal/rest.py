"""portal_rest: raw REST through the connection's authenticated session (token handled by arcgis).

GET is free. POST needs confirm=True unless the operation is read-only (query, search, generateRenderer,
...). Only the portal's own host, its helper/hosted-service hosts (``*.arcgis.com`` for ArcGIS Online)
and hosts listed in ARCGIS_SALAH_REST_HOSTS are reachable, so the token never goes anywhere else.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from .._result import err, guard, ok
from . import connections, safety, serialize

READ_ONLY_POST = {"query", "search", "generaterenderer", "queryrelatedrecords", "queryattachments", "querydomains",
                  "querytopfeatures", "identify", "find", "validatesql", "querybins", "queryanalytic", "info",
                  "self", "estimate", "getestimates", "querydatastatistics", "computestatisticshistograms"}


def allowed_hosts(gis: Any) -> set[str]:
    hosts = {urlparse(str(gis.url)).hostname or ""}
    helpers = gis.properties.get("helperServices") or {}
    for value in helpers.values():
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, dict) and entry.get("url"):
                hosts.add(urlparse(entry["url"]).hostname or "")
    for extra in os.environ.get("ARCGIS_SALAH_REST_HOSTS", "").split(","):
        if extra.strip():
            hosts.add(extra.strip().lower())
    return {h.lower() for h in hosts if h}


def host_allowed(url: str, hosts: set[str], online: bool) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in hosts:
        return True
    return parsed.scheme == "https" and online and (host == "arcgis.com" or host.endswith(".arcgis.com"))


@guard
def request(path: str, method: str = "GET", params: dict | None = None, confirm: bool = False,
            max_chars: int = 20000, connection: str = "default") -> dict:
    method = (method or "GET").upper()
    if method not in ("GET", "POST"):
        return err("method must be GET or POST.", code="bad_argument")
    gis = connections.get(connection)
    if str(path).lower().startswith(("http://", "https://")):
        url = str(path)
    else:
        url = f"{str(gis.url).rstrip('/')}/sharing/rest/{str(path).lstrip('/')}"
    if not host_allowed(url, allowed_hosts(gis), connections.is_online(gis)):
        return err(f"Host {urlparse(url).hostname} is not the portal or one of its services; add it to "
                   "ARCGIS_SALAH_REST_HOSTS if it really belongs to this deployment.", code="host_not_allowed")
    operation = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    payload = {"f": "json", **(params or {})}
    if method == "POST" and operation not in READ_ONLY_POST and not confirm:
        return safety.confirm_required(f"POST to {operation}", {"url": url, "params": safety.scrub(payload)})
    res = gis._con.post(url, payload) if method == "POST" else gis._con.get(url, payload)
    if method == "POST" and operation not in READ_ONLY_POST:
        safety.audit("portal_rest", "post", connection=connection, target=url, ok=True,
                     details={"params": list(payload)})
    data = safety.scrub(res if isinstance(res, (dict, list)) else {"text": str(res)})
    nxt = None
    if isinstance(data, dict):
        if data.get("nextStart", -1) and data.get("nextStart", -1) > 0:
            nxt = {"start": data["nextStart"]}
        elif data.get("exceededTransferLimit"):
            nxt = {"resultOffset": int(payload.get("resultOffset", 0)) + len(data.get("features") or [])}
    text = json.dumps(data, default=str, ensure_ascii=False)
    return ok({"url": url, "method": method, "chars": len(text), "next": nxt,
               "response": data if len(text) <= max_chars else serialize.text(text, max_chars)})
