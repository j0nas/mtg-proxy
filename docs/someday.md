# Someday — follow up if you ever feel like it

None of these block cutting. Background: `studio-capture.md`.

- **Decode Studio's path format (`BE1`/`BE2`).** Studio sends paths as a compact binary stream,
  never plain `M`/`D`: `BE1` looks like single-byte steps around 144 (curves), `BE2` like 3-byte
  packed steps (long edges). Three small Studio captures would crack it: one line, one square,
  one circle. Worth it if card corners stay rough after the chord-tolerance fix and multi-point `D`
  commands (2026-09-28), or for the blog post.
- **Unknown commands, replayed verbatim:** `FM0`, `TR0,1`/`TR0,0`, `APS30`/`APS0`, `TB0`, the
  second `TB99`, Studio's `ESC SYN` poll (its reply flips before a mat finishes loading), and
  `FQ1` (3) and `FQ5` (-64 after every successful scan, straight or tilted).
- **A registration check.** The machine reports no mark positions: no known query changes with
  a tilted sheet, and Studio never asks. If misregistration recurs, add proof ticks: a few short
  cuts in the margin, then `y` to cut, `r` to rescan, `q` to quit.
- **Cutting above/left of the top-left mark** (negative registered coordinates) is untested.
  Not needed: 5 mm insets register cleanly with Studio's scan start (2026-09-28).
- **USB transport:** written, never run on hardware.
- **A blog post and a standalone library** for other Cameo owners fighting Silhouette Studio.
