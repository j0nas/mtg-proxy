"""GP-GL for the Cameo 5 Alpha: the command strings we send and the replies we parse. No I/O.

Units: 1/20 mm ("SU"). Coordinates go y first (along the mat's feed), then x (across it), both
from the current origin; after a registration scan the origin is the top-left mark's corner.
Commands are ETX-terminated; the status query and the init handshake are bare escape pairs.

The job follows Silhouette Studio's own conversation with this machine, recorded over Bluetooth
with PacketLogger on 2026-09-28 (docs/studio-capture.md): prepare, describe the marks, scan,
then set up the blade inside the registered frame, cut, and return to the origin. Commands
Studio sends without a known meaning (FM0, TR, APS, TB0) are kept verbatim. Studio sends its
paths in a binary encoding (BE1/BE2); we send plain M/D moves, which this machine cuts fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ETX = "\x03"
INIT = "\x1b\x04"  # ESC EOT: reset the command parser
STATUS = "\x1b\x05"  # ESC ENQ → "0" ready, "1" moving, "2" unloaded (no mat/media), "3" paused
VERSION = "FG"
# "3" is the machine's pause button (2026-10-26: pressed mid-cut, the cut waited for it).
STATUS_TEXT = {"0": "ready", "1": "moving", "2": "unloaded", "3": "paused"}


def su(mm: float) -> int:
    return round(mm * 20)


def cmd(*parts: str) -> bytes:
    """ETX-terminate each command (bare escape sequences go through ``raw``)."""
    return "".join(p + ETX for p in parts).encode("ascii")


def raw(s: str) -> bytes:
    return s.encode("ascii")


def status_of(reply: bytes | None) -> str:
    """'ready' / 'moving' / 'unloaded' / 'paused', or the raw reply text when it is something else."""
    if reply is None:
        return "no reply"
    text = reply.decode("latin1").rstrip(ETX).strip()
    return STATUS_TEXT.get(text, repr(text))


@dataclass(frozen=True)
class Blade:
    force: int = 20  # 1..40 on the Alpha
    speed: int = 25  # 1..30
    depth: int = 5  # AutoBlade depth 1..10

    def __post_init__(self):
        if not (1 <= self.force <= 40 and 1 <= self.speed <= 30 and 0 <= self.depth <= 10):
            raise ValueError(f"blade out of range: {self}")


# Before anything else: 12x12" mat, portrait orientation.
PREPARE = ("TG1", "FN0", "TB50,0", "FM0", "TR0,1")


def mark_setup(length_mm: float, thickness_mm: float) -> tuple[str, ...]:
    """Describe the printed marks before a scan."""
    return (
        "TB99",
        "TB52,2",  # mark type: Cameo
        f"TB51,{su(length_mm)}",  # leg length
        f"TB53,{su(thickness_mm)}",  # line thickness
        "TB55,1",
        "APS30",
    )


def scan(height_mm: float, width_mm: float, top_mm: float, left_mm: float, marks: int = 3) -> tuple[str, str]:
    """TB123, the 3-mark scan (the square top-left, L marks top-right and bottom-left): the
    mark-to-mark height and width, then where the sensor starts searching, from the origin. Studio
    follows it with TB99. ``marks=2`` sends TB122, which reads the square and the bottom-left L
    only (firmware V1.04: the number only sets the mark count)."""
    if marks not in (2, 3):
        raise ValueError(f"TB12x scans 2 or 3 marks, not {marks}")
    return f"TB12{marks},{su(height_mm)},{su(width_mm)},{su(top_mm)},{su(left_mm)}", "TB99"


SCAN_REPLY = re.compile(r"^(?=.{5}\x03$) *(-?\d+)\x03$")


def scan_result(reply: bytes | None) -> str | None:
    """'found' ("    0") or 'not found' ("    1"; TB123 answers "   -1" when its area search
    finds no square, 2026-10-24); None for anything else. Scan results are padded to five
    characters; a status reply ("1" = moving) is not a scan result."""
    if reply is None:
        return None
    m = SCAN_REPLY.match(reply.decode("latin1"))
    if m is None:
        return None
    code = int(m.group(1))
    return "found" if code == 0 else "not found" if code == 1 else f"not found (reply {code})"


def blade_setup(
    blade: Blade, area_y_mm: float, area_x_mm: float, low_y_mm: float = 0.0, low_x_mm: float = 0.0
) -> tuple[str, ...]:
    """After the scan: the cutting area from the top-left mark, then the AutoBlade in slot 1.

    Studio sets the area to the mark frame, from the top-left mark (``\\0,0``); we pass the
    paper's extent instead, so a design may run past the right and bottom marks (the deck box
    does) while the machine still refuses a runaway coordinate. A design reaching above or left
    of the top-left mark moves the near corner (``low_*``, negative) out to cover it."""
    f, s, d = blade.force, blade.speed, blade.depth
    return (
        *cutting_area(area_y_mm, area_x_mm, low_y_mm, low_x_mm),
        "J1",  # tool slot 1
        f"FX{f},1",  # force
        "TJ0",
        f"!{s},1",  # speed
        "APS0",
        "FC0,1,1",
        "FE0,1",
        "FF1,0,1",
        "FF1,1,1",
        f"FX{f},1",
        "TJ3",  # AutoBlade
        f"!{s},1",
        "APS30",
        "FC18,1,1",  # blade offset 0.9 mm
        f"TF{d},1",  # AutoBlade depth
    )


def cutting_area(
    area_y_mm: float, area_x_mm: float, low_y_mm: float = 0.0, low_x_mm: float = 0.0
) -> tuple[str, str]:
    """The near corner (``\\``) and the far corner (``Z``) of the area moves and cuts may reach."""
    return f"\\{su(low_y_mm)},{su(low_x_mm)}", f"Z{su(area_y_mm)},{su(area_x_mm)}"


def move(y_mm: float, x_mm: float) -> str:
    return f"M{su(y_mm)},{su(x_mm)}"


def draw(y_mm: float, x_mm: float) -> str:
    return f"D{su(y_mm)},{su(x_mm)}"


def draw_path(points_su: list[tuple[int, int]]) -> str:
    """One GP-GL draw through several (y, x) points in SU: D y1,x1,y2,x2,… The machine gets the
    whole run of a path at once instead of one command per point."""
    return "D" + ",".join(f"{y},{x}" for y, x in points_su)


# The sensor light's stored levels (firmware V1.04, cameo-re NOTES.md). A scan restores its light
# from the selected slot, 0 at power-on (TB61 selects another; we never send it). TB63,n ramps the
# light up from zero with the sensor over the head's position until the reading passes half scale,
# stores the step that took in slot n (1-4 only) and reports it: dark ink needs more light than
# white paper, so it is a reflectance reading of the spot under the head (0-255).
LIGHT_SLOT = "TB60"  # → the selected slot


def select_light(slot: int) -> str:
    """TB61: select a slot and set the light to its stored level (RAM only, nothing saved). TB63
    leaves the light where its ramp stopped, 255 after a reading over ink, and TB123 starts its
    search with the light as it finds it (it restores the stored level only to retry), so a
    reading before a scan has to be followed by this (firmware V1.04, 2026-10-26)."""
    return f"TB61,{slot}"


def light_level(slot: int) -> str:
    """TB62: slot ``slot``'s stored level, read only."""
    return f"TB62,{slot}"


def sense(slot: int) -> str:
    """TB63: calibrate over the head's position into ``slot`` (1-4) and report the level."""
    if not 1 <= slot <= 4:
        raise ValueError(f"TB63 stores into slots 1-4, not {slot}")
    return f"TB63,{slot}"


NUMBER_REPLY = re.compile(r"^\s*(-?\d+)\s*$")


def number(reply: bytes | None) -> int | None:
    """A reply that is one integer (padded or not, ETX-terminated), else None."""
    if reply is None:
        return None
    m = NUMBER_REPLY.match(reply.decode("latin1").rstrip(ETX))
    return int(m.group(1)) if m else None


# Studio's ending: back to the origin. Never the "feed" ending (M… SO0), which moves the ORIGIN
# below the job so that every later scan on that mat starts from the wrong place.
RETURN_TO_ORIGIN = ("TB0", "L0", "\\0,0", "M0,0", "TR0,0", "J0", "FN0", "TB50,0")
