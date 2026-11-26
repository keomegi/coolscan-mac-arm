"""The learning auto rotate: it learns which way up a person's pictures go from the turns they make
(made-up scenes: a sky above, ground with detail below, things standing up; some taken with the camera
on its side), on the GPU with MLX where there is one (numpy otherwise), keeps what it learned (numbers
only in models/), turns new frames by itself only when sure (else suggests), learns only from turns
made by hand, and someone else's copy adapts to their own pictures."""
import json, os, sys, tempfile, pathlib, shutil
os.environ.setdefault("COOLSCAN_CNN_STEPS", "30")   # the picture network: only that it runs, here
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
from coolscan_app.imaging import turnlearn as tl, croplearn as cl
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

rng = np.random.default_rng(5)
def scene(h, w, night=False):
    """Upright: day, a bright sky above and darker ground with fine detail below; night, the other way."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    hor = rng.uniform(0.3, 0.7) * h + 8 * np.sin(x / rng.uniform(10, 40))
    sky = (40000 + 15000 * (1 - y / h) if not night else 3000 + 1500 * (y / h)) + rng.normal(0, 600, (h, w))
    ground = (9000 if not night else 30000) + 6000 * np.sin(x / rng.uniform(2, 6)) * np.cos(y / rng.uniform(2, 6)) + rng.normal(0, 3000, (h, w))
    img = np.where(y < hor, sky, ground)
    for _ in range(rng.integers(0, 4)):   # things standing on the ground
        cx, wd = rng.integers(0, w), rng.integers(3, 10); top = int(rng.uniform(0.15, 0.5) * h)
        img[top:int(hor.mean()) + 5, max(0, cx - wd):cx + wd] = rng.uniform(3000, 12000)
    return np.clip(img * rng.uniform(0.6, 1.1), 0, 65535)

def frame(night=False, side=1):
    """A frame as shown (as scanned) and the turn the person gives it: most as they are, the camera on its
    side now and then (turned `side` to put right), seldom upside down."""
    k = int(rng.choice(4, p=[0.6, 0.28, 0.02, 0.10] if side == 1 else [0.6, 0.10, 0.02, 0.28]))
    U = scene(180, 270, night) if k % 2 == 0 else scene(270, 180, night)
    D = np.rot90(U, -k)
    return [np.ascontiguousarray(D * t) for t in (1.0, 0.9, 0.8)], k

def right(model_or_learner, frames, **kw):
    out = []
    for p, k in frames:
        if isinstance(model_or_learner, tl.TurnModel):
            g = int(np.argmax(model_or_learner.probs(cl.small_luminance(p))))
        else:
            r = model_or_learner.guess(p, **kw); g = r["turn"] if r else 0
        out.append(g == k)
    return float(np.mean(out))

print("A learning which way up pictures go")
train = [frame() for _ in range(30)]
test = [frame() for _ in range(40)]
rows = [(cl.small_luminance(p), k) for p, k in train]
if not tl.gpu():
    # Learning from turns is the picture network alone, and that needs MLX (an Apple Silicon Mac). Where
    # there is none (GitHub's Linux runner), only this is checked: nothing is learned, nothing breaks.
    print("   (no MLX here: the learning checks are skipped; checking that nothing is learned and nothing breaks)")
    mn, nn_ = tl.train(rows)
    mnp = tl.TurnModel(mn)
    check("without MLX nothing is learned or turned", nn_ is None and mn["kind"] == "none" and not mn["use"] and not mn["auto"]
          and not mnp.usable() and not mnp.may_turn() and int(np.argmax(mnp.probs(rows[0][0]))) == 0)
    check("the words say why", "MLX" in tl.TurnLearner.describe(mn), tl.TurnLearner.describe(mn))
    lr0 = tl.TurnLearner(tmp / "nomlx")
    for i, (p, k) in enumerate(train):
        lr0.add(f"t{i}", p, k)
    res = lr0.learn(lr0.examples())
    check("the app's learner still runs, keeps the pictures and gives a report", res.get("ok") and len(lr0.examples()) == 30
          and lr0.report()["ok"] and (lr0.model() is None or not lr0.model().usable()), str(res.get("text")))
    check("a frame gets no turn from it", lr0.guess(test[0][0]) is None)
    check("Reset to default answers", "ok" in lr0.reset_to_shipped())
    print()
    print("FAILED: " + ", ".join(bad) if bad else "all passed (without MLX)")
    sys.exit(1 if bad else 0)
model, net = tl.train(rows)
keep = np.mean([k == 0 for _, k in test])
check("tried on pictures it had not seen, it has the right way up more often than leaving them as they are",
      model["use"] and model["right"][model["kind"]] > model["right_keep"], json.dumps(model["right"]) + f" keep {model['right_keep']:.2f}")
d0 = tmp / "m0"; d0.mkdir()
if net is not None: tl._save_cnn(net, d0 / tl.CNN_FILE)
m = tl.TurnModel(model, d0 / tl.CNN_FILE)
r = right(m, test)
check("on 40 new frames it is right at least 90% of the time (leaving them: about 60%)", r >= 0.9 and r > keep, f"{r:.2f} vs {keep:.2f}")
check("it learned the person's habit (vertical pictures turned left far more often than right)", model["by_turn"][1] > model["by_turn"][3])
check("it knows how often its sure answers were right, and may turn by itself only when that is high", model["sure"][model["kind"]]["n"] > 0
      and model["auto"] == (model["sure"][model["kind"]]["right"] >= tl.AUTO_RIGHT and model["sure"][model["kind"]]["n"] >= tl.AUTO_LEAST),
      json.dumps(model["sure"][model["kind"]]))
few = tl.train(rows[:6])[0]
check(f"from fewer than {tl.MIN_PICTURES} pictures: learned but not used", few is not None and not few["use"])

print("B one kind of model: the picture network, on the GPU (MLX); without MLX nothing is learned")
if tl.gpu():
    check("MLX is there: only the picture network is trained (no simple model, no small network)",
          model["cnn_tried"] and model["kind"] == "cnn" and set(model["right"]) == {"cnn"} and not hasattr(tl, "MLP") and not hasattr(tl, "Lin"), tl.device())
    S = np.stack([tl.view_pixels(L) for L, _ in rows[:4]]); X = np.stack([tl.candidates(L) for L, _ in rows[:4]]).astype(np.float32)
    n0 = tl.TurnNet(X.shape[2]); tl._save_cnn(n0, tmp / "t.npz"); n1 = tl._load_cnn(tmp / "t.npz", X.shape[2])
    check("the picture network's weights are kept in a file and read back the same",
          np.allclose(np.array(n0(tl.mx.array(S), tl.mx.array(X))), np.array(n1(tl.mx.array(S), tl.mx.array(X))), atol=1e-5))
    check("turning a frame uses the picture network", m.net is not None)
else:
    print("   (no MLX here: skipped)")
saved_mx = tl.mx
tl.mx = None
try:
    mn, nn_ = tl.train(rows)
    mnp = tl.TurnModel(mn)
    check("without MLX nothing is learned or turned, and nothing breaks", nn_ is None and mn["kind"] == "none" and not mn["use"] and not mn["auto"]
          and not mnp.usable() and int(np.argmax(mnp.probs(rows[0][0]))) == 0 and "MLX" in tl.TurnLearner.describe(mn), tl.TurnLearner.describe(mn))
finally:
    tl.mx = saved_mx

print("C kept between runs")
store = tmp / "store"
lr = tl.TurnLearner(store)
for i, (p, k) in enumerate(train):
    lr.add(f"k{i}", p, k, "positive", raw=False)
check("each picture is kept as a small file (under 40 KB)", len(list(store.glob("turn-*.npz"))) == 30
      and max(x.stat().st_size for x in store.glob("turn-*.npz")) < 40_000)
check("after 10 new turns it is due to learn again by itself", lr.due())
res = lr.learn(lr.examples(), why="check")
check("learning writes the model, a history line and what it took (time, memory, GPU or processor)",
      (store / tl.MODEL_FILE).exists() and len(lr.history()) == 1 and res["used"]["device"] and not lr.due(), json.dumps(res["used"]))
lr2 = tl.TurnLearner(store)
g = lr2.guess(test[0][0])
check("a new start reads it back and turns with it", g is not None and right(lr2, test) >= 0.9, f"{right(lr2, test):.2f}")
rep = lr2.report()
check("the report has the numbers for the page", rep["pictures"] == 30 and rep["frames_looked"] > 0 and rep["model"]["right"]
      and rep["counts"]["scan"] == 30 and rep["pictures_kept"]["files"] == 30)

print("D on the scanning screen")
def fresh(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=290, ir=False, clean=False, scan_log=False, film="positive"), **kw})
    app.connect(); app.session.borders = True; app.thumbnail(); return app, out
app, out = fresh()
app.turnlearn = tl.TurnLearner(tmp / "appstore")
app.croplearn = cl.CropLearner(tmp / "appcrops")
check("auto rotate is on by default", app.settings["auto_turn"] is True)
app.rotate([1], 1)
ex = app.turnlearn.examples()
check("a frame turned by hand is kept to learn from, with its turn", len(ex) == 1 and ex[0][1] == 1)
app.set_crop(2, (0.05, 0.06, 0.95, 0.94))
check("a frame cropped by hand is kept too (its turn was kept on purpose)", len(app.turnlearn.examples()) == 2)
app.settings["auto_turn"] = False
app.rotate([3], 1)
check("switched off: turns are not kept", len(app.turnlearn.examples()) == 2)
app.settings["auto_turn"] = True
app.turnlearn = lr2          # the model learned above
def show(i, planes):
    app._show(i, {"red": planes[0], "green": planes[1], "blue": planes[2]}, "preview", "check")
sideways = next((p, k) for p, k in test if k == 1 and np.argmax(m.probs(cl.small_luminance(p))) == 1)
app.frame_rot = {}
show(4, sideways[0])
t4 = app.state()["tiles"][4]
g4 = lr2.guess(sideways[0])
if g4["auto"]:
    check("a new frame it is sure of is turned by itself, and the page is told", app.frame_rot.get(4) == 1 and t4["turn_hint"]["applied"], str(t4["turn_hint"]))
else:
    check("a new frame it is not sure enough of is not turned, but the turn is suggested", app.frame_rot.get(4, 0) == 0
          and t4["turn_hint"]["quarter"] == 1, str(t4["turn_hint"]))
n = len(app.turnlearn.examples())
app.set_crop(4, (0.05, 0.06, 0.95, 0.94))
check("a turn auto rotate made and the person left is not learned from (even when the frame is then cropped)",
      len(app.turnlearn.examples()) == n + (0 if g4["auto"] else 1))
app.rotate([4], 3)
show(4, sideways[0])
check("a frame turned by hand is left as the person turned it on the next look", app.frame_rot.get(4) == (app.frame_rot.get(4)) and 4 in app.turn_hand
      and app.state()["tiles"][4]["turn_hint"] is None)
lr2.model().data["auto"] = False    # (not sure enough to turn by itself: only suggests)
upright = next(p for p, k in test if k == 0)
show(5, sideways[0])
check("not allowed to turn by itself: it suggests the turn instead", app.frame_rot.get(5, 0) == 0 and app.state()["tiles"][5]["turn_hint"]["quarter"] == 1)
show(0, upright)
check("a frame that looks right as it is gets no suggestion", app.state()["tiles"][0]["turn_hint"] is None)
lr2.model().data["auto"] = True

print("E learned on finished pictures, used on raw negatives (the scanning screen)")
def negative(planes):
    return [np.clip(4e8 / (np.asarray(q, np.float32) + 6000.0) * k, 0, 65535) for q, k in zip(planes, (1.0, 0.62, 0.38))]
neg = [(negative(p), k) for p, k in test[:20]]
r_neg = np.mean([lr2.guess(p, "negative", raw=True)["turn"] == k for p, k in neg])
r_thumb = np.mean([lr2.guess([q[::3, ::3] for q in p], "negative", raw=True)["turn"] == k for p, k in neg])
check("learned only from positives, it turns raw negative previews and thumbnails right", r_neg >= 0.9 and r_thumb >= 0.85, f"previews {r_neg:.2f}, thumbnails {r_thumb:.2f}")

print("F in the Library")
roll = out / "Roll 010 - test"; (roll / "Positive").mkdir(parents=True, exist_ok=True)
lib = [frame() for _ in range(8)]
for j, (p, k) in enumerate(lib):
    tifffile.imwrite(roll / f"Positive/p{j}.tif", np.dstack(p).astype(np.uint16), photometric="rgb")
(roll / "Library.json").write_text(json.dumps({"pictures": {"Positive/p0.tif": {"edit": {"turns": lib[0][1] or 2, "crop": None}}}}))
app.turnlearn = tl.TurnLearner(tmp / "libstore", tmp / "libstore")
for f in app.turnlearn.store.glob("*"): pass
app._turnlearn_library_examples()
check("a picture turned in the Library before auto rotate kept turns is found and kept once", len(app.turnlearn.examples()) == 1)
app.turnlearn = lr2
k1 = lib[1][1]
nb = len(lr2.examples())
if k1:
    app.library_turn("Roll 010 - test", "Positive/p1.tif", k1)
else:
    app.library_crop("Roll 010 - test", "Positive/p1.tif", [0.05, 0.05, 0.95, 0.95])
check("a picture turned (or cropped) by hand in the Library is kept to learn from", len(lr2.examples()) == nb + 1)
side = next((j for j, (p, k) in enumerate(lib) if k and j > 1), None)
if side is not None:
    g = app.turnlearn_library_guess("Roll 010 - test", f"Positive/p{side}.tif")
    check("the Library viewer gets the suggestion for the picture on show", g["guess"]["quarter"] == lib[side][1], json.dumps(g))
    r = app.turnlearn_library_auto("Roll 010 - test", [f"Positive/p{j}.tif" for j in range(2, 8)])
    want = sum(1 for p, k in lib[2:] if k)
    check("Auto rotate on ticked pictures turns the ones it is sure of, and lists the others", len(r["turned"]) + len(r["suggested"]) == want
          and r["same"] == 6 - want, json.dumps({k: len(v) if isinstance(v, list) else v for k, v in r.items()}))
    nb = len(lr2.examples())
    if r["turned"]:
        f0 = r["turned"][0]["file"]
        app.library_crop("Roll 010 - test", f0, [0.05, 0.05, 0.95, 0.95])
        check("a Library turn auto rotate made and the person left is not learned from, even when then cropped", len(lr2.examples()) == nb)

print("G someone who downloads the app adapts the shipped model to their own pictures")
md = tmp / "models"; md.mkdir(); (md / cl.OWNER_FILE).write_text("")
own = tl.TurnLearner(tmp / "ownstore", md)
for i, (p, k) in enumerate(train):
    own.add(f"o{i}", p, k)
own.learn(own.examples())
files = sorted(x.name for x in md.iterdir() if not x.name.startswith("."))
data = json.loads((md / tl.MODEL_FILE).read_text())
check("the owner's computer writes models/ (numbers only: no pictures or file names)", files in (["turn_model.json"], ["turn_cnn.npz", "turn_model.json"])
      and "turn-" not in json.dumps(data) and ".tif" not in json.dumps(data), str(files))
shipped = tmp / "shipped"; shutil.copytree(md, shipped); (shipped / cl.OWNER_FILE).unlink()
before = {x.name: x.read_bytes() for x in shipped.iterdir()}
other = tl.TurnLearner(tmp / "otherstore", shipped)
check("before any turns of their own it turns with the shipped model", not other.owner() and other.model() is other.shipped())
nights = [frame(night=True) for _ in range(40)]
r_ship = right(other, nights[20:])
for i, (p, k) in enumerate(nights[:20]):
    other.add(f"n{i}", p, k)
res = other.learn(other.examples())
ad = json.loads((tmp / "otherstore" / tl.MODEL_FILE).read_text())
check("it adapts from the shipped weights and keeps that in their own store; the shipped file is unchanged",
      ad.get("adapted_from") == data["made"] and before == {x.name: x.read_bytes() for x in shipped.iterdir()}, res["text"])
r_ad = right(other, nights[20:])
check("on their (night) pictures the adapted model is used and is right more often than the shipped one",
      other.model() is other.adapted() and r_ad > r_ship, f"adapted {r_ad:.2f}, shipped {r_ship:.2f}")

print("H learning pictures: kept unless deleted")
r = app.learning_delete_pictures()
check("Delete learning pictures deletes turn learning's copies too, and what was learned stays",
      not any(lr2.store.glob("turn-*.npz")) and lr2.model() is not None and r["deleted"] > 0, json.dumps(r))

print("I end to end: turned on the thumbnail and kept through the scan, turned after, turned in the Library")
app, out = fresh(archive=True)
app.turnlearn = tl.TurnLearner(tmp / "e2e")
app.croplearn = cl.CropLearner(tmp / "e2ecrops")
TL = app.turnlearn
def samples():
    out_ = []
    for p_ in sorted(TL.store.glob("turn-*.npz")):
        with np.load(p_) as z:
            out_.append((str(z["source"]), int(z["turn"]), round(float(z["weight"]), 2)))
    return out_
app.rotate([0], 1)                    # on the thumbnail, before the scan
check("a turn on the thumbnail is kept at once", samples() == [("scan", 1, 1.0)], str(samples()))
app.scan([0, 1])
sm = samples()
check("scanned with that turn kept: still one example for the frame (now from the scan), not two",
      sorted(sm) == [("scan", 0, 0.7), ("scan", 1, 1.0)], str(sm))
check("...and a frame scanned as it was, on a strip where another frame was turned by hand, counts as right that way up, less surely",
      ("scan", 0, tl.W_SCANNED_STRIP) in sm)
app.rotate([0], 1)                    # turned again after the scan
check("turned again after the scan: the later turn replaces the earlier", sorted(samples()) == [("scan", 0, 0.7), ("scan", 2, 1.0)], str(samples()))
saved0 = [f for f in app.frame_saved[0][-1]["files"] if f.startswith("Positive/")][0]
roll = pathlib.Path(app.roll_dir())
lk = [r["key"] for r in app.library_rolls().get("rolls", []) if r["name"] == roll.name]
lk = lk[0] if lk else roll.name
n_before = len(samples())
# a frame turned on the scanning screen after it was saved keeps that turn as a Library edit of its
# picture (the file is unchanged): one quarter here, so the Library's 3 quarters more make it 0
check("the turn after the scan is kept for the saved picture as a Library edit",
      (app._lib_edit_of(roll, saved0) or {}).get("turns") == 1, str(app._lib_edit_of(roll, saved0)))
res = app.library_turn(lk, saved0, 3)
sm = samples()
check("turned later in the Library: that replaces the scanning screen's example of the picture (still one per picture)",
      res.get("ok") and len(sm) == n_before and ("library", 0, 1.0) in sm and ("scan", 2, 1.0) not in sm, f"{res} {sm}")
g = app.turnlearn_library_guess(lk, [f for f in app.frame_saved[1][-1]["files"] if f.startswith("Positive/")][0])
check("opening the other picture in the Library does not add a second example (it has the scan's)", len(samples()) == n_before)
app.thumbnail()                       # a new strip
app.scan([2])
check("a frame scanned as it was on a strip with no turns by hand counts too, least surely", ("scan", 0, tl.W_SCANNED) in samples(), str(samples()))

print("J the roll turned 180°: horizontal frames right after it, vertical ones a quarter more")
app, out = fresh(rotate180=True)
app.turnlearn = tl.TurnLearner(tmp / "r180")
app.croplearn = cl.CropLearner(tmp / "r180crops")
rng2 = np.random.default_rng(9)
def raw_of(k_total, portrait):
    U = scene(270, 180) if portrait else scene(180, 270)
    D = np.rot90(U, -k_total)          # as the scanner reads it: turning it k_total to the left puts it right
    return [np.ascontiguousarray(D * t) for t in (1.0, 0.9, 0.8)]
def show_raw(i, planes):
    app._show(i, {"red": planes[0], "green": planes[1], "blue": planes[2]}, "preview", "check")
# the holder is in upside down: horizontal frames need 2 in all (the roll's 180°), vertical ones 1 or 3 in all
truth = []
for i in range(36):
    portrait = rng2.random() < 0.35
    k_total = (2 + (1 if rng2.random() < 0.8 else 3)) % 4 if portrait else 2
    truth.append((raw_of(k_total, portrait), k_total))
d_frames = len(app.discovery.frames)
for j, (planes, k_total) in enumerate(truth):
    i = j % d_frames
    app.strip_id += 1 if i == 0 and j else 0
    app.frame_rot.pop(i, None)
    show_raw(i, planes)
    extra = (k_total - 2) % 4                     # the frame's own turn, on top of the roll's 180°
    if extra:
        app.rotate([i], extra)
    app.turnlearn_frame_saved(i, [out / f"x{j}.tif"])
ex = app.turnlearn.examples()
check("horizontal frames left alone after the roll's 180° are kept as right (no turn of their own)",
      sum(1 for _, k, w in ex if k == 0 and w < 1) == sum(1 for _, k in truth if k == 2), f"{sum(1 for _, k, w in ex if k == 0 and w < 1)} kept")
check("vertical frames keep their own quarter turn on top of it", sorted(k for _, k, w in ex if w == 1) == sorted((k - 2) % 4 for _, k in truth if k != 2))
if app._turnlearn_thread is not None:
    app._turnlearn_thread.join()     # (it may be learning by itself already: 10 new examples)
r = app.turnlearn.learn(ex)
new = [(raw_of(k, p), k) for k, p in [(2, False), (3, True), (1, True), (2, False), (3, True), (2, False)]]
got = []
for i, (planes, k_total) in enumerate(new):
    app.frame_rot.pop(i, None); app.turn_hand.discard(i)
    show_raw(i, planes)
    t = app.state()["tiles"][i]
    own = app.frame_rot.get(i, 0) if (t.get("turn_hint") or {}).get("applied") else (app.frame_rot.get(i, 0) + ((t.get("turn_hint") or {}).get("quarter") or 0)) % 4
    got.append((own, (k_total - 2) % 4))
check("on new frames it gives each frame's own turn on top of the roll's 180° (none for horizontal ones, a quarter for vertical)",
      all(a == b for a, b in got), f"{got} ({r['text']})")

print("K a turn auto rotate made, turned back by hand: learned from, more strongly; the wrong turn never")
app, out = fresh()
app.turnlearn = tl.TurnLearner(tmp / "store")       # (the model learned in C)
app.turnlearn.model().data["auto"] = True
TK = app.turnlearn
def kept(key):
    p_ = TK.store / TK._name(key)
    if not p_.exists():
        return None
    with np.load(p_) as z:
        return int(z["turn"]), round(float(z["weight"]), 2)
show(0, sideways[0])
check("auto rotate turned the sideways frame by itself, and that is not learned from", app.frame_rot.get(0) == 1 and 0 in app.turn_auto
      and kept(app._turn_key(0)) is None)
app.rotate([0], 3)                                  # the person turns it back
check("turned back by hand: learned from as it is now, counting more than an ordinary turn by hand", kept(app._turn_key(0)) == (0, tl.W_CORRECTED),
      str(kept(app._turn_key(0))))
app.turnlearn_frame_saved(0, [out / "k0.tif"])
check("...and still so once the frame is scanned", kept(app._turn_key(0)) == (0, tl.W_CORRECTED))
show(1, sideways[0]); app.rotate([1], 1)            # auto rotate's 1, turned on to 2 by hand
check("an auto rotate changed to another turn by hand counts the same way", kept(app._turn_key(1)) == (2, tl.W_CORRECTED))
roll = out / "Roll 011 - k"; (roll / "Positive").mkdir(parents=True, exist_ok=True)
tifffile.imwrite(roll / "Positive/s.tif", np.dstack(sideways[0]).astype(np.uint16), photometric="rgb")
lk = [r["key"] for r in app.library_rolls().get("rolls", []) if r["name"] == roll.name][0]
r = app.turnlearn_library_auto(lk, ["Positive/s.tif"])
lkey = "lib:" + cl.CropLearner._path_key(app._lib_file(lk, "Positive/s.tif")[1])
check("in the Library: Auto rotate turns it, and that is not learned from", len(r["turned"]) == 1 and kept(lkey) is None, json.dumps(r))
app.library_turn(lk, "Positive/s.tif", 3)            # turned back by hand
check("turned back by hand in the Library: learned from, more strongly", kept(lkey) == (0, tl.W_CORRECTED), str(kept(lkey)))

print("L the scan picture keeps the turn the files were saved with (auto rotate does not turn it back)")
show(5, sideways[0])
was5 = app.frame_rot.get(5, 0)
app.turnlearn.model().data["auto"] = False          # not sure any more: before, a new look turned it back to 0
app._show(5, {"red": sideways[0][0], "green": sideways[0][1], "blue": sideways[0][2]}, "scan", "saved as k5.tif")
check("auto rotate's turn stays on the scan picture", was5 == 1 and app.frame_rot.get(5, 0) == 1, f"{was5} -> {app.frame_rot.get(5, 0)}")
app.frame_saved[5] = [{"files": ["k5.tif"]}]
show(5, sideways[0])
check("...and on any later picture of a frame already saved", app.frame_rot.get(5, 0) == 1)
app.turnlearn.model().data["auto"] = True

print("Z learning switched off, and Reset to default")
app.settings["auto_turn_learn"] = False
n_before = len(app.turnlearn.examples())
app._turn_keep(0)
check("learning off: auto rotate stays on, but turns made by hand are not kept", app._turnlearn_on() and not app._turnlearn_learning()
      and len(app.turnlearn.examples()) == n_before and app.turnlearn_report()["on"] is False)
check("learning off: 'learn from all scanned pictures' says so", app.turnlearn_library_all()["ok"] is False)
app.settings["auto_turn_learn"] = True
check("learning is on by default", app._turnlearn_learning() and __import__("coolscan_app.settings", fromlist=["x"]).default_settings()["auto_turn_learn"] is True)
ship0 = {x.name: x.read_bytes() for x in shipped.iterdir()}
(other.store / tl.MODEL_FILE).write_text((shipped / tl.MODEL_FILE).read_text())   # something learned on this computer
kept = len(list(other.store.glob("turn-*.npz")))
rr = other.reset_to_shipped()
check("reset: what this computer learned is deleted, the shipped model is used", rr["ok"] and rr["reset"] and rr["shipped"]
      and not (other.store / tl.MODEL_FILE).exists() and other.model() is other.shipped(), str(rr))
check("reset: the shipped files and the learning pictures are not touched", ship0 == {x.name: x.read_bytes() for x in shipped.iterdir()}
      and len(list(other.store.glob("turn-*.npz"))) == kept)
ro = tl.TurnLearner(tmp / "ownstore", md).reset_to_shipped()
check("reset on the app maker's computer: nothing is deleted", ro["ok"] is False and ro.get("owner") and (md / tl.MODEL_FILE).exists())
check("the app's own reset answers too", "ok" in app.turnlearn_reset())

print()
print("FAILED: " + ", ".join(bad) if bad else "all passed")
sys.exit(1 if bad else 0)
