"""HTTP client for the live ArcGIS Pro bridge (Layer 1b).

The bridge is a .NET add-in running INSIDE an open ArcGIS Pro session (see
``SalahAIBridge/`` and ``docs/PROTOCOL.md``). This module is a thin, dependency-
free client (Python stdlib ``urllib`` only — no new deps) that speaks the same
``{"ok": ...}`` envelope as the rest of the system. Loopback-only, no token.

Design rule: it NEVER raises across the MCP boundary. Connection failures
and malformed replies all come back as a normal error envelope so
the agent gets a clean message (e.g. "bridge unreachable") when ArcGIS Pro or
the add-in isn't running.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .._result import err
from ..config import CONFIG


def _base_url() -> str:
    return f"http://127.0.0.1:{CONFIG.bridge_port}"


def post(command: str, params: dict | None = None) -> dict:
    """POST one command to the bridge and return its envelope verbatim.

    On success returns the add-in's ``{"ok": ...}`` envelope unchanged. On any
    transport/auth/parse problem returns a locally built error envelope; it
    never raises so the caller (a ``@guard``-wrapped op) can forward it as-is.
    """
    url = f"{_base_url()}/cmd"
    body = json.dumps({"command": command, "params": params or {}}).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Auth/transport failure (401/400/500). Prefer the add-in's own JSON
        # error body when it sent one; otherwise synthesize a message.
        detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
        parsed = _try_json(detail)
        if isinstance(parsed, dict) and "ok" in parsed:
            return parsed
        return err(f"bridge HTTP {exc.code}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        return err(f"bridge unreachable: {exc.reason}")
    except Exception as exc:  # noqa: BLE001 - surface anything as a clean envelope
        return err(f"bridge unreachable: {exc}")

    parsed = _try_json(raw)
    if not isinstance(parsed, dict) or "ok" not in parsed:
        return err(f"bridge returned a non-envelope response: {raw[:200]!r}")
    return parsed


def health() -> dict:
    """GET /health — no-auth liveness probe. Returns the bridge's envelope or a
    clean ``bridge unreachable`` error."""
    url = f"{_base_url()}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return err(f"bridge unreachable: {exc.reason}")
    except Exception as exc:  # noqa: BLE001
        return err(f"bridge unreachable: {exc}")

    parsed = _try_json(raw)
    if isinstance(parsed, dict) and "ok" in parsed:
        return parsed
    return err(f"bridge returned a non-envelope response: {raw[:200]!r}")


def _try_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None
