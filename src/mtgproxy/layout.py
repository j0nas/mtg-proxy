"""Cut geometry for the card sheet, straight from the engine's own layout code.

``generate_dxf.py single`` (the same layout engine that placed the cards in the
PDF) produces a DXF in page coordinates with the origin top-left and Y going
NEGATIVE downwards; this converts it into a page-sized SVG in millimetres,
Y-down positive from the same origin, which cut-proxies sends to the cutter
directly. Rounded corners come through as true arcs.

Registration marks are NOT drawn: the cutter finds the printed ones itself. The
mark geometry it needs (inset from the page edge, mark-to-mark distances, leg
length) is derived from the same assets/layouts.json entries create_pdf.py uses.

Every sheet is the 3x3: nine standard cards on portrait A4, printed with 3-mark registration
3.5 mm from the sides and 9 mm from the top and bottom, and with proof targets, small black squares
a proof cut traces (``cameo.proof``), also from layouts.json. It is the engine's ``borderless``
variant, its name for the tighter inset, which has nothing to do with borderless printing (that
stays off).
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import engine

PAPER, CARD_SIZE = "a4", "standard"
VARIANT = "borderless"  # the engine's name for it (layouts.json: layouts[paper][card][variant])
NOMINAL_CARDS = 9  # for messages when layouts.json can't be read
# create_pdf.py's flags for it. No page label: the engine prints it 1.5 mm from the bottom edge,
# clipped by the printer, below the bottom-left mark.
ENGINE_FLAGS = ["--card_size", CARD_SIZE, "--paper_size", PAPER, "--borderless", "--label", ""]


@dataclass
class Geometry:
    cards: int
    page_w_mm: float
    page_h_mm: float
    reg_inset_mm: float
    reg_x_mm: float
    reg_y_mm: float
    cut_bbox_mm: tuple[float, float, float, float]
    reg_length_mm: float = 9.4  # the printed L marks' legs, from the corner line to the leg's end
    reg_inset_y_mm: float | None = None  # marks' distance from the top and bottom, when not reg_inset_mm
    # The proof targets the engine prints with the marks: centres, page mm (y down), and their side.
    proof_targets: tuple[tuple[float, float], ...] = ()
    proof_target_mm: float = 0.0

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


def layout_def() -> dict:
    """layouts.json's entry for the 3x3 sheet."""
    try:
        return engine.layouts()["layouts"][PAPER][CARD_SIZE][VARIANT]
    except KeyError:
        raise LayoutError(f"the engine has no {VARIANT} layout for {PAPER} with {CARD_SIZE} cards") from None


def page_size_mm() -> tuple[float, float]:
    paper_def = engine.layouts()["paper_sizes"][PAPER]
    w, h = engine.size_to_mm(paper_def["width"]), engine.size_to_mm(paper_def["height"])
    # create_pdf.py lays the page out in the layout's orientation; the DXF matches it.
    if (layout_def().get("orientation") == "landscape") == (w < h):
        w, h = h, w
    return w, h


def registration_mm() -> tuple[float, float]:
    """(inset, leg length) of the printed marks in mm, resolved as create_pdf.py does: the layout's
    own registration settings, else the defaults (the variant's inset, the default's length)."""
    own = layout_def().get("registration") or {}
    defaults = engine.layouts()["defaults"]["registration"]
    inset = own.get("inset") or defaults[VARIANT]["inset"]
    length = own.get("length") or defaults["default"]["length"]
    return engine.size_to_mm(inset), engine.size_to_mm(length)


def printed_marks() -> dict:
    """The marks the sheet prints, for run.json: inset and leg length, and the inset from the top and
    bottom where the layout sets its own (None: the same)."""
    inset, length = registration_mm()
    inset_y = registration_inset_y_mm()
    return {
        "inset": round(inset, 3),
        "inset_y": None if inset_y is None else round(inset_y, 3),
        "length": round(length, 3),
    }


def registration_inset_y_mm() -> float | None:
    """The marks' distance from the top and bottom edges when the layout sets its own (``inset_y``,
    the engine fork's 3x3 layout), else None: the same as the sides."""
    own = layout_def().get("registration") or {}
    return engine.size_to_mm(own["inset_y"]) if own.get("inset_y") else None


def proof_targets_mm() -> tuple[tuple[tuple[float, float], ...], float]:
    """The proof targets the engine prints with the marks (layouts.json ``proof_targets``): each one's
    centre in page mm from the top-left corner, y down, in the order the proof rounds take them, and
    their side. None and 0 when the engine prints none."""
    own = layout_def().get("proof_targets") or {}
    at = tuple((engine.size_to_mm(x), engine.size_to_mm(y)) for x, y in own.get("at", ()))
    return at, engine.size_to_mm(own["size"]) if at else 0.0


def build_cut_svg(out: Path | None) -> Geometry:
    """Write the cut-path SVG (unless ``out`` is None) and return the geometry."""
    import ezdxf

    page_w, page_h = page_size_mm()
    inset, leg = registration_mm()
    inset_y = registration_inset_y_mm()
    top = inset if inset_y is None else inset_y
    # generate_dxf.py picks the orientation that fits the most cards unless told; create_pdf.py
    # takes the layout's, so it is passed.
    args = ["--card_size", CARD_SIZE, "--paper_size", PAPER, "--variant", VARIANT]
    args += ["--orientation", layout_def()["orientation"]]

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
    targets, side = proof_targets_mm()
    for x, y in targets:
        if not (
            inset + side / 2 < x < page_w - inset - side / 2 and top + side / 2 < y < page_h - top - side / 2
        ):
            raise LayoutError(
                f"the proof target at {x:g}, {y:g} mm falls outside the registration-mark frame"
            )

    if out is not None:
        body = "\n".join(
            f'  <path id="card{i + 1}" d="{d}" fill="none" stroke="#ff0000" stroke-width="0.1"/>'
            for i, d in enumerate(paths)
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{fmt(page_w)}mm" height="{fmt(page_h)}mm" viewBox="0 0 {fmt(page_w)} {fmt(page_h)}">\n'
            f"  <!-- {PAPER}/{CARD_SIZE} 3x3: {len(paths)} cards, "
            "generated by mtg-proxy from silhouette-card-maker -->\n"
            f"{body}\n</svg>\n"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg)

    return Geometry(
        cards=len(paths),
        page_w_mm=round(page_w, 3),
        page_h_mm=round(page_h, 3),
        reg_inset_mm=round(inset, 3),
        reg_x_mm=round(page_w - 2 * inset, 3),
        reg_y_mm=round(page_h - 2 * top, 3),
        cut_bbox_mm=(round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)),
        reg_length_mm=round(leg, 3),
        reg_inset_y_mm=None if inset_y is None else round(inset_y, 3),
        proof_targets=tuple((round(x, 3), round(y, 3)) for x, y in targets),
        proof_target_mm=round(side, 3),
    )
