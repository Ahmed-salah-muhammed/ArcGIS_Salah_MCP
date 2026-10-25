"""Chained geoprocessing in a single call, plus the async job runner.

Two round-trip killers live here.

``pipeline()``
    ANY chain of geoprocessing tools - anything reachable as ``arcpy.<toolbox>.<Tool>``
    (analysis, management, conversion, sa, ia, ...) - runs in one call. Buffer -> clip ->
    dissolve is just the classic example: it used to be three tool calls, i.e. three
    model inferences. Here the steps are declared up front and executed server-side,
    each able to reference an earlier step's output via ``{{stepN}}`` / ``{{prev}}``.

``submit()`` / ``job_status()``
    Some geoprocessing genuinely takes minutes. Rather than holding the MCP call
    open (and risking a client timeout), the work moves to a worker thread and
    the agent gets a job id back immediately, then polls. The agent stays
    responsive to the user while ArcGIS grinds.
"""
from __future__ import annotations

import concurrent.futures
import re
import threading
import time
import uuid
from typing import Any, Callable

from .._result import err, guard, ok
from . import cache

_PLACEHOLDER = re.compile(r"\{\{\s*(prev|step(\d+))\s*\}\}", re.IGNORECASE)


# --- Step execution --------------------------------------------------------

def _resolve_tool(arcpy, dotted: str):
    """'analysis.Buffer' -> the callable. Also accepts a bare 'Buffer'."""
    target: Any = arcpy
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        raise ValueError("Empty tool name.")
    for part in parts:
        target = getattr(target, part, None)
        if target is None:
            raise AttributeError(
                f"Geoprocessing tool '{dotted}' not found. Use a dotted name "
                f"like 'analysis.Buffer' or 'management.Dissolve'."
            )
    if not callable(target):
        raise TypeError(f"'{dotted}' is not callable.")
    return target


def _substitute(value: Any, outputs: list[str]) -> Any:
    """Replace {{prev}} / {{stepN}} with an earlier step's output path.

    Steps are 1-indexed in the placeholder because that is how the agent (and a
    human reading the pipeline) counts them.
    """
    if isinstance(value, list):
        return [_substitute(v, outputs) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, outputs) for k, v in value.items()}
    if not isinstance(value, str):
        return value

    def repl(match: re.Match) -> str:
        token, num = match.group(1).lower(), match.group(2)
        if token == "prev":
            if not outputs:
                raise ValueError("{{prev}} used in the first step — nothing precedes it.")
            return str(outputs[-1])
        idx = int(num)
        if not 1 <= idx <= len(outputs):
            raise ValueError(
                f"{{{{step{idx}}}}} refers to a step that has not run yet "
                f"({len(outputs)} completed)."
            )
        return str(outputs[idx - 1])

    return _PLACEHOLDER.sub(repl, value)


def _run_steps(
    steps: list[dict],
    stop_on_error: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    from . import ops

    arcpy = ops._arcpy()
    outputs: list[str] = []
    results: list[dict] = []

    for i, step in enumerate(steps, start=1):
        name = step.get("tool")
        if not name:
            results.append({"step": i, "ok": False, "error": "Step is missing 'tool'."})
            if stop_on_error:
                break
            continue

        label = step.get("label") or name
        if progress:
            progress(f"[{i}/{len(steps)}] {label}")

        started = time.perf_counter()
        try:
            args = _substitute(list(step.get("args") or []), outputs)
            kwargs = _substitute(dict(step.get("kwargs") or {}), outputs)
            fn = _resolve_tool(arcpy, name)
            raw = fn(*args, **kwargs)

            # A GP Result's str() is its primary output path — that is what the
            # next step chains onto.
            out_path = str(raw) if raw is not None else None
            messages = None
            try:
                messages = raw.getMessages()
            except Exception:  # noqa: BLE001 - not every return is a Result
                pass

            outputs.append(out_path)
            if out_path:
                cache.invalidate(out_path)
            results.append(
                {
                    "step": i,
                    "tool": name,
                    "label": label,
                    "ok": True,
                    "output": out_path,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "messages": messages,
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            results.append(
                {
                    "step": i,
                    "tool": name,
                    "label": label,
                    "ok": False,
                    "error": str(exc),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
            if stop_on_error:
                break

    succeeded = sum(1 for r in results if r.get("ok"))
    return {
        "steps": results,
        "completed": succeeded,
        "requested": len(steps),
        "final_output": outputs[-1] if outputs else None,
        "ok_all": succeeded == len(steps),
    }


@guard
def pipeline(steps: list[dict], stop_on_error: bool = True) -> dict:
    """Run several geoprocessing tools in order, chaining their outputs.

    Each step is ``{"tool": "analysis.Buffer", "args": [...], "kwargs": {...},
    "label": "optional"}``. Inside ``args``/``kwargs``, ``{{prev}}`` expands to
    the previous step's output and ``{{step2}}`` to step 2's, so intermediate
    paths never have to make a round trip back to the agent.
    """
    if not steps:
        return err("No steps provided.", code="empty_pipeline")
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        return err("steps must be a list of {tool, args, kwargs} objects.")
    result = _run_steps(steps, stop_on_error)
    return ok(result) if result["ok_all"] else {"ok": False, "error": "One or more steps failed.", "data": result}


# --- Async jobs ------------------------------------------------------------

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 100


def _set(job_id: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _worker(job_id: str, steps: list[dict], stop_on_error: bool) -> None:
    _set(job_id, state="running", started_at=time.time())
    try:
        result = _run_steps(
            steps,
            stop_on_error,
            progress=lambda msg: _set(job_id, progress=msg),
        )
        _set(
            job_id,
            state="finished" if result["ok_all"] else "failed",
            result=result,
            finished_at=time.time(),
        )
    except Exception as exc:  # noqa: BLE001 - a worker must never die silently
        _set(job_id, state="failed", error=str(exc), finished_at=time.time())


@guard
def submit(steps: list[dict], stop_on_error: bool = True, label: str | None = None) -> dict:
    """Start a pipeline on a worker thread and return a job id immediately."""
    if not steps:
        return err("No steps provided.", code="empty_pipeline")

    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        # Keep the table bounded: drop the oldest finished jobs first.
        if len(_JOBS) >= _MAX_JOBS:
            done = sorted(
                (j for j in _JOBS.values() if j["state"] in ("finished", "failed")),
                key=lambda j: j.get("finished_at") or 0,
            )
            for stale in done[: max(1, len(_JOBS) - _MAX_JOBS + 1)]:
                _JOBS.pop(stale["id"], None)
        _JOBS[job_id] = {
            "id": job_id,
            "label": label or f"{len(steps)} step(s)",
            "state": "queued",
            "progress": None,
            "submitted_at": time.time(),
            "steps": len(steps),
        }

    # On THE arcpy thread, not a fresh one: arcpy calls off the importing thread failed with
    # PostgreSQL geodatabases (see arcpy_thread). Jobs therefore queue behind other arcpy work.
    from .. import arcpy_thread

    arcpy_thread.submit(_worker, job_id, steps, stop_on_error, label=f"job {job_id}")
    return ok({"job_id": job_id, "state": "queued", "steps": len(steps), "next": _next_hint(job_id)})


def _new_job(label: str, **extra: Any) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        if len(_JOBS) >= _MAX_JOBS:
            done = sorted((j for j in _JOBS.values() if j["state"] in ("finished", "failed")),
                          key=lambda j: j.get("finished_at") or 0)
            for stale in done[: max(1, len(_JOBS) - _MAX_JOBS + 1)]:
                _JOBS.pop(stale["id"], None)
        _JOBS[job_id] = {"id": job_id, "label": label, "state": "queued", "progress": None,
                         "submitted_at": time.time(), **extra}
    return job_id


def submit_call(label: str, fn: Any, arguments: dict | None = None) -> dict:
    """Run one tool function as a background job on the arcpy thread; poll with job_status.

    For calls that outlive the MCP client's tool timeout (about a minute was reported): the client
    gives up waiting but the work carries on, and its envelope is kept here.
    """
    from .. import arcpy_thread

    job_id = _new_job(label, tool=label)

    def work() -> None:
        _set(job_id, state="running", started_at=time.time())
        try:
            result = fn(**(arguments or {}))
            failed = isinstance(result, dict) and result.get("ok") is False
            _set(job_id, state="failed" if failed else "finished", result=result, finished_at=time.time())
        except Exception as exc:  # noqa: BLE001 - a job must never die silently
            _set(job_id, state="failed", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())

    arcpy_thread.submit(work, label=f"background {label}")
    return ok({"job_id": job_id, "state": "queued", "tool": label, "next": _next_hint(job_id)})


# Jobs that are not themselves arcpy work (pro_batch: it dispatches arcpy calls TO the arcpy thread and
# portal/live calls elsewhere) run here, so they never hold the arcpy thread while waiting on the network.
_JOB_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")


def _next_hint(job_id: str) -> str:
    return f"pro_server(action='job', job_id='{job_id}') until state is finished or failed"


def submit_job(label: str, fn: Callable[[Callable[[str], None]], Any], **extra: Any) -> dict:
    """Run ``fn(progress)`` as a background job on the job pool; its return value is kept as the result."""
    job_id = _new_job(label, **extra)

    def work() -> None:
        _set(job_id, state="running", started_at=time.time())
        try:
            result = fn(lambda msg: _set(job_id, progress=msg))
            failed = isinstance(result, dict) and result.get("ok") is False
            _set(job_id, state="failed" if failed else "finished", result=result, finished_at=time.time())
        except Exception as exc:  # noqa: BLE001 - a job must never die silently
            _set(job_id, state="failed", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())

    _JOB_POOL.submit(work)
    return ok({"job_id": job_id, "state": "queued", "label": label, "next": _next_hint(job_id)})


def jobs_summary() -> dict:
    with _JOBS_LOCK:
        counts: dict[str, int] = {}
        for j in _JOBS.values():
            counts[j["state"]] = counts.get(j["state"], 0) + 1
    return counts


@guard
def job_status(job_id: str | None = None) -> dict:
    """Poll one job, or list them all when ``job_id`` is omitted."""
    with _JOBS_LOCK:
        if job_id is None:
            return ok(
                {
                    "jobs": [
                        {k: v for k, v in j.items() if k != "result"}
                        for j in sorted(
                            _JOBS.values(), key=lambda j: -j.get("submitted_at", 0)
                        )
                    ]
                }
            )
        job = _JOBS.get(job_id)
        if job is None:
            return err(f"No job '{job_id}'.", code="unknown_job")
        snapshot = dict(job)

    if snapshot.get("started_at"):
        end = snapshot.get("finished_at") or time.time()
        snapshot["elapsed_s"] = round(end - snapshot["started_at"], 1)
    return ok(snapshot)
