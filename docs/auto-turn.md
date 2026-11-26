# Auto rotate: which way up a picture goes

Auto rotate uses a ready-made model, **Deep Image Orientation Detection** by Duarte Barbosa
(<https://github.com/duartebarbosadev/deep-image-orientation-detection>, MIT licence): an
EfficientNetV2-S trained on about 190,000 photographs to say whether a picture was turned 0, 90, 180
or 270°.

- Its file (`orientation_model_v2_0.9882.onnx`, 80 MB) is **not in the app or in git**. The app fetches it
  once from the author's GitHub release into `~/.coolscan-crops/base/` and checks its SHA-256.
- It runs on MLX (the GPU of an Apple Silicon Mac), read by a small ONNX reader in
  `coolscan_app/imaging/turnbase.py`; nothing new is installed.
- Without MLX, or until the file is there, auto rotate uses what it learned from your own turns
  (`imaging/turnlearn.py`), as before. `COOLSCAN_TURN_BASE=off` switches the ready-made model off.

## What to expect

| Picture | What auto rotate does | How often right (357 pictures of rolls 1 to 11, each tried all four ways round) |
|---|---|---|
| Thumbnail piece (about 96 px) | suggests only, never turns by itself | 94% |
| Preview scan, Library picture (250 px and up) | turns by itself when 85% sure | 99.2% at 200 px, 99.6% at 300 px, 99.8% at 448 px |
| The scan itself, just before its files are written | turns by itself when 85% sure | 99.8% (3 wrong of 1428; 2 of them "sure") |

So frames can look the wrong way up after the thumbnail pass and still be saved the right way up:
the scan is looked at again. A frame you turned or cropped by hand is never turned again. A test run
(test mode) is never turned. The strip's "in upside down" guess uses the same model on the thumbnail
pieces and needs 7 in 10 clear frames to agree.

The numbers were measured in the cloud on the processor (2026-11-25), with pictures made the way the
app makes them. The model gave the same scores on MLX as on onnxruntime (to 6 decimals). Speed on the
Mac's GPU is not measured yet.

The app's own learning (from your turns) reached 79% on the same pictures, below leaving every picture
as it is (95%), which is why the ready-made model took over.

Check: `checks/scripts/turnbase_test.py` (part A needs MLX, onnxruntime and
`COOLSCAN_TURN_BASE=/path/to/orientation_model_v2_0.9882.onnx`; skipped otherwise).
