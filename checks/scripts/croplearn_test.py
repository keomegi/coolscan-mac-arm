"""The learning auto crop: it learns a person's way of cropping from their crops (made-up frames with
borders, where the "person" always cuts a little past the border, further on B&W), keeps what it
learned, uses it only when it beats the built-in rule, and the app keeps hand crops (scanning screen
and Library) whether or not auto crop is on, unless learning is switched off."""
import json, os, sys, tempfile, pathlib, time
os.environ.setdefault("COOLSCAN_CNN_STEPS", "40")   # the picture network: only that it runs, here
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
from coolscan_app.imaging import croplearn as cl, cropcnn
from coolscan_app.imaging.autocrop import find_picture
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

rng = np.random.default_rng(3)
def picture(h, w):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    a, b, c = rng.uniform(15, 60, 3)
    p = 20000 + 12000 * np.sin(x / a + rng.uniform(0, 6)) * np.cos(y / b) + 6000 * np.sin((x + y) / c)
    p[rng.integers(0, h // 2):, rng.integers(0, w // 2):rng.integers(w // 2, w)] *= rng.uniform(0.3, 1.0)  # a dark part
    return p

def frame(bw):
    """A frame as a person sees it: white film edges top and bottom, black gaps at the ends (sometimes
    none, sometimes a sliver of the next frame), and their crop: the border and 0.8% more (2.5% on B&W)."""
    h, w = 180, 270
    img = picture(h, w) * rng.uniform(0.5, 1.2)
    tint = [np.ones((h, w))] * 3 if bw else [np.ones((h, w)), 0.5 + 0.5 * picture(h, w) / 40000, 0.4 + 0.6 * picture(h, w) / 40000]
    t, b = rng.uniform(.02, .07, 2); l, r = rng.uniform(0, .06, 2)
    img[:int(t * h)] = img[h - int(b * h):] = rng.uniform(50000, 64000)
    for side, c in (("l", l), ("r", r)):
        n = int(c * w)
        if n:
            band = img[:, :n] if side == "l" else img[:, w - n:]
            band[:] = rng.uniform(100, 2500)
            if rng.random() < .3 and n > 6:
                (img[:, :n // 4] if side == "l" else img[:, w - n // 4:])[:] = rng.uniform(5000, 30000)
    img = np.clip(img + rng.normal(0, 400, img.shape), 0, 65535)
    m = 0.025 if bw else 0.008
    user = (int(l * w) / w + (m if int(l * w) else 0), int(t * h) / h + m, 1 - int(r * w) / w - (m if int(r * w) else 0), 1 - int(b * h) / h - m)
    inside = np.zeros((h, w), bool); inside[int(t * h):h - int(b * h), int(l * w):w - int(r * w)] = True
    planes = [np.where(inside, np.clip(img * k, 0, 65535), img) for k in tint]   # colour only in the picture
    return planes, user, ("mono" if bw else "negative")

def err(cuts, user):
    u = cl.cuts_of(user)
    return [abs(cuts[s] - u[s]) for s in cl.SIDES]

print("A learning from crops (made-up frames)")
train = [frame(i % 2 == 0) for i in range(36)]
test = [frame(i % 2 == 0) for i in range(40)]
t0 = time.time()
rows = [(cl.small_luminance(p), u, find_picture(p), cl.context_of(p, f, False)) for p, u, f in train]
model, net = cl.train(rows, cnn=False)
took = time.time() - t0
check("it learns from 36 crops in under 30 s on the processor (simple model and small network)", model is not None and took < 30, f"{took:.1f} s")
check("tried on crops it had not seen, it is nearer the person's crops than the built-in rule",
      model["error_learned"] < model["error_rule"] and model["use"], json.dumps(model["errors"]) + f" rule {model['error_rule']:.4f}")
m = cl.Model(model)
el, er = [], []
for p, u, f in test:
    L = cl.small_luminance(p); rule = find_picture(p)
    el += err(m.cuts(L, rule, cl.context_of(p, f, False)), u)
    er += err(cl.cuts_of(rule) if rule else dict.fromkeys(cl.SIDES, 0.0), u)
el, er = np.mean(el), np.mean(er)
check("on 40 new frames its edges are on average within 1% of the person's, and nearer than the rule's",
      el < 0.01 and el < er, f"learned {el * 100:.2f}%, rule {er * 100:.2f}%")
bwm = [np.mean(m.cuts(cl.small_luminance(p), find_picture(p), cl.context_of(p, f, False))["top"]) - cl.cuts_of(u)["top"]
       for p, u, f in test]
check("it learned the B&W habit (cutting further in) from the film type", abs(np.mean(bwm)) < 0.008, f"{np.mean(bwm) * 100:.2f}%")
check("it can tell B&W from colour by the pixels as well", cl.context_of(test[0][0], None)[-1] == 1.0 and cl.context_of(test[1][0], None)[-1] == 0.0)
few = cl.train(rows[:5], cnn=False)[0]
check(f"from fewer than {cl.MIN_PICTURES} crops: learned but not used", few is not None and not few["use"], str(few and few["pictures"]))

print("B the picture network (CNN)")
if cropcnn.available():
    model2, net2 = cl.train(rows[:14], cnn=True)
    check("with MLX and 12+ crops it is tried beside the other two, and how near it got is noted",
          model2["cnn_tried"] and model2["errors"].get("cnn") is not None, json.dumps(model2["errors"]))
    S = np.stack([s for _, s in cl._side_strips(rows[0][0], rows[0][2], rows[0][3])])
    n = cropcnn.fit(S, np.array([0.03] * 4), epochs=2)
    cropcnn.save(n, tmp / "c.npz")
    check("its weights are kept in a file and read back the same", np.allclose(cropcnn.scores(n, S), cropcnn.scores(cropcnn.load(tmp / "c.npz"), S)))
else:
    print("   (no MLX here: skipped; the other two models still learn)")

print("C kept between runs")
store = tmp / "store"
lr = cl.CropLearner(store)
for k, (p, u, f) in enumerate(train):
    lr.add_hand_crop(f"k{k}", p, u, f, raw=True)
check("hand crops are kept as small files (under 40 KB each)", len(list(store.glob("hand-*.npz"))) == 36
      and max(x.stat().st_size for x in store.glob("hand-*.npz")) < 40_000)
lr.add_hand_crop("k0", train[1][0], train[1][1], "mono")
check("a newer crop of the same frame replaces the older", len(list(store.glob("hand-*.npz"))) == 36)
check("after 10 new crops it is due to learn again by itself", lr.due())
res = lr.learn(lr.hand_examples(), why="check")
check("learning writes the model, a history line, and what it took (time, processor, memory)",
      (store / cl.MODEL_FILE).exists() and len(lr.history()) == 1 and res["used"]["seconds"] >= 0
      and res["used"]["memory_mb"] and not lr.due(), json.dumps(res["used"]))
lr2 = cl.CropLearner(store)
p, u, f = test[3]
got = lr2.crop(p, find_picture(p), f, raw=True)
check("a new start reads it back and crops with it", got is not None and max(err(cl.cuts_of(got), u)) < 0.03, f"{got} vs {u}")
rep = lr2.report()
check("the report has the numbers for the page", rep["pictures"] == 36 and rep["hand_crops"] == 36 and rep["crops_made"] == 1
      and rep["model"]["errors"] and rep["store_mb"] > 0, json.dumps({k: rep[k] for k in ("pictures", "hand_crops", "crops_made", "store_mb")}))

print("D in the app")
def fresh(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=290, ir=False, clean=False, scan_log=False), **kw})
    app.connect(); app.session.borders = True; app.thumbnail(); return app, out
app, out = fresh()
app.croplearn = cl.CropLearner(tmp / "appstore")
check("learning is on by default, auto crop off", app.settings["auto_crop_learn"] is True and app.settings["auto_crop"] is False)
app.set_crop(1, (0.05, 0.06, 0.95, 0.94))
check("a crop drawn by hand is kept to learn from, with auto crop off", len(app.croplearn.hand_examples()) == 1)
app.set_crop(2, None)
check("clearing a crop keeps nothing", len(app.croplearn.hand_examples()) == 1)
app.settings["auto_crop_learn"] = False
app.set_crop(3, (0.05, 0.06, 0.95, 0.94))
check("learning switched off: hand crops are not kept", len(app.croplearn.hand_examples()) == 1)
r0 = app.auto_crop(4)
app.settings["auto_crop_learn"] = True
app.croplearn = lr2
r1 = app.auto_crop(4)
check("with what it learned, auto crop gives its own crop (the rule's when learning is off)",
      r1["found"] and r0["crop"] != r1["crop"] and lr2.uses["n"] >= 2, f"{r0['crop']} -> {r1['crop']}")
app.settings["auto_crop_learn"] = False
check("switched off: the built-in rule again", app.auto_crop(4)["crop"] == r0["crop"])
app.settings["auto_crop_learn"] = True
app.croplearn = cl.CropLearner(tmp / "appstore2")
r = app.croplearn_learn(wait=True)
check("Learn with too few crops says how many more it needs", r["ok"] and "Crop at least" in r["text"], r["text"])
rep = app.croplearn_report()
check("the report says whether it is on and the least number of crops", rep["on"] is True and rep["least"] == cl.MIN_PICTURES)

print("E crops kept in the Library")
roll = out / "Roll 009 - test"; (roll / "Positive").mkdir(parents=True, exist_ok=True)
marks = {"pictures": {}}
for k, (p, u, f) in enumerate(train[:10]):
    name = f"Positive/pic{k:02d}.tif"
    tifffile.imwrite(roll / name, np.dstack(p).astype(np.uint16), photometric="rgb")
    marks["pictures"][name] = {"edit": {"turns": 0, "crop": list(u)}}
marks["pictures"]["Positive/pic00.tif"]["edit"]["turns"] = 1   # turned: learned on the picture as turned
marks["pictures"]["Positive/pic00.tif"]["edit"]["crop"] = list(cl.rect_of({"left": 0.05, "top": 0.05, "right": 0.05, "bottom": 0.05}))
(roll / "Library.json").write_text(json.dumps(marks))
from coolscan_app.library import apply_edit, read_small, tidy_edit
ex = app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)
check("the Library's crops are found and read (10 pictures, one turned)", len(ex) == 10 and ex[0][0].shape[0] > ex[0][0].shape[1], str(len(ex)))
t0 = time.time(); ex2 = app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)
check("read again from the kept small copies", len(ex2) == 10 and np.allclose(ex[3][0], ex2[3][0]), f"{time.time() - t0:.2f} s")
n0 = app.croplearn._state().get("new", 0)
r = app.library_crop("Roll 009 - test", "Positive/pic03.tif", [0.1, 0.1, 0.9, 0.9])
check("a crop kept in the Library counts towards learning again", r.get("ok") and app.croplearn._state().get("new", 0) == n0 + 1, str(r))
r = app.library_autocrop("Roll 009 - test", ["Positive/pic05.tif"], save=False)
check("the Library's Auto crop still works", r["ok"] and r["pictures"][0]["found"], str(r))

print()
print("FAILED: " + ", ".join(bad) if bad else "all passed")
sys.exit(1 if bad else 0)
