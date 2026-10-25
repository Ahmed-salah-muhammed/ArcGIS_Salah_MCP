"""portal_python: run ArcGIS API for Python code against an authenticated ``gis`` - the escape hatch.

It runs IN-PROCESS on a worker thread, on purpose: a separate process would pay ``import arcgis``
(17-60 s measured) and a new sign-in on every call. The trade-off is that a runaway call cannot be
killed - after ``timeout_s`` the tool returns and the code keeps running in the background.

``mode="read"`` (default) puts the code through an AST check that refuses calls and assignments that
write to the portal (add/update/delete/publish/share/create/edit_features/append/calculate/truncate/
*_definition/update_role/... and REST POST). It is a seatbelt, NOT a sandbox: it catches mistakes,
not determined code. ``mode="write"`` needs confirm=True, is logged in full to the audit log, and can
be switched off with ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC=false.
"""
from __future__ import annotations

import ast
import collections
import io
import json
import re
import threading
import time
import traceback
from typing import Any

from .. import stdio_guard
from .._result import err, guard, ok
from . import _imports, connections, safety, serialize

MUTATING_CALLS = {
    "add", "update", "delete", "publish", "share", "unshare", "create", "create_view", "edit_features", "append",
    "calculate", "truncate", "overwrite", "update_definition", "add_to_definition", "delete_from_definition",
    "disable", "enable", "reassign_to", "update_role", "update_license_type", "add_users", "remove_users",
    "clone_items", "post", "put", "patch", "move", "protect", "copy_item", "delete_features", "rename", "start",
    "stop", "invite_users", "remove", "save", "export", "delete_items", "share_items", "allocate", "deallocate",
    "execute_notebook", "upload", "register", "unregister", "replace", "restart", "set_password", "update_level",
}
MUTATING_PREFIXES = ("delete_", "update_", "add_", "remove_", "create_", "set_", "publish_", "share_", "unshare_")
MUTATING_ATTRS = {"sharing_level", "privileges", "title", "snippet", "description", "tags", "access",
                  "capabilities", "renderer", "basemap", "content_status"}
BANNED_NAMES = {"exec", "eval", "compile", "__import__", "breakpoint", "input"}
BANNED_MODULE_CALLS = {("os", "remove"), ("os", "unlink"), ("os", "rmdir"), ("os", "system"), ("os", "popen"),
                       ("shutil", "rmtree"), ("shutil", "move"), ("subprocess", "run"), ("subprocess", "Popen"),
                       ("subprocess", "call"), ("subprocess", "check_output"), ("requests", "post"),
                       ("requests", "put"), ("requests", "delete"), ("requests", "patch")}
_SESSIONS: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_MAX_SESSIONS = 20
_SESSION_LOCK = threading.Lock()


def read_mode_violations(code: str) -> list[str]:
    """AST seatbelt for mode='read'. Returns human-readable reasons (empty = allowed)."""
    tree = ast.parse(code)
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                name = fn.attr
                base = fn.value.id if isinstance(fn.value, ast.Name) else None
                if (base, name) in BANNED_MODULE_CALLS:
                    problems.append(f"line {node.lineno}: {base}.{name}() is not allowed in read mode")
                elif name in MUTATING_CALLS or name.startswith(MUTATING_PREFIXES):
                    problems.append(f"line {node.lineno}: .{name}() writes to the portal")
            elif isinstance(fn, ast.Name):
                if fn.id in BANNED_NAMES:
                    problems.append(f"line {node.lineno}: {fn.id}() is not allowed")
                elif fn.id == "open" and _opens_for_write(node):
                    problems.append(f"line {node.lineno}: open() for writing is not allowed in read mode")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr in MUTATING_ATTRS:
                    problems.append(f"line {node.lineno}: assigning .{t.attr} writes to the portal")
        elif isinstance(node, ast.Delete):
            problems.append(f"line {node.lineno}: del statements are not allowed in read mode")
    return problems


def _opens_for_write(call: ast.Call) -> bool:
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def _namespace(connection: str, code: str) -> dict:
    _imports.ensure_base()
    import arcgis  # type: ignore
    from arcgis.features import FeatureLayer, FeatureLayerCollection, GeoAccessor, Table  # type: ignore
    from arcgis.gis import ItemProperties, ItemTypeEnum  # type: ignore

    ns: dict[str, Any] = {"gis": connections.get(connection), "arcgis": arcgis, "json": json,
                          "ItemProperties": ItemProperties, "ItemTypeEnum": ItemTypeEnum, "FeatureLayer": FeatureLayer,
                          "FeatureLayerCollection": FeatureLayerCollection, "Table": Table, "GeoAccessor": GeoAccessor,
                          "result": None, "__name__": "portal_python"}
    try:
        import pandas as pd  # type: ignore

        ns["pd"] = pd
    except Exception:  # noqa: BLE001
        pass
    for name in connections.status():
        ns[f"gis_{name}"] = connections.get(name)
    if re.search(r"\b(Map|Scene)\s*\(", code):
        amap = _imports.load("arcgis.map")
        ns.update(Map=amap.Map, Scene=amap.Scene)
    return ns


@guard
def run(code: str, mode: str = "read", confirm: bool = False, session_id: str | None = None,
        timeout_s: int = 120, max_chars: int = 20000, connection: str = "default") -> dict:
    mode = (mode or "read").lower()
    if mode not in ("read", "write"):
        return err("mode must be read | write.", code="bad_argument")
    try:
        compiled = compile(code, "<portal_python>", "exec")
    except SyntaxError as exc:
        return err(f"SyntaxError: {exc.msg} (line {exc.lineno})", code="syntax_error")
    if mode == "read":
        problems = read_mode_violations(code)
        if problems:
            return err("Read mode refused this code (it looks like it writes). Inspect first in read mode; to "
                       "change the portal, ask the user and use mode='write' with confirm=True.",
                       code="read_mode_blocked", problems=problems[:20])
    else:
        if not safety.write_exec_allowed():
            return err("Write mode is disabled (ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC=false).", code="disabled")
        if not confirm:
            return safety.confirm_required("Running portal_python in WRITE mode",
                                           {"code": serialize.text(code, 3000), "connection": connection})
    with _SESSION_LOCK:
        ns = _SESSIONS.get(session_id) if session_id else None
    if ns is None:
        ns = _namespace(connection, code)
        if session_id:
            with _SESSION_LOCK:
                _SESSIONS[session_id] = ns
                while len(_SESSIONS) > _MAX_SESSIONS:
                    _SESSIONS.popitem(last=False)
    else:
        if re.search(r"\b(Map|Scene)\s*\(", code) and "Map" not in ns:
            amap = _imports.load("arcgis.map")
            ns.update(Map=amap.Map, Scene=amap.Scene)
    ns["result"] = None

    capture = stdio_guard.install()  # stdout is process-global: capture per thread, never on the MCP channel
    outcome: dict = {}

    def work() -> None:
        buf = io.StringIO()
        capture.buffers[threading.get_ident()] = buf
        try:
            exec(compiled, ns)  # noqa: S102 - the escape hatch, guarded above
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001
            outcome["ok"] = False
            outcome["error"] = f"{type(exc).__name__}: {exc}"
            outcome["traceback"] = traceback.format_exc(limit=6)
        finally:
            outcome["stdout"] = buf.getvalue()
            capture.buffers.pop(threading.get_ident(), None)

    started = time.perf_counter()
    worker = threading.Thread(target=work, name="portal_python", daemon=True)
    worker.start()
    worker.join(max(1, min(int(timeout_s or 120), 3600)))
    elapsed = round(time.perf_counter() - started, 2)
    if worker.is_alive():
        return err(f"Still running after {timeout_s} s; it continues in the background (it cannot be killed). "
                   "Use a session_id and read `result` later, or split the work.", code="timeout",
                   session_id=session_id)
    stdout = safety.scrub(serialize.text(outcome.get("stdout", ""), max_chars))
    if mode == "write":
        safety.audit("portal_python", "write", connection=connection, ok=outcome.get("ok"),
                     details={"code": code, "error": outcome.get("error")})
    if not outcome.get("ok"):
        return err(safety.scrub(outcome["error"]), code="exception", stdout=stdout or None,
                   traceback=safety.scrub(outcome.get("traceback")))
    result = safety.scrub(serialize.jsonable(ns.get("result")))
    text = json.dumps(result, default=str)
    if len(text) > max_chars:
        result = serialize.text(text, max_chars)
    return ok({"result": result, "stdout": stdout or None, "seconds": elapsed, "session_id": session_id,
               "variables": sorted(k for k in ns if not k.startswith("_") and k not in (
                   "gis", "arcgis", "json", "pd", "result", "ItemProperties", "ItemTypeEnum", "FeatureLayer",
                   "FeatureLayerCollection", "Table", "GeoAccessor", "Map", "Scene"))[:50] if session_id else None})
