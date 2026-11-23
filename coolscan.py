#!/usr/bin/env python3
"""Coolscan: a small Mac app for Nikon Coolscan film scanners, built on the nkscan driver.

Run:   python coolscan.py            (then the browser opens on http://127.0.0.1:8765)
Try:   python coolscan.py --demo     (a pretend scanner, to try the screens)
Test:  python coolscan.py --test     (test mode: one frame scanned with several sets of settings, to compare)

Needs Python 3.13+ and:  pip install nkscan numpy
The app itself is in the coolscan_app folder next to this file.
"""
import sys

try:
    import numpy  # noqa: F401
except ImportError:
    sys.exit("numpy is missing. Turn the environment on (source .venv/bin/activate), "
             "or install it with:  pip install nkscan numpy")

try:
    from coolscan_app.server import main
except ImportError as e:
    sys.exit(f"The coolscan_app folder that belongs next to coolscan.py is missing or incomplete ({e}).")

if __name__ == "__main__":
    main()
