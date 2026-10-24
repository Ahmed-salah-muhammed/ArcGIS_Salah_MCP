"""One thread owns arcpy - the Python counterpart of ArcGIS Pro's Main CIM Thread.

Measured on this machine (ArcGIS Pro 3.7, PostgreSQL 18 enterprise geodatabase): ``arcpy.ListVersions``
failed with ERROR 87395, ``ArcSDESQLExecute`` with ERROR 87413 and ``arcpy.da.ListVersions`` with a
TypeError carrying only the path whenever they ran on ANY thread other than the one that imported
arcpy - the main thread included, and on every retry. On the importing thread they worked. SQL Server
was unaffected. The server used to import arcpy on a warm-up thread and run tools on the event-loop
thread, which is exactly the failing pattern.

So arcpy is imported on ONE long-lived worker thread and every arcpy-backed tool runs there
(``submit`` / ``make_async``). Two side effects are wanted as well: calls are serialised (arcpy is not
built for concurrent use), and the asyncio event loop no longer blocks while a long call runs, so
other requests - status, live bridge, background-job polling - are still answered.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import threading
import time
from typing import Any, Callable

_STATE: dict[str, Any] = {"thread_id": None, "busy": None, "since": None, "queued": 0, "completed": 0}
_LOCK = threading.Lock()


def _remember_thread() -> None:
    with _LOCK:
        _STATE["thread_id"] = threading.get_ident()


# max_workers=1: the executor keeps that single thread alive for its whole life, so every job - and the
# arcpy import - runs on the same OS thread.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="arcpy",
                                                  initializer=_remember_thread)


def on_arcpy_thread() -> bool:
    return _STATE["thread_id"] == threading.get_ident()


def submit(fn: Callable[..., Any], *args: Any, label: str | None = None, **kwargs: Any) -> concurrent.futures.Future:
    """Queue ``fn`` on the arcpy thread; returns a Future."""
    with _LOCK:
        _STATE["queued"] += 1

    def job() -> Any:
        with _LOCK:
            _STATE.update(queued=_STATE["queued"] - 1, busy=label or getattr(fn, "__name__", "call"),
                          since=time.time())
        try:
            return fn(*args, **kwargs)
        finally:
            with _LOCK:
                _STATE.update(busy=None, since=None, completed=_STATE["completed"] + 1)

    return _EXECUTOR.submit(job)


def run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` on the arcpy thread and wait (directly when already on it - no deadlock)."""
    if on_arcpy_thread():
        return fn(*args, **kwargs)
    return submit(fn, *args, **kwargs).result()


def make_async(fn: Callable[..., Any], label: str) -> Callable[..., Any]:
    """An async stand-in for a sync tool function that runs it on the arcpy thread.

    ``functools.wraps`` keeps the name, docstring and (through ``__wrapped__``) the signature.
    """
    @functools.wraps(fn)
    async def runner(**kwargs: Any) -> Any:
        return await asyncio.wrap_future(submit(fn, label=label, **kwargs))

    return runner


def status() -> dict:
    """What the arcpy thread is doing right now (cheap; never waits for it)."""
    with _LOCK:
        snap = dict(_STATE)
    busy_for = round(time.time() - snap["since"], 1) if snap["since"] else None
    return {"busy_with": snap["busy"], "busy_for_s": busy_for, "queued": snap["queued"],
            "completed": snap["completed"], "started": snap["thread_id"] is not None}
