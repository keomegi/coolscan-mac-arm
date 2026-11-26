# What has been tested, and on what

## In short

**Run on the real Coolscan IV (LS-40) on an M1 Mac:**

- Thumbnail, preview, scanning, Archive mode and saving (2026-11-22), with five frames compared
  with Nikon Scan's own scans. Several whole rolls of colour negative and B&W film since.
- Coolscan.app in its own window, connecting to the scanner (2026-11-24).
- Multi-sample 2x and 4x and Multi-Exposure, on one colour negative film (2026-11-23): they work,
  with no visible gain over 1x on that film, where grain dominates.
- The graphics chip (MLX) for grain reduction and colour conversion (2026-11-25).
- IR Clean, the app's own dust cleaner, was measured against Nikon's Digital ICE on real colour-negative
  frames from this scanner (per-pixel difference about 0.3%).

**Not run on a real scanner:** any model other than the LS-40, and real failures (the error help
is tested with made-up failures only). Mounted slides are not confirmed anywhere in these notes.

**Everything else** is checked with the app's pretend scanner (`--demo`) and the automatic checks
in `checks/`, which say nothing about the hardware itself.

[Back to the README](../README.md)
