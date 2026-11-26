#!/usr/bin/env python3
"""Coolscan: a small Mac app for Nikon Coolscan film scanners, built on the nkscan driver.

Set up once:  bash setup.sh             (makes .venv here and installs nkscan and numpy)

Run:   python3 coolscan.py           (then the browser opens on http://127.0.0.1:8765)
Try:   python3 coolscan.py --demo    (a pretend scanner, to try the screens)
Test:  python3 coolscan.py --test    (test mode: one frame scanned with several sets of settings, to compare)

Or as a Mac app with its own window:  bash mac/build_app.sh  (once), then open Coolscan.

Needs Python 3.13+. The app itself is in the coolscan_app folder next to this file.
Started with another Python, this file carries on with the one in .venv by itself.
"""
import os
import sys


def _use_the_environment():
    """numpy is missing in this Python: carry on in the folder's own .venv, if setup.sh made one."""
    here = os.path.dirname(os.path.abspath(__file__))
    python = os.path.join(here, ".venv", "bin", "python")
    if os.environ.get("COOLSCAN_IN_VENV") or not os.access(python, os.X_OK):
        return
    os.environ["COOLSCAN_IN_VENV"] = "1"    # so this can never go round in a circle
    os.execv(python, [python, os.path.abspath(__file__), *sys.argv[1:]])


try:
    import numpy  # noqa: F401
except ImportError:
    _use_the_environment()
    sys.exit("numpy is missing. Run bash setup.sh in the Coolscan folder first (it makes .venv and installs "
             "what the app needs), then start again.")

try:
    from coolscan_app.server import main
except ImportError as e:
    sys.exit(f"The coolscan_app folder that belongs next to coolscan.py is missing or incomplete ({e}).")

if __name__ == "__main__":
    main()
