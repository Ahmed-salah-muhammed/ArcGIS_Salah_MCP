"""Generate the Salah MCP ribbon button icons from the official Calcite UI icons.

Each button uses a real icon from the Calcite Design System
(https://developers.arcgis.com/calcite-design-system/icons/), fetched from the
Esri/calcite-ui-icons repo, rasterised with matplotlib (pure Python — no native
Cairo needed), tinted to a per-button accent colour, and saved as crisp 32px and
16px PNGs on a transparent background. One hue per button so they read as a set.

Dev-only tooling. Requires: matplotlib, svgpath2mpl, Pillow. Run with any Python:
    python SalahAIBridge/Images/make_icons.py

Note: ZoomToActiveLayer{16,32}.png are NOT produced here — they ship as-is with
the add-in. Running this script leaves them untouched.
"""
from __future__ import annotations

import io
import os
import urllib.request
import xml.etree.ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402
from svgpath2mpl import parse_path  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 256                       # supersampled raster, downscaled with LANCZOS
MARGIN = 0.12                  # fraction of the canvas kept as padding
ICON_BASE = "https://raw.githubusercontent.com/Esri/calcite-ui-icons/master/icons/"
SVG_NS = "{http://www.w3.org/2000/svg}"

# Esri / Calcite palette — one hue per button so they stay distinguishable.
GREEN = (53, 172, 70)          # Start Server
CYAN = (0, 169, 181)           # Ping
BLUE = (0, 122, 194)           # Publish
ORANGE = (240, 132, 10)        # Create Web App (preview)
PURPLE = (143, 79, 209)        # Deploy Web App
INDIGO = (88, 86, 214)         # Create Dashboard

# Button file stem -> (Calcite icon name, accent colour). The icon names are the
# exact slugs from the Calcite icons site / calcite-ui-icons repo.
BUTTONS = {
    "StartServer": ("play", GREEN),     # start / stop the loopback listener
    "Ping": ("gauge", CYAN),            # health check
    "Publish": ("upload-to", BLUE),     # upload layers to ArcGIS Online
    "PreviewApp": ("browser", ORANGE),  # create + preview the web app
    # Deploy the web app to GitHub. Calcite has no brand icons, so this is the
    # GitHub mark from GitHub's own Octicons (MIT-licensed).
    "CreateApp": (
        "https://raw.githubusercontent.com/primer/octicons/main/icons/mark-github-24.svg",
        PURPLE,
    ),
    # 'dashboard' in Calcite is a gauge dial — too close to Ping's gauge — so use
    # a bar-chart glyph, which reads clearly as analytics/dashboard.
    "Dashboard": ("graph-bar", INDIGO),  # create the interactive dashboard
}


def _fetch_svg(source: str) -> bytes:
    # A full URL is used verbatim (e.g. GitHub's Octicons for the GitHub mark);
    # a bare slug resolves to the matching Calcite icon at its 32px artboard.
    url = source if source.startswith("http") else ICON_BASE + source + "-32.svg"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _render_alpha(svg: bytes) -> Image.Image:
    """Rasterise a Calcite icon to an ``SS``-px anti-aliased alpha mask.

    Calcite icons are a single black glyph path (holes encoded by nonzero
    winding) plus a ``fill="none"`` artboard frame we ignore. We draw the glyph
    black on white, then take the inverted luminance as the alpha — so holes
    (white) stay transparent and anti-aliased edges become partial alpha.
    """
    root = ET.fromstring(svg)
    vb = (root.get("viewBox") or "0 0 32 32").replace(",", " ").split()
    vw, vh = float(vb[2]), float(vb[3])

    paths = [
        parse_path(p.get("d"))
        for p in root.iter(SVG_NS + "path")
        if p.get("d") and (p.get("fill") or "").lower() != "none"
    ]
    compound = MplPath.make_compound_path(*paths)

    fig = plt.figure(figsize=(1, 1), dpi=SS)
    ax = fig.add_axes([MARGIN, MARGIN, 1 - 2 * MARGIN, 1 - 2 * MARGIN])
    ax.set_xlim(0, vw)
    ax.set_ylim(0, vh)
    ax.invert_yaxis()          # SVG y grows downward
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(PathPatch(compound, facecolor="black", edgecolor="none", antialiased=True))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=SS, facecolor="white")
    plt.close(fig)

    gray = Image.open(buf).convert("L").resize((SS, SS), Image.LANCZOS)
    return ImageOps.invert(gray)


def _tint(alpha: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    glyph = Image.new("RGBA", alpha.size, color + (0,))
    glyph.putalpha(alpha)
    return glyph


def main(out_dir: str = HERE) -> None:
    for stem, (icon, color) in BUTTONS.items():
        glyph = _tint(_render_alpha(_fetch_svg(icon)), color)
        for size in (32, 16):
            glyph.resize((size, size), Image.LANCZOS).save(
                os.path.join(out_dir, f"{stem}{size}.png")
            )
        print(f"wrote {stem}  <- calcite '{icon}'")


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else HERE)
