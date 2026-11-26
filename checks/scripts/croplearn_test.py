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
from coolscan_app.imaging import croplearn as cl
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

def frame(bw, extra=0.0):
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
    m = (0.025 if bw else 0.008) + extra
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
model, net = cl.train(rows)
took = time.time() - t0
check("it learns from 36 crops in under 30 s on the processor (small neural network)", model is not None and took < 30, f"{took:.1f} s")
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
few = cl.train(rows[:5])[0]
check(f"from fewer than {cl.MIN_PICTURES} crops: learned but not used", few is not None and not few["use"], str(few and few["pictures"]))

print("B one kind of model: the small neural network (the simple model and the picture network were taken out)")
check("only the small neural network is trained and kept", model["kind"] == "net" and model["small"]["kind"] == "net"
      and set(model["errors"]) == {"net"} and net is None and not model["cnn_tried"], json.dumps(model["errors"]))
check("the picture network's module is gone", not (pathlib.Path(cl.__file__).parent / "cropcnn.py").exists())
oldm = tmp / "oldkind"; oldm.mkdir()
(oldm / cl.MODEL_FILE).write_text(json.dumps({**model, "kind": "cnn", "small": {**model["small"], "kind": "simple", "w": [0.0] * len(model["small"]["mu"])}}))
check("a model kept by an older version (another kind) is left unused, not an error", cl.CropLearner(oldm).model() is None)

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
check("learning switched off: auto crop goes on using what it has (only keeping and learning stop)", app.auto_crop(4)["crop"] == r1["crop"])
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
n1 = app.croplearn._state().get("new", 0)
app.library_crop("Roll 009 - test", "Positive/pic05.tif", r["rect"])
ex3 = app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)
check("an auto crop kept unchanged (in place of the person's crop) is not learned from, nor counted", len(ex3) == 9 and app.croplearn._state().get("new", 0) == n1, f"{len(ex3)} examples")
app.library_crop("Roll 009 - test", "Positive/pic05.tif", [r["rect"][0] + 0.02, r["rect"][1], r["rect"][2], r["rect"][3]])
ex4 = app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)
check("...but once changed by hand, it is", len(ex4) == 10 and app.croplearn._state().get("new", 0) == n1 + 1, f"{len(ex4)} examples")
app.library_autocrop("Roll 009 - test", ["Positive/pic06.tif"], save=True)
check("the bulk Auto crop's crops are not learned from", len(app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)) == 9)

print("F every crop step, not just the final crop")
st = app.croplearn.steps()
k5 = "lib:" + app.croplearn._path_key(roll / "Positive/pic05.tif")
kinds = [x["kind"] for x in st.get(k5, [])]
check("the Library's steps are kept: auto crop's box, then each crop kept", kinds[:3] == ["auto", "saved", "saved"], str(kinds))
ex5 = [e for e in app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)]
withsteps = [e for e in ex5 if e[3]]
check("a picture's earlier steps go along with its final crop", len(withsteps) >= 1 and all(len(e) == 4 for e in ex5), f"{len(withsteps)} with steps")
app.set_crop(1, (0.06, 0.07, 0.95, 0.93)); app.set_crop(1, (0.05, 0.06, 0.95, 0.94))
hx = [e for e in app.croplearn.hand_examples() if e[3]]
check("on the scanning screen too: the crop drawn before the final one is kept as a step", len(hx) == 1 and hx[0][3][0][0] == 0.06, str(hx and hx[0][3]))
rep = app.croplearn.steps_report()
check("the report has the steps: pictures, how many took more than one, which way auto crop was moved",
      rep["pictures"] >= 2 and rep["several"] >= 2 and rep["from_auto"] >= 1 and rep["auto_moved"]["left"] is not None and rep["recent"], json.dumps({k: rep[k] for k in ("pictures", "steps", "several", "from_auto", "auto_moved")}))
# learning with steps: a "person" whose first try is always 3% too far in; the earlier try is learned as "not here"
rows2 = []
for p_, u, f in train[:16]:
    first = tuple(v + (0.03 if i < 2 else -0.03) for i, v in enumerate(u))
    rows2.append((cl.small_luminance(p_), u, find_picture(p_), cl.context_of(p_, f, False), [first]))
m2 = cl.train(rows2)[0]
check("it learns with the steps, and is still near the final crops", m2 is not None and m2["pictures_with_steps"] == 16
      and m2["error_learned"] < m2["error_rule"], json.dumps(m2["errors"]))

print("G what is learned is kept in the app's models/ folder (for GitHub), the samples outside it")
st2, md = tmp / "s2", tmp / "models"
md.mkdir(); (md / cl.OWNER_FILE).write_text("")   # the app maker's computer
lr3 = cl.CropLearner(st2, md)
for k, (p_, u, f) in enumerate(train[:10]):
    lr3.add_hand_crop(f"g{k}", p_, u, f)
lr3.learn(lr3.hand_examples())
files = sorted(x.name for x in md.iterdir() if not x.name.startswith("."))
check("models/ holds only the model file (and the CNN's weights when that is in use)", files in (["crop_model.json"], ["crop_cnn.npz", "crop_model.json"]), str(files))
data = json.loads((md / "crop_model.json").read_text())
flat = json.dumps(data)
check("the model file has numbers only: no pictures, file names or crops", "hand-" not in flat and ".tif" not in flat and "rect" not in flat
      and set(data) <= {"version", "kind", "small", "pictures", "sides", "error_learned", "error_rule", "errors", "within_1pc", "use",
                        "small_use", "cnn_tried", "pictures_with_steps", "made", "used", "why", "examples"}, str(sorted(data)))
check("the samples and steps stay in the store, not in models/", any(st2.glob("hand-*.npz")) and not any(md.glob("*.npz")) or (md / "crop_cnn.npz").exists())
check("default: models/ beside coolscan_app (in the app's folder, so in git); in the checks a throwaway folder",
      cl.APP_MODELS.parent == pathlib.Path(cl.__file__).resolve().parents[2] and cl.CropLearner().models == cl.MODELS
      and cl.MODELS != cl.APP_MODELS and not str(cl.STORE).startswith(str(pathlib.Path.home() / ".coolscan-crops")))
old = cl.CropLearner(st2, md2 := tmp / "models-empty")
(st2 / "model.json").write_text((md / "crop_model.json").read_text())
check("a model kept the old way (in the store) is still used until it learns again", old.model() is not None)

print("H crops on thumbnails, previews and scans")
def shrink(planes, f):
    return [p[:p.shape[0] // f * f, :p.shape[1] // f * f].reshape(p.shape[0] // f, f, p.shape[1] // f, f).mean(axis=(1, 3)) for p in planes]
def grow(planes, f):
    return [np.kron(p, np.ones((f, f), np.float32)) for p in planes]
sizes = {"scan": lambda p: grow(p, 4), "preview": lambda p: p, "thumbnail": lambda p: shrink(p, 3)}
p, u, f = test[5]
Ls = {k: cl.small_luminance(g(p), f, True) for k, g in sizes.items()}
check("a frame comes out the same size to learn from, whatever it was cropped on", len({L.shape for L in Ls.values()}) == 1
      and max(np.abs(Ls["scan"] - Ls["thumbnail"]).mean(), np.abs(Ls["scan"] - Ls["preview"]).mean()) < 0.3,
      str({k: L.shape for k, L in Ls.items()}) + f" {np.abs(Ls['scan'] - Ls['thumbnail']).mean():.2f}")
by = {k: [] for k in sizes}; apart = []
for p, u, f in test[:16]:
    got = {k: lr2.crop(g(p), find_picture(g(p)), f, raw=True) for k, g in sizes.items()}
    for k in sizes:
        by[k] += err(cl.cuts_of(got[k]), u) if got[k] else [1.0]
    if all(got.values()):
        apart.append(max(abs(a - b) for a, b in zip(got["scan"], got["thumbnail"])))
nears = {k: round(float(np.mean(v)) * 100, 2) for k, v in by.items()}
check("auto crop with what it learned works on a scan, a preview and a thumbnail of a frame (within 1.5% of the person's)",
      all(v < 1.5 for v in nears.values()), f"off by % {nears}")
check("...and the three crops of the same frame agree (on average within 1.5%)", len(apart) == 16 and np.mean(apart) < 0.015, f"{np.mean(apart) * 100:.2f}%")
lh = cl.CropLearner(tmp / "sizes")
for k, (p, u, f) in enumerate(train[:12]):
    lh.add_hand_crop(f"h{k}", list(sizes[["scan", "preview", "thumbnail"][k % 3]](p)), u, f, raw=True)
r = lh.learn(lh.hand_examples())
check("crops drawn on thumbnails, previews and scans are all learned from together", r["pictures"] == 12 and r["use"], r["text"])

print("I learned on finished pictures (the Library), used on raw negatives (the scanning screen)")
def negative(planes):
    """A raw scan of a colour negative: brightness turned over, under an orange mask."""
    return [np.clip(4e8 / (np.asarray(q, np.float32) + 6000.0) * k, 0, 65535) for q, k in zip(planes, (1.0, 0.62, 0.38))]
lp = cl.CropLearner(tmp / "pos")
lp.learn([(cl.small_luminance(p), u, cl.context_of(p, f, False)) for p, u, f in train])   # finished pictures only
p, u, f = test[2]
Lp, Ln = cl.small_luminance(p), cl.small_luminance(negative(p), f, True)
check("a raw negative is mirrored into a positive to learn from and crop", np.corrcoef(Lp.ravel(), Ln.ravel())[0, 1] > 0.9,
      f"likeness {np.corrcoef(Lp.ravel(), Ln.ravel())[0, 1]:.2f}")
on = {"finished picture": [], "negative preview": [], "negative thumbnail": []}
for p, u, f in test[:16]:
    for k, planes, raw in (("finished picture", p, False), ("negative preview", negative(p), True), ("negative thumbnail", shrink(negative(p), 3), True)):
        got = lp.crop(planes, find_picture(planes), f, raw=raw)
        on[k] += err(cl.cuts_of(got), u) if got else [1.0]
on = {k: round(float(np.mean(v)) * 100, 2) for k, v in on.items()}
check("learned only from finished pictures, it crops negative previews and thumbnails about as well (within 1.5%)",
      on["negative preview"] < 1.5 and on["negative thumbnail"] < 1.5, f"off by % {on}")

print("J someone who downloads the app starts from the shipped model and adapts it to their own crops")
import shutil
shipped = tmp / "shipped-models"; shutil.copytree(md, shipped); (shipped / cl.OWNER_FILE).unlink()
before = {x.name: x.read_bytes() for x in shipped.iterdir()}
other = cl.CropLearner(tmp / "other-store", shipped)
p, u, f = test[7]
check("before any crops of their own, auto crop uses the shipped model", not other.owner() and other.model() is not None
      and other.model() is other.shipped() and other.crop(p, find_picture(p), f, raw=True) is not None)
theirs = [frame(i % 2 == 0, extra=0.02) for i in range(24)]   # they cut 2% further in than the app's maker
for k, (p, u, f) in enumerate(theirs[:14]):
    other.add_hand_crop(f"o{k}", p, u, f, raw=True)
r = other.learn(other.hand_examples())
m = json.loads((tmp / "other-store" / cl.MODEL_FILE).read_text())
check("it learns again starting from the shipped weights, and keeps the result in their own store",
      m.get("adapted_from") == json.loads((shipped / cl.MODEL_FILE).read_text())["made"] and m["error_shipped"] is not None, r["text"])
check("the shipped model file is not changed", before == {x.name: x.read_bytes() for x in shipped.iterdir()})
check("on their crops the adapted model is nearer than the shipped one, and is used",
      m["use"] and m["error_learned"] < m["error_shipped"] and other.model() is other.adapted(),
      f"adapted {m['error_learned'] * 100:.2f}%, shipped {m['error_shipped'] * 100:.2f}%, rule {m['error_rule'] * 100:.2f}%")
ea, es = [], []
for p, u, f in theirs[14:]:
    ea += err(cl.cuts_of(other.crop(p, find_picture(p), f, raw=True)), u)
    es += err(other.shipped().cuts(cl.small_luminance(p, f, True), cl.rule_rect_of(cl.small_luminance(p, f, True)), cl.context_of(p, f, True)), u)
check("on 10 new frames of theirs it crops their way", np.mean(ea) < np.mean(es) and np.mean(ea) < 0.012,
      f"adapted {np.mean(ea) * 100:.2f}%, shipped {np.mean(es) * 100:.2f}%")
d = json.loads((shipped / cl.MODEL_FILE).read_text()); d["made"] = "2099-01-01 00:00:00"
(shipped / cl.MODEL_FILE).write_text(json.dumps(d))
check("when a newer model comes with the app, that is used until it has adapted again", other.model() is other.shipped())
check("the report says whose model it is", "adapt" in other.report()["role"]["text"].lower() or "came with the app" in other.report()["role"]["text"],
      other.report()["role"]["text"])

print("J2 Reset to default: back to the model that came with the app")
had = (other.store / cl.MODEL_FILE).exists()
ship0 = {x.name: x.read_bytes() for x in shipped.iterdir()}
kept = len(list(other.store.glob("hand-*.npz")))
rr = other.reset_to_shipped()
check("the adapted model is deleted and the shipped one is used again", had and rr["ok"] and rr["reset"] and rr["shipped"]
      and not (other.store / cl.MODEL_FILE).exists() and other.adapted() is None and other.model() is other.shipped(), str(rr))
check("the shipped file and the learning pictures are not touched", ship0 == {x.name: x.read_bytes() for x in shipped.iterdir()}
      and len(list(other.store.glob("hand-*.npz"))) == kept)
check("a second reset says there was nothing to reset", other.reset_to_shipped()["reset"] is False)
ro = lr3.reset_to_shipped()
check("the app maker's computer (models/.owner) has no other default: nothing is deleted", ro["ok"] is False and ro.get("owner")
      and (md / cl.MODEL_FILE).exists(), str(ro))
r = app.croplearn_reset()
check("the app's own reset answers too", "ok" in r)

print("K learning pictures: all kept unless the person deletes them")
check("deleting after learning is off by default", app.settings["crop_learn_delete_after"] is False)
kept = lr3.pictures_kept()
r = lr3.delete_pictures()
check("Delete learning pictures removes the small greyscale copies, and what was learned stays",
      kept["files"] == 10 and r["deleted"] == 10 and not any(st2.glob("hand-*.npz")) and lr3.model() is not None, json.dumps(r))
n_lib = len(app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of))
app.croplearn.delete_pictures()
check("Library crops are read again from the Library after that", len(app.croplearn.library_examples(out, read_small, apply_edit, tidy_edit, app._lib_film_of)) == n_lib)
app.settings["crop_learn_delete_after"] = True
app.set_crop(1, (0.05, 0.06, 0.95, 0.94))
app.croplearn_learn(wait=True)
check("switched on: none are left once it has learned", not any(app.croplearn.store.glob("hand-*.npz"))
      and not any((app.croplearn.store / "library").glob("*.npz")))

print()
print("FAILED: " + ", ".join(bad) if bad else "all passed")
sys.exit(1 if bad else 0)
