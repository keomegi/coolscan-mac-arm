"""The older check scripts in checks/scripts, run one by one with the pretend scanner.

Each runs in a throwaway copy of the app and a throwaway HOME. A script passes when it exits
cleanly and none of its checks says FAIL. Scripts that need an optional part (notify.py, test
mode) are skipped when that part is not here.
"""
import re
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env, needs_notify, needs_testmode

SCRIPTS = ROOT / "checks" / "scripts"

# script: (what it checks, extra mark)
CHECKS = {
    "screen_test.py": ("frame pictures, crop, saved settings, dust route", None),
    "next_test.py": ("rolls, notifications, renumbering, errors", needs_notify),
    "name_test.py": ("file names", None),
    "roll_optional_test.py": ("roll folders off, overwriting with a confirm step", None),
    "taken_test.py": ("'Photo taken' dates", None),
    "batch_a_test.py": ("scan times, stall check, lens hold, strips, going back to a roll", None),
    "profile_test.py": ("film profiles", None),
    "multi_test.py": ("Multi-Exposure", None),
    "develop_test.py": ("re-develop from RAW files, the 4-way view", None),
    "export_test.py": ("the Export dialog: where, names, TIFF/PNG/DNG/JPEG/HEIC, sizes, metadata, presets, background job", None),
    "library_test.py": ("library: folders, marks and dates written into files, search, copies, routes", None),
    "filmcolour_test.py": ("the roll's colour of negatives: FilmProfile.json, shown in the Library, film base, scanning", None),
    "crop_scan_test.py": ("scanning only the crop", None),
    "auto_crop_test.py": ("auto crop", None),
    "croplearn_test.py": ("the learning auto crop", None),
    "turnlearn_test.py": ("the learning auto rotate", None),
    "frame_fit_test.py": ("frames fitted to the gaps, nudged by hand; Auto crop trim and sides", None),
    "autoturn_verdict_test.py": ("auto rotate's own turns: Accept / Reject; strip upside down first; learn from the whole Library", None),
    "turnbase_test.py": ("auto rotate's ready-made model: read and run, thumbnails suggest, scans turned before saving, Library", None),
    "queue_edit_test.py": ("scan queue changes while scanning; scanned frames edited as Library edits", None),
    "preview_skip_test.py": ("previews without autofocus or metering, marked on the frame", None),
    "frame_settings_test.py": ("settings of one frame: scanned with them, never saved as the shared ones", None),
    "keepawake_test.py": ("keeping the Mac awake, noticing a sleep", None),
    "thumb_reuse_test.py": ("reusing the strip's thumbnail pass", None),
    "applog_test.py": ("the app log, scanner codes in plain words, log history, Clear cache", None),
    "stall_test.py": ("driver log in the app folder, warning when the scanner goes quiet", None),
    "notify_optional_test.py": ("the app with or without notify.py", None),
    "archive_default_test.py": ("Archive mode on by default and at start, no separate _IR.tif", None),
    "bw_film_test.py": ("B&W film: no dust removal on silver, chromogenic B&W cleaned", None),
    "kodachrome_dust_test.py": ("Kodachrome: dust removal off when chosen, can be ticked again", None),
    "check_dust.py": ("the dust cleaner on a made-up negative", None),
    "models_test.py": ("other Coolscan models with the pretend scanner; the LS-40 left as it was", None),
    "gpu_test.py": ("the graphics chip (MLX) gives the same pictures as numpy; skips without MLX", None),
    "test_test.py": ("test mode: queue, shared passes, files, failures", needs_testmode),
    "strip_test_test.py": ("test mode over a whole strip", needs_testmode),
    "preview_test_test.py": ("test mode's preview runs", needs_testmode),
}
# these print what the app does, for reading; they pass when they run to the end
SMOKE = ["run_next.py", "watch_next.py", "dust_app_next.py", "enh_app.py"]
FAIL = re.compile(r"^\s*(FAIL\b|FAILED\b|\d+ FAILED)", re.M)


def _params():
    out = []
    for name, (_, mark) in CHECKS.items():
        if (SCRIPTS / name).exists() or mark is not None:
            out.append(pytest.param(name, marks=[mark] if mark else [], id=name))
    return out


def _run(app_copy, home, name):
    r = subprocess.run([sys.executable, str(app_copy / "checks" / "scripts" / name)], cwd=app_copy,
                       env=clean_env(home), capture_output=True, timeout=900)
    out = r.stdout.decode(errors="replace").replace("\0", "") + r.stderr.decode(errors="replace")
    return r.returncode, out


@pytest.mark.parametrize("name", _params())
def test_check_script(name, app_copy, home):
    rc, out = _run(app_copy, home, name)
    tail = out[-3000:]
    assert rc == 0, f"{name} stopped with code {rc}:\n{tail}"
    fails = [line.strip() for line in out.splitlines() if FAIL.match(line)]
    assert not fails, f"{name}: " + "\n".join(fails)


@pytest.mark.parametrize("name", [n for n in SMOKE if (SCRIPTS / n).exists()])
def test_script_runs_through(name, app_copy, home):
    rc, out = _run(app_copy, home, name)
    assert rc == 0, f"{name} stopped with code {rc}:\n{out[-3000:]}"


def test_every_script_is_run():
    """A new check script in checks/scripts must be added to CHECKS or SMOKE above (helpers excepted)."""
    helpers = {"cs_next.py", "roll_sim.py"}
    here = {p.name for p in SCRIPTS.glob("*.py")} - helpers
    missing = here - set(CHECKS) - set(SMOKE)
    assert not missing, f"not run by the suite: {sorted(missing)}"
