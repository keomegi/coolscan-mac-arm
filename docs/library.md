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
  to compare with the file as it is, **Reset** to take the crop and turn off, and **Export…**.
- **Export…** (ticked pictures, or the picture on show) opens the Export dialog; see
  [Exporting](#exporting) below. **Export with previous** repeats the last export without it.
- Ticked pictures: **Auto crop** sets a crop on each one where it finds a border; **Reset crop &
  turn** takes them off.
- A RAW file is not cropped here. To make a picture again from its RAW file, use the Re-develop page.
- Copies made before 2026-11-25 (when crop and turn made new files straight away) stay in
  `Edited/` and are listed as versions of their picture.

## Exporting

**Export…** makes copies for use elsewhere. Every copy has the crop, turn, the roll's colour and
the date taken put in; the pictures themselves are never changed. The dialog is laid out like
Lightroom's: presets down the left, sections that fold on the right.

- **Presets**: *For Lightroom* (TIFF 16-bit, as scanned, all metadata, then opened in Lightroom),
  *Full size TIFF*, *JPEG to share*, *Small JPEG for email* are built in; they set the file, size
  and metadata and leave the folder and names alone. **Add** keeps everything in the dialog under
  a name of your own; **Update** and **Remove** change or delete yours. The dialog opens with what
  was used last. Presets are kept in `~/.coolscan-export.json`.
- **What to Export**: the ticked pictures, the whole roll (also the **Export roll…** button above
  the pictures) or all rolls; the finished pictures, their RAW scans (Archive mode's untouched
  scans, infrared channel kept in TIFF 16-bit) or both; with or without your crop and turn (RAW
  scans are then framed like their pictures); with or without the roll's colour of negatives.
  RAW scans go into a `RAW` folder in each roll's folder, or get `_RAW` in the name.
- **Export Location**: a chosen folder (optionally a subfolder; roll folders kept or everything in
  one folder) or each roll's `Edited/` folder (TIFF 16-bit only; those copies show in the app).
  A file already there: new name `(2)`, skip, or replace. Among the scans themselves names get
  `_modified` and nothing is ever replaced.
- **File Naming**: original name, custom name and number, date taken and number, roll and frame,
  or a template of `{name} {text} {seq} {date} {roll} {frame}`. The example shows the first name.
- **File Settings**: TIFF 16-bit (the default), TIFF 8-bit, PNG (8-bit), JPEG, HEIC or DNG, and
  the colour space (as scanned, sRGB, Adobe RGB, ProPhoto RGB). JPEG and HEIC are made by the Mac's
  own converter (`sips`) with the chosen quality. DNG is a linear DNG of the finished picture with
  its colour matrix and a straight tone curve; it is not the scanner's unprocessed data.
- **Image Sizing**: full size, or long edge / width and height / megapixels / percentage; never
  larger unless "Don't enlarge" is unticked. Resolution (pixels per inch) as scanned or typed.
- **Metadata**: everything (date, caption, tags, people, stars, scanner, film), all except people,
  date and caption only, or none; optional creator and copyright.
- **After Export**: nothing, Show in Finder, or Open in Lightroom.

The export runs in the background with a progress bar; **Stop** ends it after the picture in hand.
Code: `coolscan_app/exporting.py`, `web/export.js`; checks `checks/scripts/export_test.py` and
`checks/page/page_export.js`. Not yet tried on a Mac at the time of writing: JPEG and HEIC through
`sips`, and how Lightroom shows the DNG.

## Colour of negatives, kept with each roll

Each roll folder can have a `FilmProfile.json` that says where its colour negatives get their
colour from. The scanning screen (Film box, "Colour from") and the Library (the **Colour of
negatives** row under a roll's description) both read and write it, so they always agree, and
going back to a roll later brings it back.

A film's three colour layers do not gain density at the same rate, and in old film (Orwocolor,
Svema) they have drifted far apart, most of all in the thin parts: two numbers per colour then
leave thin, underexposed frames with a strong cast. So with "This roll's own frames" and "Film base
picked" the roll also gets a **colour curve**: the roll's frames together say how red, green and blue
run against each other from the clearest to the densest film (each frame's measurement in
`FilmColour.json` keeps how much film there is at each density, `q`). A roll scanned before the app
kept that has its RAW files looked at once, the first time it is shown. The curve assumes the roll
as a whole has no colour of its own (a roll of nothing but grass would come out less green).

- **Each frame on its own** (no file, the old way): each frame's colour is worked out from that
  frame alone.
- **This roll's own frames**: from the frames of this roll measured so far (needs 3 or more;
  frames are measured when they are scanned as colour negatives). It follows as more are scanned.
- **Film profile: …**: a saved film profile. Its numbers are copied into the roll's file, so the
  roll keeps them even if the profile is deleted later.
- **Film base picked**: for a film with an unusual mask (Orwocolor, Svema colour, expired film).
  Open a picture, press **Film base…** (key B): the whole frame is shown as scanned; click on clear
  film, between two frames or along the edge, where nothing was exposed. The base sets where each
  colour starts; how far each colour goes still comes from the roll's measured frames (or that
  frame, if fewer than 3 are measured). A spot that is as dense as the picture is refused, and an
  uneven spot or one denser than parts of the picture gets a warning.

In the Library this works like crop and turn: **the picture files are never changed**. Pictures
that have a RAW file (Archive mode) are shown, and exported, with the roll's colour; **Show
original** shows them as saved. The app matches each saved picture with its RAW file colour by
colour (it also finds whether the flat or the Nikon Scan tone was used) and puts the roll's colour
in place of the frame's own. A picture made with colour restoration or grain reduction does not
match its RAW file closely enough and is shown as saved; the picture's info panel says why. Pictures
without a RAW file, RAW files themselves and other versions (Re-developed, Edited) are shown as they
are. Tones the saved picture had already cut off at pure black or white cannot come back; to get
them, develop the RAW file again on the Re-develop page.

**One picture on its own.** In the viewer, **Own colour** colours that picture on its own instead
of with the roll's colour (ticked pictures: the **Colour** row). It is kept in `Library.json`
(`"colour": "own"` or `"roll"`); the file is not changed.

**Another film scanned into the roll by mistake.** Every colour negative scanned is compared with
the roll's frames from earlier strips (once the roll has 3 or more): the colour of the film base,
that is how much denser the thinnest film is in green and blue than in red. When a strip's is
clearly another (more than 0.20 in density on its first frame, 0.12 from the second on), an orange
bar appears above the frames and the log says so. Until you answer, that strip's frames are
coloured each on their own (never with the roll's colour) and kept out of the roll's measurements
(marked `"own"` in `FilmColour.json`, which the Library follows). The bar offers **New roll…**
(frames already scanned from the strip stay where they are), **Same film** (the marks come off, the
roll's colour is used again, and the Library shows the frames already scanned with it) and **Keep
in this roll, own colour**. A frame scanned with "Scan only the cropped part" is not measured, so
it is not compared. The limits (0.20 and 0.12) are a first guess, not yet tried on real rolls.

## Auto crop that learns from your crops

Auto crop starts with a built-in rule (straight edges, dark or clear bands at the sides). It also
learns how **you** crop, and once that is closer to your crops than the rule, Auto crop crops your way.

- **What it learns from**: every crop you draw by hand on the scanning screen (whether or not "Crop
  each frame automatically" is on) and every crop kept in the Library. Clearing a crop teaches nothing.
- **When**: by itself after every 10 new crops, in the background, or now with **Learn from my
  crops** (Settings, Crop tab, and the Library's Crop row for ticked pictures).
- **What it knows about each frame**: the pixels along each side, where the built-in rule would
  cut, the film type (colour negative, slide, B&W, chromogenic B&W), whether it is the raw scan or
  the finished picture, and whether it has no colour (measured, so B&W is recognised even when
  the film type is unknown). So it can learn, say, a tighter crop on B&W.
- **One model**: a small neural network on numbers measured along each side, learned on the
  processor in seconds and tried on crops it was not trained on (5 rounds). It is not used until
  it beats the built-in rule. (Until 26 November 2026 a simple weighted model and a picture
  network (CNN) were learned beside it; they were taken out to keep learning light: on 11 rolls
  the small network's edges were within 1% of the hand crop 94.9% of the time, the CNN's 95.7%.)
- **Where it is kept**: what it **learned** is in the app's folder, in `models/`:
  `crop_model.json` (numbers only: the model's weights, how near it got to your crops, when it
  learned and how long it took; no pictures, no file names, no crops). It goes to GitHub with the app, so
  a new copy of the app starts with what was learned. What it **learns from** stays outside the app's
  folder, in `~/.coolscan-crops/` on the Mac, and never goes to GitHub: `hand-*.npz` (each crop drawn
  on the scanning screen with a tiny greyscale copy of the frame, about 10-20 KB), `library/` (small
  greyscale copies of Library pictures, so learning again is quick), `steps.jsonl` (every crop step,
  see below) and `history.json` (each time it learned). Delete both to start again.
- **Someone else who downloads the app** starts with your model and it keeps adapting to *their*
  crops: from 3 crops of theirs it learns again starting from your model's weights (pulled back
  towards them, so a few odd crops cannot undo them), and uses that adapted version only where it
  lands nearer their crops than your model does (or neither, if on their crops your model is no
  better than the built-in rule). Their adapted model is kept in their own `~/.coolscan-crops/`, never
  over the file in `models/`. Only the computer with a `models/.owner` file (your Mac; the file is
  not in git) writes `models/`. When a newer model comes with an update, theirs adapts again from it.
- **Thumbnails, previews and scans**: a crop is kept as shares of the frame, and each frame is made
  the same size (200 pixels) before it is looked at, so crops drawn on a thumbnail, a preview or a
  scan all count the same, and Auto crop works on each.
- **Negatives and finished pictures**: raw scans of negatives (colour, B&W, chromogenic) are turned
  into positives (their brightness mirrored) before they are learned from or cropped, so what it
  learns from finished pictures in the Library also crops negatives on the scanning screen.
- **Learning pictures** (the small greyscale copies): all kept by default, so it learns better as
  crops are added. **Delete learning pictures** (Settings, under Auto crop learning) deletes them;
  what was learned stays and Library crops are read from the Library again next time, but crops
  from the scanning screen can then not be learned from again. "Delete learning pictures after
  learning" (off by default) does that each time it has learned.
- **Every step, not only the final crop**: auto crop's box, each crop you keep (Done) and each crop
  you draw on the scanning screen is noted. The final crop is what it learns to make; the earlier
  steps you moved away from are learned as "not here". An auto crop kept unchanged teaches nothing and
  is left out. The Learning report shows the steps: how many pictures took more than one, which way
  you usually move auto crop's edges, and the way to the last few crops (auto → saved → saved).
- **Learning report** (Settings, Crop tab): a few plain lines (what it learned from, what is in
  use and how far its edges are from yours against the rule); under **More detail**, what the
  last learning took (seconds, processor time, the app's memory, GPU memory, GPU or processor);
  how long cropping a frame with it takes; and the earlier runs, to see it getting better.
- **Switch it off**: Settings, Crop tab, "Auto crop learns from my crops". Off: none of your crops
  is kept and nothing more is learned; Auto crop goes on using what it has. It all happens on your
  Mac: no picture and nothing learned from one is sent anywhere.
- **Reset to default** (same tab): forgets what was learned from your crops on this Mac and goes
  back to the model that came with the app from GitHub. On the Mac that makes the app's models
  (it has `models/.owner`) there is no other default, so the button changes nothing there. Auto crop's "cuts further in by" setting
  is not added to a learned crop (your own margin is in it already); "Left and right only" still is.

## Auto rotate that learns from your turns

Auto rotate learns which way up your pictures go (as they are, a quarter left, a quarter right, upside
down) from the turns you make, and turns new frames the same way.

- **What it learns from**: every frame you turn by hand on the scanning screen (thumbnails, previews,
  scans) and every picture you turn in the Library; also frames and pictures you crop by hand, since
  you looked at them and kept their turn on purpose. A turn auto rotate made itself and you left as it
  was is not learned from. Raw negatives are mirrored into positives first, so what it learns in the
  Library works on the scanning screen too.
- **One picture, one example; the latest turn wins**: a turn made on the thumbnail or preview and kept
  through the scan is one example (taken from the scan once it is made); turning it again after the
  scan replaces it, and turning the saved picture later in the Library replaces that.
- **Pictures already the right way up count too**, less surely: a frame scanned without a turn (0.4;
  0.7 on a strip where you turned other frames, so you looked), a Library picture you opened and left
  (0.3). A turn by hand (1) always replaces them. The kinds of turn are evened out (halfway) so the
  many upright pictures do not swamp the few turned ones.
- **"Turn every frame 180°"**: turns are counted from how the frame was shown, so with it on, a
  horizontal frame left alone counts as right after the roll's 180°, and a vertical frame learns its
  own quarter turn on top. Auto rotate then gives each frame its own turn on top of the roll's.
- **How**: each of the four ways a frame could be turned is looked at and scored for "the right way
  up" by a picture network (CNN) that looks at the pixels and at measured numbers (brightness from
  top to bottom, where the fine detail is, upright or lying lines, and which way you usually turn).
  It learns from 24 pictures on, on the GPU with MLX; without MLX nothing is learned from your turns.
  (A simple model and a small neural network were learned beside it until 26 November 2026; on real
  turns the picture network was clearly the best, so they were taken out.) "Auto rotate learns from my
  turns" (Settings, Auto rotate tab) switches the learning off without switching auto rotate off, and
  **Reset to default** there goes back to the turn model that came with the app.
- **When it turns by itself**: only once it is right more often than leaving frames as they are, only
  when it is 90% sure, and only when its sure answers were right at least 95% of the time on the
  check. Otherwise it suggests the turn under the picture (with a "Turn it" button), and in the
  Library viewer. The Library's **Auto rotate** (ticked pictures) turns those it is sure of and lists the rest.
- **Where it is kept**: like crop learning: `models/turn_model.json` (numbers only) and `turn_cnn.npz`,
  written only on the computer with `models/.owner`; small greyscale copies in `~/.coolscan-crops/turns/`
  (never on GitHub). Someone who downloads the app starts from your model and it adapts to their turns
  (kept in their own folder), used only where it is right more often on their pictures.
- **Settings**: "Auto rotate learns from my turns" (on by default; off: nothing kept, learned, turned or
  suggested), **Learn from my turns**, and its **Learning report** (how often each model is right,
  how often when sure, what learning took on the GPU). **Delete learning pictures** deletes its copies too.

[Back to the README](../README.md)

## Photo taken, whole roll

Under a roll's description: type a year (`1988`) or a day (`1988-02-06` or `6.2.1988`) and press
**Set for all N pictures**. A year keeps each scan's month, day and time; a day puts the pictures one
second apart from 12:00, in name order. The date goes into the app's list (`Library.json`) and into the
files' tags (EXIF and XMP; pixels untouched). RAW files and files Lightroom has written into are not
changed: their date is kept in the app's list and put into exported copies. A picture whose date was
changed by itself (or with a few ticked others) keeps its own date when the roll's date is set again,
unless "also the pictures with a date of their own" is ticked.
