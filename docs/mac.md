# The Mac side: the app window, the graphics chip, sleep

## As a Mac app (its own window and Dock icon)

Build it once (needs the Xcode Command Line Tools; `xcode-select --install` if `swiftc` is missing):

```
cd ~/Documents/GitHub/nikon_scan_project
mac/build_app.sh
```

That puts **Coolscan.app** in your own Applications folder (`~/Applications`), so Launchpad and
Spotlight find it. Open it, then right-click its Dock icon > Options > Keep in Dock.

- It is the same app in its own window instead of a browser tab. It runs this folder's
  `coolscan.py` with this folder's `.venv`, so updates to the app need no new build, only quitting
  and opening it again. Build again only if you move the folder or `mac/` changes.
- Closing the window quits it. The server behind the page stops with it, the scanner is let go and
  nothing is left on port 8765. If a scan is running, it asks first.
- If the app crashes or is force-quit, the server notices and stops by itself too.
- What Python printed while starting is in `~/Library/Logs/Coolscan.log` (View > Show Start-up Log).
- View > Open in Browser still opens the page in a browser if you want both.
- The icon is drawn by `mac/make_icon.py` (needs Pillow) into `mac/AppIcon.icns`.
- Don't run `python coolscan.py` in Terminal at the same time: only one can use port 8765 and the
  scanner. If you do, the app says the port is busy.

Quitting the Terminal way (Ctrl-C) or with `kill` now also always lets go of the port.

## The Mac going to sleep

A sleeping Mac drops the USB connection to the scanner, so a scan stops halfway. While the app
is working on anything (thumbnail, preview, scan, a batch, the test queue, re-develop) it keeps
the Mac awake with macOS's own `caffeinate` tool, and lets go 10 minutes after the last job, so
the Mac is still awake when you feed the next strip. The first time, the Activity list says
"the Mac will not go to sleep by itself while the app is working". The screen may still go
dark; that does not matter.

What this cannot stop:

- **Closing the lid on battery** always puts the Mac to sleep. Keep the lid open while scanning.
  On power, a Mac with its lid closed only stays awake with an external display attached.
- Choosing Sleep from the Apple menu, or a low battery.

If the Mac does sleep anyway, the Activity list says how long it slept. The app then checks the
scanner. If the connection dropped, it says so and connects again by itself (up to three
tries). The frame that was being scanned is not saved: scan it again. If reconnecting does not
work, switch the scanner off and on and press Connect.

With an app from before this change, start it as `caffeinate -i python coolscan.py` instead.

## The graphics chip (MLX)

With MLX installed, grain reduction, the conversion into the colour space
(slides through Nikon's profile too) and full-size screen pictures run on the graphics chip (dust removal stays on the processor, which is as quick);
without it they run on the processor as before. The pictures are the same either way (a few
pixels can be one level apart out of 65,535; `python checks/scripts/gpu_test.py RAW.tif`
compares the two on one of your own RAW files). The app log's first line says which is in use.
To keep everything on the processor: `COOLSCAN_GPU=0 python coolscan.py`.

[Back to the README](../README.md)
