# ET-8550 printer presets (read off the Windows driver, 2026-09-03)

Every sheet prints from the Windows PC (`print-sheet.sh`, `make-proxies --print`). The presets
below live there as the defaults of the PC's print queues: `Glossy photo paper proxies` for proxy
sheets, `ET-8550 Series(Network)` for plain-paper test sheets, `Premium glossy paper` for Epson
Premium Glossy 220 gsm. The job only names the queue and sends the page at 100 %, so the queue's
defaults decide paper type, quality and feeder.

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
