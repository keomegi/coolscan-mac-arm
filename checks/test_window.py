"""Coolscan.app: the server it starts (`coolscan.py --window`) must end, and free its port, as soon
as the app's window closes, and also on `kill`. The Swift app itself is only built on a Mac
(mac/build_app.sh); here we check the Python side and that the app's files are in place."""
import shutil
import socket
import subprocess
import sys
import time

import pytest

from conftest import ROOT, clean_env, fetch, free_port

pytestmark = pytest.mark.quick


def port_is_free(port):
    """Nothing answers on the port any more."""
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def start_window_mode(app_copy, home):
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-u", "coolscan.py", "--demo", "--window", "--port", str(port)],
                            cwd=app_copy, env=clean_env(home), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    end = time.time() + 30
    lines = []
    while time.time() < end:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if line.startswith("COOLSCAN_URL "):
            url = line.split(" ", 1)[1].strip()
            assert url == f"http://127.0.0.1:{port}"
            return proc, port, url
    proc.kill()
    raise AssertionError("no COOLSCAN_URL line:\n" + "".join(lines))


def finish(proc, seconds=12):
    """Wait for the server to end; returns the rest of what it printed."""
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("the server did not stop")
    return proc.stdout.read()


def test_closing_the_window_stops_the_server(app_copy, home):
    proc, port, url = start_window_mode(app_copy, home)
    status, _ = fetch(url + "/api/state")
    assert status == 200
    assert not port_is_free(port)
    proc.stdin.close()          # what happens when Coolscan.app's window closes (or the app crashes)
    out = finish(proc)
    assert proc.returncode == 0, out
    assert "Coolscan has stopped." in out
    assert port_is_free(port)


def test_kill_stops_the_server(app_copy, home):
    proc, port, url = start_window_mode(app_copy, home)
    proc.terminate()            # SIGTERM: `kill`, logging out
    out = finish(proc)
    proc.stdin.close()
    assert "Coolscan has stopped." in out
    assert port_is_free(port)


def test_mac_app_files():
    mac = ROOT / "mac"
    for name in ("Coolscan.swift", "build_app.sh", "AppIcon.icns"):
        assert (mac / name).is_file(), name
    assert (mac / "AppIcon.icns").read_bytes()[:4] == b"icns"
    swift = (mac / "Coolscan.swift").read_text()
    assert '"--window"' in swift and "COOLSCAN_URL " in swift and "CoolscanFolder" in swift
    assert "CoolscanFolder" in (mac / "build_app.sh").read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_build_script_parses():
    r = subprocess.run(["bash", "-n", str(ROOT / "mac" / "build_app.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_setup_script():
    """setup.sh makes .venv for both ways of starting; the app's builder runs it when .venv is missing."""
    setup = ROOT / "setup.sh"
    assert setup.is_file()
    r = subprocess.run(["bash", "-n", str(setup)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "requirements.txt" in setup.read_text()
    assert "setup.sh" in (ROOT / "mac" / "build_app.sh").read_text()


def test_starter_switches_to_the_folders_environment(tmp_path):
    """Started with a Python that has no numpy, coolscan.py carries on with .venv/bin/python next to it."""
    shutil.copy2(ROOT / "coolscan.py", tmp_path / "coolscan.py")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    fake = tmp_path / ".venv" / "bin" / "python"
    fake.write_text('#!/bin/sh\necho "IN_VENV $COOLSCAN_IN_VENV $@"\n')
    fake.chmod(0o755)
    # -S -I: no site-packages, so numpy cannot be found, as in a Python it was never installed into
    r = subprocess.run([sys.executable, "-S", "-I", str(tmp_path / "coolscan.py"), "--demo"],
                       capture_output=True, text=True, timeout=30)
    assert "IN_VENV 1" in r.stdout and "--demo" in r.stdout, r.stdout + r.stderr
    # and with no .venv it says what to do
    shutil.rmtree(tmp_path / ".venv")
    r = subprocess.run([sys.executable, "-S", "-I", str(tmp_path / "coolscan.py"), "--demo"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "setup.sh" in r.stderr
