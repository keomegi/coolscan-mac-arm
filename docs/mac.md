# The Mac side: the app window, the graphics chip, sleep

## As a Mac app (its own window and Dock icon)

Set the folder up first if you have not ([setup.md](setup.md)). Then build the app once, in the
Coolscan folder (needs Apple's Command Line Tools; `xcode-select --install` if `swiftc` is missing):

```
bash mac/build_app.sh                 # into ~/Applications
bash mac/build_app.sh /Applications   # or anywhere else you like
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
- Don't run `python3 coolscan.py` in Terminal at the same time: only one can use port 8765 and the
  scanner. If you do, the app says the port is busy.

Quitting the Terminal way (Ctrl-C) or with `kill` also always lets go of the port.

## The Mac going to sleep

A sleeping Mac drops the scanner's USB connection, so the app keeps the Mac awake while it works
and for 10 minutes after. It cannot stop a closed lid on battery: keep the lid open while scanning.
If the Mac sleeps anyway, the app reconnects by itself; scan the frame that was in progress again.

## The graphics chip (MLX)

With MLX installed, grain reduction, the conversion into the colour space
(slides through Nikon's profile too) and full-size screen pictures run on the graphics chip (dust removal stays on the processor, which is as quick);
without it they run on the processor as before. The pictures are the same either way (a few
pixels can be one level apart out of 65,535; `python checks/scripts/gpu_test.py RAW.tif`
compares the two on one of your own RAW files). The app log's first line says which is in use.
To keep everything on the processor: `COOLSCAN_GPU=0 python3 coolscan.py`.
`bash setup.sh` installs MLX with the rest.

[Back to the README](../README.md)
