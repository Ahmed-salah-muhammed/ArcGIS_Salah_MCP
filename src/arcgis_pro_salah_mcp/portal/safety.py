"""Safety rails shared by every portal_* tool: confirmation, secrets, audit log, limits, credits.

* Irreversible / bulk / public / admin writes refuse with ``code="confirm_required"`` and a preview
  until called again with ``confirm=True`` (the same rail as the egdb and live tools).
* Secrets never travel as tool arguments: callers pass the NAME of an environment variable
  (``password_env``), and every value read that way is registered so ``scrub`` masks it in any output.
* Every portal write appends one JSON line to the audit log (no secrets) - see ``audit_path``.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .._result import err

_SECRETS: set[str] = set()
_LOCK = threading.Lock()

# token=..., "token": "...", password=..., Authorization: Bearer ...
_TOKEN_PATTERNS = [
    re.compile(r"(?i)((?:access_)?token|password|passwd|pwd|api_?key|client_secret)(\s*[=:]\s*[\"']?)([^\"'&\s,}]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"),
]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def max_edits() -> int:
    """Feature edits allowed in one call (ARCGIS_SALAH_MAX_EDITS, default 5000)."""
    return _env_int("ARCGIS_SALAH_MAX_EDITS", 5000)


def max_users() -> int:
    """Users one onboarding run may create (ARCGIS_SALAH_MAX_USERS, default 200)."""
    return _env_int("ARCGIS_SALAH_MAX_USERS", 200)


def credit_limit() -> float:
    """Most credits one confirmed call may spend (ARCGIS_SALAH_CREDIT_LIMIT, default 100)."""
    return _env_float("ARCGIS_SALAH_CREDIT_LIMIT", 100.0)


def write_exec_allowed() -> bool:
    return os.environ.get("ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC", "true").strip().lower() not in {
        "0", "false", "no", "off"}


# --- secrets ---------------------------------------------------------------

def register_secret(value: str | None) -> None:
    if value and len(value) >= 4:
        with _LOCK:
            _SECRETS.add(value)


def secret_from_env(env_name: str | None, what: str = "password") -> tuple[str | None, dict | None]:
    """Read a secret by env-var NAME -> (value, None) or (None, error envelope). Never echoes the value."""
    if not env_name:
        return None, None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
        return None, err(f"{what}_env must be an environment variable NAME (e.g. ARCGIS_ADMIN_PASSWORD), "
                         "not the secret itself.", code="bad_secret_reference")
    value = os.environ.get(env_name)
    if not value:
        return None, err(f"Environment variable {env_name} is not set (it should hold the {what}).",
                         code="secret_missing", env=env_name)
    register_secret(value)
    return value, None


def scrub(value: Any) -> Any:
    """Mask registered secrets and anything that looks like a token/password in strings, recursively."""
    if isinstance(value, str):
        out = value
        with _LOCK:
            secrets = sorted(_SECRETS, key=len, reverse=True)
        for s in secrets:
            out = out.replace(s, "***")
        out = _TOKEN_PATTERNS[0].sub(lambda m: m.group(1) + m.group(2) + "***", out)
        out = _TOKEN_PATTERNS[1].sub(lambda m: m.group(1) + "***", out)
        return out
    if isinstance(value, dict):
        return {k: ("***" if isinstance(k, str) and k.lower() in {"token", "password", "access_token",
                                                                   "refresh_token", "api_key", "apikey"}
                    else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


# --- confirmation ----------------------------------------------------------

def confirm_required(action: str, preview: Any = None, hint: str | None = None) -> dict:
    return err(f"{action} needs confirm=True. Show the user the preview, ask, then call again with confirm=True.",
               code="confirm_required", preview=preview,
               **({"hint": hint} if hint else {}))


# --- audit log -------------------------------------------------------------

def audit_path() -> Path | None:
    """ARCGIS_SALAH_AUDIT_LOG (a file path, or 'off'); default %LOCALAPPDATA%/arcgis_pro_salah_mcp/audit.jsonl."""
    raw = os.environ.get("ARCGIS_SALAH_AUDIT_LOG", "").strip()
    if raw.lower() in {"off", "0", "false", "none"}:
        return None
    if raw:
        return Path(raw)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "arcgis_pro_salah_mcp" / "audit.jsonl"


def audit(tool: str, action: str, *, connection: str | None = None, target: Any = None, ok: bool | None = None,
          before: Any = None, after: Any = None, details: Any = None) -> None:
    """Append one line to the audit log. Never raises (an unwritable log must not break a tool)."""
    path = audit_path()
    if path is None:
        return
    record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool, "action": action,
              "connection": connection, "target": target, "ok": ok}
    for key, val in (("before", before), ("after", after), ("details", details)):
        if val is not None:
            record[key] = val
    try:
        line = json.dumps(scrub(record), default=str, ensure_ascii=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_audit(limit: int = 50) -> list[dict]:
    path = audit_path()
    if path is None or not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --- credits ---------------------------------------------------------------

def credit_gate(estimate: float | None, confirm: bool, what: str, is_online: bool) -> dict | None:
    """None when the call may proceed; otherwise an error envelope (confirm_required / credit_limit)."""
    if not is_online or not estimate:
        return None
    if estimate > credit_limit():
        return err(f"{what} is estimated at {estimate:g} credits, above ARCGIS_SALAH_CREDIT_LIMIT "
                   f"({credit_limit():g}). Raise the limit deliberately or split the job.",
                   code="credit_limit", estimated_credits=estimate)
    if not confirm:
        return confirm_required(what, preview={"estimated_credits": estimate},
                                hint="This spends ArcGIS Online credits.")
    return None
