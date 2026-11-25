# Test mode, in detail

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
  each run's settings, files and time.
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
  what the cleaner touched, set "Negatives as" to "Negative (raw)" on the ordinary
  screen before running the test: the files are then saved as scanned.
- Full-resolution passes are large (about 90 MB each while a test runs, with the infrared).
  They are let go when the queue ends.

[Back to the README](../README.md)
