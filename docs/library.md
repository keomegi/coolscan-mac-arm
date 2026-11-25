# Library and Auto crop, in detail

## Library: crop and turn without changing the files

In the Library (http://127.0.0.1:8765/library), crop and turn work like Lightroom: **the picture
files are never changed**. The crop and turn are kept as settings in the folder's `Library.json`,
so they are still there after you quit and open the app again, and the Library shows each picture
with them (grid, viewer and the strip of thumbnails along the bottom of the viewer).

- The buttons are on a bar under the picture: **⟲ Left / Right ⟳**, **Auto crop** (key A: finds
  the picture inside the frame, leaving out the film's edges, holder and gap, the same way as on
  the scanning screen), **Crop…** (key C: drag, then **Done**; the whole picture is shown while you
  crop, so a crop can be made larger again; **No crop** takes it off), **Show original** (key O)
  to compare with the file as it is, **Reset** to take the crop and turn off, and **Export copy**.
- **Export copy** / **Export edited copies** (ticked pictures) write new TIFFs with the crop and
  turn put in, into the folder's `Edited/` folder, with the same date, profile, stars, tags and
  caption. Nothing is overwritten (a second export gets a number). **JPEG** exports include the
  crop and turn too.
- Ticked pictures: **Auto crop** sets a crop on each one where it finds a border; **Reset crop &
  turn** takes them off.
- A RAW file is not cropped here: **Crop from RAW…** opens it on the Re-develop page.
- Copies made before 2026-11-25 (when crop and turn made new files straight away) stay in
  `Edited/` and are listed as versions of their picture.

## Auto crop that learns from your crops

Auto crop starts with a built-in rule (straight edges, dark or clear bands at the sides). It also
learns how **you** crop, and once that is closer to your crops than the rule, Auto crop crops your way.

- **What it learns from**: every crop you draw by hand on the scanning screen (whether or not "Crop
  each frame automatically" is on) and every crop kept in the Library. Clearing a crop teaches nothing.
- **When**: by itself after every 10 new crops, in the background, or now with **Learn from my
  crops** (Additional settings, and the Library's Crop row for ticked pictures).
- **What it knows about each frame**: the pixels along each side, where the built-in rule would
  cut, the film type (colour negative, slide, B&W, chromogenic B&W), whether it is the raw scan or
  the finished picture, and whether it has no colour (measured, so B&W is recognised even when
  the film type is unknown). So it can learn, say, a tighter crop on B&W.
- **Three models**, all learned each time, and the one closest to your crops on crops it was not
  trained on (5 rounds) is used: a simple weighted model (works from about 8 crops), a small neural
  network, and a picture network (CNN) that looks at the pixels itself (tried from 12 crops; it
  usually needs 100 or more to win; it runs on the GPU with MLX and is left out without MLX). A
  bigger model is only picked when it is clearly (10%) better than the smaller one. None of them is
  used until it beats the built-in rule.
- **Where it is kept**: `~/.coolscan-crops/` on the Mac: `model.json` (what was learned, a few KB),
  `cnn.npz` (the picture network's weights, about 100 KB, when it is the one used), `history.json`
  (each time it learned), `hand-*.npz` (each hand crop as a tiny greyscale copy, about 10-20 KB)
  and `library/` (small copies of Library pictures, so learning again is quick). Delete the folder
  to start again.
- **Learning report** (Additional settings): how many crops, which model is in use, how far each
  model's edges were from yours on average and how often within 1%, against the rule; what the
  last learning took (seconds, processor time, the app's memory, GPU memory, GPU or processor);
  how long cropping a frame with it takes; and the earlier runs, to see it getting better.
- **Switch it off**: Additional settings, "Auto crop learns from my crops". Off: nothing is kept
  or learned, and Auto crop uses the built-in rule only. Auto crop's "cuts further in by" setting
  is not added to a learned crop (your own margin is in it already); "Left and right only" still is.

[Back to the README](../README.md)
