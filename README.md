# Coolscan

A replacement for Nikon Scan on a modern Mac. Nikon's own software cannot run on Apple
Silicon, so this app drives the scanner itself, through the open-source
[nkscan](https://github.com/activexray/nkscan) driver. It scans film strips and slides, removes
dust and scratches with the scanner's infrared pass (tuned to match Nikon's Digital ICE), turns
negatives into positives, and keeps your rolls in order.

**Tested on one scanner only: the Nikon Coolscan IV ED (LS-40)**, on an M1 Mac. The other
Coolscans nkscan drives (LS-50, LS-4000, LS-5000, LS-8000, LS-9000) should work but have never
been tried with this app: see [docs/other-models.md](docs/other-models.md). It is not made for
Intel Macs or Windows.

## Set up (once)

Needs an Apple Silicon Mac and Python 3.13 or newer. In Terminal, in this folder:

```
bash setup.sh
```

It makes a private environment (`.venv`) here and installs the scanner driver and the rest.
New to this? [docs/setup.md](docs/setup.md) goes from nothing to a first scan, step by step.

## Run it: two ways

Both are the same app, with the same settings and scans. Use one at a time.

**As a Mac app.** Build it once, then open **Coolscan** from Launchpad or Spotlight:

```
bash mac/build_app.sh           # needs Apple's tools: xcode-select --install
```

It has its own window and Dock icon. Closing the window quits it.

**From Terminal with Python.** The page opens in your browser:

```
python3 coolscan.py             # --demo: no scanner   --test: test mode
```

Ctrl-C quits.

After an update, quit and start again. Neither way needs a new build or setup.

## What it does

- **Thumbnail, preview, scan** of a strip or slide: one frame, some, or all.
- **Dust and scratch removal** from the infrared pass. Off for silver B&W film and Kodachrome.
- **Negatives to positives**, per frame or with a film profile made from your own rolls.
- **Archive mode** (on by default): an untouched `RAW/` scan plus the finished `Positive/` picture.
- **Crop and turn**, by hand or automatic, and scanning only the cropped part.
- **Focus, exposure, grain reduction, faded-colour restoration, multi-sample, colour space.**
- **Scanned pictures**: browse, star, tag, crop, turn and export without changing the files.
- **Re-develop**: make the pictures again from RAW files with other settings, without the scanner.

The **?** on each box in the app explains it. The long version is in `docs/`.

## Where files go

Scans go to `~/Pictures/Coolscan`, one folder per roll:

```
Roll 002 - Kodak Portra ISO 400/
  RAW/20261122_005_R002.tif         untouched scan + infrared (Archive mode)
  Positive/20261122_005_R002.tif    the finished picture
  Description.txt  ScanLog.txt  FilmColour.json  FilmProfile.json  Library.json
  Edited/          Library exports
  Re-developed/    one folder per Re-develop run
```

**File names:** `[Prefix_]YYYYMMDD_NNN_RNNN.tif`: optional prefix, scan date, picture number,
roll number. Nothing is ever overwritten.

Settings live in `~/.coolscan.json`, test results in `Tests/` in the app folder.

## When something goes wrong

The Activity list (the A key) says what happened, with the driver's own words in brackets.

- **Scanner not answering:** check power and USB, then press Connect.
- **Hardware error:** switch the scanner off and on, then Connect.
- **No film:** feed the strip in again. The app finds the frames again.
- **Autofocus failed:** press Pick, click a detailed spot in the big picture, try again.
- **Banding after film sat in the scanner a long time:** Eject and feed it in again.
- **"numpy is missing" or "nkscan is missing":** run `bash setup.sh`.
- **Port 8765 busy:** another copy is running. Quit it, or `pkill -f coolscan.py`.
- **The Mac slept mid-scan:** the app reconnects by itself; scan that frame again.
- **Start-up problems with Coolscan.app:** View > Show Start-up Log
  (`~/Library/Logs/Coolscan.log`). The driver's log is `~/.coolscan-driver.log`.

## More detail

- [docs/setup.md](docs/setup.md): setting up from nothing, step by step
- [docs/scanning-screen.md](docs/scanning-screen.md): every box, button and setting, and how focusing works
- [docs/library.md](docs/library.md): Scanned pictures, exporting, colour per roll, the learning Auto crop
- [docs/auto-turn.md](docs/auto-turn.md): how the app works out which way up a picture goes
- [docs/redevelop.md](docs/redevelop.md): making pictures again from RAW files
- [docs/test-mode.md](docs/test-mode.md): comparing settings side by side
- [docs/mac.md](docs/mac.md): Coolscan.app, the Mac going to sleep, the graphics chip
- [docs/other-models.md](docs/other-models.md): other Coolscan models, whole rolls in the SA-30
- [docs/tested.md](docs/tested.md): what has been tried on the real scanner and what hasn't
- [docs/developers.md](docs/developers.md): how the code is laid out, and the checks
