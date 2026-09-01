#!/usr/bin/env python3
"""Generate ink-light placeholder test cards at exact MTG dimensions (63x88mm).

Writes N front images + 1 back image into the given directories (defaults:
game/front and game/back, i.e. run from the silhouette-card-maker root).

The placeholders are cut-accuracy gauges, not art:
- rounded hairline frame at exactly 1.0 mm inside the cut line; after a perfect
  cut the white margin around the frame is uniform 1.0 mm on all sides
- gray tick lines at 0.5 mm and 1.5 mm insets on each side midpoint to read
  cut offset in 0.5 mm steps
- gray quarter-arcs marking the theoretical 3 mm corner cut radius
- center crosshair on front AND back: hold a cut card against the light to
  check front/back duplex alignment
- "TOP" marker to spot rotation/orientation mistakes
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PPI = 800
MM = PPI / 25.4
W, H = round(63 * MM), round(88 * MM)  # exactly 63 x 88 mm

BLACK = (40, 40, 40)
GRAY = (150, 150, 150)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_LIGHT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def px(mm: float) -> int:
    return round(mm * MM)


def draw_gauges(d: ImageDraw.ImageDraw) -> None:
    # main frame: 1.0 mm inside the cut line
    d.rounded_rectangle(
        [px(1), px(1), W - 1 - px(1), H - 1 - px(1)],
        radius=px(2.5), outline=BLACK, width=px(0.25),
    )
    # offset ticks at 0.5 and 1.5 mm insets, centered on each side
    tick_len = px(6)
    for inset in (0.5, 1.5):
        i = px(inset)
        cx, cy = W // 2, H // 2
        d.line([cx - tick_len // 2, i, cx + tick_len // 2, i], fill=GRAY, width=px(0.15))          # top
        d.line([cx - tick_len // 2, H - 1 - i, cx + tick_len // 2, H - 1 - i], fill=GRAY, width=px(0.15))  # bottom
        d.line([i, cy - tick_len // 2, i, cy + tick_len // 2], fill=GRAY, width=px(0.15))          # left
        d.line([W - 1 - i, cy - tick_len // 2, W - 1 - i, cy + tick_len // 2], fill=GRAY, width=px(0.15))  # right
    # theoretical 3 mm corner cut radius (the cut path itself, at the corners)
    r = px(3)
    wdt = px(0.15)
    d.arc([0, 0, 2 * r, 2 * r], 180, 270, fill=GRAY, width=wdt)
    d.arc([W - 1 - 2 * r, 0, W - 1, 2 * r], 270, 360, fill=GRAY, width=wdt)
    d.arc([W - 1 - 2 * r, H - 1 - 2 * r, W - 1, H - 1], 0, 90, fill=GRAY, width=wdt)
    d.arc([0, H - 1 - 2 * r, 2 * r, H - 1], 90, 180, fill=GRAY, width=wdt)
    # center crosshair (duplex alignment check)
    cx, cy = W // 2, H // 2
    arm = px(5)
    d.line([cx - arm, cy, cx + arm, cy], fill=BLACK, width=px(0.15))
    d.line([cx, cy - arm, cx, cy + arm], fill=BLACK, width=px(0.15))
    d.ellipse([cx - px(2), cy - px(2), cx + px(2), cy + px(2)], outline=GRAY, width=px(0.15))


def make_card(title: str, subtitle: str) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    draw_gauges(d)
    f_big = ImageFont.truetype(FONT, px(5))
    f_small = ImageFont.truetype(FONT_LIGHT, px(2.8))
    cx = W // 2
    d.text((cx, px(10)), "▲ TOP", font=f_small, fill=GRAY, anchor="mm")
    d.text((cx, px(20)), title, font=f_big, fill=BLACK, anchor="mm")
    d.text((cx, px(27)), subtitle, font=f_small, fill=BLACK, anchor="mm")
    d.text((cx, H - px(14)), "63 × 88 mm", font=f_small, fill=BLACK, anchor="mm")
    d.text((cx, H - px(9)), "frame = 1.0 mm from cut · ticks 0.5 mm", font=f_small, fill=GRAY, anchor="mm")
    return img


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=8, help="number of front cards")
    p.add_argument("--front_dir", default="game/front")
    p.add_argument("--back_dir", default="game/back")
    p.add_argument("--double_sided_dir", default="game/double_sided")
    p.add_argument(
        "--per_card_backs", action="store_true",
        help="give each card its own numbered back (via the double_sided dir) "
             "instead of one shared back; only useful for double-sided builds",
    )
    args = p.parse_args()

    front_dir = Path(args.front_dir)
    back_dir = Path(args.back_dir)
    front_dir.mkdir(parents=True, exist_ok=True)
    back_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, args.count + 1):
        make_card(f"TEST CARD {i}", f"front · {i} of {args.count}").save(
            front_dir / f"test_{i:02d}.png", dpi=(PPI, PPI)
        )
    make_card("TEST BACK", "duplex alignment check").save(
        back_dir / "test_back.png", dpi=(PPI, PPI)
    )
    wrote_ds = ""
    if args.per_card_backs:
        ds_dir = Path(args.double_sided_dir)
        ds_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, args.count + 1):
            make_card(f"BACK {i}", f"back of TEST CARD {i}").save(
                ds_dir / f"test_{i:02d}.png", dpi=(PPI, PPI)
            )
        wrote_ds = f", {args.count} numbered backs -> {ds_dir}"
    print(f"wrote {args.count} fronts -> {front_dir}, 1 back -> {back_dir}{wrote_ds} ({W}x{H}px @ {PPI} PPI = 63x88mm)")


if __name__ == "__main__":
    main()
