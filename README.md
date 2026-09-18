# MTG Proxy Factory — ET-8550 + Silhouette Cameo 5 Alpha

Decklist in → print-ready PDF + Silhouette cut file out. A Python package (`mtgproxy`,
CLI `mtg-proxy`, managed with `uv`) that drives the vendored
[silhouette-card-maker](https://github.com/Alan-Cha/silhouette-card-maker) engine in-process
(cloned in `silhouette-card-maker/` from the fork `j0nas/silhouette-card-maker`, branch
`local-patches`: batch + parallel Scryfall fetching, `--token_copies`, MTGA parser fixes).
`./setup.sh` makes a fresh clone runnable (§0).

Stack: ET-8550 (your own print profile) → 135 gsm glossy photo paper → 80 µm matte
lamination pouches → Cameo 5 Alpha with AutoBlade. Total card thickness ≈ 0.32 mm,
very close to a real MTG card (0.305 mm).

```
mtg-proxy make       decklist → ./<deck>/{<deck>.pdf, <deck>-duplex.pdf?, *.studio3, CUT-NOTES.md, run.json}
  (make-proxies.sh)  in the CURRENT directory (-o DIR for another parent); decklist paths are cwd-relative too.
                     A deck fetched from a URL also lands there as <deck>.txt.
mtg-proxy cut        cut a printed sheet on the Cameo directly (no Studio); run it inside the deck
  (cut-proxies.sh)   folder and it reads run.json for paper / card size / registration pattern
mtg-proxy offset     store the printer's duplex offset once, auto-applied afterwards
  (save-offset.sh)
mtg-proxy notes      show the newest CUT-NOTES.md below the current directory (or a named run)
mtg-proxy cache      Scryfall image cache stats / --clear (~/.cache/mtg-proxy, MTG_PROXY_CACHE overrides)
mtg-proxy doctor     check engine, cutter driver, data files
mtg-proxy rebase-template / make-back   template surgery (§2¾) / regenerate assets/back.png
src/mtgproxy/        the package (engine.py = in-process engine access, build.py = the pipeline,
                     decks.py = Moxfield/Archidekt, layout.py = cut SVG, studio3.py = template patcher)
tests/               pytest suite (no network, no printer); ruff + pytest run in the pre-commit hook
data/cut_offset.json machine cut offset (mm), baked into every cutting template at build time
data/offset_data.json printer duplex offset (mtg-proxy offset), applied to every double-sided PDF
templates/           project cutting-template bases — Studio saves rebased offset-free, so they
                     open with the Cameo 5 Alpha profile pre-selected (see §2¾)
decks/               your decklists (gitignored)
(no output/ here)    results live where you ran the command, one folder per deck; a rerun
                     replaces only its own files. Under WSL they are also mirrored FLAT into
                     %USERPROFILE%\Desktop\projects\mtg-proxy (MTG_PROXY_WIN_OUT overrides).
assets/back.png      default card back for --backs runs — replace with your own any time
silhouette-card-maker/  vendored PDF engine — its own git repo, gitignored here (setup.sh clones it)
inkscape-silhouette/    vendored cutter driver with its own venv — same deal
```

The `*.sh` scripts are three-line shims onto the CLI (kept for the dotfiles' `make-proxies` /
`cut-proxies` functions); `make-proxies --test`, `make-proxies --notes`, `cut-proxies --dry-run`
etc. all still work. `uv run mtg-proxy ...` is the same thing from inside the repo.

## 0. Setup (fresh clone)

```sh
./setup.sh              # clones the engine fork + cutter driver, builds both venvs, installs git hooks
./setup.sh --no-cutter  # print-only machine: skip inkscape-silhouette
mtg-proxy doctor        # (uv run mtg-proxy doctor) — what's missing and how to fix it
```

Needs `uv` and `git` on PATH; USB cutting on the Mac additionally needs `libusb` (Homebrew,
managed in the dotfiles). Everything is idempotent — rerun `setup.sh` after pulling.

## 1. Verify the workflow first (no wasted ink)

```sh
./make-proxies.sh --test            # build ./test-sheet/test-sheet.pdf (in your cwd)
./make-proxies.sh --test --print    # ...and send it to the default CUPS printer at 100%
```

`--print` goes through CUPS with scaling forced off (media A4/letter to match the PDF;
plain paper from the main tray for the test sheet, otherwise the **4x2 Glossy** preset
translated from the Windows driver — rear feeder, glossy photo, High quality; see
`docs/printer-presets/README.md` for all five presets and the CUPS mapping;
`MTG_PROXY_LP_OPTS` adds lp options). On the Mac the ET-8550 is the default printer, so
no name is needed.

`./test-sheet/` (and the Windows mirror) gets one A4 sheet of 8 **placeholder gauge
cards** — exact 63×88 mm standard-MTG geometry, hairline art only, near-zero ink:

- **frame** sits exactly 1.0 mm inside the cut line → after cutting, the white margin
  around the frame must be a uniform 1.0 mm on all four sides of every card
- **gray ticks** at 0.5 mm and 1.5 mm insets → read any cut offset in 0.5 mm steps
- **gray corner arcs** = the theoretical 3 mm-radius corner cut path
- **center crosshair on front and back** → hold a cut card against the light; the
  shadows coincide when duplex alignment is right (only relevant for `--backs` runs)
- **▲ TOP** marker catches rotation/flip mistakes

Print it with your usual profile (**actual size / 100%, borderless OFF** — scaling moves
the registration marks and registration fails), manual duplex with **long-edge flip**,
laminate, cut. If the gauges read clean, switch to real cards.

## 2. Real decks

```sh
./make-proxies.sh decks/mydeck.txt                    # MTGA format, A4, 4-mark registration
./make-proxies.sh decks/mydeck.txt -f moxfield        # also: mtgo, archidekt, deckstats, simple, ...
./make-proxies.sh https://moxfield.com/decks/<id>     # fetch straight from Moxfield
./make-proxies.sh https://archidekt.com/decks/<id>    # ...or from Archidekt
./make-proxies.sh <deck-url> --board considering      # ...or just its "Considering"/Maybeboard
./make-proxies.sh decks/mydeck.txt -r 3               # 3-mark fallback (see §4)
./make-proxies.sh decks/mydeck.txt --backs            # double-sided (default is fronts only)
./make-proxies.sh decks/mydeck.txt --basics           # include basic lands (skipped by default)
./make-proxies.sh decks/mydeck.txt -t 2               # plus 2 of each distinct token, grouped at the end of the sheets
./make-proxies.sh decks/mydeck.txt -- --prefer_set sld  # extra fetch.py args after --
./make-proxies.sh decks/mydeck.txt --skip-fetch       # reuse already-fetched images
./make-proxies.sh decks/mydeck.txt --dry-run          # resolve the decklist + output folder, stop
./make-proxies.sh decks/mydeck.txt --print --printer X  # CUPS destination other than the 4x2-glossy instance
./make-proxies.sh decks/mydeck.txt --trim "Temple Garden" --trim "Battle Angels of Tyr:0.5"   # see below
```

**Moxfield URLs** work directly as the decklist argument: the deck is pulled from Moxfield's
JSON API (`mtgproxy/decks.py`) into the output folder as `<deckname>/<deckname>.txt` and the
run continues from that file. `--board main|side|considering` picks the board — this exists because Moxfield
itself cannot export (or even copy) the Considering board. Each line carries the exact
printing picked on Moxfield (set + collector number), which the fetch honors over the
default fancy-art preferences. Private decks are not reachable. The saved decklist file can
be rerun offline later like any other.

**Archidekt URLs** work the same way (Archidekt's public `/api/decks/<id>/` JSON). Archidekt has categories instead of boards: a card is in the deck
when its primary category is flagged "included in deck", and the stock excluded categories
are Sideboard and Maybeboard — so `--board main` is everything counted in the deck
(commander included), `--board side` the Sideboard category and `--board considering` the
Maybeboard. Chosen printings are preserved here too.

PDFs are **fronts only by default** — add `--backs` for double-sided output (backs come
from `assets/back.png`). **Basic lands are skipped by default** (`--basics` includes
them). **Double-faced cards** (transform/MDFC) are pulled out of the main PDF into a
separate `<deck>-duplex.pdf` with their real backfaces — print that one with manual
duplex, long-edge flip, and cut it like any other sheet. Fetching resolves the whole
deck in bulk (75 cards per Scryfall
request) and downloads images in parallel; a 100-card deck builds in ~15 s the first time.
**Card images are cached** across decks and runs in `~/.cache/mtg-proxy` (keyed by the exact
Scryfall image URL, so a printing never goes stale; `mtg-proxy cache` shows the size,
`--clear` empties it, `--no-cache` bypasses it for one run) — reruns and overlapping decks
take a couple of seconds. Card *lookups* always hit Scryfall, so a list without printings
still resolves to current art. If Scryfall ever rate-limits anyway, the fetch backs off
automatically — `MTG_FETCH_WORKERS=1` forces fully serial fetching as a last resort.

**Contrasting bleed on borderless cards** (`--trim`): the engine builds the 1.25 mm print bleed
by smearing each image's outermost pixel row outward. Every Scryfall scan has a thin rim there;
on a black-bordered card it's black on black, but on borderless / extended-art printings the rim
is a dark line against art and the bleed shows as a band (the corners are fine — they're filled
from inside the art). `--trim "Card Name"` replaces that card's outer 0.3 mm ring with the ring
just inside before the bleed is built; `--trim "Card Name:0.5"` sets the width, `--trim all` does
every card, and the flag repeats. Trims are recorded in `run.json` and applied once even across
`--skip-fetch` reruns. To find candidates, look for borderless printings in the decklist.

Default decklist format is MTG Arena style (`4 Lightning Bolt` / `2 Arid Mesa (MH2) 244`,
optional `Deck`/`Sideboard` headers). The count is optional too — a bare `Lightning Bolt`
line means one copy, so a plain list of card names works as-is. `simple` is bare card
names, one per line.

Every output folder contains a `CUT-NOTES.md` checklist generated for that exact run
(paper size, mark pattern, matching machine profile, cut settings) and a `run.json` sidecar
with the same facts in machine form — `cut-proxies` reads it, so the registration pattern
you printed is the one the cutter scans for, without retyping `-r`.

## 2½. Invoking it

Run it from any shell (macOS, Linux or WSL) — the dotfiles define `make-proxies` /
`cut-proxies` functions that find the clone (`$MTG_PROXY_DIR`, else
`~/Desktop/projects/mtg-proxy`, else `~/projects/mtg-proxy`) and call the shims, which `uv run`
the CLI from the repo's own venv (syncing it first if needed). The working directory only
matters for relative decklist paths and for where the output folder goes:

```sh
make-proxies --test
make-proxies decks/mydeck.txt -r 3                       # relative to your cwd
make-proxies /mnt/c/Users/<you>/Downloads/mydeck.txt     # WSL
make-proxies 'C:\Users\<you>\Downloads\mydeck.txt'       # C:\ paths converted via wslpath
```

Under WSL the clone's `decks/` folder is reachable from Explorer as
`\\wsl.localhost\<distro>\<clone path>\decks` — save new decklists there. Output PDFs/cut
files are also mirrored into the Windows folder (see above) for printing. From
PowerShell/cmd, the equivalent one-liner is `wsl <clone path>/make-proxies.sh <args>`.

## 2¾. Machine cut offset (`data/cut_offset.json`)

The Cameo 5 Alpha cuts ~1mm high relative to the registration marks it scans — a machine
bias, independent of paper size or layout. The compensation lives as one number
in `data/cut_offset.json` (currently `y_mm: 1.0`, positive = shift cuts down) and is baked
into every cutting template at build time by `mtgproxy/studio3.py`; the placed template
carries it in its name (`a4-standard-v5+y1mm.studio3`). **Never nudge shapes in Silhouette
Studio** — hand-edited templates get overwritten by the next run's mirror. If alignment
drifts (new blade, new machine), cut one sheet, measure the vertical error, update the
number, rerun. (The value was calibrated 2026-08 by hand-nudging a template in Studio until
cuts landed perfectly; the patcher was validated bit-exact against that template.)

**The machine profile, material, and media are baked into the template base**
(`templates/a4-standard-v5-alpha.studio3`): Studio opens the generated cut file with **Cameo 5
Alpha** pre-selected (instead of the upstream default, plain Cameo 5), the saved material
selection loaded, and media set to **A4**. The base was made by saving a generated template from
Studio with those settings switched (machine + material 2026-08, A4 media 2026-09), then
transplanting the pristine stock geometry back in with `mtg-proxy rebase-template` — which proves
its work by checking that base + cut offset reproduces the Studio save byte-for-byte (the test
suite re-proves the shipped base against the stock template on every run). To bake different
Studio state: open the generated `*+y1mm.studio3` in Studio, change *settings only* (never move
shapes), save it over the mirrored copy, then rerun the rebase:

```sh
uv run mtg-proxy rebase-template \
  '<windows mirror folder>/a4-standard-v5-alpha+y1mm.studio3' \
  silhouette-card-maker/cutting_templates/a4-standard-v5.studio3 \
  templates/a4-standard-v5-alpha.studio3
```

On media: the type **must be the A4 preset, not "Custom"**. The upstream template ships with
typed-in dimensions of 296.93 × 210.06 mm — true A4 within 0.07 mm, so it looked like a purely
cosmetic label — but with Custom media the Alpha produced **warped cuts** even on the 4x2
layout; switching Page Setup's media to A4 fixed it (found 2026-09: the machine evidently
handles a preset differently from dimensionally identical custom media). The A4 setting is
baked into the base now; if Page Setup ever shows "Custom" again, re-bake with the recipe above.

## 3. Duplex offset calibration (once, for `--backs` decks and `<deck>-duplex.pdf` sheets)

1. Print `silhouette-card-maker/calibration/a4-calibration.pdf` exactly like a card
   sheet (100 %, manual duplex, long-edge flip).
2. Against a strong light, find the front/back square pair that lines up; read its red
   `(x, y)` label. Units are 300 PPI pixels, ≈ 0.085 mm each.
3. `./save-offset.sh -x <x> -y <y>` (= `mtg-proxy offset`; add `-a <deg>` for rotational error).

The offset lands in `data/offset_data.json` (tracked — commit it, and it follows the
printer to every clone) and every future double-sided PDF gets it automatically.

## 4. Cameo 5 Alpha — cutting cheat sheet

**Software**: Silhouette Studio **v5.0.402+** required for the Alpha; the free "Starter"
edition of Studio v5 is incompatible. Firmware updates only ship through Studio, and the
first-run firmware update is mandatory before it will cut at all.

**The one setting that matters — machine profile must match the PDF's marks:**

| PDF generated with | Studio machine profile |
|---|---|
| `-r 4` (default here) — L-marks in all 4 corners | **Cameo 5 Alpha** |
| `-r 3` — square + 2 L-marks | **Cameo 5** (yes, the old profile, even on Alpha hardware) |

The A4 template base opens with Cameo 5 Alpha and A4 media pre-selected (§2¾) — just verify
them. For `-r 3` runs or fallback templates, set both manually in Page Setup (media must be
A4, not "Custom" — Custom warps the cuts). A documented bug (Alpha
firmware 1.05 + Studio ≥ 5.0.402) makes Studio auto-detect the Alpha as a plain Cameo 5,
silently breaking Print & Cut — the layouts are identical between patterns, so the same
`.studio3` template serves both. Print & Cut is already enabled inside the bundled
templates; nothing to toggle.

**Registration, 4-mark pattern**: an Alpha user cutting this exact card layout reported it
needs light-colored Post-its covering the cards nearest **both bottom corners** during the
mark scan (remove after registration, before cutting). Community consensus (project docs +
Silhouette School) is that 4-mark is currently *fussier* than 3-mark and the accuracy gain
is minor — if it keeps failing, `-r 3` + machine profile "Cameo 5" is the proven path.
Your **matte** laminate is an advantage either way: glare from glossy surfaces is a known
cause of mark-scan failures.

**Placement**: sheet in the top-left of the mat grid, aligned to the *paper* edge (not the
laminate edge); standard adhesive 12×12 mat; left mat edge against the machine notch;
pinch rollers on the mat. Avoid very bright or very dark rooms for the scan; keep the lid
closed.

**Cut settings** (AutoBlade — not the Kraft blade; it can't turn the 3 mm corner radii):

| | Force | Speed | Depth | Passes |
|---|---|---|---|---|
| **Our default** (laminated 135 gsm photo paper, `cut-proxies` defaults) | 25 | 25 | 5 | 3 |
| @kgclippy's Cameo 5a reference (same stack class) | 30 | 25 | 7 | 3 |
| Author's heavier reference (250 gsm + 3 mil, Cameo 5) | 35 | 25 | 7 | 4 |

Tune in this order: **passes → force → speed → depth**. Test on one sheet: run the job,
*don't eject*, lift a corner to check, re-run the job to add a pass if needed.
Rippled/torn edges = force too high or blade dull. Max force/speed makes the machine skip
(power-cycle to rehome if it does).

**Finish**: cut cards can delaminate at the edges — run them through the laminator once
more. Laminator note: run pouches one heat grade above their rating if lamination is
cloudy (e.g. 80 µm ≈ 3 mil pouches on a 5 mil setting); wavy cards = too hot.

**A3**: the ET-8550 can print it (18 cards/sheet) but it needs the 12×24″ mat — the
standard 12×12″ mat is too short.

## 5. Cutting without Silhouette Studio (`cut-proxies.sh`)

Studio on the Alpha (firmware 1.05, Studio ≥ 5.0.402) mis-identifies the machine and
picks the registration scan on its own, which is what turns a working 4x2 sheet into a
morning of failed registrations. `cut-proxies.sh` skips Studio entirely: it drives the
Cameo over USB or Bluetooth LE with [inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette)
(pure-Python libusb driver, Cameo 5 Alpha support since its PR #348), and it chooses the
scan command **explicitly** — `-r 4` sends the four-L-mark scan (`TB124`), `-r 3` the
square + two-L scan (`TB123`). The Alpha accepts both; Studio only ever lets it do one.

```sh
cd mydeck && cut-proxies        # reads ./run.json: paper, card size, 3- or 4-mark — as printed
cut-proxies --run mydeck        # same, from the parent folder
cut-proxies --ble               # Bluetooth LE — no cable, no pairing (Mac)
cut-proxies -r 3                # no run.json (or override it): sheet printed with -r 3
cut-proxies --passes 4          # one more pass; also --force/--speed/--depth
cut-proxies --dry-run           # no machine: builds output/cut/<name>.{svg,cmds}, checks the scan cmd
cut-proxies --preview           # look at the paths in a window before sending
```

Without a `run.json` in reach it falls back to flags/defaults (A4, standard, 4-mark) and says so.

What it does, in order:

1. `mtgproxy/layout.py` asks silhouette-card-maker's own `generate_dxf.py` for the card
   outlines of the paper/card combo (the same layout engine that placed the cards in the
   PDF) and converts them to a page-sized SVG in mm, rounded corners as real arcs.
   Registration-mark geometry (10 mm inset, 277×190 mm apart on A4 landscape) comes
   from the same `assets/layouts.json` the PDF used. The marks themselves are not
   drawn — the machine scans the printed ones.
2. The machine cut bias from `data/cut_offset.json` is applied as an X/Y offset
   (override with `--x-off`/`--y-off`, `--y-off 0` to disable). It was measured through
   Studio, so verify it once with a `make-proxies --test` gauge sheet on this path.
3. `sendto_silhouette.py` is invoked with force/speed/depth/passes, the regmark scan,
   and the offsets; the run's log and the raw command transcript land in `output/cut/`.

Sheet placement is unchanged: top-left of the mat grid, aligned to the paper edge, mat
against the left notch, lid closed. If the scan fails the job stops with
"Couldn't find registration marks" and nothing is cut.

**Where to run it**: on the Mac. inkscape-silhouette talks libusb; on Windows that means
replacing Silhouette's USB driver with WinUSB (Zadig), which breaks Studio's connection.
The Mac needs no driver dance — plug the USB cable in, or use `--ble`
(`--scan` lists nearby BLE devices if the default name "CAMEO 5 ALPHA" doesn't match).

Setup (once): `./setup.sh` clones the driver into `inkscape-silhouette/` (gitignored, like the
card-maker clone) and builds its own venv; `cut-proxies` prints the exact commands if it is
missing. `libusb` comes from Homebrew via the dotfiles.

## 6. Development

```sh
uv sync                 # venv with the engine's pinned deps + dev tools (ruff, pytest)
uv run pytest           # ~50 unit tests: deck parsing (recorded API fixtures), template patcher
                        # (bit-exact against the stock template), cut geometry, notes/sidecar/mirror/
                        # print-job builders, CLI dry runs — no network, no printer, ~1 s
uv run ruff check src tests && uv run ruff format src tests
```

`setup.sh` points `core.hooksPath` at `.githooks/`, whose pre-commit runs ruff + pytest;
`git commit --no-verify` skips it once. Dependencies are pinned to the engine's
`requirements.txt` versions in `pyproject.toml` and locked in `uv.lock`; bump both together
when the fork updates. The engine is imported in-process (`mtgproxy/engine.py` puts its root
on `sys.path` and runs its click commands with `standalone_mode=False` under `chdir`), so its
`game/` working dirs and `data/offset_data.json` stay where the engine expects them.

## Sources

- [silhouette-card-maker docs](https://alan-cha.github.io/silhouette-card-maker/) — tutorial, troubleshooting, SPECIFICATION.md
- [GitHub issue #162](https://github.com/Alan-Cha/silhouette-card-maker/issues/162) — Alpha user's 4-corner registration experience
- [Silhouette School: 3 vs 4 registration marks](https://www.silhouetteschoolblog.com/2026/01/silhouette-registration-mark-types.html)
- [Silhouette School: Cameo 5a firmware/Studio detection bug](https://www.silhouetteschoolblog.com/2025/12/silhouette-cameo-5a-major-bug-warning.html)
- [Silhouette School: registration failures on glossy media](https://www.silhouetteschoolblog.com/2016/10/silhouette-print-and-cut-registration-failed.html)
- [@kgclippy's Cameo 5a MTG-proxy settings](https://www.tiktok.com/@kgclippy/video/7603979095810084127) (ET-8550 + laminated photo paper + matte laminate: F30/S25/D7/P3)
