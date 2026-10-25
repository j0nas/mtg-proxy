"""Cut geometry for a paper/card layout, straight from the engine's own layout code.

``generate_dxf.py single`` (the same layout engine that placed the cards in the
PDF) produces a DXF in page coordinates with the origin top-left and Y going
NEGATIVE downwards; this converts it into a page-sized SVG in millimetres,
Y-down positive from the same origin, which cut-proxies sends to the cutter
directly. Rounded corners come through as true arcs.

Registration marks are NOT drawn: the cutter finds the printed ones itself. The
mark geometry it needs (inset from the page edge, mark-to-mark distances, leg
length) is derived from the same assets/layouts.json entries create_pdf.py uses.

A sheet has one of two layouts, both printed with 3-mark registration. ``3x3``, the default
on A4 with standard cards, is nine cards on portrait A4 with the marks 3.5 mm from the sides and
9 mm from the top and bottom: the engine's ``borderless`` variant, its name for the tighter inset,
which has nothing to do with borderless printing (that stays off). ``4x2`` is the engine's own
layout for the paper (four by two standard cards on landscape A4, marks 10 mm in), and the
default for every other paper and card size.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import engine

DEFAULT = "3x3"  # on the paper and card it is built for (ONLY); see ``default``
# Our layout names → the engine's layout variant (layouts.json: layouts[paper][card][variant]).
VARIANTS = {"3x3": "borderless", "4x2": "default"}
ONLY = {"3x3": ("a4", "standard")}  # the one paper and card a layout is built for; others: any
NOMINAL_CARDS = {"4x2": 8, "3x3": 9}  # on A4, for messages when layouts.json can't be read


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
    reg_length_mm: float = 9.4  # the printed L marks' legs, from the corner line to the leg's end
    layout: str = DEFAULT
    reg_inset_y_mm: float | None = None  # marks' distance from the top and bottom, when not reg_inset_mm

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


def check(name: str, paper: str, card_size: str) -> None:
    """Raise unless ``name`` is a layout that ``paper`` and ``card_size`` can use."""
    if name not in VARIANTS:
        raise LayoutError(f"unknown layout {name!r} (known: {', '.join(VARIANTS)})")
    if name in ONLY and (paper, card_size) != ONLY[name]:
        want_paper, want_card = ONLY[name]
        raise LayoutError(
            f"--layout {name} is {want_paper.upper()} paper with {want_card} cards only, "
            f"not {paper} with {card_size}"
        )


def default(paper: str, card_size: str) -> str:
    """The layout a sheet gets unless told: 3x3 on A4 with standard cards, else 4x2, the engine's
    own layout for the paper."""
    return DEFAULT if ONLY[DEFAULT] == (paper, card_size) else "4x2"


def engine_flags(name: str) -> list[str]:
    """create_pdf.py's flags for this layout. A 3x3 sheet gets no page label: the engine prints
    it 1.5 mm from the bottom edge, clipped by the printer, below the bottom-left mark."""
    return ["--borderless", "--label", ""] if VARIANTS[name] == "borderless" else []


def layout_def(paper: str, card_size: str, name: str) -> dict:
    """layouts.json's entry for this paper, card size and layout."""
    layouts = engine.layouts()
    if paper not in layouts["paper_sizes"]:
        raise LayoutError(
            f"unknown paper size {paper!r} (known: {', '.join(sorted(layouts['paper_sizes']))})"
        )
    sizes = layouts["layouts"].get(paper, {})
    if card_size not in sizes:
        raise LayoutError(
            f"no {card_size!r} layout for {paper!r} (available: {', '.join(sorted(sizes)) or 'none'})"
        )
    variant = VARIANTS[name]
    if variant not in sizes[card_size]:
        raise LayoutError(f"the engine has no {name} layout for {paper!r} with {card_size!r} cards")
    return sizes[card_size][variant]


def page_size_mm(paper: str, card_size: str, name: str) -> tuple[float, float]:
    layout = layout_def(paper, card_size, name)
    paper_def = engine.layouts()["paper_sizes"][paper]
    w, h = engine.size_to_mm(paper_def["width"]), engine.size_to_mm(paper_def["height"])
    # create_pdf.py lays the page out in the layout's orientation; the DXF matches it.
    if layout.get("orientation") == "landscape" and w < h:
        w, h = h, w
    if layout.get("orientation") == "portrait" and w > h:
        w, h = h, w
    return w, h


def registration_mm(paper: str, card_size: str, name: str) -> tuple[float, float]:
    """(inset, leg length) of the printed marks in mm, resolved as create_pdf.py does: the layout's
    own registration settings, else the defaults (the variant's inset, the default's length)."""
    own = layout_def(paper, card_size, name).get("registration") or {}
    defaults = engine.layouts()["defaults"]["registration"]
    inset = own.get("inset") or defaults[VARIANTS[name]]["inset"]
    length = own.get("length") or defaults["default"]["length"]
    return engine.size_to_mm(inset), engine.size_to_mm(length)


def registration_inset_y_mm(paper: str, card_size: str, name: str) -> float | None:
    """The marks' distance from the top and bottom edges when the layout sets its own (``inset_y``,
    the engine fork's 3x3 layout), else None: the same as the sides."""
    own = layout_def(paper, card_size, name).get("registration") or {}
    return engine.size_to_mm(own["inset_y"]) if own.get("inset_y") else None


def build_cut_svg(paper: str, card_size: str, out: Path | None, name: str) -> Geometry:
    """Write the cut-path SVG (unless ``out`` is None) and return the geometry."""
    import ezdxf

    page_w, page_h = page_size_mm(paper, card_size, name)
    inset, leg = registration_mm(paper, card_size, name)
    inset_y = registration_inset_y_mm(paper, card_size, name)
    top = inset if inset_y is None else inset_y
    args = ["--card_size", card_size, "--paper_size", paper]
    if VARIANTS[name] != "default":
        # generate_dxf.py picks the orientation that fits the most cards unless told; create_pdf.py
        # takes the layout's, so it is passed (the engine's default layout's pick is its own).
        orientation = layout_def(paper, card_size, name)["orientation"]
        args += ["--variant", VARIANTS[name], "--orientation", orientation]

    with tempfile.TemporaryDirectory() as tmp:
        dxf_path = Path(tmp) / "cut.dxf"
        engine.run_engine_script("generate_dxf.py", ["single", str(dxf_path), *args])
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
    if min(xs) < inset or min(ys) < top or max(xs) > page_w - inset or max(ys) > page_h - top:
        raise LayoutError("card outlines fall outside the registration-mark frame")

    if out is not None:
        body = "\n".join(
            f'  <path id="card{i + 1}" d="{d}" fill="none" stroke="#ff0000" stroke-width="0.1"/>'
            for i, d in enumerate(paths)
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{fmt(page_w)}mm" height="{fmt(page_h)}mm" viewBox="0 0 {fmt(page_w)} {fmt(page_h)}">\n'
            f"  <!-- {paper}/{card_size} {name}: {len(paths)} cards, "
            "generated by mtg-proxy from silhouette-card-maker -->\n"
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
        reg_y_mm=round(page_h - 2 * top, 3),
        cut_bbox_mm=(round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)),
        reg_length_mm=round(leg, 3),
        layout=name,
        reg_inset_y_mm=None if inset_y is None else round(inset_y, 3),
    )
