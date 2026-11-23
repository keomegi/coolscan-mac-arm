import sys, os, json, time, tempfile, pathlib, hashlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import cs_next as cs
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
D = time.strftime("%Y%m%d")
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)
def fresh(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=145, ir=False), **kw})
    app.connect(); app.thumbnail(); return app, out
def names(out): return sorted(str(p.relative_to(out)) for p in out.rglob("*.tif"))
def sums(out): return {str(p.relative_to(out)): hashlib.sha1(p.read_bytes()).hexdigest() for p in out.rglob("*.tif")}

print("F names: prefix, date, roll number, picture number")
app, out = fresh(basename=""); app.scan([0, 1])
check("no prefix: date_001_R001.tif", names(out) == [f"Roll 001/{D}_001_R001.tif", f"Roll 001/{D}_002_R001.tif"], str(names(out)))
check("the prefix starts out empty", cs.App(demo=True).settings["basename"] == "")
app, out = fresh(basename="Scan"); app.scan([0])
check("with a prefix: Scan_date_001_R001.tif", names(out) == [f"Roll 001/Scan_{D}_001_R001.tif"], str(names(out)))
app, out = fresh(basename="", name_date=False); app.scan([0])
check("date switched off: 001_R001.tif", names(out) == ["Roll 001/001_R001.tif"], str(names(out)))
app, out = fresh(basename="Trip_", ir=True, archive=True); app.scan([0])
check("own _ in the prefix not doubled; RAW, IR and Positive alike",
      names(out) == [f"Roll 001/Positive/Trip_{D}_001_R001.tif", f"Roll 001/RAW/Trip_{D}_001_R001.tif", f"Roll 001/RAW/Trip_{D}_001_R001_IR.tif"], str(names(out)))
check("no suffix and no roll-of-the-day setting left", not {"suffix", "roll_tag", "roll_day", "roll_day_count"} & set(app.settings))
app, out = fresh(basename="", photo_date="2005-06-14"); app.scan([0])
check("the Photo taken date does not go into the name", names(out) == [f"Roll 001/{D}_001_R001.tif"], str(names(out)))
app.new_roll({}); app.new_roll({"film_brand": "Kodak"})
check("the roll number in the name is always the roll being scanned", app.state()["sample_name"] == f"{D}_001_R003.tif" and app.state()["next_number"] == 1, app.state()["sample_name"])
real_strftime = cs.time.strftime
app, out = fresh(basename="")
real_scan = app.session.scan_frame
def midnight(frame, **kw):                     # the clock passes midnight during the batch
    r = real_scan(frame, **kw)
    cs.time.strftime = lambda f, *a: "20261123" if f == "%Y%m%d" else real_strftime(f, *a)
    return r
app.session.scan_frame = midnight
app.scan([0, 1]); cs.time.strftime = real_strftime
check("a batch that runs past midnight keeps one date", names(out) == [f"Roll 001/{D}_001_R001.tif", f"Roll 001/{D}_002_R001.tif"], str(names(out)))
cs.time.strftime = lambda f, *a: "20261123" if f == "%Y%m%d" else real_strftime(f, *a)
app.session.scan_frame = real_scan
check("next day: new date, the number counts on", app.state()["sample_name"] == "20261123_003_R001.tif", app.state()["sample_name"])
app.scan([2]); cs.time.strftime = real_strftime
check("...and is saved that way", "Roll 001/20261123_003_R001.tif" in names(out), str(names(out)))

print("G counter with names from every version")
app, out = fresh(ir=True)
roll = out / "Roll 003 - Kodak"; roll.mkdir()
for n in range(1, 5):                       # this afternoon's version: Scan001_R003.tif
    (roll / f"Scan{n:03d}_R003.tif").write_text(f"old {n}"); (roll / f"Scan{n:03d}_R003_IR.tif").write_text(f"old ir {n}")
(roll / "Scan_005_R003.tif").write_text("20:35 version"); (roll / "Scan_005_R003_IR.tif").write_text("20:35 version ir")
(roll / "006_R003.tif").write_text("20:35 version, no prefix")
(roll / "20261122_R003_007.tif").write_text("20:47 version: roll number before the picture number")
(roll / "20261122_R003_007_IR.tif").write_text("20:47 version ir")
app.settings["roll"] = roll.name
before = sums(out)
check("mid-roll after the update: next number is 8", app.next_number() == 8 and app.state()["next_number"] == 8, str(app.next_number()))
app.settings["basename"] = ""
app.scan([0, 1])
check("it carries on as date_008_R003 and 009", (roll / f"{D}_008_R003.tif").is_file() and (roll / f"{D}_009_R003_IR.tif").is_file(), str(names(out)[:6]))
check("the pictures from before are untouched", all(sums(out)[k] == v for k, v in before.items()))
app.settings["start"] = 20; app.scan([0])
check("typed 20: used once, then it counts on (21)", (roll / f"{D}_020_R003.tif").is_file() and app.settings["start"] == 0 and app.next_number() == 21)
app.settings["archive"] = True; app.scan([0])
check("Archive mode switched on mid-roll: carries on", (roll / "RAW" / f"{D}_021_R003.tif").is_file() and (roll / "Positive" / f"{D}_021_R003.tif").is_file() and app.next_number() == 22)
app.settings["archive"] = False
(roll / f"{D}_022_r003.TIF").write_text("same name in other letters")
check("a name that differs only in capitals counts as taken", app.next_number() == 23, str(app.next_number()))

print("I roll number change with names from every version")
allbefore = sorted(p.name for p in roll.rglob("*") if p.is_file()); digest = sorted(hashlib.sha1(p.read_bytes()).hexdigest() for p in roll.rglob("*.tif"))
r = app.renumber_roll(1)
new = out / app.settings["roll"]
got = sorted(p.name for p in new.rglob("*.tif"))
check("roll 003 -> 001: every style of name follows", r.get("ok") and app.settings["roll"] == "Roll 001 - Kodak"
      and "Scan001_R001.tif" in got and "Scan004_R001_IR.tif" in got and "Scan_005_R001.tif" in got and "006_R001.tif" in got
      and "20261122_R001_007.tif" in got and "20261122_R001_007_IR.tif" in got
      and f"{D}_008_R001.tif" in got and f"{D}_009_R001_IR.tif" in got and f"{D}_021_R001.tif" in got
      and not any("R003" in n.upper() for n in got), json.dumps(r) + " " + str(got[:6]))
allafter = sorted(p.name for p in new.rglob("*") if p.is_file())
check("same pictures, byte for byte", sorted(hashlib.sha1(p.read_bytes()).hexdigest() for p in new.rglob("*.tif")) == digest)
check("same number of files; the one with .TIF in capitals follows too", len(allafter) == len(allbefore) and f"{D}_022_R001.TIF" in allafter, f"{len(allbefore)} -> {len(allafter)}")
check("the counter still counts on afterwards", app.next_number() == 23, str(app.next_number()))
app.settings["basename"] = "R001"                       # a prefix that looks like a roll number
app.scan([0]); r = app.renumber_roll(2)
check("a prefix that looks like the roll number is left alone", (out / app.settings["roll"] / f"R001_{D}_023_R002.tif").is_file(), str(sorted(p.name for p in (out / app.settings["roll"]).glob("R001*"))))
app.new_roll({})
check("New roll starts at 1 again", app.next_number() == 1 and app.settings["roll"] == "Roll 003", f"{app.settings['roll']} {app.next_number()}")

print("H settings saved by earlier versions")
cs.SETTINGS_FILE.write_text(json.dumps({"start": 1, "defaults_version": 2, "basename": "Scan", "suffix": "x", "roll": "Roll 002 - x", "roll_tag": "date_day", "notify_imessage": "+15550100"}))
a = cs.App(demo=True); a.load_settings()
check("old settings: the default prefix 'Scan' is emptied, old switches are dropped", a.settings["basename"] == "" and a.settings["name_date"] is True and a.settings["start"] == 0
      and a.settings["defaults_version"] == 5 and a.settings["roll"] == "Roll 002 - x" and a._stem(None, 5) == f"{D}_005_R002", a._stem(None, 5))
check("...and nothing of them is written back", not {"suffix", "roll_tag", "notify_imessage"} & set(json.loads(cs.SETTINGS_FILE.read_text())))
cs.SETTINGS_FILE.write_text(json.dumps({"start": 0, "defaults_version": 4, "basename": "Trip", "roll": "Roll 001 - x", "roll_tag": "day"}))
a = cs.App(demo=True); a.load_settings()
check("a prefix the person typed is kept; 'roll of the day' becomes the roll's own number", a.settings["basename"] == "Trip" and a._stem(None, 5) == f"Trip_{D}_005_R001", a._stem(None, 5))
cs.SETTINGS_FILE.write_text(json.dumps({"defaults_version": 5, "basename": "Scan", "name_date": False}))
a = cs.App(demo=True); a.load_settings()
check("'Scan' typed again after the update stays; date switched off stays off", a.settings["basename"] == "Scan" and a.settings["name_date"] is False)
print(); print("FAILED: " + ", ".join(bad) if bad else "all passed")
