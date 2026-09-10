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
