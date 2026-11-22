"""Locked by the open ArcGIS Pro session -> run the tool INSIDE that session.

ArcPy here runs in its own process. A dataset that is a layer in the map of an open ArcGIS Pro (its Contents
pane) is held by Pro's process, so a schema change or an in-place edit from here fails with a lock error
(ERROR 000464 "Cannot get exclusive schema lock", ERROR 000496 "table is being edited", "Cannot acquire a
lock"), while the same tool run in Pro works: there the lock is its own. Users had to remove the layer from
the map first.

``lock_aware(arcpy)`` wraps arcpy so every toolbox function (``arcpy.management.AddField`` ...) that fails
with a lock error is sent once to the SalahAIBridge add-in (``run_gp``), which runs it in the open session.
Everything else on arcpy passes through untouched. With no bridge, the error says what to do instead of
only "locked". Pure Python apart from the arcpy it is handed, so it is unit-tested with a fake arcpy.
"""
from __future__ import annotations

import functools
import inspect
import re
from typing import Any

# The toolbox modules whose functions are geoprocessing tools (arcpy.<alias>.<Tool>).
TOOLBOXES = frozenset({
    "management", "analysis", "conversion", "edit", "cartography", "stats", "sa", "ia", "ddd", "na", "lr", "ga",
    "gapro", "geocoding", "md", "locref", "un", "tn", "topographic", "defense", "intelligence", "aviation",
    "maritime", "reviewer", "wmx", "server", "sharing", "parcel", "nd", "oceans", "stpm", "geoai", "rt", "indoors",
    "bdc", "crime", "cd", "trajectory",
})

_LOCK = re.compile(
    r"000464|000496|160706|schema lock|exclusive lock|cannot acquire a lock|lock request conflicts"
    r"|is being edited|being used by another|in use by another|locked by another",
    re.IGNORECASE,
)

# A geoprocessing tool in Pro can take a while (CalculateField on a big table); the bridge's default is 30 s.
LIVE_TIMEOUT = 900


class DatasetLocked(RuntimeError):
    """A lock that could not be worked around; ``guard`` returns its ``code`` and ``hint``."""

    code = "locked"

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


def is_lock_error(error: object) -> bool:
    return bool(_LOCK.search(str(error)))


class LiveResult:
    """What the bridge returned, shaped like the part of an arcpy Result the ops use."""

    def __init__(self, outputs: list, reply: dict):
        self._outputs = [str(o) for o in outputs]
        self.live = reply
        self.outputCount = len(self._outputs)

    def __getitem__(self, i: int) -> str:
        return self._outputs[i]

    def getOutput(self, i: int) -> str:  # noqa: N802 - arcpy's name
        return self._outputs[i]

    def getMessages(self, *_a: Any) -> str:  # noqa: N802 - arcpy's name
        return "Run in the open ArcGIS Pro session (the dataset was locked by it)."

    def __str__(self) -> str:
        return self._outputs[0] if self._outputs else ""

    __fspath__ = __str__


def _plain(value: Any) -> Any:
    """An argument as JSON the bridge can turn into a GP value."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "outputCount"):  # an arcpy Result: its first output
        return str(value[0]) if value.outputCount else ""
    if hasattr(value, "factoryCode") and getattr(value, "factoryCode", 0):  # SpatialReference
        return int(value.factoryCode)
    if hasattr(value, "exportToString"):
        return value.exportToString()
    return str(value)


def positional(func: Any, args: tuple, kwargs: dict) -> list:
    """(args, kwargs) -> the positional list a GP tool takes; skipped parameters become "" (the default)."""
    if not kwargs:
        return [_plain(a) for a in args]
    try:
        params = list(inspect.signature(func).parameters)
    except (TypeError, ValueError):
        params = []
    unknown = [k for k in kwargs if k not in params]
    if unknown:
        raise ValueError(f"cannot pass {', '.join(unknown)} by name to the open session")
    values = list(args) + [None] * max(0, len(params) - len(args))
    last = len(args) - 1
    for name, value in kwargs.items():
        i = params.index(name)
        values[i] = value
        last = max(last, i)
    return [_plain(v) for v in values[: last + 1]]


def run_in_pro(tool: str, func: Any, args: tuple, kwargs: dict, cause: Exception, arcpy: Any = None) -> LiveResult:
    """Run ``tool`` in the open Pro session after ``cause`` (a lock error) here. Raises DatasetLocked if it cannot."""
    from ..live import client, policy
    from .register import bridge_listening

    what = str(cause).strip().splitlines()[0] if str(cause).strip() else type(cause).__name__
    if not bridge_listening():
        raise DatasetLocked(
            f"{tool}: the dataset is locked by another application ({what}).",
            "It is probably a layer in an open ArcGIS Pro map. Either open ArcGIS Pro with the Salah MCP add-in "
            "(SalahAIBridge) so the tool runs inside that session automatically, or close Pro / remove the layer "
            "from the map and run it again.") from cause
    blocked = policy.check("run_gp", confirm=True)  # the caller already asked for this tool; read-only still applies
    if blocked:
        raise DatasetLocked(f"{tool}: the dataset is locked by the open ArcGIS Pro session ({what}).",
                            str(blocked.get("error", ""))) from cause
    try:
        values = positional(func, args, kwargs)
    except ValueError as exc:
        raise DatasetLocked(f"{tool}: the dataset is locked by the open ArcGIS Pro session ({what}).",
                            f"It could not be sent to that session: {exc}. Pass the parameters in order (args).") from cause
    env: dict = {}
    workspace = getattr(getattr(arcpy, "env", None), "workspace", None)
    if workspace:
        env["workspace"] = str(workspace)
    reply = client.post("run_gp", {"tool": tool, "args": values, "kwargs": env}, timeout=LIVE_TIMEOUT)
    if not reply.get("ok"):
        raise DatasetLocked(
            f"{tool}: locked here ({what}), and running it in the open ArcGIS Pro session failed: {reply.get('error')}",
            "If Pro shows a dialog or an edit session with unsaved edits, finish it (save or discard) and run again.",
        ) from cause
    data = reply.get("data") or {}
    return LiveResult(data.get("outputs") or [], data)


class _Toolbox:
    """arcpy.<toolbox> with its tools wrapped; anything else on the module is passed through."""

    def __init__(self, module: Any, alias: str, arcpy: Any):
        self._module, self._alias, self._arcpy = module, alias, arcpy

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._module, name)
        if not callable(attr) or not name[:1].isupper() or isinstance(attr, type):
            return attr
        tool = f"{self._alias}.{name}"

        @functools.wraps(attr)
        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return attr(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - only lock errors are handled, the rest re-raise
                if not is_lock_error(exc):
                    raise
                return run_in_pro(tool, attr, args, kwargs, exc, self._arcpy)

        return call

    def __dir__(self) -> list:
        return dir(self._module)


class LockAwareArcpy:
    """arcpy, with the toolbox modules wrapped by ``_Toolbox``."""

    def __init__(self, arcpy: Any):
        object.__setattr__(self, "_arcpy", arcpy)
        object.__setattr__(self, "_toolboxes", {})

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._arcpy, name)
        if name not in TOOLBOXES:
            return attr
        if name not in self._toolboxes:
            self._toolboxes[name] = _Toolbox(attr, name, self._arcpy)
        return self._toolboxes[name]

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._arcpy, name, value)

    def __dir__(self) -> list:
        return dir(self._arcpy)


def wrap_toolbox(module: Any, alias: str, arcpy: Any = None) -> Any:
    """One toolbox module imported on its own (``import arcpy.un as un``), with the same lock handling."""
    return module if isinstance(module, _Toolbox) else _Toolbox(module, alias, getattr(arcpy, "_arcpy", arcpy))


def lock_aware(arcpy: Any) -> Any:
    return arcpy if isinstance(arcpy, LockAwareArcpy) else LockAwareArcpy(arcpy)
