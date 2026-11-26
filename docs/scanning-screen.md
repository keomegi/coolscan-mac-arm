# The scanning screen, in detail

Every box, button and setting of the main screen. The ? on each box in the app has the short version.

## The screen

The bar at the top says what the scanner is doing and holds the main buttons: Thumbnail,
Preview and the blue **Scan** button, with Connect, Load and Eject kept quieter beside them
because they are needed less often. Under them, like Nikon Scan's status line, a row of small labels
shows the settings the next scan will use ("Next scan: Colour negative · 2900 dpi · dust removal:
on, IR Clean ..."), plus an orange "frame 3: own settings" when a frame has settings of its own.
While a strip is scanned it says "This scan, frame 3" and shows that frame's settings, its own
ones in orange; a setting changed meanwhile is for the next scan and does not change the line.
The next line shows the latest line of the **Activity**
list. The Activity button at the right (or the A key) unfolds the whole list over the page;
Esc or the button folds it again, and the app remembers which way you left it. When something
goes wrong (a frame failed, dust removal failed, the scanner was lost) the line turns red and
the Activity button shows a red count of problems you have not seen yet; the count goes when
you open the list. While something runs, the same line shows the progress ("Scanning frame 4
(2 of 2) · 60%") and the times, Stop becomes available, and the frame being scanned gets a green
edge and a "Scanning…" mark among the small pictures.

On the right are the film and the picture:

- **Frames**: the frames of the strip as cards, two or more to a row, in frame order, frame 1 first,
  each with
  its number and tick box. They appear by themselves after the thumbnail pass. The ← → keys move
  along them in that order. Left of each picture the card shows what the frame will be scanned
  with (resolution, dust removal, focus, exposure) and a label: **Same as all frames**, or
  **Own settings** in orange, with the frame's own values in yellow.
- **Ticks only select.** Tick the frames you want and press Scan ("Scan 2 selected"); with none
  ticked, Scan takes the whole strip. Once the scan has started the ticks are taken off.
- **The scan queue**: while a strip is scanned, each card shows its place (Scanning…, Next, 2nd in
  line, Done, Failed), and a waiting frame has "↑ Sooner". Two buttons appear above the cards:
  **Add to scan** puts the ticked frames at the end (a frame scanned already is scanned again) and
  **Take out of scan** removes the ticked frames that are still waiting. A tick by itself changes
  nothing. The scan keeps going throughout; file numbers follow the order the frames are scanned in.
- **Scan again with the same settings?** A frame scanned on this strip already, with nothing
  changed since (settings, its own settings, crop, frame position, roll folder), is asked about
  before it is scanned again: Cancel, scan only the other frames, or scan again.
- **Editing a scanned frame**: a frame scanned on this strip can still be cropped and turned on
  this screen. The change is kept for its saved picture as a Library edit (in the folder's
  Library.json, like a crop made in the Library), so the file is never changed. A crop reaching
  past the part that was saved is cut to what the file holds; press Scan to scan the frame again
  with the new crop. RAW files are left alone (they are edited on the Re-develop page).
- **Settings for one frame**: "Change for this frame…" on a card, "Settings for this frame…"
  under the big picture, or the S key, opens a list of the settings a frame can have of its own
  (resolution, multi-sample, Multi-Exposure, dust removal and how, grain, restore colours, focus,
  exposure, white balance, negatives as, negative tone, colour space). Each starts at "Same as all
  frames", which follows the settings on the left; pick a value to scan only this frame with it.
  With several frames ticked, "Also for the other ticked frames" sets them together. "Same as all
  frames again" takes them all back. A frame's own settings belong to the strip in the scanner:
  another strip, and they are gone. They are never saved as the settings on the left, and a
  test-mode run ignores them.
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
  or corners, or drag its middle to move it (no need to press Crop first). Above the picture,
  **Shape** keeps the crop to 3:2, 4:3, 5:4, square, 16:9 or the frame's own shape while you
  drag (Free lets it be any shape; with Free, hold Shift while dragging to keep the shape the
  crop has now). Choosing a shape fits the crop to it straight away, and ⇄ turns the crop
  between wide and tall. Next to it is the crop's size in pixels at the scan resolution. It is
  an estimate, worked out from a 35 mm frame as the LS-40 scans it (about 4320 pixels across at
  2900 dpi), so the saved file can differ by a few pixels.
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
- **Keys**: ← → look at another frame, X ticks it, ⌘A ticks all, Space enlarges, C crops,
  [ and ] turn the frame, P preview scan, T thumbnail, ⌘Return scan, A the Activity list, Esc
  leaves crop or enlarge. "Keyboard shortcuts" under the big picture (or the ? key) lists them.
  Tab reaches the frames as one stop (the frame you are looking at); the arrow keys then move
  between frames.

On the left are the settings, in boxes that fold away. Click a box's title to fold or unfold it;
a folded box shows its main choices in a few words, and the app remembers which boxes you keep
open. The boxes run in the order you use them: Saved settings, Film, Thumbnail & preview (what the two quick looks are, each explained in a line), Focus & exposure, Roll, then Scan quality, Save as, File names and When a strip is done. At first Film, Thumbnail & preview, Focus & exposure and Roll are open. "New roll…" sits next to the roll list. The "?" on a box shows a short explanation of what is in it.

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
- **Focus & exposure** (open at first): the settings used for every preview and scan, and under
  them "Do now, on the frame you are looking at": Autofocus, Auto exposure, Calibrate and Reset,
  each with a line saying what it does to the settings above. Only Calibrate and Reset change
  them. While Focus or Exposure is kept from one frame (after Calibrate, or chosen by hand), Reset
  is lit up in orange and a line says what is overridden.
  - **Calibrate**: autofocus and auto exposure on that frame, then switches the settings
    to keep that focus and exposure for every preview and scan (like locking AE in Nikon Scan).
  - **Autofocus** / **Auto exposure**: either one on its own. "Pick" lets you click the big
    picture to choose the focus point. "Reset" goes back to focusing and exposing each frame on its own.
  - See **How focusing works** just below for exactly when the scanner focuses.

### How focusing works

The thumbnail pass never focuses. Focusing happens only before a preview or a scan, and how
often depends on the **Focus** setting (Focus & exposure box):

- **Autofocus each frame** (the default). The first time a frame is previewed or scanned,
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
- **Reset**: back to "Autofocus each frame" and each frame exposed on its own; the
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
- **No separate infrared file.** The option to save the infrared as its own `_IR.tif` was taken
  out on 2026-11-24. The infrared is still read for dust removal, and Archive mode keeps it inside
  the RAW file as a 4th channel. `_IR.tif` files from earlier scans are still understood.
- **Dust & scratch removal**: uses the infrared picture to find dust and scratches. "Dust
  removal by" picks the method.
  It is never done on ordinary (silver) B&W film, and choosing **Kodachrome** switches it off:
  Kodachrome's cyan dye blocks infrared much as dust does (Nikon's own ICE on the LS-40 had the
  same limit), so the cleaner can paint over parts of the picture. You can tick it again for
  Kodachrome to try it; check the result closely. The methods:
  - *IR Clean* (the default; called *Built in* until 26 November 2026): the app's own cleaner,
    named after the infrared (IR) picture it works from, tuned on five real negatives until its
    result lined up with Nikon Scan's Digital ICE on the same scans. It leaves a pixel exactly
    as scanned unless its infrared is clearly darker than the rest of the film (about one pixel
    in eight on those frames), gives light dirt its light back, and fills heavy dirt from the
    clean film around it. It adds no grain. It runs in the app right after the pass, about a
    second a frame.
  - *nkscan* (called *Driver* until 26 November 2026): the cleaner of nkscan, the scanner
    driver, which the app used before. The older method. It rebuilds about
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
  with it on, dust removal is always done by the app ("nkscan" can only clean one pass).

- **Negatives as**: Positive (default, like Nikon Scan), Negative (raw) or Both.
- **Archive mode** (the switch at the top of the window, on by default): saves two folders in the target folder. `RAW/` holds the untouched scan with
  the infrared as a 4th channel (RGBI, like SilverFast's archive files). `Positive/` holds the
  finished picture, inverted if it is a negative, with dust removed if ticked. Both come from
  one pass, so a frame takes about half as long as it did with two passes. Every RAW file now
  also carries a note of how it was scanned (the film, the pass's exposures, the turn and crop),
  so it can be developed again later without the scanner (see **Re-develop RAW files**).
  The app starts in Archive mode every time; to have it start the way you left the switch, untick
  **Start in Archive mode** in Settings, on the Scanning tab. Files
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
- **Settings window** (the ⚙ button in the top bar, or ⌘,): the things set once and rarely
  changed, in one window over the page, on seven tabs: **Saving** (Save as, File names),
  **Thumbnail and preview**, **Scanning** (while a strip is scanned, when it is done, frame
  positions, Archive mode), **Crop** (auto crop and learning from your crops), **Auto rotate** (and
  learning from your turns), **Notifications** and **Logs**. Each group opens with a line saying
  what it is; its "?" shows the longer explanation. Learning happens on your Mac only; each of the
  two learning groups has a switch to stop it and **Reset to default**, which goes back to the
  model that came with the app. The **Preview** group can switch off
  autofocus and exposure metering for previews: quicker (autofocus took 7 to 18 s a frame on the
  LS-40), but the preview may be soft or wrongly exposed, and such a frame gets a red "AF off" /
  "AE off" mark like in Nikon Scan. The scan itself still focuses and meters as set. **Notify me when a strip is done** (Notifications tab) tells
  you when a batch finishes or stops on an error, with the number of frames saved and the roll.
  Tick it, then pick any mix of three ways; the box explains how to set up each one. Settings
  saved before this box existed switch it on by themselves if a way was already filled in.
  - *Banner on this Mac*: a notification on the Mac.
  - *Run a shortcut*: type the name of one of your own shortcuts from the Shortcuts app (the
    field suggests them). The app runs it and hands it the message as text, so the shortcut
    decides how it reaches you: an email to yourself, a reminder with an alert, a push app.
  - *ntfy topic*: the free ntfy phone app. It goes through a server on the internet, so only
    the roll number is sent, not your film notes.

  "Send a test" tries them. (The iMessage option is gone: a message to your own account does
  not ring your phone.)

  These options live in `coolscan_app/extras/notify.py`, which is optional and listed in `.gitignore`,
  so it is never committed. Without that file the app runs as usual and the box says the
  messages are not installed; put the file back and restart the app to get them again.
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
you choose otherwise under "Negatives as".

[Back to the README](../README.md)

## Auto rotate: the mark on a frame, and the strip's frame direction

- A frame auto rotate turned by itself has a purple border and a **↻ Auto rotated** mark on its card. It is not asked about: when a turn is wrong, turn the frame back with the turn buttons, and auto rotate learns from that more strongly.
- Before guessing each frame, auto rotate looks at the whole strip: when most frames that are clearly the right way up or upside down come out upside down (normal for some films, not a mistake), every frame is turned 180° and a purple bar over the frames says 'Frame direction found' and asks whether the frames below look correct (Accept / Reject). The answer is kept for the roll, so later strips of the same roll are not asked about unless one comes out the other way round (Reject turns the strip the other way and guesses each frame again). Not sure: the strip is kept as the last one was.
- The "Turn every frame 180°" switch is hidden from the Film card unless "Show “Turn every frame 180°” on the main screen" is ticked in Settings ⚙.
- Auto rotate uses a ready-made model ([auto-turn.md](auto-turn.md)). On thumbnails it only suggests a turn (too few pixels to be sure); a preview scan it turns by itself when sure; and the scan itself is looked at once more just before its files are written, so the saved files are very likely the right way up even when the thumbnail was not.
- A frame already scanned keeps the turn its files were saved with: auto rotate never turns its picture on screen again.
- Settings ⚙ › **Learn from the whole Library**: once, when every Library picture is the right way up, keeps each finished picture with its turn, plus its RAW file (as the scanner reads it, and made as small as a thumbnail pass), then learns.
- In Archive mode each RAW file keeps the frame's piece of the thumbnail pass inside it (a private TIFF tag, about 65 KB; other programs ignore it), as the scanner sent it and turned like the file. Auto rotate learns from it too (a frame with few pixels), on the scanning screen and with Learn from the whole Library. RAW files from before have none: for those the RAW is made that small.
