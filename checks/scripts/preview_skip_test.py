"""Preview scans without autofocus or metering (Settings > Preview scan): quicker, marked on the frame."""
import sys, tempfile, pathlib
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
app.settings.update(outdir=str(tmp / "out"), clean=False)
app.connect(); app.thumbnail()
calls = []
real_scan, real_focus = app.session.scan_frame, app.session.focus_frame
def scan(frame, **kw):
    calls.append(("scan", kw.get("focus"), "exposures" in kw and kw["exposures"] is not None)); return real_scan(frame, **kw)
def focus(frame, **kw):
    calls.append(("focus",)); return real_focus(frame, **kw)
app.session.scan_frame, app.session.focus_frame = scan, focus

app.preview([0])
check("both on: the preview focuses and meters", calls[0] == ("focus",) and calls[1][2] is False, str(calls))
check("both on: no mark", app.state()["tiles"][0]["preview_skipped"] is None)
calls.clear()
app.settings.update(preview_af=False, preview_ae=False)
app.preview([1, 2])
check("off: no focusing", not any(c[0] == "focus" for c in calls), str(calls))
check("off: the lens holds", all(c[1] == "hold" for c in calls if c[0] == "scan"), str(calls))
check("off: the last preview's exposure is reused", all(c[2] for c in calls if c[0] == "scan"), str(calls))
t = app.state()["tiles"]
check("off: frames marked AF off and AE off", t[1]["preview_skipped"] == {"af": False, "ae": False} and t[2]["preview_skipped"] == {"af": False, "ae": False}, str(t[1]["preview_skipped"]))
check("frame 2 was not focused for later", app.frame_focus.get(1) is None)
calls.clear()
app.scan([1])
check("the scan still focuses that frame", ("focus",) in calls, str(calls))
app2 = cs.App(demo=True); app2.settings.update(outdir=str(tmp / "o2"), clean=False, preview_ae=False); app2.connect(); app2.thumbnail()
app2.preview([0])
check("nothing to reuse yet: the first preview is metered and not marked AE off", app2.state()["tiles"][0]["preview_skipped"] is None)
app.settings.update(preview_af=True, preview_ae=True); app.preview([1])
check("previewed again with both on: the mark goes", app.state()["tiles"][1]["preview_skipped"] is None)
print("FAILED: " + ", ".join(bad) if bad else "all preview checks passed")
sys.exit(1 if bad else 0)
