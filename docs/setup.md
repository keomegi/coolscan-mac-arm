# Setting up, step by step

From nothing to a first scan. About ten minutes, most of it waiting for downloads.

## What you need

- A Mac with Apple Silicon (M1 or newer). The app is not made for Intel Macs or Windows.
- A Nikon Coolscan that connects by USB. The app is made for the **Coolscan IV ED (LS-40)**;
  see [other-models.md](other-models.md) for the rest.
- **Python 3.13 or newer.** Check in Terminal with `python3 --version`. If it is older or missing,
  install the current one from <https://www.python.org/downloads/macos/>
  (or `brew install python` if you use Homebrew).
- Only for the Mac app with its own window: Apple's **Command Line Tools**
  (`xcode-select --install`, a few minutes, free).

You do **not** need to download the separate `nkscan` program or clear its quarantine flag
(`xattr -d com.apple.quarantine …`). The app talks to the scanner through nkscan's Python
package, which step 2 installs for you.

## 1. Get the app

Open Terminal and put the folder wherever you like:

```
git clone https://github.com/keomegi/coolscan-mac-arm.git coolscan
cd coolscan
```

(Or download the ZIP from GitHub, unpack it, and `cd` into the folder.)
Every command below is typed in this folder.

## 2. Set it up (once)

```
bash setup.sh
```

This makes a private Python environment in the folder (`.venv`) and installs what the app needs
into it: `nkscan` (the scanner driver), `numpy`, and `mlx` (picture work on the graphics chip).
Nothing is installed outside the folder. It is safe to run again, for example after an update.

## 3. Try it without the scanner (optional)

```
python3 coolscan.py --demo
```

A browser tab opens on http://127.0.0.1:8765 with a pretend scanner, so you can look around.
Ctrl-C in Terminal quits.

## 4. Connect the scanner

1. Plug the scanner into power and into the Mac by USB (a USB-A to USB-C adapter or hub is fine).
2. Put the film holder in: the SA-21 strip feeder for strips, the MA-20/21 for mounted slides.
3. Switch the scanner on and wait until its light stops blinking.
4. Quit anything else that may be using the scanner (VueScan, Image Capture, another copy of this app).

## 5. Start the app: pick one of two ways

Both run the same app with the same settings and the same scans folder. Use only one at a time.

### A. As a Mac app (its own window and Dock icon)

Build it once:

```
bash mac/build_app.sh
```

This puts **Coolscan.app** in your own Applications folder (`~/Applications`). From then on, open
**Coolscan** from Launchpad or Spotlight like any app. Closing the window quits it. To keep it in
the Dock: while it runs, right-click its icon > Options > Keep in Dock.

The app runs the code in the folder from step 1, so keep that folder. If you move it, build again.

### B. From Terminal with Python

```
python3 coolscan.py
```

The page opens in your browser at http://127.0.0.1:8765. Ctrl-C in Terminal quits.

| Add | What it does |
| --- | --- |
| `--demo` | pretend scanner, to try the screens |
| `--no-browser` | do not open a browser tab |
| `--port 9000` | another port, if 8765 is taken |
| `--test` | test mode, if it is installed ([test-mode.md](test-mode.md)) |

`python3 coolscan.py` finds the folder's `.venv` by itself. If you prefer to turn the environment
on yourself, `source .venv/bin/activate` and then `python coolscan.py` does the same.

## 6. First scan

1. The top of the screen should say the scanner is connected. If not, press **Connect**.
2. Feed a strip in. The thumbnails appear by themselves.
3. Pick the **Film** type (colour negative, slide, B&W).
4. Click a frame and press **Preview** to look at it, then **Scan**, or **Scan all frames** for the strip.
5. The pictures are in `~/Pictures/Coolscan`, one folder per roll. **Scanned pictures** at the top
   of the screen shows them.

Every box in the app has a **?** that explains it. [scanning-screen.md](scanning-screen.md) has
the long version.

## Updating later

```
git pull
bash setup.sh
```

Then quit the app and open it again. Coolscan.app needs a new build only if something in `mac/`
changed or you moved the folder.

## If setup goes wrong

- **"Python 3.13 or newer was not found"**: install Python (see the top), open a new Terminal
  window, run `bash setup.sh` again.
- **"swiftc is missing"** when building the app: run `xcode-select --install`, then build again.
- **"numpy is missing" or "nkscan is missing"** when starting: run `bash setup.sh`.
- **The scanner is not found**: check power and USB, quit other scanning software, switch the
  scanner off and on, press Connect.
- **A newer nkscan misbehaves**: the app was last checked on the real scanner with nkscan 0.20.0.
  To go back to it: `.venv/bin/python -m pip install nkscan==0.20.0`.
- **Port 8765 busy**: another copy is running. Quit it, or `pkill -f coolscan.py`.
- **Coolscan.app shows an error at start**: View > Show Start-up Log (`~/Library/Logs/Coolscan.log`).

More in the README's [When something goes wrong](../README.md#when-something-goes-wrong).

[Back to the README](../README.md)
