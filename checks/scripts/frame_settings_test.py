"""Settings for one frame: a frame scanned with its own resolution or grain reduction, the others with
the shared settings; nothing of a frame's own settings ends up in the saved settings."""
import json, sys, tempfile, pathlib
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
app.settings.update(outdir=str(tmp / "out"), dpi=725, clean=False, name_date=False, basename="Scan", grain=0)
app.connect(); app.thumbnail()
st = app.state()
check("no frame has its own settings at first", st["frame_settings"] == {})
r = app.set_frame_settings([1], {"dpi": "362", "grain": 2})
check("set for frame 2", r["ok"] and app.state()["frame_settings"] == {"1": {"dpi": 362, "grain": 2}}, str(app.state()["frame_settings"]))
app.set_frame_settings([1], {"grain": None})
check("'same as all frames' takes one back", app.state()["frame_settings"] == {"1": {"dpi": 362}}, str(app.state()["frame_settings"]))
app.set_frame_settings([2], {"dpi": 725})
check("a value equal to the shared one is not kept", "2" not in app.state()["frame_settings"])
check("not a frame setting: left out", app.set_frame_settings([1], {"outdir": "/x"})["ok"] and "outdir" not in app.state()["frame_settings"]["1"])
check("no frame: refused", not app.set_frame_settings([99], {"dpi": 362})["ok"])

seen = {}
real = app.session.scan_frame
def spy(frame, *a, **kw):
    seen.setdefault(len(seen), (kw.get("dpi"), app.state()["settings"]["dpi"]))
    app.save_settings()           # saved in the middle of a frame with its own settings
    return real(frame, *a, **kw)
app.session.scan_frame = spy
app.scan([0, 1])
check("frame 1 scanned at the shared 725 dpi, frame 2 at its own 362", [v[0] for v in seen.values()] == [725, 362], str(seen))
check("the page sees the shared settings while frame 2 is scanned", all(v[1] == 725 for v in seen.values()), str(seen))
check("shared settings back afterwards", app.settings["dpi"] == 725 and app.frame_kept is None)
saved = json.loads(settings_module.SETTINGS_FILE.read_text())
check("a frame's own setting is never saved as the shared one", saved["dpi"] == 725, str(saved["dpi"]))
check("still there for the next scan of this strip", app.state()["frame_settings"] == {"1": {"dpi": 362}})
app.strip_id += 1
check("another strip: gone", app.state()["frame_settings"] == {})
app.set_frame_settings([0], {"dpi": 362}); app.set_frame_settings([0], reset=True)
check("reset takes them all back", app.state()["frame_settings"] == {})

print("FAILED: " + ", ".join(bad) if bad else "all frame settings checks passed")
sys.exit(1 if bad else 0)
