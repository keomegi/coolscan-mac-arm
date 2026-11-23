"""Shared helpers for the tests. None of them needs the scanner: the app's pretend scanner (--demo) stands in.

Everything that writes files runs in a throwaway copy of the app and a throwaway HOME, so the tests
never touch ~/Pictures/Coolscan, the app's saved settings or the app folder itself.
"""
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "coolscan_app"
EXTRAS = PACKAGE / "extras"

# the optional parts, kept out of git: the tests that need them are skipped when they are not there
HAVE_NOTIFY = (EXTRAS / "notify.py").is_file()
HAVE_TESTMODE = (EXTRAS / "testmode.py").is_file() and all((PACKAGE / "web" / n).is_file() for n in ("test.html", "test.js"))
OPTIONAL_FILES = ["coolscan_app/extras/notify.py", "coolscan_app/extras/testmode.py",
                  "coolscan_app/web/test.html", "coolscan_app/web/test.js"]

needs_notify = pytest.mark.skipif(not HAVE_NOTIFY, reason="coolscan_app/extras/notify.py is not here (optional, kept out of git)")
needs_testmode = pytest.mark.skipif(not HAVE_TESTMODE, reason="test mode is not here (optional, kept out of git)")


def copy_app(dest, without_optional=False):
    """A copy of the launcher, the package and the check scripts in dest; returns dest."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copy2(ROOT / "coolscan.py", dest / "coolscan.py")
    shutil.copytree(PACKAGE, dest / "coolscan_app", ignore=ignore)
    shutil.copytree(ROOT / "checks" / "scripts", dest / "checks" / "scripts", ignore=ignore)
    if without_optional:
        for f in OPTIONAL_FILES:
            (dest / f).unlink(missing_ok=True)
    return dest


def clean_env(home):
    env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    env.pop("PYTHONPATH", None)
    return env


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url, timeout=10):
    """(status, body) for a GET; a 404 or 500 comes back as its status, not as an exception."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@contextlib.contextmanager
def running_app(root, home, *args, env_extra=None, wait=30):
    """Start `python coolscan.py --demo --no-browser --port N [args]` from root; yields (base url, process).
    The process's output is in proc.output once the block ends."""
    port = free_port()
    env = clean_env(home)
    env.update(env_extra or {})
    proc = subprocess.Popen([sys.executable, "coolscan.py", "--demo", "--no-browser", "--port", str(port), *args],
                            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        end = time.time() + wait
        while True:
            if proc.poll() is not None:
                raise AssertionError(f"the app stopped at start-up:\n{proc.stdout.read()}")
            try:
                status, body = fetch(base + "/api/state", timeout=2)
                if status == 200 and json.loads(body).get("connected") is not None:
                    break
            except (OSError, ValueError):
                pass
            if time.time() > end:
                raise AssertionError("the app did not answer within %d s" % wait)
            time.sleep(0.2)
        yield base, proc
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        proc.output = out


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def app_copy(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    return copy_app(d)
