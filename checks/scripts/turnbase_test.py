"""Auto rotate's ready-made model (imaging/turnbase.py): its ONNX file read and run on MLX gives the same
numbers as onnxruntime (when both are installed and COOLSCAN_TURN_BASE names the file; skipped otherwise);
the model's classes become the app's turns; and the app's handling, with a stand-in for the model:
thumbnails only get a suggestion, a preview is turned, the scan is looked at again before its files are
written, a frame turned by hand is left alone, and the Library uses it."""
import os, sys, tempfile, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
from coolscan_app.imaging import turnlearn as tl, croplearn as cl, turnbase as tb
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

print("A the model's file, read here and run on MLX")
real = os.environ.get("COOLSCAN_TURN_BASE", "")
try:
    import onnxruntime as ort
except Exception:
    ort = None
if tb.mx is None or ort is None or not os.path.exists(real):
    print("   skipped (needs MLX, onnxruntime and COOLSCAN_TURN_BASE=/path/to/" + tb.NAME + ")")
else:
    check("the file is the one the app expects (SHA-256, size)", tb._sha(real) == tb.SHA256 and os.path.getsize(real) == tb.BYTES)
    net = tb.base()
    x = np.random.default_rng(0).normal(0, 1, (1, tb.SIDE, tb.SIDE, 3)).astype(np.float32)
    want = ort.InferenceSession(real, providers=["CPUExecutionProvider"]).run(None, {"input": x.transpose(0, 3, 1, 2)})[0]
    got = net(x)
    check("the same scores as onnxruntime", got.shape == (1, 4) and float(np.abs(got - want).max()) < 1e-3, f"{float(np.abs(got - want).max()):.2e}")
    tb.forget()

print("B pictures in, turns out")
check("no file: no model, nothing fetched while the checks run", (os.path.exists(real) or tb.base() is None) and tb._fetch["state"] is None)
a = (np.random.default_rng(1).random((300, 448, 3)) * 255).astype(np.uint8)
p = tb.prepare(a)
check("a picture is made the model's size", p.shape == (tb.SIDE, tb.SIDE, 3) and p.dtype == np.float32)
check("...a grey one and a tiny one too", tb.prepare(a[:40, :60, 0]).shape == (tb.SIDE, tb.SIDE, 3))
flat = tb.prepare(np.full((100, 150, 3), 128, np.uint8))
check("...with the model's own mean taken off", abs(float(flat[..., 0].mean()) - (128 / 255 - 0.485) / 0.229) < 1e-3)
neg = tb.picture([np.linspace(1000, 60000, 600 * 900).reshape(600, 900)] * 3, "negative")
pos = tb.picture([np.linspace(1000, 60000, 600 * 900).reshape(600, 900)] * 3, "positive")
check("a negative is turned round into a positive to look at, and made small", neg.shape[:2] == pos.shape[:2] and max(neg.shape[:2]) <= 2 * tb.LOOK
      and neg[0, 0, 0] > neg[-1, -1, 0] and pos[0, 0, 0] < pos[-1, -1, 0], str(neg.shape))
check("a thumbnail piece counts as small, a preview does not", tb.is_small(np.zeros((70, 100, 3))) and not tb.is_small(np.zeros((300, 450, 3))))

class Net:   # says "class c" for whatever it is shown; c follows the picture's turn like the real model's
    def __init__(self, c): self.c = c
    def __call__(self, x):
        z = np.full((len(x), 4), -1.0)
        for r in range(len(x)): z[r, (self.c + r) % 4] = 3.0
        return z
for c in range(4):
    tb.base = lambda c=c: Net(c)   # (works without MLX too)
    p1, p4 = tb.probs(a), tb.probs(a, votes=4)
    check(f"the model's class {c} (turned {c} quarters left) = turn it {(-c) % 4} to the left", int(p1.argmax()) == (-c) % 4 and int(p4.argmax()) == (-c) % 4
          and abs(p1.sum() - 1) < 1e-9 and p4.max() > p1.max(), f"{p1.round(3)}")

print("C the app with the model: thumbnails suggest, previews and scans turn")
class Base:
    """Stand-in for the model: `small` for thumbnail-sized pictures, `big` for larger ones."""
    small = np.array([0.03, 0.91, 0.03, 0.03]); big = np.array([0.03, 0.03, 0.03, 0.91]); seen = []
base = Base()
tb.base = lambda: base
def fake_probs(rgb, votes=1):
    base.seen.append(np.asarray(rgb).shape)
    return base.small if tb.is_small(rgb) else base.big
tb.probs = fake_probs
app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
app.settings.update(outdir=str(out), dpi=290, ir=False, clean=False, scan_log=False, film="positive", rotate180=False, archive=False)
app.turnlearn = tl.TurnLearner(tmp / "store"); app.croplearn = cl.CropLearner(tmp / "crops")
app.connect(); app.session.borders = True; app.thumbnail()
st = app.state(); n = len(st["frames"])
small_seen = all(max(s[:2]) < tb.SMALL for s in base.seen)
check("thumbnail pieces are small pictures", n >= 3 and base.seen and small_seen, str(base.seen[:2]))
check("on a thumbnail it only suggests: nothing turned, a hint that says so", not any(app.frame_rot.get(i, 0) for i in range(n))
      and all(not st["tiles"][i]["turn_auto"] for i in range(n)) and (st["tiles"][0].get("turn_hint") or {}).get("small") is True
      and st["tiles"][0]["turn_hint"]["applied"] is False and st["tiles"][0]["turn_hint"]["quarter"] == 1, str(st["tiles"][0].get("turn_hint")))
check("a sideways strip says nothing about the holder", st["strip_turn"] is None and not app.settings["rotate180"])
base.seen.clear()
app.settings["dpi"] = 2900
app.scan([0])
st = app.state()
saved = app.frame_saved[0][-1]
f = pathlib.Path(saved["folder"]) / saved["files"][0]
h, w = tifffile.imread(f).shape[:2]
check("the scan is looked at before its files are written (a big picture)", any(max(s[:2]) >= tb.SMALL for s in base.seen), str(base.seen))
check("...and turned as the model says of the scan, not of the thumbnail", app.frame_rot.get(0) == 3 and saved["turns"] == 3 and h > w, f"{app.frame_rot.get(0)} {saved['turns']} {w}x{h}")
check("...marked as auto rotate's own turn, with Accept / Reject", st["tiles"][0]["turn_auto"] and (st["tiles"][0].get("turn_hint") or {}).get("applied") is True)
app.rotate([1], 1)                                   # turned by hand: left alone
app.scan([1])
check("a frame turned by hand is scanned as the person turned it", app.frame_rot.get(1) == 1 and app.frame_saved[1][-1]["turns"] == 1)
base.big = np.array([0.4, 0.2, 0.2, 0.2])            # not sure of the scan
app.scan([2])
check("not sure of the scan: saved as it was shown", app.frame_rot.get(2, 0) == 0 and app.frame_saved[2][-1]["turns"] == 0)
app.settings["auto_turn"] = False
base.big = np.array([0.03, 0.03, 0.03, 0.91])
if n > 3:
    app.scan([3])
    check("auto rotate off: nothing is turned", app.frame_rot.get(3, 0) == 0)
app.settings["auto_turn"] = True

print("D the Library")
roll = out / "Roll 030 - base"; roll.mkdir(parents=True, exist_ok=True)
tifffile.imwrite(roll / "p0.tif", (np.random.default_rng(3).random((400, 600, 3)) * 65535).astype(np.uint16), photometric="rgb")
lk = [r["key"] for r in app.library_rolls()["rolls"] if r["name"] == roll.name][0]
g = app.turnlearn_library_guess(lk, "p0.tif", seen=False)["guess"]
check("a Library picture is guessed by the model, from its colours", g and g.get("by") == "base" and g["turn"] == 3 and g["auto"] and g["quarter"] == 3, str(g))
r = app.turnlearn_library_auto(lk, ["p0.tif"])
check("Auto rotate in the Library turns it", len(r["turned"]) == 1 and r["turned"][0]["turn"] == 3, str(r))
rep = app.turnlearn_report()
check("the report says which model is in use", (rep.get("base") or {}).get("state") in ("ready", "missing", "no MLX", "off") and "base_sure" in rep)

print()
print("ALL PASS" if not bad else f"{len(bad)} FAILED: " + "; ".join(bad))
sys.exit(1 if bad else 0)
