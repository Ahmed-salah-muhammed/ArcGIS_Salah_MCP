"""End-to-end checks that need a real ArcGIS Pro install (tests/test_core.py stays backend-free).

Run with ArcGIS Pro's own interpreter:

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" -m pytest tests/test_full_e2e.py -q

Three groups:
* the server over real stdio (a missing dependency of the MCP SDK once stopped it from starting at all, and
  nothing in test_core.py could see that),
* the pro_* ops with real arcpy on a throwaway file geodatabase, including the lock a second process holds,
* the live bridge, ONLY when ArcGIS Pro is open with the SalahAIBridge add-in AND ``ARCGIS_E2E_LIVE=1`` is set:
  these add a temporary layer (``mcp_e2e_sites``) to the open map, edit it and remove it again. They are skipped
  when the session already has unsaved edits, because they end by discarding the edit session.

Without arcpy every test here is skipped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

arcpy = pytest.importorskip("arcpy", reason="needs ArcGIS Pro's arcgispro-py3 interpreter")

from arcgis_pro_salah_mcp.live import ops as live  # noqa: E402
from arcgis_pro_salah_mcp.pro import ops as pro  # noqa: E402
from arcgis_pro_salah_mcp.pro import register  # noqa: E402

ROWS = [("Site A", "Open", 120.0, 31.24, 30.05), ("Site B", "Closed", 80.0, 31.25, 30.06),
        ("Site C", "Open", 200.0, 31.22, 30.04), ("Site D", "Pending", 50.0, 31.21, 30.07)]


def _make_fc(folder: Path, name: str) -> str:
    gdb = arcpy.management.CreateFileGDB(str(folder), "e2e.gdb")[0]
    fc = arcpy.management.CreateFeatureclass(gdb, name, "POINT", spatial_reference=4326)[0]
    for field, kind in (("NAME", "TEXT"), ("STATUS", "TEXT"), ("COST", "DOUBLE")):
        arcpy.management.AddField(fc, field, kind)
    with arcpy.da.InsertCursor(fc, ["NAME", "STATUS", "COST", "SHAPE@XY"]) as cursor:
        for name_, status, cost, x, y in ROWS:
            cursor.insertRow((name_, status, cost, (x, y)))
    return fc


@pytest.fixture(scope="module")
def fc(tmp_path_factory) -> str:
    return _make_fc(tmp_path_factory.mktemp("e2e"), "sites")


# --- the server over real stdio --------------------------------------------------------------------------

def test_server_starts_over_stdio_and_lists_its_tools():
    env = dict(os.environ, PYTHONPATH=SRC, ARCGIS_SALAH_NO_WARMUP="1", ARCGIS_SALAH_NO_PORTAL_WARMUP="1")
    proc = subprocess.Popen([sys.executable, "-m", "arcgis_pro_salah_mcp.server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8")

    def call(method: str, params: dict | None = None, request_id: int | None = None) -> dict | None:
        message: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if request_id is not None:
            message["id"] = request_id
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        if request_id is None:
            return None
        while True:
            line = proc.stdout.readline()
            if not line:
                raise AssertionError("the server exited: " + proc.stderr.read()[-800:])
            reply = json.loads(line)
            if reply.get("id") == request_id:
                return reply

    try:
        started = time.perf_counter()
        init = call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "e2e", "version": "1"}}, 1)
        assert init["result"]["serverInfo"]["name"]
        assert time.perf_counter() - started < 60
        call("notifications/initialized")
        names = {t["name"] for t in call("tools/list", request_id=2)["result"]["tools"]}
        assert {"pro_query", "pro_run_gp", "live_map", "live_layer", "live_edit", "portal_search"} <= names
        status = call("tools/call", {"name": "pro_server", "arguments": {"action": "status"}}, 3)
        assert status["result"]["isError"] is False
    finally:
        proc.kill()


# --- pro_* ops with real arcpy -----------------------------------------------------------------------------

def test_describe_read_and_statistics(fc):
    described = pro.describe_layer(fc)
    assert described["ok"], described
    rows = pro.get_features(fc, limit=10)
    assert rows["ok"] and rows["data"]["returned"] == len(ROWS)
    assert "SHAPE" not in {k.upper() for k in rows["data"]["rows"][0]}  # geometry is left out by default
    stats = pro.field_statistics(fc, "COST")
    assert stats["ok"] and stats["data"]["sum"] == pytest.approx(450.0)


def test_schema_edit_and_geoprocessing(fc, tmp_path):
    assert pro.add_field(fc, "CHECKED", "SHORT")["ok"]
    assert pro.calculate_field(fc, "CHECKED", "1")["ok"]
    assert "CHECKED" in [f.name for f in arcpy.ListFields(fc)]
    out = os.path.join(os.path.dirname(fc), "sites_buffer")
    assert pro.buffer(fc, out, "100 Meters")["ok"]
    assert int(arcpy.management.GetCount(out)[0]) == len(ROWS)
    generic = pro.run_gp("management.GetCount", [fc])
    assert generic["ok"] and generic["data"]["outputs"] == [str(len(ROWS))]


_HOLDER = r"""
import sys, time, arcpy
cursor = arcpy.da.SearchCursor(sys.argv[1], ["NAME"]); next(cursor)
print("holding", flush=True)
time.sleep(120)
"""


def test_a_dataset_another_process_holds_is_reported_as_locked(fc, tmp_path, monkeypatch):
    """A second process holds the feature class the way an open Pro map does; with no bridge to run the tool in,
    the op must say 'locked' and what to do, and reads must still work."""
    monkeypatch.setattr(register, "bridge_listening", lambda: False)
    script = tmp_path / "holder.py"
    script.write_text(_HOLDER, encoding="utf-8")
    holder = subprocess.Popen([sys.executable, str(script), fc], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "holding"
        locked = pro.add_field(fc, "WHILE_HELD", "TEXT")
        assert locked["ok"] is False and locked["code"] == "locked", locked
        assert "000464" in locked["error"] and "SalahAIBridge" in locked["hint"]
        assert pro.describe_layer(fc)["ok"]
    finally:
        holder.kill()
        holder.wait()
    assert pro.add_field(fc, "AFTER_RELEASE", "TEXT")["ok"]


# --- the live bridge: needs ArcGIS Pro open with the add-in, and ARCGIS_E2E_LIVE=1 --------------------------

LAYER = "mcp_e2e_sites"


def _live_ready() -> str | None:
    if os.environ.get("ARCGIS_E2E_LIVE", "").lower() not in ("1", "true", "yes", "on"):
        return "set ARCGIS_E2E_LIVE=1 to run the checks that change the open ArcGIS Pro session"
    if not register.bridge_listening():
        return "ArcGIS Pro is not open with the SalahAIBridge add-in"
    ping = live.live_ping()
    version = tuple(int(p) for p in str((ping.get("data") or {}).get("addin_version", "0")).split("."))
    if version < (0, 1, 7):
        return "the add-in is older than 0.1.7"
    if live.live_edit_session("status")["data"].get("has_unsaved_edits"):
        return "the open session has unsaved edits (these checks end by discarding the edit session)"
    return None


@pytest.fixture(scope="module")
def live_fc(tmp_path_factory):
    reason = _live_ready()
    if reason:
        pytest.skip(reason)
    path = _make_fc(tmp_path_factory.mktemp("e2e_live"), LAYER)
    added = live.live_add_layer(path)
    assert added["ok"], added
    yield path
    live.live_edit_session("discard", confirm=True)
    live.live_layer_set(LAYER, remove=True)


def test_live_layer_info_selection_and_style(live_fc):
    info = live.live_layer_info(LAYER)["data"]
    assert info["geometry"] == "Point" and info["count"] == len(ROWS)
    assert live.live_selection("select", LAYER, "STATUS = 'Open'")["data"]["selected"] == 2
    got = live.live_selection("get", LAYER, fields=["NAME"])["data"]
    assert got["fields"] == ["NAME"] and sorted(r["NAME"] for r in got["rows"]) == ["Site A", "Site C"]
    assert live.live_selection("clear", LAYER)["ok"]

    styled = live.live_layer_set(LAYER, definition_query="STATUS <> 'Closed'", labels={"expression": "$feature.NAME"},
                                 renderer={"type": "unique", "field": "STATUS"})["data"]
    assert styled["layer"]["count_shown"] == 3 and styled["layer"]["renderer"] == "UniqueValueRenderer"
    assert styled["layer"]["labels"] == {"visible": True, "expression": "$feature.NAME", "engine": "Arcade"}
    assert live.live_layer_set(LAYER, definition_query="")["data"]["layer"]["count_shown"] == len(ROWS)
    assert live.live_layer_set(LAYER, renderer={"type": "unique", "field": "NOPE"})["ok"] is False


def test_live_edits_go_through_pros_edit_session(live_fc):
    added = live.live_edit_features(LAYER, adds=[{"attributes": {"NAME": "Site E", "COST": 10.5},
                                                  "geometry": {"x": 31.23, "y": 30.02}}])["data"]
    new_id = added["added_object_ids"][0]
    assert added["added"] == 1 and added["has_unsaved_edits"] is True
    assert live.live_edit_features(LAYER, updates=[{"object_id": new_id, "attributes": {"STATUS": "Open"}}])["ok"]
    row = live.live_query(LAYER, f"OBJECTID = {new_id}", ["NAME", "STATUS", "COST"], 1)["data"]["rows"][0]
    assert row == {"NAME": "Site E", "STATUS": "Open", "COST": 10.5}
    assert live.live_edit_features(LAYER, deletes=[new_id])["code"] == "confirm_required"
    assert live.live_edit_features(LAYER, deletes=[new_id], confirm=True)["data"]["deleted"] == 1
    assert live.live_layer_info(LAYER)["data"]["count"] == len(ROWS)
    assert live.live_edit_session("undo")["ok"]  # the delete comes back
    assert live.live_layer_info(LAYER)["data"]["count"] == len(ROWS) + 1
    assert live.live_edit_features(LAYER, adds=[{"attributes": {"NOPE": 1}}])["ok"] is False


def test_a_layer_locked_by_pro_is_edited_inside_pro(live_fc):
    """The reason pro/locks.py exists: the dataset is a layer of the open map, so arcpy here is locked out."""
    # With unsaved edits on the layer even Pro cannot change its schema: the answer says so instead of "locked".
    assert live.live_edit_features(LAYER, adds=[{"attributes": {"NAME": "pending"}, "geometry": {"x": 31.2, "y": 30.0}}])["ok"]
    pending = pro.add_field(live_fc, "WHILE_EDITING", "TEXT")
    assert pending["ok"] is False and pending["code"] == "locked" and "save or discard" in pending["hint"], pending
    assert live.live_edit_session("discard", confirm=True)["data"]["has_unsaved_edits"] is False

    out = pro.add_field(live_fc, "FROM_OUTSIDE", "TEXT")
    assert out["ok"], out
    assert "open ArcGIS Pro session" in out["data"]["ran_in"]
    named = pro.run_gp("management.AddField", [live_fc], {"field_name": "NAMED", "field_type": "LONG"})
    assert named["ok"] and "ran_in" in named["data"], named
    fields = [f["name"] for f in live.live_layer_info(LAYER)["data"]["fields"]]
    assert {"FROM_OUTSIDE", "NAMED"} <= set(fields)
