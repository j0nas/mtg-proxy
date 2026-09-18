# ET-8550 printer presets (read off the Windows driver, 2026-09-03)

The five custom presets saved in the Windows Epson driver, read straight
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

## The same thing on the Mac (Epson driver queue)

Two ways the Mac can talk to the ET-8550, with very different option sets:

- **AirPrint / IPP Everywhere** (what "Add Printer" picks by default): generic IPP options only
  (`media`, `InputSlot`, `MediaType=photographic-glossy`, `cupsPrintQuality`, `ColorModel`). No
  Quiet Mode, no Bidirectional, no Best Quality.
- **Epson driver** (Add Printer → "Use: EPSON ET-8550 Series"; installed under
  `/Library/Printers/EPSON`): the full driver with Epson's own option codes. Since 2026-09 the
  Mac queue is this one, named `EPSON_ET_8550_Series_2` (the AirPrint queue was replaced).

Mapping for **4x2 Glossy** on the Epson-driver queue (codes from the Epson PPD,
`lpoptions -p EPSON_ET_8550_Series_2 -l` lists them all, `gzcat "/Library/Printers/PPDs/Contents/Resources/EPSON ET-8550 Series.gz" | grep EPIJ_Medi` shows the labels):

| Windows | Epson PPD option |
|---|---|
| A4 | `-o EPIJ_Size=1` |
| Rear Paper Feeder | `-o EPIJ_FdSo=0` (11 = Auto Select, 2/3 = cassettes) |
| Photo Paper Glossy | `-o EPIJ_Medi=145` (92 Ultra Glossy, 13 Premium Glossy, 12 Matte, 2 Photo Quality Ink Jet, 0 plain, 159/160 thick) |
| Quality High | `-o EPIJ_Qual=306` (307 = Best Quality, 303 = Normal) |
| Bidirectional off | `-o EPIJ_OPT_Bi_D=0` |
| Quiet Mode on | `-o EPIJ_Silt=1` |
| Landscape | comes from the PDF page itself |
| no scaling (essential for registration) | `-o print-scaling=none -o fit-to-page=false` |

"Emphasize Text" / "Emphasize Thin Lines" (Windows: More Options → Image Options…) do not exist
in the Mac driver at all; the nearest controls are Print Quality and Sharpen. Irrelevant for the
proxy sheets — they are flattened rasters with solid registration marks.

On the Mac this mapping exists as the CUPS printer instance `EPSON_ET_8550_Series_2/4x2-glossy`
(`~/.cups/lpoptions`, chezmoi-managed): `lp -d EPSON_ET_8550_Series_2/4x2-glossy file.pdf` from
any tool. `make-proxies --print` finds whichever live queue carries a `/4x2-glossy` instance
(`MTG_PROXY_LP_INSTANCE` overrides) and uses plain-paper defaults for the `--test` sheet; if no
instance exists it falls back to the AirPrint-style explicit options above, which only make sense
on an AirPrint queue. Recreate the instance by hand with

```sh
lpoptions -p EPSON_ET_8550_Series_2/4x2-glossy \
  -o EPIJ_Size=1 -o EPIJ_FdSo=0 -o EPIJ_Medi=145 -o EPIJ_Qual=306 \
  -o EPIJ_OPT_Bi_D=0 -o EPIJ_Silt=1 -o print-scaling=none -o fit-to-page=false
```

The macOS 26 print dialog folds the driver's panes into the collapsed "Printer Options" section;
Quiet Mode and Bidirectional are not exposed there even on the Epson driver, but the CUPS
instance sets them on every job sent through `lp` regardless of what the dialog shows.
