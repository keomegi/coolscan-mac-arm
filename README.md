# Coolscan

A small Mac app for the Nikon Coolscan IV ED (LS-40), built on the open-source
nkscan driver. It runs in your web browser at http://127.0.0.1:8765.

## Set up (once)

nkscan needs Python 3.13 or newer. This setup uses Python 3.15 in its own environment (`.venv`)
inside the project folder. In Terminal:

```
cd ~/Documents/GitHub/nikon_scan_project
python3.15 -m venv .venv
source .venv/bin/activate
pip install nkscan numpy
```

## Run

```
cd ~/Documents/GitHub/nikon_scan_project
source .venv/bin/activate
python coolscan.py
```

You see `(.venv)` at the start of the Terminal prompt while the environment is on.
Your browser opens on the app. Press Ctrl-C in Terminal to quit.
To try the screens without the scanner: `python coolscan.py --demo`.

## What the buttons do

- **Thumbnail only**: one quick pass over the strip. Shows the whole strip with the frames found.
- **Thumbnail when film goes in** (on by default): while the scanner is idle the app asks it
  every 2 seconds whether film is in. When a strip or slide goes in, the thumbnail pass runs by
  itself, like Nikon Scan. A slide has no thumbnail pass, so it gets a preview scan instead.
  After Eject the same strip is not taken back in: the app waits until the scanner says the
  film is out. If the pass fails it is not tried again until film goes in again.
- **Preview**: a preview of each frame (or the selected ones). "Preview from" picks how:
  - *Preview scan*: a low-resolution scan of each frame (sharper, slower).
  - *Thumbnail (fast)*: cuts each frame out of the thumbnail pass, so nothing is scanned again.
- **Calibrate**: autofocus and auto exposure on the selected frame, then keeps that focus and
  exposure for every preview and scan (like locking AE in Nikon Scan).
- **Autofocus** / **Auto exposure**: either one on its own. "Pick" lets you click a preview to
  choose the focus point. "Reset" goes back to focusing and exposing each frame on its own.
- **Rotate all 180°**: turns every preview and every saved scan.
- **Rotate selected ⟲ / ⟳ 90°**: turns the selected frames. Click selects one frame,
  ⌘-click adds or removes one, Shift-click selects a range.
- **Scan all frames** (batch scan): scans every frame on the strip, one after the other, into the
  roll folder with continuing numbers. Select frames first and the button becomes
  "Scan N selected frames" and scans only those. Full scans are saved as 16-bit TIFF with Nikon's colour profile, plus
  `_IR.tiff` when "Save infrared" is on. "Dust & scratch removal" uses nkscan's infrared cleaning.

- **Negatives saved as**: Positive (default, like Nikon Scan), Negative (raw) or Both.
- **Archive mode**: saves two folders in the target folder. `RAW/` holds the untouched scan with
  the infrared as a 4th channel (RGBI, like SilverFast's archive files). `Positive/` holds the
  finished picture, inverted if it is a negative, with dust removed if ticked. Dust removal in
  archive mode takes a second pass at the same focus and exposure.
- **File names** follow Nikon Scan: prefix + 3-digit counter + suffix, e.g. `Scan001.tif`.
  Prefix, suffix, number of digits and start number can be changed.
- **Negative tone**: "Flat" (default) keeps the plain conversion, lighter and lower in contrast,
  as a starting point for editing (for example Lightroom's Auto). "Nikon Scan look" gives
  negatives the same brightness and contrast as Nikon Scan, measured on five frames scanned in
  both programs.
- **Roll number in name**: file names end with the roll number, e.g. `Scan001_R002.tif`.
- **Colour space**: finished pictures are saved in ProPhoto RGB by default, converted through
  Nikon's LS-40 profile, with the ProPhoto profile embedded. Also Adobe RGB (1998), sRGB,
  Apple RGB, ColorMatch, Bruce, Wide Gamut, or Scanner RGB (Nikon's profile, no conversion).
  RAW files and raw negatives always keep Nikon's profile. ProPhoto files look right in
  Lightroom, Photoshop, Preview and Photos; a program that ignores colour profiles shows them
  dull, so export to sRGB before sharing.
- **Roll**: scans go into a folder per roll inside the target folder (`Roll 001`, …). Each new
  strip keeps adding to the same folder with continuing numbers. **New roll…** asks you to
  confirm and lets you type the film's brand, name, ISO and notes (all optional). The new folder
  is named from them, e.g. `Roll 002 - Kodak Portra ISO 400`, numbering starts again at 001,
  and the film details are written into each file (description and EXIF ISO). The current roll
  and settings are remembered between runs (in `~/.coolscan.json`).
- **Photo taken**: type a year (or year-month-day) and the files get that as their EXIF
  "date taken", so Apple Photos, Lightroom and Google Photos sort them by it. The real scan date
  stays in the file's description.

- **Stop on errors**: off by default. If one frame fails (for example autofocus), the rest of the
  strip is still scanned and the Activity list says which frame failed. Tick it to stop at the
  first failure instead. A lost connection or missing film always stops the batch.
- **Eject film when done**: ejects the strip after a batch in which every frame was saved.
- **Write scan log**: adds to `ScanLog.txt` in the roll folder, like Nikon Scan's scan log: the
  scanner and settings, then for each frame when it started and which files were saved, or why
  it failed.

Previews of negatives are inverted so you can judge them. Negatives are saved as positives unless
you choose otherwise under "Negatives saved as".

## When something goes wrong

The Activity list says what happened and what to do, with the driver's own wording in square
brackets (send that part along if you report a problem).

- **The scanner stopped answering**: check power and the USB cable, then press Connect.
- **There is no film**: feed the strip in again. Nikon's driver rewinds a strip that has sat idle
  for a while. The app notices that the film moved, takes it in again if it can, and finds the
  frames again before the next preview or scan.
- **Autofocus failed**: press Pick, click a part of the preview with clear detail, try again.
- **Banding after the same strip sat in the scanner for a long time**: Nikon's help says the
  scanner calibrates itself each time film is changed, so press Eject and feed the strip again.
  (nkscan has no separate "calibrate the scanner" command.)
- **Hardware error**: switch the scanner off and on, then press Connect.

## Tested so far

Thumbnail, preview, scanning, Archive mode and saving have run on the LS-40 on the Mac
(2026-11-22), and five frames were compared with Nikon Scan's own scans. Features added after
that (rolls, roll number in names, Nikon tone, photo date, ProPhoto as default) are tested with the pretend scanner
(`--demo`) and on the saved scan files only.

The error help, the film-moved check, the thumbnail when film goes in, carrying on after a
failed frame, eject when done and the scan log are tested with the pretend scanner only, with failures made up to look like nkscan's.
None of them has been seen with a real failure on the scanner yet.
