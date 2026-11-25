"""Archive mode is on by default and at each start (unless "Start in Archive mode" is off);
the separate infrared file (_IR.tif) is no longer offered or saved."""
import sys, json, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import cs_next as cs
cs.app.default_settings = cs.settings.default_settings  # the real defaults, not the older checks' ones
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)
def started(saved):
    cs.SETTINGS_FILE.write_text(json.dumps(saved)); app = cs.App(demo=True); app.load_settings(); return app.settings

st = cs.App(demo=True).settings
check("a first start is in Archive mode, set to start in it", st["archive"] and st["archive_default"])
check("no separate infrared file by default", st["ir"] is False)
st = started({"archive": False, "ir": True, "defaults_version": 5})
check("settings from before: Archive mode switched on once, _IR.tif off", st["archive"] and not st["ir"] and st["defaults_version"] == 6)
st = started({"archive": False, "archive_default": True, "defaults_version": 6})
check("turned off last time, but starts in Archive mode", st["archive"])
st = started({"archive": False, "archive_default": False, "defaults_version": 6})
check("'Start in Archive mode' off: starts the way it was left (off)", not st["archive"])
st = started({"archive": True, "archive_default": False, "defaults_version": 6})
check("'Start in Archive mode' off: starts the way it was left (on)", st["archive"])
check("_IR.tif is not part of saved settings sets", "ir" not in cs.PRESET_KEYS if hasattr(cs, "PRESET_KEYS") else True)

app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
app.settings.update(outdir=str(out), dpi=145)
app.connect(); app.thumbnail(); app.scan([0])
names = sorted(str(p.relative_to(out)) for p in out.rglob("*.tif"))
check("an Archive scan saves RAW and Positive, no _IR.tif", any("/RAW/" in n for n in names) and any("/Positive/" in n for n in names)
      and not any(n.endswith("_IR.tif") for n in names), str(names))
app.settings["archive"] = False; app.scan([1])
names = sorted(str(p.relative_to(out)) for p in out.rglob("*.tif"))
check("an ordinary scan saves no _IR.tif either", not any(n.endswith("_IR.tif") for n in names), str(names))

print("all passed" if not bad else f"FAILED {bad}")
sys.exit(1 if bad else 0)
