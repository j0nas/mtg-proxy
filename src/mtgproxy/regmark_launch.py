"""Run inkscape-silhouette's sendto_silhouette.py with the registration behaviour we actually need.

Three adjustments, each a narrow wrap around the UNMODIFIED vendored driver:

- Mark geometry (--length / --thickness, mm). Before a scan the driver describes the marks with
  two hard-coded commands, TB51,400 (length) and TB53,10 (thickness), in the Cameo's 1/20 mm
  units: 20 mm × 0.5 mm. Silhouette Studio instead announces the marks' real size (its Print &
  Cut panel: 9.40 × 0.99 mm for the card-maker's A4 sheets). Those two commands are rewritten.

- Cut area (--beyond, mm). With registration on, Graphtec.plot clips every point to the rectangle
  between the marks, clamping outside points onto its edge (its --sw_clipping switch only changes
  whether the clamped segments are DRAWN, which would cut straight lines along the frame). The
  Cameo 5 line gets no hardware cutting-area limit at that frame (the \\y,x Zy,x area commands are
  skipped for it; the mat-sized Z from setup is the only one), so the clip box is widened to the
  right and bottom by --beyond. Nothing is allowed above or left of the top-left mark: negative
  coordinates relative to the registered origin are untested territory.

- Dry-run scan (MTGPROXY_SIMULATE_SCAN=1, no device). The scan is answered as found, so the
  driver runs the whole cut through its real geometry and clip code; the transcript and log then
  show exactly what would be cut. (A plain dry run stops at the scan.)

Runs under the DRIVER's venv python (no mtg-proxy imports):

    python regmark_launch.py <driver_dir> [--length MM] [--thickness MM] [--beyond MM] -- <driver args>
"""

from __future__ import annotations

import atexit
import os
import runpy
import sys

SCAN_FOUND = b"    0\x03"  # the machine's reply to a successful TB123/TB124 scan
USAGE = "usage: regmark_launch.py <driver_dir> [--length MM] [--thickness MM] [--beyond MM] -- <driver args>"


def replacements(length_mm: float | None, thickness_mm: float | None) -> dict[str, str]:
    """The mark-description commands to rewrite to the printed geometry (1/20 mm units)."""
    repl = {}
    if length_mm is not None:
        repl["TB51,400"] = f"TB51,{round(length_mm * 20)}"
    if thickness_mm is not None:
        repl["TB53,10"] = f"TB53,{round(thickness_mm * 20)}"
    return repl


def rewrite(cmd, repl: dict[str, str], seen: set[str]):
    """Apply ``repl`` to one command or a list of them (send_command takes both)."""

    def one(c):
        if isinstance(c, str) and c in repl:
            seen.add(c)
            return repl[c]
        return c

    return [one(c) for c in cmd] if isinstance(cmd, list) else one(cmd)


def is_scan(cmd) -> bool:
    cmds = cmd if isinstance(cmd, list) else [cmd]
    return any(isinstance(c, str) and c.startswith(("TB123", "TB124")) for c in cmds)


def widen(clip: dict, beyond_mm: float) -> None:
    """Grow the driver's clip box right ('urx') and down ('lly') once; top/left stay put."""
    if beyond_mm and not clip.get("_widened"):
        clip["urx"] += beyond_mm
        clip["lly"] += beyond_mm
        clip["_widened"] = True


def parse(argv: list[str]) -> tuple[str, dict[str, float], list[str]]:
    if "--" not in argv:
        sys.exit(USAGE)
    split = argv.index("--")
    head, driver_args = argv[1:split], argv[split + 1 :]
    if not head or len(head) % 2 == 0:
        sys.exit(USAGE)
    opts: dict[str, float] = {}
    for flag, value in zip(head[1::2], head[2::2], strict=True):
        if flag not in ("--length", "--thickness", "--beyond"):
            sys.exit(f"regmark_launch: unknown option {flag}\n{USAGE}")
        opts[flag[2:]] = float(value)
    return head[0], opts, driver_args


def main(argv: list[str]) -> None:
    driver_dir, opts, driver_args = parse(argv)
    repl = replacements(opts.get("length"), opts.get("thickness"))
    beyond = opts.get("beyond", 0.0)
    simulate = os.environ.get("MTGPROXY_SIMULATE_SCAN") == "1"
    seen: set[str] = set()
    scanned = False

    sys.path.insert(0, driver_dir)
    # The driver's package is only importable once driver_dir is on the path.
    from silhouette import Graphtec

    cameo = Graphtec.SilhouetteCameo
    original_send, original_clip = cameo.send_command, cameo.clip_point

    def send_command(self, cmd, *args, **kwargs):
        nonlocal scanned
        scanned = scanned or is_scan(cmd)
        result = original_send(self, rewrite(cmd, repl, seen), *args, **kwargs)
        if simulate and self.transport is None and is_scan(cmd):
            self.mock_response = SCAN_FOUND  # dry run only: pretend all marks were found
        return result

    def clip_point(self, x, y, bbox):
        if "clip" in bbox:
            widen(bbox["clip"], beyond)
        return original_clip(self, x, y, bbox)

    cameo.send_command = send_command
    cameo.clip_point = clip_point

    def report() -> None:
        # No scan, no cut (the driver stopped earlier, e.g. no machine found): nothing to report.
        if (
            scanned
            and "--regmark" in driver_args
            and driver_args[driver_args.index("--regmark") + 1] == "True"
        ):
            missing = [c for c in repl if c not in seen]
            if missing:
                print(
                    f"regmark_launch: WARNING — the driver never sent {', '.join(missing)}; "
                    "its mark commands changed and the requested mark geometry was NOT applied.",
                    file=sys.stderr,
                )

    atexit.register(report)
    script = os.path.join(driver_dir, "sendto_silhouette.py")
    sys.argv = [script, *driver_args]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main(sys.argv)
