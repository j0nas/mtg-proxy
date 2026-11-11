"""The registration sensor read through the firmware's debug set, for the readings before the scan.

TB63 (``Session.sense``) is a light calibration used as a reading: 1.7-4.1 s each, and 0-255 with
ink HIGH. The debug command ``a2`` reads the sensor once in 60 ms (0.19 s with a move) and the
other way round: more reflected light reads higher, paper 545-615 and black ink 65-160 at the light
level used here, the ADC saturating near 616 (2026-11-11, laminated glossy sheet; cameo-re NOTES.md,
"The debug set opens from the host" and its machine subsection).

Three things a reading needs that TB63 did for itself:

* The light. TB63 switches it off when it ends, so ``on`` goes before the readings (``a0``, ``a1``)
  and ``off`` after them; the scan turns it on itself but starts from the level it finds, so the
  caller still restores ``TB61`` before it (``driver.cut``).
* The sensor sits SENSOR_RIGHT mm right of the blade, and ``a2`` reads wherever it is: the head goes
  that far left of the point to read.
* A move that gets there. ``M`` is held inside the cutting area, near the mat's left zero, which
  the head must pass to read the square with the sensor 19 mm to its right. ``g9`` isn't, but lands
  short, so it is sent at ``G9_ALONG``/``G9_ACROSS`` times the distance (offsets under 0.06 mm,
  measured after a power cycle).

The set stays open until power-off and every lowercase byte in it is a command, two of which never
return, so everything lowercase goes through ``protocol.debug`` and nothing else ever does.
"""

from __future__ import annotations

import time

from . import protocol as p
from .session import CutError, Session

LEVEL = 120  # the light's level: paper reads 545-615, below the ADC's 616
# mm the sensor sits right of the blade, in the calibrated terms of ``M`` (the head goes to x - SENSOR_RIGHT,
# through G9's correction). The test sheet's gauges cut true with it on 2026-11-11 (every card's margin
# within a fraction of a millimetre of 1.0 mm), so not 19.2 / G9_ACROSS, which is 0.22 mm less.
SENSOR_RIGHT = 19.2
G9_ALONG, G9_ACROSS = 1.0068, 1.0118
# The furthest left of the mat's zero the head may go (TB63 itself went to -17.0).
HEAD_LEFT_LIMIT = -17.5
SETTLE_S = 0.05  # the light's rise before a first reading


class DebugSensor:
    """The sensor. ``open`` unlocks the debug set and leaves the light on, or returns None (and says
    so) where the set isn't verified or doesn't answer, and the caller keeps TB63; ``off`` ends it."""

    def __init__(self, s: Session):
        self.s = s
        self.reads = 0

    @classmethod
    def open(cls, s: Session, firmware: str) -> DebugSensor | None:
        if p.DEBUG_FIRMWARE not in firmware:
            s.log.say(
                f"WARNING: the sensor's fast reading is only verified on firmware {p.DEBUG_FIRMWARE}, this is "
                f"{firmware!r}: using the slow one (TB63), the checks take minutes longer"
            )
            return None
        sensor = cls(s)
        s.send(p.UNLOCK)
        sensor.on()
        level = p.number(s.query(p.debug(p.SENSOR_READ), timeout=3))
        if level is None:
            sensor.off()
            s.log.say(
                "WARNING: the Cameo's fast sensor reading (a2) didn't answer with a number: using the slow "
                "one (TB63), the checks take minutes longer"
            )
            return None
        s.log("a2_open", level=level, firmware=firmware)
        return sensor

    def on(self) -> None:
        """The light on, at LEVEL (after any TB63 or scan, which switch it off or set it)."""
        self.s.send_debug(p.SENSOR_ON, p.sensor_level(LEVEL))
        time.sleep(SETTLE_S)

    def off(self) -> None:
        self.s.send_debug(p.SENSOR_OFF)

    def reaches(self, x_mm: float) -> bool:
        """The sensor can be put over ``x_mm`` in on the mat: the head stays within HEAD_LEFT_LIMIT."""
        return x_mm - SENSOR_RIGHT >= HEAD_LEFT_LIMIT

    def sense(self, y_mm: float, x_mm: float) -> tuple[int | None, str | None]:
        """Read the spot at (y, x) mm in the mat's frame, as ``Session.sense`` does: the level, and the
        reply as it came. The head goes SENSOR_RIGHT mm left of ``x`` to put the sensor there."""
        head = x_mm - SENSOR_RIGHT
        if head < HEAD_LEFT_LIMIT:
            raise CutError(
                f"the sensor can't reach {x_mm:g} mm in on the mat: its head would go {-head:.1f} mm left of "
                f"the mat's zero, past the {-HEAD_LEFT_LIMIT:g} mm it may. Put the paper further right"
            )
        self.s.send_debug(p.head_to(y_mm * G9_ALONG, head * G9_ACROSS))
        self.s.wait_ready(30, poll=0.05)
        reply = self.s.query(p.debug(p.SENSOR_READ), timeout=5)
        self.reads += 1
        return p.number(reply), None if reply is None else reply.decode("latin1").rstrip(p.ETX)
