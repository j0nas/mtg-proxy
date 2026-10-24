# mtg-proxy

Decklist in, print-ready PDF out, cut on the Cameo by our own driver. A small Python CLI (`mtg-proxy`,
managed with `uv`) that drives [silhouette-card-maker](https://github.com/Alan-Cha/silhouette-card-maker)
in-process, from my fork `j0nas/silhouette-card-maker` (branch `local-patches`: batched and
parallel Scryfall fetching, `--token_copies`, MTGA parser fixes).

Built for one setup: Epson ET-8550, 135 gsm glossy photo paper, 80 µm laminate, Silhouette
Cameo 5 Alpha with the AutoBlade. Finished card is ~0.32 mm; a real one is 0.305 mm. What the
cards cost and why the stack looks like this is written up at
[jona.no/docs/mtg-proxying](https://jona.no/docs/mtg-proxying).

```
mtg-proxy make      decklist -> ./<deck>/{<deck>.pdf, <deck>-duplex.pdf?, CUT-NOTES.md, run.json}
mtg-proxy cut       cut a printed sheet on the Cameo; reads run.json from the deck folder
mtg-proxy redo      queue miscuts / a skipped page of a run for reprinting (BACKLOG.txt)
mtg-proxy backlog   show the queue, `backlog build` prints it as one sheet, `backlog drop` edits it
mtg-proxy offset    store the printer's duplex offset once
mtg-proxy notes     show the newest CUT-NOTES.md below the cwd
mtg-proxy cache     Scryfall image cache stats / --clear (~/.cache/mtg-proxy)
mtg-proxy doctor    check engine and data files
```

`make-proxies.sh`, `cut-proxies.sh` and `save-offset.sh` are shims onto those commands, and
`mtg-proxy.sh` onto the whole CLI. Output lands in the current directory, one folder per deck. Under WSL it is also mirrored flat into
`%USERPROFILE%\Desktop\projects\mtg-proxy` (`MTG_PROXY_WIN_OUT` overrides).

```
src/mtgproxy/          engine.py (in-process engine), build.py (pipeline), decks.py (Moxfield/Archidekt),
                       local.py (--images),
                       layout.py (sheet layouts, cut SVG), cutting.py (cut-proxies), cameo/ (the Cameo driver)
data/cut_offset.json   the machine's cut offset in mm, applied to every cut
data/offset_data.json  printer duplex offset, applied to every double-sided PDF
assets/back.png        default card back for --backs; replace with your own
silhouette-card-maker/ the vendored engine clone, gitignored, made by setup.sh
```

## Setup

```sh
./setup.sh              # clones the engine fork, builds the venv, installs git hooks
mtg-proxy doctor        # what's missing and how to fix it
```

Needs `uv` and `git`. USB cutting on a Mac also needs `libusb` from Homebrew. Rerun `setup.sh`
after pulling; it's idempotent.

## Test sheet first

```sh
make-proxies --test                # ./test-sheet/test-sheet.pdf
make-proxies --test --print        # and send it to the default CUPS printer at 100 %
make-proxies --test --layout 3x3   # nine of them on portrait A4 (see Nine to a sheet)
```

One A4 of eight gauge cards: exact 63x88 mm geometry, hairline art, almost no ink. The frame sits
1.0 mm inside the cut line, so after cutting the white margin must be 1.0 mm on all four sides.
Gray ticks at 0.5 and 1.5 mm read any offset. A crosshair on front and back checks duplex
alignment against a light. A TOP marker catches flips.

Print at actual size, borderless off. Scaling moves the registration marks and the scan fails.
Duplex is manual, long-edge flip. If the gauges read clean, print real cards.

`--print` goes through CUPS with scaling forced off, using the `4x2-glossy` printer instance
(rear feeder, glossy photo, High). `docs/printer-presets/README.md` has the Windows presets and
the CUPS mapping.

## Real decks

```sh
make-proxies decks/mydeck.txt                    # MTGA format, A4, 4-mark registration
make-proxies decks/mydeck.txt -f moxfield        # also mtgo, archidekt, deckstats, simple
make-proxies https://moxfield.com/decks/<id>     # straight from Moxfield
make-proxies https://archidekt.com/decks/<id>    # or Archidekt
make-proxies <deck-url> --board considering      # main | side | considering
make-proxies decks/mydeck.txt -r 3               # 3-mark registration (see Cutting)
make-proxies decks/mydeck.txt --layout 3x3       # nine to a sheet, portrait, 3-mark (see below)
make-proxies decks/mydeck.txt --backs            # double-sided; default is fronts only
make-proxies decks/mydeck.txt --basics           # include basic lands; skipped by default
make-proxies decks/mydeck.txt --images cards/    # your own card images where you have them
make-proxies decks/mydeck.txt -t 2               # 2 of each distinct token at the end
make-proxies decks/mydeck.txt --skip-fetch       # reuse fetched images
make-proxies decks/mydeck.txt --dry-run
make-proxies decks/mydeck.txt -- --prefer_set sld   # extra fetch.py args after --
make-proxies decks/mydeck.txt --trim "Temple Garden" --trim "Battle Angels of Tyr:0.5"
```

Moxfield and Archidekt URLs are read from the sites' JSON, saved as `<deck>/<deck>.txt`, and
the run continues from that file. Each line carries the printing chosen on the site, which the
fetch honors. `--board considering` exists because Moxfield can't export its Considering board.
Private decks are not reachable.

Double-faced cards go to a separate `<deck>-duplex.pdf` with their real backs. Print it with
manual duplex and cut it like any other sheet.

Images are cached in `~/.cache/mtg-proxy`, keyed by Scryfall image URL. A 100-card deck builds in
~15 s the first time and a couple of seconds after. Lookups always hit Scryfall, so a list without
printings resolves to current art. `MTG_FETCH_WORKERS=1` forces serial fetching if Scryfall
complains.

`--trim`: the engine builds the 1.25 mm bleed by smearing each image's outer pixel row outward.
On borderless printings that row is a dark rim, so the bleed shows as a band. `--trim "Card"`
replaces the outer 0.3 mm with the ring just inside first; `:0.5` sets the width, `all` does
every card.

Every output folder gets a `CUT-NOTES.md` for that run and a `run.json` that `cut-proxies` reads,
so the registration pattern you printed is the one it scans for. `run.json` also holds the
manifest: which printing sits on which page and slot of each PDF, which is what `redo` uses.

### Your own card images

`--images DIR` prints your own card images in the same run as the fetched cards. Name each image
after the card's front face, lower case, with dashes for anything that isn't a letter or digit:
`fell-the-profane.jpg` for `Fell the Profane // Fell Mire`, `norn-s-annex.png` for `Norn's
Annex`. Case, punctuation and the line's set and number don't matter; .jpg and .png both work.

```sh
make-proxies decks/aminatou.txt -f moxfield --basics --images cards/
make-proxies decks/aminatou.txt -f moxfield --images cards/ --dry-run   # how many from cards/, how many fetched
```

- Every deck line with an image prints from it, as many copies as the line asks for, and is never
  looked up. The rest is fetched as usual. The sheet keeps the decklist's order.
- An image is one face: a double-faced name prints as a single-sided card, never on the duplex
  sheet, and gets the plain back with `--backs`.
- Basics follow `--basics` like fetched ones: skipped unless you ask for them.
- Images should be the whole card at 63 × 88 mm, full bleed, at any resolution. The engine scales
  them to the card and builds the bleed from their edge, as it does for scans. `--trim all` leaves
  them alone; naming one in `--trim` still trims it.
- An image that matches no deck line is ignored; `--dry-run` counts them.
- Line-by-line formats only (mtga, moxfield, archidekt, mtgo, deckstats, simple) and deck URLs.
  The moxfield and archidekt formats skip a line without `(SET) number` unless it has an image;
  `-f mtga` reads both kinds.
- A card from an image brings no tokens with `-t`, since it is never looked up.

### Nine to a sheet

`--layout 3x3` puts nine standard cards on portrait A4, one more than the default 4x2. It is the
card-maker's tighter layout, with its marks set by the engine fork: 3.5 mm from the paper's sides,
9 mm from the top and bottom (`inset_y`), legs 6 mm, and no page label. The firmware reads each L
half a leg in from its corner and starts the bottom-left search 10 mm above it. With the card-maker's
3.5 mm all round and 10.5 mm legs, the bottom-left L read 1–2 mm left on a laminated sheet, three
scans out of three; at 7 mm its search began on the bottom-left card's corner and once read that
(2026-10-24). Now every pass stays 1.2–2.6 mm clear of the card print (simulated on a real sheet).
It prints 3-mark registration only, the square top-left and L marks top-right and bottom-left, and
refuses `-r 4`, which hasn't been tried at these insets. It is A4 with standard cards and fronts
only. `--backs` is refused, and so is a deck whose
double-faced cards would need a duplex sheet; `--split-faces` prints their faces on the 3x3 sheet.
`run.json` records the layout, so `cut-proxies` scans the right frame and cuts the right outlines;
`redo`, `backlog --layout 3x3` and `backlog build --layout 3x3` count nine to a sheet.

The marks' outer edges are 3.0 mm from the paper's sides, and the ET-8550 prints to 2.96 mm from
every edge, so the sheet must print at 100 %. The PDF page is landscape like every sheet's: print it
upright (rotated a quarter turn, then portrait at 100 %) and put it on the mat portrait with the
square at the top-left. The square sits 0.6 mm from the top-left card's print, beside its corner.

## Reprints and leftovers

A deck rarely ends clean: a miscut or two, a card that laminated badly, and a last page that
was only half full and not worth a sheet. All of that goes into one queue, `BACKLOG.txt`, in
the directory the deck folders live in, and gets printed as one well-filled sheet later.

```sh
make-proxies decks/mydeck.txt --defer-partial   # partial last page → BACKLOG.txt, not the PDF
cd mydeck && mtg-proxy redo "Sol Ring" p3.5 last  # after cutting: by name, page.slot, or a whole page
mtg-proxy redo dlast                            # d = the duplex PDF (d1, d1.2, dlast)
cd .. && mtg-proxy backlog                      # 11 fronts = 1 full sheet + 3 waiting, 2 duplex
mtg-proxy backlog build                         # ./backlog-<date>/ — a run folder like any deck
mtg-proxy backlog build --full-only             # full sheets now, the rest stays queued
mtg-proxy backlog drop "Sol Ring"               # or by the number the listing shows
```

`redo` takes the exact printing, trim and front/duplex placement from the run's manifest, so
the reprint matches what you cut. Repeat a name for more copies; a name must match one card
(`temple` is fine, `l` is not). The backlog is MTGA lines with the source deck in a comment,
so it can be edited by hand. `backlog build` writes the built cards into the run folder's
decklist and removes them from the queue; a miscut from a backlog sheet goes back in with
`redo` like any other. Runs made before manifests existed are re-derived from their decklist
through the image cache on the first `redo`; pass the `-t N` / `--basics` they were built with.

A card printed from `--images` is queued with its image path (`image=…` in the comment), and
`backlog build` copies those images into the run folder's `images/` and prints them again. If an
image has moved since, the build stops and names it.

The dotfiles' `make-proxies` and `cut-proxies` functions find the clone (`$MTG_PROXY_DIR`, then
`~/Desktop/projects/mtg-proxy`) and run the shims from any shell, including WSL with Windows
paths.

## Cut offset

With cut-proxies' scan, the Cameo 5 Alpha cuts 0.5 mm high relative to the printed marks, and
true in x. `data/cut_offset.json` holds the correction (`y_mm: 0.5`; positive shifts cuts
right/down). It was measured with the deck-box project's calibration sheet (`pnpm calib` in
`~/Desktop/projects/silhouette/deckbox`): printed vernier scales at five stations, cut vernier
ticks scored against them. To re-measure, score one with the offset off (`--x-off 0 --y-off 0`)
and read where each cut tick 0 lands against its printed 0 line.

## Duplex offset

Once, for `--backs` decks and duplex sheets:

1. Print `silhouette-card-maker/calibration/a4-calibration.pdf` like a card sheet.
2. Against a light, find the front/back square pair that lines up and read its `(x, y)`.
   Units are 300 PPI pixels, ~0.085 mm.
3. `mtg-proxy offset -x <x> -y <y>` (`-a <deg>` for rotation).

Stored in `data/offset_data.json`, tracked, applied to every double-sided PDF.

## Cutting

`cut-proxies` drives the Cameo over Bluetooth LE (`--usb` for the cable) with its own driver,
`src/mtgproxy/cameo/`, and sends the scan command explicitly: `-r 4` is the four-L-mark scan,
`-r 3` the square plus two L's (untested on the machine; see Registration). Both follow the
sheet's `run.json`, which holds the marks and layout `make-proxies` printed.

Run bare at a terminal, `cut-proxies` asks first. Enter cuts the default: this folder's
`run.json`, or a plain A4 sheet (standard cards, four L-marks) when there is none, with the
laminate preset over Bluetooth LE. "Choose…" picks another run below this folder, the preset, and
cut, proof first, dry run or probe. `--usb` and `--ble-name` keep the menu; any other flag, or a
pipe, skips it and cuts as told.

```sh
cd mydeck && cut-proxies   # menu; Enter cuts this run (paper, card size, mark pattern from run.json)
cut-proxies --run mydeck   # same, from the parent folder
cut-proxies --preset paper # plain paper instead of laminate
cut-proxies --passes 4     # also --force/--speed/--depth, --x-off/--y-off
cut-proxies --usb          # over the cable instead of Bluetooth LE
cut-proxies --scan         # list the Bluetooth devices in range
cut-proxies --dry-run      # no machine: runs the whole job against a stand-in, checks the bounds
cut-proxies --probe        # scans the marks, logs what the machine reports, cuts nothing
cut-proxies --proof        # scores each mark's L after the scan; y cuts, r rescans, q stops
cut-proxies --layout 3x3   # a 3x3 sheet without its run.json (3-mark; 3.5 mm sides, 9 mm top/bottom)
cut-proxies --svg job.svg --reg-inset 5   # any page-sized SVG
```

Settings, AutoBlade only (the Kraft blade can't turn the 3 mm corners):

| `--preset` | Force | Speed | Depth | Passes |
|---|---|---|---|---|
| `laminate` (default): 130–135 gsm glossy in 80 µm pouches | 20 | 25 | 5 | 3 |
| `paper`: plain copier paper, cut through | 10 | 5 | 1 | 1 |

Explicit flags override the preset. Don't eject after a cut: lift a corner. At a terminal,
cut-proxies then watches the mat. Unload it, load the next sheet, and five seconds after the mat
is back in, the same job starts on it, counting sheets as it goes. It only starts after it has
seen the mat leave, and Enter during the countdown holds it. A batch started with proof cuts has
them on its first sheet only.

Enter while it watches brings up the menu instead: "next sheet" cuts a sheet that's already
loaded, "next sheet, proof cuts first" asks for proof cuts again, "one more pass" rescans the
marks and cuts this sheet once more, for a cut that isn't through, and "done" stops. Ctrl-C stops
at any point. When a job fails (marks not found, proof stopped, the connection lost), it offers to
try the same job again. From a script, a failure exits, and `--passes 1` is the extra pass.
Rippled edges mean too much force or a dull blade. Max force or speed makes the machine skip; power-cycle to rehome.

The job follows Silhouette Studio's own conversation with this machine, recorded over Bluetooth
(`docs/studio-capture.md`). `mtgproxy.cameo`:

- `geometry`: the SVG → polylines in page mm, curves flattened to a 0.02 mm chord. Everything
  inside an outline is cut before it, nearest start first. Closed loops are cut as continuous
  laps with a 0.5 mm overcut.
- `protocol`: the GP-GL strings, pinned by the tests to Studio's.
- `transport`: Bluetooth LE (bleak), USB (libusb1), or a recording stand-in for dry runs.
- `session`: preflight (refuses without a loaded mat), mark description and scan, blade setup,
  and the cut in ≤1 KB pieces with a status wait between them. It always returns to the origin
  and never moves it.
- `proof`: the proof cuts over the marks, and whether one would touch the job.

Registration:

- `--reg-length` / `--reg-thickness` (mm, default the layout's: 9.4 × 1 on the card-maker's 4x2
  A4, 6 × 1 on its 3x3) describe the printed marks before the scan.
- The scan starts on white paper above the top-left mark's horizontal leg, and the sensor
  travels down onto it. It registers only from a window about 2 mm wide each way, and from one
  load to the next the marks move about that much (the hand's millimetre on the mat, and where
  the machine takes the mat in). So each sheet starts where the last sheet with the same marks
  registered (`output/cut/scan-start.json`), and a miss tries the starts 2 mm around it, across
  first, then down and up, then the diagonals: up to 9 starts, 15–20 s per miss. With nothing
  remembered it starts at (7.5, 18) mm at a 10 mm inset, from the laminated sheets of
  2026-10-20, and (2.5, 11.5) at 5 mm, where starting 0.5 mm past the vertical leg skewed cuts
  and 11.5 never did (calibration sheets, 2026-09-28). `--scan-start TOP,LEFT` (repeatable)
  replaces the search with exactly those starts. Coordinates are from the job's own origin, which
  the job's first command (`TG1`) moves 16.5 mm in once per mat load: anything that measures
  positions on the machine must send it first.
- 3-mark sheets (`make-proxies -r 3`, then `cut-proxies` reads it from `run.json`) are scanned
  with `TB123`, which finds the top-left square by an area search instead of one line down:
  rows 3 mm apart from the start to 30 mm below it, each sweeping 30 mm to its right, for a
  dark run 3.5–9 mm long. It then measures the square down and across and takes its centre less
  2.5 mm as the edge, which lands on the L marks' centre lines: the frame and the cut are the
  same as a 4-mark sheet's. The square must print exactly 5 mm. The card-maker drew it with a
  1 mm outline until the engine fork's cd4166a; at 6 mm the machine found it, its sideways pass
  ran past it and the scan failed (`-1`) from every start. Regenerate 3-mark sheets made before
  that. The search must cross the square's left edge from white paper: it starts at (8, 4) mm at
  a 10 mm inset, which registered first time on plain A4 (2026-10-24); starts on or past the
  square never find it (`1`). No bottom-right mark is read, so a stretched sheet's far corner
  goes unchecked. A 3x3 sheet's search starts at (11, 1) mm, 2 mm into its square (3.5–8.5 mm
  across, 9–14 mm down) and 2.5 mm left of it, so the first row runs through the square: a start
  above it on laminate took the firmware's retry and registered off the sheet (2026-10-25).
- `--reg-inset MM` moves the expected marks from the layout's inset (10 mm; 3.5 mm on a 3x3 sheet)
  toward the paper edge (5 mm is proven). It must match the printed marks.
- Cuts may reach above or left of the top-left mark, as long as they stay on the paper: the
  cutting area then starts past them (`\\-y,-x`); every other job sends Studio's `\\0,0`.
  First used on the deck box's round 4 (2026-09-28). Keep 6 mm around every mark free of print
  (Graphtec's guidance): ink near a mark can be read as part of it.

The machine reports no mark positions (FQ5 reads -64 after every successful scan, straight or
tilted; its firmware keeps them to itself), so a misread can't be caught from the replies. It can
be caught by eye: `--proof` scores every mark's L along its centre lines after the scan (a
3-mark sheet's square: its outline, along the edge of the black), then waits. Lift the lid: registered right, every cut runs down the middle of the
black and ends where the leg ends; a misread corner's cuts sit beside its lines, by the size of
the misread. Each leg is left uncut for 1.5 mm either side of where the sensor crosses it, so a
rescan reads clean ink: what stays is an L at each corner and a piece at each leg's end. `y`
cuts the job, `r` goes home and scans again (the new cuts go on the same marks; up to three
rounds), `q` stops with nothing more cut. Every run writes its own
`output/cut/<name>.<date>-<time>.session.jsonl`: each byte sent and received, with timestamps,
plus the Bluetooth movement events.

Placement: sheet top-left on the mat grid, aligned to the paper edge, not the laminate edge.
Standard 12x12" mat against the left notch, pinch rollers on the mat, lid closed, room neither
very bright nor very dark. A sheet that creeps on a worn mat skews long jobs: use a sticky mat
or tape the edges. If the scan fails nothing is cut. Glare from glossy laminate is a known cause
of failed scans; glossy sheets cut fine here, but matte is the safer choice if scans fail.

Finish: cut cards can delaminate at the edges. Run them through the laminator again. Cloudy
lamination is too cold (run 80 µm pouches on the 5 mil setting); wavy cards are too hot.

A3 works on the ET-8550 (18 cards a sheet) but needs the 12x24" mat.

## Development

```sh
uv sync
uv run pytest           # ~90 tests, no network, no printer, no Cameo, ~2 s
uv run ruff check src tests && uv run ruff format src tests
```

The pre-commit hook runs both. Dependencies are pinned to the engine's `requirements.txt` in
`pyproject.toml`; bump both when the fork updates. The engine is imported in-process
(`engine.py` puts its root on `sys.path` and runs its click commands under `chdir`), so its
working dirs stay where it expects them.

## Sources

- [silhouette-card-maker docs](https://alan-cha.github.io/silhouette-card-maker/)
- [GitHub issue #162](https://github.com/Alan-Cha/silhouette-card-maker/issues/162), an Alpha user's 4-corner registration experience
- [Silhouette School: 3 vs 4 registration marks](https://www.silhouetteschoolblog.com/2026/01/silhouette-registration-mark-types.html)
- [Silhouette School: registration failures on glossy media](https://www.silhouetteschoolblog.com/2016/10/silhouette-print-and-cut-registration-failed.html)
