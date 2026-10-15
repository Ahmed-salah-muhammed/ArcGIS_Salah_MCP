"""Safety policy for the live bridge (Layer 1b).

The ``live_*`` tools drive a *running* ArcGIS Pro session, so unlike the headless
``pro_*`` tools they can change what a human analyst is actively looking at. Two
guard rails sit in front of the bridge and are enforced **here, client-side,
before any HTTP request is sent** (so a blocked command never even reaches the
add-in):

* **Read-only mode** (``ARCGIS_BRIDGE_READONLY``) — blocks every command that
  changes data, the map's contents, or the disk. Reads and navigation stay
  allowed so the agent can still answer questions about the open session.
* **Destructive confirmation** — a small set of commands can irreversibly
  overwrite or delete data; they require an explicit ``confirm=True`` so the
  agent (and user) opt in deliberately.

Mirroring the wire contract in ``docs/PROTOCOL.md``, command names here are the
protocol commands (``run_gp``), not the tool names (``live_run_gp``).
"""
from __future__ import annotations

from .._result import err
from ..config import CONFIG

# Reads and navigation: change no data, no map contents, no disk. Always allowed,
# including in read-only mode. (``zoom_to`` only moves the camera.)
READ_ONLY = frozenset({"ping", "list_layers", "query", "zoom_to"})

# Change data, the map's layers, or write to disk -> blocked in read-only mode.
MUTATING = frozenset({"run_gp", "add_layer", "export_layout", "add_toolbox"})

# Subset of MUTATING that can irreversibly overwrite/delete data -> need confirm.
# ``run_gp`` runs an arbitrary geoprocessing tool (Delete, overwriting outputs,
# editing in place), so it is the one that demands an explicit opt-in.
DESTRUCTIVE = frozenset({"run_gp"})


def check(command: str, confirm: bool = False) -> dict | None:
    """Gate ``command`` against the active policy.

    Returns an ``err(...)`` envelope (with a machine-readable ``code``) when the
    command must be refused, or ``None`` when it may proceed. ``confirm`` is the
    caller's explicit opt-in for destructive commands; it is ignored for the
    rest.
    """
    if CONFIG.bridge_readonly and command in MUTATING:
        return err(
            f"'{command}' is blocked: the live bridge is in read-only mode "
            f"(ARCGIS_BRIDGE_READONLY is set). Unset it to allow changes to the "
            f"open ArcGIS Pro session.",
            code="readonly",
        )
    if command in DESTRUCTIVE and not confirm:
        return err(
            f"'{command}' can overwrite or delete data in the open ArcGIS Pro "
            f"session. Re-run with confirm=True to proceed.",
            code="confirm_required",
        )
    return None
