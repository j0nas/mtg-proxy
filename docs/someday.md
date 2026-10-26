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
- **USB transport:** written, never run on hardware.
- **A blog post and a standalone library** for other Cameo owners fighting Silhouette Studio.

## Next, from the first deck box on 220 gsm (2026-10-26)

These came out of a day of cutting one box. Jonas wants them built.

- **Proof targets, not proof cuts on the marks.** Proof cuts score the square's outline and the L
  legs, so every round leaves grooves on the marks themselves. After three rounds the scan could no
  longer read the square: grooves beside it merged with it, or split it, in the firmware's search
  for a 3.5-9 mm dark run. Print a few small black shapes in the header and footer for the proof
  cuts instead, and use a fresh one for each retry. Cut them with a light setting of their own, just
  enough to see; the box's force 33 came down hard on the marks.
- **Score folds in a light pass instead of perforating them.** The deck box's folds are dashed cuts
  (blue in its SVG, 203 paths against 11 red ones). They take most of the cutting time and noise,
  and a scored line may fold better. This needs a blade setting per kind of line in the driver.
- **Marks further from the paper's top edge on the deck box.** After its search row catches the
  square, the firmware backs up 7 mm to measure it. With the square 5 mm below the paper's edge,
  the window for a scan start is about 1 mm: low enough that the measuring pass starts on paper,
  high enough to cross the square. A sheet that sits 0.5 mm off misses it. The proxy sheets' square
  is 9 mm down.
- **Our own registration, as a fallback.** Fit the frame from TB63 readings across each mark's
  edges (more marks than three, with a residual check) and cut through our own transform, for
  sheets the firmware's scan can't read.
- **The cutter stands fully on its shelf.** With the mat overhanging the shelf's edge, the sheet
  tilted as it fed, and scans missed or registered off. A note for the README's placement section.

Cut settings found that day for Epson Premium Glossy 220 gsm in an 80 µm pouch: force 33, speed 4,
depth 6, 3 passes cut through. At force 20, depths 7, 8 and 9 all cut equally deep and none went
through in 3 passes: past depth 6, the force is what limits it. Force 30 at speed 10 and depth 8,
on a whole box, slipped the carriage 2.5 mm partway in.
