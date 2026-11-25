# Coolscan

A Mac app for the Nikon Coolscan IV ED (LS-40), built on the open-source
[nkscan](https://github.com/activexray/nkscan) driver. It runs in its own window
(or a browser tab) at http://127.0.0.1:8765.

## Set up (once)

```
cd ~/Documents/GitHub/nikon_scan_project
python3.15 -m venv .venv
source .venv/bin/activate
pip install nkscan numpy
pip install mlx        # optional: faster picture work on the graphics chip
```

The app talks to the scanner through nkscan's Python package, so the separate
`nkscan` command-line program (and its quarantine flag) is not needed.

## Run

```
cd ~/Documents/GitHub/nikon_scan_project
source .venv/bin/activate
python coolscan.py              # --demo: no scanner   --test: test mode
```

Ctrl-C in Terminal quits.

**As a Mac app:** run `mac/build_app.sh` once (needs `xcode-select --install`). It puts
**Coolscan.app** in `~/Applications`. Updates need only quit and reopen, not a new build.
Closing the window quits the app. Don't run it from Terminal at the same time.

## What it does

- **Thumbnail, preview, scan** of a strip or slide, one frame, ticked frames or all of them.
  The thumbnail runs by itself when film goes in.
- **Dust and scratch removal** from the infrared pass, tuned to match Nikon's Digital ICE.
  Off for silver B&W film and Kodachrome.
- **Negatives to positives**, per frame or with a **film profile** made from your own rolls.
- **Archive mode** (on by default): an untouched `RAW/` file with the infrared kept, plus the
  finished `Positive/` picture, from one pass.
- **Crop** (drag, fixed shapes, Auto crop that learns from your crops), **turn**, and
  **scan only the cropped part** to save time.
- **Focus and exposure** per frame, the same for all, or by hand. Pick a focus point.
- **Grain reduction**, **restore faded colours**, **multi-sample** and **Multi-Exposure**.
- **Colour space**: ProPhoto RGB by default; Adobe RGB, sRGB and others.
- **Settings for one frame**, and **saved settings** under a name.
- **Library** (`/library`): browse, star, tag, crop and turn without changing the files, export copies.
- **Re-develop** (`/develop`): make the finished pictures again from RAW files with other
  settings, without the scanner.
- **Test mode** (`--test`): compare settings side by side on one frame or a strip.
- Keeps the Mac awake while it works, a scan log, scan times, and a message when a strip is done.

The **?** on each box in the app explains it. The long version is in `docs/`.

## Where files go

Scans go to `~/Pictures/Coolscan`, one folder per roll:

```
Roll 002 - Kodak Portra ISO 400/
  RAW/20261122_005_R002.tif         untouched scan + infrared (Archive mode)
  Positive/20261122_005_R002.tif    the finished picture
  Description.txt  ScanLog.txt  FilmColour.json  Library.json
  Edited/          Library exports
  Re-developed/    one folder per Re-develop run
```

**File names:** `[Prefix_]YYYYMMDD_NNN_RNNN.tif`, for example `Trip_20261122_005_R001.tif`:
optional prefix, scan date (can be switched off), picture number, roll number. Numbers count on
from what is already in the folder, and nothing is ever overwritten. **Photo taken** sets the
date the pictures were taken (a year alone keeps the scan's month, day and time).

Settings live in `~/.coolscan.json`, test results in `Tests/` in the app folder.

## When something goes wrong

The Activity list (the A key) says what happened, with the driver's own words in brackets.

- **Scanner not answering:** check power and USB, then press Connect.
- **Hardware error:** switch the scanner off and on, then Connect.
- **No film:** feed the strip in again. The app finds the frames again.
- **Autofocus failed:** press Pick, click a detailed spot in the big picture, try again.
- **Banding after film sat in the scanner a long time:** Eject and feed it in again.
- **Port 8765 busy:** another copy is running. Quit it, or `pkill -f coolscan.py`.
- **The Mac slept mid-scan:** the app reconnects by itself; scan that frame again.
  Keep the lid open on battery.
- **Start-up problems with Coolscan.app:** View > Show Start-up Log
  (`~/Library/Logs/Coolscan.log`). The driver's log is `~/.coolscan-driver.log`.

## More detail

- [docs/scanning-screen.md](docs/scanning-screen.md): every box, button and setting, and how focusing works
- [docs/library.md](docs/library.md): the Library and the learning Auto crop
- [docs/redevelop.md](docs/redevelop.md): re-developing RAW files
- [docs/test-mode.md](docs/test-mode.md): test mode
- [docs/mac.md](docs/mac.md): Coolscan.app, the graphics chip (MLX), the Mac going to sleep
- [docs/developers.md](docs/developers.md): how the code is laid out, and the checks (`python -m pytest`)
- [docs/tested.md](docs/tested.md): what has been tried on the real scanner and what hasn't
