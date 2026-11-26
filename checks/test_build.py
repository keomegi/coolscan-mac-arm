"""Does it build? Python is not compiled ahead, so this is the nearest thing: every file is read by
Python (a typing slip fails here, not halfway through a strip), every module loads, the page's
scripts parse, and the app starts and serves its pages. All of it takes seconds."""
import importlib
import json
import pkgutil
import py_compile
import shutil
import subprocess
import sys

import pytest

from conftest import HAVE_TESTMODE, PACKAGE, ROOT, clean_env, fetch, running_app

pytestmark = pytest.mark.quick

PY_FILES = sorted([ROOT / "coolscan.py", *PACKAGE.rglob("*.py"), *(ROOT / "checks").rglob("*.py")])
PY_FILES = [p for p in PY_FILES if "__pycache__" not in p.parts]
JS_FILES = sorted([*(PACKAGE / "web").glob("*.js"), *(ROOT / "checks" / "page").glob("*.js")])


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_python_file_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)


def _modules():
    sys.path.insert(0, str(ROOT))
    import coolscan_app
    names = ["coolscan_app"]
    for m in pkgutil.walk_packages(coolscan_app.__path__, "coolscan_app."):
        names.append(m.name)
    return names


@pytest.mark.parametrize("name", _modules())
def test_module_imports(name):
    importlib.import_module(name)


def test_package_layout():
    """The folders the app is split into, and that nothing was left behind at the top by a move."""
    for sub in ("scanner", "imaging", "extras", "web"):
        assert (PACKAGE / sub).is_dir(), sub
    top = {p.stem for p in PACKAGE.glob("*.py")}
    assert top == {"__init__", "app", "server", "settings", "rolls", "keepawake", "library", "exporting", "framesettings", "applog", "croplearning", "turnlearning"}, top


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_page_script_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_launcher_help(home):
    r = subprocess.run([sys.executable, "coolscan.py", "--help"], cwd=ROOT, env=clean_env(home),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    for flag in ("--demo", "--test", "--port", "--no-browser"):
        assert flag in r.stdout


def test_demo_app_starts_and_serves_its_pages(app_copy, home):
    with running_app(app_copy, home) as (base, _):
        state = json.loads(fetch(base + "/api/state")[1])
        assert state["demo"] is True and state["connected"] is True
        for path, word in (("/", b"<html"), ("/app.js", b"function"), ("/style.css", b"{"),
                           ("/develop", b"<html"), ("/develop.js", b"function")):
            status, body = fetch(base + path)
            assert status == 200, path
            assert word in body.lower() if word == b"<html" else word in body, path


def test_test_flag(app_copy, home):
    """--test opens the test page when test mode is here, and says it is not installed otherwise."""
    with running_app(app_copy, home, "--test") as (base, proc):
        status, _ = fetch(base + "/test")
    if HAVE_TESTMODE:
        assert status == 200
    else:
        assert status == 404
        assert "Test mode is not installed" in proc.output


def test_nkscan_has_what_the_app_calls():
    """Only where nkscan is installed (the Mac's .venv): the driver calls the app makes still exist."""
    nkscan = pytest.importorskip("nkscan")
    for name in ("list_devices", "Session", "init_logging"):
        assert hasattr(nkscan, name), f"nkscan.{name} is gone; check nkscan's CHANGELOG"
