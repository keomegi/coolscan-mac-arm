# How the app is put together, and its checks

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
    croplearn.py    the learning auto crop: learns from your crops (simple model, small neural network)
    cropcnn.py      ...and its picture network (CNN, MLX on the GPU)
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

[Back to the README](../README.md)
