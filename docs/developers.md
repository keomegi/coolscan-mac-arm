# How the app is put together, and its checks

## How the app is put together

`coolscan.py` only starts the app (and switches to the folder's `.venv` if it was started with
another Python). The app itself is the `coolscan_app` folder next to it, one file per job:

```
setup.sh          makes .venv and installs requirements.txt (--dev: requirements-dev.txt too)
coolscan.py       the starter:  python3 coolscan.py [--demo] [--test] [--port N] [--no-browser]
mac/              Coolscan.app: Coolscan.swift (the window), build_app.sh, the icon
coolscan_app/
  app.py            the scanner session: connect, thumbnail, preview, focus, exposure, film watcher
  server.py         the web page's server and the start-up
  settings.py       the settings and how they are kept between runs
  framesettings.py  settings for one frame
  rolls.py          file names, roll folders, roll numbers, film descriptions, film profiles
  library.py        Scanned pictures: browsing, stars, tags, crop and turn kept as settings
  exporting.py      the Export dialog's copies
  croplearning.py   the learning auto crop, in the app
  turnlearning.py   the learning auto rotate, in the app
  applog.py         the app's own log (logs/, one file per day)
  keepawake.py      keeps the Mac from going to sleep while a job runs, notices when it slept anyway
  scanner/          talking to the scanner
    driver.py         the stall check, and the driver's failures in plain words
    models.py         what differs between Coolscan models
    strip.py          where each frame sits on the strip
    demo.py           the pretend scanner (--demo, and the tests)
    scanning.py       scanning frames and saving them, scan log, scan times
  imaging/          the pictures
    files.py          writing TIFF and PNG files, reading a saved TIFF back
    tiffmeta.py       changing a TIFF's date, caption, keywords and stars without touching pixels
    colour.py         negative to positive, pictures for the screen, colour spaces and profiles
    filmfix.py        the colour of a roll's negatives, kept with the roll
    dust.py           dust and scratch removal from the infrared picture
    enhance.py        grain reduction, faded-colour restoration, lining passes up, two exposures together
    gpu.py            the heavy picture work on the graphics chip (MLX), when it is installed
    autocrop.py       finding the picture inside a frame (Auto crop)
    croplearn.py      the learning auto crop: learns from your crops (a small neural network, numpy)
    turnbase.py       which way up a picture goes, from a ready-made model
    turnlearn.py      ...and from your own turns
    develop.py        re-developing finished pictures from RAW scans
  extras/           optional parts, listed in .gitignore and never committed
    notify.py         banner, shortcut and ntfy messages; without it the "Tell me" options are hidden
    testmode.py       test mode (--test); without it --test opens the ordinary screen
  web/              the pages: index (scanning), library (Scanned pictures) with export.js,
                    develop (Re-develop), style.css; test mode's page is optional like testmode.py
models/           what the learning auto crop has learned (committed)
checks/           the tests
docs/             the long documentation
```

The page files are read from disk on every request, so a change to `web/` needs only a reload of
the page. A change to a `.py` file needs the app quit and started again. Coolscan.app runs this
folder's code, so it needs a new build only when `mac/` changes or the folder moves.

## Tests

The tests need no scanner: the app's pretend scanner stands in. They run on GitHub's Linux machines after
every push (the "Tests" workflow, `.github/workflows/tests.yml`), and on your Mac with:

```
bash setup.sh --dev                      # once: adds tifffile and pytest to .venv
source .venv/bin/activate
python -m pytest -m quick                # seconds: every file compiles, every module loads, the app starts
python -m pytest                         # everything, a few minutes
```

They are in `checks/` (not `tests/`: on a Mac that would be the same folder as test mode's
`Tests/`). See `checks/README.md`. **They say nothing about the LS-40 itself**: only a scan
on the real scanner shows that.

Settings and the current roll are kept in `~/.coolscan.json`.

[Back to the README](../README.md)
