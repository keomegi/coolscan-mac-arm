"""Auto crop: finding the picture inside a frame (on real scans where they are at hand, and on
made-up ones), and how the app uses it: the button, "Crop each frame automatically", crops set by hand."""
import sys, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
from coolscan_app.imaging.autocrop import find_picture
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

print("A real negatives from the LS-40 (when the project folder is at hand)")
REAL = pathlib.Path("/mnt/project-files/ice-harness/real")
files = [REAL / "scan_1.tiff"] + sorted((REAL / "test").rglob("RAW/Scan*.tif"))
files = [f for f in files if f.exists()]
if not files:
    print("   (not here: skipped)")
for f in files:
    a = tifffile.imread(f)[::4, ::4, :3]
    for label, sub in (("at 1/4 size", a), ("at thumbnail size", a[::22, ::22])):
        r = find_picture([sub[..., c] for c in range(3)])
        ok = r is not None and 0.02 <= r[1] <= 0.08 and 0.92 <= r[3] <= 0.98 and r[0] <= 0.08 and r[2] >= 0.92
        check(f"{f.parent.parent.name}/{f.name} {label}: the dark film edges go, no side loses more than 8%", ok, str(r))

print("B made-up frames")
rng = np.random.default_rng(1)
y, x = np.mgrid[0:300, 0:450].astype(np.float32)
picture = (20000 + 15000 * np.sin(x / 37) * np.cos(y / 23) + rng.normal(0, 800, (300, 450))).clip(0, 65535)
check("a frame that is all picture: nothing found", find_picture([picture] * 3) is None)
slide = np.full((300, 450), 30.0); slide[30:270, 40:410] = picture[30:270, 40:410]   # a slide in its black mount
r = find_picture([slide] * 3)
check("a slide in a black mount: cut to the picture", r is not None and abs(r[0] - 40 / 450) < 0.02 and abs(r[1] - 0.1) < 0.02
      and abs(r[2] - 410 / 450) < 0.02 and abs(r[3] - 0.9) < 0.02, str(r))
neg = picture.copy(); neg[:15] = 400; neg[-15:] = 400; neg[:, :12] = 65535; neg[:, -9:] = 65535   # film edges and two gaps
r = find_picture([neg] * 3)
check("a negative with dark edges and clear gaps: all four sides found", r is not None and r[0] > 12 / 450 and r[1] > 0.05
      and r[2] < 1 - 9 / 450 and r[3] < 0.95 and r[0] < 0.05 and r[2] > 0.95, str(r))
dark_pic = picture.copy(); dark_pic[:, :120] = 900   # a dark part of the picture along a side, not a border
dark_pic[:15] = 400; dark_pic[-15:] = 400
r = find_picture([dark_pic] * 3)
check("a big dark part of the picture is not taken for a border (no side loses more than a fifth)",
      r is not None and r[0] <= 0.21, str(r))
check("no planes, or a tiny picture: nothing", find_picture([]) is None and find_picture([np.zeros((10, 10))]) is None)

print("C in the app")
def fresh(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=290, ir=False, clean=False, scan_log=False), **kw})
    app.connect(); app.session.borders = True; app.thumbnail(); return app, out
app, out = fresh()
check("off by default, nothing cropped after the thumbnail pass", app.settings["auto_crop"] is False and app.crops == {})
r = app.auto_crop(1)
t = app.state()["tiles"][1]
check("the Auto crop button crops one frame to its picture, marked as found automatically",
      r["found"] and t["crop"] and t["crop_auto"] and 1 not in app.crops.keys() - {1} and len(app.crops) == 1, str(r))
check("...the film's dark edges are left out", t["crop"][1] > 0.03 and t["crop"][3] < 0.97, str(t["crop"]))
app.settings["auto_crop"] = True
app.auto_crop_setting_changed()
check("switched on: every frame is cropped", sorted(app.crops) == list(range(len(app.discovery.frames))) and app.crop_auto == set(app.crops))
app.set_crop(2, (0.1, 0.1, 0.9, 0.9))
app.set_crop(3, None)
app.preview([1, 2, 3])
check("a preview crops again (finer than the thumbnail) but leaves crops set or cleared by hand alone",
      app.crops[2] == (0.1, 0.1, 0.9, 0.9) and 3 not in app.crops and 1 in app.crop_auto and 2 not in app.crop_auto, str(app.crops))
pre = app.crops[1]
check("the preview's crop finds the clear gaps at the frame's ends as well", pre[0] > 0.02 and pre[2] < 0.98, str(pre))
calls = []
real = app.session.scan_frame
app.session.scan_frame = lambda frame, *a, **kw: (calls.append(tuple(frame)), real(frame, *a, **kw))[1]
app.settings["negatives"] = "negative"
app.scan([1])
f = sorted(out.rglob("*.tif"))[0]
img = tifffile.imread(f).astype(np.float64)
img = img if img.ndim == 2 else (img.mean(-1) if img.shape[-1] in (3, 4) else img.mean(0))
check("scanned with an auto crop: only the picture is scanned, and the file has no film edge or gap",
      calls and calls[0] != tuple(app.discovery.frames[1]) and img[:, 0].mean() < 60000 and img[0].mean() > 2000
      and img[-1].mean() > 2000 and img[:, -1].mean() < 60000, f"{calls} {img[0].mean():.0f} {img[:, 0].mean():.0f}")
app.settings["auto_crop"] = False
app.auto_crop_setting_changed()
check("switched off: the crops it made go, the one set by hand stays", app.crops == {2: (0.1, 0.1, 0.9, 0.9)}, str(app.crops))
app, out = fresh()
app.session.borders = False
app.thumbnail()
r = app.auto_crop(0)
check("a frame with no border: nothing found, the crop is as it was, and the Activity list says so",
      not r["found"] and r["crop"] is None and any("found no border" in l for l in app.log), str(r))
app.thumbnail()
check("a new thumbnail pass starts over", app.crops == {} and app.crop_auto == set() and app.crop_hand == set())

print()
print("all passed" if not bad else f"{len(bad)} FAILED: {bad}")
