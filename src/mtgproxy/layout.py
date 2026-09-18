"""Cut geometry for a paper/card layout, straight from the engine's own layout code.

``generate_dxf.py single`` (the same layout engine that placed the cards in the
PDF) produces a DXF in page coordinates with the origin top-left and Y going
NEGATIVE downwards; this converts it into a page-sized SVG in millimetres,
Y-down positive from the same origin, which inkscape-silhouette can send to
the cutter directly. Rounded corners come through as true arcs.

Registration marks are NOT drawn: the cutter finds the printed ones itself. The
mark geometry it needs (inset from the page edge, mark-to-mark distances) is
derived from the same assets/layouts.json defaults create_pdf.py uses.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import engine


@dataclass
class Geometry:
    paper: str
    card_size: str
    cards: int
    page_w_mm: float
    page_h_mm: float
    reg_inset_mm: float
    reg_x_mm: float
    reg_y_mm: float
    cut_bbox_mm: tuple[float, float, float, float]

    def as_dict(self) -> dict:
        return asdict(self)


def fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def polyline_to_svg_path(entity) -> str:
    """One closed DXF POLYLINE (lines + bulge arcs) → SVG path (Y negated)."""
    parts: list[str] = []
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
            raise LayoutError(f"unsupported DXF sub-entity {kind}")
    parts.append("Z")
    return " ".join(parts)


class LayoutError(Exception):
    pass


def page_size_mm(paper: str, card_size: str) -> tuple[float, float]:
    layouts = engine.layouts()
    if paper not in layouts["paper_sizes"]:
        raise LayoutError(
            f"unknown paper size {paper!r} (known: {', '.join(sorted(layouts['paper_sizes']))})"
        )
    paper_def = layouts["paper_sizes"][paper]
    w, h = engine.size_to_mm(paper_def["width"]), engine.size_to_mm(paper_def["height"])
    sizes = layouts["layouts"].get(paper, {})
    if card_size not in sizes:
        raise LayoutError(
            f"no {card_size!r} layout for {paper!r} (available: {', '.join(sorted(sizes)) or 'none'})"
        )
    layout = sizes[card_size]["default"]
    # create_pdf.py lays the page out in the layout's orientation; the DXF matches it.
    if layout.get("orientation") == "landscape" and w < h:
        w, h = h, w
    if layout.get("orientation") == "portrait" and w > h:
        w, h = h, w
    return w, h


def registration_inset_mm() -> float:
    return engine.size_to_mm(engine.layouts()["defaults"]["registration"]["default"]["inset"])


def build_cut_svg(paper: str, card_size: str, out: Path | None) -> Geometry:
    """Write the cut-path SVG (unless ``out`` is None) and return the geometry."""
    import ezdxf

    page_w, page_h = page_size_mm(paper, card_size)
    inset = registration_inset_mm()

    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "cut.dxf"
        engine.run_engine_script(
            "generate_dxf.py", ["single", str(dxf_path), "--card_size", card_size, "--paper_size", paper]
        )
        doc = ezdxf.readfile(dxf_path)

    paths: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    for e in doc.modelspace():
        if e.dxftype() not in ("POLYLINE", "LWPOLYLINE"):
            raise LayoutError(f"unexpected DXF entity {e.dxftype()}")
        paths.append(polyline_to_svg_path(e))
        for v in e.vertices if e.dxftype() == "POLYLINE" else e:
            loc = v.dxf.location if e.dxftype() == "POLYLINE" else v
            xs.append(loc.x if hasattr(loc, "x") else loc[0])
            ys.append(-(loc.y if hasattr(loc, "y") else loc[1]))
    if not paths:
        raise LayoutError("DXF contained no card outlines")
    if min(xs) < inset or min(ys) < inset or max(xs) > page_w - inset or max(ys) > page_h - inset:
        raise LayoutError("card outlines fall outside the registration-mark frame")

    if out is not None:
        body = "\n".join(
            f'  <path id="card{i + 1}" d="{d}" fill="none" stroke="#ff0000" stroke-width="0.1"/>'
            for i, d in enumerate(paths)
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{fmt(page_w)}mm" height="{fmt(page_h)}mm" viewBox="0 0 {fmt(page_w)} {fmt(page_h)}">\n'
            f"  <!-- {paper}/{card_size}: {len(paths)} cards, generated by mtg-proxy from silhouette-card-maker -->\n"
            f"{body}\n</svg>\n"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg)

    return Geometry(
        paper=paper,
        card_size=card_size,
        cards=len(paths),
        page_w_mm=round(page_w, 3),
        page_h_mm=round(page_h, 3),
        reg_inset_mm=round(inset, 3),
        reg_x_mm=round(page_w - 2 * inset, 3),
        reg_y_mm=round(page_h - 2 * inset, 3),
        cut_bbox_mm=(round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)),
    )
