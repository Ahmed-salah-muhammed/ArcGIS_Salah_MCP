"""Shared JSON result envelope.

Every tool speaks the same agent-friendly contract, mirroring the
``qgis_salah_mcp`` project:

    success ->  {"ok": True,  "data": <payload>}
    failure ->  {"ok": False, "error": <message>, ...extra}

Keeping a single envelope makes the three layers (Pro / Portal / WebApp)
interchangeable from the agent's point of view.
"""
from __future__ import annotations

import functools
import traceback
from typing import Any, Callable


def ok(data: Any = None, **extra: Any) -> dict:
    """Build a success envelope."""
    out = {"ok": True, "data": data}
    out.update(extra)
    return out


def err(message: str, **extra: Any) -> dict:
    """Build an error envelope."""
    out = {"ok": False, "error": str(message)}
    out.update(extra)
    return out


def guard(fn: Callable[..., Any]) -> Callable[..., dict]:
    """Decorator: run ``fn`` and always return a result envelope.

    Any exception is caught and converted into an ``err(...)`` payload so the
    MCP server never crashes a tool call — the agent gets a structured error
    instead of a broken transport.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            result = fn(*args, **kwargs)
            # Allow ops to either return a raw payload or a full envelope.
            if isinstance(result, dict) and "ok" in result:
                return result
            return ok(result)
        except NotImplementedError as exc:
            return err(f"Not implemented yet: {exc}", code="not_implemented")
        except Exception as exc:  # noqa: BLE001 - we deliberately surface everything
            # An exception may name its own code and hint (pro.locks.DatasetLocked: code "locked").
            code = getattr(exc, "code", None)
            extra = {"hint": exc.hint} if isinstance(getattr(exc, "hint", None), str) else {}
            return err(
                str(exc),
                code=code if isinstance(code, str) else "exception",
                type=type(exc).__name__,
                traceback=traceback.format_exc(),
                **extra,
            )

    return wrapper
