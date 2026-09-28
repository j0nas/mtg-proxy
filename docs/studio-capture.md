# Silhouette Studio ↔ Cameo 5 Alpha, recorded (2026-09-28)

Silhouette Studio cutting mtg-proxy's `test-sheet.pdf` (card-maker A4 standard, landscape, four
L-marks at a 10 mm inset, 9.4 × 1 mm) from a Studio cut template, over
Bluetooth LE. Recorded with Apple's PacketLogger, with the `Bluetooth_macOS.mobileconfig` logging
profile installed (without it PacketLogger records nothing on current macOS), and decoded with
`tshark`. Plain paper, copy-paper preset: force 15, speed 10, depth 3. Firmware
`CAMEO 5 ALPHA V1.04`. `mtgproxy.cameo` follows this conversation; the raw capture stays out of
the repo because it also holds other nearby devices' adverts.

## The link

Vendor service `e2088282-4fde-42f9-bb22-6ec3c7ed8f91`: CONTROL `61490654…` (handle 0x17, movement
events), READ `8dcf199a…` (0x1a, replies), WRITE `6d92661d…` (0x1d, commands). Replies and events
arrive as **indications**. Studio enables both CCCDs, then asks `ESC DC1` (0x11), which returns the
firmware string.

## Idle polling

While connected, Studio polls about every 0.5 s and ignores unchanged answers:

| Query | Reply | Meaning |
|---|---|---|
| `ESC ENQ` (0x05) | `0` / `1` / `2` | ready / moving / no mat loaded |
| `ESC NAK` (0x15) | ` 2, 0` | tool slots |
| `ESC SYN` (0x16) | `   1` or `   0` | unknown |

Before the job: `FG`, `TI` (empty), `TO` (`0`), `TB71` (`    0,    0`), `FA` (`    0,    0`),
`TC` (`    0,    0`). With no mat the status stays `2`; loading it raises a CONTROL `0` event.

## The job (writes in order, ETX-separated)

```
TG1 FN0 TB50,0 FM0 TR0,1
TB99 TB52,2 TB51,188 TB53,20 TB55,1 APS30
TB124,3800,5540,50,230 TB99            ← scan
                                        CONTROL "1" … 18.6 s … CONTROL "0" + READ "    0" (found)
\0,0 Z3800,5540 J1
FX15,1 TJ0 !10,1 APS0 FC0,1,1 FE0,1 FF1,0,1 FF1,1,1
FX15,1 TJ3
!10,1 APS30 FC18,1,1 TF3,1
M165,226 BE1… BE2… …                    ← paths, binary-encoded (BE1/BE2), ~9 KB for 8 cards
                                        … 33 s of cutting, status "1" throughout …
TB0
L0
\0,0 M0,0 TR0,0 J0 FN0 TB50,0           ← back to the origin (no SO0: the origin never moves)
```

Studio polls `ESC ENQ` during the scan too; those replies (`1`, moving) are **unpadded**, while
the scan's own result is padded to five characters (`    0` found, `    1` not found). Reading the
first reply after `TB124` as the result is therefore only safe when nothing else is queried
during the scan; `mtgproxy.cameo` accepts only the padded form.

## What it settled

- **The scan start.** `TB124,h,w,top,left`: h = 190 mm and w = 277 mm (mark to mark), then top
  2.5 mm and left **11.5 mm** from the origin. That is 7.5 mm above the top-left mark's
  horizontal leg and 0.5 mm past its vertical leg: the sensor starts on white paper and travels
  down onto a mark line.
  `mtgproxy.cameo` uses top = max(inset − 7.5, min(2.5, inset / 2)) and left = max(inset +
  thickness + 0.5, min(11.5, inset + length − 1)): Studio's (2.5, 11.5) for any inset from 5 to
  10 mm. Calibration sheets (2026-09-28): at a 5 mm inset, left 6.5 skewed the cut and 11.5 did
  not. With that start our cuts land 0.5 mm high, the `driver` offset in `data/cut_offset.json`.
- **The order.** Marks are described and scanned before the blade is set up; the blade setup and
  cutting area come after, in the registered frame.
- **Mark description** `TB51,188 TB53,20` = 9.4 mm legs, 1 mm lines (length and thickness, in
  that order).
- **The ending** returns to the origin (`M0,0`) and never sets a new one.
- **Studio's minimum inset is 10 mm**; the deck box prints at 5 mm, outside anything Studio does.

Unknowns kept verbatim: `FM0`, `TR0,1`/`TR0,0`, `APS30`/`APS0`, `TB0`, the second `TB99`, and
the `ESC SYN` poll. No command in the capture reads back where the marks were found.

## Our driver's first probe (same sheet, straight after Studio's cut)

`cut-proxies -p a4 -c standard -r 4 --probe` over BLE: connected in 2.6 s. The scan used Studio's
start and found the marks in 18.75 s (Studio: 18.6). Queries before → after the scan:

| Query | Before | After | Reading |
|---|---|---|---|
| `FQ5` | `0` | `-64` | changes with the scan: a candidate registration readback, unit unknown |
| `[` / `U` | `0,0` / `3800,5540` | `-20000,-20000` / `20000,20000` | cutting-area corners; Studio's `Z` survived its own job, and the scan resets the area to ±1 m |
| `TB71`, `FA` | `0,0` | `0,0` | no stored sensor/roller calibration |
| `FQ0`, `FQ2` | `100`, `18` | same | static (18 is probably the 0.9 mm blade offset) |
| `OA OC OO OH OW OS OE` | — | — | no reply: unsupported |

Machine state (origin, cutting area) persists between jobs and apps, so every job sets both
explicitly. Next: whether `FQ5` repeats on an untouched sheet and tracks a deliberate skew.

