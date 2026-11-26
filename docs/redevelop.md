# Re-develop RAW files, in detail

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
   colour space, restore faded colours, grain reduction, and dust removal on or off (by IR Clean). These are used for the pictures below and for every
   file you develop. nkscan's dust method is not here: it works by scanning the film
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

**From the Library.** Tick pictures of one roll and press **↻ Re-develop ticked…**: the Re-develop
page opens that roll's RAW folder with their RAW files ticked, ready for "Develop N ticked files".
A picture with no RAW file (only Archive mode keeps one) cannot be developed again; tag it
**🎞 Rescan** instead (key **S** on the picture on show), and the **🎞 Rescan list** at the top of
the Library lists those pictures by roll, with their frame numbers, to scan again. Both are kept in
the folder's `Library.json` only, never in the picture files.

What to know:

- A RAW file developed with the same settings it was scanned with comes out identical to the
  `Positive/` file the scan made (so the feature changes nothing unless you ask it to).
- **Film profiles need the pass's exposures.** New scans keep them (in the RAW file and in the
  roll's `FilmColour.json`), so their RAW files can use a film profile. A file scanned before the
  app kept them is developed with each frame on its own, and the page says so.
- A file scanned before the app wrote the film type into RAW files is developed as the film set on
  the scanning screen, and the page says so; pick the film on the page if it is wrong.
- The second-pass dust method (nkscan's) cannot be used here: it needs the scanner. IR Clean
  works on the file's infrared and so is fine.

[Back to the README](../README.md)
