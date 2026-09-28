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
STATUS = "\x1b\x05"  # ESC ENQ → "0" ready, "1" moving, "2" unloaded (no mat/media)
VERSION = "FG"
STATUS_TEXT = {"0": "ready", "1": "moving", "2": "unloaded"}


def su(mm: float) -> int:
    return round(mm * 20)


def cmd(*parts: str) -> bytes:
    """ETX-terminate each command (bare escape sequences go through ``raw``)."""
    return "".join(p + ETX for p in parts).encode("ascii")


def raw(s: str) -> bytes:
    return s.encode("ascii")


def status_of(reply: bytes | None) -> str:
    """'ready' / 'moving' / 'unloaded', or the raw reply text when it is something else."""
    if reply is None:
        return "no reply"
    text = reply.decode("latin1").rstrip(ETX).strip()
    return STATUS_TEXT.get(text, repr(text))


@dataclass(frozen=True)
class Blade:
    force: int = 20  # 1..40 on the Alpha
    speed: int = 25  # 1..30
    depth: int = 4  # AutoBlade depth 1..10

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


def scan(kind: int, height_mm: float, width_mm: float, top_mm: float, left_mm: float) -> tuple[str, str]:
    """TB124 (four L-marks) or TB123 (square + two L's): the mark-to-mark height and width, then
    where the sensor starts searching, from the origin. Studio follows it with TB99."""
    return f"TB{kind},{su(height_mm)},{su(width_mm)},{su(top_mm)},{su(left_mm)}", "TB99"


SCAN_REPLY = re.compile(r"^ {4}([01])\x03$")


def scan_result(reply: bytes | None) -> str | None:
    """'found' ("    0") or 'not found' ("    1"); None for anything else. Scan results are
    padded to five characters; a status reply ("1" = moving) is not a scan result."""
    if reply is None:
        return None
    m = SCAN_REPLY.match(reply.decode("latin1"))
    return None if m is None else ("found" if m.group(1) == "0" else "not found")


def blade_setup(blade: Blade, area_y_mm: float, area_x_mm: float) -> tuple[str, ...]:
    """After the scan: the cutting area from the top-left mark, then the AutoBlade in slot 1.

    Studio sets the area to the mark frame; we pass the paper's extent instead, so a design may
    run past the right and bottom marks (the deck box does) while the machine still refuses a
    runaway coordinate."""
    f, s, d = blade.force, blade.speed, blade.depth
    return (
        "\\0,0",
        f"Z{su(area_y_mm)},{su(area_x_mm)}",
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


def move(y_mm: float, x_mm: float) -> str:
    return f"M{su(y_mm)},{su(x_mm)}"


def draw(y_mm: float, x_mm: float) -> str:
    return f"D{su(y_mm)},{su(x_mm)}"


# Studio's ending: back to the origin. Never the "feed" ending (M… SO0), which moves the ORIGIN
# below the job so that every later scan on that mat starts from the wrong place.
RETURN_TO_ORIGIN = ("TB0", "L0", "\\0,0", "M0,0", "TR0,0", "J0", "FN0", "TB50,0")
