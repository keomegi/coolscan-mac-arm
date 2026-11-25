# What has been tested, and on what

## Tested so far

Auto crop, fixed 2026-11-23 evening: it missed the gap between negatives at the left and right ends of a frame unless the gap was at the scanner's ceiling, which it only is at a final scan's exposure, not on a thumbnail or preview. It now measures the gap against the picture itself. Checked on the seven real LS-40 frames here (including 20261123_019_R003) at full, half and a third of the exposure and at thumbnail size: every gap is cut. Not tried in the app on the Mac yet.

Auto crop (2026-11-23): tried on the six real colour negatives from the LS-40 in the project
folder, at a quarter of full size and at thumbnail size. On all of them it cut the dark film
edges at top and bottom (about 4% each) and the clear gap at the frame ends where there was one,
and never more than 6% from any side. Also tested on made-up frames (a slide in a black mount, a
picture with no border, a large dark part of the picture that must not be cut) and in the app with
the pretend scanner (`checks/scripts/auto_crop_test.py`, `checks/page/page_auto_crop.js`). **Not tried on slides
from the real scanner, or on the Mac yet.** A frame askew in the holder by about a degree is
handled by cutting the wider end of each border; it cannot straighten the picture.

Added on 2026-11-23 (keeping the Mac awake), not run on the Mac or the scanner: tested in the
cloud with a pretend `caffeinate` and the pretend scanner (26 checks in
`checks/scripts/keepawake_test.py`): it starts once per run of jobs with `-i -s -m -w <app>`, stays on
while jobs follow each other, lets go after the wait, notices a sleep from the clock jumping,
gives the "the Mac went to sleep" message when the scanner drops after one, and connects again.
Two things only the Mac can show: that macOS honours it during a long pass, and whether the
LS-40 comes back by itself after a real sleep or needs switching off and on.

Added on 2026-11-23, not run on the scanner or the Mac yet:

- Scanning only the crop: nkscan's `scan_frame` scans exactly the rectangle it is given (its
  CHANGELOG: "a crop, can be scanned on a perforation-framed unit"; it sets the scanner's SET WINDOW to it and puts it in the
  LS-40's frame table first). Tested with the pretend scanner only (27 checks in
  `checks/scripts/crop_scan_test.py`, and the page in a browser): the rectangle the driver gets, metering
  on the whole frame, focus, infrared and dust removal on the crop, averaged passes,
  Multi-Exposure, Archive mode and the option off. **The one thing only the scanner can show:
  that the crop lands on the right part of the film.** Which way the picture's rows and columns
  run on the film is taken from nkscan's documentation. If a cropped scan shows the wrong part
  (mirrored, or another part of the frame), untick "Scan only the cropped part" and tell me.
- The focus point: the app handed nkscan the point the wrong way round (across and down on
  the picture, where nkscan takes along the sensor and along the feed). It now turns it the
  way nkscan's documentation describes. Not tried on the scanner; a point near the centre of
  the frame was barely affected before.

Added on the night of 2026-11-22, none of it run on the scanner or on the Mac yet:

- Multi-Exposure: tested with made-up passes and the pretend scanner only (36 checks). There
  the dark end of a scan comes out about half as noisy at 2x and a quarter at 4x, with no
  step where the passes meet, and a lamp a few percent off between the passes is measured and
  taken out. **Not tried on the scanner.** Two things only the scanner can show: whether it
  takes an exposure two or four times its own (if not, the frame is saved from the normal
  pass and the Activity list says so), and how well two passes of real film line up.
- Test mode: tested with the pretend scanner (78 checks) and a run through its page in a
  browser: the queue, runs starting from saved settings, sharing a pass and asking for an own
  scan, the saved files and the test log, times, a failing run, Stop, a lost scanner, and
  that the settings and the roll are left as they were. Not run on the scanner or the Mac.
- Film profiles: tested on a made-up roll only (an orange mask, a different contrast for each
  colour, every frame exposed on its own, the way this scanner exposes negatives). There the
  profile finds the film's numbers within a few percent and a grey card stays grey in frames
  full of green, orange or blue, where "each frame on its own" turns it clearly coloured. In the
  app: 51 checks with the pretend scanner and a run through the page. **No real film has been
  through it.** It rests on one thing that only the scanner can confirm: that twice the exposure
  reads twice the light (the driver gives the exposure as a time, so it should). If colours with
  a profile look wrong on your film, switch back to "Each frame on its own" and tell me.
  With that choice nothing about the pictures changes: they come out the same to the last digit
  as before profiles existed.

- The new screen (frames in two rows, looking and ticking, the big picture and its Thumbnail,
  Preview and Scan labels, enlarge, crop, the keys, folding boxes, saved settings): tested with
  the pretend scanner, by checks on the app (51 of them) and by a run through the page in a
  browser, in light and dark mode. Crop was checked on the saved files: their size, the
  infrared file, Archive mode, and a frame turned by 90°.
- The built-in dust cleaner tuned to Nikon's ICE: measured in the cloud on five real
  colour-negative frames from this scanner at 2900 dpi, against ICE's result on the same
  pixels (per-pixel difference from ICE 0.25%, before 1.71%; 12% of the frame changed, before
  42%, ICE 12.5%). Not tried on slides or at other resolutions, where there is no ICE result
  to compare with. Using it for ordinary scans (not only Archive mode) is new and was tested
  with the pretend scanner only.


Added on 2026-11-23, tested with the pretend scanner and a browser only, plus one real RAW scan
from the Mac developed in the cloud:

- Re-develop RAW files: 37 checks on the app and a run through its page in a browser (light and
  dark mode), with the pretend scanner. What was checked: a RAW file now carries how it was
  scanned (film, the pass's exposures, turn, crop); the four views differ in the right way; a file
  developed with the settings of its scan comes out identical to the scan's `Positive/` file to
  the last digit; nothing is overwritten (each run a new folder); older files with no note still
  develop, and the page says what they can and cannot do; turning and cropping; saving the
  in-between steps. On one real LS-40 RAW scan the dust removal rebuilt the same pixels it did
  when scanning, and the four views came out as expected. **Not run on the scanner or the Mac.**

Added late on 2026-11-22, tested with the pretend scanner and a browser only: scan times, the
stall check (its reading of the driver's notes was tested on made-up lines in the driver's
format, because the driver only writes real ones during a real pass), leaving the lens where
it is, "Run a shortcut" (the Shortcuts command was stood in for; it has not run on a Mac),
going back to a roll, and the selection being dropped when another strip goes in.

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
- Messages: the banner runs through macOS and could only be tested with a stand-in, so use
  "Send a test" first. ntfy was tested against a local stand-in server.
- Roll number change, film description, the file name order (prefix, date, number, roll), the
  next number and the "Photo taken" year: pretend scanner and a browser, with real folders and
  files, including rolls that hold names from the earlier versions.

The error help, the film-moved check, the thumbnail when film goes in, carrying on after a
failed frame, eject when done and the scan log are tested with the pretend scanner only, with failures made up to look like nkscan's.
None of them has been seen with a real failure on the scanner yet.

[Back to the README](../README.md)
