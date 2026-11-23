"""Scanning only the crop: the rectangle handed to the driver, what is metered and focused,
the files, the picture on screen, and the cases that still scan the whole frame."""
import sys, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)
def fresh(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=290, ir=False, clean=False, scan_log=False), **kw})
    app.connect(); app.thumbnail(); return app, out
def only(out, pattern="*.tif"):
    return sorted(p for p in out.rglob(pattern))
def spy(app):
    """Note every rectangle the driver is asked to scan, meter or focus on."""
    calls = []
    s = app.session
    for name in ("scan_frame", "meter_frame", "focus_frame"):
        real = getattr(s, name)
        def wrap(frame, *a, _real=real, _name=name, **kw):
            calls.append((_name, tuple(frame), kw.get("exposures"), kw.get("focus")))
            return _real(frame, *a, **kw)
        setattr(s, name, wrap)
    return calls
def pixels(path):
    a = tifffile.imread(path)
    return a if a.ndim == 2 else (a if a.shape[-1] in (3, 4) else np.moveaxis(a, 0, -1))

print("A from the crop to the rectangle")
app, out = fresh()
whole = app.discovery.frames[0]
check("the demo frame is (top, left, bottom, right) = (600, 50, 13400, 900)", tuple(whole) == (600, 50, 13400, 900), str(whole))
app.set_crop(0, (0.25, 0.1, 0.75, 0.6))
rect, windowed = app._scan_rect(0, whole)
# across 0.25..0.75 is the feed: 600 + 0.25*12800 .. 600 + 0.75*12800; down 0.1..0.6 is the sensor from X 900 down
check("across the picture is along the feed (Y), down is along the sensor from its highest X",
      windowed and rect == (3800, 390, 10200, 815), str(rect))
check("Archive mode keeps the whole frame", app._scan_rect(0, whole, archive=True) == (whole, False))
app.settings["crop_scan"] = False
check("with 'Scan only the cropped part' off the whole frame is scanned", app._scan_rect(0, whole) == (whole, False))
app.settings["crop_scan"] = True
app.set_crop(0, (0.0, 0.0, 1.0, 1.0))
check("a crop that is the whole frame scans the frame", app._scan_rect(0, whole) == (whole, False))
check("a frame without a crop scans the frame", app._scan_rect(1, app.discovery.frames[1]) == (app.discovery.frames[1], False))

print("B what the driver is asked for")
app, out = fresh(negatives="negative")   # files as scanned, so only the noise differs between passes
app.scan([0]); full = pixels(only(out)[0])
app.set_crop(1, (0.25, 0.1, 0.75, 0.6))
calls = spy(app)
app.scan([1])
scans = [c for c in calls if c[0] == "scan_frame"]
meters = [c for c in calls if c[0] == "meter_frame"]
focuses = [c for c in calls if c[0] == "focus_frame"]
frame1 = tuple(app.discovery.frames[1])
rect1, _ = app._scan_rect(1, frame1)
check("one pass, over the crop only", len(scans) == 1 and scans[0][1] == rect1 and rect1 != frame1, str(scans))
check("exposure is metered on the whole frame and handed to the pass", len(meters) == 1 and meters[0][1] == frame1
      and scans[0][2] == {"red": 1200, "green": 900, "blue": 700}, str(meters))
check("focus is found on the whole frame (each frame's own focus) and the pass keeps it",
      len(focuses) == 1 and focuses[0][1] == frame1 and isinstance(scans[0][3], (int, str)), str(focuses))
cut = pixels(only(out)[1])
h, w = full.shape[:2]
r0, r1, c0, c1 = round(0.1 * h), round(0.6 * h), round(0.25 * w), round(0.75 * w)
check("the saved file is the size of the crop", abs(cut.shape[0] - (r1 - r0)) <= 1 and abs(cut.shape[1] - (c1 - c0)) <= 1,
      f"{cut.shape[:2]} vs {(r1 - r0, c1 - c0)}")
# same film, different noise and dust: compare with the same part of a whole-frame scan, cut afterwards
app2, out2 = fresh(crop_scan=False, negatives="negative")
app2.set_crop(1, (0.25, 0.1, 0.75, 0.6))
app2.scan([1])
old = pixels(only(out2)[0])
same = old.shape == cut.shape
diff = float(np.median(np.abs(old.astype(np.int32) - cut.astype(np.int32)))) if same else -1
check("it shows the same part of the film as a whole-frame scan cut afterwards", same and diff < 800,
      f"shapes {old.shape} {cut.shape}, median difference {diff:.0f} of 65535")
log = [l for l in app.log if "frame 2" in l or "saved" in l]
check("the activity list says only the crop was scanned",
      any("scanning only the cropped part" in l for l in log) and any("(only that part was scanned)" in l for l in log), "\n".join(log))
t = app.state()["tiles"][1]
check("the picture on screen stays the whole frame (thumbnail) and says why",
      t["src"] == "thumbnail" and "Only the crop was scanned" in t["info"] and t["saved"], t["info"])
check("the crop is still there for the next scan", t["crop"] == (0.25, 0.1, 0.75, 0.6))

print("C infrared, dust, cases that scan the whole frame")
app, out = fresh(ir=True, clean=True)
app.set_crop(0, (0.1, 0.2, 0.6, 0.9))
calls = spy(app)
app.scan([0])
files = {p.name: pixels(p).shape[:2] for p in only(out)}
check("with infrared: one pass over the crop, picture and infrared file the same size",
      len(files) == 2 and len(set(files.values())) == 1 and [c[1] for c in calls if c[0] == "scan_frame"] == [app._scan_rect(0, app.discovery.frames[0])[0]], str(files))
check("the infrared is metered too", [c for c in calls if c[0] == "meter_frame"] and True)
app, out = fresh(archive=True)
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
calls = spy(app)
app.scan([0])
check("Archive mode: the pass is the whole frame", [c[1] for c in calls if c[0] == "scan_frame"] == [tuple(app.discovery.frames[0])])
app, out = fresh(crop_scan=False)
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
calls = spy(app)
app.scan([0])
check("option off: the pass is the whole frame, the file is still cropped",
      [c[1] for c in calls if c[0] == "scan_frame"] == [tuple(app.discovery.frames[0])] and not [c for c in calls if c[0] == "meter_frame"])
app, out = fresh(exposure_mode="same")
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
calls = spy(app)
app.scan([0])
m = [c for c in calls if c[0] == "meter_frame"]
check("'same exposure' mode meters once on a whole frame, as before", len(m) == 1 and m[0][1] == tuple(app.discovery.frames[0]), str(m))
app, out = fresh(samples=2)  # the demo reads lines 16 times, so this stays one pass; make it average passes
app.caps.max_samples = 1
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
calls = spy(app)
app.scan([0])
sc = [c[1] for c in calls if c[0] == "scan_frame"]
check("averaged passes all go over the crop", len(sc) == 2 and len(set(sc)) == 1 and sc[0] != tuple(app.discovery.frames[0]), str(sc))
app, out = fresh(multi_exposure=2)
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
calls = spy(app)
app.scan([0])
sc = [c[1] for c in calls if c[0] == "scan_frame"]
check("both Multi-Exposure passes go over the crop", len(sc) == 2 and len(set(sc)) == 1 and sc[0] != tuple(app.discovery.frames[0]), str(sc))

print("D focus point")
app, out = fresh(focus_mode="auto")
app.settings["focus_point"] = [0.2, 0.3]   # across, down on the picture as the scanner sends it
check("nkscan gets (x from the low X edge, y along the feed)", app._point() == (0.7, 0.2), str(app._point()))
f = app.discovery.frames[0]
r = (f[0] + 1280, f[1], f[0] + 6400, f[3])   # a crop from 0.1 to 0.5 across, full height
p = app._point(f, r)
check("on a crop the point is moved into the crop's own fractions", abs(p[0] - 0.7) < 1e-6 and abs(p[1] - 0.25) < 1e-6, str(p))
check("a point outside the crop is kept at its edge", app._point(f, (f[0] + 6400, f[1], f[2], f[3]))[1] == 0.0)
app.set_crop(0, (0.1, 0.0, 0.5, 1.0))
calls = spy(app)
app.scan([0])
sc = [c for c in calls if c[0] == "scan_frame"]
check("'auto' focus mode with a point: the pass over the crop focuses on that point of the crop",
      sc and isinstance(sc[0][3], tuple) and abs(sc[0][3][0] - 0.7) < 1e-6 and abs(sc[0][3][1] - 0.25) < 0.01, str(sc))

print("E film profile notes")
app, out = fresh(film="negative")
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
app.scan([0, 1])
notes = list(out.rglob("FilmColour.json"))
import json
frames = json.loads(notes[0].read_text()).get("frames", {}) if notes else {}
check("a frame scanned as a crop is not noted for a film profile, a whole one is", len(frames) == 1, str(list(frames)))

print()
print("all passed" if not bad else f"{len(bad)} FAILED: {bad}")
