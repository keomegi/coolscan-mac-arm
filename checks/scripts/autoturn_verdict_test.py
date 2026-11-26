"""Auto rotate's own turns get Accept / Reject on the frame card; the strip's "upside down" is found first
(every frame 180°) with its own Accept / Reject; "Learn from the whole Library" keeps every Library picture
with the turn it has. A stand-in model says what auto rotate sees, so only the app's handling is checked."""
import json, os, sys, tempfile, pathlib, time
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

class Fake:
    """Says the same for every picture: `p`, or `first` for the next `n` pictures."""
    def __init__(self, p): self.p, self.first, self.n = np.array(p, float), None, 0
    def usable(self): return True
    def may_turn(self): return True
    def probs(self, L):
        if self.n > 0:
            self.n -= 1
            return self.first
        return self.p

app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
app.settings.update(outdir=str(out), dpi=290, ir=False, clean=False, scan_log=False, film="positive", rotate180=False)
app.turnlearn = tl.TurnLearner(tmp / "store"); app.croplearn = cl.CropLearner(tmp / "crops")
fake = Fake([0.02, 0.95, 0.02, 0.01])          # every frame: a quarter to the left, sure
app.turnlearn.model = lambda: fake
app.connect(); app.session.borders = True; app.thumbnail()
st = app.state(); n = len(st["frames"])
print("A a frame card's Accept / Reject")
check("frames auto rotate turned by itself are marked on their cards", n >= 3 and all(st["tiles"][i]["turn_auto"] for i in range(n)))
check("a strip of sideways pictures says nothing about the holder: not turned 180°", st["strip_turn"] is None and not app.settings["rotate180"])
r = app.turnlearn_verdict([0], True)
ex = app.turnlearn.examples()
check("Accept: kept as it is, no longer marked, learned from as a right guess", r["frames"] == [0] and app.frame_rot.get(0) == 1
      and not app.state()["tiles"][0]["turn_auto"] and len(ex) == 1 and ex[0][1] == 1 and ex[0][2] == 1.0, str([e[1:] for e in ex]))
r = app.turnlearn_verdict([1], False)
ex = sorted(e[1:] for e in app.turnlearn.examples())
check("Reject: turned back, no longer marked, learned from more strongly", app.frame_rot.get(1, 0) == 0
      and not app.state()["tiles"][1]["turn_auto"] and (0, tl.W_CORRECTED) in ex, str(ex))
check("a frame not turned by auto rotate: Accept / Reject do nothing", app.turnlearn_verdict([0], False)["frames"] == [] and app.frame_rot.get(0) == 1)

print("B the strip in upside down: found first, for every frame")
was_roll = (app.settings["use_roll"], app.settings["roll"])
app.settings.update(use_roll=False, roll="")   # no roll: nothing remembered between strips (B2 is with a roll)
fake.p = np.array([0.9, 0.03, 0.04, 0.03])     # each frame, once the strip is turned: the right way up
fake.first, fake.n = np.array([0.05, 0.05, 0.85, 0.05]), n   # the strip as the scanner sends it: upside down
app.thumbnail()
st = app.state()
check("an upside-down strip: every frame turned 180°, shown with Accept / Reject", app.settings["rotate180"] is True
      and st["strip_turn"] and st["strip_turn"]["turned"] and st["strip_turn"]["agree"] == n, json.dumps(st["strip_turn"]))
check("...and each frame is then the right way up on its own (no extra turn)", not any(app.frame_rot.get(i, 0) for i in range(n)))
app.turnlearn_strip_verdict(True)
check("Accept: kept, the bar goes", app.settings["rotate180"] is True and app.state()["strip_turn"] is None)
fake.first, fake.n = np.array([0.05, 0.05, 0.85, 0.05]), n
app.thumbnail()
app.turnlearn_strip_verdict(False)
check("Reject: turned back for every frame, the bar goes", app.settings["rotate180"] is False and app.state()["strip_turn"] is None)
fake.first, fake.n = np.array([0.85, 0.05, 0.05, 0.05]), n
app.thumbnail()
check("a strip the right way up after one upside down: 180° taken off, with the bar", app.settings["rotate180"] is False
      and app.state()["strip_turn"] is None)
app.settings["rotate180"] = True
fake.first, fake.n = np.array([0.85, 0.05, 0.05, 0.05]), n
app.thumbnail()
check("...after one that was turned 180°: taken off, with the bar", app.settings["rotate180"] is False
      and (app.state()["strip_turn"] or {}).get("changed") is True)
fake.first, fake.n = np.array([0.4, 0.1, 0.4, 0.1]), n
app.settings["rotate180"] = True
app.thumbnail()
check("not sure: kept as the last strip was, no bar", app.settings["rotate180"] is True and app.state()["strip_turn"] is None)
print("B2 the answer is kept for the roll: asked once, again only when a strip comes out the other way")
DOWN, UP, UNSURE = np.array([0.05, 0.05, 0.85, 0.05]), np.array([0.85, 0.05, 0.05, 0.05]), np.array([0.4, 0.1, 0.4, 0.1])
def strip(first, rot=None):
    if rot is not None:
        app.settings["rotate180"] = rot
    fake.first, fake.n = first, n
    app.thumbnail()
    return app.state()["strip_turn"]
app.settings.update(use_roll=True, roll="Roll A", strip_turn_rolls={})
check("first strip of a roll, upside down: asked", (strip(DOWN, False) or {}).get("turned") is True)
app.turnlearn_strip_verdict(True)
check("Accept: kept for the roll", app.settings["strip_turn_rolls"] == {"Roll A": True}, str(app.settings["strip_turn_rolls"]))
check("next strip the same way: turned 180°, not asked again", strip(DOWN) is None and app.settings["rotate180"] is True)
check("...also when the last strip scanned was another way round", strip(DOWN, False) is None and app.settings["rotate180"] is True)
check("a strip not clear: as said for the roll, not asked", strip(UNSURE, False) is None and app.settings["rotate180"] is True)
st2 = strip(UP)
check("a strip the other way round: asked again", st2 and st2["turned"] is False and st2.get("flipped") is True
      and app.settings["rotate180"] is False, json.dumps(st2))
app.turnlearn_strip_verdict(True)
check("...Accept: the roll now goes that way", app.settings["strip_turn_rolls"] == {"Roll A": False} and strip(UP) is None)
st2 = strip(DOWN)
app.turnlearn_strip_verdict(False)
check("Reject: turned back, and that is kept for the roll", st2 and app.settings["rotate180"] is False
      and app.settings["strip_turn_rolls"] == {"Roll A": False})
app.settings["roll"] = "Roll B"
check("another roll: asked on its own", (strip(DOWN) or {}).get("turned") is True and "flipped" not in app.state()["strip_turn"])
app.turnlearn_strip_verdict(True)
check("...and each roll keeps its own answer", app.settings["strip_turn_rolls"] == {"Roll A": False, "Roll B": True})
app.settings.update(use_roll=False, roll="")
check("no roll chosen: nothing remembered, asked as before", (strip(DOWN, False) or {}).get("turned") is True)
app.turnlearn_strip_verdict(True)
app.settings.update(use_roll=was_roll[0], roll=was_roll[1], rotate180=True)
app.settings["show_rotate180"] = False
check("the Film card's switch is hidden unless asked for in Settings", app.state()["settings"]["show_rotate180"] is False)

print("C learn from the whole Library")
del app.turnlearn.model   # the real one again
roll = out / "Roll 020 - lib"; (roll / "Positive").mkdir(parents=True, exist_ok=True); (roll / "RAW").mkdir()
for j in range(3):
    tifffile.imwrite(roll / f"Positive/p{j}.tif", (np.random.default_rng(j).random((120, 180, 3)) * 65535).astype(np.uint16), photometric="rgb")
tifffile.imwrite(roll / "RAW/p0.tif", np.zeros((120, 180, 3), np.uint16), photometric="rgb")
lk = [r["key"] for r in app.library_rolls()["rolls"] if r["name"] == roll.name][0]
app.library_turn(lk, "Positive/p1.tif", 3, by_hand=False)   # (turned by auto rotate: not learned from by itself)
before = app.turnlearn.counts()["library"]
r = app.turnlearn_library_all()
for _ in range(300):
    if not app._turnlearn_all_thread.is_alive(): break
    time.sleep(0.1)
al = app.turnlearn_report()["all"]
lib = [e for e in app.turnlearn.examples()]
check("every finished Library picture is kept, with the turn it has", r["ok"] and al and not al["busy"] and al["added"] == 3, json.dumps(al))
check("...and a RAW file beside one: as the scanner reads it and as small as a thumbnail pass", al["raws"] == 2
      and app.turnlearn.counts()["library"] == before + 5, json.dumps(al))
check("...its turn as kept in the Library (3 for the one turned to the right)", sorted(e[1] for e in lib).count(3) >= 1)

print("D thumbnail passes kept inside the RAW files, and learned from")
from coolscan_app.imaging import files as imf
app.settings.update(archive=True, rotate180=False)
fake2 = Fake([0.9, 0.03, 0.04, 0.03]); app.turnlearn.model = lambda: fake2
app.thumbnail()
app.rotate([2], 1)                                  # turned by hand, then scanned
app.scan([2])
saved = app.frame_saved[2][-1]
folder = pathlib.Path(saved["folder"])
raw = folder / [f for f in saved["files"] if f.startswith("RAW/")][0]
kept = imf.read_thumb_pass(raw)
rh, rw = tifffile.imread(raw).shape[:2]
check("the frame's thumbnail pass is kept inside its RAW file, turned like it", kept is not None and len(kept) == 3
      and (rh > rw) == (kept[0].shape[0] > kept[0].shape[1]) and max(kept[0].shape) < 200, f"raw {rw}x{rh}, thumb {kept[0].shape if kept else None}")
check("...and the RAW file reads as before", tifffile.imread(raw).shape[2] in (3, 4) and imf.read_tiff_info(raw)["coolscan"].get("film") == "positive"
      and imf.read_tiff_info(raw).get("has_thumb_pass") is True)
check("no extra files are made", sorted(p.name for p in raw.parent.iterdir()) == [raw.name], str(sorted(p.name for p in raw.parent.iterdir())))
key = [r["key"] for r in app.library_rolls()["rolls"] if r["name"] == folder.name][0]
n1, n2 = len(app.turnlearn.examples()), len(app.turnlearn.examples(views=True))
check("the scanned frame is learned from as the thumbnail pass saw it too (still one example per picture)", n2 == n1 + 1, f"{n1} {n2}")
del app.turnlearn.model
r = app.turnlearn_library_all()
for _ in range(300):
    if not app._turnlearn_all_thread.is_alive(): break
    time.sleep(0.1)
lk2 = "lib-thumb:" + cl.CropLearner._path_key(raw)
check("Learn from the whole Library uses the thumbnail pass kept in the RAW file", (tmp / "store" / app.turnlearn._name(lk2)).exists(),
      json.dumps(app.turnlearn_report()["all"]))

print()
print("FAILED: " + ", ".join(bad) if bad else "all passed")
sys.exit(1 if bad else 0)
