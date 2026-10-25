"""Core tests that run WITHOUT any ArcGIS backend (no ArcPy, no arcgis pkg).

Run:  python -m pytest tests/  (with src on PYTHONPATH or package installed)
"""
import json
import os
import time

import pytest
from pathlib import Path

import arcgis_pro_salah_mcp as pkg
from arcgis_pro_salah_mcp._result import ok, err, guard
from arcgis_pro_salah_mcp.pro import metadata
from arcgis_pro_salah_mcp.webapp import generator
from arcgis_pro_salah_mcp.webapp import dashboard


def test_version():
    assert pkg.__version__


def test_result_envelopes():
    assert ok(123) == {"ok": True, "data": 123}
    e = err("boom", code="x")
    assert e["ok"] is False and e["error"] == "boom" and e["code"] == "x"


def test_guard_catches_exceptions():
    @guard
    def explode():
        raise ValueError("nope")

    out = explode()
    assert out["ok"] is False
    assert out["type"] == "ValueError"


def test_guard_wraps_plain_return():
    @guard
    def plain():
        return 42

    assert guard(plain)()  # callable
    assert plain() == {"ok": True, "data": 42}


def test_pro_ping_is_graceful_without_arcpy():
    # ArcPy isn't installed in CI -> guard should return an error envelope,
    # never raise.
    from arcgis_pro_salah_mcp.pro import ops as pro

    out = pro.ping()
    assert isinstance(out, dict) and "ok" in out


def test_live_ops_are_graceful_without_a_bridge(monkeypatch):
    # No ArcGIS Pro / add-in is running in CI. The live_* ops must return a
    # clean {"ok": false, "error": "bridge unreachable: ..."} envelope and never
    # raise. Point the client at a closed port so the connection is refused fast.
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import ops as live

    monkeypatch.setattr(CONFIG, "bridge_port", 9)  # discard port: nothing listens

    for call in (
        lambda: live.live_ping(),
        lambda: live.live_list_layers("Map"),
        lambda: live.live_zoom_to("cities"),
        lambda: live.live_query("cities", where="1=1", fields=["NAME"], limit=5),
        # confirm=True so the policy gate passes and we exercise the transport.
        lambda: live.live_run_gp("analysis.Buffer", args=["a", "b", "1 Kilometers"], confirm=True),
        lambda: live.live_add_layer("C:/data/x.shp"),
        lambda: live.live_export_layout("Layout", "C:/out/map.pdf", dpi=200),
    ):
        out = call()
        assert isinstance(out, dict) and out["ok"] is False
        assert "unreachable" in out["error"]


def test_live_client_post_returns_envelope_without_a_bridge(monkeypatch):
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import client

    monkeypatch.setattr(CONFIG, "bridge_port", 9)
    out = client.post("ping")
    assert out["ok"] is False and "error" in out


def test_live_readonly_mode_blocks_mutating_ops(monkeypatch):
    # In read-only mode the mutating live_* ops must refuse BEFORE any HTTP call,
    # returning a clean {"ok": false, "code": "readonly"} envelope. Point the
    # client at a dead port so a regression that lets the request through would
    # surface as "unreachable" instead of "readonly".
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import ops as live

    monkeypatch.setattr(CONFIG, "bridge_port", 9)
    monkeypatch.setattr(CONFIG, "bridge_readonly", True)

    for call in (
        lambda: live.live_run_gp("management.Delete", args=["x"], confirm=True),
        lambda: live.live_add_layer("C:/data/x.shp"),
        lambda: live.live_export_layout("Layout", "C:/out/map.pdf"),
    ):
        out = call()
        assert out["ok"] is False
        assert out["code"] == "readonly"
        assert "unreachable" not in out["error"]


def test_live_readonly_mode_still_allows_reads(monkeypatch):
    # Reads/navigation stay allowed in read-only mode, so they go to the bridge
    # and (with nothing listening) come back as "unreachable", not "readonly".
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import ops as live

    monkeypatch.setattr(CONFIG, "bridge_port", 9)
    monkeypatch.setattr(CONFIG, "bridge_readonly", True)

    for call in (
        lambda: live.live_ping(),
        lambda: live.live_list_layers("Map"),
        lambda: live.live_query("cities", where="1=1"),
        lambda: live.live_zoom_to("cities"),
    ):
        out = call()
        assert out["ok"] is False
        assert "unreachable" in out["error"]


def test_live_run_gp_requires_confirmation(monkeypatch):
    # run_gp is destructive: without confirm=True it must be refused locally,
    # even when NOT in read-only mode.
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import ops as live

    monkeypatch.setattr(CONFIG, "bridge_port", 9)
    monkeypatch.setattr(CONFIG, "bridge_readonly", False)

    out = live.live_run_gp("analysis.Buffer", args=["a", "b", "1 Kilometers"])
    assert out["ok"] is False and out["code"] == "confirm_required"

    # With confirm=True the gate passes and the call reaches the (dead) bridge.
    out = live.live_run_gp(
        "analysis.Buffer", args=["a", "b", "1 Kilometers"], confirm=True
    )
    assert out["ok"] is False and "unreachable" in out["error"]


def test_metadata_infers_known_themes():
    # Field names alone should reveal the domain, even with a vague layer name.
    roads = metadata.infer_theme("Layer1", ["RD_NAME", "highway_class", "speed_limit"])
    assert roads["theme"] == "transportation" and roads["score"] >= 2

    census = metadata.infer_theme(
        "tracts", ["TOTAL_POPULATION", "median_income", "household_count"]
    )
    assert census["theme"] == "demographics"

    # The layer name can carry the theme on its own.
    assert metadata.infer_theme("River_Basins", ["id", "label"])["theme"] == "hydrology"


def test_metadata_general_fallback_when_nothing_matches():
    out = metadata.infer_theme("mystery", ["foo", "bar", "baz"])
    assert out["theme"] == "general" and out["score"] == 0


def test_metadata_ignores_boring_fields_for_detection():
    # OBJECTID / Shape* must not drive detection.
    out = metadata.infer_theme("layer", ["OBJECTID", "Shape", "Shape_Area"])
    assert out["theme"] == "general"


def test_build_item_metadata_is_descriptive():
    meta = metadata.build_item_metadata(
        layer_name="city_roads",
        geometry_type="Polyline",
        field_names=["OBJECTID", "Shape_Length", "ROAD_NAME", "lanes", "speed_limit"],
        feature_count=1234,
        crs="WGS 1984",
    )
    assert "transportation" in meta["summary"].lower()
    assert "1,234" in meta["description"]
    assert "WGS 1984" in meta["description"]
    # Boring fields are not surfaced as key attributes.
    assert "OBJECTID" not in meta["description"]
    assert "ROAD_NAME" in meta["description"]
    assert "Salah MCP" in meta["tags"]
    # Tags are de-duplicated (case-insensitive).
    lowered = [t.lower() for t in meta["tags"]]
    assert len(lowered) == len(set(lowered))


def test_build_item_metadata_handles_unknown_layer():
    meta = metadata.build_item_metadata("misc", "Point", ["foo"], feature_count=3)
    assert meta["theme"] == "general"
    assert "feature layer" in meta["description"].lower()


def test_build_webmap_description_lists_layers():
    desc = metadata.build_webmap_description(
        [
            {"name": "Roads", "theme_label": "a transportation network"},
            {"name": "Rivers", "theme_label": "hydrography and water features"},
        ]
    )
    assert "Roads" in desc and "Rivers" in desc
    assert "2 layers" in desc


def test_webapp_generator_writes_description(tmp_path):
    out = generator.create_web_app(
        title="Described App",
        webmap_id="map123",
        out_dir=str(tmp_path / "desc"),
        description="A transportation network web map.",
    )
    assert out["ok"] is True
    config_js = (Path(out["data"]["output_dir"]) / "config.js").read_text(encoding="utf-8")
    assert "transportation network" in config_js


def test_webapp_generator_with_webmap(tmp_path):
    out = generator.create_web_app(
        title="Cairo Demo",
        webmap_id="abc123",
        out_dir=str(tmp_path / "app"),
        widgets=["legend", "home"],
    )
    assert out["ok"] is True
    app_dir = Path(out["data"]["output_dir"])
    for fname in ("index.html", "app.js", "config.js"):
        assert (app_dir / fname).exists()

    config_js = (app_dir / "config.js").read_text(encoding="utf-8")
    assert "abc123" in config_js
    index_html = (app_dir / "index.html").read_text(encoding="utf-8")
    assert "Cairo Demo" in index_html
    assert "__JS_SDK_VERSION__" not in index_html  # placeholder was replaced


def test_webapp_generator_requires_a_source(tmp_path):
    out = generator.create_web_app(title="x", out_dir=str(tmp_path / "a"))
    assert out["ok"] is False


def test_dashboard_generator_with_layers(tmp_path):
    out = dashboard.create_dashboard(
        title="Sales Dashboard",
        layer_item_ids=["lyr123"],
        out_dir=str(tmp_path / "dash"),
        category_field="region",
        value_fields=["revenue", "units"],
    )
    assert out["ok"] is True
    app_dir = Path(out["data"]["output_dir"])
    for fname in ("index.html", "dashboard.js", "config.js"):
        assert (app_dir / fname).exists()

    config_js = (app_dir / "config.js").read_text(encoding="utf-8")
    assert "lyr123" in config_js
    assert '"kind": "dashboard"' in config_js
    assert "region" in config_js and "revenue" in config_js

    index_html = (app_dir / "index.html").read_text(encoding="utf-8")
    assert "Sales Dashboard" in index_html
    assert "__JS_SDK_VERSION__" not in index_html  # placeholder replaced
    assert "arcgis-map" in index_html and "calcite-shell" in index_html


def test_dashboard_generator_requires_a_source(tmp_path):
    out = dashboard.create_dashboard(title="x", out_dir=str(tmp_path / "d"))
    assert out["ok"] is False


def test_dashboard_generator_rejects_bad_widget(tmp_path):
    out = dashboard.create_dashboard(
        title="x", webmap_id="id", out_dir=str(tmp_path / "e"), widgets=["bogus"]
    )
    assert out["ok"] is False


def test_webapp_generator_rejects_bad_widget(tmp_path):
    out = generator.create_web_app(
        title="x", webmap_id="id", out_dir=str(tmp_path / "b"), widgets=["nope"]
    )
    assert out["ok"] is False


# =====================================================================
# Schema cache — pure logic, no ArcPy
# =====================================================================


def test_cache_hits_for_geodatabase_dataset(tmp_path):
    from arcgis_pro_salah_mcp.pro import cache

    cache.invalidate_all()
    gdb = tmp_path / "data.gdb"
    gdb.mkdir()
    dataset = str(gdb / "roads")

    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"feature_count": calls["n"]}

    first = cache.get_or_load(dataset, loader)
    second = cache.get_or_load(dataset, loader)
    assert first == second
    assert calls["n"] == 1, "second call should be served from cache"

    # ArcGIS churns .lock files inside a .gdb on every READ, so the container
    # mtime must NOT be treated as a change signal — otherwise the cache never
    # hits in real use (measured: 0 hits across two identical pro_context calls).
    (gdb / "roads.lock").write_text("lock", encoding="utf-8")
    cache.get_or_load(dataset, loader)
    assert calls["n"] == 1, "a .gdb lock file must not invalidate the entry"

    assert cache.stamp(dataset) is None, "gdb datasets are deliberately unstamped"


def test_cache_stamps_plain_files(tmp_path):
    from arcgis_pro_salah_mcp.pro import cache

    cache.invalidate_all()
    shp = tmp_path / "roads.shp"
    shp.write_text("data", encoding="utf-8")
    dataset = str(shp)

    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache.get_or_load(dataset, loader)
    cache.get_or_load(dataset, loader)
    assert calls["n"] == 1

    # A plain file carries its own trustworthy mtime. Set it explicitly: NTFS
    # ticks at ~15 ms, so rewriting the file could land in the same tick.
    stat = os.stat(shp)
    os.utime(shp, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    cache.get_or_load(dataset, loader)
    assert calls["n"] == 2, "a changed file must invalidate the entry"


def test_cache_ttl_expires_entries(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import cache

    cache.invalidate_all()
    monkeypatch.setattr(cache, "TTL_SECONDS", 0.01)
    dataset = str(tmp_path / "d.gdb" / "t")

    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache.get_or_load(dataset, loader)
    time.sleep(0.05)
    cache.get_or_load(dataset, loader)
    assert calls["n"] == 2, "an entry older than the TTL must be reloaded"


def test_cache_can_be_disabled(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import cache

    cache.invalidate_all()
    monkeypatch.setattr(cache, "TTL_SECONDS", 0)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"n": calls["n"]}

    dataset = str(tmp_path / "d.gdb" / "t")
    cache.get_or_load(dataset, loader)
    cache.get_or_load(dataset, loader)
    assert calls["n"] == 2, "TTL_SECONDS=0 disables caching entirely"


def test_cache_explicit_invalidation(tmp_path):
    from arcgis_pro_salah_mcp.pro import cache

    cache.invalidate_all()
    dataset = str(tmp_path / "x.gdb" / "streets")
    cache.get_or_load(dataset, lambda: {"v": 1})
    assert cache.invalidate(dataset) == 1
    assert cache.invalidate(dataset) == 0


def test_cache_stats_shape():
    from arcgis_pro_salah_mcp.pro import cache

    cache.invalidate_all()
    stats = cache.stats()
    assert set(stats) == {"entries", "hits", "misses", "hit_rate", "ttl_seconds"}


# =====================================================================
# Pipeline placeholder substitution
# =====================================================================


def test_pipeline_substitutes_prev_and_step_refs():
    from arcgis_pro_salah_mcp.pro import pipeline

    outputs = ["C:/a.gdb/buf", "C:/a.gdb/clip"]
    assert pipeline._substitute("{{prev}}", outputs) == "C:/a.gdb/clip"
    assert pipeline._substitute("{{step1}}", outputs) == "C:/a.gdb/buf"
    assert pipeline._substitute(["{{step1}}", 5], outputs) == ["C:/a.gdb/buf", 5]
    assert pipeline._substitute({"k": "{{step2}}"}, outputs) == {"k": "C:/a.gdb/clip"}
    assert pipeline._substitute("no placeholder", outputs) == "no placeholder"


def test_pipeline_rejects_forward_reference():
    from arcgis_pro_salah_mcp.pro import pipeline

    with pytest.raises(ValueError):
        pipeline._substitute("{{step3}}", ["only-one"])
    with pytest.raises(ValueError):
        pipeline._substitute("{{prev}}", [])


def test_pipeline_rejects_empty_and_malformed():
    from arcgis_pro_salah_mcp.pro import pipeline

    assert pipeline.pipeline([])["ok"] is False
    assert pipeline.pipeline(["not-a-dict"])["ok"] is False


def test_job_status_unknown_id():
    from arcgis_pro_salah_mcp.pro import pipeline

    result = pipeline.job_status("does-not-exist")
    assert result["ok"] is False
    assert result["code"] == "unknown_job"


# =====================================================================
# Pure lookup tools (must work with no ArcGIS installed)
# =====================================================================


def test_topology_rules_lookup():
    from arcgis_pro_salah_mcp.pro import validate

    everything = validate.describe_topology_rules()
    assert everything["ok"] is True
    assert everything["data"]["total"] == 31

    area = validate.describe_topology_rules("area")
    assert "Must Not Overlap (Area)" in area["data"]["rules"]
    assert validate.describe_topology_rules("nonsense")["ok"] is False


def test_geoai_model_types_lookup():
    from arcgis_pro_salah_mcp.pro import geoai

    detection = geoai.list_model_types("object_detection")
    assert detection["ok"] is True
    assert "FASTERRCNN" in detection["data"]["model_types"]
    assert "KITTI_rectangles" in detection["data"]["training_formats"]
    assert geoai.list_model_types("not_a_task")["ok"] is False


def test_utility_network_trace_types_lookup():
    from arcgis_pro_salah_mcp.pro import unet

    result = unet.list_trace_types()
    assert result["ok"] is True
    assert len(result["data"]["trace_types"]) == 10
    assert "ISOLATION" in result["data"]["trace_types"]


# =====================================================================
# Validation happens BEFORE the backend is touched, so a bad call fails
# instantly and stays testable with no ArcGIS present.
# =====================================================================


def test_attribute_rule_validation_without_arcpy():
    from arcgis_pro_salah_mcp.pro import schema

    bad_type = schema.add_attribute_rule("t", "n", "NONSENSE", "return 1;")
    assert bad_type["ok"] is False and "rule_type" in bad_type["error"]

    no_field = schema.add_attribute_rule("t", "n", "CALCULATION", "return 1;")
    assert no_field["code"] == "missing_field"

    field_on_constraint = schema.add_attribute_rule(
        "t", "n", "CONSTRAINT", "return true;", field="X"
    )
    assert field_on_constraint["code"] == "unexpected_field"

    no_triggers = schema.add_attribute_rule(
        "t", "n", "CALCULATION", "return 1;", field="X"
    )
    assert no_triggers["code"] == "missing_triggers"

    validation_with_triggers = schema.add_attribute_rule(
        "t", "n", "VALIDATION", "return true;", triggering_events=["INSERT"]
    )
    assert validation_with_triggers["code"] == "unexpected_triggers"

    no_error_info = schema.add_attribute_rule(
        "t", "n", "CONSTRAINT", "return true;", triggering_events=["INSERT"]
    )
    assert no_error_info["code"] == "missing_error"


def test_trace_validation_without_arcpy():
    from arcgis_pro_salah_mcp.pro import unet

    assert unet.trace("un", "BOGUS")["ok"] is False
    assert unet.trace("un", "SHORTEST_PATH")["code"] == "missing_attribute"
    assert unet.trace("un", "SUBNETWORK")["code"] == "missing_tier"


def test_geoai_threshold_validation_without_arcpy():
    from arcgis_pro_salah_mcp.pro import geoai

    assert geoai.find_similar_features("a", "b", "c", threshold=5.0)["ok"] is False
    assert geoai.detect_objects_by_text("r", "cat", box_threshold=0)["ok"] is False


def test_create_topology_rejects_unknown_rule():
    from arcgis_pro_salah_mcp.pro import validate

    result = validate.create_topology("fd", "topo", ["A"], [{"rule_type": "Nope"}])
    assert result["code"] == "unknown_rule"
    assert result["unknown"] == ["Nope"]


# =====================================================================
# Python toolbox (.pyt) generator — pure, no ArcPy
# =====================================================================


def _simple_tool():
    return {
        "name": "BufferRoads",
        "label": "Buffer Roads",
        "parameters": [
            {"name": "roads", "label": "Roads", "datatype": "feature_class"},
            {
                "name": "distance",
                "label": "Distance",
                "datatype": "linear_unit",
                "default": "100 Meters",
            },
            {
                "name": "output",
                "label": "Output",
                "datatype": "feature_class",
                "direction": "Output",
            },
        ],
        "steps": [
            {
                "tool": "analysis.Buffer",
                "args": ["{{roads}}", "{{output}}", "{{distance}}"],
            }
        ],
    }


def test_pyt_generator_writes_valid_python(tmp_path):
    from arcgis_pro_salah_mcp.pro import toolbox

    out = tmp_path / "Demo.pyt"
    result = toolbox.create_python_toolbox(str(out), "Demo Tools", [_simple_tool()])
    assert result["ok"] is True
    assert result["data"]["tool_count"] == 1

    source = out.read_text(encoding="utf-8")
    # The generator guarantees syntactically valid Python; prove it independently.
    compile(source, str(out), "exec")
    assert "class Toolbox(object):" in source
    assert "class BufferRoads(object):" in source
    assert "self.tools = [BufferRoads]" in source
    assert "arcpy.analysis.Buffer(roads, output, distance)" in source
    assert 'param.value = ' in source


def test_pyt_generator_adds_extension_check(tmp_path):
    from arcgis_pro_salah_mcp.pro import toolbox

    tool = _simple_tool()
    tool["required_extensions"] = ["Spatial"]
    out = tmp_path / "Ext.pyt"
    assert toolbox.create_python_toolbox(str(out), "Ext", [tool])["ok"] is True
    source = out.read_text(encoding="utf-8")
    assert "arcpy.CheckExtension('Spatial')" in source
    assert "def isLicensed(self):" in source


def test_pyt_generator_rejects_bad_input(tmp_path):
    from arcgis_pro_salah_mcp.pro import toolbox

    assert toolbox.create_python_toolbox(str(tmp_path / "a.pyt"), "A", [])["ok"] is False

    bad_type = _simple_tool()
    bad_type["parameters"][0]["datatype"] = "not_a_real_type"
    result = toolbox.create_python_toolbox(str(tmp_path / "b.pyt"), "B", [bad_type])
    assert result["code"] == "bad_parameter"

    derived_input = _simple_tool()
    derived_input["parameters"][0]["parameter_type"] = "Derived"
    result = toolbox.create_python_toolbox(str(tmp_path / "c.pyt"), "C", [derived_input])
    assert result["code"] == "bad_parameter"

    duplicate = _simple_tool()
    duplicate["parameters"].append(duplicate["parameters"][0])
    result = toolbox.create_python_toolbox(str(tmp_path / "d.pyt"), "D", [duplicate])
    assert result["code"] == "bad_parameter"


def test_pyt_generator_catches_broken_execute_code(tmp_path):
    from arcgis_pro_salah_mcp.pro import toolbox

    tool = _simple_tool()
    tool.pop("steps")
    tool["execute_code"] = "if True\n    pass"  # missing colon
    result = toolbox.create_python_toolbox(str(tmp_path / "e.pyt"), "E", [tool])
    assert result["code"] == "invalid_source"


def test_pyt_datatype_lookup():
    from arcgis_pro_salah_mcp.pro import toolbox

    result = toolbox.list_datatypes()
    assert result["ok"] is True
    assert result["data"]["aliases"]["feature_class"] == "DEFeatureClass"


# =====================================================================
# ModelBuilder (.atbx) generator — pure, no ArcPy
# =====================================================================


def _chained_model_args(out_path):
    return dict(
        out_path=str(out_path),
        toolbox_label="Salah Models",
        model_name="BufferAndDissolve",
        steps=[
            {
                "tool": "analysis.Buffer",
                "label": "Buffer roads",
                "params": {
                    "in_features": "{{roads}}",
                    "buffer_distance_or_field": "{{distance}}",
                },
                "output_param": "out_feature_class",
            },
            {
                "tool": "management.Dissolve",
                "label": "Dissolve",
                "params": {"in_features": "{{step1}}"},
                "output_param": "out_feature_class",
                "output": "{{result}}",
            },
        ],
        parameters=[
            {"name": "roads", "label": "Roads", "datatype": "feature_class"},
            {
                "name": "distance",
                "label": "Distance",
                "datatype": "linear_unit",
                "default": "100 Meters",
            },
            {
                "name": "result",
                "label": "Result",
                "datatype": "feature_class",
                "direction": "Output",
            },
        ],
    )


def test_atbx_generator_structure_and_chaining(tmp_path):
    import zipfile

    from arcgis_pro_salah_mcp.pro import modelbuilder

    out = tmp_path / "Models.atbx"
    result = modelbuilder.create_model(**_chained_model_args(out))
    assert result["ok"] is True
    assert result["data"]["processes"] == 2

    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "toolbox.content" in names
        assert "toolbox.content.rc" in names
        assert "BufferAndDissolve.tool/tool.content" in names
        assert "BufferAndDissolve.tool/tool.model" in names
        model = json.loads(z.read("BufferAndDissolve.tool/tool.model"))
        content = json.loads(z.read("BufferAndDissolve.tool/tool.content"))

    assert content["type"] == "ModelTool"
    assert set(content["params"]) == {"roads", "distance", "result"}

    buffer_proc, dissolve_proc = model["processes"]
    assert buffer_proc["system_tool"] == "analysis.Buffer"
    assert dissolve_proc["system_tool"] == "management.Dissolve"

    # The chain: Buffer's output element is exactly Dissolve's input element.
    buffered_id = buffer_proc["params"]["out_feature_class"]["element_id"]
    assert dissolve_proc["params"]["in_features"]["element_id"] == buffered_id

    # ArcGIS rejects the model unless the intermediate declares BOTH a datatype
    # (else "the value is not a Feature Class") and a value (else "value is
    # required"). Both were established by running generated models in Pro 3.x.
    intermediate = next(v for v in model["variables"] if v["id"] == buffered_id)
    assert intermediate["datatype"]["type"] == "DEFeatureClass"
    assert intermediate["value"].startswith("%scratchgdb%")


def test_atbx_inspect_round_trip(tmp_path):
    from arcgis_pro_salah_mcp.pro import modelbuilder

    out = tmp_path / "Models.atbx"
    modelbuilder.create_model(**_chained_model_args(out))

    result = modelbuilder.inspect_model(str(out))
    assert result["ok"] is True
    tool = result["data"]["tools"][0]
    assert tool["name"] == "BufferAndDissolve"
    assert tool["type"] == "ModelTool"
    assert [p["tool"] for p in tool["processes"]] == [
        "analysis.Buffer",
        "management.Dissolve",
    ]
    # Resource keys must resolve back to human labels.
    assert tool["processes"][0]["title"] == "Buffer roads"


def test_atbx_generator_rejects_bad_input(tmp_path):
    from arcgis_pro_salah_mcp.pro import modelbuilder

    assert modelbuilder.create_model(str(tmp_path / "a.atbx"), "A", "M", [])["ok"] is False

    undotted = modelbuilder.create_model(
        str(tmp_path / "b.atbx"), "B", "M", [{"tool": "Buffer"}]
    )
    assert undotted["code"] == "bad_model"

    forward_ref = modelbuilder.create_model(
        str(tmp_path / "c.atbx"),
        "C",
        "M",
        [{"tool": "analysis.Buffer", "params": {"in_features": "{{step9}}"}}],
    )
    assert forward_ref["code"] == "bad_model"

    unknown_ref = modelbuilder.create_model(
        str(tmp_path / "d.atbx"),
        "D",
        "M",
        [{"tool": "analysis.Buffer", "params": {"in_features": "{{nope}}"}}],
    )
    assert unknown_ref["code"] == "bad_model"


def test_atbx_inspect_rejects_non_zip(tmp_path):
    from arcgis_pro_salah_mcp.pro import modelbuilder

    legacy = tmp_path / "old.tbx"
    legacy.write_bytes(b"not a zip")
    result = modelbuilder.inspect_model(str(legacy))
    assert result["code"] == "not_atbx"
    assert modelbuilder.inspect_model(str(tmp_path / "missing.atbx"))["code"] == "not_found"


# =====================================================================
# Server wiring: warm-up and toolset gating
# =====================================================================


def test_warmup_status_never_blocks():
    from arcgis_pro_salah_mcp import bootstrap

    status = bootstrap.warmup_status()
    assert set(status) == {"state", "seconds", "error"}
    assert status["state"] in {"idle", "importing", "ready", "failed", "skipped"}


def test_running_under_arcgis_python_is_a_cheap_path_check():
    from arcgis_pro_salah_mcp import bootstrap

    # Must never import arcpy (that costs ~25 s) — just a path inspection.
    assert isinstance(bootstrap.running_under_arcgis_python(), bool)


def test_toolset_filter_prunes_groups():
    import importlib

    from arcgis_pro_salah_mcp import server

    # Reload so a previous test's filtering cannot leak into this one.
    server = importlib.reload(server)
    total = len(server.mcp._tool_manager._tools)

    result = server.apply_toolset_filter("core,portal")
    assert result["filtered"] is True
    remaining = set(server.mcp._tool_manager._tools)
    assert len(remaining) < total
    assert not any(n.startswith("pro_geoai_") for n in remaining)
    assert not any(n.startswith("pro_un_") for n in remaining)
    assert any(n.startswith("portal_") for n in remaining)
    assert "pro_context" in remaining, "core tools must always survive"

    importlib.reload(server)


def test_toolset_membership_classification():
    from arcgis_pro_salah_mcp import server

    assert server._toolset_of("pro_geoai_detect_by_text") == "geoai"
    assert server._toolset_of("pro_un_trace") == "unet"
    assert server._toolset_of("pro_create_model") == "authoring"
    assert server._toolset_of("pro_apply_schema") == "schema"
    assert server._toolset_of("pro_validate_all") == "validate"
    assert server._toolset_of("pro_context") == "core"
    assert server._toolset_of("pro_buffer") == "core"


# =====================================================================
# Deep-learning notebook authoring (pure — no arcgis.learn needed)
# =====================================================================

from arcgis_pro_salah_mcp.pro import dl_notebooks as _dl_module

# Captured at import time, before any test patches them, so the real ones are testable.
_REAL_PROJECT_FOLDER = _dl_module._project_folder
_REAL_BRIDGE_LISTENING = _dl_module._bridge_listening


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch, request):
    """A test must never reach a real ArcGIS Pro bridge.

    With the add-in running, an unguarded test that creates a notebook would REGISTER a
    temp file in the developer's real open project. Scoped by name so the older tests that
    exercise the real bridge client (against an unused port) keep doing so.
    """
    monkeypatch.setattr(_dl_module, "_project_folder", lambda: None)
    monkeypatch.setattr(_dl_module, "_bridge_listening", lambda: False)
    from arcgis_pro_salah_mcp.pro import register as _register_module

    monkeypatch.setattr(_register_module, "bridge_listening", lambda: False)
    if request.node.name.startswith(("test_dl_", "test_register_toolbox", "test_generators_register")):
        from arcgis_pro_salah_mcp.live import client

        monkeypatch.setattr(client, "post", lambda *a, **k: {
            "ok": False, "error": "bridge unreachable (blocked in tests)"})


def _nb_code(path) -> str:
    """All code-cell source of a notebook, joined, in order."""
    nb = json.loads(Path(path).read_text(encoding="utf-8"))
    return "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")


def test_dl_plan_recommends_and_lists_questions():
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    plan = dl.plan("detect buildings")["data"]
    assert plan["mode"] == "train"
    assert plan["recommended"] == "maskrcnn"
    assert plan["next_tool"] == "pro_dl_notebook"
    required = {q["key"] for q in plan["questions"] if q["required"]}
    assert required == {"training_data", "batch_size", "epochs"}
    keys = {q["key"] for q in plan["questions"]}
    assert {"learning_rate", "chip_size", "backbone", "pointrend",
            "mixed_precision", "early_stopping"} <= keys
    assert "ONE message" in plan["how_to_ask"]


def test_dl_plan_picks_a_lighter_model_for_boxes_only():
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    assert dl.plan("just count the buildings with bounding boxes")["data"]["recommended"] == "fasterrcnn"
    assert dl.plan("x", prefer="unet")["data"]["recommended"] == "unet"


def test_dl_questions_only_cover_options_the_model_has():
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    def keys(model):
        return {q["key"] for q in dl.plan("x", prefer=model)["data"]["questions"]}

    assert "pointrend" in keys("maskrcnn") and "pointrend" not in keys("unet")
    assert "backbone" not in keys("yolov3"), "YOLOv3 has no backbone argument"
    assert {"oversample", "mixup", "input_features"} <= keys("featureclassifier")


def test_dl_routing_understands_classification():
    """Regression: every classification phrasing used to come back as Mask R-CNN."""
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    def model_for(goal):
        data = dl.plan(goal)["data"]
        return data["recommended"] if data["mode"] == "train" else data["mode"]

    assert model_for("a model that classifies objects") == "featureclassifier"
    assert model_for("classify each building by roof type") == "featureclassifier"
    assert model_for("تصنيف الاوبجيكت classification") == "featureclassifier"
    assert model_for("land cover classification") == "unet"
    assert model_for("classify the pixels") == "unet"
    assert model_for("detect buildings") == "maskrcnn"
    # Plain "classification" is genuinely ambiguous: it must ask, not guess.
    assert model_for("classification") == "choose"
    assert model_for("detect and classify buildings") == "choose"

    choice = dl.plan("classification")["data"]
    assert {c["key"] for c in choice["choices"]} == {"pixel", "object", "detect"}
    assert {c["recommended"] for c in choice["choices"]} == {"unet", "featureclassifier", "maskrcnn"}


def test_dl_create_refuses_until_the_interview_is_answered(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "n.ipynb"
    none = dl.create_training_notebook("maskrcnn", str(out))
    assert none["ok"] is False and none["code"] == "needs_answers"
    assert none["missing"] == ["training_data", "batch_size", "epochs"]
    assert not out.exists(), "nothing may be written before the answers exist"

    partial = dl.create_training_notebook(
        "maskrcnn", str(out), training_data=r"F:\x", batch_size=4)
    assert partial["missing"] == ["epochs"]
    assert [q["key"] for q in partial["questions"]] == ["epochs"]


def test_dl_notebook_follows_the_course_notebook_steps(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "b.ipynb"
    r = dl.create_training_notebook(
        "maskrcnn", str(out), training_data=r"F:\AI_course\AIcourse\building",
        batch_size=4, epochs=30, chip_size=400, options={"pointrend": True},
        model_name="buildings_30epochs_v1")
    assert r["ok"] is True and r["data"]["status"] == "created"

    nb = json.loads(out.read_text(encoding="utf-8"))
    # Same container Pro itself writes.
    assert (nb["nbformat"], nb["nbformat_minor"]) == (4, 4)
    assert nb["metadata"]["kernelspec"]["name"] == "python3"
    assert nb["metadata"]["kernelspec"]["display_name"] == "ArcGISPro"

    code = _nb_code(out)
    steps = [
        "from arcgis.learn import MaskRCNN, prepare_data",
        "prepare_data(TRAINING_DATA",
        "data.classes",
        "data.show_batch()",
        "model = MaskRCNN(data, pointrend=POINTREND)",
        "model.supported_datasets",
        "model.lr_find()",
        "model.fit(EPOCHS",
        "model.plot_losses()",
        "model.average_precision_score()",
        "model.show_results()",
        "model.save(MODEL_NAME)",
    ]
    # Search forward from the previous hit: a comment in the parameters cell also
    # mentions model.lr_find(), which must not count as the real step.
    pos = 0
    for step in steps:
        found = code.find(step, pos)
        assert found != -1, f"missing or out of order: {step!r}"
        pos = found + len(step)

    assert r'TRAINING_DATA = r"F:\AI_course\AIcourse\building"' in code
    assert "BATCH_SIZE     = 4" in code and "EPOCHS         = 30" in code
    assert "CHIP_SIZE      = 400" in code and "POINTREND      = True" in code


def test_dl_every_cell_is_runnable_one_at_a_time(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    r = dl.create_training_notebook(
        "unet", str(tmp_path / "u.ipynb"), training_data=r"F:\x", batch_size=8, epochs=3)
    nb = json.loads((tmp_path / "u.ipynb").read_text(encoding="utf-8"))
    assert r["data"]["cells"] == len(nb["cells"])
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] == "code" and i and nb["cells"][i - 1]["cell_type"] != "markdown":
            # the only allowed exception is the second code cell of the inference step
            assert "".join(cell["source"]).startswith(("OUT_", "arcpy.ia"))
    assert all(c["outputs"] == [] and c["execution_count"] is None
               for c in nb["cells"] if c["cell_type"] == "code")


def test_dl_metric_cell_depends_on_what_the_model_really_has(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    def code_for(model):
        p = tmp_path / f"{model}.ipynb"
        assert dl.create_training_notebook(
            model, str(p), training_data=r"F:\x", batch_size=2, epochs=1)["ok"]
        return _nb_code(p)

    assert "model.average_precision_score()" in code_for("fasterrcnn")
    unet = code_for("unet")
    assert "model.accuracy()" in unet and "model.mIOU()" in unet
    assert "average_precision_score" not in unet
    # FeatureClassifier has plot_confusion_matrix but neither accuracy nor AP in arcgis.learn.
    fc = code_for("featureclassifier")
    assert "model.plot_confusion_matrix()" in fc
    assert "model.accuracy()" not in fc and "average_precision_score" not in fc
    # arcgis.learn gives SamLoRA no metric method, so none may be called.
    sam = code_for("samlora")
    assert "model.accuracy()" not in sam and "average_precision_score" not in sam
    assert "no accuracy" in sam


def test_dl_inference_cell_matches_the_task(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    def code_for(model, **extra):
        p = tmp_path / f"{model}.ipynb"
        dl.create_training_notebook(model, str(p), training_data=r"F:\x", batch_size=2,
                                    epochs=1, **extra)
        return _nb_code(p)

    assert "DetectObjectsUsingDeepLearning" in code_for("maskrcnn")
    assert "DetectObjectsUsingDeepLearning" in code_for("retinanet")
    assert "ClassifyPixelsUsingDeepLearning" in code_for("unet")

    fc = code_for("featureclassifier", input_features=r"F:\roofs.gdb\buildings")
    assert "ClassifyObjectsUsingDeepLearning" in fc
    assert r'INPUT_FEATURES = r"F:\roofs.gdb\buildings"' in fc
    assert "in_features=INPUT_FEATURES" in fc

    # ArcGISProject('CURRENT') raises OSError outside Pro (observed when the notebook
    # was executed headless), so the output location must fall back instead of dying.
    code = code_for("maskrcnn")
    assert "except OSError" in code and "arcpy.env.scratchGDB" in code
    assert 'f"{DEFAULT_GDB}' in code
    assert "ArcGISProject('CURRENT').defaultGeodatabase}" not in code


def test_dl_rejects_options_the_model_does_not_have(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    base = dict(training_data=r"F:\x", batch_size=2, epochs=1)
    out = str(tmp_path / "x.ipynb")
    assert dl.create_training_notebook("unet", out, options={"pointrend": True}, **base)["code"] == "unsupported_option"
    assert dl.create_training_notebook("yolov3", out, options={"backbone": "resnet50"}, **base)["code"] == "unsupported_option"
    assert dl.create_training_notebook("unet", out, options={"backbone": ["a"]}, **base)["code"] == "bad_value"
    assert dl.create_training_notebook("not_a_model", out, **base)["code"] == "unknown_model"
    assert dl.create_training_notebook("unet", out, **{**base, "epochs": 0})["code"] == "bad_value"
    assert dl.create_training_notebook("unet", out, learning_rate=-1, **base)["code"] == "bad_value"
    assert dl.create_training_notebook("unet", out, val_split_pct=1.5, **base)["code"] == "bad_value"
    assert not (tmp_path / "x.ipynb").exists()


def test_dl_all_registered_models_generate_valid_python(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    for key in dl.MODELS:
        r = dl.create_training_notebook(
            key, str(tmp_path / f"{key}.ipynb"),
            training_data=r"F:\some folder\chips", batch_size=2, epochs=1)
        assert r["ok"] is True, (key, r)
        compile(_nb_code(tmp_path / f"{key}.ipynb"), key, "exec")


def test_dl_model_arguments_are_only_what_the_library_accepts(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    # Registry facts read from the installed arcgis.learn with inspect.signature.
    assert dl.MODELS["maskrcnn"]["formats"] == ["RCNN_Masks"]
    assert dl.MODELS["unet"]["formats"] == ["Classified_Tiles"]
    assert dl.MODELS["featureclassifier"]["formats"] == ["Labeled_Tiles", "MultiLabeled_Tiles", "Imagenet"]
    assert "pointrend" in dl.MODELS["maskrcnn"]["init_args"]
    assert "pointrend" not in dl.MODELS["unet"]["init_args"]
    assert {"mixup", "oversample"} <= set(dl.MODELS["featureclassifier"]["init_args"])
    assert "backbone" not in dl.MODELS["yolov3"]["init_args"]

    p = tmp_path / "m.ipynb"
    dl.create_training_notebook("featureclassifier", str(p), training_data=r"F:\x", batch_size=2,
                                epochs=1, options={"backbone": "resnet34", "oversample": True})
    code = _nb_code(p)
    assert "FeatureClassifier(data, backbone=BACKBONE, oversample=OVERSAMPLE)" in code
    assert "'resnet34'" in code and "OVERSAMPLE" in code


def test_dl_overwrite_is_explicit(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    args = dict(training_data=r"F:\x", batch_size=2, epochs=1)
    out = str(tmp_path / "o.ipynb")
    assert dl.create_training_notebook("unet", out, **args)["ok"] is True
    assert dl.create_training_notebook("unet", out, **args)["code"] == "exists"
    assert dl.create_training_notebook("unet", out, overwrite=True, **args)["ok"] is True


def test_dl_data_path_can_be_left_for_the_user(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "later.ipynb"
    r = dl.create_training_notebook(
        "maskrcnn", str(out), batch_size=4, epochs=30, data_path_later=True)
    assert r["ok"] is True and r["data"]["data_path_placeholder"] is True

    code = _nb_code(out)
    assert dl._DATA_PLACEHOLDER in code
    # Until it is replaced, loading must stop with a message that says what to do.
    assert "os.path.isdir(TRAINING_DATA)" in code
    assert "Set TRAINING_DATA in the Parameters cell first" in code
    compile(code, "later", "exec")


def test_dl_data_path_later_does_not_waive_the_other_answers(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "x.ipynb"
    r = dl.create_training_notebook("maskrcnn", str(out), data_path_later=True)
    assert r["code"] == "needs_answers"
    assert r["missing"] == ["batch_size", "epochs"], "only the path is waived"
    assert not out.exists()


def test_dl_real_path_wins_over_the_placeholder_flag(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "real.ipynb"
    r = dl.create_training_notebook(
        "unet", str(out), training_data=r"F:\chips", batch_size=2, epochs=1,
        data_path_later=True)
    assert r["data"]["data_path_placeholder"] is False
    assert dl._DATA_PLACEHOLDER not in _nb_code(out)
    assert r"F:\chips" in _nb_code(out)


def test_dl_models_lookup():
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    everything = dl.list_models()["data"]
    assert {m["key"] for m in everything["models"]} == set(dl.MODELS)
    assert "groundingdino" not in json.dumps(everything).lower()
    assert [m["key"] for m in dl.list_models("object_classification")["data"]["models"]] == ["featureclassifier"]
    assert dl.list_models("nonsense")["ok"] is False


def test_dl_is_exposed_as_a_single_tool():
    """Every tool schema is sent on every request, so the feature must stay ONE tool."""
    import asyncio

    from arcgis_pro_salah_mcp import server

    names = [t.name for t in asyncio.run(server.mcp.list_tools()) if t.name.startswith("pro_dl_")]
    assert names == ["pro_dl_notebook"]
    assert server._toolset_of("pro_dl_notebook") == "geoai"


def test_dl_entry_point_asks_then_creates(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = str(tmp_path / "flow.ipynb")

    # 1) Only a goal: recommend + ask. Nothing is written.
    first = dl.notebook("detect buildings", out_path=out)["data"]
    assert first["status"] == "ask_user"
    assert first["model"] == "maskrcnn"
    assert first["missing"] == ["training_data", "batch_size", "epochs"]
    assert not (tmp_path / "flow.ipynb").exists()

    # 2) Partial answers: only the unanswered questions come back.
    second = dl.notebook("detect buildings", out_path=out, batch_size=4)["data"]
    assert second["missing"] == ["training_data", "epochs"]
    assert "batch_size" not in {q["key"] for q in second["questions"]}

    # 3) All answers: created.
    third = dl.notebook("detect buildings", out_path=out, training_data=r"F:\x",
                        batch_size=4, epochs=30, options={"pointrend": True})
    assert third["ok"] is True and third["data"]["status"] == "created"
    assert (tmp_path / "flow.ipynb").exists()


def test_dl_entry_point_routes_by_what_the_user_means(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    ambiguous = dl.notebook("classification", out_path=str(tmp_path / "a.ipynb"))["data"]
    assert ambiguous["status"] == "choose"
    assert {c["key"] for c in ambiguous["choices"]} == {"pixel", "object", "detect"}

    answers = dict(training_data=r"F:\x", batch_size=8, epochs=5)
    made = dl.notebook("a model that classifies objects", out_path=str(tmp_path / "c.ipynb"), **answers)
    assert made["data"]["status"] == "created" and made["data"]["model"] == "FeatureClassifier"

    # Choosing explicitly skips the routing.
    chosen = dl.notebook("classification", model="unet", out_path=str(tmp_path / "u.ipynb"), **answers)
    assert chosen["data"]["model"] == "UnetClassifier"
    assert dl.notebook("x", model="nope")["code"] == "unknown_model"


def test_dl_entry_point_uses_the_open_project_folder(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    answers = dict(training_data=r"F:\x", batch_size=4, epochs=1)

    monkeypatch.setattr(dl, "_bridge_listening", lambda: True)
    calls = []
    monkeypatch.setattr(dl, "_project_folder", lambda: calls.append(1) or str(tmp_path))

    # While still interviewing, the (slow) project lookup must NOT run at all.
    first = dl.notebook("detect buildings")["data"]
    assert first["status"] == "ask_user" and "out_path" not in first["missing"]
    assert calls == [], "the project lookup must wait until we are about to write"

    r = dl.notebook("detect buildings", **answers)["data"]
    assert r["status"] == "created" and calls == [1]
    assert (tmp_path / "Mask_R_CNN_training.ipynb").exists()

    # Bridge not listening: ask for the path up-front, together with the other questions.
    monkeypatch.setattr(dl, "_bridge_listening", lambda: False)
    down = dl.notebook("detect buildings")["data"]
    assert down["missing"] == ["training_data", "batch_size", "epochs", "out_path"]
    assert down["questions"][0]["key"] == "out_path"

    # Listening, but the lookup fails when we go to write: still ask, never guess a folder.
    monkeypatch.setattr(dl, "_bridge_listening", lambda: True)
    monkeypatch.setattr(dl, "_project_folder", lambda: None)
    late = dl.notebook("detect buildings", **answers)["data"]
    assert late["status"] == "ask_user" and late["missing"] == ["out_path"]


def test_dl_bridge_probe_is_a_fast_socket_check(monkeypatch):
    import socket
    import time

    from arcgis_pro_salah_mcp.config import CONFIG

    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    monkeypatch.setattr(CONFIG, "bridge_port", server_socket.getsockname()[1])
    assert _REAL_BRIDGE_LISTENING() is True
    server_socket.close()

    started = time.perf_counter()
    assert _REAL_BRIDGE_LISTENING() is False        # nothing listening any more
    assert (time.perf_counter() - started) < 0.5, "a refused connection must be near-instant"


def test_dl_project_folder_lookup_never_raises(monkeypatch):
    from arcgis_pro_salah_mcp.live import client

    monkeypatch.setattr(client, "post", lambda *a, **k: {
        "ok": True, "data": {"project_path": r"C:\Projects\geo\geo.aprx"}})
    assert _REAL_PROJECT_FOLDER() == r"C:\Projects\geo"

    monkeypatch.setattr(client, "post", lambda *a, **k: {"ok": False, "error": "bridge unreachable"})
    assert _REAL_PROJECT_FOLDER() is None

    def boom(*a, **k):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(client, "post", boom)
    assert _REAL_PROJECT_FOLDER() is None


def test_dl_creation_stays_fast(tmp_path):
    """Measured ~5 ms for 32 cells (compile + write). A big regression would be a real bug."""
    import time

    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    n = 5
    started = time.perf_counter()
    for i in range(n):
        assert dl.create_training_notebook(
            "maskrcnn", str(tmp_path / f"s{i}.ipynb"), training_data=r"F:\x",
            batch_size=4, epochs=30)["ok"]
    per_call_ms = (time.perf_counter() - started) / n * 1000
    assert per_call_ms < 250, f"notebook creation took {per_call_ms:.0f} ms/call"


# =====================================================================
# Change detection: Esri's ready-made model + a trainable ChangeDetector
# =====================================================================


def test_dl_routing_understands_change_detection():
    """Regression class: 'change detection' must never fall through to Mask R-CNN."""
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    def route(goal):
        d = dl.plan(goal)["data"]
        return d["recommended"] if d["mode"] != "choose" else "choose"

    assert route("change detection") == "choose"
    asked = dl.plan("change detection")["data"]
    assert {c["recommended"] for c in asked["choices"]} == {"esri_building_change", "changedetector"}
    assert route("detect building change with esri pretrained model") == "esri_building_change"
    assert route("اعمل change detection من ايزري") == "esri_building_change"
    assert dl.plan("x", prefer="changedetector")["data"]["recommended"] == "changedetector"
    assert route("detect buildings") == "maskrcnn", "'change' words must not hijack other goals"


def test_dl_pretrained_facts_come_from_the_item_page():
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    entry = dl.PRETRAINED["esri_building_change"]
    assert entry["item_id"] == "a779106ce9eb4fc28de6d04d8edb893c"
    assert entry["cell_size_m"] == (0.10, 0.20)
    joined = " ".join(entry["heads_up"])
    assert "10-20 cm" in joined and "Requires Subscription" in joined
    assert "cannot be fine-tuned" in joined, "this is why no epochs are asked"
    listed = dl.list_models("change_detection")["data"]
    assert [p["key"] for p in listed["pretrained"]] == ["esri_building_change"]
    assert [m["key"] for m in listed["models"]] == ["changedetector"]


def test_dl_pretrained_notebook_has_no_training_in_it(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "chg.ipynb"
    r = dl.create_pretrained_notebook(
        "esri_building_change", str(out),
        input_raster=r"F:\aerial_2019.tif", second_raster=r"F:\aerial_2024.tif")
    assert r["ok"] is True and r["data"]["status"] == "created"
    assert r["data"]["image_paths_are_placeholders"] is False

    code = _nb_code(out)
    compile(code, "chg", "exec")
    assert r'BEFORE_RASTER = r"F:\aerial_2019.tif"' in code
    assert r'AFTER_RASTER  = r"F:\aerial_2024.tif"' in code
    assert "arcpy.ia.DetectChangeUsingDeepLearning(" in code
    assert "a779106ce9eb4fc28de6d04d8edb893c" in code
    # Ready-made model: nothing is trained, so none of the training vocabulary may appear.
    for banned in ("EPOCHS", "lr_find", "prepare_data", ".fit(", "LEARNING_RATE"):
        assert banned not in code, banned
    # Licence is shown BEFORE the download, and the subscription flag is surfaced.
    assert code.index("item.licenseInfo") < code.index("item.download(")
    # The item stores its licence as HTML; showing raw tags was an observed defect.
    assert "re.sub(r'<[^>]+>'" in code and "https?://" in code
    assert "Requires Subscription" in code
    # Out-of-spec imagery is flagged (10-20 cm).
    assert "0.1 <= r.meanCellWidth <= 0.2" in code

    nb = json.loads(out.read_text(encoding="utf-8"))
    assert (nb["nbformat"], nb["nbformat_minor"]) == (4, 4)
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] == "code":
            assert nb["cells"][i - 1]["cell_type"] == "markdown", "one heading per code cell"


def test_dl_pretrained_asks_for_both_images_and_only_waives_with_permission(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "x.ipynb"
    refused = dl.create_pretrained_notebook("esri_building_change", str(out))
    assert refused["code"] == "needs_answers"
    assert refused["missing"] == ["input_raster", "second_raster"]
    assert not out.exists()
    assert dl.create_pretrained_notebook(
        "esri_building_change", str(out), input_raster=r"F:\a.tif")["missing"] == ["second_raster"]

    later = dl.create_pretrained_notebook("esri_building_change", str(out), data_path_later=True)
    assert later["ok"] is True and later["data"]["image_paths_are_placeholders"] is True
    code = _nb_code(out)
    assert "<PUT THE PATH TO THE BEFORE-date IMAGE HERE>" in code
    assert "<PUT THE PATH TO THE AFTER-date IMAGE HERE>" in code
    assert "Set {name} in the Parameters cell first" in code, "must stop with instructions"


def test_dl_pretrained_options_are_validated(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    base = dict(input_raster=r"F:\a.tif", second_raster=r"F:\b.tif")
    out = str(tmp_path / "o.ipynb")
    assert dl.create_pretrained_notebook("esri_building_change", out, options={"processor": "TPU"}, **base)["code"] == "bad_value"
    assert dl.create_pretrained_notebook("esri_building_change", out, options={"epochs": "9"}, **base)["code"] == "unsupported_option"
    assert dl.create_pretrained_notebook("esri_building_change", out, batch_size=0, **base)["code"] == "bad_value"
    assert dl.create_pretrained_notebook("nope", out, **base)["code"] == "unknown_model"
    assert not (tmp_path / "o.ipynb").exists()

    ok_ = dl.create_pretrained_notebook(
        "esri_building_change", out, batch_size=2,
        options={"processor": "GPU", "output_name": "chg_2024"}, **base)
    assert ok_["ok"] is True
    code = _nb_code(out)
    assert "PROCESSOR     = 'GPU'" in code and "OUTPUT_NAME   = 'chg_2024'" in code
    assert "BATCH_SIZE    = 2" in code


def test_dl_changedetector_training_notebook(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = tmp_path / "cd.ipynb"
    r = dl.create_training_notebook(
        "changedetector", str(out), training_data=r"F:\pairs", batch_size=4, epochs=5,
        options={"attention_type": "BAM"},
        input_raster=r"F:\aerial_2019.tif", second_raster=r"F:\aerial_2024.tif")
    assert r["ok"] is True
    code = _nb_code(out)
    compile(code, "cd", "exec")
    assert "from arcgis.learn import ChangeDetector, prepare_data" in code
    assert "model = ChangeDetector(data, attention_type=ATTENTION_TYPE)" in code
    assert "ATTENTION_TYPE = 'BAM'" in code
    # Verified against the installed class: it has precision_recall_score, no accuracy/AP.
    assert "model.precision_recall_score()" in code
    assert "average_precision_score" not in code and "model.accuracy()" not in code
    # Applied with the two-date tool, not a single-image one.
    assert "DetectChangeUsingDeepLearning(" in code
    assert r'SECOND_RASTER = r"F:\aerial_2024.tif"' in code
    assert "in_raster2" in dl.MODELS["changedetector"]["data_note"]

    why = dl.plan("x", prefer="changedetector")["data"]["questions"][0]["why"]
    assert "images2" in why, "the export needs the second-date raster"
    assert "attention_type" in {q["key"] for q in dl.plan("x", prefer="changedetector")["data"]["questions"]}
    bad = dl.create_training_notebook(
        "changedetector", str(tmp_path / "bad.ipynb"), training_data=r"F:\p", batch_size=1,
        epochs=1, options={"attention_type": "XYZ"})
    assert bad["code"] == "bad_value"


def test_dl_entry_point_handles_the_pretrained_flow(tmp_path):
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    out = str(tmp_path / "flow.ipynb")
    first = dl.notebook("اعمل change detection من ايزري", out_path=out)["data"]
    assert first["status"] == "ask_user" and first["mode"] == "pretrained"
    assert first["model"] == "esri_building_change"
    assert first["missing"] == ["input_raster", "second_raster"]
    keys = {q["key"] for q in first["questions"]}
    assert not keys & {"epochs", "learning_rate", "training_data"}, "no training questions"
    assert {"batch_size", "processor", "output_name"} <= keys
    assert any("10-20 cm" in h for h in first["heads_up"])
    assert not (tmp_path / "flow.ipynb").exists()

    made = dl.notebook("change detection from esri", out_path=out,
                       input_raster=r"F:\a.tif", second_raster=r"F:\b.tif")["data"]
    assert made["status"] == "created" and made["mode"] == "pretrained"

    # The user can still pick the trainable route explicitly.
    train = dl.notebook("change detection", model="changedetector",
                        out_path=str(tmp_path / "t.ipynb"))["data"]
    assert train["status"] == "ask_user" and "epochs" in train["missing"]


def test_dl_notebook_hands_over_manual_steps_and_never_touches_the_project(tmp_path, monkeypatch):
    """Registering a notebook through the add-in does not work on Pro 3.7, so it is not attempted."""
    from arcgis_pro_salah_mcp.live import client
    from arcgis_pro_salah_mcp.pro import dl_notebooks as dl

    posted = []
    monkeypatch.setattr(client, "post", lambda *a, **k: posted.append(a) or {"ok": False, "error": "blocked"})
    made = dl.notebook("detect buildings", out_path=str(tmp_path / "a.ipynb"),
                       training_data=r"F:\x", batch_size=4, epochs=1)["data"]
    assert made["status"] == "created" and "project" not in made
    assert "Add and Open Notebook" in made["add_to_pro"]
    assert posted == []


def test_add_toolbox_is_a_mutating_live_command(monkeypatch):
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import client, ops as live, policy

    assert "add_toolbox" in policy.MUTATING
    sent = {}
    monkeypatch.setattr(client, "post", lambda command, params=None: sent.update(
        command=command, params=params) or {"ok": True, "data": {"registered": True}})
    assert live.live_add_toolbox(r"C:\p\m.atbx")["ok"] is True
    assert sent == {"command": "add_toolbox", "params": {"path": r"C:\p\m.atbx"}}

    sent.clear()
    monkeypatch.setattr(CONFIG, "bridge_readonly", True)
    blocked = live.live_add_toolbox(r"C:\p\m.atbx")
    assert blocked["ok"] is False and blocked["code"] == "readonly" and sent == {}


def test_atbx_extra_outputs_wire_a_derived_value_into_a_later_step(tmp_path):
    import json
    import zipfile

    from arcgis_pro_salah_mcp.pro import modelbuilder as mb

    out = tmp_path / "chain.atbx"
    steps = [
        {"tool": "ia.ExportTrainingDataForDeepLearning", "params": {"in_raster": "{{img}}"},
         "output_param": "out_folder", "output_datatype": "folder"},
        {"tool": "ia.TrainDeepLearningModel", "params": {"in_folder": "{{step1}}"},
         "output_param": "out_folder", "output_datatype": "folder",
         "extra_outputs": [{"param": "out_model_file", "datatype": "file"}]},
        {"tool": "ia.DetectObjectsUsingDeepLearning",
         "params": {"in_raster": "{{img}}", "in_model_definition": "{{step2.out_model_file}}"},
         "output_param": "out_detected_objects", "output_datatype": "feature_class"},
    ]
    made = mb.create_model(str(out), "T", "Chain", steps,
                           parameters=[{"name": "img", "datatype": "raster_layer"}],
                           allow_unverified=True)
    assert made["ok"] is True, made

    model = json.loads(zipfile.ZipFile(out).read("Chain.tool/tool.model"))
    train, detect = model["processes"][1], model["processes"][2]
    derived_id = train["params"]["out_model_file"]["element_id"]
    assert detect["params"]["in_model_definition"] == {"element_id": derived_id}
    var = next(v for v in model["variables"] if v["id"] == derived_id)
    assert var["datatype"] == {"type": "DEFile"} and "value" not in var

    # A folder intermediate must not default into a geodatabase.
    folder = next(v for v in model["variables"] if v.get("datatype") == {"type": "DEFolder"})
    assert folder["value"].startswith("%scratchfolder%")

    # Asking for an output the earlier step never declared is an error, not a silent blank.
    bad = mb.create_model(str(tmp_path / "bad.atbx"), "T", "Bad", [
        steps[0], {"tool": "ia.DetectObjectsUsingDeepLearning",
                   "params": {"in_model_definition": "{{step1.out_model_file}}"},
                   "output_param": "out_detected_objects"}],
        parameters=[{"name": "img", "datatype": "raster_layer"}], allow_unverified=True)
    assert bad["ok"] is False and bad["code"] == "bad_model" and "extra_outputs" in bad["error"]



# =====================================================================
# Workflow -> toolbox: guard, richer .pyt steps, registration in the project
# =====================================================================


def _fc_chain():
    return [
        {"tool": "analysis.Buffer", "params": {"in_features": "{{roads}}", "buffer_distance_or_field": "10 Meters"},
         "output_param": "out_feature_class"},
        {"tool": "management.Dissolve", "params": {"in_features": "{{step1}}"},
         "output_param": "out_feature_class", "output": "{{result}}"},
    ]


_FC_PARAMS = [{"name": "roads", "datatype": "feature_layer"},
              {"name": "result", "datatype": "feature_class", "direction": "Output"}]


def test_atbx_refuses_patterns_that_are_known_not_to_run(tmp_path):
    from arcgis_pro_salah_mcp.pro import modelbuilder as mb

    def build(steps, **kw):
        return mb.create_model(str(tmp_path / "m.atbx"), "T", "M", steps, parameters=_FC_PARAMS, **kw)

    assert build(_fc_chain())["ok"] is True  # the verified shape still works

    # Multi-input tool fed from a reference: five encodings failed in Pro 3.7.
    intersect = [{"tool": "analysis.Intersect", "params": {"in_features": ["{{roads}}", "{{roads}}"]},
                  "output_param": "out_feature_class", "output": "{{result}}"}]
    refused = build(intersect)
    assert refused["ok"] is False and refused["code"] == "unsupported_model"
    assert "pro_create_python_toolbox" in refused["error"]
    # ...but only references are refused: literal paths are not something we found failing.
    literal = [{"tool": "analysis.Intersect", "params": {"in_features": r"C:\a.gdb\x;C:\a.gdb\y"},
                "output_param": "out_feature_class", "output": "{{result}}"}]
    assert build(literal)["ok"] is True

    # Folder / file / layer intermediates did not validate either.
    folder = [{"tool": "ia.ExportTrainingDataForDeepLearning", "params": {"in_raster": "{{roads}}"},
               "output_param": "out_folder", "output_datatype": "folder"}]
    assert build(folder)["code"] == "unsupported_model"
    # A DECLARED output parameter is not an intermediate, so its type is not the issue.
    declared = [{"tool": "analysis.Buffer", "params": {"in_features": "{{roads}}"},
                 "output_param": "out_feature_class", "output": "{{result}}", "output_datatype": "folder"}]
    assert build(declared)["ok"] is True
    extra = [{"tool": "analysis.Buffer", "params": {"in_features": "{{roads}}"},
              "output_param": "out_feature_class", "output": "{{result}}",
              "extra_outputs": [{"param": "p", "datatype": "file"}]}]
    assert build(extra)["code"] == "unsupported_model"

    # The escape hatch exists for someone holding a Pro-authored reference.
    assert build(intersect, allow_unverified=True)["ok"] is True


def test_pyt_steps_support_keywords_lists_and_memory_cleanup(tmp_path):
    from arcgis_pro_salah_mcp.pro import toolbox

    tool = {
        "name": "ClipIntersectExport", "label": "Clip Intersect Export",
        "parameters": [
            {"name": "inp", "label": "Input", "datatype": "feature_layer"},
            {"name": "clip", "label": "Clip", "datatype": "feature_layer"},
            {"name": "overlay", "label": "Overlay", "datatype": "feature_layer"},
            {"name": "out", "label": "Output", "datatype": "feature_class", "direction": "Output"},
        ],
        "steps": [
            {"tool": "analysis.Clip", "kwargs": {"in_features": "{{inp}}", "clip_features": "{{clip}}",
                                                  "out_feature_class": "{{memory}}"}},
            {"tool": "analysis.Intersect", "kwargs": {"in_features": ["{{step1}}", "{{overlay}}"],
                                                       "out_feature_class": "{{memory}}"}},
            {"tool": "conversion.ExportFeatures", "kwargs": {"in_features": "{{step2}}", "out_features": "{{out}}"}},
        ],
    }
    out = tmp_path / "Chain.pyt"
    assert toolbox.create_python_toolbox(str(out), "Chain", [tool])["ok"] is True
    source = out.read_text(encoding="utf-8")
    compile(source, str(out), "exec")

    assert "in_features=[step1, overlay]" in source           # list elements are resolved, not quoted
    assert "in_features=step2, out_features=out" in source   # {{stepN}} is the earlier OUTPUT PATH
    assert "'memory\\\\step1'" in source and "'memory\\\\step2'" in source
    assert "finally:" in source and "arcpy.management.Delete('memory\\\\step1')" in source
    assert "messages.addMessage(" in source and "arcpy.AddMessage" not in source
    assert "def _first_output(result):" in source
    # The step output is not memory-cleaned unless the step asked for {{memory}}.
    assert "memory\\\\step3" not in source


def test_register_toolbox_reports_every_outcome_without_raising(monkeypatch):
    from arcgis_pro_salah_mcp.live import ops as live
    from arcgis_pro_salah_mcp.pro import register

    monkeypatch.setattr(register, "bridge_listening", lambda: False)
    off = register.register_toolbox(r"C:\p\t.pyt")
    assert off["added"] is False and "not running" in off["reason"] and off["manual"]

    monkeypatch.setattr(register, "bridge_listening", lambda: True)
    monkeypatch.setattr(live, "live_add_toolbox", lambda p: {"ok": True, "data": {
        "registered": True, "already_registered": False, "steps": []}})
    good = register.register_toolbox(r"C:\p\t.atbx")
    assert good["added"] is True and good["manual"] is None

    monkeypatch.setattr(live, "live_add_toolbox", lambda p: {"ok": True, "data": {
        "registered": False, "steps": [{"step": "x", "ok": False, "detail": "boom"}]}})
    failed = register.register_toolbox(r"C:\p\t.atbx")
    assert failed["added"] is False and failed["manual"] and failed["steps"][0]["detail"] == "boom"

    monkeypatch.setattr(live, "live_add_toolbox", lambda p: {"ok": False, "error": "unknown command 'add_toolbox'"})
    assert "0.1.4" in register.register_toolbox(r"C:\p\t.pyt")["hint"]

    # A .pyt makes Pro ask "trust this code?" on the UI thread; every bridge call then times out.
    monkeypatch.setattr(live, "live_add_toolbox", lambda p: {"ok": False, "error": "bridge unreachable: timed out"})
    pending = register.register_toolbox(r"C:\p\t.pyt")
    assert pending["added"] is False and pending["pending"] is True and "Trust" in pending["hint"]
    assert "pending" not in register.register_toolbox(r"C:\p\t.atbx")   # only .pyt has that dialog

    def boom(p):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(live, "live_add_toolbox", boom)
    assert "socket exploded" in register.register_toolbox(r"C:\p\t.pyt")["reason"]


def test_generators_register_in_the_project_only_when_asked(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import modelbuilder as mb
    from arcgis_pro_salah_mcp.pro import register, toolbox

    calls = []
    monkeypatch.setattr(register, "register_toolbox", lambda p: calls.append(p) or {"added": True})

    tool = _simple_tool()
    quiet = toolbox.create_python_toolbox(str(tmp_path / "q.pyt"), "Q", [tool])
    assert quiet["ok"] is True and calls == [] and "project" not in quiet["data"]

    loud = toolbox.create_python_toolbox(str(tmp_path / "l.pyt"), "L", [tool], register_in_project=True)
    assert calls == [str(tmp_path / "l.pyt")] and loud["data"]["project"] == {"added": True}

    calls.clear()
    made = mb.create_model(str(tmp_path / "m.atbx"), "T", "M", _fc_chain(), parameters=_FC_PARAMS,
                           register_in_project=True)
    assert made["ok"] is True and calls == [str(tmp_path / "m.atbx")]

    # A generator that refuses or fails never touches the project.
    calls.clear()
    assert toolbox.create_python_toolbox(str(tmp_path / "n.pyt"), "N", [], register_in_project=True)["ok"] is False
    assert mb.create_model(str(tmp_path / "x.atbx"), "T", "M", [], register_in_project=True)["ok"] is False
    assert calls == []



# =====================================================================
# Enterprise geodatabase (pro_egdb_*) — backend-free, with a fake arcpy
# =====================================================================

from types import SimpleNamespace as _NS


class _FakeResult:
    outputCount = 1

    def __getitem__(self, i):
        return r"C:\out\conn.sde"

    def getMessages(self):
        return "Succeeded"


class _FakeArcpy:
    """Records every GP call; behaviour per tool can be overridden through ``effects``."""

    def __init__(self, versions=(), replicas=(), workspace="RemoteDatabase", described=None, effects=None):
        self.calls = []
        fake = self

        class _Toolbox:
            def __getattr__(self, name):
                def tool(*args, **kwargs):
                    fake.calls.append((name, args, kwargs))
                    if effects and name in effects:
                        effects[name](*args, **kwargs)
                    return _FakeResult()
                return tool

        self.management = _Toolbox()
        self.da = _NS(ListVersions=lambda c: list(versions), ListReplicas=lambda c: list(replicas))
        self.env = _NS(workspace=None)
        self._workspace = workspace
        self._described = described or {}

    def Describe(self, path):
        if isinstance(self._workspace, Exception):
            raise self._workspace
        return self._described.get(path, _NS(workspaceType=self._workspace, isVersioned=True, hasGlobalID=True))

    def AcceptConnections(self, conn, accept):
        self.calls.append(("AcceptConnections", (conn, accept), {}))


def _version(name, parent=None, children=()):
    return _NS(name=name, access="Public", parentVersionName=parent, children=list(children), isOwner=True,
               created="2026-10-24", lastModified="2026-10-24", description="")


def _use(monkeypatch, fake):
    from arcgis_pro_salah_mcp.pro import egdb

    monkeypatch.setattr(egdb, "_arcpy", lambda: fake)
    return egdb


def _sde(tmp_path, name="admin.sde"):
    path = tmp_path / name
    path.write_text("x")
    return str(path)


def test_egdb_create_validates_everything_before_importing_arcpy(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import egdb

    def no_arcpy():
        raise AssertionError("arcpy must not be imported for a call that is already known to be wrong")

    monkeypatch.setattr(egdb, "_arcpy", no_arcpy)
    keys = _sde(tmp_path, "keycodes.ecp")
    monkeypatch.delenv("EGDB_NOPE", raising=False)
    cases = {
        "bad platform": dict(database_platform="MYSQL"),
        "missing keycodes": dict(authorization_file=str(tmp_path / "none.ecp")),
        "db auth without password env": dict(dba_user="sa"),
        "sde schema without admin password": dict(account_authentication="OPERATING_SYSTEM_AUTH"),
        "unset env var": dict(dba_user="sa", dba_password_env="EGDB_NOPE", gdb_admin_password_env="EGDB_NOPE"),
        "dbo schema on postgres": dict(database_platform="POSTGRESQL", schema_type="DBO_SCHEMA"),
        "postgis on sql server": dict(spatial_type="POSTGIS", account_authentication="OPERATING_SYSTEM_AUTH",
                                      schema_type="DBO_SCHEMA"),
    }
    for label, override in cases.items():
        args = dict(database_platform="SQL_SERVER", instance=r"HOST\SQLEXPRESS", database_name="gis",
                    authorization_file=keys)
        args.update(override)
        res = egdb.create_gdb(**args)
        assert res["ok"] is False, label
        assert res["code"] in {"bad_argument", "not_found", "missing_secret"}, (label, res)
    assert egdb.create_gdb("SQL_SERVER", "h", "gis", keys, dba_user="sa", dba_password_env="EGDB_NOPE",
                           gdb_admin_password_env="EGDB_NOPE")["code"] == "missing_secret"


def test_egdb_create_with_windows_auth_and_dbo_schema_needs_no_secrets(tmp_path, monkeypatch):
    fake = _FakeArcpy()
    egdb = _use(monkeypatch, fake)
    res = egdb.create_gdb("sql_server", r"HOST\SQLEXPRESS", "gis", _sde(tmp_path, "keycodes.ecp"),
                          account_authentication="OPERATING_SYSTEM_AUTH", schema_type="DBO_SCHEMA")
    assert res["ok"] is True and res["data"]["platform"] == "SQL_Server"
    name, _, kwargs = fake.calls[0]
    assert name == "CreateEnterpriseGeodatabase"
    assert kwargs["sde_schema"] == "DBO_SCHEMA" and kwargs["database_platform"] == "SQL_Server"
    for absent in ("database_admin", "database_admin_password", "gdb_admin_name", "gdb_admin_password"):
        assert absent not in kwargs, absent


def test_egdb_irreversible_ops_need_confirm(tmp_path, monkeypatch):
    fake = _FakeArcpy(versions=[_version("sde.DEFAULT"), _version("sde.v1", "sde.DEFAULT")])
    egdb = _use(monkeypatch, fake)
    conn = _sde(tmp_path)
    refused = [
        egdb.delete_version(conn, "sde.v1"),
        egdb.unregister_versioned(conn + r"\fc"),
        egdb.unregister_replica(conn, "r1"),
        egdb.accept_connections(conn, False),
        egdb.reconcile_post(conn, ["sde.v1"], delete_versions=True),
    ]
    assert all(r["ok"] is False and r["code"] == "confirm_required" for r in refused), refused
    assert fake.calls == []  # nothing reached the database

    assert egdb.delete_version(conn, "sde.v1", confirm=True)["ok"] is True
    assert egdb.accept_connections(conn, True)["ok"] is True  # re-opening never needs confirmation
    assert [c[0] for c in fake.calls] == ["DeleteVersion", "AcceptConnections"]


def test_egdb_reconcile_reconciles_listed_versions_and_trusts_only_the_log(tmp_path, monkeypatch):
    log_text = {"value": "Reconcile completed."}

    def write_log(*args, **kwargs):
        with open(args[10], "w", encoding="utf-8") as fh:
            fh.write(log_text["value"])

    versions = [_version("dbo.DEFAULT"), _version("dbo.a", "dbo.DEFAULT"), _version("dbo.b", "dbo.a")]
    fake = _FakeArcpy(versions=versions, effects={"ReconcileVersions": write_log})
    egdb = _use(monkeypatch, fake)
    conn = _sde(tmp_path)

    done = egdb.reconcile_post(conn, ["dbo.b"], "dbo.a")["data"]
    args = fake.calls[-1][1]
    assert args[1] == "ALL_VERSIONS" and args[2] == "dbo.a" and args[3] == ["dbo.b"]
    assert done["reconciled"] is True and done["posted"] is True

    # Pro answers "success" even when it reconciled nothing - the log is the only honest signal.
    log_text["value"] = ("Warning: The reconcile process was not performed. The specified target version "
                         "(dbo.a) has no edit versions to reconcile with.")
    skipped = egdb.reconcile_post(conn, ["dbo.b"], "dbo.a")["data"]
    assert skipped["reconciled"] is False and skipped["posted"] is False

    # Defaults: target = the real DEFAULT (dbo.DEFAULT here), edit versions = every other version.
    egdb.reconcile_post(conn)
    args = fake.calls[-1][1]
    assert args[2] == "dbo.DEFAULT" and args[3] == ["dbo.a", "dbo.b"]
    egdb.reconcile_post(conn, ["dbo.a"], blocking_only=True)
    assert fake.calls[-1][1][1] == "BLOCKING_VERSIONS"


def test_egdb_version_and_replica_lists_are_json_safe(tmp_path, monkeypatch):
    child = _version("dbo.child", "dbo.DEFAULT")
    versions = [_version("dbo.DEFAULT", children=[child]), child]
    replicas = [_NS(name="dbo.r1", type="OneWay", role="Parent", owner="dbo", version="dbo.DEFAULT",
                    datasets=["MCP.DBO.fc"], lastSend="t", lastReceive="t", hasConflicts=False, isSender=True)]
    egdb = _use(monkeypatch, _FakeArcpy(versions=versions, replicas=replicas))
    conn = _sde(tmp_path)

    listed = egdb.list_versions(conn)
    json.dumps(listed)  # the MCP transport has to serialise it
    assert listed["data"]["versions"][0]["children"] == ["dbo.child"]
    reps = egdb.list_replicas(conn)
    json.dumps(reps)
    assert reps["data"]["replicas"][0]["datasets"] == ["MCP.DBO.fc"]

    # The DEFAULT version is looked up, not assumed to be sde.DEFAULT.
    created = egdb.create_version(conn, "edits")["data"]
    assert created["parent"] == "dbo.DEFAULT"


def test_egdb_create_replica_checks_child_type_and_data_first(tmp_path, monkeypatch):
    child = str(tmp_path / "child.gdb")
    fake = _FakeArcpy(described={
        child: _NS(workspaceType="LocalDatabase"),
        "fc_ok": _NS(isVersioned=True, hasGlobalID=True),
        "fc_raw": _NS(isVersioned=False, hasGlobalID=False),
    })
    egdb = _use(monkeypatch, fake)
    for rtype in ("TWO_WAY_REPLICA", "ONE_WAY_CHILD_TO_PARENT_REPLICA"):
        res = egdb.create_replica(["fc_ok"], child, "r", rtype)
        assert res["ok"] is False and res["code"] == "bad_argument", rtype
    raw = egdb.create_replica(["fc_raw"], child, "r", "ONE_WAY_REPLICA")
    assert raw["code"] == "not_ready" and "versioned" in raw["error"] and "GlobalID" in raw["error"]
    assert fake.calls == []
    assert egdb.create_replica(["fc_ok"], child, "r", "one_way_replica")["ok"] is True
    assert fake.calls[-1][1][1] == "ONE_WAY_REPLICA"


def test_egdb_connection_problems_are_explained(tmp_path, monkeypatch):
    egdb = _use(monkeypatch, _FakeArcpy(workspace=RuntimeError("Could not open SDE workspace")))
    missing = egdb.list_versions(str(tmp_path / "gone.sde"))
    assert missing["code"] == "not_found"
    dead = egdb.describe(_sde(tmp_path))
    assert dead["code"] == "connection_failed" and "database server is running" in dead["error"]


def test_egdb_create_connection_uses_script_keywords(tmp_path, monkeypatch):
    fake = _FakeArcpy()
    egdb = _use(monkeypatch, fake)
    bad = egdb.create_connection(str(tmp_path), "c", "SQL_SERVER", "h", account_authentication="OPERATING_SYSTEM_AUTH",
                                 version_type="TRADITIONAL")
    assert bad["code"] == "bad_argument" and "TRANSACTIONAL" in bad["error"]
    assert fake.calls == []
    branch = egdb.create_connection(str(tmp_path), "c", "sql_server", "h", account_authentication="OPERATING_SYSTEM_AUTH",
                                    version="sde.v1", version_type="branch")
    kwargs = fake.calls[-1][2]
    assert kwargs["out_name"] == "c.sde" and kwargs["version_type"] == "BRANCH" and "username" not in kwargs
    assert "ignored" in branch["data"]["note"]


def test_egdb_toolset_can_be_switched_off():
    from arcgis_pro_salah_mcp import server

    assert "egdb" in server.TOOLSETS and server.TOOLSETS["egdb"] == ("pro_egdb_",)


def test_egdb_reconcile_matches_version_names_case_insensitively(tmp_path, monkeypatch):
    """Pro 3.7 skipped 'dbo.c' for the stored 'DBO.c' and reconciled nothing (verified on SQL Server)."""
    versions = [_version("dbo.DEFAULT"), _version("DBO.p", "dbo.DEFAULT"), _version("DBO.c", "DBO.p")]
    fake = _FakeArcpy(versions=versions)
    egdb = _use(monkeypatch, fake)
    conn = _sde(tmp_path)
    egdb.reconcile_post(conn, ["dbo.c"], "dbo.p")
    args = fake.calls[-1][1]
    assert args[2] == "DBO.p" and args[3] == ["DBO.c"]
    unknown = egdb.reconcile_post(conn, ["dbo.nope"], "dbo.p")
    assert unknown["code"] == "unknown_version" and "DBO.c" in unknown["versions"]
    assert egdb.reconcile_post(conn, ["dbo.c"], "dbo.missing")["code"] == "unknown_version"


def test_egdb_explains_sql_server_snapshot_and_non_geodatabase_errors(tmp_path, monkeypatch):
    def busy(*args, **kwargs):
        raise RuntimeError("Active connections found during upgrade ERROR: Failed to enable database option "
                           "READ_COMMITTED_SNAPSHOT on SQL Server database (GIS).")

    fake = _FakeArcpy(effects={"EnableEnterpriseGeodatabase": busy}, described={
        _sde(tmp_path): _NS(workspaceType="RemoteDatabase", connectionProperties=_NS(database="GIS"))})
    egdb = _use(monkeypatch, fake)
    res = egdb.enable_gdb(_sde(tmp_path), _sde(tmp_path, "keycodes.ecp"))
    assert res["code"] == "needs_read_committed_snapshot"
    assert "ALTER DATABASE [GIS] SET READ_COMMITTED_SNAPSHOT ON" in res["error"]

    def plain_db(conn):
        raise TypeError(conn)  # what arcpy.da.ListVersions raised on a database that is not a geodatabase

    fake.da.ListVersions = plain_db
    listed = egdb.list_versions(_sde(tmp_path))
    assert listed["ok"] is False and "Is it a geodatabase" in listed["error"]



# --------------------------------------------------------------------- egdb additions


class _FakeSQL:
    """Stands in for arcpy.ArcSDESQLExecute: records statements, answers queries from ``answers``."""

    def __init__(self, log, answers=None, fail_on=None):
        self.log, self.answers, self.fail_on = log, answers or {}, fail_on

    def execute(self, statement):
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError("database said no")
        self.log.append(statement)
        for key, value in self.answers.items():
            if key in statement:
                return value
        return True


def _dbms_fake(dbms, answers=None, fail_on=None, **kw):
    fake = _FakeArcpy(**kw)
    fake.sql_log = []
    fake.ArcSDESQLExecute = lambda conn: _FakeSQL(fake.sql_log, answers, fail_on)
    base = fake.Describe

    def describe(path):
        d = base(path)
        if not hasattr(d, "connectionProperties"):
            d.connectionProperties = _NS(instance=f"sde:{dbms}:host", database="GIS")
        return d

    fake.Describe = describe
    return fake


def test_egdb_license_comes_from_the_caller_never_from_the_code(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import egdb as egdb_module

    fake = _FakeArcpy()
    egdb = _use(monkeypatch, fake)
    monkeypatch.delenv(egdb_module.AUTHORIZATION_ENV, raising=False)
    args = dict(account_authentication="OPERATING_SYSTEM_AUTH", schema_type="DBO_SCHEMA")
    missing = egdb.create_gdb("SQL_SERVER", "h", "gis", **args)
    assert missing["code"] == "missing_authorization" and egdb_module.AUTHORIZATION_ENV in missing["error"]
    assert egdb.enable_gdb(_sde(tmp_path))["code"] == "missing_authorization"
    assert fake.calls == []

    keys = _sde(tmp_path, "my_keycodes.ecp")
    monkeypatch.setenv(egdb_module.AUTHORIZATION_ENV, keys)
    assert egdb.create_gdb("SQL_SERVER", "h", "gis", **args)["ok"] is True
    assert fake.calls[-1][2]["authorization_file"] == keys


def test_egdb_point_in_time_connection_and_project_registration(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import register

    fake = _FakeArcpy()
    egdb = _use(monkeypatch, fake)
    common = dict(out_folder=str(tmp_path), out_name="c", database_platform="SQL_SERVER", instance="h",
                  account_authentication="OPERATING_SYSTEM_AUTH")
    assert egdb.create_connection(**common, version_type="POINT_IN_TIME")["code"] == "bad_argument"
    assert egdb.create_connection(**common, date="2026-10-24 04:00:00")["code"] == "bad_argument"
    calls = []
    monkeypatch.setattr(register, "register_database", lambda p: calls.append(p) or {"added": True})
    made = egdb.create_connection(**common, version_type="POINT_IN_TIME", date="2026-10-24 04:00:00",
                                  register_in_project=True)
    assert fake.calls[-1][2]["date"] == "2026-10-24 04:00:00"
    assert made["data"]["project"] == {"added": True} and calls == [r"C:\out\conn.sde"]


def test_egdb_upgrade_checks_by_default_and_disconnect_needs_confirm(tmp_path, monkeypatch):
    fake = _FakeArcpy()
    fake.ListUsers = lambda conn: [_NS(ID=7, Name="SDE")]
    fake.DisconnectUser = lambda conn, users: fake.calls.append(("DisconnectUser", (conn, users), {}))
    egdb = _use(monkeypatch, fake)
    conn = _sde(tmp_path)
    assert egdb.upgrade_gdb(conn)["ok"] is True
    assert fake.calls[-1][1][1:] == ("PREREQUISITE_CHECK", "NO_UPGRADE")
    assert egdb.upgrade_gdb(conn, upgrade=True)["code"] == "confirm_required"
    # Esri's tool FAILS with ERROR 001046 on a current geodatabase; that is reported as up to date.
    current = _FakeArcpy(described={conn: _NS(workspaceType="RemoteDatabase", currentRelease=True, release="3,0,0")})
    egdb_current = _use(monkeypatch, current)
    assert egdb_current.upgrade_gdb(conn)["data"]["up_to_date"] is True and current.calls == []
    egdb = _use(monkeypatch, fake)

    assert egdb.disconnect_users(conn)["code"] == "bad_argument"
    assert egdb.disconnect_users(conn, users=["bob"])["code"] == "bad_argument"
    assert egdb.disconnect_users(conn, all_users=True)["code"] == "confirm_required"
    done = egdb.disconnect_users(conn, users=[12, 13], confirm=True)["data"]
    assert fake.calls[-1] == ("DisconnectUser", (conn, [12, 13]), {}) and done["still_connected"][0]["id"] == 7


def test_egdb_version_health_diagnoses_freely_and_repairs_only_when_confirmed(tmp_path, monkeypatch):
    fake = _FakeArcpy()
    egdb = _use(monkeypatch, fake)
    conn = _sde(tmp_path)
    assert egdb.version_health(conn)["ok"] is True and fake.calls[-1][0] == "DiagnoseVersionTables"
    assert egdb.version_health(conn, scope="metadata")["ok"] is True
    assert fake.calls[-1][0] == "DiagnoseVersionMetadata"
    assert egdb.version_health(conn, action="repair")["code"] == "confirm_required"
    assert egdb.version_health(conn, action="repair", scope="tables", confirm=True)["ok"] is True
    assert fake.calls[-1][0] == "RepairVersionTables"
    assert egdb.version_health(conn, action="explode")["code"] == "bad_argument"


def test_egdb_manage_principal_sql_per_dbms_and_never_leaks_the_password(tmp_path, monkeypatch):
    conn = _sde(tmp_path)
    monkeypatch.setenv("NEW_PWD", "S3cr'et!")

    fake = _dbms_fake("sqlserver")
    egdb = _use(monkeypatch, fake)
    assert egdb.manage_principal(conn, "drop_user", "editor1")["code"] == "confirm_required"
    assert egdb.manage_principal(conn, "drop_user", "sde", confirm=True)["code"] == "bad_argument"
    assert egdb.manage_principal(conn, "drop_user", "x; DROP TABLE t", confirm=True)["code"] == "bad_argument"
    assert egdb.manage_principal(conn, "drop_user", "editor1", confirm=True)["ok"] is True
    assert fake.sql_log[1:] == ["IF SCHEMA_ID('editor1') IS NOT NULL DROP SCHEMA [editor1]",
                            "IF DATABASE_PRINCIPAL_ID('editor1') IS NOT NULL DROP USER [editor1]",
                            "IF SUSER_ID('editor1') IS NOT NULL DROP LOGIN [editor1]"]
    res = egdb.manage_principal(conn, "set_password", "editor1", password_env="NEW_PWD")
    assert fake.sql_log[-1] == "ALTER LOGIN [editor1] WITH PASSWORD = N'S3cr''et!'"

    # A logged-in user is refused BEFORE anything is dropped (SQL Server cannot drop a login in use).
    busy = _dbms_fake("sqlserver", answers={"dm_exec_sessions": [[57], [58]]})
    egdb = _use(monkeypatch, busy)
    refused = egdb.manage_principal(conn, "drop_user", "editor1", confirm=True)
    assert refused["code"] == "user_connected" and refused["sessions"] == [57, 58] and len(busy.sql_log) == 1
    # ArcSDESQLExecute cannot run KILL (SQL Server: "cannot be used inside user transactions"), so the
    # statements are handed to a DBA instead of being attempted.
    assert refused["kill_sql"] == ["KILL 57;", "KILL 58;"] and not any("KILL" in x for x in busy.sql_log)
    egdb = _use(monkeypatch, fake)
    assert "S3cr" not in json.dumps(res)

    fake = _dbms_fake("sqlserver", answers={"database_role_members": [["editor1"], ["viewer2"]]})
    egdb = _use(monkeypatch, fake)
    assert egdb.manage_principal(conn, "drop_role", "editors", confirm=True)["ok"] is True
    assert fake.sql_log[1:] == ["ALTER ROLE [editors] DROP MEMBER [editor1]",
                                "ALTER ROLE [editors] DROP MEMBER [viewer2]",
                                "IF DATABASE_PRINCIPAL_ID('editors') IS NOT NULL DROP ROLE [editors]"]

    # PostgreSQL: Create Database User grants privileges (PostGIS tables, the database) that block
    # DROP ROLE, so DROP OWNED revokes them - only after proving the role owns no objects.
    fake = _dbms_fake("postgresql", answers={"pg_class": [[0]]})
    egdb = _use(monkeypatch, fake)
    egdb.manage_principal(conn, "drop_user", "editor1", confirm=True)
    egdb.manage_principal(conn, "set_password", "editor1", password_env="NEW_PWD")
    assert fake.sql_log[1:] == ["DROP SCHEMA IF EXISTS editor1", "DROP OWNED BY editor1",
                                "DROP ROLE IF EXISTS editor1", "ALTER ROLE editor1 WITH PASSWORD 'S3cr''et!'"]
    fake = _dbms_fake("postgresql", answers={"pg_class": [[3]]})
    egdb = _use(monkeypatch, fake)
    owner = egdb.manage_principal(conn, "drop_user", "owner1", confirm=True)
    assert owner["code"] == "owns_data" and len(fake.sql_log) == 1  # only the ownership query ran

    # A refusal from the database (e.g. the schema still holds data) comes back, nothing is cascaded.
    fake = _dbms_fake("postgresql", fail_on="DROP SCHEMA")
    egdb = _use(monkeypatch, fake)
    refused = egdb.manage_principal(conn, "drop_user", "owner1", confirm=True)
    assert refused["code"] == "sql_failed" and "CASCADE" not in json.dumps(refused)


def test_egdb_table_privileges_are_granular_but_only_for_simple_tables(tmp_path, monkeypatch):
    conn = _sde(tmp_path)
    fake = _dbms_fake("sqlserver", described={
        "plain": _NS(name="GIS.DBO.parcels", isVersioned=False, isArchived=False, relationshipClassNames=[]),
        "versioned": _NS(name="GIS.DBO.roads", isVersioned=True, isArchived=False, relationshipClassNames=[]),
        "attached": _NS(name="GIS.DBO.poles", isVersioned=False, isArchived=False,
                        relationshipClassNames=["GIS.DBO.poles__ATTACHREL"]),
    })
    egdb = _use(monkeypatch, fake)
    assert egdb.table_privileges(conn, ["plain"], "editors", grant=["DROP"])["code"] == "bad_argument"
    done = egdb.table_privileges(conn, ["plain"], "editors", grant=["select", "insert"], revoke=["delete"])
    assert done["ok"] is True
    assert fake.sql_log == ["GRANT SELECT, INSERT ON DBO.parcels TO editors", "REVOKE DELETE ON DBO.parcels FROM editors"]
    for ds in ("versioned", "attached"):
        res = egdb.table_privileges(conn, [ds], "editors", grant=["SELECT"])
        assert res["code"] == "use_change_privileges", ds


def test_egdb_cloud_preparation_follows_esri_and_masks_the_password(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.pro import egdb

    def no_arcpy():
        raise AssertionError("a dry run must not touch arcpy")

    monkeypatch.setattr(egdb, "_arcpy", no_arcpy)
    monkeypatch.setenv("SDE_PWD", "Cl0ud'Pwd")
    rds = egdb.prepare_cloud_database("POSTGRESQL", "rds", sde_password_env="SDE_PWD")["data"]
    assert rds["statements"] == ["CREATE ROLE sde LOGIN PASSWORD '********'", "GRANT rds_superuser TO sde",
                                 "CREATE SCHEMA sde AUTHORIZATION sde"]
    assert "Cl0ud" not in json.dumps(rds) and rds["executed"] is False
    assert "azure_pg_admin" in egdb.prepare_cloud_database("POSTGRESQL", "AZURE")["data"]["statements"][1]
    azure = egdb.prepare_cloud_database("SQL_SERVER", "AZURE_SQL_DATABASE")["data"]["statements"]
    assert azure[0].startswith("CREATE USER sde WITH PASSWORD") and "GRANT VIEW DATABASE STATE TO sde" in azure
    assert "GRANT KILL DATABASE CONNECTION TO sde" in azure
    rds_sql = egdb.prepare_cloud_database("SQL_SERVER", "RDS")["data"]["statements"]
    assert rds_sql[0].startswith("CREATE LOGIN sde") and rds_sql[-1] == "ALTER SERVER ROLE processadmin ADD MEMBER sde"
    assert egdb.prepare_cloud_database("ORACLE", "RDS")["code"] == "bad_argument"
    assert egdb.prepare_cloud_database("POSTGRESQL", "rds", execute=True)["code"] == "bad_argument"
    blocked = egdb.prepare_cloud_database("POSTGRESQL", "rds", admin_connection=_sde(tmp_path),
                                          sde_password_env="SDE_PWD", execute=True)
    assert blocked["code"] == "confirm_required"


def test_egdb_views_keywords_and_replica_schema_validate_first(tmp_path, monkeypatch):
    fake = _FakeArcpy()
    egdb = _use(monkeypatch, fake)
    conn = _sde(tmp_path)
    assert egdb.create_view(conn, "v_parcels", "DELETE FROM parcels")["code"] == "bad_argument"
    assert egdb.create_view(conn, "v-parcels", "SELECT 1")["code"] == "bad_argument"
    assert egdb.create_view(conn, "v_parcels", "SELECT objectid FROM dbo.parcels")["ok"] is True
    assert egdb.config_keywords(conn, "import", str(tmp_path / "none.txt"))["code"] == "not_found"
    assert egdb.config_keywords(conn, "export", str(tmp_path / "kw.txt"))["ok"] is True
    assert egdb.replica_schema(conn, "export", str(tmp_path / "s.xml"))["code"] == "bad_argument"
    assert egdb.replica_schema(conn, "compare", _sde(tmp_path, "s.xml"))["code"] == "bad_argument"
    assert egdb.replica_schema(conn, "export", str(tmp_path / "s.xml"), replica_name="r1")["ok"] is True
    assert [c[0] for c in fake.calls] == ["CreateDatabaseView", "ExportGeodatabaseConfigurationKeywords",
                                          "ExportReplicaSchema"]


def test_add_database_is_a_mutating_live_command(monkeypatch):
    from arcgis_pro_salah_mcp.config import CONFIG
    from arcgis_pro_salah_mcp.live import client, ops as live, policy

    assert "add_database" in policy.MUTATING
    sent = {}
    monkeypatch.setattr(client, "post", lambda command, params=None: sent.update(
        command=command, params=params) or {"ok": True, "data": {"registered": True}})
    assert live.live_add_database(r"C:\p\gis.sde")["ok"] is True
    assert sent == {"command": "add_database", "params": {"path": r"C:\p\gis.sde"}}
    sent.clear()
    monkeypatch.setattr(CONFIG, "bridge_readonly", True)
    assert live.live_add_database(r"C:\p\gis.sde")["code"] == "readonly" and sent == {}


def test_register_toolbox_names_the_add_in_version_for_databases(monkeypatch):
    from arcgis_pro_salah_mcp.live import ops as live
    from arcgis_pro_salah_mcp.pro import register

    monkeypatch.setattr(register, "bridge_listening", lambda: True)
    monkeypatch.setattr(live, "live_add_database", lambda p: {"ok": False, "error": "unknown command 'add_database'"})
    old = register.register_database(r"C:\p\gis.sde")
    assert old["added"] is False and "0.1.5" in old["hint"] and "Add Database" in old["manual"]



# =====================================================================
# Threading: one arcpy thread, a non-blocking server, background jobs, "CURRENT"
# =====================================================================


def test_arcpy_thread_runs_everything_on_one_thread_and_never_deadlocks():
    import threading

    from arcgis_pro_salah_mcp import arcpy_thread

    ids = {arcpy_thread.submit(threading.get_ident).result() for _ in range(5)}
    assert len(ids) == 1 and threading.get_ident() not in ids
    # run() from ON the arcpy thread calls straight through instead of queueing behind itself.
    nested = arcpy_thread.submit(lambda: arcpy_thread.run(threading.get_ident)).result(timeout=5)
    assert nested in ids
    assert arcpy_thread.status()["started"] is True


def test_make_async_keeps_what_fastmcp_reads_from_the_function():
    import asyncio
    import inspect

    from arcgis_pro_salah_mcp import arcpy_thread

    def tool(path: str, limit: int = 3) -> dict:
        """Doc kept."""
        return {"path": path, "limit": limit}

    runner = arcpy_thread.make_async(tool, "tool")
    assert inspect.iscoroutinefunction(runner) and runner.__doc__ == "Doc kept."
    assert list(inspect.signature(runner).parameters) == ["path", "limit"]
    assert asyncio.run(runner(path="a", limit=1)) == {"path": "a", "limit": 1}


def test_every_tool_is_async_and_arcpy_tools_run_on_the_arcpy_thread(monkeypatch):
    import asyncio
    import threading

    from arcgis_pro_salah_mcp import arcpy_thread, server

    tools = server.mcp._tool_manager._tools
    assert all(t.is_async for t in tools.values())          # nothing blocks the event loop any more
    assert tools["pro_analysis"].parameters["properties"]    # schemas untouched by the re-routing

    monkeypatch.setattr(server.pro, "layer_summary", lambda d: {"ok": True, "data": {"thread": threading.get_ident()}})
    res = asyncio.run(server.mcp.call_tool("pro_query", {"action": "summary", "dataset": "x"}))
    blocks = res[0] if isinstance(res, tuple) else res
    payload = json.loads(blocks[0].text)
    assert payload["data"]["thread"] == arcpy_thread._STATE["thread_id"]
    # portal / live / batch tools never take the arcpy thread
    assert not server._runs_on_arcpy("portal_search") and not server._runs_on_arcpy("pro_batch")
    assert server._runs_on_arcpy("pro_egdb_versions") and not server._runs_on_arcpy("pro_server")


def _wait_job(server, job_id):
    import time

    for _ in range(100):
        status = server.pro_server("job", job_id)["data"]
        if status["state"] in ("finished", "failed"):
            return status
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_batch_background_returns_at_once_and_keeps_the_result(monkeypatch):
    import time

    from arcgis_pro_salah_mcp import server

    def slow(**kwargs):
        time.sleep(0.3)
        return {"ok": True, "data": {"seen": kwargs}}

    monkeypatch.setitem(server._ORIGINAL_FN, "pro_query", slow)
    started = time.perf_counter()
    sub = server.pro_batch([{"tool": "pro_query", "arguments": {"dataset": "a"}}], background=True)
    assert sub["ok"] is True and time.perf_counter() - started < 0.2
    status = _wait_job(server, sub["data"]["job_id"])
    assert status["state"] == "finished"
    assert status["result"]["data"]["calls"][0]["result"]["data"]["seen"] == {"dataset": "a"}
    assert server.pro_batch([{"tool": "pro_no_such_tool"}])["code"] == "unknown_tool"
    assert server.pro_batch([{"tool": "pro_batch", "arguments": {}}])["code"] == "unknown_tool"
    assert server.pro_batch([])["code"] == "bad_argument"


def test_batch_passes_results_between_calls_and_stops_on_error(monkeypatch):
    from arcgis_pro_salah_mcp import server

    seen = {}
    monkeypatch.setitem(server._ORIGINAL_FN, "portal_search",
                        lambda **k: {"ok": True, "data": {"items": [{"id": "abc", "n": 3}], "ids": ["x", "y"]}})

    def item(**k):
        seen.update(k)
        return {"ok": True, "data": k}

    monkeypatch.setitem(server._ORIGINAL_FN, "portal_item", item)
    out = server.pro_batch([
        {"tool": "portal_search", "arguments": {"query": "q"}},
        {"tool": "portal_item", "arguments": {"action": "get", "item_id": "{{call1.data.items.0.id}}",
                                             "properties": {"ids": "{{call1.data.ids}}", "note": "n={{prev.data.items.0.n}}"}}},
    ])
    assert out["ok"] is True and out["data"]["succeeded"] == 2
    assert seen["item_id"] == "abc"
    assert seen["properties"] == {"ids": ["x", "y"], "note": "n=3"}   # whole placeholder keeps the list type

    bad = server.pro_batch([
        {"tool": "portal_search", "arguments": {}},
        {"tool": "portal_item", "arguments": {"item_id": "{{call1.data.missing}}"}},
        {"tool": "portal_search", "arguments": {}},
    ])
    assert bad["ok"] is False and bad["code"] == "batch_partial"
    calls = bad["data"]["calls"]
    assert calls[1]["result"]["code"] == "bad_reference" and calls[2].get("skipped") is True
    assert server.pro_batch([{"tool": "portal_search", "arguments": {"q": "{{call1.data}}"}}],
                            parallel=True)["code"] == "bad_argument"


def test_batch_parallel_runs_concurrently(monkeypatch):
    import time

    from arcgis_pro_salah_mcp import server

    def slow(**k):
        time.sleep(0.3)
        return {"ok": True, "data": k}

    monkeypatch.setitem(server._ORIGINAL_FN, "portal_search", slow)
    started = time.perf_counter()
    out = server.pro_batch([{"tool": "portal_search", "arguments": {"query": str(i)}} for i in range(4)],
                           parallel=True)
    assert out["ok"] is True and time.perf_counter() - started < 0.9
    assert [c["result"]["data"]["query"] for c in out["data"]["calls"]] == ["0", "1", "2", "3"]


def test_current_means_the_project_open_in_pro(monkeypatch):
    from types import SimpleNamespace

    from arcgis_pro_salah_mcp.live import client
    from arcgis_pro_salah_mcp.pro import ops

    monkeypatch.setattr(client, "post", lambda *a, **k: {"ok": True, "data": {"project_path": r"C:\p\open.aprx"}})
    assert ops._resolve_current("CURRENT") == r"C:\p\open.aprx"
    assert ops._resolve_current(r"C:\other.aprx") == r"C:\other.aprx"

    class FakeProject:
        def __init__(self, path):
            self.filePath = path

    monkeypatch.setattr(ops, "_arcpy", lambda: SimpleNamespace(mp=SimpleNamespace(ArcGISProject=FakeProject)))
    done = ops.execute_code("print(arcpy.mp.ArcGISProject('CURRENT').filePath)")
    assert done["data"]["stdout"].strip() == r"C:\p\open.aprx" and "overwritten" in done["data"]["notes"][0]

    monkeypatch.setattr(client, "post", lambda *a, **k: {"ok": False, "error": "bridge unreachable"})
    dead = ops.execute_code('aprx = arcpy.mp.ArcGISProject("CURRENT")')
    assert dead["ok"] is False and "outside Pro" in dead["error"]


# =====================================================================
# Portal (ArcGIS API for Python) - all backend-free: fake GIS objects, no arcgis import.
# =====================================================================

class _FakeUser(dict):
    """Behaves like arcgis.gis.User (a dict with attribute access and the write methods we call)."""

    def __init__(self, calls, **props):
        super().__init__(props)
        self._calls = calls
        self._groups = []

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def groups(self):
        return list(self._groups)

    def update_license_type(self, ut):
        self._calls.append(("type", self["username"], ut))
        self["userLicenseTypeId"] = ut
        return True

    def update_role(self, role):
        self._calls.append(("role", self["username"], role))
        if role.startswith("org_"):
            self["role"], self["roleId"] = role, None
        else:
            self["role"], self["roleId"] = "org_user", role
        return True


_FAKE_STATE: dict = {}


class _FakeGroup(dict):
    def __init__(self, gid, title, owner="me"):
        super().__init__(id=gid, title=title, owner=owner, capabilities=[])

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def add_users(self, usernames):
        for u in usernames:
            _FAKE_STATE["users"][u]._groups.append(self)
        return {"notAdded": []}


class _FakeRole:
    def __init__(self, role_id, name, privileges):
        self.role_id, self.name, self.privileges, self.description = role_id, name, privileges, name


class _FakeGIS:
    def __init__(self, is_portal=False, admin=True, license_403=False):
        calls: list = []
        self.calls = calls
        self.url = "https://www.arcgis.com"
        self.properties = {"isPortal": is_portal, "name": "Test Org", "id": "org1"}
        users = {"adminuser": _FakeUser(calls, username="adminuser", role="org_admin", roleId=None,
                                        privileges=["portal:admin:viewUsers"] if admin else [])}
        _FAKE_STATE["users"] = users
        groups = [_FakeGroup("a" * 32, "Field Editors"), _FakeGroup("b" * 32, "QA Team"),
                  _FakeGroup("c" * 32, "Dup"), _FakeGroup("d" * 32, "Dup")]
        roles = [_FakeRole("rGIS000000000000", "Existing Custom", ["portal:user:joinGroup"])]

        class Roles:
            def all(self):
                return list(roles)

            def create(self, name, description, privileges):
                calls.append(("create_role", name))
                r = _FakeRole("rNEW000000000000", name, [p for p in privileges if not p.startswith("bad:")])
                roles.append(r)
                return r

        class Users:
            roles = Roles()

            @property
            def me(self):
                return users["adminuser"]

            @property
            def license_types(self):
                if license_403:
                    raise RuntimeError("You do not have permissions to access this resource (Error Code: 403)")
                return [{"id": "creatorUT", "name": "Creator"}, {"id": "viewerUT", "name": "Viewer"},
                        {"id": "GISProfessionalStdUT", "name": "Professional"}]

            def get(self, username, outside_org=True):
                return users.get(username)

            def search(self, query=None, max_users=100, role=None, user_type=None):
                return list(users.values())

            def create(self, username, password, firstname, lastname, email, role=None, user_type=None,
                       description=None, credits=-1, groups=None, **kw):
                calls.append(("create", username, role, user_type, password))
                builtin = (role or "org_user").startswith("org_")
                users[username] = _FakeUser(calls, username=username,
                                            role=role if builtin and role != "org_admin" else "org_user",
                                            roleId=None if builtin else role, userLicenseTypeId=user_type,
                                            firstName=firstname, lastName=lastname, email=email)
                return None  # Enterprise sometimes returns None on success - the code must re-read

        class Groups:
            def get(self, gid):
                return next((g for g in groups if g.id == gid), None)

            def search(self, query, max_groups=1000, **kw):
                title = query.split('"')[1] if '"' in query else query
                return [g for g in groups if g.title.lower() == title.lower()]

        self.users = Users()
        self.groups = Groups()


def _write_csv(path, rows,
               header="username,password,firstname,lastname,email,user_type,role,groups,credits,description"):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8-sig")
    return path


def test_portal_onboard_validation_rules_block_the_whole_run(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.portal import access, connections

    gis = _FakeGIS(is_portal=True)            # Enterprise: passwords are required
    monkeypatch.setattr(connections, "get", lambda name="default": gis)
    csv_path = _write_csv(tmp_path / "users.csv", [
        "gis.sara01,Change.Me.2026a,\u0633\u0627\u0631\u0629,Doe,sara@example.com,Creator,GIS Specialist,"
        "Field Editors;QA Team,,ok",
        "short,Change.Me.2026b,Mona,Saleh,mona@example.com,Professional,Viewer,,,",           # username too short
        "gis.rsmith03,,Robin,Smith,not-an-email,Wizard,Nope,Dup;Missing,abc,",                   # many problems
        "gis.sara01,Change.Me.2026c,Dup,Row,dup@example.com,Viewer,Viewer,,,",               # duplicate
    ])
    out = access.onboard("validate", str(csv_path))
    assert out["ok"] is False and out["code"] == "invalid_rows"
    rows = {r["line"]: r for r in out["rows"]}
    assert rows[2]["errors"] is None and rows[2]["role"] == "GIS Specialist"      # preset role counts as valid
    assert any("at least 6" in e for e in rows[3]["errors"])
    errs = " | ".join(rows[4]["errors"])
    for needle in ("password is required on ArcGIS Enterprise", "email looks invalid", "unknown user type",
                   "unknown role", "ambiguous", "'Missing' not found", "not a number"):
        assert needle in errs, needle
    assert any("duplicate username" in e for e in rows[5]["errors"])
    report = Path(out["report"]).read_text(encoding="utf-8-sig")
    assert "Change.Me" not in report and "gis.sara01" in report
    assert not any(c[0] == "create" for c in gis.calls)                           # nothing was created
    plans = access.load_csv(csv_path)
    assert plans[0].row["firstname"] == "\u0633\u0627\u0631\u0629"                 # Arabic survives utf-8-sig


def test_portal_onboard_run_needs_confirm_then_creates_type_before_role(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.portal import access, connections

    gis = _FakeGIS(is_portal=False)
    monkeypatch.setattr(connections, "get", lambda name="default": gis)
    csv_path = _write_csv(tmp_path / "u.csv", [
        "gis.jdoe01,,Jane,Doe,jane@example.com,Creator,GIS Specialist,Field Editors,,",
        "gis.boss001,Change.Me.2026x,Boss,One,boss@example.com,creatorUT,Administrator,,,",
        "adminuser,,Ad,Min,a@example.com,Creator,Administrator,,,",                     # already exists
    ])
    pending = access.onboard("run", str(csv_path))
    assert pending["code"] == "confirm_required"
    assert pending["preview"]["summary"]["grants_admin_to"] == ["gis.boss001", "adminuser"]
    assert pending["preview"]["summary"]["existing"] == ["adminuser"]
    assert not any(c[0] == "create" for c in gis.calls)

    done = access.onboard("run", str(csv_path), confirm=True)
    assert done["ok"] is True, done
    assert done["data"]["counts"] == {"created": 2, "skipped": 1}
    creates = [c for c in gis.calls if c[0] == "create"]
    boss = next(c for c in creates if c[1] == "gis.boss001")
    assert boss[2] == "org_user"                                   # never created directly as admin ...
    assert ("role", "gis.boss001", "org_admin") in gis.calls        # ... promoted afterwards
    assert ("create_role", "GIS Specialist") in gis.calls           # preset created because the CSV uses it
    jane_create = next(c for c in creates if c[1] == "gis.jdoe01")
    assert jane_create[2] == "rNEW000000000000"                    # the new role's id, not its name
    assert jane_create[4] is None                                  # Online + blank password = email invitation
    assert [g.title for g in _FAKE_STATE["users"]["gis.jdoe01"].groups] == ["Field Editors"]
    report = Path(done["data"]["report"]).read_text(encoding="utf-8-sig")
    assert "Change.Me" not in report


def test_portal_user_types_fall_back_to_aliases_on_403():
    from arcgis_pro_salah_mcp.portal import access

    assert access.user_type_lookup([])["professional"] == "GISProfessionalStdUT"
    lookup = access.user_type_lookup([{"id": "creatorUT", "name": "Creator"}])
    assert lookup["creator"] == "creatorUT" and "professional" not in lookup      # only types the org has
    ut, _, _, warnings = access._lookups(_FakeGIS(license_403=True))
    assert ut["viewer"] == "viewerUT" and any("403" in w for w in warnings)
    roles, names = access.role_lookup([("rX", "Field Crew")])
    assert roles["field crew"] == "rX" and roles["viewer"] == "iAAAAAAAAAAAAAAA"
    assert names["org_admin"] == "Administrator"
    assert access.normalize_header(" User Type ") == "user_type"
    assert access.role_matches({"role": "org_user", "roleId": "iAAAAAAAAAAAAAAA"}, "iAAAAAAAAAAAAAAA")
    assert not access.role_matches({"role": "org_user", "roleId": "iAAAAAAAAAAAAAAA"}, "org_user")


def test_portal_users_create_requires_confirm_and_never_takes_a_raw_password(monkeypatch):
    from arcgis_pro_salah_mcp.portal import access, connections

    gis = _FakeGIS()
    monkeypatch.setattr(connections, "get", lambda name="default": gis)
    kwargs = dict(firstname="N", lastname="U", email="n@example.com", user_type="Viewer", role="Viewer")
    assert access.users("create", "gis.new001", password_env="Secret Value!", **kwargs)["code"] == \
        "bad_secret_reference"
    monkeypatch.setenv("MCP_TEST_PWD", "S3cret.Value.123")
    pending = access.users("create", "gis.new001", password_env="MCP_TEST_PWD", **kwargs)
    assert pending["code"] == "confirm_required" and "S3cret" not in json.dumps(pending)
    done = access.users("create", "gis.new001", password_env="MCP_TEST_PWD", confirm=True, **kwargs)
    assert done["ok"] is True and done["data"]["verified"] is True
    assert done["data"]["user"]["roleId"] == "iAAAAAAAAAAAAAAA"
    assert access.users("create", "gis.new001", password_env="MCP_TEST_PWD", confirm=True, **kwargs)["code"] == \
        "exists"


def test_portal_safety_secrets_scrub_audit_and_credits(tmp_path, monkeypatch):
    from arcgis_pro_salah_mcp.portal import safety

    monkeypatch.setenv("ARCGIS_SALAH_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("MCP_TEST_SECRET", "very-secret-value")
    value, problem = safety.secret_from_env("MCP_TEST_SECRET")
    assert value == "very-secret-value" and problem is None
    assert safety.secret_from_env("NOT_SET_ANYWHERE_123")[1]["code"] == "secret_missing"
    clean = safety.scrub("url?token=abc.def&x=1 password=hunter22 Authorization: Bearer xyz very-secret-value")
    for leaked in ("abc.def", "hunter22", "xyz", "very-secret-value"):
        assert leaked not in clean
    assert safety.scrub({"token": "t", "nested": ["very-secret-value"]}) == {"token": "***", "nested": ["***"]}
    safety.audit("portal_x", "delete", target="id1", ok=True, details={"code": "pwd=very-secret-value"})
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "very-secret-value" not in line and '"action": "delete"' in line
    assert safety.read_audit(5)[-1]["target"] == "id1"
    assert safety.credit_gate(5, False, "x", True)["code"] == "confirm_required"
    assert safety.credit_gate(5, True, "x", True) is None
    assert safety.credit_gate(5, False, "x", False) is None                     # Enterprise: no credits
    monkeypatch.setenv("ARCGIS_SALAH_CREDIT_LIMIT", "2")
    assert safety.credit_gate(5, True, "x", True)["code"] == "credit_limit"
    monkeypatch.setenv("ARCGIS_SALAH_AUDIT_LOG", "off")
    assert safety.audit_path() is None


def test_portal_python_read_mode_guard():
    from arcgis_pro_salah_mcp.portal import pyexec

    allowed = "items = gis.content.search('owner:me')\nresult = [(i.title, i.size) for i in items]\nprint(len(items))"
    assert pyexec.read_mode_violations(allowed) == []
    for code in ("gis.content.get('x').delete()", "item.update({'title': 'x'})", "lyr.edit_features(adds=[])",
                 "item.sharing.sharing_level = 'EVERYONE'", "import os\nos.remove('f')", "open('x.txt', 'w')",
                 "flc.manager.update_definition({})", "user.update_role('org_admin')", "gis._con.post('u', {})",
                 "eval('1')", "role.privileges = []", "lyr.manager.truncate()", "g.add_users(['a'])"):
        assert pyexec.read_mode_violations(code), code


def test_portal_python_runs_captures_and_gates_write_mode(monkeypatch):
    from arcgis_pro_salah_mcp.portal import pyexec, safety

    monkeypatch.setattr(pyexec, "_namespace", lambda connection, code: {"result": None, "gis": None})
    safety.register_secret("tok-123456")
    out = pyexec.run("print('hi tok-123456')\nresult = {'n': 2}")
    assert out["ok"] is True and out["data"]["result"] == {"n": 2}
    assert out["data"]["stdout"].strip() == "hi ***"
    assert pyexec.run("x.delete()")["code"] == "read_mode_blocked"
    assert pyexec.run("def (:")["code"] == "syntax_error"
    assert pyexec.run("result = 1", mode="write")["code"] == "confirm_required"
    monkeypatch.setenv("ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC", "false")
    assert pyexec.run("result = 1", mode="write", confirm=True)["code"] == "disabled"
    monkeypatch.delenv("ARCGIS_SALAH_ALLOW_PORTAL_WRITE_EXEC")
    assert pyexec.run("raise ValueError('boom')")["code"] == "exception"
    s1 = pyexec.run("counter = 41", session_id="t1")
    s2 = pyexec.run("result = counter + 1", session_id="t1")
    assert s1["ok"] and s2["data"]["result"] == 42


def test_portal_rest_host_allowlist_and_post_confirm(monkeypatch):
    from arcgis_pro_salah_mcp.portal import connections, rest

    hosts = {"www.arcgis.com", "utility.arcgis.com"}
    assert rest.host_allowed("https://services3.arcgis.com/x/FeatureServer/0/query", hosts, True)
    assert not rest.host_allowed("https://evil.example.com/steal", hosts, True)
    assert not rest.host_allowed("https://services3.arcgis.com/x", {"gis.company.com"}, False)
    sent = []

    class Con:
        def get(self, url, params):
            sent.append(("GET", url))
            return {"nextStart": 11, "results": []}

        def post(self, url, params):
            sent.append(("POST", url))
            return {"success": True}

    gis = type("G", (), {"url": "https://www.arcgis.com", "properties": {"isPortal": False, "helperServices": {}},
                         "_con": Con()})()
    monkeypatch.setattr(connections, "get", lambda name="default": gis)
    got = rest.request("portals/self")
    assert got["ok"] and got["data"]["next"] == {"start": 11}
    assert sent[-1][1].endswith("/sharing/rest/portals/self")
    assert rest.request("https://services1.arcgis.com/o/arcgis/rest/services/x/FeatureServer/0/query",
                        method="POST")["ok"]                                      # read-only POST is free
    assert rest.request("content/users/me/items/x/delete", method="POST")["code"] == "confirm_required"
    assert rest.request("https://evil.example.com/x")["code"] == "host_not_allowed"


def test_portal_knowledge_search_flags_deprecations():
    from arcgis_pro_salah_mcp.portal import knowledge

    entries = [
        {"q": "arcgis.gis.ContentManager.add", "k": "method", "s": "(self, item_properties)", "d": "Adds content",
         "dep": "2.3.0 Removed in: 3.0.0. Use Folder.add() instead."},
        {"q": "arcgis.gis._impl._content_manager.Folder.add", "k": "method", "s": "(self, item_properties, file)",
         "d": "Adds an item to the folder"},
        {"q": "arcgis.features.managers.FeatureLayerCollectionManager.create_view", "k": "method", "s": "(name)",
         "d": "Creates a view of the hosted feature layer"},
    ]
    hits = knowledge.search_entries(entries, "add")
    assert hits[0]["qualified_name"].endswith("Folder.add")                  # deprecated one ranks lower
    dep = next(h for h in hits if h["qualified_name"] == "arcgis.gis.ContentManager.add")
    assert dep["deprecated"] and "Folder.add" in dep["use_instead"]
    assert knowledge.search_entries(entries, "create view")[0]["qualified_name"].endswith("create_view")
    assert knowledge.search_entries(entries, "zzz") == []

    def old():
        """Do a thing.

        .. deprecated:: 2.3.0
           Removed in: 3.0.0. Use `Folder.add()` instead.
        """

    summary, deprecated = knowledge._summary(old)
    assert summary == "Do a thing." and "Folder.add" in deprecated
    doc = "Intro\n\n**Parameter**  **Description**\nx  the x\n\n:return: thing\n\n.. code-block:: python\n  f()"
    assert knowledge._section(doc, "parameters").startswith("**Parameter**")
    assert knowledge._section(doc, "examples").startswith(".. code-block")


def test_portal_content_helpers(tmp_path, monkeypatch):
    import zipfile

    from arcgis_pro_salah_mcp.portal import connections, content

    csv_file = tmp_path / "pts.csv"
    csv_file.write_text("name,lat,lon\na,1,2\n", encoding="utf-8")
    assert content._prepare_upload(str(csv_file))[1:3] == ("CSV", "csv")
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        (tmp_path / f"roads{ext}").write_bytes(b"x")
    zipped, kind, _, note = content._prepare_upload(str(tmp_path / "roads.shp"))
    assert kind == "Shapefile" and "sidecar" in note
    assert sorted(zipfile.ZipFile(zipped).namelist()) == ["roads.dbf", "roads.prj", "roads.shp", "roads.shx"]
    gdb = tmp_path / "data.gdb"
    gdb.mkdir()
    (gdb / "a00000001.gdbtable").write_bytes(b"x")
    (gdb / "x.lock").write_bytes(b"x")
    zipped, kind, _, _ = content._prepare_upload(str(gdb))
    assert kind == "File Geodatabase" and all(not n.endswith(".lock") for n in zipfile.ZipFile(zipped).namelist())
    assert content._prepare_upload(str(zipped))[1] == "File Geodatabase"
    props, rejected = content._clean_props({"title": "T", "tags": "a, b", "owner": "x"})
    assert props == {"title": "T", "tags": ["a", "b"]} and rejected == ["owner"]
    assert content.share(["a" * 32], level="world")["code"] == "bad_argument"   # validated before connecting

    captured = {}

    class Content:
        def advanced_search(self, **kw):
            captured.update(kw)
            return {"results": [{"id": "1" * 32, "title": "A", "type": "Web Map", "owner": "me"}], "total": 1,
                    "nextStart": -1}

    me = type("Me", (), {"username": "jdoe_gis"})()
    gis = type("G", (), {"url": "https://www.arcgis.com", "properties": {"isPortal": False, "id": "ORG"},
                         "content": Content(), "users": type("U", (), {"me": me})()})()
    monkeypatch.setattr(connections, "get", lambda name="default": gis)
    res = content.search("title:roads", item_type="Web Map", owner="me")
    assert res["ok"] and res["data"]["items"][0]["item_url"].endswith("id=" + "1" * 32)
    assert "owner:jdoe_gis" in captured["query"] and "orgid:ORG" in captured["query"]
    assert captured["filter"] == 'type:"Web Map"' and res["data"]["next_start"] is None


def test_portal_layer_edit_rules_and_renderers(monkeypatch):
    from arcgis_pro_salah_mcp.portal import connections, layers

    class Feat:
        def __init__(self, attrs):
            self.attributes = attrs

    class FS:
        def __init__(self, feats):
            self.features = [Feat(a) for a in feats]

    edits = []

    class Lyr:
        url = "https://services.arcgis.com/o/arcgis/rest/services/s/FeatureServer/0"
        properties = {"name": "pts", "objectIdField": "OBJECTID", "geometryType": "esriGeometryPoint",
                      "fields": [{"name": "OBJECTID"}, {"name": "KIND"}, {"name": "POP"}],
                      "drawingInfo": {"labelingInfo": [{"x": 1}]}}

        def query(self, where="1=1", return_count_only=False, return_ids_only=False, object_ids=None,
                  out_statistics=None, out_fields=None, **kw):
            if return_count_only:
                return 3
            if return_ids_only:
                return {"objectIds": [1, 2, 3]}
            if object_ids:
                return FS([{"OBJECTID": 1, "KIND": "old"}])
            if out_statistics:
                return FS([{"KIND": "a", "n": 5}, {"KIND": "b", "n": 2}])
            return FS([{"POP": v} for v in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)])

        def edit_features(self, **kw):
            edits.append(kw)
            key = next(k for k in ("adds", "updates", "deletes") if k in kw)
            n = len(kw[key]) if isinstance(kw[key], list) else len(str(kw[key]).split(","))
            return {f"{key[:-1]}Results": [{"success": True, "objectId": i} for i in range(n)]}

    lyr = Lyr()
    monkeypatch.setattr(connections, "get", lambda name="default": object())
    monkeypatch.setattr(layers, "open_layer", lambda gis, target, layer=0: (lyr, None, None))
    assert layers.edit("edit", "t", updates=[{"KIND": "x"}])["code"] == "bad_argument"     # no OBJECTID
    dry = layers.edit("edit", "t", updates=[{"OBJECTID": 1, "KIND": "new"}], dry_run=True)
    assert dry["data"]["preview"]["update_diff"][0]["changes"]["KIND"] == {"from": "old", "to": "new"}
    assert not edits
    assert layers.edit("edit", "t", where="KIND='x'")["code"] == "confirm_required"         # deletes
    done = layers.edit("edit", "t", adds=[{"KIND": "a"}, {"KIND": "b"}], updates=[{"OBJECTID": 1, "KIND": "n"}],
                       batch_size=1)
    assert done["ok"] and done["data"]["result"]["adds"]["succeeded"] == 2 and len(edits) == 3
    monkeypatch.setenv("ARCGIS_SALAH_MAX_EDITS", "1")
    assert layers.edit("edit", "t", adds=[{"a": 1}, {"a": 2}])["code"] == "too_many"
    assert layers.edit("calculate", "t", where="1=1",
                       expressions={"field": "KIND", "value": "z"})["code"] == "confirm_required"

    unique = layers.build_renderer(lyr, {"type": "unique", "field": "KIND"})
    assert [u["value"] for u in unique["uniqueValueInfos"]] == ["a", "b"] and unique["defaultSymbol"] is None
    breaks = layers.build_renderer(lyr, {"type": "class_breaks", "field": "POP", "classes": 5})
    assert [b["classMaxValue"] for b in breaks["classBreakInfos"]] == [2.0, 4.0, 6.0, 8.0, 10.0]
    assert layers.build_renderer(lyr, {"type": "uniqueValue", "field1": "X"})["type"] == "uniqueValue"  # raw JSON
    merged = layers._merge_drawing(lyr, renderer={"type": "simple"})
    assert merged["labelingInfo"] == [{"x": 1}] and merged["renderer"] == {"type": "simple"}


def test_portal_maps_helpers():
    from arcgis_pro_salah_mcp.portal import maps

    assert maps._union([[[1, 2], [3, 4]], None, [[0, 5], [2, 9]]]) == [[0, 2], [3, 9]]
    layers = [{"id": "a1", "title": "Roads", "url": "u1"}, {"id": "b2", "title": "Parcels", "url": "u2"}]
    assert maps._find(layers, "parcels") == 1 and maps._find(layers, 0) == 0
    with pytest.raises(LookupError):
        maps._find(layers, "nope")
    popup = maps._popup({"displayField": "NAME", "fields": [{"name": "OBJECTID"}, {"name": "NAME", "alias": "Name"},
                                                            {"name": "Shape__Area"}]})
    visible = {f["fieldName"]: f["visible"] for f in popup["fieldInfos"]}
    assert popup["title"] == "{NAME}" and visible == {"OBJECTID": False, "NAME": True, "Shape__Area": False}
    summary = maps._summary({"baseMap": {"title": "Topographic"}, "operationalLayers": [
        {"id": "x", "title": "T", "layerType": "ArcGISFeatureLayer", "popupInfo": {}, "layerDefinition": {
            "drawingInfo": {"renderer": {"type": "classBreaks"}}, "definitionExpression": "POP > 5"}}]})
    assert summary["basemap"] == "Topographic" and summary["layers"][0]["renderer"] == "classBreaks"
    assert summary["layers"][0]["definitionExpression"] == "POP > 5"


def test_portal_connections_validate_before_signing_in():
    from arcgis_pro_salah_mcp.portal import connections

    assert connections.connect(name="9bad")["code"] == "bad_name"
    assert connections.connect(source="carrier-pigeon")["code"] == "bad_source"
    assert connections.connect(action="fly")["code"] == "bad_action"
    with pytest.raises(RuntimeError):
        connections.get("never_opened")
    assert connections._looks_expired(RuntimeError("Invalid token. (Error Code: 498)"))
    assert not connections._looks_expired(RuntimeError("403 forbidden"))
    assert connections.connect(action="list")["ok"] is True


def test_toolset_aliases_and_portal_groups():
    from arcgis_pro_salah_mcp import server

    assert server._toolset_of("portal_layer_edit") == "portal_data"
    assert server._toolset_of("portal_users") == "portal_admin"
    assert server._toolset_of("portal_search") == "portal_base"
    assert server._toolset_of("portal_map") == "portal_maps"
    assert server._toolset_of("pro_batch") == "core" and server._toolset_of("pro_egdb_replica") == "egdb"
    assert set(server.TOOLSET_ALIASES["portal"]) == {g for g in server.TOOLSETS if g.startswith("portal_")}


def test_server_merged_tools_validate_arguments_without_a_backend():
    from arcgis_pro_salah_mcp import server

    assert server.pro_layer("add")["code"] == "bad_argument"                      # no data_path
    assert server.pro_layer("teleport")["code"] == "bad_action"
    assert server.pro_analysis("buffer", "roads")["code"] == "bad_argument"       # no out/distance
    assert server.pro_query("rows")["code"] == "bad_argument"                     # no dataset
    assert server.pro_domain("create", workspace="w")["code"] == "bad_argument"
    assert server.live_add_to_project("C:/x/file.txt")["code"] == "bad_argument"
    assert server.webapp_create("t", kind="poster")["code"] == "bad_argument"
    assert server.pro_server("toolsets")["data"]["active_tools"] == len(server.mcp._tool_manager._tools)
    assert server.pro_server("status")["ok"] is True
    assert server.pro_symbology("symbols")["ok"] is True                         # pure: no arcpy needed


def test_no_tool_function_shadows_a_module_alias():
    """A tool named like an imported module alias replaces the module at import time (pro_symbology did:
    every kind but categorized/graduated would have raised AttributeError). Caught by pyflakes, now by a test."""
    import ast

    from arcgis_pro_salah_mcp import server

    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    aliases = {a.asname or a.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
               for a in node.names}
    functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not aliases & functions, aliases & functions


def test_resources_and_prompts_are_served():
    import asyncio
    import re

    from arcgis_pro_salah_mcp import server

    async def go():
        resources = {str(r.uri) for r in await server.mcp.list_resources()}
        prompts = {p.name for p in await server.mcp.list_prompts()}
        guide = await server.mcp.read_resource("arcgis://guide/portal-users")
        index = await server.mcp.read_resource("arcgis://guides")
        rendered = await server.mcp.get_prompt("onboard_users_from_csv", {"csv_path": "C:/u.csv"})
        return resources, prompts, guide, index, rendered

    resources, prompts, guide, index, rendered = asyncio.run(go())
    guide_files = {p.stem for p in server.GUIDES_DIR.glob("*.md")}
    assert {f"arcgis://guide/{g}" for g in guide_files} <= resources
    assert {"arcgis://guides", "arcgis://live/project", "arcgis://portal/my-content"} <= resources
    # status / toolsets / module map are tools (pro_server, portal_api), not duplicated as resources
    assert not {"arcgis://toolsets", "arcgis://server/status", "arcgis://portal/module-map"} & resources
    assert "arcgis://guide/" not in " ".join(m.content.text for m in rendered.messages)  # prompts use the tool
    assert {"plan_big_task", "onboard_users_from_csv", "org_audit", "explore_arcgis_api"} <= prompts
    assert "org_admin" in list(guide)[0].content and "portal-users" in list(index)[0].content
    assert "C:/u.csv" in rendered.messages[0].content.text
    # every tool a playbook or the instructions name must exist
    names = set(server.mcp._tool_manager._tools)
    text = " ".join(p.read_text(encoding="utf-8") for p in server.GUIDES_DIR.glob("*.md")) + server.INSTRUCTIONS
    mentioned = set(re.findall(r"\b((?:pro|portal|live|webapp)_[a-z_]+)\b", text))
    groups = set(server.TOOLSETS) | set(server.TOOLSET_ALIASES)
    unknown = {m for m in mentioned if m not in names and m not in groups and not m.endswith("_")}
    assert not unknown, unknown


def test_playbooks_are_reachable_through_a_tool_in_any_client():
    from arcgis_pro_salah_mcp import server

    topics = server.pro_server("guide")["data"]["topics"]
    assert set(topics) == {p.stem for p in server.GUIDES_DIR.glob("*.md")}
    got = server.pro_server("guide", topic="arcgis://guide/portal-layers.md")["data"]
    assert got["topic"] == "portal-layers" and "create_view" in got["markdown"]
    assert server.pro_server("guide", topic="nope")["code"] == "bad_argument"


def test_portal_item_resource_template_returns_item_context(monkeypatch):
    import asyncio

    from arcgis_pro_salah_mcp import server
    from arcgis_pro_salah_mcp.portal import connections, content

    class Item(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                raise AttributeError(name) from None

        def get_data(self):
            return {"baseMap": {"title": "Topographic"},
                    "operationalLayers": [{"id": "a", "title": "Roads", "layerType": "ArcGISFeatureLayer"}]}

    item = Item(id="a" * 32, title="City map", type="Web Map", owner="me", access="private", typeKeywords=[])
    gis = type("G", (), {"url": "https://www.arcgis.com",
                         "content": type("C", (), {"get": staticmethod(lambda i: item if i == "a" * 32 else None)})()})()
    monkeypatch.setattr(connections, "get", lambda name="default": gis)
    ctx = content.item_context("a" * 32)
    assert ctx["ok"] and ctx["data"]["map"]["basemap"] == "Topographic"
    assert ctx["data"]["map"]["layers"][0]["title"] == "Roads"

    async def go():
        templates = [t.uriTemplate for t in await server.mcp.list_resource_templates()]
        body = await server.mcp.read_resource("arcgis://portal/item/" + "a" * 32)
        missing = await server.mcp.read_resource("arcgis://portal/item/not-an-id")
        return templates, list(body)[0].content, list(missing)[0].content

    templates, body, missing = asyncio.run(go())
    assert "arcgis://portal/item/{item_id}" in templates
    assert json.loads(body)["title"] == "City map"
    assert "not an item id" in json.loads(missing)["error"]                   # error envelope, not a crash


def test_stdio_guard_routes_prints_off_the_protocol_channel(monkeypatch, capsys):
    import io
    import sys
    import threading

    from arcgis_pro_salah_mcp import stdio_guard

    class Real(io.StringIO):
        buffer = object()

    real = Real()
    monkeypatch.setattr(sys, "stdout", real)
    router = stdio_guard.install()
    assert router.buffer is Real.buffer and stdio_guard.install() is router       # idempotent
    print("stray")
    assert real.getvalue() == "" and "stray" in capsys.readouterr().err
    buf = io.StringIO()
    router.buffers[threading.get_ident()] = buf
    print("captured")
    router.buffers.pop(threading.get_ident())
    assert buf.getvalue() == "captured\n"
