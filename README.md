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
mtg-proxy make      decklist -> ~/Desktop/proxies/<deck>/{<deck>.pdf, <deck>-duplex.pdf?, CUT-NOTES.md, run.json}
mtg-proxy cut       cut a printed sheet on the Cameo; reads run.json from the deck folder
mtg-proxy redo      queue miscuts / a skipped page of a run for reprinting (BACKLOG.txt)
mtg-proxy backlog   show the queue, `backlog build` prints it as one sheet, `backlog drop` edits it
mtg-proxy offset    store the printer's duplex offset once
mtg-proxy notes     show the newest CUT-NOTES.md in ~/Desktop/proxies
mtg-proxy cache     Scryfall image cache stats / --clear (~/.cache/mtg-proxy)
mtg-proxy print     print a PDF on the PC's Epson queues (--paper plain|glossy|premium, --dry-run)
mtg-proxy doctor    check engine and data files
```

`make-proxies.sh`, `cut-proxies.sh`, `print-sheet.sh` and `save-offset.sh` are shims onto those commands, and
`mtg-proxy.sh` onto the whole CLI. Output lands in `~/Desktop/proxies`, one folder per deck, wherever the command
runs from; `-o` or `MTG_PROXY_OUT` puts it elsewhere. `cut-proxies`, `redo`, `backlog`, `notes` and `simulate` look there
too, unless you are standing in an older output folder (a deck folder, or the folder above one), which
they use instead. `--run` takes a run's folder name: `cut-proxies --run mydeck`. Under WSL output is also mirrored flat into
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
make-proxies --test --print        # and print it on the PC's plain-paper queue
make-proxies --test --layout 4x2   # eight of them on landscape A4 (see Sheet layouts)
```

One portrait A4 of nine gauge cards: exact 63x88 mm geometry, hairline art, almost no ink. The
frame sits 1.0 mm inside the cut line, so after cutting the white margin must be 1.0 mm on all four
sides.
Gray ticks at 0.5 and 1.5 mm read any offset. A crosshair on front and back checks duplex
alignment against a light. A TOP marker catches flips.

Print at actual size, borderless off. Scaling moves the registration marks and the scan fails.
Duplex is manual, long-edge flip. If the gauges read clean, print real cards.

Nothing is printed from the Mac. `--print` (and `print-sheet.sh`) copies the PDF to the Windows PC
over SSH and prints it there with SumatraPDF at 100 %, through one of the PC's Epson queues: real
sheets go to `Glossy photo paper proxies`, `--test` sheets to `ET-8550 Series(Network)` (plain
paper). Landscape pages are turned upright first, because the driver ignores a landscape setting
and clips the page.

```sh
print-sheet.sh deck/deck.pdf                       # glossy queue
print-sheet.sh deck/deck.pdf --paper premium       # Premium glossy paper (220 gsm)
print-sheet.sh deck/deck.pdf --paper plain --dry-run   # copy to the PC, show the command, stop
```

The queues carry the paper presets as their defaults; `docs/printer-presets/README.md` lists them.
The command needs the key `~/.ssh/pomelomadness_ed25519` and the PC on the same LAN. It sends the
job once and never retries, since a resend prints a second sheet.

## Real decks

```sh
make-proxies decks/mydeck.txt                    # MTGA format, A4, nine to a sheet
make-proxies decks/mydeck.txt -f moxfield        # also mtgo, archidekt, deckstats, simple
make-proxies https://moxfield.com/decks/<id>     # straight from Moxfield
make-proxies https://archidekt.com/decks/<id>    # or Archidekt
make-proxies <deck-url> --board considering      # main | side | considering
make-proxies decks/mydeck.txt --layout 4x2       # eight to a sheet, landscape (see below)
make-proxies decks/mydeck.txt --backs            # double-sided; default is fronts only
make-proxies decks/mydeck.txt --basics           # include basic lands; skipped by default
make-proxies decks/mydeck.txt --images cards/    # your own card images where you have them
make-proxies decks/mydeck.txt -t 2               # 2 of each distinct token at the end
make-proxies decks/mydeck.txt --skip-fetch       # reuse fetched images
make-proxies decks/mydeck.txt --dry-run
make-proxies decks/mydeck.txt -- --prefer_set sld   # extra fetch.py args after --
make-proxies decks/mydeck.txt --alt-names        # also rank printings with another name on the card
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

Fancy art (the default) ranks a card's printings newest first and prefers showcase, then
borderless, extended or full art. Printings that show another name on the card stay out of that
ranking: Universes Beyond versions such as "Rathalos, King of the Skies" for Drakuseth, Maw of
Flames, and Japanese-only Mystical Archive cards. `--alt-names` lets them back in. A set and
collector number on the deck line always wins, so `1 Drakuseth, Maw of Flames (SLD) 2237` prints
the Rathalos art without the flag. Prize printings such as the Legacy Championship duals carry the
real name and stay in. With `--prefer_lang`, translated printings count as the card's own name.

`--trim`: the engine builds the 1.25 mm bleed by smearing each image's outer pixel row outward.
On borderless printings that row is a dark rim, so the bleed shows as a band. `--trim "Card"`
replaces the outer 0.3 mm with the ring just inside first; `:0.5` sets the width, `all` does
every card.

Every output folder gets a `CUT-NOTES.md` for that run and a `run.json` that `cut-proxies` reads,
so the layout you printed is the one it cuts. `run.json` also holds the
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

### Sheet layouts

An A4 sheet of standard cards holds nine unless told otherwise: the 3x3 layout, on portrait A4.
It is the card-maker's tighter layout, with its marks set by the engine fork: 3.5 mm from the
paper's sides, 9 mm from the top and bottom (`inset_y`), legs 6 mm, and no page label. The
firmware reads each L half a leg in from its corner and starts the bottom-left search 10 mm above
it. With the card-maker's 3.5 mm all round and 10.5 mm legs, the bottom-left L read 1–2 mm left on
a laminated sheet, three scans out of three; at 7 mm its search began on the bottom-left card's
corner and once read that (2026-10-24). Now every pass stays 1.2–2.6 mm clear of the card print
(simulated on a real sheet), and four laminated sheets registered and cut in a row (2026-10-25).

Both A4 layouts also print four proof targets, the 3 mm black squares `cut-proxies --proof` traces
(see Cutting), in the strip between the top marks' row and the cards: 11.75 mm down at x 120, 142,
164 and 186 mm on a 3x3 sheet, 12.9 mm down at x 165, 190, 215 and 240 mm on a 4x2 one
(layouts.json `proof_targets`, which the engine prints from and `cut-proxies` cuts from). On a 3x3
sheet that strip runs from the marks' line 9 mm down to the cards' print at 14.5 mm (the print
bleeds 0.7 mm past the cards' cut), and the bottom one, from 282.4 mm to 288, is the same height;
the side strips are 5 mm wide and the gaps between cards 1.3 mm, filled with the cards' bleed. The
top strip keeps the targets inside the mark frame and between the two marks the scan reads
first, where the registration is best. They leave 1.25 mm of paper above and below, keep 6 mm and
more from every mark, and stay out of the square's search area, the readings of the sensor check
and the place a third of the way along where the firmware reads its light for a retry. Layouts on
other paper and card sizes have none.

Check a new mark layout offline before printing it. `mtg-proxy simulate sheet.pdf` (inside a run
folder, just `mtg-proxy simulate`) walks the scan's passes over the page's print: the area search,
the two passes that measure the square, and two across each L. It takes `--layout`, `--reg-inset`,
`--reg-length` and `--scan-start` like `cut-proxies`, and `--page` for a later page. Each line gives
the dark runs the pass crosses and the nearest print that is not a mark. The firmware's detector is
relative, so a pass fails when its first dark run is not a mark or print comes within 1 mm of it,
the width of the sensor's spot; the command then exits 1. It needs no machine. Vector PDFs work as
well as the engine's raster ones. It does not model the firmware's retry after a failed search, nor
the paper's edge.

A 3x3 sheet's marks have their outer edges 3.0 mm from the paper's sides, and the ET-8550 prints
to 2.96 mm from every edge, so the sheet must print at 100 %. The PDF page is landscape like every
sheet's: print it upright (rotated a quarter turn, then portrait at 100 %) and put it on the mat
portrait with the square at the top-left. The square sits 0.6 mm from the top-left card's print,
beside its corner.

A 3x3 sheet goes double-sided as a 4x2 one does: `--backs` prints the whole deck double-sided,
and double-faced cards go to `<deck>-duplex.pdf` (`--split-faces` puts both faces on the main
sheet instead). The engine saves every page landscape, so both sides print like a 4x2 sheet's,
manual duplex with a long-edge flip, and the saved duplex offset moves the back pages the same
way. On the engine's output each back sits behind its front, upright when the card is turned
over side to side (checked at 300 ppi, and by the tests at 50 ppi). The back pages carry no
registration marks (the engine fork's `--no_back_marks`): nothing scans them, and turned over the
long edge a back's square lands right behind the front's top-right L, where on double-sided glossy
it showed through enough to make every TB123 misread that L (2026-10-26). The first 3x3 sheet
printed double-sided (2026-10-26) had its backs slightly off with the offset measured on the
landscape calibration sheet; check against a light before laminating a batch
(`make-proxies --test --backs` numbers each back), and remeasure the offset if it is off.

`--layout 4x2` is the engine's own layout: eight cards on landscape A4, the marks 10 mm in. It is
the default on every other paper and card size, since 3x3 is A4 with standard cards only.
`run.json` records the layout, so `cut-proxies` scans the right frame and cuts the right outlines,
and `redo` counts the run's sheets. `backlog` and `backlog build` count nine to a sheet unless
given `--layout 4x2`.

## Reprints and leftovers

A deck rarely ends clean: a miscut or two, a card that laminated badly, and a last page that
was only half full and not worth a sheet. All of that goes into one queue, `BACKLOG.txt`, in
the directory the deck folders live in (`~/Desktop/proxies`), and gets printed as one well-filled sheet later.

```sh
make-proxies decks/mydeck.txt --defer-partial   # partial last page → BACKLOG.txt, not the PDF
mtg-proxy redo "Sol Ring" p3.5 last             # after cutting the newest run: by name, page.slot, or a whole page
mtg-proxy redo dlast --run mydeck               # d = the duplex PDF (d1, d1.2, dlast); --run names another run
mtg-proxy backlog                               # 11 fronts = 1 full sheet + 2 waiting, 2 duplex
mtg-proxy backlog build                         # backlog-<date>/ next to the decks, a run folder like any deck
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

Stored in `data/offset_data.json`, tracked, applied to every double-sided PDF, 3x3 and 4x2 alike:
both are landscape pages, and the engine shifts the back pages in the page's own pixels.

## Cutting

`cut-proxies` drives the Cameo over Bluetooth LE (`--usb` for the cable) with its own driver,
`src/mtgproxy/cameo/`, and scans the 3-mark registration every sheet is printed with (see
Registration). It follows the sheet's `run.json`, which holds the paper, card size and layout
`make-proxies` printed.

Run bare at a terminal, `cut-proxies` asks first. Enter cuts the default: this folder's
`run.json`, else the newest run in `~/Desktop/proxies`, or a plain A4 sheet (standard cards, the
default layout) when there is none, with the laminate preset over Bluetooth LE. "Choose…" picks
another run, the preset, and
cut, proof first, dry run or probe. `--usb` and `--ble-name` keep the menu; any other flag, or a
pipe, skips it and cuts as told.

```sh
cut-proxies                # menu; Enter cuts the newest run (paper, card size, layout from run.json)
cut-proxies --run mydeck   # a named run in ~/Desktop/proxies, no menu
cut-proxies --preset paper # plain paper instead of laminate
cut-proxies --passes 4     # also --force/--speed/--depth, --x-off/--y-off
cut-proxies --usb          # over the cable instead of Bluetooth LE
cut-proxies --scan         # list the Bluetooth devices in range
cut-proxies --dry-run      # no machine: runs the whole job against a stand-in, checks the bounds
cut-proxies --probe        # scans the marks, logs what the machine reports, cuts nothing
cut-proxies --sense        # scans, then reads each mark back with the sensor (TB63), cuts nothing
cut-proxies --no-check     # skips the sensor check of the marks before and after the scan
cut-proxies --diagnose     # before any scan: where each mark sits and how it reads; cuts nothing
cut-proxies --proof        # traces a printed proof target after the scan; y cuts, r rescans, q stops
cut-proxies --layout 4x2   # a 4x2 sheet without its run.json (a plain sheet is 3x3)
cut-proxies --paper-at 3   # the paper's corner 3 mm right of and below the grid's (see Placement)
cut-proxies --svg job.svg --layout 4x2 --reg-inset 5   # any SVG the size of that layout's page
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
- `proof`: the proof targets, the edge a proof cut traces, and whether one would touch the job.

Registration:

- Every sheet has 3-mark registration: a filled 5 mm square top-left and L marks top-right and
  bottom-left, scanned with `TB123`. `--reg-length` / `--reg-thickness` (mm, default the layout's:
  6 × 1 on the 3x3, 9.4 × 1 on the 4x2) describe the L marks before the scan. Older sheets printed
  with 4-mark registration are refused (their `run.json` says so): rebuild and reprint them.
- `TB123` finds the square by an area search: rows 3 mm apart from the start to 30 mm below it,
  each sweeping 30 mm to its right, for a dark run 3.5–9 mm long. It then measures the square
  down and across and takes its centre less 2.5 mm as the corner, which lands on the L marks'
  centre lines. No bottom-right mark is read, so a stretched sheet's far corner goes unchecked.
- The square must print exactly 5 mm. The card-maker drew it with a 1 mm outline until the
  engine fork's cd4166a; at 6 mm the machine found it, its sideways pass ran past it and the scan
  failed (`-1`) from every start.
- The search starts 2 mm below the square's top edge and 6 mm left of it, or 1 mm from the paper's
  edge: (11, 1) mm on a 3x3 sheet (the square 3.5–8.5 mm across, 9–14 mm down), (12, 4) on a 4x2
  (10–15 mm both ways). The first row runs through the square, and the sweep crosses its left
  edge from white paper. On laminated 3x3 sheets a start 2 mm above the square, near the paper's
  edge, took the firmware's retry and registered off the sheet, while 2 mm into it registered
  first time (2026-10-25). On plain A4, starts on or past the square never found it (`1`). A 4x2
  sheet registered once from (8, 4), 2 mm above its square (2026-10-24); (12, 4) follows the 3x3
  sheets and hasn't been tried. `--scan-start TOP,LEFT` (repeatable) replaces the start with
  exactly those; `--paper-at` moves only the default start (see Placement). Coordinates are from
  the job's own origin, which the job's first command (`TG1`) moves 16.5 mm in once per mat load:
  anything that measures positions on the machine must send it first.
- `--reg-inset MM` puts the expected marks MM from every paper edge instead of the layout's
  (3.5 mm from the sides and 9 mm from the top and bottom on a 3x3 sheet, 10 mm on a 4x2). The
  deck box prints its marks 5 mm in. It must match the printed marks.
- Cuts may reach above or left of the top-left mark, as long as they stay on the paper: the
  cutting area then starts past them (`\\-y,-x`); every other job sends Studio's `\\0,0`.
  First used on the deck box's round 4 (2026-09-28). Keep 6 mm around every mark free of print
  (Graphtec's guidance): ink near a mark can be read as part of it.

The machine reports no mark positions (FQ5 reads -64 after every successful scan, straight or
tilted; its firmware keeps them to itself), so a misread can't be caught from the replies. Its
sensor can be read, though: TB63 reports how much light the spot under the head needs, 255 over
black ink and 151-242 over laminated paper. So every cut reads the marks itself (`cameo/sensing.py`):
the square's middle before the scan, which must be ink or the sheet isn't where its marks should
be, and after it 9 points through the frame the scan made: ink in the middle of the square and on
each L leg's centre line, paper just outside the square. A frame off by about half a millimetre
fails. A leg that never reads full ink still passes when it is a narrow line darker than the paper
on both sides by 40 (a double-sided sheet's leg peaked at 239 over paper at 91-132); the edge of a
shadow is dark on one side only.

A landscape sheet (4x2, the deck box) comes out of the printer portrait with its square top-right,
and goes on the mat landscape with the square top-left. When the square's middle reads no ink on a
landscape sheet, the head reads once more where the square lies on a sheet loaded as printed, and
ink there stops the run with "the sheet lies portrait, as it leaves the printer: turn it a quarter
anticlockwise so the square is top-left, reload and rerun".

Before the scan it also reads blank paper beside each mark: 3 mm above the square, inside each L's
elbow 3 mm from its corner, and the middle of the top margin for comparison. On glossy film the
sensor reads the light the film mirrors back, so it sees the film's angle: where the sheet doesn't
lie flat, blank paper reads 255 like black ink, the scan misreads or finds nothing, and an ink
reading there proves nothing. When paper beside a mark reads like ink, the run stops before
scanning: "sheet not detected as flat (blank paper inside the top-right L: 255, mid-top: 171)".
Unload it and press the sheet down onto the mat on a table. That sheet had been stuck down on a mat lying on
a chair cushion: the waves were too slight to see, and re-laying it on a table fixed it. Paper above 220 there is only a warning: the
scan may misread, and the check after it catches a misread. Sheets that registered read 150-210
beside their marks; a sheet that didn't lie flat along its top edge (2026-10-26) read 255 inside
the top-right L with 171 in the middle of the top margin. The check adds about 50 s a sheet,
and each reading stores a light level in the machine's slot 4, which the scan never uses (it checks that first, and gives the scan its
own light back afterwards: TB63 leaves the light where its ramp stopped, and TB123 starts with the
light as it finds it).

A scan that finds nothing, or one the check disagrees with, is tried again, then once more as
TB122, the firmware's two-mark scan: the square and the bottom-left L only. The check still reads
the top-right L, so a two-mark frame is cut only when that L reads where it puts it. On
2026-10-26 a double-sided sheet whose back showed through behind its top-right L misread on every
TB123 and registered first time as TB122. When all three disagree, nothing is cut, and
`cut-proxies --diagnose` shows why: before any scan it reads across the square and each leg in the
loaded mat's frame (about 4 minutes), and says where the sheet sits, how it is turned and
stretched, which marks never read as full ink or sit on unusually bright paper, and what the blank
paper beside each mark reads, in the words a cut would stop or warn with.

A misread can also be caught by eye. With `--proof` the blade traces the edge of one proof target
after the scan, in one light pass at force 10, speed 5, depth 2 (`--proof-blade FORCE,SPEED,DEPTH`
changes it), then sets the job's own blade again and waits. The targets are 3 mm filled black
squares printed with the marks, inside the mark frame and clear of the marks and the job: four on
every sheet the engine prints (see Sheet layouts), or one `--proof-target X,Y` for each on a sheet
it didn't, such as the paper deck box (their centres in page mm, `--proof-target-size` if they
aren't 3 mm). Lift the lid and look at the target. Registered right, the cut runs along the edge of
the black all round. Registered off, white shows between the black and the cut on one side and the
cut runs inside the black on the other; the width of the white is the error, its side the
direction. `y` cuts the job, `r` goes home and scans again and the next round traces the next
target (up to three rounds), `q` stops with nothing more cut. A target whose outline would touch the
job is left out. Force 10 at depth 2 is a first guess, untried on the machine: meant to score the
laminate's film and stay far from cutting through, and the first proof on a laminated sheet shows
whether its groove is easy to see.

Each target is cut once, so the marks stay as printed and no round cuts on an earlier round's
groove. Until 2026-10-26 the proof cuts went on the marks themselves, at the job's own blade, and
after three rounds on one deck box sheet the grooves on and around the square kept the firmware's
area search from finding it. A retry from the menu after a stop goes on with the next target by
itself; a later run on the same sheet takes `--proof-from N`, N being the targets cut so far, which
a run that stopped after a proof cut prints. A sheet built before the targets existed is refused
with `--proof` (its `run.json` says so): rebuild and reprint it, or cut it without. Every run writes
its own `output/cut/<name>.<date>-<time>.session.jsonl`: each byte sent and received, with
timestamps, plus the Bluetooth movement events.

Placement: sheet top-left on the mat grid, aligned to the paper edge, not the laminate edge.
Stick it down with the mat on a table or another hard, flat surface: on a soft one the mat bends
under your hand and the sheet bonds in waves the sensor reads as ink.
Standard 12x12" mat against the left notch, pinch rollers on the mat, lid closed, room neither
very bright nor very dark. A sheet that creeps on a worn mat skews long jobs: use a sticky mat
or tape the edges. If the scan fails nothing is cut. Glare from glossy laminate is a known cause
of failed scans; glossy sheets cut fine here, but matte is the safer choice if scans fail.

A laminated sheet can also lie with its whole pouch on the mat's tacky area, the pouch's corner on
the grid's: a pouch margin left on the untacky border lifts, and the scan misreads the marks beside
it. The paper then sits a few millimetres right of and below the grid's corner, and
`--paper-at RIGHT,DOWN` says how far, in mm, right first and then down (`--paper-at 3` for 3 mm
both ways). Every reading before the scan moves by that much, `--diagnose`'s included, and so does
the default scan start; a `--scan-start` is used as given. Left at 0 for such a sheet, the
placement reading lands beside the square and the run stops before the scan. After the scan
everything is measured from the top-left mark, so the cut itself is the same. The deck box lays
its sheets this way, with the paper about 3 mm in.

Finish: cut cards can delaminate at the edges. Run them through the laminator again. Cloudy
lamination is too cold (run 80 µm pouches on the 5 mil setting); wavy cards are too hot.

A3 works on the ET-8550 (18 cards a sheet) but needs the 12x24" mat.

## Development

```sh
uv sync
uv run pytest           # ~220 tests, no network, no printer, no Cameo, ~4 s
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
