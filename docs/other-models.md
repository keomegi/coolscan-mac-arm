# Other Coolscan models

The app is made for the Coolscan IV (LS-40), and on it nothing here changes. It also drives the
other models nkscan drives, setting itself up from what the scanner says it can do:

| Model | dpi | Connects by | nkscan has run it with (README, 0.20.0) |
| --- | --- | --- | --- |
| Coolscan IV ED (LS-40) | 2900 | USB | SA-21, MA-20/21 |
| Coolscan V ED (LS-50) | 4000 | USB | SA-21, IA-20/21, MA-20/21, SA-30, SF-210/200 |
| Super Coolscan 4000 ED (LS-4000) | 4000 | FireWire | SA-21, SA-30 |
| Super Coolscan 5000 ED (LS-5000) | 4000 | USB | SA-21, MA-20/21, SA-30 |
| Super Coolscan 8000 ED (LS-8000) | 4000 | FireWire | FH-869S, FH-869G (120) |
| Super Coolscan 9000 ED (LS-9000) | 4000 | FireWire | FH-835S (35 mm), FH-869S (120) |

The Coolscan III (LS-30) and older models are not among them: nkscan does not know them.
FireWire models need FireWire on the Mac: macOS dropped it, and nkscan's README points to the
ASFireWire driver plus an adapter.

What changes with the model:

- **Resolutions**: the scanner's own, from its optical dpi down (4000, 2000, 1333, ... on the
  4000 dpi models). A resolution saved on another scanner (2900 from the LS-40) moves to the
  nearest one this scanner has, and the Activity list says so.
- **Multi-sample**: in one pass where the scanner reads a line several times (all but the LS-40),
  otherwise by averaging passes, as on the LS-40.
- **Superfine** (Scan quality, LS-5000 only): reads one line of the sensor at a time, so no
  banding from line to line, about twice as long.
- **Format** (Film, LS-8000/9000 with a 120 holder): 6×4.5, 6×6, 6×7, 6×8 or 6×9. The crop size
  shown under the picture then uses that frame's length.
- **Colour profile**: nkscan embeds Nikon's own profile for the model (the LS-50 shares the
  LS-5000's, the LS-40 the LS-4000's).
- **Slides** (MA-20/21 mount, SF-210 feeder): one frame, no thumbnail pass; the app takes a
  preview instead, as it does for a slide on the LS-40.

## Whole rolls and long strips

The SA-30 roll adapter feeds an uncut roll (up to 40 frames), and a 9000's FH-835S holds two
strips. nkscan's thumbnail pass covers all of it, so the frames list shows every frame and
**Scan all** scans the whole roll in one go. With more than 6 frames the frames become one row of
bigger frames that scrolls sideways (◀ ▶ under it, the arrow keys, or a swipe); the line under the
row says which frames are in view, and the frame being scanned is scrolled into view as the scan
goes along. With the LS-40's SA-21 (6 frames at most) the frames look as they always have.

To try a model without one: `python coolscan.py --demo --demo-model LS-5000:SA-30` (also
`LS-50`, `LS-50:SF-210`, `LS-5000`, `LS-4000` (a 40-frame roll), `LS-40:MA-20`, `LS-8000`
(120 film), `LS-9000`). **None of this has run on a real LS-50, 4000, 5000, 8000 or 9000**: only
with the pretend scanner, whose numbers are what those models are expected to report.
