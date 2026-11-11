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
sheet isn't what we think it is) is the one to rule out before the interesting one. The same goes
for a check: one that passes on any dark reading passes on the paper's edge, the pouch and a card's
print as well as on the mark (2026-11-11). A check needs a reading that would come out differently
if the failure it guards against had happened.

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

## 2026-11-11: the wizard's cut handed on --svg as a string

The paper deck box's window cut (`cut-proxies --svg … --reg-inset 5 --preset paper …`) stopped
before reaching the Cameo, with `AttributeError: 'str' object has no attribute 'is_file'` in
`run_cut`. First thought, on the deck box's side: its change the evening before, which dropped `-p`
and `--layout` from its flags. Actual cause: feed974 split `cut` into the command and `cut_flow`, so
the wizard could call the flow, and the command forwarded `ctx.params`. Typer calls the command with
converted values (a `Path` for `--svg` and `--run`), while `ctx.params` keeps Click's raw ones, a
str. Every `--svg` cut failed. The cut's tests stub `run_cut` and none of them passed `--svg`, and
the wizard's tests stub `cut_flow`, so all of them stayed green.

The lesson: when a refactor changes how a command hands its options on, test it through the real
entry point with each kind of value it converts, a path among them. Two copies of the same input
that differ in type are a trap; hand on the one the function was given.

What changed: `cut` forwards its own arguments as Typer gave them, and `test_cutting.py` sends
`--svg` through the CLI and checks that `run_cut` gets a `Path`.

## 2026-11-11: a scan the firmware had to retry passed every check and miscut

The last of seven cuts of the Tatty DFC sheet (`output/cut/tatty-dfcs-and-staple-mdfcs.20261111-125454`)
came out right at the top-left and far out on the right. The scan answered "found", all nine
readings after it passed, and the cut went ahead.

Two things in its log set it apart from the six before it. The scan took 24 s against 11 s for the
others (one took 17 s): that is the firmware's retry, which recalibrates the light, searches again
from a third of a frame away, and still answers "found". And the readings after the scan test each
L with one point on each leg and accept any dark reading, where the square also gets blank paper on
all four sides. The paper ends 3.5 mm right of the top-right L, and the pouch's sealed border and
the mat beyond it read as dark as ink, so a corner registered out there reads 255 on both legs. The
likely mechanism, not yet measured on the sheet: on the retry, the top-right pass (it starts 4 mm
inside the leg and sweeps outward) missed the leg and fired on the paper's edge, and the pass up the
horizontal leg then ran past that leg's end. The firmware's only check on its fit is a turn under 5°.

The first question was how the checks let it through, and the answer was already in the code:
`sensing.py` records that on 2026-10-26 the top-right leg readings passed "only because everything
there read 255". The gap was noted and left open.

The lesson: a check proves only what it could have failed on. Dark at one point can't fail for a
frame registered onto the paper's edge, the pouch or a card's print, which are the misregistrations
that happen. Behind it is a larger pattern: every check so far tests the consequences of a fit the
firmware computes and won't show, and each failure has added another indirect test.

What changed: after the scan, every L leg now also has blank paper read 2.5 mm either side of it
(2.0 mm on the 3x3, whose marks lie 3.5 mm from the paper's edge), judged against the paper level the
square's checks use, so a leg must be a thin line with paper beside it; `held` still reads only
ink, since a cut groove beside a mark reads dark. A scan over 16 s (the retry; 11-12 s otherwise) is
logged as `slow_scan` with its duration and says so on the terminal, and the stricter check decides;
no stop for the duration alone. A test has everything round the top-right L read as ink after the
scan and the cut stop before the blade goes down. On the good test sheet the new points read paper
(96-113) and the legs 255 (`output/cut/stepC-scan-*`). The other fix on the table, registering from
our own readings, is `--register sensor` (see the debug-sensor entry above). The root, that the
firmware reports neither its sensor nor the marks it found, is being looked at as a firmware patch in
cameo-re.

## 2026-11-11: the firmware's debug sensor read, in the dark and in the wrong place

Looking for a flash patch turned up a command that needs none: `FPGRFCC1` opens the firmware's
debug set, whose `a2` reads the sensor once in 60 ms (cameo-re NOTES.md). Two of its first three
runs on a laminated test sheet read nonsense. The first read 0-4 everywhere. First thought: `a2`
reads another channel. Actual cause: TB63, which the placement had just used, switches the light
off when it ends, and TB61 only sets its level. The second read paper on the square and dark
everywhere at the top-right. Actual cause: the sensor sits 19.2 mm right of the blade, and TB63
moves the head by that before it reads, so no reading so far had shown it. With the light on and
the head shifted, all three marks read crisply (paper 550-615, ink 65-160). The left marks then
needed a move the cutting area doesn't clamp (`g9`), which travels 1.2% short across the carriage
and 0.7% along the feed: a fixed calibration that normal moves apply and `g9` skips, measured
after a power cycle.

The lesson: a command lifted out of the firmware's own routines comes without what its callers do
around it (here, switching the light on and moving the sensor over the spot). Read the callers
before the first try, and make that first try a row across a known feature: both mistakes showed
in one run each because every run read profiles, never single points.

What changed: the readings before the scan (the placement and its hunt, the flatness check) now
use `a2` through `cameo/reading.py`, and the scripts that worked were cameo-re's
`scripts/debug_sensor_sweeps.py` and `scripts/debug_sensor_g9.py` (logs: `output/cut/a2-test*.session.jsonl`).
The helper opens the set once per connection and does what the callers did around TB63: light on
(`a0`, `a1,120`) and off, the head 19.2 mm left of the point, `g9` at 1.0068 and 1.0118 times the
distance. The safety rule is in the code: `protocol.cmd` raises on any lowercase letter,
`protocol.debug` takes only `a0`, `a1,n`, `a2`, `a4` and `g9`, and a test sends a whole cut through a
fake machine and checks that nothing else lowercase left the driver. On other firmware than V1.04, or
when `a2` doesn't answer with a number, the run falls back to TB63 and says so on the terminal.
`sensing.py` judges both scales (`Tb63`, `A2`): on a2, ink is a reading under half the blank paper
read beside the mark, paper at least three quarters of it, and blank paper under half of the top
margin's reads as a lifted sheet. That last threshold is a guess from the TB63 behaviour (a mirroring
film took light away from the sensor); no a2 reading of a lifted or wavy sheet exists. The checks
after the scan are still TB63, which needs the paper beside the square on its own scale: they now read
the four paper points before the square's middle, and the "lifts" test reads one TB63 reading of the
top margin when it needs it. Measured on the test sheet (`output/cut/stepB-*.session.jsonl`): the
placement and flatness check took 5.5 s placed right and 7-8 s with the sheet 3 mm off, against 41 s on
TB63; a2 puts the square's bottom edge 0.3-0.4 mm higher than TB63 does (TB63 saturates at 255 over a
spot half on the ink, which makes the square look bigger), not yet checked against the scan.

A second step made the sensor register the sheet itself (`cut --register sensor`, off by default).
`survey.py`, which did this on TB63 only as a fallback after a failed scan, now takes a mode
(`survey.A2_MODE`: readings 0.5 mm apart, edges halved to under 0.2 mm, given up after eight readings
with no paper) and runs on the debug sensor: after the placement and flatness check every mark is
surveyed, the page fitted, the fit refused unless square and as wide and tall as printed, and the
marks read again through the fit (the square with paper on four sides, every leg with paper either
side). No TB123 runs, so no transform sits under the cut's moves; the fit is in the mat's own frame,
which the `g9` moves read in. On the test sheet, three runs and one from a start 3 mm off: 46
readings, 12.4 s for the survey and 30-32 s from connect to ready to cut (against 76 s for the scan
path, readings included), marks 203.01-203.02 x 279.45-279.46 mm apart against 203 x 279 printed,
turned 0.03°, the fit's origin repeating within 0.01 mm. The acceptance cut, the same evening on the
same test sheet: every gauge card came out with its margin within a fraction of a millimetre of
1.0 mm on all four sides, so the sensor's 19.2 mm is the offset in `M`'s terms and
`data/cut_offset.json` carries over. The top of the bottom-right card came out a little closer to
its frame, which fits the fit's height: 279.46 mm against 279.13 for the print, measured with `M`
over the cards' hairlines. A likely cause is backlash along the feed: the survey halves in on each
edge from both sides, where the hairline readings all came from one side. Approaching every reading
from the same side would test it. With that, the sensor's registration became the default
(`--register sensor`); `--register scan` keeps the Cameo's own scan as the fallback until more
sheets have cut through the new flow, and `--no-check` uses it too. The sensor
cannot reach left of 1.7 mm on the mat, so a sheet laid much further left than the test sheet's
3.6 mm stops with a plain message.

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
