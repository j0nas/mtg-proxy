# mtg-proxy

Decklist in, print-ready PDF and Silhouette cut file out. A small Python CLI (`mtg-proxy`,
managed with `uv`) that drives [silhouette-card-maker](https://github.com/Alan-Cha/silhouette-card-maker)
in-process, from my fork `j0nas/silhouette-card-maker` (branch `local-patches`: batched and
parallel Scryfall fetching, `--token_copies`, MTGA parser fixes).

Built for one setup: Epson ET-8550, 135 gsm glossy photo paper, 80 µm laminate, Silhouette
Cameo 5 Alpha with the AutoBlade. Finished card is ~0.32 mm; a real one is 0.305 mm. What the
cards cost and why the stack looks like this is written up at
[jona.no/docs/mtg-proxying](https://jona.no/docs/mtg-proxying).

```
mtg-proxy make      decklist -> ./<deck>/{<deck>.pdf, <deck>-duplex.pdf?, *.studio3, CUT-NOTES.md, run.json}
mtg-proxy cut       cut a printed sheet on the Cameo, no Silhouette Studio; reads run.json from the deck folder
mtg-proxy offset    store the printer's duplex offset once
mtg-proxy notes     show the newest CUT-NOTES.md below the cwd
mtg-proxy cache     Scryfall image cache stats / --clear (~/.cache/mtg-proxy)
mtg-proxy doctor    check engine, cutter driver, data files
```

`make-proxies.sh`, `cut-proxies.sh` and `save-offset.sh` are shims onto those commands. Output
lands in the current directory, one folder per deck. Under WSL it is also mirrored flat into
`%USERPROFILE%\Desktop\projects\mtg-proxy` (`MTG_PROXY_WIN_OUT` overrides).

```
src/mtgproxy/          engine.py (in-process engine), build.py (pipeline), decks.py (Moxfield/Archidekt),
                       layout.py (cut SVG), studio3.py (template patcher)
data/cut_offset.json   machine cut bias in mm, baked into every cutting template
data/offset_data.json  printer duplex offset, applied to every double-sided PDF
templates/             cutting-template base with the Cameo 5 Alpha profile and A4 media pre-selected
assets/back.png        default card back for --backs; replace with your own
silhouette-card-maker/ and inkscape-silhouette/   vendored clones, gitignored, made by setup.sh
```

## Setup

```sh
./setup.sh              # clones the engine fork and cutter driver, builds both venvs, installs git hooks
./setup.sh --no-cutter  # print-only machine
mtg-proxy doctor        # what's missing and how to fix it
```

Needs `uv` and `git`. USB cutting on a Mac also needs `libusb` from Homebrew. Rerun `setup.sh`
after pulling; it's idempotent.

## Test sheet first

```sh
make-proxies --test            # ./test-sheet/test-sheet.pdf
make-proxies --test --print    # and send it to the default CUPS printer at 100 %
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
make-proxies decks/mydeck.txt --backs            # double-sided; default is fronts only
make-proxies decks/mydeck.txt --basics           # include basic lands; skipped by default
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
so the registration pattern you printed is the one it scans for.

The dotfiles' `make-proxies` and `cut-proxies` functions find the clone (`$MTG_PROXY_DIR`, then
`~/Desktop/projects/mtg-proxy`) and run the shims from any shell, including WSL with Windows
paths.

## Cut offset

The Cameo 5 Alpha cuts ~1 mm high relative to the marks it scans, regardless of layout.
`data/cut_offset.json` holds the correction (`y_mm: 1.0`, positive shifts cuts down) and
`studio3.py` bakes it into every generated template, named like `a4-standard-v5+y1mm.studio3`.
Don't nudge shapes in Studio; the next run overwrites the file. If alignment drifts, cut one
sheet, measure, update the number.

The template base `templates/a4-standard-v5-alpha.studio3` also carries the machine profile
(Cameo 5 Alpha), the material, and the media, which must be the A4 preset, not Custom. The
upstream template ships with typed-in dimensions 0.07 mm off true A4; that looked cosmetic, but
Custom media gave warped cuts on the Alpha. To bake other Studio settings, open a generated
template in Studio, change settings only, save it, and run:

```sh
uv run mtg-proxy rebase-template <saved.studio3> \
  silhouette-card-maker/cutting_templates/a4-standard-v5.studio3 \
  templates/a4-standard-v5-alpha.studio3
```

It checks that base plus offset reproduces the Studio save byte for byte, and the test suite
re-checks the shipped base on every run.

## Duplex offset

Once, for `--backs` decks and duplex sheets:

1. Print `silhouette-card-maker/calibration/a4-calibration.pdf` like a card sheet.
2. Against a light, find the front/back square pair that lines up and read its `(x, y)`.
   Units are 300 PPI pixels, ~0.085 mm.
3. `mtg-proxy offset -x <x> -y <y>` (`-a <deg>` for rotation).

Stored in `data/offset_data.json`, tracked, applied to every double-sided PDF.

## Cutting

`cut-proxies` skips Silhouette Studio. Studio with Alpha firmware 1.05 mis-detects the machine
as a plain Cameo 5 and picks the registration scan on its own, which is how a working sheet
turns into a morning of failed scans. This drives the Cameo over USB or Bluetooth LE with
[inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette) and sends the scan
command explicitly: `-r 4` is the four-L-mark scan, `-r 3` the square plus two L's.

```sh
cd mydeck && cut-proxies   # reads run.json: paper, card size, mark pattern
cut-proxies --run mydeck   # same, from the parent folder
cut-proxies --ble          # Bluetooth, no cable
cut-proxies --passes 4     # also --force/--speed/--depth, --x-off/--y-off
cut-proxies --dry-run      # builds output/cut/<name>.{svg,cmds}, no machine
cut-proxies --preview
```

Run it on the Mac. On Windows, libusb means replacing Silhouette's driver with WinUSB, which
breaks Studio.

Placement: sheet top-left on the mat grid, aligned to the paper edge, not the laminate edge.
Standard 12x12" mat against the left notch, pinch rollers on the mat, lid closed, room neither
very bright nor very dark. If the scan fails nothing is cut.

Settings, AutoBlade only (the Kraft blade can't turn the 3 mm corners):

| | Force | Speed | Depth | Passes |
|---|---|---|---|---|
| Default here (laminated 135 gsm photo paper) | 25 | 25 | 5 | 3 |
| @kgclippy, same stack on a Cameo 5a | 30 | 25 | 7 | 3 |
| Upstream author, 250 gsm + 3 mil on a Cameo 5 | 35 | 25 | 7 | 4 |

Tune passes, then force, then speed, then depth. Run one sheet, don't eject, lift a corner, add a
pass if needed. Rippled edges mean too much force or a dull blade. Max force or speed makes the
machine skip; power-cycle to rehome.

If you do use Studio: v5.0.402 or newer, not the free Starter edition. Machine profile must
match the marks: `-r 4` wants "Cameo 5 Alpha", `-r 3` wants plain "Cameo 5" even on Alpha
hardware. 4-mark is fussier than 3-mark; one Alpha user needed light Post-its over the two
bottom-corner cards during the scan. Glare from glossy laminate is a known cause of failed
scans; I cut glossy-laminated sheets fine with `cut-proxies`, but matte is the safer choice if
registration keeps failing.

Finish: cut cards can delaminate at the edges. Run them through the laminator again. Cloudy
lamination is too cold (run 80 µm pouches on the 5 mil setting); wavy cards are too hot.

A3 works on the ET-8550 (18 cards a sheet) but needs the 12x24" mat.

## Development

```sh
uv sync
uv run pytest           # ~50 tests, no network, no printer, ~1 s
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
- [Silhouette School: Cameo 5a firmware/Studio detection bug](https://www.silhouetteschoolblog.com/2025/12/silhouette-cameo-5a-major-bug-warning.html)
- [Silhouette School: registration failures on glossy media](https://www.silhouetteschoolblog.com/2016/10/silhouette-print-and-cut-registration-failed.html)
- [@kgclippy's Cameo 5a settings](https://www.tiktok.com/@kgclippy/video/7603979095810084127)
