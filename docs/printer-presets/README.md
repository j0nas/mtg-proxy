# ET-8550 printer presets (pulled from PomeloMadness, 2026-09-03)

The five custom presets saved in the Windows Epson driver on PomeloMadness, read straight
off the driver's Printing Preferences dialog (screenshots alongside: `<preset>-main.png`,
`<preset>-more-options.png`). The driver stores them as an opaque blob
(`C:\ProgramData\Epson\EPSON ET-8550 Series\E_TCXYCE.UCB`), so the dialog is the source
of truth.

Common to all five: Paper Source **Rear Paper Feeder**, Document Size **A4**, Color,
Borderless **off**, 2-Sided off, Multi-Page off, Copies 1, Collate on, Reverse Order on,
Quiet Mode on, Output Paper = Same as Document Size, no Reduce/Enlarge, Rotate 180 /
Bidirectional Printing / Mirror Image all **off** (unchecked Bidirectional = slower,
cleaner unidirectional passes).

| Preset | Orientation | Paper Type (Windows) | Quality | Color Correction |
|---|---|---|---|---|
| **4x2 Glossy** (the proxy sheet profile) | Landscape | Photo Paper Glossy | High | Automatic |
| Matte A4 Proxy | Portrait | Epson Matte | High | Automatic |
| Holographic A4 Proxy | Portrait | Epson Ultra Glossy | Best | Automatic |
| Photo Quality Ink Jet | Portrait | Epson Photo Quality Ink Jet | High | Automatic |
| Holo Proxy with CC | Portrait | Epson Ultra Glossy | Best | Custom (Adobe RGB, "Thick paper") |

"Holo Proxy with CC" has more under its Advanced… color dialog and the Maintenance tab
(thick-paper mode) that the screenshots don't show; the panel text lists "Thick paper"
and "Adobe RGB".

## The same thing on the Mac (CUPS / IPP Everywhere driver)

The Mac talks to the ET-8550 through CUPS' generic IPP driver, so the option names differ
and a few Windows-only toggles (Quiet Mode, Bidirectional) have no equivalent. Mapping
for **4x2 Glossy**:

| Windows | CUPS option |
|---|---|
| Rear Paper Feeder | `-o InputSlot=rear` |
| A4 | `-o media=A4` |
| Landscape | comes from the PDF page itself |
| Photo Paper Glossy | `-o MediaType=photographic-glossy` |
| Color | `-o ColorModel=RGB` |
| Quality High | `-o cupsPrintQuality=High` |
| Reverse Order | `-o outputorder=reverse` (irrelevant for one sheet) |
| no scaling (not a preset field, but essential for registration) | `-o print-scaling=none -o fit-to-page=false` |

Other paper types on the Mac side: Epson Ultra Glossy → `photographic-high-gloss`, Epson
Matte → `photographic-matte`, Photo Quality Ink Jet → `photographic` (nearest), plain →
`stationery`. Thick paper → `com.epson-thickpaper1`/`2`.

`make-proxies --print` uses exactly the 4x2 Glossy mapping for real sheets, and plain
paper from the main tray for the `--test` sheet. Full list of what the Mac driver offers:
`lpoptions -p EPSON_ET_8550_Series -l`.
