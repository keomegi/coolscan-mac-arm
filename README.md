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
To compare settings on one frame or a whole strip: `python coolscan.py --test` (see "Test mode" below).

## How the app is put together

`coolscan.py` only starts the app. The app itself is the `coolscan_app` folder next to it,
in a few folders, one file per job:

```
coolscan_app/
  app.py          the scanner session: connect, thumbnail, preview, focus, exposure, film watcher
  server.py       the web page's server and the start-up
  settings.py     the settings and how they are kept between runs
  rolls.py        file names, roll folders, roll numbers, film descriptions, film profiles
  keepawake.py    keeps the Mac from going to sleep while a job runs, notices when it slept anyway
  scanner/        talking to the scanner
    driver.py       the stall check, and the driver's failures in plain words
    demo.py         the pretend scanner (--demo, and the tests)
    scanning.py     scanning frames and saving them, scan log, scan times
  imaging/        the pictures
    files.py        writing TIFF and PNG files, reading a saved TIFF back
    colour.py       negative to positive, pictures for the screen, colour spaces and profiles
    dust.py         dust and scratch removal from the infrared picture
    enhance.py      grain reduction, faded-colour restoration, lining passes up, two exposures together
    autocrop.py     finding the picture inside a frame (Auto crop)
    develop.py      re-developing finished pictures from RAW scans
  extras/         optional parts, listed in .gitignore and never committed
    notify.py       banner, shortcut and ntfy messages; without it the "Tell me" options are hidden
    testmode.py     test mode (--test); without it --test opens the ordinary screen
  web/            the page: index.html, style.css, app.js, develop.html, develop.js
                  (test mode's page, test.html and test.js, is optional like testmode.py)
```

## Tests

The tests need no scanner: the app's pretend scanner stands in. They run on GitHub's Linux machines after
every push (the "Tests" workflow, `.github/workflows/tests.yml`), and on your Mac with:

```
cd ~/Documents/GitHub/nikon_scan_project
source .venv/bin/activate
pip install -r requirements-dev.txt      # once: tifffile and pytest
python -m pytest -m quick                # seconds: every file compiles, every module loads, the app starts
python -m pytest                         # everything, a few minutes
```

They are in `checks/` (not `tests/`: on a Mac that would be the same folder as test mode's
`Tests/`). See `checks/README.md`. **They say nothing about the LS-40 itself**: only a scan
on the real scanner shows that.

Settings and the current roll stay in `~/.coolscan.json`, as before.

## The screen

The bar at the top says what the scanner is doing and holds the main buttons: Connect, Load,
Eject, Thumbnail, Preview and the blue **Scan** button. While something runs, the bar shows the
progress and the times, and Stop becomes available.

On the right are the film and the picture:

- **Frames**: the frames of the strip as small pictures in two rows. They appear by themselves
  after the thumbnail pass. Under them, "Whole strip" unfolds the thumbnail pass as the scanner
  saw it.
- **Looking and ticking are two things.** Click a frame to look at it: it gets a blue edge and
  fills the big picture below. Tick a frame (its box, or ⌘-click, or the X key; Shift-click
  ticks a run) to choose it for scanning. With nothing ticked, Scan takes the whole strip and
  the button says "Scan all 6 frames"; with some ticked it says "Scan 2 selected frames".
  Ticks are dropped when another strip goes in.
- **The big picture** shows the frame you clicked, and a label says where the picture comes
  from: **Thumbnail** (cut from the thumbnail pass, small and rough), **Preview** (after you
  asked for a preview scan) or **Scan** (the picture that was just saved, shown after the frame
  is scanned). A scanned frame also lists the files it was saved as and how long it took.
- **Preview scan**: an extra, never needed before scanning. It scans the frame you are looking
  at (or the ticked ones) at the preview resolution so you can judge it better than the
  thumbnail allows.
- **Enlarge**: double-click a frame or the big picture, or press Space, to see it as large as
  the window. Click or press Esc to go back.
- **Crop**: press Crop and a box with handles appears over the whole frame: drag its sides or
  corners in (or drag across the picture to draw a new box). That part of the frame is what gets
  saved when the frame is scanned (also its `_IR.tif`). To change a crop, drag one of its sides
  or corners, or drag its middle to move it (no need to press Crop first).
- **Auto crop**: the button finds the picture inside the frame and crops to it, leaving out the
  dark film edges and holder, a slide's mount, and the clear film between negatives. It looks at
  the picture on screen, so after a preview scan it is more exact than on the thumbnail. "Crop
  each frame automatically" (Save as box, off by default) does this for every frame as soon as
  it has a picture, and again after a preview scan. A crop you set, change or clear by hand is
  left alone; switching the box off removes only the crops it made. Each automatic crop can
  still be changed with the handles. With "Scan only the cropped part"
  ticked (Save as box, on by default) the scanner reads only that part, as Nikon Scan does: the
  film is stepped only over the crop and only the crop crosses the scanner's slow USB link, so a
  crop of half the frame should take about half the scanning time (focus and exposure take as
  long as before). Exposure is still measured on the whole frame, so a cropped frame comes out
  as bright as its preview; focus is the frame's own, or the focus point when you chose one.
  After such a scan the big picture stays the whole frame (marked Scanned), so the crop box can
  still be moved for another scan. Unticked, the whole frame is scanned and the crop is cut out
  when the file is saved. In Archive mode `RAW/` always keeps the whole frame (so the whole
  frame is scanned) and only `Positive/` is cropped. A frame scanned as a crop is not noted for
  making a film profile, which needs whole frames. The crop belongs to that frame of that strip,
  turns with the frame, and is forgotten with the next strip. "Clear crop" removes it. A crop
  made after a frame was scanned applies to the next scan of it, not to the file already saved.
- **Keys**: ← → ↑ ↓ look at another frame, X ticks it, ⌘A ticks all, Space enlarges, C crops,
  [ and ] turn the frame, P preview scan, T thumbnail, ⌘Return scan, Esc leaves crop or enlarge.

On the left are the settings, in boxes that fold away. Click a box's title to fold or unfold it;
a folded box shows its main choices in a few words, and the app remembers which boxes you keep
open. The "?" on a box shows a short explanation of what is in it.

- **Saved settings**: "Save as…" keeps the scan settings under a name (for example "Portra,
  archive" or "Quick look"), the list switches between them, and "Delete" removes one. Saved
  under a name that exists, the settings are updated. What is kept is how to scan and save
  (film type, resolution, dust removal, focus and exposure choices, colour, file name choices,
  what happens when a strip is done). The folder, the roll and its film details, and the
  message settings are not part of it, so loading a set never moves you to another roll. When
  the settings in use differ from every saved set, the list says "Not saved".

## What the buttons do

- **Thumbnail only**: one quick pass over the strip. Shows the whole strip with the frames found.
- **Thumbnail when film goes in** (on by default): while the scanner is idle the app asks it
  every 2 seconds whether film is in. When a strip or slide goes in, the thumbnail pass runs by
  itself, like Nikon Scan. A slide has no thumbnail pass, so it gets a preview scan instead.
  After Eject the same strip is not taken back in: the app waits until the scanner says the
  film is out. If the pass fails it is not tried again until film goes in again.
- **Preview**: a low-resolution scan of the frame you are looking at, or of the ticked frames,
  to replace its thumbnail in the big picture with something sharper. (The old choice "Preview
  from: thumbnail" is gone, because every frame now shows its thumbnail by itself.)
- **Focus & exposure** has two parts. The buttons under "Do once, now" act once, right away, on
  the frame you are looking at. The choices under "Settings" are used for every preview and scan.
  - **Calibrate**: autofocus and auto exposure on that frame, then switches the settings
    to keep that focus and exposure for every preview and scan (like locking AE in Nikon Scan).
  - **Autofocus** / **Auto exposure**: either one on its own. "Pick" lets you click the big
    picture to choose the focus point. "Reset" goes back to focusing and exposing each frame on its own.
  - See **How focusing works** just below for exactly when the scanner focuses.

### How focusing works

The thumbnail pass never focuses. Focusing happens only before a preview or a scan, and how
often depends on the **Focus** setting (Focus & exposure box):

- **Autofocus each frame once** (the default). The first time a frame is previewed or scanned,
  the scanner focuses on it, and the app remembers that lens position for that frame. Every
  later pass over the same frame (a second preview, the scan, multi-sample passes, the
  Multi-Exposure pass, test-mode runs) uses that same position, and the lens is not even moved
  when it is already there. Each frame gets its own focus, because film curls differently from
  frame to frame. The remembered positions are forgotten, and every frame is focused again,
  when a new strip's frames are found (Thumbnail, or a strip going in), after Eject, after
  Reset, and when you pick or clear a focus point.
- **Same focus for all frames**. Every frame uses one lens position: the one from the last
  Autofocus or Calibrate. If there is none yet, each frame is focused once as above until
  there is.
- **Autofocus on every pass**. The scanner focuses again before every pass, even of the same
  frame. Slowest; only useful when you suspect the film moves.
- **Manual lens position**. No autofocus at all: every pass uses the number you type
  (Autofocus fills it in for you each time you press it).

The buttons, which act once, right away, on the frame you are looking at:

- **Autofocus**: focuses that frame now. That frame keeps the result; with "each frame once"
  the other frames still get their own focus. With "Same focus for all frames" this is the
  focus every frame uses.
- **Calibrate**: Autofocus and Auto exposure on that frame, then switches Focus to "Same focus
  for all frames" (and exposure to the same), so the whole strip uses this one focus. Handy
  for flat film in a slide holder; for curly negatives "each frame once" is usually better.
- **Reset**: back to "Autofocus each frame once" and each frame exposed on its own; the
  remembered focus is dropped, so frames are focused again.
- **Pick** (Focus point): click the big picture where the scanner should focus, for example a
  face or anything with clear detail, instead of the centre. It counts for every frame. "Clear"
  goes back to the centre.

**Cropped scans**: with "Scan only the cropped part", focusing is still done on the whole
frame (at the focus point, or the frame's centre), the same as without a crop, and the scan of
the crop uses that lens position. So a crop at the edge of the frame is in focus as well as the
middle was. (Only "Autofocus on every pass" focuses on the crop itself: at the focus point when
it is inside the crop, otherwise the crop's centre.) Re-developing a RAW file never focuses: it
does not use the scanner.

**Where you see it**: the Activity list says "frame N: autofocus" and then "focus focused, lens
position 240" each time the scanner really focuses, and the line under the Focus & exposure
buttons says what is set now.
- **Rotate all 180°**: turns every preview and every saved scan.
- **⟲ / ⟳** above the big picture: turn the frame you are looking at (or the ticked ones) by
  90°, on the screen and in the saved scan.
- **Scan all frames** (batch scan): scans every frame on the strip, one after the other, into the
  roll folder with continuing numbers. Tick frames first and the button becomes
  "Scan N selected frames" and scans only those. Full scans are saved as 16-bit TIFF.
- **Also save the infrared picture (_IR.tif)**: when ticked, each frame gets a second file ending
  in `_IR.tif`, a grey picture that shows only the dust and scratches. It is an extra for
  checking or for other software. Dust removal works the same with it unticked, and "Negatives
  saved as" has nothing to do with it.
- **Dust & scratch removal**: uses the infrared picture to find dust and scratches. "Dust
  removal by" picks the method:
  - *Built in* (the default): the app's own cleaner, tuned on five real negatives until its
    result lined up with Nikon Scan's Digital ICE on the same scans. It leaves a pixel exactly
    as scanned unless its infrared is clearly darker than the rest of the film (about one pixel
    in eight on those frames), gives light dirt its light back, and fills heavy dirt from the
    clean film around it. It adds no grain. It runs in the app right after the pass, about a
    second a frame.
  - *Driver (older method)*: nkscan's own cleaner, which the app used before. It rebuilds about
    four pixels in ten, brightens clean film a little and adds grain of its own. Kept so you
    can compare, or go on with a roll you started with it. In Archive mode it needs a second
    pass.
  - *Nikon ICE (helper)* only shows when a working `ice_clean` helper sits next to
    `coolscan.py`; on this Mac it does not, so the choice is hidden.

  Nikon's ICE was only used as something to compare against. No Nikon code is in the app.
- **Grain reduction** (off by default, levels 1 to 4): smooths film grain and keeps edges and
  texture, like Nikon's GEM. Our own method, not Nikon's.
- **Restore faded colours** (off by default; low, medium, high): lines up the three colours of a
  faded negative or slide again and strengthens them, like Nikon's ROC. Our own method. A picture
  that really is one colour all over (a sunset) loses some of it, so start low.
- **Multi-sample**: cleaner shadows. If the scanner can read each line several times, that is
  used ("one pass"). If it reads each line once, 2x and 4x scan the frame that many times at the
  same focus and exposure and average the passes; a pass that lands more than half a pixel off
  is left out. The Activity list says which kind your scanner is when it connects.
- **Multi-Exposure** (off by default; 2x or 4x longer): scans each frame twice, the second time
  with the exposure that many times longer and the lens where the first pass left it, and puts
  the two together: where the film is dense the picture is read from the longer pass (less
  noise there), where the longer pass burns out it stays the normal pass, with a smooth
  change in between. Dense film means the shadows of a slide and the highlights of a
  negative, so it helps slides most; on negatives the gain is small. Each frame takes at
  least twice as long. The infrared is read once, in the normal pass. If the scanner will
  not take the longer exposure, or the second pass comes back another size, more than half a
  pixel off or does not fit the first, the frame is saved from the normal pass alone and the
  Activity list says why. It cannot be combined with multi-sample by averaging passes, and
  with it on, dust removal is always done by the app ("Driver" can only clean one pass).

- **Negatives saved as**: Positive (default, like Nikon Scan), Negative (raw) or Both.
- **Archive mode**: saves two folders in the target folder. `RAW/` holds the untouched scan with
  the infrared as a 4th channel (RGBI, like SilverFast's archive files). `Positive/` holds the
  finished picture, inverted if it is a negative, with dust removed if ticked. Both come from
  one pass, so a frame takes about half as long as it did with two passes. Every RAW file now
  also carries a note of how it was scanned (the film, the pass's exposures, the turn and crop),
  so it can be developed again later without the scanner (see **Re-develop RAW files**). Files
  scanned before this still re-develop, with a little less to go on (see that section).
- **File names** are built in this order: prefix, scan date, picture number, roll number, e.g.
  `20261122_005_R001.tif`. The roll number is always there and is the number of the roll you
  are scanning. The prefix is empty unless you type one (`Trip_20261122_005_R001.tif`), and the
  date can be switched off with "Scan date in name"; each brings its own `_`, so leaving one
  out leaves no stray underscore. The date is the day of the scan, not the "Photo taken" date.
  A batch that runs past midnight keeps the date it started with.
- **Next number**: shows the number the next scan gets. It counts on by itself from the pictures
  already in the roll folder (including `RAW/` and `Positive/`), whatever their prefix. Type
  another number to carry on from there; it is used for the next batch, then the roll counts on
  by itself again. A name that is already taken is skipped. Nothing is ever overwritten.
- **Colour of negatives** (in the Film box, for colour negatives): where the colour of the
  finished picture comes from.
  - *Each frame on its own* (the default, and how it always worked): each of red, green and
    blue is stretched between the thinnest and densest film in that one frame. That takes the
    orange mask off and suits ordinary pictures. It goes wrong when a frame is mostly one colour
    (grass, a sunset, blue sky): the app cannot tell that from a colour cast and takes it out.
  - *A film profile*: the colour comes from the film instead of from the frame. The app measures
    every frame as it is scanned (the thinnest and densest film in each colour, with the
    scanner's exposure taken out so that frames can be compared) and keeps the numbers in
    `FilmColour.json` in the roll's folder. **Save this roll's film…** turns the measurements of
    the roll you are in into a profile under a name you give, for example "Kodak Gold 200". It
    needs at least 3 scanned frames; a whole strip or a whole roll is better, and frames with
    deep shadows and bright highlights help most. Pick the profile under "Colour from" and
    every frame scanned after that gets the same colour: a grey wall stays grey even in a frame
    full of grass. Brightness is still set frame by frame, by the same amount for all three
    colours. Use it for the rest of that roll and for later rolls of the same film.
  - A profile is switched off when you start a new roll (it may be another film) and comes back
    when you go back to a roll that used it. It is part of saved settings. Thumbnails are always
    shown "each frame on its own", because the thumbnail pass has no exposure to go by; a preview
    scan and the scan itself show the profile's colour.
  - These are not maker's profiles like SilverFast's NegaFix, which are measured film by film
    on each scanner model. The numbers here come from your own film on your own scanner, which
    is the only place accurate ones can come from without that kind of measuring. A profile
    made from a roll with odd lighting carries that into every roll you use it on; delete it and
    save it again from a better roll.
- **Negative tone**: "Flat" (default) keeps the plain conversion, lighter and lower in contrast,
  as a starting point for editing (for example Lightroom's Auto). "Nikon Scan look" gives
  negatives the same brightness and contrast as Nikon Scan, measured on five frames scanned in
  both programs.
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
- **Going back to a roll**: the list at the top of the Roll box holds every roll folder in the
  target folder. Pick one and the next scans go into it again, counting on from the pictures
  already there, with that roll's number, film details and description. Nothing is renamed or
  moved. Not while a scan is running.
- **Roll number**: type another number and press Change to renumber the roll you are scanning.
  Its folder is renamed and so is the `_R…` part of its files. If older roll folders already
  use that number or a higher one, nothing happens until you agree: then they are moved, whole
  and untouched, into a folder called `Earlier rolls`. Nothing is deleted or overwritten, and
  it cannot be done while a scan is running.
- **Film description**: free text about the roll (camera, place, dates). It is saved as
  `Description.txt` in the roll folder and updated whenever you change it, also during a scan.
  New roll… has the same box. If you edit the file by hand, the app picks that up when it starts.
- **When a strip is done**: tells you when a batch finishes or stops on an error, with the
  number of frames saved and the roll. Three ways, any mix of them:
  - *Banner on this Mac*: a notification on the Mac.
  - *Run a shortcut*: type the name of one of your own shortcuts from the Shortcuts app (the
    field suggests them). The app runs it and hands it the message as text, so the shortcut
    decides how it reaches you: an email to yourself, a reminder with an alert, a push app.
  - *ntfy topic*: the free ntfy phone app. It goes through a server on the internet, so only
    the roll number is sent, not your film notes.

  "Send a test" tries them. (The iMessage option is gone: a message to your own account does
  not ring your phone.)

  These options live in `coolscan_app/extras/notify.py`, which is optional and listed in `.gitignore`,
  so it is never committed. Without that file the app runs as usual and the "Tell me" options
  are hidden; put the file back and restart the app to get them again.
- **Photo taken**: type a year (or year-month-day) and the files get that as their EXIF
  "date taken" and as the file's own date, so Apple Photos, Lightroom and Google Photos sort
  them by it. With only a year (`2005`), the files get the day and time of the scan in that
  year, e.g. `2005-11-22 20:59:12`, so they keep the order and spacing they were scanned in.
  With a full date, they get that day at noon, one second apart. The real scan date stays in
  the file's description.

- **Stop on errors**: off by default. If one frame fails (for example autofocus), the rest of the
  strip is still scanned and the Activity list says which frame failed. Tick it to stop at the
  first failure instead. A lost connection or missing film always stops the batch.
- **Eject film when done**: ejects the strip after a batch in which every frame was saved.
- **Write scan log**: adds to `ScanLog.txt` in the roll folder, like Nikon Scan's scan log: the
  scanner and settings, then for each frame when it started, which files were saved and how
  long it took, or why it failed.
- **Scan times**: while a strip is scanned, the line above the Activity list shows the time on
  this frame, the time on this strip and about how long is left. Each finished frame writes its
  time in the Activity list and the scan log (scanning and dust removal apart), and the strip
  its total and the time per frame.
- **The stall check**: during a pass the scanner sends the picture while its stage moves. If the
  computer is slow to take it, the scanner's memory fills and the stage has to stop and start
  again. The driver measures that, and after each frame the Activity list says either "the
  scanner never had to wait for the computer" or for how long it waited. The driver's own
  notes are in `~/.coolscan-driver.log`, started fresh each time the app starts.

Pictures of negatives on the screen are inverted so you can judge them. Negatives are saved as positives unless
you choose otherwise under "Negatives saved as".

## Test mode

For finding out what a setting really does on your film: one frame, some frames or the whole
strip, scanned or worked out again with several sets of settings, each result saved under its own name so you can put them side by
side. It is a separate way of starting the app and does not appear on the ordinary screen:

```
cd ~/Documents/GitHub/nikon_scan_project
source .venv/bin/activate
python coolscan.py --test
```

Test mode is optional and kept out of git, like `notify.py`: `coolscan_app/extras/testmode.py`,
`web/test.html`, `web/test.js` and the test results in `Tests/` are listed in `.gitignore`. Without
those files the app runs as usual, and `--test` says test mode is not installed and opens the
ordinary screen.

The browser opens on the test page (http://127.0.0.1:8765/test). "Normal screen" at the top
right goes to the ordinary screen, which works as always; the settings set there are the ones
the runs start from.

1. **Frames to test**: every frame of a new strip starts ticked. Click a frame to untick or tick
   it; **Whole strip** ticks them all, **Untick all** clears them. (Thumbnail in the bar finds the
   frames if they are not there yet.)
2. **Queue**: under "Add a run", leave everything on "As it is" to scan with the settings in
   use, or change one or more of: resolution, multi-sample, Multi-Exposure, dust removal on or
   off and by which method, grain reduction, restore faded colours, negative tone, colour of
   negatives (film profile, or **Profile made from this strip**, see below) and colour space. "Starts from" lets a run begin from one of your
   saved settings instead of the settings in use. A label is optional; without one the run is
   named after what it changes ("dust removal off", "Slides, 1450 dpi"). The queue is kept
   between starts of the app, up to 40 runs.
3. **Run the queue** (the button says how many runs on how many frames), then look at **Results**: each run shows the whole frame, a piece of it at
   full size (one pixel of the scan to one pixel of the screen), how long it took and what the
   time went on, and the files it saved. Click a spot on any whole frame and every result shows
   that spot at full size; click a piece to see it larger. Stop ends the queue after the step
   it is on.

What to know:

- **Runs share a pass where they can.** Two runs that need the same pass over the film (same
  resolution, multi-sample, Multi-Exposure, film type, focus and exposure) use one scan, and
  only the work after the pass is done again. So "dust removal on" against "dust removal off",
  or two tones, or two colour spaces, differ only by that setting and not by the noise of
  another pass, and the second one takes seconds. Its time says "using the pass of an earlier
  run". Tick **Own scan** when you want a run to scan again anyway, for example to see how much
  two passes of the same frame differ. The frame is focused once and every pass of the test
  uses that lens position.
- **Where the results go**: a new folder for every test in `Tests/` inside the app's own folder
  (`~/Documents/GitHub/nikon_scan_project/Tests/`), not in your scans folder. `.gitignore` keeps
  `Tests/` out of git, so test pictures are never committed. For example
  `Tests/20261123-101500 frame 3/`, holding `01 Settings in use.tif`, `02 dust removal off.tif`
  and so on (in `RAW/` and `Positive/` if a run is in Archive mode), plus `Test log.txt` with
  each run's settings, files and time. The infrared picture, when "Also save the infrared
  picture" is on, is saved once for a pass and not again for each run that shares it.
- **Tests stay out of your roll.** Nothing is written into a roll folder or your scans folder, the roll's next
  number does not move, test frames are not measured for film profiles, and the ordinary
  screen keeps the picture it had. The settings in use are put back when the queue ends,
  whether it finished, was stopped or failed, and cannot be changed while it runs.
- **Several frames**: the frames are done one after another, each with every run, so the
  passes runs share are kept for one frame at a time. The folder is named for what was tested
  (`Tests/20261123-101500 whole strip (6 frames)/`, `... frames 1, 3/`) and the files say the
  frame first: `frame 03 - 02 multi-sample 4x.tif`. Results can be laid side by side as **each
  frame with all its runs** or **each run over all frames**, and **Time per run** adds up every
  run over all the frames, with its time a frame, so you see what a setting costs. A run that used
  another run's pass says so: the scanning is counted with the run that took the pass. To time
  multi-sample or Multi-Exposure against a plain scan, those runs need their own pass anyway, so
  their totals hold the whole cost. Test log.txt ends with the same totals.
- **Preview runs** (Kind of run: Preview): a run that makes a preview of the frame as the Preview
  button does (whole frame, no infrared, no dust removal), at the preview resolution or the
  Resolution the run sets, saved as a picture beside the others. Previews never share a pass,
  because their time is what you are testing. With **Autofocus** On, the frame is focused again
  for that run and the time it took is shown apart; Off leaves the lens where it is (no
  focusing, so the picture is only as sharp as the lens already was). "As it is" focuses the way
  the Focus setting says. Each result shows its time in large type first ("4.2 s · autofocus
  1.6 s · scanning 2.4 s"), and **Time per run** gives every run's time a frame, its autofocus and
  scanning times, and marks the fastest in bold. Autofocus now also shows in the time of an
  ordinary scan ("autofocus 0:02, scanning 0:41").
- **Profile made from this strip** (Colour of negatives, for colour negatives): test frames are not
  measured for your rolls' film profiles, so this makes a temporary profile from the test's own
  frames. Every frame is measured on its first whole pass; once all ticked frames are done, the
  profile is made and the runs that asked for it are finished with it (their time adds "plus 0:02
  for the strip's profile afterwards"). It needs at least 3 frames, and a whole strip is better.
  With fewer, or on slides, those runs are saved with each frame on its own colour and say why.
  While the test runs they wait in a hidden folder inside the test's folder, which is removed at
  the end. If the test is stopped before the profile is made, the log says those files have each
  frame on its own colour. The profile is not kept: to keep one, scan the roll and use
  "Save this roll's film..." as usual.
- **Turn and crop** of the frame apply to the test as they would to a scan of it.
- A run that fails is marked and the queue goes on with the next; a lost scanner ends it.
- A finished picture stretched "each frame on its own" changes all over when dust is removed,
  a little, because the dust shifts what counts as its darkest and lightest part. To see only
  what the cleaner touched, set "Negatives saved as" to "Negative (raw)" on the ordinary
  screen before running the test: the files are then saved as scanned.
- Full-resolution passes are large (about 90 MB each while a test runs, with the infrared).
  They are let go when the queue ends.

## Re-develop RAW files

Everything that happens after the scan — turning a negative into a positive, the colour of
negatives, the tone, the colour space, colour restoration, grain reduction, turning, cropping
and dust removal from the infrared — can be done again from a RAW file (Archive mode's untouched
scan), with other settings, **without scanning again**. Open it from "Re-develop RAW files" at
the top of the ordinary screen, or go to http://127.0.0.1:8765/develop.

1. **RAW files**: pick a roll (its `RAW/` folder is found for you) or type a folder. Each file is
   listed with the film it was scanned as, its size, and a note when it has no infrared (so no
   dust removal) or no kept exposures (so film profiles cannot be used on it).
2. **How to develop**: film, colour of negatives (each frame on its own, or a film profile), tone,
   colour space, restore faded colours, grain reduction, and dust removal on or off (built-in, or
   the Nikon ICE helper when it is set up). These are used for the pictures below and for every
   file you develop. The driver's older dust method is not here: it works by scanning the film
   again, which re-developing does not do.
3. **Look**: one file shown four ways, to compare — **As scanned**, **Dust removed**, **Film
   correction only**, and **Dust removed + film correction** (what is saved). Click a spot to see
   it at full size in all four. Turn and crop here; the crop a frame was scanned with is where it
   starts.
4. **Save**: develops the ticked files (or all of them) into a new folder of its own,
   `Re-developed/<date and time>/` inside the roll's folder, with the same names as the RAW files
   and a `Develop log.txt`. "Also save the in-between steps" keeps the dust-removed-as-scanned file
   and the no-dust-removal positive too, to compare. **Nothing is ever overwritten** — each run is
   a new folder, and a name that is somehow taken gets a number.

What to know:

- A RAW file developed with the same settings it was scanned with comes out identical to the
  `Positive/` file the scan made (so the feature changes nothing unless you ask it to).
- **Film profiles need the pass's exposures.** New scans keep them (in the RAW file and in the
  roll's `FilmColour.json`), so their RAW files can use a film profile. A file scanned before the
  app kept them is developed with each frame on its own, and the page says so.
- A file scanned before the app wrote the film type into RAW files is developed as the film set on
  the scanning screen, and the page says so; pick the film on the page if it is wrong.
- The second-pass dust method (the driver's) cannot be used here: it needs the scanner. Built-in
  and Nikon ICE both work on the file's infrared and so are fine.

## The Mac going to sleep

A sleeping Mac drops the USB connection to the scanner, so a scan stops halfway. While the app
is working on anything (thumbnail, preview, scan, a batch, the test queue, re-develop) it keeps
the Mac awake with macOS's own `caffeinate` tool, and lets go 10 minutes after the last job, so
the Mac is still awake when you feed the next strip. The first time, the Activity list says
"the Mac will not go to sleep by itself while the app is working". The screen may still go
dark; that does not matter.

What this cannot stop:

- **Closing the lid on battery** always puts the Mac to sleep. Keep the lid open while scanning.
  On power, a Mac with its lid closed only stays awake with an external display attached.
- Choosing Sleep from the Apple menu, or a low battery.

If the Mac does sleep anyway, the Activity list says how long it slept. The app then checks the
scanner. If the connection dropped, it says so and connects again by itself (up to three
tries). The frame that was being scanned is not saved: scan it again. If reconnecting does not
work, switch the scanner off and on and press Connect.

With an app from before this change, start it as `caffeinate -i python coolscan.py` instead.

## When something goes wrong

The Activity list says what happened and what to do, with the driver's own wording in square
brackets (send that part along if you report a problem).

- **The scanner stopped answering**: check power and the USB cable, then press Connect.
- **There is no film**: feed the strip in again. Nikon's driver rewinds a strip that has sat idle
  for a while. The app notices that the film moved, takes it in again if it can, and finds the
  frames again before the next preview or scan.
- **Autofocus failed**: press Pick, click a part of the big picture with clear detail, try again.
- **Banding after the same strip sat in the scanner for a long time**: Nikon's help says the
  scanner calibrates itself each time film is changed, so press Eject and feed the strip again.
  (nkscan has no separate "calibrate the scanner" command.)
- **Hardware error**: switch the scanner off and on, then press Connect.

## Tested so far

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
