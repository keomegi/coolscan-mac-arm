# Checks for the Coolscan app

None of these needs the scanner: they use the app's pretend scanner (`--demo`). They say nothing
about the LS-40 itself; only a scan on the real scanner shows that.

The folder is called `checks`, not `tests`, because a Mac does not tell `tests/` from test mode's
`Tests/` folder.

## Running them

```
pip install -r requirements-dev.txt     # once: numpy, tifffile, pytest
python -m pytest -m quick               # seconds: does it load
python -m pytest                        # everything (a few minutes; the browser ones only with node + playwright)
python -m pytest -k crop                # only what has "crop" in its name
```

GitHub runs the same after every push (`.github/workflows/tests.yml`, on Linux with Python 3.15).
There `notify.py` and test mode are not in the repository, so their checks are skipped; they run
on your Mac where those files are.

| File | What it checks |
| --- | --- |
| `test_build.py` | every Python file compiles, every module loads, the page's scripts parse, `coolscan.py --help`, the app starts with `--demo` and serves its pages, `--test`, and (where nkscan is installed) that the driver calls the app makes still exist |
| `test_optional.py` | the app loads, starts and scans with `extras/notify.py` and test mode taken out |
| `test_checks.py` | runs each script in `scripts/` (below) in a throwaway copy of the app with a throwaway HOME |
| `test_pages.py` | runs the browser scripts in `page/` against the app (needs node and its playwright package; skipped without them) |
| `conftest.py` | shared helpers: copying the app, starting it on a free port |

Everything writes into throwaway folders: never into `~/Pictures/Coolscan`, `~/.coolscan.json`
or the app folder.

## The scripts

The older checks are plain scripts, and each still runs on its own, e.g.
`python checks/scripts/name_test.py`. Each ends with "all passed" or says what failed.

| Script | What it checks |
| --- | --- |
| `screen_test.py` | frame pictures, crop, saved settings, dust route |
| `next_test.py` | rolls, notifications, renumbering, errors (needs `extras/notify.py`) |
| `name_test.py` | file names |
| `taken_test.py` | "Photo taken" dates |
| `batch_a_test.py` | scan times, stall check, lens hold, strips, going back to a roll |
| `profile_test.py` | film profiles, on a made-up roll (`roll_sim.py`) and in the app |
| `multi_test.py` | Multi-Exposure |
| `develop_test.py` | re-develop from RAW files, the 4-way view (also takes a real RAW file as an argument) |
| `crop_scan_test.py` | scanning only the crop |
| `auto_crop_test.py` | Auto crop and the Crop box |
| `keepawake_test.py` | keeping the Mac awake, noticing a sleep, connecting again |
| `thumb_reuse_test.py` | reusing the strip's thumbnail pass |
| `applog_test.py` | the app log (JSON Lines in logs/), scanner codes in plain words, deleting old logs, Clear cache |
| `notify_optional_test.py` | the app with or without `notify.py` |
| `check_dust.py` | the dust cleaner on a made-up negative |
| `test_test.py`, `strip_test_test.py`, `preview_test_test.py` | test mode (kept out of git with it) |
| `run_next.py`, `watch_next.py`, `dust_app_next.py`, `enh_app.py` | print what the app does in made-up situations, for reading |

`cs_next.py` lets the scripts reach every part of the package by one name (`cs.App`, `cs.shrink`).
A new script must be added to the list in `test_checks.py`; a check there fails otherwise.

## The page in a browser

`page/*.js` drive the page with Playwright. `test_pages.py` starts the app for each; by hand:
start the app with a throwaway HOME and `python coolscan.py --demo --no-browser --port 8795`, then

```
node checks/page/page_test.js <that HOME>/Pictures/Coolscan <folder for pictures> 8795   # the ordinary screen
node checks/page/page_profiles.js 8795 <folder for pictures>                             # film profiles (a fresh start of the app)
node checks/page/page_crop_scan.js 8795 <folder for pictures>                            # "Scan only the cropped part"
node checks/page/page_crop_handles.js 8795 <folder for pictures>                         # changing a crop by its sides, corners or middle
node checks/page/page_auto_crop.js 8795 <folder for pictures>                            # Auto crop (start the app with COOLSCAN_DEMO_BORDERS=1)
node checks/page/page_develop.js 8795 <folder for shots>                                 # re-develop (scan a strip in Archive mode first)
node checks/page/page_test_mode.js 8795 <folder for pictures>                            # the test page (--test as well)
node checks/page/page_strip_test.js 8795 <folder for pictures>                           # the test page over a whole strip (--test)
node checks/page/page_preview_test.js 8795 <folder for pictures>                         # preview runs on the test page (--test)
```

Each prints what it found; `"errs": []` at the end means the page raised no errors.
`page_test_mode.js` already failed before the folders were reorganised (it waits for 4 results
that do not come); it is marked as expected to fail until test mode's page is looked at.
`after_pictures.js <port> <folder>` takes the pictures in `screens/`.
