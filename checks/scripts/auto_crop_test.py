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
# the clear gap between negatives, looked at by eye on each frame (2026-11-23): which sides have one (Scan003 also has a sliver on the right, under 1%, too thin to count on at thumbnail size)
GAPS = {"scan_1.tiff": "LR", "sample_4pics/Scan001.tif": "LR", "sample_4pics/Scan002.tif": "R",
        "sample_4pics/Scan003.tif": "L", "sample_4pics/Scan004.tif": "LR", "test_samples/Scan001.tif": "LR"}
for name, gaps in (("20261123_019_R003.tif", "R"),    # a RAW scan from the user's roll 3
                   ("20261124_041_R005.tif", "R")):   # a dark black-and-white frame (roll 5), its band no darker than the picture
    extra = pathlib.Path("/mnt/project-files") / name
    if extra.exists():
        files.append(extra); GAPS[name] = gaps
if not files:
    print("   (not here: skipped)")
for f in files:
    a = tifffile.imread(f)[::4, ::4, :3].astype(np.float32)
    key = f.name if f.name in GAPS else f"{f.parents[2].name}/{f.name}"
    gaps = GAPS.get(key, "")
    # the gap only reaches the scanner's ceiling at a final scan's exposure: thumbnails and previews are darker
    sizes = (("at 1/4 size", a), ("at thumbnail size", a[::8, ::8]))
    if "_R005" not in f.name:   # that one is a finished picture (its white top and bottom are not the scanner's): no lower exposure
        sizes += (("at half the exposure", a * 0.5), ("thumbnail at a third of the exposure", a[::8, ::8] / 3))
    for label, sub in sizes:
        r = find_picture([sub[..., c] for c in range(3)])
        edge = 0.1 if "thumbnail" in label else 0.08   # a thumbnail is about 90 rows high here
        ok = r is not None and 0.02 <= r[1] <= edge and 1 - edge <= r[3] <= 0.98 and r[0] <= 0.08 and r[2] >= 0.92
        ok = ok and (r[0] > 0.004) == ("L" in gaps) and ((r[2] < 0.996) == ("R" in gaps) or key.endswith("Scan003.tif"))
        check(f"{key} {label}: the dark film edges and the gaps ({gaps or 'none'}) go, no side loses more than 8%", ok, str(r))

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
dim = picture * 0.6; dim[:15] = 250; dim[-15:] = 250; dim[:, :12] = 34000; dim[:, -9:] = 34000   # the same at a thumbnail's exposure
dim += rng.normal(0, 300, dim.shape)
r = find_picture([dim] * 3)
check("...and at a lower exposure, where the gaps are well below the scanner's ceiling", r is not None and r[0] > 12 / 450
      and r[2] < 1 - 9 / 450 and r[0] < 0.05 and r[2] > 0.95, str(r))
dark_pic = picture.copy(); dark_pic[:, :120] = 900   # a dark part of the picture along a side, not a border
dark_pic[:15] = 400; dark_pic[-15:] = 400
r = find_picture([dark_pic] * 3)
check("a big dark part of the picture is not taken for a border (no side loses more than a fifth)",
      r is not None and r[0] <= 0.21, str(r))
# a sliver of the next frame outside the clear gap (rolls 4-6): the gap's outer line is the strongest
# step, the band reaching the side is not even, so the gap's inner line and all outside it must go
sl = picture.copy(); sl[:12] = 400; sl[-12:] = 400
sl[:, :8] = picture[:, 200:208]; sl[:, 8:20] = 65535     # a sliver of picture, then the gap (left)
sl[:, -6:] = picture[:, 300:306]; sl[:, -18:-6] = 65535  # and on the right
r = find_picture([sl] * 3)
check("a sliver of the next frame outside the clear gap: the gap and the sliver go, the picture stays",
      r is not None and 20 / 450 <= r[0] <= 20 / 450 + 0.012 and 1 - 18 / 450 - 0.012 <= r[2] <= 1 - 18 / 450, str(r))
# the film's edges along the top and bottom as dark as the picture's shadows (not "dark"): the clear gap
# is judged between them, so it still looks even
fe = picture.copy(); fe[:12] = 3000; fe[-12:] = 3000; fe[:, :45] = 65535; fe[:12, :45] = 3000; fe[-12:, :45] = 3000
fe[0:150, 60:70] = 1500   # a shadow in the picture, as dark as the film's edges
r = find_picture([fe] * 3)
check("a wide clear gap crossed by film edges no darker than the picture: all of the gap goes",
      r is not None and 45 / 450 <= r[0] <= 45 / 450 + 0.012, str(r))
check("no planes, or a tiny picture: nothing", find_picture([]) is None and find_picture([np.zeros((10, 10))]) is None)

print("B2 the user's rolls 4-6, RAW (when this Mac's Pictures folder is at hand)")
ROLLS = pathlib.Path.home() / "Pictures" / "Coolscan"
# sides that kept the clear gap (or the gap and a sliver of the next frame) before 2026-11-24, and how far
# in (share of the frame, measured by eye on the column levels) the gap ends there
SIDES = {"20261123_012_R004.tif": ("R", 0.040), "20261123_001_R005.tif": ("L", 0.094),
         "20261123_002_R005.tif": ("L", 0.033), "20261123_015_R005.tif": ("L", 0.033),
         "20261124_026_R005.tif": ("R", 0.042), "20261124_031_R005.tif": ("L", 0.032),
         "20261124_035_R005.tif": ("L", 0.035)}
found = {f.name: f for f in ROLLS.glob("Roll 00[456]*/RAW/*.tif")} if ROLLS.exists() else {}
if not any(n in found for n in SIDES):
    print("   (not here: skipped)")
for name, (side, end) in SIDES.items():
    if name not in found:
        continue
    a = tifffile.imread(found[name])
    a = (a if a.shape[-1] in (3, 4) else np.moveaxis(a, 0, -1))[::4, ::4, :3].astype(np.float32)
    a = np.rot90(a) if a.shape[0] > a.shape[1] else a   # the strip runs across, as the scanner sees it
    r = find_picture([a[..., c] for c in range(3)])
    cut = None if r is None else (r[0] if side == "L" else 1 - r[2])
    check(f"{name}: the {side} gap goes, and no more than 1.5% past it", cut is not None and end <= cut <= end + 0.015, str(r))

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
