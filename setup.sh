#!/bin/bash
# Sets Coolscan up in this folder: makes the Python environment (.venv) and installs what the app needs.
#
#   bash setup.sh            safe to run again; it only adds what is missing
#   bash setup.sh --dev      also what the tests need (pytest, tifffile)
#
# Afterwards:  python3 coolscan.py     in Terminal, or     bash mac/build_app.sh     for Coolscan.app
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

new_enough() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' >/dev/null 2>&1; }

if [ -x .venv/bin/python ] && new_enough .venv/bin/python; then
    echo "Using the environment that is already here (.venv, $(.venv/bin/python --version 2>&1))."
else
    py=""
    for c in python3.15 python3.14 python3.13 python3 \
             /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
             /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if command -v "$c" >/dev/null 2>&1 && new_enough "$c"; then py="$c"; break; fi
    done
    if [ -z "$py" ]; then
        echo "Python 3.13 or newer was not found (the scanner driver, nkscan, needs it)." >&2
        echo "Get it from https://www.python.org/downloads/macos/ (or: brew install python), then run bash setup.sh again." >&2
        exit 1
    fi
    if [ -e .venv ]; then
        echo "The .venv folder here is broken or has a Python older than 3.13. Move it away or delete it, then run bash setup.sh again." >&2
        exit 1
    fi
    echo "Making the environment (.venv) with $("$py" --version 2>&1)…"
    "$py" -m venv .venv
fi

echo "Installing what the app needs…"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
if [ "${1:-}" = "--dev" ]; then
    .venv/bin/python -m pip install -r requirements-dev.txt
fi

echo
echo "Ready. Start Coolscan one of two ways:"
echo "  In Terminal:   python3 coolscan.py          (add --demo to try it without a scanner)"
echo "  As a Mac app:  bash mac/build_app.sh            (once; then open Coolscan from Launchpad or Spotlight)"
