"""The scan queue (take a frame out, add one, move one earlier while a strip is scanned) and frames
already scanned, cropped or turned again on the main screen (kept as a Library edit, file unchanged)."""
import json, sys, tempfile, pathlib, threading, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import cs_next as cs
import coolscan_app.settings as settings_module

tmp = pathlib.Path(tempfile.mkdtemp())
settings_module.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

app = cs.App(demo=True)
app.settings.update(outdir=str(tmp / "out"), dpi=362, clean=False, name_date=False, basename="Scan", use_roll=False)
app.connect(); app.thumbnail()

# -- the queue: changes made while frame 1 is being scanned
order, gate = [], threading.Event()
real = app._scan_one_as_set
def one(s, d, i, *a):
    order.append(i)
    if i == 0:
        check("the queue is in the state while scanning", [it["i"] for it in app.state()["queue"]] == [0, 1, 2, 3])
        check("remove a waiting frame", app.queue_change("remove", 2)["ok"])
        check("add a frame at the end", app.queue_change("add", 5)["ok"])
        check("frame 1 again: it is being scanned, so not added", not app.queue_change("add", 0)["ok"])
        check("move frame 6 earlier", app.queue_change("earlier", 5)["ok"])
        check("frame 3 is gone: cannot move it", not app.queue_change("earlier", 2)["ok"])
    return real(s, d, i, *a)
app._scan_one_as_set = one
app.scan([0, 1, 2, 3])
check("scanned in the changed order", order == [0, 1, 5, 3], str(order))
check("queue gone afterwards", app.state()["queue"] == [])
check("no queue: a change is refused", not app.queue_change("add", 1)["ok"])
order.clear()
def again(s, d, i, *a):
    order.append(i)
    if i == 0 and order.count(0) == 1:
        app.queue_change("add", 0)   # not while it is scanned
    if i == 1 and len(order) == 2:
        check("a frame done already can be added to scan again", app.queue_change("add", 0)["ok"])
    return real(s, d, i, *a)
app._scan_one_as_set = again
app.scan([0, 1])
check("frame 1 scanned twice", order == [0, 1, 0], str(order))
app._scan_one_as_set = real

# -- a scanned frame cropped and turned again on the main screen
lib = tmp / "out" / "Library.json"
name = app.frame_saved[3][-1]["files"]
check("frame 4 was saved", bool(name), str(name))
app.set_crop(3, [0.1, 0.2, 0.6, 0.7])
marks = json.loads(lib.read_text()) if lib.exists() else {}
e = (marks.get("pictures", {}).get(name[0]) or {}).get("edit")
check("its crop is kept for the saved picture", e == {"turns": 0, "crop": [0.1, 0.2, 0.6, 0.7]}, str(e))
before = (tmp / "out" / name[0]).stat().st_mtime
app.rotate([3], 1)
marks = json.loads(lib.read_text())
e = marks["pictures"][name[0]]["edit"]
check("a turn: one quarter, crop turned with it", e["turns"] == 1 and e["crop"] == [0.2, 0.4, 0.7, 0.9], str(e))
check("the file itself is unchanged", (tmp / "out" / name[0]).stat().st_mtime == before)
app.rotate([3], 3); app.set_crop(3, None)
marks = json.loads(lib.read_text())
check("crop cleared and turned back: no edit", marks.get("pictures", {}).get(name[0], {}).get("edit") is None, str(marks.get("pictures")))
# saved with a crop: a new crop inside it is relative to what was saved
app.set_crop(4, [0.2, 0.2, 0.8, 0.8]); app.scan([4])
f5 = app.frame_saved[4][-1]["files"][0]
app.set_crop(4, [0.2, 0.2, 0.5, 0.8])
e = json.loads(lib.read_text())["pictures"][f5]["edit"]
check("a crop inside the saved crop: the left half of the file", e["crop"] == [0.0, 0.0, 0.5, 1.0], str(e))
app.set_crop(4, [0.0, 0.0, 1.0, 1.0])
e = json.loads(lib.read_text()).get("pictures", {}).get(f5, {}).get("edit")
check("a crop past what was saved: cut to the file (no edit)", e is None, str(e))

# -- scanned already with nothing changed since: the app can tell, so the page can ask first
app2 = cs.App(demo=True)
app2.settings.update(outdir=str(tmp / "out2"), dpi=362, clean=False, name_date=False, basename="Scan", use_roll=False)
app2.connect(); app2.thumbnail()
check("nothing scanned yet: nothing to ask about", app2.scanned_same([0, 1, 2]) == [] and app2.scanned_same() == [])
app2.scan([0, 1])
check("frames 1 and 2 scanned: the same again", app2.scanned_same([0, 1, 2]) == [0, 1], str(app2.scanned_same([0, 1, 2])))
check("no frames named = the whole strip", app2.scanned_same() == [0, 1], str(app2.scanned_same()))
app2.set_frame_settings([0], {"dpi": 725})
check("frame 1 given its own resolution: not the same any more", app2.scanned_same([0, 1]) == [1])
app2.set_frame_settings([0], reset=True)
check("taken back: the same again", app2.scanned_same([0, 1]) == [0, 1])
app2.set_crop(1, [0.1, 0.1, 0.9, 0.9])
check("frame 2 cropped: not the same", app2.scanned_same([0, 1]) == [0])
app2.set_crop(1, None)
app2.settings["grain"] = 2 if app2.settings.get("grain") != 2 else 1
check("a shared setting changed: none is the same", app2.scanned_same([0, 1]) == [])
app2.scan([0])
check("scanned with the new setting: frame 1 the same again, frame 2 not", app2.scanned_same([0, 1]) == [0])
check("several frames into the queue at once need a scan", not app2.queue_add_many([2, 3])["ok"])
got = {}
real2 = app2._scan_one_as_set
def many(s, d, i, *a):
    if i == 2 and not got:
        got["add"] = app2.queue_add_many([2, 4, 5])        # frame 3 is being scanned: passed over
        got["out"] = app2.queue_remove_many([3, 5, 0])     # frame 4 and 6 wait; frame 1 is not in it
        got["left"] = [it["i"] for it in app2.state()["queue"]]
    return real2(s, d, i, *a)
app2._scan_one_as_set = many
app2.scan([2, 3])
check("add several: those not in the scan yet", got.get("add") == {"ok": True, "added": [4, 5]}, str(got.get("add")))
check("take several out: those waiting", got.get("out") == {"ok": True, "removed": [3, 5]}, str(got.get("out")))
check("what is left is scanned", got.get("left") == [2, 4], str(got.get("left")))
print("FAILED: " + ", ".join(bad) if bad else "all queue and edit checks passed")
sys.exit(1 if bad else 0)
