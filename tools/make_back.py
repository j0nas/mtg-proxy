#!/usr/bin/env python3
"""Generate a generic proxy card back (63x88mm @ 800 PPI) at assets/back.png.

Deliberately NOT the official MTG back: proxies must be identifiable as
proxies, and a custom back avoids reprinting copyrighted art. Replace
assets/back.png with your own image any time; the workflow just copies it.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PPI = 800
W, H = round(63 / 25.4 * PPI), round(88 / 25.4 * PPI)  # 63x88mm

BG = (26, 22, 38)          # deep indigo
PANEL = (36, 31, 52)
LINE = (168, 142, 84)      # antique gold
LINE_DIM = (96, 82, 56)
TEXT = (196, 172, 116)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

mm = PPI / 25.4


def rounded(inset_mm: float, width_px: int, color, fill=None):
    i = round(inset_mm * mm)
    d.rounded_rectangle(
        [i, i, W - 1 - i, H - 1 - i],
        radius=round((4.0 - inset_mm * 0.35) * mm),
        outline=color,
        width=width_px,
        fill=fill,
    )


# frame: outer hairline, inner panel, double rules
rounded(3.0, max(2, round(0.25 * mm)), LINE_DIM)
rounded(4.2, max(2, round(0.45 * mm)), LINE, fill=PANEL)
rounded(5.6, max(1, round(0.15 * mm)), LINE_DIM)

# central lozenge motif
cx, cy = W // 2, H // 2
r_out = round(16 * mm)
r_in = round(11 * mm)
d.polygon(
    [(cx, cy - r_out), (cx + r_out, cy), (cx, cy + r_out), (cx - r_out, cy)],
    outline=LINE, width=max(2, round(0.4 * mm)),
)
d.polygon(
    [(cx, cy - r_in), (cx + r_in, cy), (cx, cy + r_in), (cx - r_in, cy)],
    outline=LINE_DIM, width=max(1, round(0.2 * mm)),
)
d.ellipse(
    [cx - round(4 * mm), cy - round(4 * mm), cx + round(4 * mm), cy + round(4 * mm)],
    outline=LINE, width=max(2, round(0.35 * mm)),
)

font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", round(6.5 * mm))
font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", round(2.6 * mm))

d.text((cx, cy - r_out - round(7 * mm)), "PROXY", font=font_big, fill=TEXT, anchor="mm")
d.text((cx, cy + r_out + round(7 * mm)), "PLAYTEST COPY", font=font_small, fill=TEXT, anchor="mm")
d.text((cx, H - round(8.5 * mm)), "NOT FOR SALE", font=font_small, fill=LINE_DIM, anchor="mm")

out = Path(__file__).resolve().parent.parent / "assets" / "back.png"
out.parent.mkdir(parents=True, exist_ok=True)
img.save(out, dpi=(PPI, PPI))
print(f"wrote {out} ({W}x{H}px @ {PPI} PPI)")
