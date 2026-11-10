# Lessons

Every encounter with the printer, the sheets, the Cameo or this code that cost time is logged here:
what we saw, what we first thought it was, what it turned out to be, the general lesson, and what
changed. Read this before working on building, registration or cutting. The firmware's side of
things is in cameo-re (`INVESTIGATION.md`, `NOTES.md`); the README keeps the dated measurements
behind each setting.

## General lessons

**Check the inputs before explaining the outputs.** When something that worked starts failing,
first establish what changed upstream: which code built the artifact (on every machine involved),
where it came from, and what it physically is. On 2026-10-31 a whole evening went to theories about
sheet tilt, lifting laminate and card print near the scan's passes, while the sheets had been printed
by a checkout weeks behind GitHub, with another layout. Two minutes of sensor readings across the
sheet's corner showed it; they should have come first.

**Fix the class of failure, not the instance.** The instance was "these eight sheets carry the
first 3x3 layout's marks". The class is "the machine that builds a sheet and the machine that cuts
it can run different code, and nothing notices". Code that handled the old layout's narrow margins
fixed the instance and was removed once the sheets were cut; the freshness check and the marks
recorded in run.json fix the class. Before writing a fix, name the class it belongs to and ask
whether the fix would still be needed if the class were fixed. A guard only protects the checkouts
that have it: on 2026-11-10 the class came back on a checkout older than the check, and what ended
it was running everything on one machine.

**Make artifacts carry their contract, and check it where they meet.** A sheet's run.json now
records the code that built it and the marks it printed, and `cut-proxies` refuses a sheet whose
marks differ from its own layout's. A sheet cut without its run.json carries no such check, so the
freshness check before every build and cut is the first line.

**A reading has more than one cause: report what was seen, and rule out the boring cause first.**
"Blank paper above the square reads like ink" was reported as "the sheet lifts off the mat"; it was
the square sitting 5.5 mm higher than the layout said, so the reading landed on the pouch's edge. A
message that names one cause sends the operator to fix the wrong thing. The boring cause (the
sheet isn't what we think it is) is the one to rule out before the interesting one.

**Every search gets a budget and fails with a reason.** The sensor survey once spent 4.5 minutes
searching a corner where no reading came out as paper. A search now gives up after five readings
with no paper, and paper that no reading can tell from ink stops the run before the scan, naming
the corner. Failure should be cheap and say what to do.

**When measurements stop agreeing with each other, restore a known machine state before measuring
more.** After a run was interrupted mid-survey and Bluetooth writes started timing out, readings of
the same sheet contradicted each other by millimetres. A power-cycle and reload fixed both. The
steppers run open-loop: a carriage that slips stays off until power-off (cameo-re NOTES.md).

**Physical constraints the operator states are requirements.** The laminate has to be tacked down
along its whole edge; advice to line the paper's corner up with the grid's corner put the pouch's
top border over the mat's untacky margin, and that sheet caught and came off the mat.

**Verify a theory on the machine before building on it.** The run-ups past the print and the
sensor survey were built for a tilt that the logs never showed. They are sound in the simulator and
in the tests, and the survey may prove its worth, but they were not the answer to that evening's
problem. Measure first; then build for what was measured.

**Fix the fail-fast before rerunning a slow failure.** Editing the code while a run is in progress
is safe (the process has already loaded it), so the fix that makes the next failure cost seconds
goes in first, and a run that is going to fail is stopped and restarted on it.

## 2026-10-31 to 11-01: eight sheets printed by a stale checkout

Eight laminated 3x3 proxy sheets, printed on the PC by `make-proxies` from a checkout that hadn't
been pulled in weeks. It printed the first 3x3 layout (2026-10-24): marks 3.5 mm from every edge,
10.5 mm legs, frame 203 x 290 mm. The Mac cut them as today's layout: 9 mm from the top and bottom,
6 mm legs, frame 203 x 279 mm. All eight were cut in the end, with `--reg-inset 3.5`, a
`--paper-at` measured per sheet, and `--reg-length 6` for the last three.

- **The scan took the bottom-left card's corner for the bottom-left L.** First thought: a sheet
  turned 0.2-0.45° on the mat brings that card's print onto the scan's bottom-left pass (the
  simulator agrees that it would). Built: run-ups that start both passes past the print, and a
  sensor survey that registers without the scan. Actual cause: the frame was 11 mm taller than
  the cutter assumed, so the pass started 11 mm above the real L, right beside the card.
- **Runs stopped as "the sheet lifts off the mat at its top-left corner".** First thought: lifting
  laminate, and later a wavy corner. Actual cause: the square sat 3.5 mm below the paper's edge
  instead of 9, so the blank-paper reading 3 mm above it landed on the pouch's dark edge band, and
  the placement took that band for the square and put the paper 7 mm too high. A sensor map of the
  corner (12 x 13 readings) showed the square right next to the paper's top edge.
- **The placement searched the wrong place.** Hand-laid sheets lay up to 10 mm apart on the mat
  from one to the next, and a `--paper-at` taken from the previous sheet was 5-9 mm off. The
  placement's search reaches 4.5 mm and, past that, latched onto the pouch's band or a card's art.
  Two short rows of readings (one across the left margin for the paper's edge, one down through
  the square) found each sheet in about a minute. A search that finds the paper by its edges when
  the square isn't near where it is expected would make this automatic (not built).
- **Bluetooth writes timed out**, on the first commands after connecting, three times in four
  connections, after a run had been interrupted mid-survey. Readings of the same sheet then
  disagreed by millimetres. A power-cycle and reload fixed both.
- **A sheet caught and came off the mat**, after I advised putting the paper's corner near the
  grid's corner, which left the pouch's top border off the tack. Laid with the laminate edge along
  the tacky edge, the next sheets stayed put.
- **The check after a correct scan failed** on "blank paper above the square", 2.25 mm from the
  paper's edge on that layout, and the survey it fell back to couldn't measure the square, whose
  near edges had the pouch's band 3.5 mm away. Worked around for these sheets, then removed: the
  layout is retired, and the freshness check keeps it from coming back. The survey still measures
  the square from its right and bottom edges, because today's 3x3 also has only 3.5 mm of paper
  left of the square.
- **The survey took 4.5 minutes to fail** on a corner whose paper read 221-255 everywhere. Now a
  search gives up after five readings with no paper, and paper above 235 beside a mark stops the
  run before the scan (no sheet that registered ever read more than 223 there).
- **One sheet's corner was too glossy to read** (paper 220+ around the top-right L): both the scan
  and the survey failed. Trimming the warped laminate off fixed it; it then cut first time.
- **One sheet's top row of cards came out slightly low.** Probably the old layout again: the scan
  measures the square's height from a pass starting 7 mm above it, which there began in the
  pouch's band. Not pursued, since the layout is retired.
- **One run stopped as "lifting" for a sheet lying 2.5 mm off**: its square's edge read past where
  a square with ink under its middle can end. The placement now searches around it instead.

The run-ups were built for the tilt that turned out not to be the cause, and were first tried on the
machine the same night, on Gisa's deck box (a 125 µm pouch): told a frame 7.5 mm larger both ways,
the scan registered first time and every sensor check through the stretched frame agreed. Its
first try had stopped with the sheet lifting during the scan (paper beside the square 135-141
before it, 197-224 after); a stickier mat held it.

Gisa's next cut, force 33, depth 7, 4 passes, worked out from 80 µm for its 125 µm pouch, cut the
paper and the top film and stopped in the bottom one: the extrapolation took a thick film to cut
like paper. A pass at force 38 then made the carriage sound like it skipped, and Jonas paused it.
**Raise depth, then passes, before force**: a skipped stepper loses its place on an open-loop
machine. Interrupting that paused run hung it: its return to the origin waited on the pause for
ever, behind cut commands already in the Cameo's buffer. It now gives up after a minute and says
to switch the Cameo off and on, which drops them; pressing pause would carry them out.

What changed for the class: `make-proxies` and `cut-proxies` compare this checkout and its engine
with GitHub before they run, and stop when either is behind (`freshness.py`, `--allow-stale`
overrides it); run.json records both versions and the printed marks, which `cut-proxies` checks
against its layout; every cut's session log records the versions that cut it.

## 2026-11-10: the PC's engine clone fell behind again

`make-proxies` on the PC (WSL) stopped at the duplex PDF with `create_pdf: No such option
'--no_back_marks'`. First thought: pull the engine; that fixed the run. Actual cause: the same class
as 2026-10-31. The PC kept its own mtg-proxy clone with its own engine clone, pulled by hand and at
different times: mtg-proxy there already passed the new flag, its engine was from before the fork
added it, and the clone predated `freshness.py`, so nothing compared either with GitHub. Moving the
engine into the dotfiles was considered and dropped: it would swap one pair of drifting checkouts for
another.

What changed for the class: everything runs from the Mac. `--print` already sent sheets to the PC's
Epson queues over SSH, so the PC's clone only fed the Windows mirror (`mirror.py`, removed with it),
and the clone is deleted. A bare `mtg-proxy` is now a wizard through make, print and cut, so there is
one command to run, on one machine.
