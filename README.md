# MTG Proxy Factory — ET-8550 + Silhouette Cameo 5 Alpha

Decklist in → print-ready PDF + Silhouette cut file out, built on
[silhouette-card-maker](https://github.com/Alan-Cha/silhouette-card-maker) (cloned in
`silhouette-card-maker/`, venv inside, managed with `uv`). The clone carries local
patches in `plugins/mtg/` (batch + parallel Scryfall fetching) — re-apply them if you
ever pull upstream updates.

Stack: ET-8550 (your own print profile) → 135 gsm glossy photo paper → 80 µm matte
lamination pouches → Cameo 5 Alpha with AutoBlade. Total card thickness ≈ 0.32 mm,
very close to a real MTG card (0.305 mm).

```
make-proxies.sh      decklist → ./<deck>/{<deck>.pdf, <deck>-duplex.pdf?, *.studio3, CUT-NOTES.md}
                     in the CURRENT directory (-o DIR for another parent); decklist paths are cwd-relative too
save-offset.sh       store the printer's duplex offset once, auto-applied afterwards
data/cut_offset.json machine cut offset (mm), baked into every cutting template at build time
data/offset_data.json printer duplex offset (save-offset.sh), applied to every double-sided PDF
templates/           project cutting-template bases — Studio saves rebased offset-free, so they
                     open with the Cameo 5 Alpha profile pre-selected (see §2¾)
tools/               placeholder/test-card + card-back generators, studio3 offset patcher + rebaser
decks/               put your decklists here
(no output/ here)    results live where you ran the command, one folder per deck; a rerun
                     replaces only its own files. Under WSL they are also mirrored FLAT into
                     %USERPROFILE%\Desktop\projects\mtg-proxy (MTG_PROXY_WIN_OUT overrides).
                     `make-proxies --notes [deck]`
                     shows the newest CUT-NOTES.md below the current directory
assets/back.png      default card back for --backs runs — replace with your own any time
silhouette-card-maker/  vendored PDF engine — its own git repo (fork j0nas/silhouette-card-maker,
                     branch local-patches), excluded from this outer repo's git
```

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
./make-proxies.sh <moxfield-url> --board considering  # ...or just its "Considering" board
./make-proxies.sh decks/mydeck.txt -r 3               # 3-mark fallback (see §4)
./make-proxies.sh decks/mydeck.txt --backs            # double-sided (default is fronts only)
./make-proxies.sh decks/mydeck.txt --basics           # include basic lands (skipped by default)
./make-proxies.sh decks/mydeck.txt -- --prefer_extra_art --tokens   # extra fetch.py args
./make-proxies.sh decks/mydeck.txt --skip-fetch       # reuse already-fetched images
```

**Moxfield URLs** work directly as the decklist argument: the deck is pulled from Moxfield's
JSON API (`tools/fetch_moxfield.py`) into `decks/<deckname>.txt` and the run continues from
that file. `--board main|side|considering` picks the board — this exists because Moxfield
itself cannot export (or even copy) the Considering board. Each line carries the exact
printing picked on Moxfield (set + collector number), which the fetch honors over the
default fancy-art preferences. Private decks are not reachable. The saved decklist file can
be rerun offline later like any other.

PDFs are **fronts only by default** — add `--backs` for double-sided output (backs come
from `assets/back.png`). **Basic lands are skipped by default** (`--basics` includes
them). **Double-faced cards** (transform/MDFC) are pulled out of the main PDF into a
separate `<deck>-duplex.pdf` with their real backfaces — print that one with manual
duplex, long-edge flip, and cut it like any other sheet. Fetching resolves the whole
deck in bulk (75 cards per Scryfall
request) and downloads images in parallel; a 100-card deck builds in ~15 s. If Scryfall
ever rate-limits anyway, the fetch backs off automatically — `MTG_FETCH_WORKERS=1`
forces fully serial fetching as a last resort.

Default decklist format is MTG Arena style (`4 Lightning Bolt` / `2 Arid Mesa (MH2) 244`,
optional `Deck`/`Sideboard` headers). The count is optional too — a bare `Lightning Bolt`
line means one copy, so a plain list of card names works as-is. `simple` is bare card
names, one per line.

Every output folder contains a `CUT-NOTES.md` checklist generated for that exact run
(paper size, mark pattern, matching machine profile, cut settings).

## 2½. Invoking it

Run the script from any shell (macOS, Linux or WSL) — the dotfiles define a `make-proxies`
function that finds the clone (`$MTG_PROXY_DIR`, else `~/Desktop/projects/mtg-proxy`, else
`~/projects/mtg-proxy`), and the script resolves its own location, so the working directory
only matters for relative decklist paths:

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
into every cutting template at build time by `tools/offset_studio3.py`; the placed template
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
transplanting the pristine stock geometry back in with `tools/rebase_template.py` — which proves
its work by checking that base + cut offset reproduces the Studio save byte-for-byte. To bake
different Studio state: open the generated `*+y1mm.studio3` in Studio, change *settings only*
(never move shapes), save it over the mirrored copy, then rerun the rebase:

```sh
silhouette-card-maker/venv/bin/python tools/rebase_template.py \
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
3. `./save-offset.sh -x <x> -y <y>` (add `-a <deg>` for rotational error).

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
./cut-proxies.sh                 # A4, standard cards, 4-mark, USB, F25/S25/D5/P3
./cut-proxies.sh --ble           # Bluetooth LE — no cable, no pairing (Mac)
./cut-proxies.sh -r 3            # sheet printed with make-proxies -r 3
./cut-proxies.sh --passes 4      # one more pass; also --force/--speed/--depth
./cut-proxies.sh --dry-run       # no machine: builds output/cut/a4-standard.{svg,cmds}
./cut-proxies.sh --preview       # look at the paths in a window before sending
```

What it does, in order:

1. `tools/cut_svg.py` asks silhouette-card-maker's own `generate_dxf.py` for the card
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

Setup (once): the driver clone lives in `inkscape-silhouette/` (gitignored, like the
card-maker clone) with its own venv; `cut-proxies.sh` prints the exact clone + venv
commands if it is missing. `libusb` comes from Homebrew via the dotfiles.

## Sources

- [silhouette-card-maker docs](https://alan-cha.github.io/silhouette-card-maker/) — tutorial, troubleshooting, SPECIFICATION.md
- [GitHub issue #162](https://github.com/Alan-Cha/silhouette-card-maker/issues/162) — Alpha user's 4-corner registration experience
- [Silhouette School: 3 vs 4 registration marks](https://www.silhouetteschoolblog.com/2026/01/silhouette-registration-mark-types.html)
- [Silhouette School: Cameo 5a firmware/Studio detection bug](https://www.silhouetteschoolblog.com/2025/12/silhouette-cameo-5a-major-bug-warning.html)
- [Silhouette School: registration failures on glossy media](https://www.silhouetteschoolblog.com/2016/10/silhouette-print-and-cut-registration-failed.html)
- [@kgclippy's Cameo 5a MTG-proxy settings](https://www.tiktok.com/@kgclippy/video/7603979095810084127) (ET-8550 + laminated photo paper + matte laminate: F30/S25/D7/P3)
