"""Generate real **ModelBuilder** models inside an ArcGIS ``.atbx`` toolbox.

Why this can be done precisely
------------------------------
An ``.atbx`` is not an opaque binary — it is a **ZIP archive of JSON**. Verified
against the toolboxes shipped with ArcGIS Pro 3.x
(``Resources/ArcToolBox/Services/PrintingTools.atbx``), the layout is::

    MyToolbox.atbx                     (zip)
      toolbox.content                  registry: alias + tool list
      toolbox.content.rc               display strings for the toolbox
      MyModel.tool/
        tool.content                   {"type": "ModelTool", params: {...}}
        tool.content.rc                display strings for the tool
        tool.model                     the model graph: variables + processes
        tool.model.diagram             canvas viewport

``tool.model`` is the model itself. **Variables** are the data elements on the
canvas (blue ovals for inputs, green for derived outputs); **processes** are the
tool boxes (yellow), each wiring its parameters to variable ids. Chaining two
tools is exactly: process A writes to variable *n*, process B reads element_id
*n*. That is what makes a generated model a genuine editable ModelBuilder model
rather than a script pretending to be one.

Every display string is indirected through ``$rc:key`` into the ``.rc`` files,
which is how ArcGIS localises toolboxes — matched here so the produced toolbox is
structurally identical to an Esri-authored one.

Like :mod:`toolbox`, this module is **pure Python** — no ``arcpy`` — so it is
fully unit-testable with no ArcGIS installed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import zipfile
from typing import Any

from .._result import err, guard, ok
from . import register as _register
from .toolbox import DATATYPES, _identifier, _resolve_datatype

# Written into tool.content so ArcGIS knows which schema it is reading. These are
# the values Pro 3.x itself writes.
_APP_VER = "13.2"
_PRODUCT = "100"
_CONTENT_VERSION = "1.0"

_PARAM_TYPES = {"required", "optional", "derived"}

# Where a step output the user did not name gets written. `%scratchgdb%` is an
# inline variable ModelBuilder resolves at run time, so the model stays portable
# instead of carrying a hard-coded path from the authoring machine. Unlike
# `memory\`, a scratch-gdb intermediate survives the run, which is what you want
# when debugging why step 3 got the wrong input.
_INTERMEDIATE_VALUE_FMT = r"%scratchgdb%\{name}"
# A folder output (e.g. exported training chips) cannot live in a geodatabase.
_FOLDER_VALUE_FMT = r"%scratchfolder%\{name}"
_FOLDER_TYPES = {"DEFolder", "DEWorkspace"}

# Default type for an intermediate. Declaring SOMETHING here is mandatory, not
# cosmetic: without a `datatype` ArcGIS validates the downstream step against an
# untyped value and refuses the model with "The value is not a Feature Class".
# Verified against ArcGIS Pro 3.x by running generated two-step models.
_DEFAULT_INTERMEDIATE_DATATYPE = "DEFeatureClass"


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rc_key(text: str) -> str:
    """A resource key: lowercase, underscore-separated, safe for the .rc map."""
    key = re.sub(r"[^0-9a-zA-Z]+", "_", str(text or "").strip().lower()).strip("_")
    return key or "value"


class _ModelBuilder:
    """Accumulates variables and processes, handing out stable element ids.

    ModelBuilder identifies everything by a string id that must be unique inside
    one model and stable across the ``tool.model`` and the diagram, so ids are
    allocated centrally here rather than being guessed per section.
    """

    def __init__(self) -> None:
        self._next = 1
        self.variables: list[dict] = []
        self.processes: list[dict] = []
        self.rc: dict[str, str] = {}
        self.params: dict[str, dict] = {}

    def new_id(self) -> str:
        value = str(self._next)
        self._next += 1
        return value

    def add_rc(self, key: str, text: str) -> str:
        self.rc[key] = str(text)
        return f"$rc:{key}"


def _param_entry(spec: dict, rc_key: str) -> dict:
    """One entry in tool.content['params']."""
    entry: dict[str, Any] = {
        "type": spec["parameter_type"],
        "displayname": f"$rc:{rc_key}.title",
        "datatype": {"type": spec["datatype"]},
    }
    if spec["direction"] == "Output":
        entry["direction"] = "out"
    if spec.get("multi_value"):
        entry["datatype"] = {"type": "GPMultiValue", "datatype": {"type": spec["datatype"]}}
    if spec.get("default") is not None:
        entry["value"] = spec["default"]
    if spec.get("filter_list"):
        entry["domain"] = {"type": "GPCodedValueDomain", "items": [
            {"value": v, "code": v} for v in spec["filter_list"]
        ]}
    return entry


def _normalise_parameter(p: dict, index: int) -> tuple[dict | None, str | None]:
    label = p.get("label") or p.get("name")
    if not label:
        return None, f"Parameter #{index + 1} needs a 'name' or 'label'."

    datatype = _resolve_datatype(p.get("datatype", "string"))
    if datatype is None:
        return None, (
            f"Parameter '{label}': unknown datatype {p.get('datatype')!r}. "
            f"Valid aliases: {sorted(DATATYPES)}"
        )

    ptype = str(p.get("parameter_type", "required")).lower()
    if ptype not in _PARAM_TYPES:
        return None, f"Parameter '{label}': parameter_type must be one of {sorted(_PARAM_TYPES)}."

    direction = str(p.get("direction", "Input")).capitalize()
    if direction not in {"Input", "Output"}:
        return None, f"Parameter '{label}': direction must be Input or Output."
    if ptype == "derived" and direction != "Output":
        return None, f"Parameter '{label}': derived parameters must be Outputs."

    return (
        {
            "name": _identifier(p.get("name") or label, f"Param{index + 1}"),
            "label": str(label),
            "datatype": datatype,
            "parameter_type": ptype,
            "direction": direction,
            "multi_value": bool(p.get("multi_value", False)),
            "default": p.get("default"),
            "filter_list": p.get("filter_list"),
        },
        None,
    )


# What has actually been verified in ArcGIS Pro 3.7 (generate -> ImportToolbox -> run on data):
# linear chains whose intermediates are FEATURE CLASSES. Two patterns were tried and did NOT run
# (see CLAUDE.md "Toolbox authoring"): feeding a step output / model parameter into a MULTI-INPUT
# parameter (5 encodings of Intersect's `in_features`), and intermediates that are folders, files
# or layers (Export -> Train -> Detect deep learning; Select By Location). A model that cannot
# validate when run as a tool is worse than no model, so these are refused up front.
_VERIFIED_INTERMEDIATE = {"DEFeatureClass"}
_MULTI_INPUT = {
    "analysis.Intersect": "in_features",
    "analysis.Union": "in_features",
    "analysis.PairwiseIntersect": "in_features",
    "management.Merge": "inputs",
    "management.Append": "inputs",
    "management.MosaicToNewRaster": "input_rasters",
    "management.CompositeBands": "in_rasters",
}
_USE_PYT = ("Use pro_create_python_toolbox for this workflow, or pass allow_unverified=True "
            "if you have a Pro-authored model of the same chain to match.")

_REF = re.compile(r"^\{\{\s*([\w.]+)\s*\}\}$")


def _build_model(
    tool_label: str, parameters: list[dict], steps: list[dict]
) -> tuple[_ModelBuilder | None, str | None]:
    """Turn parameters + steps into the variables/processes graph."""
    mb = _ModelBuilder()
    mb.add_rc("title", tool_label)

    # 1. Every declared parameter becomes a model variable exposed as a tool
    #    parameter, so the model's dialog matches the spec.
    param_var: dict[str, str] = {}
    for spec in parameters:
        vid = mb.new_id()
        rc = _rc_key(spec["label"])
        mb.add_rc(f"{rc}.title", spec["label"])
        mb.add_rc(f"model.element{vid}", spec["label"])

        variable: dict[str, Any] = {
            "id": vid,
            "title": f"$rc:model.element{vid}",
            "connection_type": "Parameter",
            "param_name": spec["name"],
        }
        if spec["direction"] == "Output":
            variable["altered"] = "true"
            variable["direction"] = "out"
        else:
            variable["marked"] = "true"
        if spec.get("default") is not None:
            variable["value"] = spec["default"]

        mb.variables.append(variable)
        mb.params[spec["name"]] = _param_entry(spec, rc)
        param_var[spec["name"]] = vid

    # 2. Each step becomes a process, with an intermediate variable for its
    #    output so the next step can consume it.
    step_output_var: dict[int, str] = {}
    # (step number, tool parameter) -> variable, for outputs a later step reads by name
    # as {{stepN.param}} - e.g. the .dlpk a training tool derives next to its folder.
    step_named_var: dict[tuple[int, str], str] = {}
    for n, step in enumerate(steps, start=1):
        tool_name = step.get("tool")
        if not tool_name:
            return None, f"Step #{n} is missing 'tool' (e.g. 'analysis.Buffer')."
        if "." not in str(tool_name):
            return None, (
                f"Step #{n}: tool must be a dotted system name like "
                f"'analysis.Buffer' or 'management.Dissolve', got {tool_name!r}."
            )

        pid = mb.new_id()
        label = step.get("label") or str(tool_name).split(".")[-1]
        mb.add_rc(f"model.element{pid}", label)

        process: dict[str, Any] = {
            "id": pid,
            "title": f"$rc:model.element{pid}",
            "marked": "true",
            "tool_type": "SystemTool",
            "system_tool": str(tool_name),
            "params": {},
        }

        for key, raw in (step.get("params") or {}).items():
            ref = _REF.match(str(raw)) if isinstance(raw, str) else None
            if ref:
                token = ref.group(1)
                if token in param_var:
                    process["params"][key] = {"element_id": param_var[token]}
                    continue
                named = re.fullmatch(r"step(\d+)\.(\w+)", token)
                if named:
                    key_ = (int(named.group(1)), named.group(2))
                    if key_ not in step_named_var:
                        return None, (
                            f"Step #{n} references {{{{{token}}}}}, but step {key_[0]} declared "
                            f"no output '{key_[1]}' (list it under 'extra_outputs')."
                        )
                    process["params"][key] = {"element_id": step_named_var[key_]}
                    continue
                m = re.fullmatch(r"step(\d+)", token)
                if m:
                    idx = int(m.group(1))
                    if idx not in step_output_var:
                        return None, (
                            f"Step #{n} references {{{{step{idx}}}}}, which has not "
                            f"produced an output yet."
                        )
                    process["params"][key] = {"element_id": step_output_var[idx]}
                    continue
                return None, (
                    f"Step #{n}: {{{{{token}}}}} matches no parameter and no earlier step."
                )
            process["params"][key] = {"value": raw}

        # The step's declared output: either straight into a tool parameter
        # (so the user sees it) or an intermediate variable on the canvas.
        out_key = step.get("output_param")
        out_target = step.get("output")
        if out_key:
            ref = _REF.match(str(out_target)) if isinstance(out_target, str) else None
            if ref and ref.group(1) in param_var:
                vid = param_var[ref.group(1)]
            else:
                vid = mb.new_id()
                inter_name = f"{_identifier(label, 'Step')}_{vid}"
                mb.add_rc(f"model.element{vid}", f"{label} output")
                inter_dt = _resolve_datatype(
                    step.get("output_datatype") or _DEFAULT_INTERMEDIATE_DATATYPE
                ) or _DEFAULT_INTERMEDIATE_DATATYPE
                # Both keys are required. Without `value` the wrapped tool fails
                # with "Output Feature Class: Value is required"; without
                # `datatype` the NEXT step fails validation with "The value is
                # not a Feature Class". Both verified empirically.
                fmt = _FOLDER_VALUE_FMT if inter_dt in _FOLDER_TYPES else _INTERMEDIATE_VALUE_FMT
                mb.variables.append(
                    {
                        "id": vid,
                        "title": f"$rc:model.element{vid}",
                        "altered": "true",
                        "intermediate": "true",
                        "value": fmt.format(name=inter_name),
                        "datatype": {"type": inter_dt},
                    }
                )
            process["params"][out_key] = {
                "altered": "true",
                "direction": "out",
                "element_id": vid,
            }
            step_output_var[n] = vid

        # Extra outputs: further parameters the tool writes or DERIVES (a trainer's
        # .dlpk, a detector's classified raster). Each is its own green oval, readable
        # by later steps as {{stepN.param}}. No `value` is written: the tool derives it.
        for extra in step.get("extra_outputs") or []:
            key = extra.get("param") if isinstance(extra, dict) else None
            if not key:
                return None, f"Step #{n}: every extra_outputs entry needs a 'param' (the tool parameter name)."
            evid = mb.new_id()
            mb.add_rc(f"model.element{evid}", extra.get("label") or key)
            edt = _resolve_datatype(extra.get("datatype") or "file")
            if edt is None:
                return None, f"Step #{n}: unknown datatype {extra.get('datatype')!r} for extra output '{key}'."
            variable = {
                "id": evid,
                "title": f"$rc:model.element{evid}",
                "altered": "true",
                "intermediate": "true",
                "datatype": {"type": edt},
            }
            if extra.get("value"):
                variable["value"] = extra["value"]
            mb.variables.append(variable)
            process["params"][key] = {"altered": "true", "direction": "out", "element_id": evid}
            step_named_var[(n, key)] = evid

        mb.processes.append(process)

    if not mb.processes:
        return None, "A model needs at least one step."
    return mb, None


def _unsupported(steps: list[dict]) -> str | None:
    """A reason this workflow is known not to run as a generated model, else None."""
    for n, step in enumerate(steps, start=1):
        tool = str(step.get("tool") or "")
        key = _MULTI_INPUT.get(tool)
        if key and key in (step.get("params") or {}):
            raw = step["params"][key]
            items = raw if isinstance(raw, list) else [raw]
            if any(isinstance(i, str) and _REF.match(i) for i in items):
                return (f"Step #{n} ({tool}): wiring '{key}' from a {{{{reference}}}} is not supported - "
                        f"multi-input parameters did not validate or run in generated ModelBuilder "
                        f"models on Pro 3.7. {_USE_PYT}")
        declared = [(step.get("output_datatype") or "feature_class", "its output")]
        declared += [(e.get("datatype") or "file", f"extra output '{e.get('param')}'")
                     for e in step.get("extra_outputs") or [] if isinstance(e, dict)]
        output = step.get("output")
        for alias, what in declared:
            if what == "its output" and isinstance(output, str) and _REF.match(output):
                continue  # a declared tool parameter, not an intermediate
            resolved = _resolve_datatype(alias)
            if resolved not in _VERIFIED_INTERMEDIATE:
                return (f"Step #{n} ({tool}): {what} is a {alias!r} intermediate; only feature-class "
                        f"intermediates have been verified to validate and run. {_USE_PYT}")
    return None


@guard
def create_model(
    out_path: str,
    toolbox_label: str,
    model_name: str,
    steps: list[dict],
    parameters: list[dict] | None = None,
    model_label: str | None = None,
    description: str | None = None,
    alias: str | None = None,
    overwrite: bool = True,
    allow_unverified: bool = False,
    register_in_project: bool = False,
) -> dict:
    """Write a ``.atbx`` containing a real, editable ModelBuilder model.

    Only linear chains of feature-class tools are verified to run; multi-input tools
    (Intersect, Union, Merge ...) fed from a reference and folder/file/layer intermediates
    are refused with ``code="unsupported_model"`` (``allow_unverified=True`` overrides).
    ``register_in_project=True`` also adds the toolbox to the OPEN Pro project.

    ``steps`` entries::

        {"tool": "analysis.Buffer",          # dotted system tool name
         "label": "Buffer the roads",        # canvas label (optional)
         "params": {"in_features": "{{roads}}",
                    "buffer_distance_or_field": "{{distance}}"},
         "output_param": "out_feature_class",  # which param carries the output
         "output": "{{buffered}}"}             # a declared parameter, or omitted
                                               # for an intermediate variable

    ``{{name}}`` refers to a declared parameter; ``{{stepN}}`` to step N's output,
    which is what wires one tool's green output oval into the next tool's input.
    A step may also list ``"extra_outputs": [{"param": "out_model_file", "datatype": "file"}]``
    for outputs the tool derives; a later step reads one as ``{{stepN.out_model_file}}``.

    Open the result in ArcGIS Pro: Catalog > Toolboxes > Add Toolbox, then
    right-click the model > Edit to see the connected graph.
    """
    if not steps:
        return err("Provide at least one step.", code="no_steps")

    out_path = str(out_path)
    if not out_path.lower().endswith(".atbx"):
        out_path += ".atbx"
    if os.path.exists(out_path) and not overwrite:
        return err(f"{out_path} already exists (pass overwrite=True).", code="exists")

    tool_class = _identifier(model_name, "Model")
    tool_label = model_label or model_name

    normalised: list[dict] = []
    seen: set[str] = set()
    for i, p in enumerate(parameters or []):
        spec, problem = _normalise_parameter(p, i)
        if problem:
            return err(problem, code="bad_parameter")
        if spec["name"] in seen:
            return err(f"Duplicate parameter name '{spec['name']}'.", code="duplicate_parameter")
        seen.add(spec["name"])
        normalised.append(spec)

    if not allow_unverified:
        problem = _unsupported(steps)
        if problem:
            return err(problem, code="unsupported_model")

    mb, problem = _build_model(tool_label, normalised, steps)
    if problem:
        return err(problem, code="bad_model")

    stamp = _now()
    toolbox_alias = _identifier(alias or toolbox_label, "Toolbox")

    toolbox_content = {
        "version": _CONTENT_VERSION,
        "alias": toolbox_alias,
        "displayname": "$rc:title",
        "toolsets": {"<root>": {"tools": [f"{tool_class}:{tool_class}.tool"]}},
    }
    toolbox_rc = {"map": {"title": toolbox_label}}

    tool_content: dict[str, Any] = {
        "type": "ModelTool",
        "displayname": "$rc:title",
        "app_ver": _APP_VER,
        "product": _PRODUCT,
        "updated": stamp,
        "params": mb.params,
    }
    if description:
        tool_content["description"] = description

    tool_model = {
        "version": _CONTENT_VERSION,
        "updated": stamp,
        "variables": mb.variables,
        "processes": mb.processes,
    }

    # Canvas viewport. ModelBuilder auto-lays-out the nodes when the model is
    # first opened, so a diagram.xml is deliberately NOT written — generating one
    # by hand risks a corrupt layout, and Pro's own layout is better anyway.
    tool_diagram = {
        "version": _CONTENT_VERSION,
        "scale": "100",
        "cx": "-220", "cy": "-297",
        "x": "-220", "y": "-297",
        "dx": "752", "dy": "593",
    }

    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    def dump(obj: dict) -> str:
        return json.dumps(obj, indent=4)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("toolbox.content", dump(toolbox_content))
        z.writestr("toolbox.content.rc", dump(toolbox_rc))
        z.writestr(f"{tool_class}.tool/tool.content", dump(tool_content))
        z.writestr(f"{tool_class}.tool/tool.content.rc", dump({"map": mb.rc}))
        z.writestr(f"{tool_class}.tool/tool.model", dump(tool_model))
        z.writestr(f"{tool_class}.tool/tool.model.diagram", dump(tool_diagram))

    result = ok(
        {
            "toolbox": os.path.abspath(out_path),
            "alias": toolbox_alias,
            "model": tool_class,
            "label": tool_label,
            "parameters": [p["name"] for p in normalised],
            "variables": len(mb.variables),
            "processes": len(mb.processes),
            "steps": [s.get("tool") for s in steps],
            "usage": (
                f"ArcGIS Pro > Catalog > Toolboxes > Add Toolbox > "
                f"{os.path.basename(out_path)}. Right-click the model > Edit to "
                f"open the graph. From Python: "
                f"arcpy.ImportToolbox(r'{os.path.abspath(out_path)}')"
            ),
        }
    )
    return _register.attach(result, os.path.abspath(out_path), register_in_project)


@guard
def inspect_model(atbx_path: str) -> dict:
    """Read back an ``.atbx``: its tools, parameters and model graph.

    Works on any ``.atbx`` — Esri's own included — which makes it the way to
    learn how an existing model is wired before cloning or modifying it.
    """
    path = str(atbx_path)
    if not os.path.exists(path):
        return err(f"Not found: {path}", code="not_found")
    if not zipfile.is_zipfile(path):
        return err(
            f"{path} is not a .atbx zip archive. Legacy binary .tbx files cannot "
            f"be read this way — open and re-save it as .atbx in ArcGIS Pro.",
            code="not_atbx",
        )

    out: dict[str, Any] = {"toolbox": os.path.abspath(path), "tools": []}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        if "toolbox.content" in names:
            content = json.loads(z.read("toolbox.content"))
            out["alias"] = content.get("alias")
            out["toolsets"] = content.get("toolsets")
        rc: dict[str, str] = {}
        if "toolbox.content.rc" in names:
            rc = (json.loads(z.read("toolbox.content.rc")) or {}).get("map", {})
        out["label"] = rc.get("title")

        for entry in sorted(n for n in names if n.endswith(".tool/tool.content")):
            folder = entry.rsplit("/", 1)[0]
            tool: dict[str, Any] = {"name": folder[: -len(".tool")]}
            meta = json.loads(z.read(entry))
            tool["type"] = meta.get("type")
            tool["parameters"] = [
                {
                    "name": k,
                    "type": v.get("type"),
                    "direction": v.get("direction", "in"),
                    "datatype": (v.get("datatype") or {}).get("type"),
                }
                for k, v in (meta.get("params") or {}).items()
            ]

            tool_rc: dict[str, str] = {}
            rc_name = f"{folder}/tool.content.rc"
            if rc_name in names:
                tool_rc = (json.loads(z.read(rc_name)) or {}).get("map", {})
            tool["label"] = tool_rc.get("title")

            model_name = f"{folder}/tool.model"
            if model_name in names:
                model = json.loads(z.read(model_name))

                def resolve(title: str | None) -> str | None:
                    if isinstance(title, str) and title.startswith("$rc:"):
                        return tool_rc.get(title[4:], title)
                    return title

                tool["variables"] = [
                    {
                        "id": v.get("id"),
                        "title": resolve(v.get("title")),
                        "param_name": v.get("param_name"),
                        "intermediate": v.get("intermediate") == "true",
                    }
                    for v in model.get("variables", [])
                ]
                tool["processes"] = [
                    {
                        "id": p.get("id"),
                        "title": resolve(p.get("title")),
                        "tool": p.get("system_tool"),
                        "tool_type": p.get("tool_type"),
                        "wired": {
                            k: (v.get("element_id") or v.get("value"))
                            for k, v in (p.get("params") or {}).items()
                        },
                    }
                    for p in model.get("processes", [])
                ]
            out["tools"].append(tool)

    out["tool_count"] = len(out["tools"])
    return ok(out)
