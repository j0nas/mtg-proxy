"""Ink-light placeholder test cards at exact MTG dimensions (63x88mm).

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

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PPI = 800
MM = PPI / 25.4
W, H = round(63 * MM), round(88 * MM)  # exactly 63 x 88 mm

BLACK = (40, 40, 40)
GRAY = (150, 150, 150)

# First font that exists wins: Debian/Ubuntu (WSL), then macOS system fonts.
FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
FONT_LIGHT = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
]


def load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def px(mm: float) -> int:
    return round(mm * MM)


def draw_gauges(d: ImageDraw.ImageDraw) -> None:
    d.rounded_rectangle(
        [px(1), px(1), W - 1 - px(1), H - 1 - px(1)], radius=px(2.5), outline=BLACK, width=px(0.25)
    )
    tick_len = px(6)
    cx, cy = W // 2, H // 2
    for inset in (0.5, 1.5):
        i = px(inset)
        d.line([cx - tick_len // 2, i, cx + tick_len // 2, i], fill=GRAY, width=px(0.15))
        d.line([cx - tick_len // 2, H - 1 - i, cx + tick_len // 2, H - 1 - i], fill=GRAY, width=px(0.15))
        d.line([i, cy - tick_len // 2, i, cy + tick_len // 2], fill=GRAY, width=px(0.15))
        d.line([W - 1 - i, cy - tick_len // 2, W - 1 - i, cy + tick_len // 2], fill=GRAY, width=px(0.15))
    r, wdt = px(3), px(0.15)
    d.arc([0, 0, 2 * r, 2 * r], 180, 270, fill=GRAY, width=wdt)
    d.arc([W - 1 - 2 * r, 0, W - 1, 2 * r], 270, 360, fill=GRAY, width=wdt)
    d.arc([W - 1 - 2 * r, H - 1 - 2 * r, W - 1, H - 1], 0, 90, fill=GRAY, width=wdt)
    d.arc([0, H - 1 - 2 * r, 2 * r, H - 1], 90, 180, fill=GRAY, width=wdt)
    arm = px(5)
    d.line([cx - arm, cy, cx + arm, cy], fill=BLACK, width=px(0.15))
    d.line([cx, cy - arm, cx, cy + arm], fill=BLACK, width=px(0.15))
    d.ellipse([cx - px(2), cy - px(2), cx + px(2), cy + px(2)], outline=GRAY, width=px(0.15))


def make_card(title: str, subtitle: str) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    draw_gauges(d)
    f_big = load_font(FONT_BOLD, px(5))
    f_small = load_font(FONT_LIGHT, px(2.8))
    cx = W // 2
    d.text((cx, px(10)), "▲ TOP", font=f_small, fill=GRAY, anchor="mm")
    d.text((cx, px(20)), title, font=f_big, fill=BLACK, anchor="mm")
    d.text((cx, px(27)), subtitle, font=f_small, fill=BLACK, anchor="mm")
    d.text((cx, H - px(14)), "63 × 88 mm", font=f_small, fill=BLACK, anchor="mm")
    d.text((cx, H - px(9)), "frame = 1.0 mm from cut · ticks 0.5 mm", font=f_small, fill=GRAY, anchor="mm")
    return img


def write_test_cards(
    count: int, front_dir: Path, back_dir: Path, double_sided_dir: Path | None = None
) -> None:
    """N gauge fronts + 1 shared back; with ``double_sided_dir`` also N numbered backs."""
    front_dir.mkdir(parents=True, exist_ok=True)
    back_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        make_card(f"TEST CARD {i}", f"front · {i} of {count}").save(
            front_dir / f"test_{i:02d}.png", dpi=(PPI, PPI)
        )
    make_card("TEST BACK", "duplex alignment check").save(back_dir / "test_back.png", dpi=(PPI, PPI))
    if double_sided_dir is not None:
        double_sided_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, count + 1):
            make_card(f"BACK {i}", f"back of TEST CARD {i}").save(
                double_sided_dir / f"test_{i:02d}.png", dpi=(PPI, PPI)
            )
