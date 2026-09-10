"""Utility-network cartography using Esri's classic AM/FM symbol fonts.

Why a separate module
---------------------
``ops.apply_categorized_symbology`` drives the high-level ``lyr.symbology`` API,
which can colour features but cannot choose a *glyph*. Utility mapping is all
glyph: a gate valve is a bowtie, a hydrant is a circle with nozzles, a meter is
an M in a box. Those are drawn from character-marker fonts, which only the CIM
reaches — so this module builds renderers directly in CIM.

Where the symbols come from
---------------------------
**ESRI AM/FM Water** (``esri_153.ttf``) and **ESRI AM/FM Gas** (``esri_151.ttf``)
ship with ArcGIS Pro. AM/FM (Automated Mapping / Facilities Management) is the
long-standing utility convention these fonts encode, and the glyph numbers in
:data:`AMFM_WATER` were read off the installed font and visually confirmed
rather than copied from memory.

Colour follows the same convention every water utility map uses: blue for
potable water, red for fire protection, a darker blue for transmission mains
than for service laterals.
"""
from __future__ import annotations

from typing import Any

from .._result import err, guard, ok
from . import ops

WATER_FONT = "ESRI AMFM Water"
GAS_FONT = "ESRI AMFM Gas"

# Glyph codes verified against the installed esri_153.ttf by rendering the font
# and inspecting it — not guessed. Keep the comments: the numbers are opaque.
AMFM_WATER: dict[str, int] = {
    "valve_open": 99,          # hollow bowtie
    "valve": 100,              # filled bowtie — the classic gate-valve symbol
    "valve_vertical": 104,     # filled hourglass (bowtie rotated 90 deg)
    "hydrant_simple": 123,     # filled circle on a stem
    "hydrant": 125,            # circle with nozzles on a stem — fire hydrant
    "hydrant_head": 177,       # nozzle burst, no stem
    "meter": 75,               # M in a box
    "meter_large": 211,        # LM in a circle
    "fitting": 38,             # filled circle
    "fitting_open": 44,        # hollow circle
    "tee": 45,
    "pump": 73,                # P in a box
    "tank": 76,                # T in a box
    "control_valve": 74,       # C in a box
    "backflow": 79,            # BF
}

# Water-utility palette (RGB). Potable blue, fire red, service teal.
COLORS: dict[str, list[int]] = {
    "transmission_blue": [0, 77, 168, 100],
    "main_blue": [0, 112, 255, 100],
    "service_blue": [115, 178, 255, 100],
    "hydrant_red": [230, 0, 0, 100],
    "valve_black": [38, 38, 38, 100],
    "meter_green": [56, 168, 0, 100],
    "fitting_grey": [104, 104, 104, 100],
    "territory_fill": [0, 112, 255, 8],
    "territory_line": [0, 77, 168, 60],
}


def _cim(class_name: str):
    import arcpy

    return arcpy.cim.CreateCIMObjectFromClassName(class_name, "V3")


def _rgb(rgb: list[int]):
    c = _cim("CIMRGBColor")
    c.values = list(rgb)
    return c


def _character_marker(char_index: int, color: list[int], size: float,
                      font: str = WATER_FONT, angle: float = 0.0):
    """One AM/FM glyph as a CIM point symbol layer."""
    marker = _cim("CIMCharacterMarker")
    marker.enable = True
    marker.characterIndex = int(char_index)
    marker.fontFamilyName = font
    marker.fontStyleName = "Regular"
    marker.size = float(size)
    marker.rotation = float(angle)
    marker.scaleSymbolsProportionally = True
    marker.respectFrame = True

    # A character marker paints through a polygon symbol, so the fill is what
    # actually gives the glyph its colour.
    fill = _cim("CIMSolidFill")
    fill.enable = True
    fill.color = _rgb(color)
    poly = _cim("CIMPolygonSymbol")
    poly.symbolLayers = [fill]
    marker.symbol = poly
    return marker


def _point_symbol(char_index: int, color: list[int], size: float,
                  font: str = WATER_FONT, angle: float = 0.0):
    sym = _cim("CIMPointSymbol")
    sym.symbolLayers = [_character_marker(char_index, color, size, font, angle)]
    ref = _cim("CIMSymbolReference")
    ref.symbol = sym
    return ref


def _line_symbol(color: list[int], width: float, dash: list[float] | None = None):
    stroke = _cim("CIMSolidStroke")
    stroke.enable = True
    stroke.color = _rgb(color)
    stroke.width = float(width)
    stroke.capStyle = "Round"
    stroke.joinStyle = "Round"
    if dash:
        effect = _cim("CIMGeometricEffectDashes")
        effect.dashTemplate = list(dash)
        effect.lineDashEnding = "NoConstraint"
        stroke.effects = [effect]
    sym = _cim("CIMLineSymbol")
    sym.symbolLayers = [stroke]
    ref = _cim("CIMSymbolReference")
    ref.symbol = sym
    return ref


def _polygon_symbol(fill_color: list[int], outline_color: list[int], width: float = 1.0):
    fill = _cim("CIMSolidFill")
    fill.enable = True
    fill.color = _rgb(fill_color)
    stroke = _cim("CIMSolidStroke")
    stroke.enable = True
    stroke.color = _rgb(outline_color)
    stroke.width = float(width)
    sym = _cim("CIMPolygonSymbol")
    sym.symbolLayers = [stroke, fill]
    ref = _cim("CIMSymbolReference")
    ref.symbol = sym
    return ref


def _apply_symbols_to_renderer(renderer, field: str, wanted: dict, fallback=None) -> list[dict]:
    """Swap our symbols into a renderer ArcGIS already built.

    Building a ``CIMUniqueValueRenderer`` from scratch produces an object that
    *looks* right — correct fields, groups, classes and symbols — and draws
    NOTHING. Verified by rendering: a hand-built renderer yields a blank map
    while the structure inspects as valid. Rather than reverse-engineering which
    undocumented member matters, let ``lyr.symbology.updateRenderer`` construct
    the renderer (ArcGIS then enumerates the real field values itself) and only
    replace each class's symbol here. Slower by one round trip, correct by
    construction.
    """
    applied = []
    for group in renderer.groups:
        for cls in group.classes:
            values = [v for uv in cls.values for v in (uv.fieldValues or [])]
            key = str(values[0]) if values else None
            spec = wanted.get(key)
            if spec is None and fallback is not None:
                spec = fallback
            if spec is None:
                continue
            cls.symbol = spec["symbol"]
            if spec.get("label"):
                cls.label = spec["label"]
            applied.append({"value": key, "label": cls.label})
    return applied


def _find_layer(aprx, layer_name: str, map_name: str | None):
    for m in aprx.listMaps():
        if map_name and m.name != map_name:
            continue
        for lyr in m.listLayers():
            if lyr.name == layer_name:
                return m, lyr
    raise ValueError(f"Layer '{layer_name}' not found.")


@guard
def list_symbols(font: str = "water") -> dict:
    """The named AM/FM glyphs available, with their font and character code.

    Pure lookup — no ArcPy. Use the names as ``symbol`` values in
    :func:`apply_unique_value_symbology`.
    """
    if font.lower() not in {"water", "gas"}:
        return err("font must be 'water' or 'gas'.")
    return ok(
        {
            "font": WATER_FONT if font.lower() == "water" else GAS_FONT,
            "symbols": AMFM_WATER,
            "colors": COLORS,
            "note": (
                "These character codes were read from the installed ESRI AM/FM "
                "font, not guessed. 'valve' is the classic filled bowtie, "
                "'hydrant' the circle-with-nozzles, 'meter' an M in a box."
            ),
        }
    )


def _symbolize(aprx, layer_name: str, field: str, classes: list[dict],
               map_name: str | None = None, default_label: str | None = None) -> dict:
    """Symbolise one layer inside an ALREADY-OPEN project.

    ArcGIS locks an ``.aprx`` per process, so a caller that styles several layers
    must reuse one project handle — opening it again mid-call fails with an error
    whose entire message is the file path.
    """
    arcpy = ops._arcpy()
    _, lyr = _find_layer(aprx, layer_name, map_name)

    if not lyr.isFeatureLayer:
        return err(f"'{layer_name}' is not a feature layer.", code="not_feature_layer")

    geometry = arcpy.Describe(lyr.dataSource).shapeType.lower()

    def resolve_color(value) -> list[int]:
        if isinstance(value, str):
            if value not in COLORS:
                raise ValueError(
                    f"Unknown colour '{value}'. Known: {sorted(COLORS)} — or pass [r,g,b,a]."
                )
            return COLORS[value]
        return list(value) if value else COLORS["fitting_grey"]

    def resolve_char(value) -> int:
        if isinstance(value, str):
            if value not in AMFM_WATER:
                raise ValueError(
                    f"Unknown symbol '{value}'. Known: {sorted(AMFM_WATER)} — "
                    f"or pass a character code."
                )
            return AMFM_WATER[value]
        return int(value)

    def build_symbol(entry):
        color = resolve_color(entry.get("color"))
        if geometry == "point":
            return _point_symbol(
                resolve_char(entry.get("symbol", "fitting")), color,
                float(entry.get("size", 12)), entry.get("font", WATER_FONT),
                float(entry.get("angle", 0)),
            )
        if geometry == "polyline":
            return _line_symbol(color, float(entry.get("width", 2)), entry.get("dash"))
        return _polygon_symbol(
            color, resolve_color(entry.get("outline_color")),
            float(entry.get("width", 1)),
        )

    wanted = {
        str(entry["value"]): {
            "symbol": build_symbol(entry),
            "label": entry.get("label", str(entry["value"])),
        }
        for entry in classes
    }
    # A single-class spec is also how you restyle a whole layer, so let it act as
    # the catch-all rather than silently matching nothing.
    fallback = next(iter(wanted.values())) if len(classes) == 1 else None

    # Let ArcGIS build the renderer; it enumerates the field's real values.
    sym = lyr.symbology
    if not hasattr(sym, "renderer") or sym.renderer.type != "UniqueValueRenderer":
        sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [field]
    lyr.symbology = sym

    cim_lyr = lyr.getDefinition("V3")
    applied = _apply_symbols_to_renderer(cim_lyr.renderer, field, wanted, fallback)
    if default_label:
        cim_lyr.renderer.defaultLabel = default_label
    lyr.setDefinition(cim_lyr)

    unmatched = sorted(set(wanted) - {str(a["value"]) for a in applied})
    payload = {
        "layer": layer_name,
        "field": field,
        "geometry": geometry,
        "classes": applied,
        "class_count": len(applied),
    }
    if unmatched:
        payload["unmatched_values"] = unmatched
        payload["note"] = (
            f"No feature has {field} = {unmatched} — those classes were skipped."
        )
    return ok(payload)


@guard
def apply_unique_value_symbology(
    aprx_path: str,
    layer_name: str,
    field: str,
    classes: list[dict],
    map_name: str | None = None,
    default_label: str | None = None,
) -> dict:
    """Symbolise a layer by field value using AM/FM glyphs or styled lines.

    Each entry in ``classes``::

        {"value": 10, "label": "Gate valve",
         "symbol": "valve",            # a name from pro_symbols, or a char code
         "color": "hydrant_red",       # a name from pro_symbols, or [r,g,b,a]
         "size": 12}                   # points; line entries use "width"/"dash"

    Point layers get character markers; line layers get strokes (pass ``width``
    and optionally ``dash``); polygon layers get a fill plus outline.
    """
    arcpy = ops._arcpy()
    aprx = arcpy.mp.ArcGISProject(aprx_path)
    result = _symbolize(aprx, layer_name, field, classes, map_name, default_label)
    aprx.save()
    return result


# The opinionated water-utility preset: which asset group gets which glyph.
WATER_PRESET: dict[str, dict] = {
    "device": {
        "field": "ASSETGROUP",
        "classes": [
            {"value": 10, "label": "Valve", "symbol": "valve",
             "color": "valve_black", "size": 18},
            {"value": 20, "label": "Hydrant", "symbol": "hydrant",
             "color": "hydrant_red", "size": 20},
            {"value": 30, "label": "Meter", "symbol": "meter",
             "color": "meter_green", "size": 16},
        ],
    },
    "line": {
        "field": "ASSETGROUP",
        "classes": [
            {"value": 10, "label": "Main", "color": "main_blue", "width": 3.5},
            {"value": 20, "label": "Service lateral", "color": "service_blue",
             "width": 1.6, "dash": [6, 3]},
        ],
    },
}


@guard
def apply_water_utility_symbology(
    aprx_path: str,
    device_layer: str = "Water Device",
    line_layer: str = "Water Line",
    territory_layer: str | None = "ServiceTerritory",
    map_name: str | None = None,
) -> dict:
    """Apply the classic AM/FM water symbology to a utility network in one call.

    Valves become filled bowties, hydrants circles-with-nozzles in red, meters
    an M in a box, mains thick blue and service laterals thin dashed blue — the
    conventional look of a water utility map, with no style pack to install.
    """
    arcpy = ops._arcpy()
    # One handle for all three layers: see _symbolize on the per-process lock.
    aprx = arcpy.mp.ArcGISProject(aprx_path)

    jobs = [
        (device_layer, WATER_PRESET["device"]["field"], WATER_PRESET["device"]["classes"]),
        (line_layer, WATER_PRESET["line"]["field"], WATER_PRESET["line"]["classes"]),
    ]
    if territory_layer:
        jobs.append((
            territory_layer, "OBJECTID",
            [{"value": 1, "label": "Service territory", "color": "territory_fill",
              "outline_color": "territory_line", "width": 1.5}],
        ))

    results = []
    for layer_name, field, classes in jobs:
        try:
            outcome = _symbolize(aprx, layer_name, field, classes, map_name)
            results.append({"layer": layer_name, "ok": True,
                            "classes": outcome.get("classes")})
        except Exception as exc:  # noqa: BLE001 - one bad layer must not sink the rest
            results.append({"layer": layer_name, "ok": False,
                            "error": str(exc).strip().splitlines()[0][:200]})

    aprx.save()

    applied = sum(1 for r in results if r["ok"])
    payload = {"layers": results, "applied": applied, "attempted": len(results),
               "font": WATER_FONT}
    if applied == len(results):
        return ok(payload)
    return {"ok": False, "error": f"{len(results) - applied} layer(s) failed.",
            "data": payload}
