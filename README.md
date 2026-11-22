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
  "Scan N selected frames" and scans only those. Full scans are saved as 16-bit TIFF, plus
  `_IR.tif` when "Save infrared" is on.
- **Dust & scratch removal**: uses the infrared channel to find dust and scratches and rebuilds
  the picture under them. The method is nkscan's. "Dust removal by" picks where it runs:
  *Built in (one pass)* cleans the scan in the app, *Driver, second pass* is the older, slower
  way. A third choice, *Nikon ICE (helper)*, only shows when a working `ice_clean` helper sits
  next to `coolscan.py`; on this Mac it does not, so the choice is hidden.
- **Grain reduction** (off by default, levels 1 to 4): smooths film grain and keeps edges and
  texture, like Nikon's GEM. Our own method, not Nikon's.
- **Restore faded colours** (off by default; low, medium, high): lines up the three colours of a
  faded negative or slide again and strengthens them, like Nikon's ROC. Our own method. A picture
  that really is one colour all over (a sunset) loses some of it, so start low.
- **Multi-sample**: cleaner shadows. If the scanner can read each line several times, that is
  used ("one pass"). If it reads each line once, 2x and 4x scan the frame that many times at the
  same focus and exposure and average the passes; a pass that lands more than half a pixel off
  is left out. The Activity list says which kind your scanner is when it connects.

- **Negatives saved as**: Positive (default, like Nikon Scan), Negative (raw) or Both.
- **Archive mode**: saves two folders in the target folder. `RAW/` holds the untouched scan with
  the infrared as a 4th channel (RGBI, like SilverFast's archive files). `Positive/` holds the
  finished picture, inverted if it is a negative, with dust removed if ticked. Both come from
  one pass, so a frame takes about half as long as it did with two passes.
- **File names**: prefix + `_` + 3-digit counter + suffix + roll number, e.g. `Scan_001_R002.tif`.
  The prefix is optional: leave it empty and the name is `001_R002.tif`. The `_` belongs to the
  prefix, so there is none without it.
- **Next number**: shows the number the next scan gets. It counts on by itself from the pictures
  already in the roll folder (including `RAW/` and `Positive/`), whatever their prefix. Type
  another number to carry on from there; it is used for the next batch, then the roll counts on
  by itself again. A name that is already taken is skipped. Nothing is ever overwritten.
- **Negative tone**: "Flat" (default) keeps the plain conversion, lighter and lower in contrast,
  as a starting point for editing (for example Lightroom's Auto). "Nikon Scan look" gives
  negatives the same brightness and contrast as Nikon Scan, measured on five frames scanned in
  both programs.
- **Roll number in name**: file names end with the roll number, e.g. `Scan_001_R002.tif`.
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
- **Roll number**: type another number and press Change to renumber the roll you are scanning.
  Its folder is renamed and so is the `_R…` part of its files. If older roll folders already
  use that number or a higher one, nothing happens until you agree: then they are moved, whole
  and untouched, into a folder called `Earlier rolls`. Nothing is deleted or overwritten, and
  it cannot be done while a scan is running.
- **Film description**: free text about the roll (camera, place, dates). It is saved as
  `Description.txt` in the roll folder and updated whenever you change it, also during a scan.
  New roll… has the same box. If you edit the file by hand, the app picks that up when it starts.
- **When a strip is done**: sends a message when a batch finishes or stops on an error, with
  the number of frames saved and the roll. Fill in your own number or Apple ID for an iMessage
  (sent through this Mac's Messages app; macOS asks once for permission), tick the banner for a
  notification on the Mac, or give an ntfy topic for the free ntfy phone app. "Send a test"
  tries it. For ntfy, which goes through a server on the internet, only the roll number is
  sent, not your film notes.
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

Thumbnail, preview, scanning, Archive mode (then with two passes) and saving have run on the
LS-40 on the Mac (2026-11-22), and five frames were compared with Nikon Scan's own scans.
Features added after that (rolls, roll number in names, Nikon tone, photo date, ProPhoto as
default) are tested with the pretend scanner (`--demo`) and on the saved scan files only.

Added on 2026-11-22 in the evening, none of it run on the scanner yet:

- Built-in dust removal and one-pass Archive mode: run on the five real RAW scans from the
  Mac and compared with nkscan's own cleaner on the same files. Same pixels rebuilt, about
  95% of them identical to the last digit, the rest within rounding.
- Grain reduction and colour restoration: tried on one real scan and on a copy of it faded on
  purpose. Not compared with Nikon's GEM and ROC.
- Multi-sample by averaging passes: pretend scanner only. Earlier scans of the same frames
  landed within about 0.1 to 0.4 pixel of each other, which is why it should work.
- Messages: the iMessage and the banner run through macOS and could only be tested with a
  stand-in, so use "Send a test" first. ntfy was tested against a local stand-in server.
- Roll number change, film description, optional prefix and the next number: pretend scanner
  and a browser, with real folders and files.

The error help, the film-moved check, the thumbnail when film goes in, carrying on after a
failed frame, eject when done and the scan log are tested with the pretend scanner only, with failures made up to look like nkscan's.
None of them has been seen with a real failure on the scanner yet.
