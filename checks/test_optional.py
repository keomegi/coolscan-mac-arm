"""notify.py and test mode are optional and kept out of git: the app must load, start and scan without them.
These run on a copy of the app with those files taken out, so they check that even where they are here."""
import json
import subprocess
import sys

import pytest

from conftest import clean_env, copy_app, fetch, running_app

pytestmark = pytest.mark.quick


@pytest.fixture
def bare_app(tmp_path):
    d = tmp_path / "bare"
    d.mkdir()
    return copy_app(d, without_optional=True)


def test_extras_folder_may_be_empty(bare_app):
    assert sorted(p.name for p in (bare_app / "coolscan_app" / "extras").iterdir()) == ["__init__.py"]


def test_loads_without_optional_parts(bare_app, home):
    code = ("import coolscan_app.server, coolscan_app.app as a; "
            "assert a.notifier is None and a.HAVE_TESTMODE is False; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=bare_app, env=clean_env(home),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


def test_starts_without_optional_parts(bare_app, home):
    with running_app(bare_app, home, "--test") as (base, proc):
        state = json.loads(fetch(base + "/api/state")[1])
        assert state["notify"] is False
        assert fetch(base + "/")[0] == 200
        assert fetch(base + "/test")[0] == 404
    assert "Test mode is not installed" in proc.output


@pytest.mark.quick
def test_notify_optional_script_without_notify(bare_app, home):
    r = subprocess.run([sys.executable, "checks/scripts/notify_optional_test.py"], cwd=bare_app,
                       env=clean_env(home), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "notify.py is MISSING" in r.stdout and "FAILED" not in r.stdout, r.stdout[-2000:]
