"""Generate ArcGIS **Python toolboxes** (``.pyt``) from a declarative spec.

A ``.pyt`` is just a Python module ArcGIS introspects: a ``Toolbox`` class that
lists tool classes, each tool exposing ``getParameterInfo`` and ``execute``.
Hand-writing one is tedious and easy to get subtly wrong (a bad ``datatype``
string, a mismatched ``parameterType``, a filter that silently does nothing), so
this module builds it from a description and validates the parts ArcGIS would
otherwise reject at load time.

Deliberately **pure Python** — no ``arcpy`` import anywhere in this file. That
keeps it unit-testable with no ArcGIS installed (see ``tests/test_core.py``) and
means the agent can author a toolbox on a machine that will never run it.

The generated toolbox is real, working code: parameters, filters, dependencies,
``updateParameters`` logic, licensing checks and an ``execute`` body that either
runs the geoprocessing steps you declared or hosts your own Python.
"""
from __future__ import annotations

import keyword
import os
import re
from typing import Any

from .._result import err, guard, ok

# The GP datatypes that cover essentially every real toolbox parameter. ArcGIS
# accepts more, but an unknown string here produces a toolbox that loads with an
# invisibly broken parameter — so the list is a whitelist, not a suggestion.
DATATYPES: dict[str, str] = {
    "feature_class": "DEFeatureClass",
    "feature_layer": "GPFeatureLayer",
    "raster": "DERasterDataset",
    "raster_layer": "GPRasterLayer",
    "table": "DETable",
    "table_view": "GPTableView",
    "dataset": "DEDatasetType",
    "workspace": "DEWorkspace",
    "folder": "DEFolder",
    "file": "DEFile",
    "field": "Field",
    "sql_expression": "GPSQLExpression",
    "string": "GPString",
    "long": "GPLong",
    "double": "GPDouble",
    "boolean": "GPBoolean",
    "date": "GPDate",
    "linear_unit": "GPLinearUnit",
    "spatial_reference": "GPSpatialReference",
    "extent": "GPExtent",
    "point": "GPPoint",
    "value_table": "GPValueTable",
    "coded_value_domain": "GPCodedValueDomain",
    "multivalue_string": "GPString",
    "layer": "GPLayer",
    "map": "GPMap",
    "cell_size": "analysis_cell_size",
    "composite": "GPComposite",
}

PARAMETER_TYPES = {"Required", "Optional", "Derived"}
DIRECTIONS = {"Input", "Output"}

_IDENT = re.compile(r"[^0-9a-zA-Z_]")


def _identifier(text: str, fallback: str = "Tool") -> str:
    """Turn any label into a safe Python class/parameter identifier."""
    cleaned = _IDENT.sub("_", str(text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}" if cleaned else fallback
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def _py(value: Any) -> str:
    """Render a Python literal for embedding in the generated source."""
    return repr(value)


def _resolve_datatype(raw: str) -> str | None:
    """Accept either a friendly alias ('feature_class') or a raw GP type."""
    if not raw:
        return None
    key = str(raw).strip()
    if key in DATATYPES:
        return DATATYPES[key]
    lowered = key.lower()
    if lowered in DATATYPES:
        return DATATYPES[lowered]
    # A raw GP datatype the caller knows is valid (DEFeatureClass, GPString…).
    if re.fullmatch(r"(DE|GP)[A-Za-z]+|Field|analysis_cell_size", key):
        return key
    return None


@guard
def list_datatypes() -> dict:
    """The parameter datatypes this generator accepts. Pure lookup."""
    return ok(
        {
            "aliases": DATATYPES,
            "parameter_types": sorted(PARAMETER_TYPES),
            "directions": sorted(DIRECTIONS),
            "note": "Pass an alias (feature_class) or a raw GP datatype (DEFeatureClass).",
        }
    )


def _validate_parameter(p: dict, index: int, seen: set[str]) -> tuple[dict | None, str | None]:
    """Normalise one parameter spec, or return a human-readable reason it is invalid."""
    label = p.get("label") or p.get("name")
    if not label:
        return None, f"Parameter #{index + 1} needs a 'name' or 'label'."

    name = _identifier(p.get("name") or label, f"param{index + 1}")
    if name in seen:
        return None, f"Duplicate parameter name '{name}'."
    seen.add(name)

    datatype = _resolve_datatype(p.get("datatype", "string"))
    if datatype is None:
        return None, (
            f"Parameter '{label}': unknown datatype {p.get('datatype')!r}. "
            f"Valid aliases: {sorted(DATATYPES)}"
        )

    ptype = str(p.get("parameter_type", "Required")).capitalize()
    if ptype not in PARAMETER_TYPES:
        return None, f"Parameter '{label}': parameter_type must be one of {sorted(PARAMETER_TYPES)}."

    direction = str(p.get("direction", "Input")).capitalize()
    if direction not in DIRECTIONS:
        return None, f"Parameter '{label}': direction must be Input or Output."

    # A Derived parameter is computed by the tool and must be an Output — ArcGIS
    # shows a broken tool dialog otherwise.
    if ptype == "Derived" and direction != "Output":
        return None, f"Parameter '{label}': Derived parameters must have direction 'Output'."

    filter_list = p.get("filter_list")
    if filter_list is not None and not isinstance(filter_list, list):
        return None, f"Parameter '{label}': filter_list must be a list."

    return (
        {
            "name": name,
            "label": str(label),
            "datatype": datatype,
            "parameter_type": ptype,
            "direction": direction,
            "multi_value": bool(p.get("multi_value", False)),
            "default": p.get("default"),
            "filter_list": filter_list,
            "filter_range": p.get("filter_range"),
            "depends_on": p.get("depends_on"),
            "category": p.get("category"),
            "description": p.get("description"),
        },
        None,
    )


def _render_parameter(p: dict) -> list[str]:
    """Source lines that build one arcpy.Parameter."""
    lines = [
        "        param = arcpy.Parameter(",
        f"            displayName={_py(p['label'])},",
        f"            name={_py(p['name'])},",
        f"            datatype={_py(p['datatype'])},",
        f"            parameterType={_py(p['parameter_type'])},",
        f"            direction={_py(p['direction'])},",
    ]
    if p["multi_value"]:
        lines.append("            multiValue=True,")
    lines.append("        )")

    if p.get("category"):
        lines.append(f"        param.category = {_py(p['category'])}")
    if p.get("default") is not None:
        lines.append(f"        param.value = {_py(p['default'])}")
    if p.get("filter_list"):
        lines.append(f"        param.filter.list = {_py(p['filter_list'])}")
    if p.get("filter_range"):
        rng = p["filter_range"]
        lines.append('        param.filter.type = "Range"')
        lines.append(f"        param.filter.list = {_py([rng[0], rng[1]])}")
    if p.get("depends_on"):
        deps = p["depends_on"]
        deps = [deps] if isinstance(deps, str) else list(deps)
        lines.append(f"        param.parameterDependencies = {_py(deps)}")
    lines.append("        params.append(param)")
    lines.append("")
    return lines


def _render_execute(tool: dict, params: list[dict]) -> list[str]:
    """The execute() body: declared GP steps, or the caller's own code."""
    lines = [
        "    def execute(self, parameters, messages):",
        '        """Run the tool."""',
    ]
    for i, p in enumerate(params):
        lines.append(f"        {p['name']} = parameters[{i}].valueAsText")
    lines.append("")

    code = tool.get("execute_code")
    if code:
        # Caller-supplied body, re-indented into the method.
        for raw in str(code).splitlines():
            lines.append(("        " + raw) if raw.strip() else "")
        lines.append("")
        return lines

    steps = tool.get("steps") or []
    if not steps:
        lines.append('        arcpy.AddMessage("Tool ran. Add \'steps\' or \'execute_code\' to give it behaviour.")')
        lines.append("        return")
        lines.append("")
        return lines

    lines.append("        arcpy.env.overwriteOutput = True")
    for n, step in enumerate(steps, start=1):
        tool_name = step.get("tool")
        args = step.get("args") or []
        label = step.get("label") or tool_name
        rendered = ", ".join(_render_arg(a, params) for a in args)
        lines.append(f'        arcpy.AddMessage("[{n}/{len(steps)}] {label}")')
        lines.append(f"        step{n} = arcpy.{tool_name}({rendered})")
    lines.append("")
    lines.append(f'        arcpy.AddMessage("Done.")')
    lines.append(f"        return step{len(steps)}" if steps else "        return")
    lines.append("")
    return lines


def _render_arg(arg: Any, params: list[dict]) -> str:
    """``{{param_name}}`` -> the local variable; ``{{stepN}}`` -> that step's result."""
    if not isinstance(arg, str):
        return _py(arg)
    m = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", arg)
    if not m:
        return _py(arg)
    token = m.group(1)
    if re.fullmatch(r"step\d+", token):
        return token
    known = {p["name"] for p in params}
    if token in known:
        return token
    return _py(arg)


def _render_tool(tool: dict, params: list[dict]) -> list[str]:
    cls = tool["class_name"]
    lines = [
        f"class {cls}(object):",
        f'    """{tool.get("description") or tool["label"]}"""',
        "",
        "    def __init__(self):",
        f"        self.label = {_py(tool['label'])}",
        f"        self.description = {_py(tool.get('description') or tool['label'])}",
        f"        self.canRunInBackground = {_py(bool(tool.get('can_run_in_background', False)))}",
        f"        self.category = {_py(tool.get('category'))}" if tool.get("category") else "",
        "",
        "    def getParameterInfo(self):",
        '        """Define the tool parameters."""',
        "        params = []",
        "",
    ]
    for p in params:
        lines.extend(_render_parameter(p))
    lines.append("        return params")
    lines.append("")

    extensions = tool.get("required_extensions") or []
    lines.append("    def isLicensed(self):")
    if extensions:
        lines.append('        """Require the extensions this tool depends on."""')
        lines.append("        try:")
        for ext in extensions:
            lines.append(f'            if arcpy.CheckExtension({_py(ext)}) != "Available":')
            lines.append("                return False")
        lines.append("        except Exception:")
        lines.append("            return False")
        lines.append("        return True")
    else:
        lines.append("        return True")
    lines.append("")

    lines.append("    def updateParameters(self, parameters):")
    lines.append('        """Adjust parameters before validation."""')
    update = tool.get("update_parameters_code")
    if update:
        for raw in str(update).splitlines():
            lines.append(("        " + raw) if raw.strip() else "")
    else:
        lines.append("        return")
    lines.append("")

    lines.append("    def updateMessages(self, parameters):")
    lines.append('        """Custom validation messages."""')
    validations = tool.get("validations") or []
    if validations:
        by_name = {p["name"]: i for i, p in enumerate(params)}
        for v in validations:
            idx = by_name.get(v.get("parameter"))
            if idx is None:
                continue
            cond = v.get("condition")
            msg = v.get("message", "Invalid value.")
            level = "setErrorMessage" if v.get("level", "error") == "error" else "setWarningMessage"
            lines.append(f"        if parameters[{idx}].value is not None:")
            lines.append(f"            value = parameters[{idx}].value")
            lines.append(f"            if {cond}:")
            lines.append(f"                parameters[{idx}].{level}({_py(msg)})")
        lines.append("        return")
    else:
        lines.append("        return")
    lines.append("")

    lines.extend(_render_execute(tool, params))
    lines.append("    def postExecute(self, parameters):")
    lines.append('        """Runs after outputs are added to the display."""')
    lines.append("        return")
    lines.append("")
    return lines


@guard
def create_python_toolbox(
    out_path: str,
    label: str,
    tools: list[dict],
    alias: str | None = None,
    description: str | None = None,
    overwrite: bool = True,
) -> dict:
    """Write a working ``.pyt`` from a declarative spec.

    ``tools`` entries::

        {"name": "BufferRoads",              # class name (derived from label if absent)
         "label": "Buffer Roads",
         "description": "...",
         "category": "Analysis",             # optional toolset
         "required_extensions": ["Spatial"], # optional isLicensed() gate
         "parameters": [
            {"name": "roads", "label": "Road layer", "datatype": "feature_class"},
            {"name": "distance", "label": "Distance", "datatype": "linear_unit",
             "default": "100 Meters"},
            {"name": "output", "label": "Output", "datatype": "feature_class",
             "direction": "Output"}
         ],
         "steps": [                          # OR "execute_code" for hand-written logic
            {"tool": "analysis.Buffer",
             "args": ["{{roads}}", "{{output}}", "{{distance}}"]}
         ],
         "validations": [
            {"parameter": "distance", "condition": "value <= 0",
             "message": "Distance must be positive."}
         ]}

    Inside ``steps.args``, ``{{param_name}}`` refers to a parameter and
    ``{{stepN}}`` to an earlier step's result.
    """
    if not tools:
        return err("Provide at least one tool.", code="no_tools")

    out_path = str(out_path)
    if not out_path.lower().endswith(".pyt"):
        out_path += ".pyt"
    if os.path.exists(out_path) and not overwrite:
        return err(f"{out_path} already exists (pass overwrite=True).", code="exists")

    toolbox_alias = _identifier(alias or label, "Toolbox")

    prepared: list[tuple[dict, list[dict]]] = []
    used_classes: set[str] = set()
    for i, tool in enumerate(tools):
        tool_label = tool.get("label") or tool.get("name")
        if not tool_label:
            return err(f"Tool #{i + 1} needs a 'label' or 'name'.")
        cls = _identifier(tool.get("name") or tool_label, f"Tool{i + 1}")
        if cls in used_classes:
            return err(f"Duplicate tool class name '{cls}'.", code="duplicate_tool")
        used_classes.add(cls)

        seen: set[str] = set()
        params: list[dict] = []
        for j, p in enumerate(tool.get("parameters") or []):
            normalised, problem = _validate_parameter(p, j, seen)
            if problem:
                return err(f"{tool_label}: {problem}", code="bad_parameter")
            params.append(normalised)

        prepared.append(({**tool, "label": str(tool_label), "class_name": cls}, params))

    header = [
        '"""',
        f"{label}",
        "",
        (description or f"Generated Python toolbox: {label}."),
        "",
        "Generated by ArcGIS Pro Salah MCP. Safe to edit by hand — it is ordinary",
        "Python; ArcGIS re-reads this file every time the toolbox is refreshed.",
        '"""',
        "",
        "import arcpy",
        "",
        "",
        "class Toolbox(object):",
        '    """The toolbox ArcGIS discovers when this .pyt is opened."""',
        "",
        "    def __init__(self):",
        f"        self.label = {_py(label)}",
        f"        self.alias = {_py(toolbox_alias)}",
        f"        self.description = {_py(description or label)}",
        f"        self.tools = [{', '.join(t['class_name'] for t, _ in prepared)}]",
        "",
        "",
    ]

    body: list[str] = []
    for tool, params in prepared:
        body.extend(_render_tool(tool, params))
        body.append("")

    source = "\n".join(header + body).rstrip() + "\n"

    # Fail loudly here rather than shipping a .pyt that ArcGIS silently refuses.
    try:
        compile(source, out_path, "exec")
    except SyntaxError as exc:
        return err(
            f"Generated toolbox has a syntax error at line {exc.lineno}: {exc.msg}. "
            f"This usually means an 'execute_code' / 'condition' snippet is malformed.",
            code="invalid_source",
            line=exc.lineno,
        )

    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(source)

    return ok(
        {
            "toolbox": os.path.abspath(out_path),
            "alias": toolbox_alias,
            "tools": [
                {
                    "class": t["class_name"],
                    "label": t["label"],
                    "parameters": [p["name"] for p in params],
                    "parameter_count": len(params),
                }
                for t, params in prepared
            ],
            "tool_count": len(prepared),
            "lines": source.count("\n") + 1,
            "usage": (
                f"In ArcGIS Pro: Catalog > Toolboxes > Add Toolbox > {os.path.basename(out_path)}. "
                f"From Python: arcpy.ImportToolbox(r'{os.path.abspath(out_path)}')"
            ),
        }
    )
