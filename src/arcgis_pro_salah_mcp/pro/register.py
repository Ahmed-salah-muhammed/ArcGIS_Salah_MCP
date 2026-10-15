"""Put a freshly written toolbox into the OPEN ArcGIS Pro project, through the live bridge.

Pure Python (no ``arcpy``/``arcgis``) and best effort: it never raises, and when the bridge is
absent or the add-in is too old it hands back the manual steps instead. Only the MCP entry points
call it (``register_in_project=True``); the generators stay side-effect free so their tests never
reach a real Pro session.

``add_toolbox`` uses only public SDK calls and is verified against the project's own item list
(see ``docs/PROTOCOL.md``). One trap is specific to ``.pyt``: the FIRST time Pro loads a Python
toolbox it asks the user to *trust the code source*, a dialog on the UI thread. Until it is
answered the bridge cannot reply (even ``ping`` times out); the add then completes by itself.
"""
from __future__ import annotations

import socket

MANUAL = "Catalog pane > right-click Toolboxes > Add Toolbox, then choose the file."
TRUST_HINT = (
    "Pro is probably showing a 'trust the code source' dialog for this .pyt. Click Trust there: "
    "the toolbox is added as soon as you do, so do not retry. Until then live_* calls time out."
)
OLD_ADDIN_HINT = (
    "The installed add-in predates add_toolbox. Install SalahAIBridge 0.1.4 or later "
    "(close Pro, double-click the .esriAddinX)."
)


def bridge_listening() -> bool:
    """True if something accepts connections on the bridge port (a ~1 ms probe)."""
    from ..config import CONFIG

    try:
        socket.create_connection(("127.0.0.1", CONFIG.bridge_port), timeout=0.25).close()
        return True
    except OSError:
        return False


def register_toolbox(path: str) -> dict:
    """Add ``path`` (.atbx / .tbx / .pyt) to the open project. Never raises."""
    if not bridge_listening():
        return {"added": False, "reason": "the live bridge is not running", "manual": MANUAL}
    try:
        from ..live import ops as live_ops

        reply = live_ops.live_add_toolbox(path)
    except Exception as exc:  # noqa: BLE001 - registration is best effort
        return {"added": False, "reason": f"{type(exc).__name__}: {exc}", "manual": MANUAL}

    if not reply.get("ok"):
        message = str(reply.get("error", ""))
        low = message.lower()
        out: dict = {"added": False, "reason": message, "manual": MANUAL}
        if "unknown command" in low:
            out["hint"] = OLD_ADDIN_HINT
        elif "timed out" in low and str(path).lower().endswith(".pyt"):
            out["pending"] = True
            out["hint"] = TRUST_HINT
        return out

    data = reply.get("data") or {}
    registered = bool(data.get("registered"))
    return {
        "added": registered,
        "already_registered": bool(data.get("already_registered")),
        "steps": data.get("steps"),
        "manual": None if registered else MANUAL,
    }


def attach(result: dict, path: str, enabled: bool) -> dict:
    """Add the registration outcome to a successful generator envelope (in place)."""
    if enabled and isinstance(result, dict) and result.get("ok") and isinstance(result.get("data"), dict):
        result["data"]["project"] = register_toolbox(path)
    return result
