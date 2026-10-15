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
