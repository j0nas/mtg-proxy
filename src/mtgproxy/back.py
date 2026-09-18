"""Generic proxy card back (63x88mm @ 800 PPI).

Deliberately NOT the official MTG back: proxies must be identifiable as
proxies, and a custom back avoids reprinting copyrighted art. Replace
assets/back.png with your own image any time; the build just copies it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .testcards import load_font

PPI = 800
MM = PPI / 25.4
W, H = round(63 * MM), round(88 * MM)

BG = (26, 22, 38)  # deep indigo
PANEL = (36, 31, 52)
LINE = (168, 142, 84)  # antique gold
LINE_DIM = (96, 82, 56)
TEXT = (196, 172, 116)

SERIF_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
]
SERIF = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
]


def make_back() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    def rounded(inset_mm: float, width_px: int, color, fill=None):
        i = round(inset_mm * MM)
        d.rounded_rectangle(
            [i, i, W - 1 - i, H - 1 - i],
            radius=round((4.0 - inset_mm * 0.35) * MM),
            outline=color,
            width=width_px,
            fill=fill,
        )

    rounded(3.0, max(2, round(0.25 * MM)), LINE_DIM)
    rounded(4.2, max(2, round(0.45 * MM)), LINE, fill=PANEL)
    rounded(5.6, max(1, round(0.15 * MM)), LINE_DIM)

    cx, cy = W // 2, H // 2
    r_out, r_in = round(16 * MM), round(11 * MM)
    d.polygon(
        [(cx, cy - r_out), (cx + r_out, cy), (cx, cy + r_out), (cx - r_out, cy)],
        outline=LINE,
        width=max(2, round(0.4 * MM)),
    )
    d.polygon(
        [(cx, cy - r_in), (cx + r_in, cy), (cx, cy + r_in), (cx - r_in, cy)],
        outline=LINE_DIM,
        width=max(1, round(0.2 * MM)),
    )
    d.ellipse(
        [cx - round(4 * MM), cy - round(4 * MM), cx + round(4 * MM), cy + round(4 * MM)],
        outline=LINE,
        width=max(2, round(0.35 * MM)),
    )
    font_big = load_font(SERIF_BOLD, round(6.5 * MM))
    font_small = load_font(SERIF, round(2.6 * MM))
    d.text((cx, cy - r_out - round(7 * MM)), "PROXY", font=font_big, fill=TEXT, anchor="mm")
    d.text((cx, cy + r_out + round(7 * MM)), "PLAYTEST COPY", font=font_small, fill=TEXT, anchor="mm")
    d.text((cx, H - round(8.5 * MM)), "NOT FOR SALE", font=font_small, fill=LINE_DIM, anchor="mm")
    return img


def write_back(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    make_back().save(out, dpi=(PPI, PPI))
    return out
