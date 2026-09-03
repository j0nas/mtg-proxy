#!/usr/bin/env python3
"""Build the cut-path SVG for a paper/card layout, straight from silhouette-card-maker.

Runs `generate_dxf.py single` from the vendored silhouette-card-maker (the same
layout engine that placed the cards in the PDF) and converts the resulting DXF
into a page-sized SVG in millimetres, which inkscape-silhouette's
sendto_silhouette.py can send to the cutter directly — no Silhouette Studio.

The DXF is in page coordinates with the origin at the top-left and Y going
NEGATIVE downwards (generate_dxf.py's convention); the SVG is Y-down positive
from the same origin, so Y is negated. Rounded corners come through as
true arcs (the DXF polylines carry bulges), not as flattened polylines.

Registration marks are NOT drawn: the cutter finds the printed ones itself.
The mark geometry it needs (inset from the page edge, mark-to-mark distances)
is printed as JSON on stdout for the calling script, derived from the same
assets/layouts.json defaults create_pdf.py uses.

Usage: cut_svg.py --paper a4 --card_size standard --out cut.svg
Run with silhouette-card-maker's venv python (needs ezdxf + click on that path).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import ezdxf

ROOT = Path(__file__).resolve().parent.parent
SCM = ROOT / "silhouette-card-maker"


def size_to_mm(s: str) -> float:
    sys.path.insert(0, str(SCM))
    import size_convert  # noqa: E402  (silhouette-card-maker module)

    return size_convert.size_to_mm(s)


def fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def polyline_to_svg_path(entity) -> str:
    """Convert one closed DXF POLYLINE (lines + bulge arcs) to an SVG path (Y negated)."""
    parts = []
    first = True
    for ve in entity.virtual_entities():
        kind = ve.dxftype()
        if kind == "LINE":
            sx, sy = ve.dxf.start.x, -ve.dxf.start.y
            ex, ey = ve.dxf.end.x, -ve.dxf.end.y
            if first:
                parts.append(f"M{fmt(sx)},{fmt(sy)}")
                first = False
            parts.append(f"L{fmt(ex)},{fmt(ey)}")
        elif kind == "ARC":
            r = ve.dxf.radius
            sp, ep = ve.start_point, ve.end_point
            sx, sy = sp.x, -sp.y
            ex, ey = ep.x, -ep.y
            if first:
                parts.append(f"M{fmt(sx)},{fmt(sy)}")
                first = False
            cx, cy = ve.dxf.center.x, -ve.dxf.center.y
            # SVG sweep-flag 1 = positive-angle direction in the SVG frame. The sign of
            # the cross product (start-centre) x (end-centre) gives that direction for
            # the short arc; the arc angle decides whether the short or long arc is meant.
            cross = (sx - cx) * (ey - cy) - (sy - cy) * (ex - cx)
            arc_deg = (ve.dxf.end_angle - ve.dxf.start_angle) % 360.0
            large = 1 if arc_deg > 180.0 else 0
            sweep = (1 if cross > 0 else 0) if not large else (0 if cross > 0 else 1)
            parts.append(f"A{fmt(r)},{fmt(r)} 0 {large} {sweep} {fmt(ex)},{fmt(ey)}")
        else:
            raise SystemExit(f"cut_svg: unsupported DXF sub-entity {kind}")
    parts.append("Z")
    return " ".join(parts)


@click.command()
@click.option("--paper", "paper", required=True, help="paper size name (a4, letter, ...)")
@click.option("--card_size", "card_size", default="standard", show_default=True)
@click.option("--out", "out", required=True, type=click.Path(dir_okay=False), help="output SVG path")
def main(paper: str, card_size: str, out: str) -> None:
    layouts = json.loads((SCM / "assets" / "layouts.json").read_text())
    paper_def = layouts["paper_sizes"][paper]
    page_w = size_to_mm(paper_def["width"])
    page_h = size_to_mm(paper_def["height"])
    reg = layouts["defaults"]["registration"]["default"]
    inset = size_to_mm(reg["inset"])
    layout = layouts["layouts"][paper][card_size]["default"]
    # create_pdf.py lays the page out in the layout's orientation; the DXF matches it.
    if layout.get("orientation") == "landscape" and page_w < page_h:
        page_w, page_h = page_h, page_w
    if layout.get("orientation") == "portrait" and page_w > page_h:
        page_w, page_h = page_h, page_w

    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "cut.dxf"
        subprocess.run(
            [sys.executable, "generate_dxf.py", "single", str(dxf_path),
             "--card_size", card_size, "--paper_size", paper],
            cwd=SCM, check=True, stdout=subprocess.DEVNULL,
        )
        doc = ezdxf.readfile(dxf_path)

    paths = []
    xs, ys = [], []
    for e in doc.modelspace():
        if e.dxftype() not in ("POLYLINE", "LWPOLYLINE"):
            raise SystemExit(f"cut_svg: unexpected DXF entity {e.dxftype()}")
        paths.append(polyline_to_svg_path(e))
        for v in (e.vertices if e.dxftype() == "POLYLINE" else e):
            loc = v.dxf.location if e.dxftype() == "POLYLINE" else v
            xs.append(loc.x if hasattr(loc, "x") else loc[0])
            ys.append(-(loc.y if hasattr(loc, "y") else loc[1]))
    if not paths:
        raise SystemExit("cut_svg: DXF contained no card outlines")
    if min(xs) < inset or min(ys) < inset or max(xs) > page_w - inset or max(ys) > page_h - inset:
        raise SystemExit("cut_svg: card outlines fall outside the registration-mark frame")

    body = "\n".join(
        f'  <path id="card{i + 1}" d="{d}" fill="none" stroke="#ff0000" stroke-width="0.1"/>'
        for i, d in enumerate(paths)
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'width="{fmt(page_w)}mm" height="{fmt(page_h)}mm" viewBox="0 0 {fmt(page_w)} {fmt(page_h)}">\n'
        f'  <!-- {paper}/{card_size}: {len(paths)} cards, generated by tools/cut_svg.py from silhouette-card-maker -->\n'
        f'{body}\n</svg>\n'
    )
    Path(out).write_text(svg)

    print(json.dumps({
        "paper": paper, "card_size": card_size, "cards": len(paths),
        "page_w_mm": round(page_w, 3), "page_h_mm": round(page_h, 3),
        "reg_inset_mm": round(inset, 3),
        "reg_x_mm": round(page_w - 2 * inset, 3),
        "reg_y_mm": round(page_h - 2 * inset, 3),
        "cut_bbox_mm": [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)],
    }))


if __name__ == "__main__":
    main()
