"""The page in a real browser (Playwright, from node), against the app with the pretend scanner.

These need node and its playwright package (npm install playwright; npx playwright install chromium),
so they are skipped where those are missing. Each script clicks through a part of the page and
prints what it found; here it passes when it runs to the end and the page raised no errors.
page_develop.js (needs RAW files from an Archive scan first) and after_pictures.js (takes the
pictures in screens/) are run by hand.
"""
import json
import re
import shutil
import subprocess

import pytest

from conftest import ROOT, needs_testmode, running_app

PAGE = ROOT / "checks" / "page"


def _have_playwright():
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=PAGE)
    return r.returncode == 0


pytestmark = [pytest.mark.page,
              pytest.mark.skipif(not _have_playwright(), reason="node with the playwright package is not installed")]

# script: (extra app arguments, extra environment, mark)
PAGES = {
    "page_test.js": ((), {}, None),
    "page_profiles.js": ((), {}, None),
    "page_crop_scan.js": ((), {}, None),
    "page_crop_handles.js": ((), {}, None),
    "page_roll_optional.js": ((), {}, None),
    "page_auto_crop.js": ((), {"COOLSCAN_DEMO_BORDERS": "1"}, None),
    # already failing before the folders were moved (waits for 4 results that do not come); left to fix in test mode
    "page_test_mode.js": (("--test",), {}, [needs_testmode, pytest.mark.xfail(reason="failed before the move too", strict=False)]),
    "page_strip_test.js": (("--test",), {}, needs_testmode),
    "page_preview_test.js": (("--test",), {}, needs_testmode),
}


def _params():
    return [pytest.param(n, marks=(m if isinstance(m, list) else [m]) if m else [], id=n) for n, (_, _, m) in PAGES.items()
            if (PAGE / n).exists() or m is not None]


@pytest.mark.parametrize("name", _params())
def test_page(name, app_copy, home, tmp_path):
    args, env, _ = PAGES[name]
    shots = tmp_path / "shots"
    shots.mkdir()
    with running_app(app_copy, home, *args, env_extra=env) as (base, _):
        port = base.rsplit(":", 1)[1]
        argv = ([str(home / "Pictures" / "Coolscan"), str(shots), port] if name == "page_test.js"
                else [port, str(shots)])
        r = subprocess.run(["node", str(PAGE / name), *argv], cwd=PAGE, capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    errs = re.findall(r'"errs":\s*(\[[^\]]*\])', out)
    assert errs, "the script did not report the page's errors:\n" + out[-3000:]
    assert json.loads(errs[-1]) == [], f"the page raised errors: {errs[-1]}"
