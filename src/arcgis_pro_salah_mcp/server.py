"""FastMCP server - wires the tool layers, resources and prompts to any MCP client (Claude, Antigravity, ...).

Speaks the standard MCP stdio transport. Run via the installed entry point (recommended), NOT by passing this
file to python directly (package-relative imports would break):

    arcgis-pro-salah-mcp

Tool naming convention:
    pro_*     -> Layer 1,  ArcGIS Pro / ArcPy (desktop, .aprx & geodatabases)
    live_*    -> Layer 1b, the OPEN ArcGIS Pro session via the .NET bridge
    portal_*  -> Layer 2,  ArcGIS API for Python (ArcGIS Online / Enterprise)
    webapp_*  -> Layer 3,  ArcGIS Maps SDK for JavaScript app generator

Related operations share one tool with an ``action`` argument (pro_layer add|remove|rename|...): fewer,
clearer choices for the model, and a smaller schema on every request. Each wrapper is a thin pass-through to
an ``@guard``-ed op; its docstring is the whole spec the model sees.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from ._result import ok as _ok, err as _err

from . import __version__, arcpy_thread, bootstrap, stdio_guard
from .pro import ops as pro
from .pro import context as pro_context_mod
from .pro import pipeline as pro_pipeline_mod
from .pro import cache as pro_cache
from .pro import schema as pro_schema
from .pro import validate as pro_validate
from .pro import geoai as pro_geoai
from .pro import unet as pro_unet
from .pro import toolbox as pro_toolbox
from .pro import modelbuilder as pro_modelbuilder
from .pro import symbology as pro_symbology_mod
from .pro import dl_notebooks as pro_dl
from .pro import egdb as pro_egdb
from .live import ops as live
from .portal import access as p_access
from .portal import admin as p_admin
from .portal import analysis as p_analysis
from .portal import apps as p_apps
from .portal import connections as p_conn
from .portal import content as p_content
from .portal import knowledge as p_knowledge
from .portal import layers as p_layers
from .portal import maps as p_maps
from .portal import pyexec as p_pyexec
from .portal import rest as p_rest
from .webapp import generator as webapp
from .webapp import dashboard as webapp_dashboard
from .webapp import github as webapp_github

INSTRUCTIONS = """\
Drive the full ArcGIS stack end to end: pro_* (ArcPy on .aprx/.gdb) -> portal_* (ArcGIS Online/Enterprise via
the ArcGIS API for Python; signs in through ArcGIS Pro automatically) -> webapp_* (static Maps SDK for JS app).
Every tool returns {"ok": true, "data": ...} or {"ok": false, "error": ..., "code": ...}.
Speed: every tool call is a full round trip, so read with pro_context (one call), chain GP tools with
pro_pipeline, and run several tools of any layer in ONE call with pro_batch ({{callN.data...}} passes results
along). Anything long (geodatabases, maintenance, training, exports, clones) -> background=true, then poll
pro_server(action="job", job_id=...).
code="confirm_required": show the preview to the user, ask, then repeat with confirm=true.
Playbooks: pro_server(action="guide", topic=...) - workflow, performance, portal-users, portal-layers, gotchas,
safety, egdb, ... (no topic lists them; also served as resources arcgis://guide/<topic>).
If you are not 100% sure of an arcgis API, call portal_api(action="doc") before portal_python.
"CURRENT" as a project path = the project open in ArcGIS Pro (resolved through the live bridge).
"""

mcp = FastMCP("ArcGIS_Pro_Salah_MCP", instructions=INSTRUCTIONS)


def _dispatch(tool: str, action: str | None, table: dict[str, Callable[[], dict]]) -> dict:
    fn = table.get((action or "").strip().lower())
    if fn is None:
        return _err(f"{tool}: action must be one of {' | '.join(table)}.", code="bad_action")
    return fn()


def _need(**values: Any) -> dict | None:
    """Cheap argument check BEFORE any backend import (a bad call fails in microseconds)."""
    missing = [k for k, v in values.items() if v in (None, "", [])]
    return _err(f"Missing required argument(s): {', '.join(missing)}.", code="bad_argument") if missing else None


# =====================================================================
# Layer 1 - ArcGIS Pro / ArcPy (core)
# =====================================================================


@mcp.tool()
def pro_context(aprx_path: str | None = None, workspace: str | None = None, datasets: list[str] | None = None,
                sample_rows: int = 3, max_datasets: int = 25, map_name: str | None = None) -> dict:
    """START HERE. Everything about a project/workspace in ONE call: maps, layers, schemas, row counts, sample rows and inferred themes. Give an .aprx ("CURRENT" = the project open in Pro), a .gdb workspace, or explicit datasets."""
    return pro_context_mod.context(aprx_path, workspace, datasets, sample_rows, max_datasets, map_name)


@mcp.tool()
def pro_find_layers(query: str, aprx_path: str | None = None, workspace: str | None = None, limit: int = 10) -> dict:
    """Search layers by meaning ("which layer has population data?") without reading every schema."""
    return pro_context_mod.find_layers(query, aprx_path, workspace, limit)


@mcp.tool()
def pro_project(action: str = "info", aprx_path: str = "CURRENT", map_name: str | None = None,
                copy_to: str | None = None) -> dict:
    """Project: info (maps, layouts, default gdb, CRS) | layers (a map's layers: name, type, CRS, visibility) | save (copy_to = saveACopy). Edits saved from here to the OPEN project are overwritten when Pro saves - use live_* for the open session."""
    return _dispatch("pro_project", action, {
        "info": lambda: pro.project_info(aprx_path),
        "layers": lambda: pro.list_layers(aprx_path, map_name),
        "save": lambda: pro.save_project(aprx_path, copy_to),
    })


@mcp.tool()
def pro_layer(action: str, aprx_path: str = "CURRENT", layer_name: str | None = None, map_name: str | None = None,
              data_path: str | None = None, new_name: str | None = None, visible: bool | None = None,
              opacity: float | None = None) -> dict:
    """Map layers in an .aprx: add (data_path) | remove | rename (new_name) | visibility (visible) | opacity (0.0-1.0)."""
    return _dispatch("pro_layer", action, {
        "add": lambda: _need(data_path=data_path) or pro.add_layer(aprx_path, data_path, map_name),
        "remove": lambda: _need(layer_name=layer_name) or pro.remove_layer(aprx_path, layer_name, map_name),
        "rename": lambda: _need(layer_name=layer_name, new_name=new_name)
        or pro.rename_layer(aprx_path, layer_name, new_name, map_name),
        "visibility": lambda: _need(layer_name=layer_name, visible=visible)
        or pro.set_visibility(aprx_path, layer_name, visible, map_name),
        "opacity": lambda: _need(layer_name=layer_name, opacity=opacity)
        or pro.set_opacity(aprx_path, layer_name, opacity, map_name),
    })


@mcp.tool()
def pro_symbology(kind: str, aprx_path: str = "CURRENT", layer_name: str | None = None, field: str | None = None,
                  classes: int | list[dict] | None = None, color_ramp: str | None = None,
                  map_name: str | None = None, layers: dict | None = None, font: str = "water") -> dict:
    """Layer symbology: categorized (unique values of field) | graduated (numeric field, classes=int, color_ramp) | utility (Esri AM/FM glyphs; classes=[{"value":10,"label":"Valve","symbol":"valve","color":"valve_black","size":14}], lines use width/dash) | water_utility (whole AM/FM water look; layers={"device","line","territory"} names) | symbols (glyph + colour names for utility)."""
    layers = layers or {}
    return _dispatch("pro_symbology", kind, {
        "categorized": lambda: _need(layer_name=layer_name, field=field)
        or pro.apply_categorized_symbology(aprx_path, layer_name, field, map_name),
        "graduated": lambda: _need(layer_name=layer_name, field=field)
        or pro.apply_graduated_symbology(aprx_path, layer_name, field, classes if isinstance(classes, int) else 5,
                                         color_ramp, map_name),
        "utility": lambda: _need(layer_name=layer_name, field=field, classes=classes)
        or pro_symbology_mod.apply_unique_value_symbology(aprx_path, layer_name, field, classes, map_name),
        "water_utility": lambda: pro_symbology_mod.apply_water_utility_symbology(
            aprx_path, layers.get("device", "Water Device"), layers.get("line", "Water Line"),
            layers.get("territory", "ServiceTerritory"), map_name),
        "symbols": lambda: pro_symbology_mod.list_symbols(font),
    })


@mcp.tool()
def pro_query(action: str = "rows", dataset: str = "", where: str | None = None, fields: list[str] | None = None,
              limit: int = 10, order_by: str | None = None, field: str | None = None,
              layer_name: str | None = None) -> dict:
    """Read a feature class/table: rows (attribute rows; where/order_by optional; geometry excluded) | count (rows matching where) | summary (count, fields, geometry, CRS - cached) | describe (infer what the layer is: item summary/description/tags) | stats (count/sum/mean/median/std/min/max of numeric `field`)."""
    if not dataset:
        return _err("dataset is required.", code="bad_argument")
    return _dispatch("pro_query", action, {
        "rows": lambda: pro.sql_query(dataset, where or "1=1", fields, limit, order_by) if (where or order_by)
        else pro.get_features(dataset, fields, limit),
        "count": lambda: pro.select_by_expression(dataset, where or "1=1"),
        "summary": lambda: pro.layer_summary(dataset),
        "describe": lambda: pro.describe_layer(dataset, layer_name),
        "stats": lambda: _need(field=field) or pro.field_statistics(dataset, field, where),
    })


@mcp.tool()
def pro_field(action: str, dataset: str, field: str, field_type: str = "TEXT", expression: str | None = None) -> dict:
    """Fields: add (field_type TEXT/SHORT/LONG/DOUBLE/DATE) | calculate (Python expression, e.g. "!POP! / !AREA!")."""
    return _dispatch("pro_field", action, {
        "add": lambda: pro.add_field(dataset, field, field_type),
        "calculate": lambda: _need(expression=expression) or pro.calculate_field(dataset, field, expression),
    })


@mcp.tool()
def pro_analysis(operation: str, dataset: str | list[str], out: str | None = None, distance: str | None = None,
                 mask: str | None = None, join_features: str | None = None, field: str | None = None,
                 target_crs: str | None = None, where: str | None = None) -> dict:
    """Common geoprocessing: buffer (distance e.g. '100 Meters') | clip (mask) | spatial_join (join_features) | dissolve (field optional) | merge (dataset = list) | reproject (target_crs e.g. 'EPSG:4326') | repair (in place) | extract (where). Several in a row -> pro_pipeline; any other GP tool -> pro_run_gp."""
    first = dataset if isinstance(dataset, str) else (dataset[0] if dataset else "")
    return _dispatch("pro_analysis", operation, {
        "buffer": lambda: _need(out=out, distance=distance) or pro.buffer(first, out, distance),
        "clip": lambda: _need(out=out, mask=mask) or pro.clip(first, mask, out),
        "spatial_join": lambda: _need(out=out, join_features=join_features) or pro.spatial_join(first, join_features, out),
        "dissolve": lambda: _need(out=out) or pro.dissolve(first, out, field),
        "merge": lambda: _need(out=out) or pro.merge(dataset if isinstance(dataset, list) else [dataset], out),
        "reproject": lambda: _need(out=out, target_crs=target_crs) or pro.reproject(first, out, target_crs),
        "repair": lambda: pro.repair_geometry(first),
        "extract": lambda: _need(out=out, where=where) or pro.extract(first, where, out),
    })


@mcp.tool()
def pro_export(out_path: str, dataset: str | None = None, aprx_path: str = "CURRENT", layout_name: str | None = None,
               what: str | None = None, dpi: int | None = None) -> dict:
    """Export: a dataset to a file (format from out_path extension), or a layout to PDF/PNG/JPG (format from extension; dpi), or what="map_series" for a layout's map book as one PDF."""
    kind = (what or ("layer" if dataset else None) or (
        "pdf" if out_path.lower().endswith(".pdf") else "image")).lower()
    return _dispatch("pro_export", kind, {
        "layer": lambda: _need(dataset=dataset) or pro.export_layer(dataset, out_path),
        "pdf": lambda: _need(layout_name=layout_name) or pro.export_pdf(aprx_path, layout_name, out_path, dpi or 300),
        "image": lambda: _need(layout_name=layout_name) or pro.export_image(aprx_path, layout_name, out_path, dpi or 150),
        "map_series": lambda: _need(layout_name=layout_name) or pro.export_map_series(aprx_path, layout_name, out_path),
    })


@mcp.tool()
def pro_run_gp(tool: str, args: list[Any] | None = None, kwargs: dict | None = None) -> dict:
    """Run ANY geoprocessing tool by dotted name, e.g. 'analysis.Buffer' / 'management.Dissolve'."""
    return pro.run_gp(tool, args, kwargs)


@mcp.tool()
def pro_execute_code(code: str) -> dict:
    """Escape hatch: run arbitrary ArcPy Python (`arcpy` pre-imported; print() is returned as stdout)."""
    return pro.execute_code(code)


@mcp.tool()
def pro_pipeline(steps: list[dict], stop_on_error: bool = True, background: bool = False,
                 label: str | None = None) -> dict:
    """Run several GP tools in ONE call, chaining outputs: steps=[{"tool":"analysis.Buffer","args":[...],"kwargs":{...}}]; {{prev}} / {{step2}} inside args = an earlier step's output. background=true returns a job_id at once (poll pro_server action=job)."""
    if background:
        return pro_pipeline_mod.submit(steps, stop_on_error, label)
    return pro_pipeline_mod.pipeline(steps, stop_on_error)


@mcp.tool()
def pro_batch(calls: list[dict], background: bool = False, parallel: bool = False, stop_on_error: bool = True,
              label: str | None = None) -> dict:
    """Run several tools (any layer) in ONE round trip: calls=[{"tool":"portal_publish","arguments":{...}}, {"tool":"portal_map","arguments":{"layers":["{{call1.data.published_item.id}}"]}}]. {{callN.<path>}} = a value from call N's result (1-based; {{prev...}} = the previous call). parallel=true runs independent calls concurrently (no references; ArcPy calls still run one at a time). background=true returns a job_id (poll pro_server action=job) - use it for anything long."""
    if not calls or not isinstance(calls, list) or not all(isinstance(c, dict) and c.get("tool") for c in calls):
        return _err('calls must be a list of {"tool": "<name>", "arguments": {...}}.', code="bad_argument")
    bad = [c["tool"] for c in calls if c["tool"] not in _ORIGINAL_FN or c["tool"] in _NO_BATCH]
    if bad:
        return _err(f"Unknown or unbatchable tool(s): {', '.join(bad)}.", code="unknown_tool")
    if parallel and _REF.search(json.dumps([c.get("arguments") for c in calls], default=str)):
        return _err("parallel=true cannot use {{callN}} references (calls run at the same time).",
                    code="bad_argument")
    if background:
        return pro_pipeline_mod.submit_job(label or f"batch of {len(calls)}",
                                           lambda progress: _run_batch(calls, parallel, stop_on_error, progress),
                                           calls=len(calls))
    return _run_batch(calls, parallel, stop_on_error)


@mcp.tool()
def pro_server(action: str = "status", job_id: str | None = None, topic: str | None = None) -> dict:
    """Server: status (warm-ups, what the arcpy thread is doing and for how long, cache, portal connections, jobs) | job (poll job_id; omit to list jobs) | guide (a playbook by topic, e.g. workflow, performance, portal-users, portal-layers, gotchas, safety, egdb; omit topic to list them - read one before an unfamiliar workflow) | toolsets (tool groups, ARCGIS_SALAH_TOOLSETS) | info (ArcGIS Pro version/licence) | ping (ArcPy reachable)."""
    return _dispatch("pro_server", action, {
        "status": _status,
        "job": lambda: pro_pipeline_mod.job_status(job_id),
        "guide": lambda: _guide(topic),
        "toolsets": _toolsets_report,
        "info": lambda: arcpy_thread.run(pro.get_info),
        "ping": lambda: arcpy_thread.run(pro.ping),
    })


# =====================================================================
# Layer 1d - Geodatabase schema: domains, subtypes, attribute rules
# =====================================================================


@mcp.tool()
def pro_domain(action: str, workspace: str | None = None, domain_name: str | None = None, field_type: str = "TEXT",
               domain_type: str = "CODED", description: str | None = None, coded_values: dict | None = None,
               range_min: float | None = None, range_max: float | None = None, table: str | None = None,
               field_name: str | None = None, subtype_codes: list | None = None) -> dict:
    """Domains: list (workspace) | create (CODED with coded_values {code: description}, or RANGE with range_min/max) | assign (table + field_name, optional subtype_codes) | delete (fails while a field uses it)."""
    return _dispatch("pro_domain", action, {
        "list": lambda: _need(workspace=workspace) or pro_schema.list_domains(workspace),
        "create": lambda: _need(workspace=workspace, domain_name=domain_name) or pro_schema.create_domain(
            workspace, domain_name, field_type, domain_type, description, coded_values, range_min, range_max),
        "assign": lambda: _need(table=table, field_name=field_name, domain_name=domain_name)
        or pro_schema.assign_domain(table, field_name, domain_name, subtype_codes),
        "delete": lambda: _need(workspace=workspace, domain_name=domain_name)
        or pro_schema.delete_domain(workspace, domain_name),
    })


@mcp.tool()
def pro_subtypes(action: str, table: str, subtype_field: str | None = None, subtypes: dict | None = None,
                 default_code: int | None = None) -> dict:
    """Subtypes: describe (field, codes, per-subtype defaults/domains) | set (make subtype_field the subtype field and create all subtypes {code: description} at once)."""
    return _dispatch("pro_subtypes", action, {
        "describe": lambda: pro_schema.describe_subtypes(table),
        "set": lambda: _need(subtype_field=subtype_field, subtypes=subtypes)
        or pro_schema.set_subtypes(table, subtype_field, subtypes, default_code),
    })


@mcp.tool()
def pro_attribute_rules(action: str, table: str | None = None, workspace: str | None = None, name: str | None = None,
                        rule_type: str | None = None, script_expression: str | None = None,
                        field: str | None = None, triggering_events: list[str] | None = None,
                        error_number: int | None = None, error_message: str | None = None,
                        description: str | None = None, subtype: str | None = None, is_editable: bool = True,
                        batch: bool = False, rule_names: list[str] | None = None,
                        evaluation_types: list[str] | None = None, extent: str | None = None) -> dict:
    """Attribute rules: describe (table) | add (CALCULATION needs field + triggering_events; CONSTRAINT/VALIDATION need error_number + error_message; Arcade script_expression) | evaluate (workspace; batch CALCULATION/VALIDATION) | enable | disable (rule_names) | delete (rule_names)."""
    return _dispatch("pro_attribute_rules", action, {
        "describe": lambda: _need(table=table) or pro_schema.describe_attribute_rules(table),
        "add": lambda: _need(table=table, name=name, rule_type=rule_type, script_expression=script_expression)
        or pro_schema.add_attribute_rule(table, name, rule_type, script_expression, field, triggering_events,
                                         error_number, error_message, description, subtype, is_editable, False, batch),
        "evaluate": lambda: _need(workspace=workspace) or pro_schema.evaluate_rules(workspace, evaluation_types, extent),
        "enable": lambda: _need(table=table, rule_names=rule_names)
        or pro_schema.toggle_attribute_rules(table, rule_names, True),
        "disable": lambda: _need(table=table, rule_names=rule_names)
        or pro_schema.toggle_attribute_rules(table, rule_names, False),
        "delete": lambda: _need(table=table, rule_names=rule_names)
        or pro_schema.delete_attribute_rule(table, rule_names, rule_type),
    })


@mcp.tool()
def pro_apply_schema(workspace: str, schema: dict, stop_on_error: bool = True) -> dict:
    """Apply a WHOLE schema in ONE call: {"domains":[...], "tables":[{"table","field_domains","subtype_field","subtypes","rules"}]}."""
    return pro_schema.apply_schema(workspace, schema, stop_on_error)


# =====================================================================
# Layer 1e - Validation: topology + geometry
# =====================================================================


@mcp.tool()
def pro_topology(action: str, topology: str | None = None, geometry: str | None = None,
                 feature_dataset: str | None = None, name: str | None = None, feature_classes: list[str] | None = None,
                 rules: list[dict] | None = None, cluster_tolerance: float = 0, validate: bool = True,
                 visible_extent_only: bool = False, count_errors: bool = True, out_workspace: str | None = None,
                 out_base_name: str | None = None) -> dict:
    """Topology: rules (valid rule strings by geometry pairing - call before create) | describe | create (feature_dataset, name, feature_classes, rules=[{"rule_type","origin","destination"}], validates) | validate (error counts + samples) | export_errors (out_workspace, out_base_name)."""
    return _dispatch("pro_topology", action, {
        "rules": lambda: pro_validate.describe_topology_rules(geometry),
        "describe": lambda: _need(topology=topology) or pro_validate.describe_topology(topology),
        "create": lambda: _need(feature_dataset=feature_dataset, name=name, feature_classes=feature_classes)
        or pro_validate.create_topology(feature_dataset, name, feature_classes, rules, cluster_tolerance, validate),
        "validate": lambda: _need(topology=topology)
        or pro_validate.validate_topology(topology, visible_extent_only, count_errors),
        "export_errors": lambda: _need(topology=topology, out_workspace=out_workspace, out_base_name=out_base_name)
        or pro_validate.export_topology_errors(topology, out_workspace, out_base_name),
    })


@mcp.tool()
def pro_check_geometry(datasets: list[str], out_table: str) -> dict:
    """Find geometry problems (self-intersections, null geometry, bad rings) and summarise them by type."""
    return pro_validate.check_geometry(datasets, out_table)


@mcp.tool()
def pro_validate_all(datasets: list[str] | None = None, topologies: list[str] | None = None,
                     scratch_workspace: str | None = None) -> dict:
    """"Is my data clean?" in ONE call: geometry checks + topology validation + attribute-rule inventory."""
    return pro_validate.validate_all(datasets, topologies, scratch_workspace)


# =====================================================================
# Layer 1f - GeoAI: deep learning, vision-language models, foundation models
# =====================================================================


@mcp.tool()
def pro_geoai_info(what: str = "environment", model_definition: str | None = None, task: str | None = None) -> dict:
    """GeoAI facts: environment (Image Analyst licence, torch, CUDA GPU - call BEFORE any long run) | model (classes, chip size, framework of a .dlpk/.emd) | model_types (valid training model_type values by task and their data format)."""
    return _dispatch("pro_geoai_info", what, {
        "environment": pro_geoai.check_environment,
        "model": lambda: _need(model_definition=model_definition) or pro_geoai.describe_model(model_definition),
        "model_types": lambda: pro_geoai.list_model_types(task),
    })


@mcp.tool()
def pro_geoai_text(in_raster: str, prompt: str, out_features: str | None = None, box_threshold: float = 0.3,
                   text_threshold: float = 0.3, tile_size: int = 512, use_gpu: bool | None = None,
                   extent: str | None = None, apply_nms: bool = True) -> dict:
    """Open-vocabulary detection from plain text ("solar panel"): no model, no training. With out_features it writes a de-duplicated layer (NMS). SLOW on CPU (731 s for one 1024x1024 image) and unreliable in CPU tests (zero-size boxes) - check pro_geoai_info first and verify results before presenting them as locations."""
    if out_features:
        return pro_geoai.workflow_text_to_layer(in_raster, prompt, out_features, box_threshold, text_threshold,
                                                apply_nms)
    return pro_geoai.detect_objects_by_text(in_raster, prompt, None, box_threshold, text_threshold, tile_size, 0,
                                            use_gpu, extent)


@mcp.tool()
def pro_geoai_infer(task: str, model_definition: str | None = None, in_raster: str | None = None,
                    out_features: str | None = None, out_raster: str | None = None, arguments: dict | None = None,
                    processor_type: str | None = None, run_nms: bool = True, in_features: str | None = None,
                    class_label_field: str | None = None, to_raster: str | None = None, in_data: str | None = None,
                    out_embeddings: str | None = None, embedding_features: str | None = None,
                    query_features: str | None = None, threshold: float = 0.8, out_location: str | None = None,
                    out_prefix: str | None = None, pretrained_models: list[str] | None = None,
                    area_of_interest: str | None = None, confidence_threshold: float | None = None) -> dict:
    """Run models on imagery: detect (in_raster, out_features, model_definition; NMS on) | classify_pixels (out_raster and/or out_features) | classify_objects (label existing in_features) | change (in_raster=before, to_raster=after, out_raster) | embeddings (foundation model -> out_embeddings) | similar (embedding_features + query_features -> out_features, threshold) | foundation (Esri pretrained buildings/roads/trees: out_location, out_prefix, pretrained_models). Long runs: pro_batch background=true."""
    md = model_definition
    return _dispatch("pro_geoai_infer", task, {
        "detect": lambda: _need(in_raster=in_raster, out_features=out_features, model_definition=md)
        or pro_geoai.detect_objects(in_raster, out_features, md, arguments, run_nms, "Confidence", "Class", 0.0,
                                    "PROCESS_AS_MOSAICKED_IMAGE", processor_type),
        "classify_pixels": lambda: _need(in_raster=in_raster, model_definition=md)
        or pro_geoai.classify_pixels(in_raster, md, out_raster, out_features, arguments,
                                     "PROCESS_AS_MOSAICKED_IMAGE", processor_type),
        "classify_objects": lambda: _need(in_raster=in_raster, out_features=out_features, model_definition=md)
        or pro_geoai.classify_objects(in_raster, out_features, md, in_features, class_label_field),
        "change": lambda: _need(in_raster=in_raster, to_raster=to_raster, model_definition=md, out_raster=out_raster)
        or pro_geoai.detect_change(in_raster, to_raster, md, out_raster),
        "embeddings": lambda: _need(out_embeddings=out_embeddings, model_definition=md)
        or pro_geoai.generate_embeddings(in_data or in_raster, out_embeddings, md, arguments),
        "similar": lambda: _need(embedding_features=embedding_features, query_features=query_features,
                                 out_features=out_features)
        or pro_geoai.find_similar_features(embedding_features, query_features, out_features, threshold),
        "foundation": lambda: _need(in_raster=in_raster, out_location=out_location, out_prefix=out_prefix)
        or pro_geoai.extract_features_with_foundation_models(in_raster, out_location, out_prefix, pretrained_models,
                                                             area_of_interest, confidence_threshold),
    })


@mcp.tool()
def pro_geoai_train(action: str, in_raster: str | None = None, out_folder: str | None = None,
                    in_class_data: str | None = None, task: str = "object_detection", tile_size: int = 256,
                    class_value_field: str | None = None, in_folder: str | None = None, model_type: str | None = None,
                    max_epochs: int = 20, batch_size: int = 4, backbone_model: str | None = None,
                    arguments: dict | None = None, detected_features: str | None = None,
                    ground_truth_features: str | None = None, out_accuracy_table: str | None = None,
                    out_report: str | None = None, min_iou: float = 0.5, training_labels: str | None = None,
                    work_folder: str | None = None, out_features: str | None = None) -> dict:
    """Training: export (chips from in_raster + in_class_data; format derived from task) | train (in_folder -> out_folder, model_type) | accuracy (detected vs ground truth: mAP/precision/recall) | train_and_detect (labels -> chips -> model -> detections). Long: use pro_batch background=true. For a notebook instead, use pro_dl_notebook."""
    return _dispatch("pro_geoai_train", action, {
        "export": lambda: _need(in_raster=in_raster, out_folder=out_folder, in_class_data=in_class_data)
        or pro_geoai.export_training_data(in_raster, out_folder, in_class_data, task, None, "TIFF", tile_size, None,
                                          class_value_field),
        "train": lambda: _need(in_folder=in_folder, out_folder=out_folder, model_type=model_type)
        or pro_geoai.train_model(in_folder, out_folder, model_type, max_epochs, batch_size, arguments, None,
                                 backbone_model),
        "accuracy": lambda: _need(detected_features=detected_features, ground_truth_features=ground_truth_features,
                                  out_accuracy_table=out_accuracy_table)
        or pro_geoai.compute_accuracy(detected_features, ground_truth_features, out_accuracy_table, out_report, None,
                                      None, min_iou),
        "train_and_detect": lambda: _need(in_raster=in_raster, training_labels=training_labels,
                                          work_folder=work_folder, out_features=out_features)
        or pro_geoai.workflow_train_and_detect(in_raster, training_labels, work_folder, out_features, task,
                                               model_type or "FASTERRCNN", max_epochs),
    })


@mcp.tool()
def pro_dl_notebook(goal: str = "detect buildings", out_path: str | None = None, model: str | None = None,
                    training_data: str | None = None, batch_size: int | None = None, epochs: int | None = None,
                    learning_rate: float | None = None, chip_size: int | None = None,
                    val_split_pct: float | None = None, options: dict | None = None, mixed_precision: bool = False,
                    early_stopping: bool = False, model_name: str | None = None, input_raster: str | None = None,
                    input_features: str | None = None, second_raster: str | None = None,
                    data_path_later: bool = False, overwrite: bool = False) -> dict:
    """Make an arcgis.learn TRAINING notebook for ArcGIS Pro (detect, segment, classify pixels/objects, change detection) to run one cell at a time. Change detection can instead use Esri's ready-made Building Change Detection (no training, needs 10-20 cm before/after images: pass input_raster=before, second_raster=after). CALL FIRST with only `goal`: it replies status=ask_user (recommended model + the questions: batch size, epochs, learning rate...) or status=choose (plain "classification" is ambiguous: pixel / object / detect). ASK the user, never guess, then call again with the answers to get status=created. `options` = model-specific args e.g. {"backbone":"resnet34","pointrend":true}. out_path defaults to the open Pro project's folder. data_path_later=true ONLY if the user said they will type the data path. Add the file in Pro via Catalog > Notebooks > Add and Open Notebook."""
    return pro_dl.notebook(goal, out_path, model, training_data, batch_size, epochs, learning_rate, chip_size,
                           val_split_pct, options, mixed_precision, early_stopping, model_name, input_raster,
                           input_features, second_raster, data_path_later=data_path_later, overwrite=overwrite)


# =====================================================================
# Layer 1g - Utility Network
# =====================================================================


@mcp.tool()
def pro_un_describe(what: str = "model", utility_network: str | None = None, include_rules: bool = False) -> dict:
    """Utility network facts: model (domain networks, tiers, terminals, network attributes, categories in ONE read - start here) | trace_types (the 10 trace types and the question each answers)."""
    return _dispatch("pro_un_describe", what, {
        "model": lambda: _need(utility_network=utility_network) or pro_unet.describe(utility_network, include_rules),
        "trace_types": pro_unet.list_trace_types,
    })


@mcp.tool()
def pro_un_author(action: str, utility_network: str | None = None, feature_dataset: str | None = None,
                  spec: dict | None = None, enable: bool = True, name: str | None = None,
                  service_territory: str | None = None, version: str = "CURRENT",
                  tier_definition: str = "HIERARCHICAL", subnetwork_controller_type: str = "SOURCE",
                  domain_network: str | None = None, rank: int | None = None, topology_type: str = "RADIAL",
                  subnetwork_field: str | None = None, rule_type: str | None = None, from_class: str | None = None,
                  from_assetgroup: str | None = None, from_assettype: str | None = None, to_class: str | None = None,
                  to_assetgroup: str | None = None, to_assettype: str | None = None, from_terminal: str | None = None,
                  to_terminal: str | None = None, field_type: str = "LONG", inline: bool = True,
                  terminals: list[str] | None = None, directional: bool = True) -> dict:
    """Author a utility network: build (WHOLE network from spec, in the order the gdb demands: create -> domain networks -> tiers -> rules -> enable topology) | create (empty, in feature_dataset) | domain_network (name; SOURCE/SINK) | tier (domain_network, name, rank 1 = most upstream, RADIAL/MESH) | rule (connectivity/containment/attachment: from_*/to_* class, assetgroup, assettype, terminals) | network_attribute (name, field_type) | category (name) | terminal_configuration (name, terminals)."""
    un = utility_network
    return _dispatch("pro_un_author", action, {
        "build": lambda: _need(feature_dataset=feature_dataset, spec=spec) or pro_unet.build(feature_dataset, spec, enable),
        "create": lambda: _need(feature_dataset=feature_dataset, name=name, service_territory=service_territory)
        or pro_unet.create(feature_dataset, name, service_territory, version),
        "domain_network": lambda: _need(utility_network=un, name=name)
        or pro_unet.add_domain_network(un, name, tier_definition, subnetwork_controller_type),
        "tier": lambda: _need(utility_network=un, domain_network=domain_network, name=name, rank=rank)
        or pro_unet.add_tier(un, domain_network, name, rank, topology_type, None, subnetwork_field),
        "rule": lambda: _need(utility_network=un, rule_type=rule_type, from_class=from_class, to_class=to_class)
        or pro_unet.add_rule(un, rule_type, from_class, from_assetgroup, from_assettype, to_class, to_assetgroup,
                             to_assettype, from_terminal, to_terminal),
        "network_attribute": lambda: _need(utility_network=un, name=name)
        or pro_unet.add_network_attribute(un, name, field_type, inline),
        "category": lambda: _need(utility_network=un, name=name) or pro_unet.add_category(un, name),
        "terminal_configuration": lambda: _need(utility_network=un, name=name, terminals=terminals)
        or pro_unet.add_terminal_configuration(un, name, terminals, directional),
    })


@mcp.tool()
def pro_un_topology(utility_network: str, action: str = "validate", extent: str | None = None) -> dict:
    """Network topology: enable | disable | validate | verify | repair | analyze. Topology must be DISABLED to change the schema and ENABLED+validated to trace."""
    return _dispatch("pro_un_topology", action, {
        "enable": lambda: pro_unet.enable_topology(utility_network),
        "disable": lambda: pro_unet.disable_topology(utility_network),
        "validate": lambda: pro_unet.validate_topology(utility_network, extent),
        "verify": lambda: pro_unet.verify_topology(utility_network),
        "repair": lambda: pro_unet.repair_topology(utility_network, extent),
        "analyze": lambda: pro_unet.analyze(utility_network),
    })


@mcp.tool()
def pro_un_trace(utility_network: str, action: str = "trace", trace_type: str | None = None,
                 starting_points: str | None = None, barriers: str | None = None, domain_network: str | None = None,
                 tier: str | None = None, subnetwork_name: str | None = None,
                 shortest_path_attribute: str | None = None, result_types: list[str] | None = None,
                 out_lines: str | None = None, out_points: str | None = None, out_json: str | None = None,
                 out_feature_class: str | None = None, from_selection: bool = True, clear_existing: bool = True,
                 as_barrier: bool = False) -> dict:
    """Traces: add_locations (build the starting-points/barriers table from the selected features into out_feature_class - a plain copy is rejected with ERROR 001911) | trace (trace_type CONNECTED, SUBNETWORK, SUBNETWORK_CONTROLLERS, UPSTREAM, DOWNSTREAM, LOOPS, SHORTEST_PATH, ISOLATION, PATH, CIRCUIT; result_types default ["SELECTION"], ["AGGREGATED_GEOMETRY"] + out_lines/out_points writes to disk). Needs an enabled, validated topology."""
    return _dispatch("pro_un_trace", action, {
        "add_locations": lambda: _need(out_feature_class=out_feature_class) or pro_unet.add_trace_locations(
            utility_network, out_feature_class, from_selection, clear_existing, as_barrier),
        "trace": lambda: _need(trace_type=trace_type) or pro_unet.trace(
            utility_network, trace_type, starting_points, barriers, domain_network, tier, None, subnetwork_name,
            shortest_path_attribute, False, False, False, True, True, None, None, None, result_types, out_points,
            out_lines, None, out_json),
    })


@mcp.tool()
def pro_un_subnetwork(utility_network: str, domain_network: str, tier: str, action: str = "update",
                      subnetwork_name: str | None = None, all_in_tier: bool = False, out_json: str | None = None) -> dict:
    """Subnetworks: update (recompute so SUBNETWORK traces are current) | export (subnetwork_name -> out_json)."""
    return _dispatch("pro_un_subnetwork", action, {
        "update": lambda: pro_unet.update_subnetwork(utility_network, domain_network, tier, subnetwork_name, all_in_tier),
        "export": lambda: _need(subnetwork_name=subnetwork_name, out_json=out_json)
        or pro_unet.export_subnetwork(utility_network, domain_network, tier, subnetwork_name, out_json),
    })


# =====================================================================
# Layer 1h - Toolbox authoring: Python toolboxes (.pyt) and ModelBuilder (.atbx)
# =====================================================================


@mcp.tool()
def pro_create_python_toolbox(out_path: str, label: str, tools: list[dict], alias: str | None = None,
                              description: str | None = None, register_in_project: bool = True) -> dict:
    """PREFERRED way to turn a workflow into a toolbox tool: writes a working .pyt (handles any tool, multi-input like Intersect, any intermediate) and adds it to the open Pro project. tools: [{"name","label","parameters":[{"name","label","datatype","direction","default"}], "steps":[{"tool":"analysis.Clip","kwargs":{"in_features":"{{param}}","out_feature_class":"{{memory}}"}}] or "execute_code"}]; {{stepN}}=earlier output, {{memory}}=temp. First load shows Pro's 'trust' dialog. See pro_toolbox_info."""
    return pro_toolbox.create_python_toolbox(out_path, label, tools, alias, description,
                                             register_in_project=register_in_project)


@mcp.tool()
def pro_create_model(out_path: str, toolbox_label: str, model_name: str, steps: list[dict],
                     parameters: list[dict] | None = None, model_label: str | None = None,
                     description: str | None = None, allow_unverified: bool = False,
                     register_in_project: bool = True) -> dict:
    """Write an editable ModelBuilder model (.atbx) - ONLY linear chains of feature-class tools are verified; multi-input tools (Intersect/Union/Merge) and folder/file/layer intermediates are refused (use pro_create_python_toolbox). steps: [{"tool":"analysis.Buffer","params":{"in_features":"{{roads}}"},"output_param":"out_feature_class","output":"{{result}}"}]. {{param}}=declared parameter, {{stepN}}=step N's output. Adds it to the open project."""
    return pro_modelbuilder.create_model(out_path, toolbox_label, model_name, steps, parameters, model_label,
                                         description, allow_unverified=allow_unverified,
                                         register_in_project=register_in_project)


@mcp.tool()
def pro_toolbox_info(what: str = "datatypes", atbx_path: str | None = None) -> dict:
    """Toolbox reference: datatypes (valid parameter datatypes/types/directions for the two creators) | model (read back any .atbx - tools, parameters, full model graph; Esri's own too)."""
    return _dispatch("pro_toolbox_info", what, {
        "datatypes": pro_toolbox.list_datatypes,
        "model": lambda: _need(atbx_path=atbx_path) or pro_modelbuilder.inspect_model(atbx_path),
    })


# =====================================================================
# Enterprise geodatabase (pro_egdb_*): create -> users -> versioning -> archiving -> maintenance -> replication
# Passwords are passed as ENV VAR NAMES (e.g. "EGDB_SDE_PWD"), never as plain text.
# Irreversible operations refuse with code="confirm_required" until called with confirm=True.
# =====================================================================


@mcp.tool()
def pro_egdb_setup(action: str, database_platform: str | None = None, instance: str | None = None,
                   database_name: str | None = None, authorization_file: str | None = None,
                   dba_user: str | None = None, dba_password_env: str | None = None,
                   gdb_admin_password_env: str | None = None, gdb_admin_name: str = "sde",
                   account_authentication: str = "DATABASE_AUTH", schema_type: str = "SDE_SCHEMA",
                   tablespace: str | None = None, spatial_type: str | None = None, admin_connection: str | None = None,
                   service: str | None = None, sde_password_env: str | None = None, execute: bool = False,
                   upgrade: bool = False, confirm: bool = False) -> dict:
    """Geodatabase setup: create (self-managed SQL_SERVER/POSTGRESQL/ORACLE with DBA rights; authorization_file = the user's OWN keycodes or env ARCGIS_EGDB_AUTHORIZATION_FILE; passwords as env-var names; OS auth needs no DBA password, SQL Server DBO_SCHEMA no gdb-admin password) | enable (existing database via an admin .sde: cloud DBaaS, Db2, SAP HANA...) | prepare_cloud (Esri's database-side SQL before enable; database_platform + service RDS/AURORA/AZURE/CLOUD_SQL/AZURE_SQL_DATABASE/AZURE_SQL_MANAGED_INSTANCE; execute=true+confirm runs it) | upgrade (check by default; upgrade=true is irreversible: back up, confirm). Long: pro_batch background=true."""
    return _dispatch("pro_egdb_setup", action, {
        "create": lambda: pro_egdb.create_gdb(database_platform, instance, database_name, authorization_file,
                                              dba_user, dba_password_env, gdb_admin_password_env, gdb_admin_name,
                                              account_authentication, schema_type, tablespace, spatial_type),
        "enable": lambda: pro_egdb.enable_gdb(admin_connection, authorization_file),
        "prepare_cloud": lambda: pro_egdb.prepare_cloud_database(database_platform, service, admin_connection,
                                                                 sde_password_env, execute, confirm),
        "upgrade": lambda: pro_egdb.upgrade_gdb(admin_connection, upgrade, confirm),
    })


@mcp.tool()
def pro_egdb_connection(action: str = "create", connection: str | None = None, out_folder: str | None = None,
                        out_name: str | None = None, database_platform: str | None = None,
                        instance: str | None = None, user: str | None = None, password_env: str | None = None,
                        database: str | None = None, account_authentication: str = "DATABASE_AUTH",
                        version: str | None = None, version_type: str | None = None, save_credentials: bool = True,
                        date: str | None = None, register_in_project: bool = True) -> dict:
    """Connections: create (.sde file, added to the open Pro project's Databases; version_type TRANSACTIONAL|HISTORICAL|POINT_IN_TIME|BRANCH + version; POINT_IN_TIME needs date 'YYYY-MM-DD HH:MM:SS': UTC for non-versioned archives, server-local for versioned; count with a cursor, GetCount ignores the moment) | describe (connection: DBMS, release, user, version, datasets)."""
    return _dispatch("pro_egdb_connection", action, {
        "create": lambda: pro_egdb.create_connection(out_folder, out_name, database_platform, instance, user,
                                                     password_env, database, account_authentication, version,
                                                     version_type, save_credentials, date, register_in_project),
        "describe": lambda: _need(connection=connection) or pro_egdb.describe(connection),
    })


@mcp.tool()
def pro_egdb_principals(action: str, admin_connection: str, name: str, password_env: str | None = None,
                        role: str | None = None, users: list[str] | None = None, grant: bool = True,
                        tablespace: str | None = None, os_authenticated: bool = False, confirm: bool = False) -> dict:
    """Database users and roles: create_user (data owner/editor/viewer, optional role) | create_role (grant/revoke it to users) | drop_user | drop_role (confirm; never cascades; a logged-in SQL Server user is refused with the KILL statements a DBA must run) | set_password (password_env). SQL Server, PostgreSQL, Oracle."""
    return _dispatch("pro_egdb_principals", action, {
        "create_user": lambda: pro_egdb.create_user(admin_connection, name, password_env, role, tablespace,
                                                    os_authenticated),
        "create_role": lambda: pro_egdb.create_role(admin_connection, name, users, grant),
        "drop_user": lambda: pro_egdb.manage_principal(admin_connection, "drop_user", name, password_env, confirm),
        "drop_role": lambda: pro_egdb.manage_principal(admin_connection, "drop_role", name, password_env, confirm),
        "set_password": lambda: pro_egdb.manage_principal(admin_connection, "set_password", name, password_env,
                                                          confirm),
    })


@mcp.tool()
def pro_egdb_privileges(datasets: list[str], principal: str, view: str = "GRANT", edit: str | None = None,
                        grant: list[str] | None = None, revoke: list[str] | None = None,
                        connection: str | None = None) -> dict:
    """Dataset privileges (connect as the data owner). Default: ArcGIS's Change Privileges - view=SELECT, edit=INSERT+UPDATE+DELETE together (GRANT|REVOKE|AS_IS); works on versioned/archived data. With grant/revoke lists (SELECT/INSERT/UPDATE/DELETE individually) + connection: simple tables only (versioned/archived/attachment datasets are refused)."""
    if grant or revoke:
        return _need(connection=connection) or pro_egdb.table_privileges(connection, datasets, principal, grant, revoke)
    return pro_egdb.change_privileges(datasets, principal, view, edit)


@mcp.tool()
def pro_egdb_dataset(action: str, dataset: str, global_ids: bool = True, editor_tracking: bool = False,
                     attachments: bool = False, register: bool = False, move_edits_to_base: bool = False,
                     compress_default: bool = True, enable: bool = True, confirm: bool = False) -> dict:
    """Prepare data for multiuser work: prepare (Register With Geodatabase, Global IDs - needed for replication -, editor tracking, attachments) | register_versioned (traditional via a TRANSACTIONAL connection, branch via a BRANCH one; replication needs move_edits_to_base=false) | unregister_versioned (irreversible: confirm) | archiving (enable=true/false; disabling keeps the history table)."""
    return _dispatch("pro_egdb_dataset", action, {
        "prepare": lambda: pro_egdb.prepare_dataset(dataset, global_ids, editor_tracking, attachments, register),
        "register_versioned": lambda: pro_egdb.register_versioned(dataset, move_edits_to_base),
        "unregister_versioned": lambda: pro_egdb.unregister_versioned(dataset, compress_default, confirm),
        "archiving": lambda: pro_egdb.enable_archiving(dataset, enable),
    })


@mcp.tool()
def pro_egdb_versions(action: str, connection: str, name: str | None = None, parent: str | None = None,
                      access: str | None = None, version: str | None = None, new_name: str | None = None,
                      description: str | None = None, edit_versions: list[str] | None = None,
                      target_version: str | None = None, post: bool = True, conflict_definition: str = "BY_OBJECT",
                      conflict_resolution: str = "FAVOR_TARGET_VERSION", delete_versions: bool = False,
                      log_file: str | None = None, blocking_only: bool = False, health_action: str = "diagnose",
                      scope: str = "tables", tables: list[str] | None = None, confirm: bool = False) -> dict:
    """Versions: list | create (name; access PUBLIC|PROTECTED|PRIVATE; parent defaults to the gdb's DEFAULT - sde.DEFAULT or dbo.DEFAULT) | alter (version: new_name/description/access, e.g. protect DEFAULT) | delete (confirm) | reconcile_post (edit_versions default all others, target default DEFAULT; returns reconciled/posted/conflicts read from the log; delete_versions needs confirm) | health (health_action diagnose (read-only) | repair (confirm); scope tables|metadata)."""
    return _dispatch("pro_egdb_versions", action, {
        "list": lambda: pro_egdb.list_versions(connection),
        "create": lambda: _need(name=name) or pro_egdb.create_version(connection, name, parent, access or "PUBLIC"),
        "alter": lambda: _need(version=version) or pro_egdb.alter_version(connection, version, new_name, description,
                                                                          access),
        "delete": lambda: _need(version=version) or pro_egdb.delete_version(connection, version, confirm),
        "reconcile_post": lambda: pro_egdb.reconcile_post(connection, edit_versions, target_version, post,
                                                          conflict_definition, conflict_resolution, delete_versions,
                                                          log_file, blocking_only, confirm),
        "health": lambda: pro_egdb.version_health(connection, health_action, scope, target_version, tables, confirm),
    })


@mcp.tool()
def pro_egdb_admin(action: str, connection: str, users: list[int] | None = None, all_users: bool = False,
                   compress: bool = True, analyze: bool = True, rebuild_indexes: bool = True,
                   datasets: list[str] | None = None, view_name: str | None = None, definition: str | None = None,
                   keywords_action: str | None = None, file: str | None = None, confirm: bool = False) -> dict:
    """Administration (connect as the gdb admin): connected_users | disconnect (session IDs from connected_users, or all_users=true; confirm) | accept_connections | block_connections (e.g. before an upgrade; confirm) | maintenance (compress + analyze + rebuild indexes incl. system tables) | create_view (view_name + SQL SELECT definition, owned by the connected user) | config_keywords (keywords_action export|import, file)."""
    return _dispatch("pro_egdb_admin", action, {
        "connected_users": lambda: pro_egdb.connected_users(connection),
        "disconnect": lambda: pro_egdb.disconnect_users(connection, users, all_users, confirm),
        "accept_connections": lambda: pro_egdb.accept_connections(connection, True, confirm),
        "block_connections": lambda: pro_egdb.accept_connections(connection, False, confirm),
        "maintenance": lambda: pro_egdb.maintenance(connection, compress, analyze, rebuild_indexes, datasets),
        "create_view": lambda: _need(view_name=view_name, definition=definition)
        or pro_egdb.create_view(connection, view_name, definition),
        "config_keywords": lambda: _need(keywords_action=keywords_action, file=file)
        or pro_egdb.config_keywords(connection, keywords_action, file),
    })


@mcp.tool()
def pro_egdb_replica(action: str, geodatabase: str | None = None, replica_name: str | None = None,
                     datasets: list[str] | None = None, child_workspace: str | None = None,
                     replica_type: str = "ONE_WAY_REPLICA", access_type: str = "FULL",
                     initial_data_sender: str = "PARENT_DATA_SENDER", expand: str = "USE_DEFAULTS",
                     reuse_schema: str = "DO_NOT_REUSE", get_related: str = "GET_RELATED", geometry: str | None = None,
                     archiving: str = "DO_NOT_USE_ARCHIVING", geodatabase_2: str | None = None,
                     direction: str = "FROM_GEODATABASE1_TO_2", conflict_policy: str | None = None,
                     conflict_definition: str = "BY_OBJECT", reconcile: str = "DO_NOT_RECONCILE",
                     out_file: str | None = None, message_file: str | None = None,
                     out_acknowledgement: str | None = None, switch_to_receiver: str = "DO_NOT_SWITCH",
                     include_unacknowledged: str = "UNACKNOWLEDGED", include_new: str = "NEW_CHANGES",
                     file: str | None = None, confirm: bool = False) -> dict:
    """Replication: create (datasets -> child_workspace; CHECK_OUT|ONE_WAY_REPLICA|TWO_WAY_REPLICA|ONE_WAY_CHILD_TO_PARENT_REPLICA; checks versioned data with GlobalIDs, enterprise child for two-way) | list (geodatabase) | synchronize (geodatabase + geodatabase_2) | export_changes / import_message / export_acknowledgement (disconnected sync steps 1-3) | unregister (irreversible: confirm) | schema_export (replica_name -> file) | schema_compare (file -> out_file) | schema_import (file)."""
    gdb = geodatabase
    return _dispatch("pro_egdb_replica", action, {
        "create": lambda: _need(datasets=datasets, child_workspace=child_workspace, replica_name=replica_name)
        or pro_egdb.create_replica(datasets, child_workspace, replica_name, replica_type, access_type,
                                   initial_data_sender, expand, reuse_schema, get_related, geometry, archiving),
        "list": lambda: _need(geodatabase=gdb) or pro_egdb.list_replicas(gdb),
        "synchronize": lambda: _need(geodatabase=gdb, replica_name=replica_name, geodatabase_2=geodatabase_2)
        or pro_egdb.synchronize(gdb, replica_name, geodatabase_2, direction, conflict_policy or "IN_FAVOR_OF_GDB1",
                                conflict_definition, reconcile),
        "export_changes": lambda: _need(geodatabase=gdb, out_file=out_file, replica_name=replica_name)
        or pro_egdb.export_changes(gdb, out_file, replica_name, switch_to_receiver, include_unacknowledged,
                                   include_new),
        "import_message": lambda: _need(geodatabase=gdb, message_file=message_file)
        or pro_egdb.import_message(gdb, message_file, out_acknowledgement,
                                   conflict_policy or "IN_FAVOR_OF_IMPORTED_CHANGES", conflict_definition),
        "export_acknowledgement": lambda: _need(geodatabase=gdb, out_file=out_file, replica_name=replica_name)
        or pro_egdb.export_acknowledgement(gdb, out_file, replica_name),
        "unregister": lambda: _need(geodatabase=gdb, replica_name=replica_name)
        or pro_egdb.unregister_replica(gdb, replica_name, confirm),
        "schema_export": lambda: _need(geodatabase=gdb, file=file, replica_name=replica_name)
        or pro_egdb.replica_schema(gdb, "export", file, replica_name, out_file),
        "schema_compare": lambda: _need(geodatabase=gdb, file=file, out_file=out_file)
        or pro_egdb.replica_schema(gdb, "compare", file, replica_name, out_file),
        "schema_import": lambda: _need(geodatabase=gdb, file=file)
        or pro_egdb.replica_schema(gdb, "import", file, replica_name, out_file),
    })


# =====================================================================
# Layer 1b - Live session (the OPEN ArcGIS Pro session via the .NET bridge)
# Loopback HTTP to the SalahAIBridge add-in (see docs/PROTOCOL.md). If Pro or the add-in isn't running
# these return {"ok": false, "error": "bridge unreachable: ..."} rather than failing.
# =====================================================================


@mcp.tool()
def live_ping() -> dict:
    """Liveness + context of the OPEN ArcGIS Pro session (project path, maps, layouts, add-in version)."""
    return live.live_ping()


@mcp.tool()
def live_map(action: str = "layers", map_name: str | None = None, layer: str | None = None, selection: bool = False,
             where: str | None = None, fields: list[str] | None = None, limit: int = 10, path: str | None = None) -> dict:
    """The open Pro session's map: layers (active or map_name) | query (rows from layer: where, fields, limit) | zoom (to layer, or its selection) | add_layer (dataset path from disk; refused in read-only mode)."""
    return _dispatch("live_map", action, {
        "layers": lambda: live.live_list_layers(map_name),
        "query": lambda: _need(layer=layer) or live.live_query(layer, where, fields, limit),
        "zoom": lambda: _need(layer=layer) or live.live_zoom_to(layer, selection),
        "add_layer": lambda: _need(path=path) or live.live_add_layer(path),
    })


@mcp.tool()
def live_run_gp(tool: str, args: list[Any] | None = None, kwargs: dict | None = None, confirm: bool = False) -> dict:
    """Run a geoprocessing tool LIVE inside Pro; output is added to the active map. Destructive: pass confirm=True (refused in read-only mode). Needs add-in 0.1.6+."""
    return live.live_run_gp(tool, args, kwargs, confirm)


@mcp.tool()
def live_export_layout(layout: str, out_path: str, dpi: int = 300) -> dict:
    """Export a layout from the open Pro session to a PDF (refused in read-only mode)."""
    return live.live_export_layout(layout, out_path, dpi)


@mcp.tool()
def live_get_request(clear: bool = False) -> dict:
    """Fetch what the user queued from the Salah MCP ribbon (Create Web App prompt / Publish). clear=True consumes it."""
    return live.live_get_request(clear)


@mcp.tool()
def live_add_to_project(path: str) -> dict:
    """Add an existing file to the OPEN Pro project: .sde/.gdb -> Databases (add-in 0.1.5+), .atbx/.tbx/.pyt -> Toolboxes (add-in 0.1.4+; a .pyt shows Pro's trust dialog the first time)."""
    ext = os.path.splitext(path.rstrip("\\/"))[1].lower()
    if ext in (".sde", ".gdb"):
        return live.live_add_database(path)
    if ext in (".atbx", ".tbx", ".pyt"):
        return live.live_add_toolbox(path)
    return _err("path must end in .sde, .gdb, .atbx, .tbx or .pyt.", code="bad_argument")


# =====================================================================
# Layer 2 - ArcGIS Online / Enterprise (ArcGIS API for Python). Signs in through ArcGIS Pro first.
# Every tool takes `connection` (default "default", opened automatically on first use).
# =====================================================================


@mcp.tool()
def portal_connect(action: str = "connect", name: str = "default", source: str = "auto", url: str | None = None,
                   profile: str | None = None, username: str | None = None, password_env: str | None = None,
                   api_key_env: str | None = None, verify_cert: bool = True) -> dict:
    """Portal sign-in: connect | whoami (user, role, user type, what it can do, platform, credits) | list | disconnect. source=auto tries the ArcGIS Pro sign-in first (Online or Enterprise, SSO included), then profile (or ARCGIS_PROFILE), then url+username+password_env (env var NAME, never the password), then api_key_env, then anonymous. Named connections (name="source"/"target") let tools work across orgs. Other portal tools connect "default" by themselves."""
    return p_conn.connect(action, name, source, url, profile, username, password_env, api_key_env, verify_cert)


@mcp.tool()
def portal_search(query: str = "", item_type: str | None = None, owner: str | None = None,
                  tags: list[str] | None = None, max_items: int = 20, start: int = 1, sort_field: str | None = None,
                  sort_order: str = "asc", outside_org: bool = False, connection: str = "default") -> dict:
    """Search items (newest first): query syntax title:"Parcels" AND tags:roads; item_type exact ("Feature Service", "Web Map", "CSV", "Dashboard", ...); owner ("me" ok). Org-only unless outside_org. Page with start/next_start. Titles are not unique - continue with ids."""
    return p_content.search(query, item_type, owner, tags, max_items, start, sort_field, sort_order, outside_org,
                            connection)


@mcp.tool()
def portal_item(action: str, item_id: str, properties: dict | None = None, data: Any = None,
                thumbnail: str | None = None, folder: str | None = None, title: str | None = None,
                export_format: str | None = None, export_parameters: dict | None = None,
                out_folder: str | None = None, target_owner: str | None = None, permanent: bool = False,
                keep_export: bool = False, max_chars: int = 20000, confirm: bool = False,
                connection: str = "default") -> dict:
    """One item: get (+ sharing, layers, is_hosted/is_view) | data (its JSON) | update (properties: title/snippet/description/tags..., data, thumbnail; re-read and compared) | move (folder) | copy (title, folder) | protect | unprotect | delete (confirm) | dependencies | download (out_folder) | export (export_format File Geodatabase/Shapefile/CSV/GeoJson/Excel/KML -> downloaded; temp export item removed unless keep_export) | reassign (target_owner, confirm) | usage."""
    return p_content.item(action, item_id, properties, data, thumbnail, folder, title, export_format,
                          export_parameters, out_folder, target_owner, permanent, keep_export, max_chars, confirm,
                          connection)


@mcp.tool()
def portal_folders(action: str = "list", name: str | None = None, new_name: str | None = None,
                   owner: str | None = None, confirm: bool = False, connection: str = "default") -> dict:
    """Folders: list | create (no-op if it exists) | rename (new_name) | delete (confirm; items inside go too)."""
    return p_content.folders(action, name, new_name, owner, confirm, connection)


@mcp.tool()
def portal_publish(path: str, title: str, item_type: str | None = None, tags: list[str] | None = None,
                   folder: str | None = None, snippet: str | None = None, description: str | None = None,
                   publish: bool = True, latitude_field: str | None = None, longitude_field: str | None = None,
                   address_fields: dict | None = None, publish_parameters: dict | None = None,
                   confirm: bool = False, connection: str = "default") -> dict:
    """Upload a local file and publish it as a hosted feature layer: CSV/Excel (latitude_field+longitude_field, or address_fields = geocoding credits, confirm), shapefile (.shp auto-zipped), .gdb folder (zipped), a feature class inside a .gdb/.sde (exported for you), GeoPackage, GeoJSON, zip, .sd. publish=false only uploads. Returns the source and published items with layer URLs."""
    return p_content.publish(path, title, item_type, tags, folder, snippet, description, publish, latitude_field,
                             longitude_field, address_fields, publish_parameters, confirm, connection)


@mcp.tool()
def portal_share(item_ids: list[str], level: str | None = None, add_groups: list[str] | None = None,
                 remove_groups: list[str] | None = None, confirm: bool = False, connection: str = "default") -> dict:
    """Share items: level private|org|everyone (everyone = public, needs confirm), add_groups / remove_groups (ids or exact titles). Verified per item (before/after)."""
    return p_content.share(item_ids, level, add_groups, remove_groups, confirm, connection)


@mcp.tool()
def portal_clone(item_ids: list[str], folder: str | None = None, target_connection: str = "default",
                 search_existing_items: bool = True, copy_data: bool = True, item_mapping: dict | None = None,
                 confirm: bool = False, connection: str = "default") -> dict:
    """Clone items (with what they use) into a folder or another org (connection=source, target_connection=target): returns a preview first, then confirm=True. search_existing_items reuses earlier clones; item_mapping {source_id: target_id}."""
    return p_content.clone(item_ids, folder, target_connection, search_existing_items, copy_data, item_mapping,
                           confirm, connection)


@mcp.tool()
def portal_layer_query(action: str = "query", target: str = "", layer: int | str | None = 0, where: str = "1=1",
                       fields: list[str] | None = None, limit: int = 50, offset: int = 0, order_by: str | None = None,
                       return_geometry: bool = False, stats: list | None = None, group_by: str | None = None,
                       distinct: bool = False, out_sr: int | None = None, connection: str = "default") -> dict:
    """Read a hosted/feature layer (target = item id + layer index/name, or a .../FeatureServer/N URL): describe (fields+domains, capabilities, count, is_hosted, is_view, view filter) | query (paged rows: limit<=2000, offset/next_offset; dates as ISO UTC) | count | stats (["sum:POP","avg:INCOME"], group_by)."""
    return p_layers.query(action, target, layer, where, fields, limit, offset, order_by, return_geometry, stats,
                          group_by, distinct, out_sr, connection)


@mcp.tool()
def portal_layer_edit(action: str = "edit", target: str = "", layer: int | str | None = 0, adds: list | None = None,
                      updates: list | None = None, deletes: list | None = None, where: str | None = None,
                      expressions: list | dict | None = None, source_item_id: str | None = None,
                      upload_format: str | None = None, upsert: bool = False, upsert_matching_field: str | None = None,
                      field_mappings: list | None = None, use_global_ids: bool = False,
                      rollback_on_failure: bool = True, batch_size: int = 500, dry_run: bool = False,
                      confirm: bool = False, connection: str = "default") -> dict:
    """Change layer data: edit (adds/updates as attribute dicts or features - updates need OBJECTID; deletes = ids or where (confirm); batched, each result checked; dry_run previews a diff) | calculate (where + expressions [{"field","value"}|{"field","sqlExpression"}]; confirm) | append (source_item_id + upload_format csv/shapefile/filegdb/geojson/excel, upsert; confirm) | truncate (all rows; confirm)."""
    return p_layers.edit(action, target, layer, adds, updates, deletes, where, expressions, source_item_id,
                         upload_format, upsert, upsert_matching_field, field_mappings, use_global_ids,
                         rollback_on_failure, batch_size, dry_run, confirm, connection)


@mcp.tool()
def portal_layer_schema(action: str, target: str, layer: int | str | None = 0, fields: list | None = None,
                        field_names: list[str] | None = None, capabilities: str | None = None,
                        renderer: dict | None = None, label_field: str | None = None,
                        label_expression: str | None = None, definition: dict | None = None, level: str = "layer",
                        view_name: str | None = None, view_where: str | None = None,
                        view_fields: list[str] | None = None, updateable: bool = False, folder: str | None = None,
                        data_file: str | None = None, confirm: bool = False, connection: str = "default") -> dict:
    """Shape a HOSTED service: add_field (fields=[{"name","type":TEXT|SHORT|LONG|DOUBLE|DATE|...,"length","alias"}]; never on a view) | delete_field (confirm) | capabilities ("Query,Create,Update,Delete,Editing,Sync,Extract") | renderer (Esri JSON, or {"type":"unique","field"} / {"type":"class_breaks","field","classes"} / {"type":"simple","color"} built from the data) | labels (label_field or Arcade label_expression) | definition (raw update_definition, level layer|service; confirm) | create_view (view_name, view_where filter, view_fields, updateable=false read-only) | overwrite (data_file; confirm)."""
    return p_layers.schema(action, target, layer, fields, field_names, capabilities, renderer, label_field,
                           label_expression, definition, level, view_name, view_where, view_fields, updateable,
                           folder, data_file, confirm, connection)


@mcp.tool()
def portal_map(action: str = "describe", item_id: str | None = None, title: str | None = None,
               layers: list[str | int] | None = None, basemap: str | None = None, tags: list[str] | None = None,
               folder: str | None = None, snippet: str | None = None, popups: bool = True,
               renderer: dict | None = None, layer: str | int | None = None, popup_fields: list[str] | None = None,
               popup_title: str | None = None, definition_expression: str | None = None,
               connection: str = "default") -> dict:
    """Web maps: create (title + layers = item ids/layer URLs; basemap omitted = org default, or a gallery title/alias e.g. topo-vector, satellite, gray-vector; auto popups; renderer spec as in portal_layer_schema; definition_expression filter) | describe | add_layers | remove_layers | popup (layer, popup_fields, popup_title) | renderer | filter | basemap | create_scene (web scene)."""
    return p_maps.webmap(action, item_id, title, layers, basemap, tags, folder, snippet, popups, renderer, layer,
                         popup_fields, popup_title, definition_expression, connection)


@mcp.tool()
def portal_apps(action: str, item_id: str | None = None, item_ids: list[str] | None = None,
                export_format: str = "CSV", out_folder: str | None = None, title: str | None = None,
                folder: str | None = None, item_mapping: dict | None = None, blocks: list[dict] | None = None,
                tags: list[str] | None = None, publish: bool = False, outside_org: bool = False,
                confirm: bool = False, connection: str = "default") -> dict:
    """App items: survey_info | survey_export (Survey123 Form item -> CSV/Shapefile/File Geodatabase/GeoJson/Excel) | dashboard_clone (item_mapping {old_layer_item: new_layer_item}; confirm) | storymap_create (title, blocks=[{"text"},{"map":webmap_id,"caption"}]) | dependency_graph (item_id(s): what requires what - run before deleting/migrating)."""
    return p_apps.apps(action, item_id, item_ids, export_format, out_folder, title, folder, item_mapping, blocks,
                       tags, publish, outside_org, confirm, connection)


@mcp.tool()
def portal_users(action: str = "search", username: str | None = None, query: str | None = None,
                 max_users: int = 50, firstname: str | None = None, lastname: str | None = None,
                 email: str | None = None, user_type: str | None = None, role: str | None = None,
                 groups: list[str] | None = None, password_env: str | None = None, description: str | None = None,
                 credits: float | None = None, reassign_to: str | None = None, confirm: bool = False,
                 connection: str = "default") -> dict:
    """Org users: search (query, role, user_type) | get (+ groups, privileges) | create (role/user_type names resolved to ids; type set before role; never created directly as admin; password_env or Online email invite; verified by re-read; confirm) | update_access (user_type/role/groups; confirm) | disable | enable | delete (reassign_to if they own content; prefer disable; confirm) | transfer_content (reassign_to; confirm)."""
    return p_access.users(action, username, query, max_users, firstname, lastname, email, user_type, role, groups,
                          password_env, description, credits, reassign_to, confirm, connection)


@mcp.tool()
def portal_onboard(action: str = "validate", csv_path: str | None = None, out_path: str | None = None,
                   update_existing: bool = False, sync_roles: bool = False, confirm: bool = False,
                   connection: str = "default") -> dict:
    """Bulk users from CSV (username,password,firstname,lastname,email,user_type,role,groups,credits,description): template (write a starter CSV) | validate (every row checked, nothing changes; one bad row blocks the run) | run (confirm: creates preset roles like "GIS Specialist", users, type -> role -> groups, verifies each, writes a report CSV without passwords) | export_users (org users to CSV). UTF-8 (Arabic safe)."""
    return p_access.onboard(action, csv_path, out_path, update_existing, sync_roles, confirm, connection)


@mcp.tool()
def portal_groups(action: str = "search", group: str | None = None, query: str | None = None,
                  title: str | None = None, tags: list[str] | None = None, description: str | None = None,
                  access: str | None = None, is_invitation_only: bool = True, is_view_only: bool = False,
                  shared_update: bool = False, users: list[str] | None = None, max_groups: int = 50,
                  confirm: bool = False, connection: str = "default") -> dict:
    """Groups (group = id or exact title): search | get (members, items) | create (title, access private|org|public, shared_update only at creation) | update | add_users | remove_users (notAdded/notRemoved checked) | delete (confirm)."""
    return p_access.groups(action, group, query, title, tags, description, access, is_invitation_only, is_view_only,
                           shared_update, users, max_groups, confirm, connection)


@mcp.tool()
def portal_roles(action: str = "list", role: str | None = None, name: str | None = None,
                 description: str | None = None, privileges: list[str] | None = None, preset: str | None = None,
                 confirm: bool = False, connection: str = "default") -> dict:
    """Roles and licenses: list (built-in + custom + presets) | get | create (name + privileges, or preset "GIS Specialist"; rejected privileges reported; confirm) | set_privileges (confirm) | delete (confirm) | user_types (license types and seats) | license_usage | privileges_catalog."""
    return p_access.roles(action, role, name, description, privileges, preset, confirm, connection)


@mcp.tool()
def portal_admin(action: str = "org", days: int = 7, hours: float | None = None, out_path: str | None = None,
                 include_items: bool = False, server_index: int = 0, service: str | None = None,
                 folder: str | None = None, level: str = "WARNING", max_records: int = 200,
                 notebook_item_id: str | None = None, timeout_min: int = 30, confirm: bool = False,
                 connection: str = "default") -> dict:
    """Org/server admin (mostly needs an administrator): org | credits | usage | history (Online) | audit_csv (users, types, roles, last login, groups -> CSV) | license | servers | services | service_start | service_stop (confirm) | logs (hours, level; Enterprise) | notebooks | notebook_run (confirm) | audit_log (this server's own write log)."""
    return p_admin.admin(action, days, hours, out_path, include_items, server_index, service, folder, level,
                         max_records, notebook_item_id, timeout_min, confirm, connection)


@mcp.tool()
def portal_geocode(action: str = "single", address: str | None = None, addresses: list[str] | None = None,
                   csv_path: str | None = None, address_field: str | None = None, out_csv: str | None = None,
                   location: list[float] | None = None, geocoder_url: str | None = None, max_locations: int = 1,
                   for_storage: bool = False, country: str | None = None, confirm: bool = False,
                   connection: str = "default") -> dict:
    """Geocoding: geocoders | single (free unless for_storage) | reverse (location [lon, lat]) | suggest | batch (addresses or csv_path + address_field -> out_csv; ArcGIS Online = 40 credits per 1,000: estimated first, confirm)."""
    return p_analysis.geocode(action, address, addresses, csv_path, address_field, out_csv, location, geocoder_url,
                              max_locations, for_storage, country, confirm, connection)


@mcp.tool()
def portal_analysis(action: str = "list", function: str | None = None, params: dict | None = None,
                    gp_url: str | None = None, confirm: bool = False, connection: str = "default") -> dict:
    """Hosted analysis: list | describe (function signature + doc) | estimate (credits) | run (params incl. output_name; item ids/layer URLs become layers; credits confirmed) | gp_describe / gp_run (any GP service URL or item; confirm)."""
    return p_analysis.analysis(action, function, params, gp_url, confirm, connection)


@mcp.tool()
def portal_api(action: str = "search", query: str | None = None, symbol: str | None = None,
               module: str | None = None, section: str | None = None, limit: int = 15, max_chars: int = 4000,
               include_heavy: bool = False) -> dict:
    """Learn the INSTALLED ArcGIS API for Python: search (query -> ranked classes/functions/methods with signature, summary, deprecated flag) | doc (symbol: signature, docstring or section parameters/returns/examples, source, deprecation + replacement) | members (symbol) | version | module_map. If you are not 100% sure of an arcgis API, read its doc before using it."""
    return p_knowledge.api(action, query, symbol, module, section, limit, max_chars, include_heavy)


@mcp.tool()
def portal_python(code: str, mode: str = "read", confirm: bool = False, session_id: str | None = None,
                  timeout_s: int = 120, max_chars: int = 20000, connection: str = "default") -> dict:
    """Escape hatch: run ArcGIS API for Python code with a signed-in `gis` (+ arcgis, pd, json, ItemProperties, FeatureLayer, FeatureLayerCollection, GeoAccessor, gis_<name>); assign `result` (stdout captured). mode=read refuses code that writes to the portal (a seatbelt, not a sandbox); mode=write needs confirm and is audited. session_id keeps variables between calls. Look APIs up with portal_api first."""
    return p_pyexec.run(code, mode, confirm, session_id, timeout_s, max_chars, connection)


@mcp.tool()
def portal_rest(path: str, method: str = "GET", params: dict | None = None, confirm: bool = False,
                max_chars: int = 20000, connection: str = "default") -> dict:
    """Raw REST with the signed-in session: path relative to <portal>/sharing/rest/ (e.g. "portals/self") or an absolute URL on the portal / its services. GET free; POST needs confirm unless read-only (query, search, generateRenderer...)."""
    return p_rest.request(path, method, params, confirm, max_chars, connection)


# =====================================================================
# Layer 3 - Web app generator (ArcGIS Maps SDK for JavaScript 5.0, static)
# =====================================================================


@mcp.tool()
def webapp_create(title: str, kind: str = "app", webmap_id: str | None = None,
                  layer_item_ids: list[str] | None = None, out_dir: str | None = None, basemap: str = "topo-vector",
                  widgets: list[str] | None = None, category_field: str | None = None,
                  value_fields: list[str] | None = None) -> dict:
    """Generate a static ArcGIS Maps SDK for JS 5.0 site from a webmap_id or layer_item_ids: kind=app (map + widgets: legend, layerList, search, basemapGallery, home) | kind=dashboard (map + live indicators + category breakdown + features-in-view list, recomputed as you pan; category_field/value_fields override the auto-picked fields)."""
    if (kind or "app").lower() == "dashboard":
        return webapp_dashboard.create_dashboard(title, webmap_id, layer_item_ids, out_dir, basemap, widgets, None,
                                                 category_field, value_fields)
    if (kind or "app").lower() != "app":
        return _err("kind must be app | dashboard.", code="bad_argument")
    return webapp.create_web_app(title, webmap_id, layer_item_ids, out_dir, basemap, widgets)


@mcp.tool()
def webapp_github_pipeline(repo_name: str, token: str, deploy_mode: str = "upload") -> dict:
    """Create a public GitHub repo, push the generated static web app, and (deploy_mode='live') enable GitHub Pages."""
    return webapp_github.github_pipeline(repo_name, token, deploy_mode)


# =====================================================================
# Batch runner (pro_batch) and server status
# =====================================================================

_REF = re.compile(r"\{\{\s*(?:call(\d+)|prev)((?:\.[A-Za-z0-9_\-]+)*)\s*\}\}")
_NO_BATCH = {"pro_batch"}


def _ref_value(results: list[dict], index: int, path: str) -> Any:
    if not 1 <= index <= len(results):
        raise ValueError(f"{{{{call{index}}}}} refers to a call that has not run yet ({len(results)} done).")
    value: Any = results[index - 1]
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            if part not in value:
                raise KeyError(f"'{part}' not found in call {index}'s result (keys: {', '.join(map(str, value))}).")
            value = value[part]
        else:
            raise KeyError(f"cannot read '{part}' from a {type(value).__name__} in call {index}'s result.")
    return value


def _resolve_refs(value: Any, results: list[dict]) -> Any:
    if isinstance(value, list):
        return [_resolve_refs(v, results) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_refs(v, results) for k, v in value.items()}
    if not isinstance(value, str) or "{{" not in value:
        return value

    def index_of(m: re.Match) -> int:
        return int(m.group(1)) if m.group(1) else len(results)

    whole = _REF.fullmatch(value.strip())
    if whole:  # keep the original type (list, dict, number)
        return _ref_value(results, index_of(whole), whole.group(2))
    return _REF.sub(lambda m: str(_ref_value(results, index_of(m), m.group(2))), value)


def _runs_on_arcpy(name: str) -> bool:
    return name.startswith("pro_") and name not in _NOT_ARCPY


def _invoke(name: str, arguments: dict) -> dict:
    fn = _ORIGINAL_FN[name]
    started = time.perf_counter()
    try:
        result = arcpy_thread.run(fn, **arguments) if _runs_on_arcpy(name) else fn(**arguments)
    except Exception as exc:  # noqa: BLE001 - bad arguments (TypeError) and the like
        result = _err(f"{type(exc).__name__}: {exc}", code="call_failed")
    return {"tool": name, "ok": bool(isinstance(result, dict) and result.get("ok", True)),
            "seconds": round(time.perf_counter() - started, 2), "result": result}


def _run_batch(calls: list[dict], parallel: bool, stop_on_error: bool,
               progress: Callable[[str], None] | None = None) -> dict:
    started = time.perf_counter()
    out: list[dict] = []
    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(calls))) as pool:
            futures = [pool.submit(_invoke, c["tool"], dict(c.get("arguments") or {})) for c in calls]
            out = [f.result() for f in futures]
    else:
        results: list[dict] = []
        for i, call in enumerate(calls, start=1):
            if progress:
                progress(f"[{i}/{len(calls)}] {call['tool']}")
            try:
                arguments = _resolve_refs(dict(call.get("arguments") or {}), results)
            except (KeyError, ValueError, IndexError) as exc:
                entry = {"tool": call["tool"], "ok": False, "seconds": 0.0,
                         "result": _err(f"reference: {exc}", code="bad_reference")}
            else:
                entry = _invoke(call["tool"], arguments)
            out.append(entry)
            results.append(entry["result"])
            if not entry["ok"] and stop_on_error:
                for rest in calls[i:]:
                    out.append({"tool": rest["tool"], "ok": False, "skipped": True})
                break
    for i, entry in enumerate(out, start=1):
        entry["call"] = i
    done = sum(1 for e in out if e.get("ok"))
    payload = {"calls": out, "succeeded": done, "requested": len(calls),
               "seconds": round(time.perf_counter() - started, 2)}
    return _ok(payload) if done == len(calls) else {"ok": False, "error": f"{len(calls) - done} call(s) failed.",
                                                    "code": "batch_partial", "data": payload}


def _status() -> dict:
    return _ok({
        "version": __version__, "tools": len(mcp._tool_manager._tools),
        "arcpy_warmup": bootstrap.warmup_status(), "arcpy_thread": arcpy_thread.status(),
        "portal_warmup": bootstrap.portal_warmup_status(), "portal_connections": p_conn.status(),
        "api_index": p_knowledge.status(), "schema_cache": pro_cache.stats(),
        "jobs": pro_pipeline_mod.jobs_summary(),
    })


# =====================================================================
# Toolset gating
#
# Every registered tool's schema is sent to the model on EVERY request. ARCGIS_SALAH_TOOLSETS keeps only
# the groups a session needs, e.g. "core,portal" or "core,egdb". "portal" turns on every portal group;
# portal_base / portal_data / portal_maps / portal_admin / portal_analysis pick individual ones.
# Unset (the default) keeps everything.
# =====================================================================

TOOLSETS: dict[str, tuple[str, ...]] = {
    # "core" carries no rule: it is whatever is left over (pro_context, pro_query, pro_batch, ...).
    "geoai": ("pro_geoai_", "pro_dl_"),
    "unet": ("pro_un_",),
    "schema": ("pro_domain", "pro_subtypes", "pro_attribute_rules", "pro_apply_schema"),
    "validate": ("pro_topology", "pro_check_geometry", "pro_validate_all"),
    "authoring": ("pro_create_python_toolbox", "pro_create_model", "pro_toolbox_info"),
    "egdb": ("pro_egdb_",),
    "live": ("live_",),
    "portal_data": ("portal_layer_",),
    "portal_maps": ("portal_map", "portal_apps"),
    "portal_admin": ("portal_users", "portal_onboard", "portal_groups", "portal_roles", "portal_admin"),
    "portal_analysis": ("portal_geocode", "portal_analysis"),
    "portal_base": ("portal_",),  # after the specific portal groups: first match wins
    "webapp": ("webapp_",),
}
TOOLSET_ALIASES = {"portal": ("portal_base", "portal_data", "portal_maps", "portal_admin", "portal_analysis")}


def _toolset_of(name: str) -> str:
    """Which toolset a tool belongs to; 'core' when it matches no group."""
    for group, patterns in TOOLSETS.items():
        for pattern in patterns:
            if (pattern.endswith("_") and name.startswith(pattern)) or name == pattern:
                return group
    return "core"


def apply_toolset_filter(enabled: str | None = None) -> dict:
    """Prune tools outside the enabled toolsets. 'core' is always kept."""
    raw = enabled if enabled is not None else os.environ.get("ARCGIS_SALAH_TOOLSETS", "")
    if not raw.strip():
        return {"filtered": False, "enabled": ["all"], "tools": len(mcp._tool_manager._tools)}
    asked = {g.strip().lower() for g in raw.split(",") if g.strip()}
    keep = {"core"}
    for g in asked:
        keep.update(TOOLSET_ALIASES.get(g, (g,)))
    unknown = asked - set(TOOLSETS) - set(TOOLSET_ALIASES) - {"core"}
    removed = []
    for name in list(mcp._tool_manager._tools):
        if _toolset_of(name) not in keep:
            mcp._tool_manager.remove_tool(name)
            removed.append(name)
    return {"filtered": True, "enabled": sorted(keep), "unknown_toolsets": sorted(unknown), "removed": len(removed),
            "tools": len(mcp._tool_manager._tools)}


def _toolsets_report() -> dict:
    counts: dict[str, int] = {}
    for name in mcp._tool_manager._tools:
        group = _toolset_of(name)
        counts[group] = counts.get(group, 0) + 1
    return _ok({"active_tools": len(mcp._tool_manager._tools), "by_toolset": counts,
                "available_toolsets": ["core"] + sorted(TOOLSETS) + sorted(TOOLSET_ALIASES),
                "aliases": {k: list(v) for k, v in TOOLSET_ALIASES.items()}, "env_var": "ARCGIS_SALAH_TOOLSETS",
                "current_setting": os.environ.get("ARCGIS_SALAH_TOOLSETS") or "(unset - all enabled)"})


# =====================================================================
# Resources (playbooks + live state) and prompts (reusable workflows)
# =====================================================================

GUIDES_DIR = Path(__file__).parent / "guides"


def _guide_title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").splitlines()[0] if path.exists() else path.stem
    return first.lstrip("# ").strip() or path.stem


def _guide_reader(path: Path) -> Callable[[], str]:
    def read() -> str:  # a static resource takes no parameters, so the path is closed over
        return path.read_text(encoding="utf-8")

    read.__name__ = f"guide_{path.stem.replace('-', '_')}"
    return read


def _register_guides() -> None:
    for path in sorted(GUIDES_DIR.glob("*.md")):
        mcp.resource(f"arcgis://guide/{path.stem}", name=f"guide-{path.stem}", title=_guide_title(path),
                     description=f"Playbook: {_guide_title(path)}", mime_type="text/markdown")(_guide_reader(path))


def _guide(topic: str | None) -> dict:
    """A playbook by topic (pro_server action=guide) - the same markdown as the arcgis://guide/<topic> resources,
    reachable from any client, including those (Claude Desktop) where only the user can attach a resource."""
    files = {p.stem: p for p in GUIDES_DIR.glob("*.md")}
    if not topic:
        return _ok({"topics": {stem: _guide_title(p) for stem, p in sorted(files.items())},
                    "next": "pro_server(action='guide', topic='<topic>')"})
    key = topic.strip().lower().removesuffix(".md").removeprefix("arcgis://guide/")
    path = files.get(key)
    if path is None:
        return _err(f"Unknown topic '{topic}'; one of: {', '.join(sorted(files))}.", code="bad_argument")
    return _ok({"topic": key, "title": _guide_title(path), "markdown": path.read_text(encoding="utf-8")})


_register_guides()


@mcp.resource("arcgis://guides", name="guides", title="Playbook index", mime_type="text/markdown",
              description="Every playbook (arcgis://guide/<topic>) with its title.")
def guides_index() -> str:
    lines = ["# Playbooks", ""]
    lines += [f"- `arcgis://guide/{p.stem}` - {_guide_title(p)}" for p in sorted(GUIDES_DIR.glob("*.md"))]
    return "\n".join(lines)


# Resources are CONTEXT a user (or Claude Code's resource tools) attaches without a tool call. Only state
# worth attaching lives here; server status, toolsets and the module map stay tools (pro_server, portal_api).
# These touch the network, so they run on a worker thread: FastMCP awaits a coroutine resource but calls a
# plain function on the event loop (a portal read can wait ~35 s on the arcgis import right after start-up).


def _as_json(envelope: dict) -> str:
    return json.dumps(envelope.get("data") if envelope.get("ok") else envelope, default=str, indent=1,
                      ensure_ascii=False)


@mcp.resource("arcgis://live/project", name="live-project", title="Open ArcGIS Pro project",
              mime_type="application/json",
              description="The project open in ArcGIS Pro right now: path, maps, layouts (via the live bridge).")
async def live_project_resource() -> str:
    return _as_json(await asyncio.to_thread(live.live_ping))


@mcp.resource("arcgis://portal/my-content", name="portal-my-content", title="My recent portal items",
              mime_type="application/json",
              description="Your 50 most recently modified ArcGIS Online/Enterprise items (id, title, type, url).")
async def portal_my_content_resource() -> str:
    return _as_json(await asyncio.to_thread(p_content.search, "", None, "me", None, 50))


@mcp.resource("arcgis://portal/item/{item_id}", name="portal-item", title="A portal item as context",
              mime_type="application/json",
              description="One item: card, sharing, every layer's fields (feature service) or its layers (web map).")
async def portal_item_resource(item_id: str) -> str:
    return _as_json(await asyncio.to_thread(p_content.item_context, item_id))


@mcp.prompt(name="plan_big_task", title="Plan and run a big ArcGIS task fast")
def plan_big_task(goal: str) -> str:
    """Decompose a large request into stages that run in as few round trips as possible."""
    return (f"Goal: {goal}\n\nPlan this as stages before calling tools (read pro_server(action='guide', topic='performance')):\n"
            "1. One read stage: pro_context (and/or a pro_batch of portal_search / portal_connect whoami / "
            "portal_layer_query describe) to learn everything needed.\n"
            "2. Ask me ONCE for every decision and confirmation the writes will need.\n"
            "3. Write stages: chain GP tools with pro_pipeline; group independent calls into pro_batch (parallel=true "
            "for independent portal/live reads); pass results with {{callN.data...}}; anything that may exceed a "
            "minute goes background=true and is polled with pro_server(action='job').\n"
            "4. One verification stage (pro_batch of reads) and a short report: what changed, ids, links.")


@mcp.prompt(name="analyze_publish_visualize", title="Analyze in Pro, publish, build a web app")
def analyze_publish_visualize(data: str, question: str, share_with: str = "private") -> str:
    """The canonical Pro -> Portal -> WebApp workflow."""
    return (f"Data: {data}\nQuestion: {question}\nSharing: {share_with}\n\n"
            "1. pro_context on the data. 2. pro_pipeline for the analysis (or pro_analysis for one step). "
            "3. pro_batch: portal_publish the result -> portal_layer_schema renderer (unique/class_breaks from the "
            "data) -> portal_map create with popups. 4. portal_share as agreed (public needs my confirmation). "
            "5. webapp_create kind=dashboard with the web map. Report item ids and links.")


@mcp.prompt(name="publish_csv_and_share", title="Publish a CSV and share it")
def publish_csv_and_share(csv_path: str, title: str, group: str = "") -> str:
    """Upload a points CSV, publish, and share with a group."""
    return (f"Publish {csv_path} as '{title}'.\n1. Look at the header (latitude/longitude columns? an address "
            "column means geocoding credits - tell me the estimate first).\n2. portal_publish with latitude_field/"
            "longitude_field (or address_fields + confirm).\n3. portal_layer_query describe to verify the count.\n"
            + (f"4. portal_share add_groups=['{group}'].\n" if group else "4. Leave it private.\n")
            + "Return the item id, layer URL and item page link.")


@mcp.prompt(name="create_filtered_view_for_dashboard", title="Filtered read-only view for a dashboard")
def create_filtered_view_for_dashboard(item_id: str, where: str, view_name: str, group: str = "") -> str:
    """A read-only hosted view with a filter, shared, mapped and turned into a dashboard."""
    return (f"Source layer item {item_id}.\n1. portal_layer_query describe (hosted? fields).\n"
            f"2. portal_layer_schema create_view view_name='{view_name}' view_where=\"{where}\" updateable=false "
            "(choose view_fields with me if some fields are sensitive).\n3. Verify view_count.\n"
            + (f"4. portal_share the VIEW with '{group}'.\n" if group else "4. Keep the view private.\n")
            + "5. portal_map create from the view, then webapp_create kind=dashboard.")


@mcp.prompt(name="onboard_users_from_csv", title="Onboard users from a CSV")
def onboard_users_from_csv(csv_path: str) -> str:
    """Validate, plan, confirm, create, verify, report (portal-users playbook)."""
    return (f"CSV: {csv_path}. Follow the playbook pro_server(action='guide', topic='portal-users').\n1. portal_connect whoami: am I an admin?\n"
            "2. portal_onboard action=validate - show me every problem row; nothing is created until all rows pass.\n"
            "3. Show the plan: new vs existing users, roles to create, anyone getting Administrator.\n"
            "4. After I confirm: portal_onboard action=run confirm=true (update_existing only if I say so).\n"
            "5. Report the counts and the report CSV path (it never contains passwords).")


@mcp.prompt(name="org_audit", title="Org audit and usage report")
def org_audit(days: int = 30) -> str:
    """Users, licenses, roles, last login, credits and usage."""
    return (f"Audit the org for the last {days} days in one pro_batch (parallel=true): portal_admin org, "
            f"portal_admin audit_csv, portal_roles user_types, portal_admin credits days={days}, portal_admin usage "
            f"days={days}. Summarize: users by type/role, never-logged-in users, seats used, credit burn, and give "
            "me the CSV path. Report 403s as 'needs an administrator' instead of retrying.")


@mcp.prompt(name="migrate_items_between_orgs", title="Migrate items between orgs or folders")
def migrate_items_between_orgs(item_ids: str, target_folder: str = "Migrated") -> str:
    """Dependency graph first, preview, confirm, clone, verify."""
    return (f"Items: {item_ids}. Target folder: {target_folder}. Follow the playbook pro_server(action='guide', topic='portal-migration').\n"
            "1. Make sure the source/target connections exist (portal_connect list; ask me for the target sign-in).\n"
            "2. portal_apps dependency_graph on the items.\n3. portal_clone preview -> show me -> confirm=true.\n"
            "4. Verify the new ids in the target and list what was reused (search_existing_items).")


@mcp.prompt(name="enterprise_gdb_setup", title="Set up an enterprise geodatabase")
def enterprise_gdb_setup(platform: str, instance: str, database: str) -> str:
    """Create -> connect -> users/roles -> privileges -> versioning, with confirmations."""
    return (f"{platform} on {instance}, database {database}. Follow the playbook pro_server(action='guide', topic='egdb').\n"
            "Ask me for: my keycodes file (or ARCGIS_EGDB_AUTHORIZATION_FILE), auth type, and the NAMES of the env "
            "vars holding passwords (never the passwords).\n1. pro_egdb_setup create (background=true via pro_batch).\n"
            "2. pro_egdb_connection create (admin + data owner + editor).\n3. pro_egdb_principals create_role / "
            "create_user.\n4. pro_egdb_privileges.\n5. pro_egdb_dataset prepare / register_versioned as I choose.\n"
            "Verify each step with pro_egdb_connection describe / pro_egdb_versions list.")


@mcp.prompt(name="workflow_to_toolbox", title="Turn a workflow into a geoprocessing tool")
def workflow_to_toolbox(workflow: str) -> str:
    """Build a parameterized .pyt from a described workflow."""
    return (f"Workflow: {workflow}\nFollow the playbook pro_server(action='guide', topic='toolboxes'). Decide the parameters with me (inputs, "
            "outputs, distances), then pro_create_python_toolbox with kwargs steps, {{param}}, {{memory}} for "
            "intermediates and {{stepN}} for chaining. It is added to the open project; warn me about Pro's trust "
            "dialog on first load. Optionally test it with pro_run_gp on small data.")


@mcp.prompt(name="dl_notebook_interview", title="Deep-learning training notebook")
def dl_notebook_interview(goal: str) -> str:
    """Interview first, never guess (pro_dl_notebook)."""
    return (f"Goal: {goal}\nCall pro_dl_notebook with only goal. If it says status=choose or ask_user, ask me "
            "those questions exactly (model, training data path, batch size, epochs, ...). Then call it again with "
            "my answers and tell me how to open the notebook in Pro (Catalog > Notebooks > Add and Open Notebook).")


@mcp.prompt(name="explore_arcgis_api", title="Do something no curated tool covers")
def explore_arcgis_api(task: str) -> str:
    """Search the installed API, inspect in read mode, then write with confirmation."""
    return (f"Task: {task}\nFollow the playbook pro_server(action='guide', topic='portal-escape-hatches'): 1. portal_api search, then portal_api doc "
            "for each API you will call. 2. portal_python mode=read to inspect (assign result). 3. Show me exactly "
            "what will change. 4. portal_python mode=write confirm=true. 5. Verify with a read.")


# ---------------------------------------------------------------------------
# Where each tool runs.
#
# FastMCP calls a sync tool function directly on the asyncio event loop, so one long call froze the
# whole server: requests behind it hung, and the client's ~1 minute tool timeout hit them too (reported
# from a real session). Every sync tool is therefore re-pointed at an async runner:
#   * arcpy-backed pro_* tools -> the single arcpy thread (arcpy only works reliably on the thread that
#     imported it - PostgreSQL calls failed with ERROR 87395/87413 elsewhere; see arcpy_thread.py);
#   * everything else (live_*, portal_*, webapp_*, pure generators, the batch runner) -> a worker thread.
# The module-level functions are untouched (tests and callers use them directly); only the registered
# Tool objects change. _ORIGINAL_FN keeps the sync functions for pro_batch.
# ---------------------------------------------------------------------------
_NOT_ARCPY = {"pro_dl_notebook", "pro_create_python_toolbox", "pro_toolbox_info", "pro_create_model",
              "pro_server", "pro_batch"}
_ORIGINAL_FN: dict[str, Any] = {}


def _on_worker_thread(fn: Any) -> Any:
    @functools.wraps(fn)
    async def runner(**kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, **kwargs)

    return runner


def _route_tools() -> None:
    for name, tool in mcp._tool_manager._tools.items():
        if tool.is_async:
            continue
        _ORIGINAL_FN[name] = tool.fn
        if _runs_on_arcpy(name):
            tool.fn = arcpy_thread.make_async(tool.fn, name)
        else:
            tool.fn = _on_worker_thread(tool.fn)
        tool.is_async = True


_route_tools()


def main() -> None:
    """Console-script entry point.

    ``bootstrap.prepare()`` re-executes under ``arcgispro-py3`` when this interpreter has no ArcPy, then starts
    importing arcpy on the arcpy thread (~25 s that the user's first request would otherwise pay). When a
    portal toolset is on, arcgis is imported and Pro's sign-in reused in the background right after.
    ``stdio_guard`` keeps library print() output off the protocol channel. Nothing here blocks the MCP
    ``initialize`` handshake.
    """
    warm = bootstrap.prepare()
    apply_toolset_filter()
    if any(name.startswith("portal_") for name in mcp._tool_manager._tools):
        bootstrap.start_portal_warmup(after=warm)
    stdio_guard.install()
    mcp.run()


if __name__ == "__main__":
    main()
