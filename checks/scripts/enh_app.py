import sys, time, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np, tifffile
import cs_next as cs
cs.SETTINGS_FILE = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
def run(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(outdir=str(out), dpi=1450, ir=False, basename="Scan", name_date=False, **kw)
    app.connect(); app.thumbnail(); t = time.time(); app.scan([0]); dt = time.time() - t
    f = next(out.rglob("Scan_001*.tif")); a = tifffile.imread(str(f))
    return a.astype(np.float64), dt, [l[9:] for l in app.log if "grain" in l or "restoration" in l], app
for film in ("negative", "positive"):
    base, t0, _, _ = run(film=film)
    for kw in (dict(grain=2), dict(restore=2), dict(grain=4, restore=3)):
        a, t, log, app = run(film=film, **kw)
        hp = lambda x: x - (np.roll(x, 1, 0) + np.roll(x, -1, 0) + np.roll(x, 1, 1) + np.roll(x, -1, 1)) / 4
        print(film, kw, "shape", a.shape, a.dtype if hasattr(a, 'dtype') else '', "| changed:", bool((a != base).any()),
              "| fine noise %.0f -> %.0f" % (hp(base[..., 1]).std(), hp(a[..., 1]).std()),
              "| mean %.0f -> %.0f" % (base.mean(), a.mean()), "| %.1fs vs %.1fs" % (t, t0), "|", log[:1])
# preview with restore on
app = cs.App(demo=True); app.settings.update(restore=3, preview_dpi=290); app.connect(); app.thumbnail(); app.preview([0, 1])
print("previews with restore:", sorted(app.previews), [len(v) for v in app.previews.values()])
app.settings["restore"] = 0; app.redraw(); print("redraw without:", [len(v) for v in app.previews.values()])
