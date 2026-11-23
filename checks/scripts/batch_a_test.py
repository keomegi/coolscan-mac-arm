"""Batch A: the driver's pass notes and scan times, no needless lens move, strips and selection,
going back to a roll."""
import sys, os, json, time, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
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
def logged(app, word): return [l[9:] for l in app.log if word in l]

print("A the driver's notes on a pass")
log = cs.DriverLog()
check("no log: nothing, and nothing breaks", log.passes() == [] and log.mark() is None)
log.path = tmp / "driver.log"
log.path.write_text("2026-11-22T21:00:00.000001Z DEBUG scan ready ready_in=1.2s\n"
                    "2026-11-22T21:00:40.000001Z DEBUG pass blocks=95 complete=true chunks=95 bytes=99187200 read_ms=41000 starved_ms=0 decode_ms=900 idle_ms=40000\n")
log.mark()
check("lines from before the mark are not counted", log.passes() == [])
with open(log.path, "a") as f:
    f.write("2026-11-22T21:01:00.1Z DEBUG scan ready ready_in=1.2s\n"
            "2026-11-22T21:01:40.1Z DEBUG blocks=95 complete=true chunks=95 bytes=99187200 read_ms=41000 starved_ms=2300 decode_ms=900 idle_ms=40000 pass\n"
            "2026-11-22T21:01:41.1Z  WARN the unit gave less than the pass promised blocks=3\n")
got = log.passes()
check("a pass after the mark is read, whichever way round the line is written",
      len(got) == 1 and got[0]["starved_ms"] == 2300 and got[0]["read_ms"] == 41000 and got[0]["bytes"] == 99187200 and got[0]["chunks"] == 95, str(got))
try:
    import nkscan
except ImportError:  # the tests run without the driver too (GitHub); this one check needs it
    nkscan = None
    print("   skip the real nkscan's log request: nkscan is not installed")
if nkscan is not None:
    real_log = cs.DRIVER_LOG; cs.DRIVER_LOG = tmp / "real-driver.log"
    started = cs.DriverLog().start()
    check("the real nkscan accepts the log request and the file is there", started and cs.DRIVER_LOG.is_file(), str(started))
    nkscan.init_logging("off"); cs.DRIVER_LOG = real_log
check("clock()", [cs.clock(x) for x in (0, 7.4, 59.6, 61, 3725)] == ["0:00", "0:07", "1:00", "1:01", "62:05"])

print("B times for a frame and a strip")
app, out = fresh(clean=True, archive=True)
app.driver_log.path = tmp / "demo-driver.log"; app.driver_log.path.write_text("")
waits = iter([0, 0, 1700, 0, 0, 0])
real = app.session.scan_frame
def scan_frame(frame, **kw):
    r = real(frame, **kw); time.sleep(0.05)
    with open(app.driver_log.path, "a") as f:
        f.write(f"2026-11-22T21:01:40.1Z DEBUG pass blocks=9 complete=true chunks=9 bytes=900 read_ms=50 starved_ms={next(waits)} decode_ms=1 idle_ms=40\n")
    return r
app.session.scan_frame = scan_frame
seen = []
real_one = app._scan_one
def scan_one(*a, **k):
    seen.append(dict(app.state()["timing"])); return real_one(*a, **k)
app._scan_one = scan_one
app.scan([0, 1, 2])
done = logged(app, " done in ")
check("each frame says how long it took, with scanning and dust removal apart", len(done) == 3 and all("scanning" in l and "dust removal" in l for l in done), "; ".join(done))
waited = logged(app, "had to wait")
check("each frame says whether the scanner had to wait", len(waited) == 3 and "never had to wait" in waited[0] and "never had to wait" in waited[1], "; ".join(waited))
check("a wait of 1.7 s is called what it is", "wait 1.7 s" in waited[2] and "stops and starts" in waited[2], waited[2])
check("the strip total and the time per frame are said", any("3 of 3 frame(s) saved, in " in l and "per frame" in l for l in app.log), app.log[-2])
t = app.state()["timing"]
check("the page gets the last strip's times", t["last_strip"]["frames"] == 3 and t["last_strip"]["of"] == 3 and t["last_strip"]["seconds"] > 0.1 and abs(t["last_strip"]["average"] * 3 - t["last_strip"]["seconds"]) < 0.01 and "now" in t, str(t))
check("...and during the strip: which frame, since when, how many done", [s.get("frame") for s in seen] == [1, 2, 3] and [s.get("done") for s in seen] == [0, 1, 2]
      and all(s["of"] == 3 and s["frame_started"] >= s["strip_started"] for s in seen) and "average" in seen[1], str(seen[1]))
text = (out / "Roll 001" / "ScanLog.txt").read_text()
check("the scan log has the times too", text.count(" took ") == 4 and "The scanner had to wait 1.7 s" in text and "per frame" in text, text[-500:])
app2, out2 = fresh(clean=False)
app2.scan([0])
check("without the driver's notes nothing is claimed about waiting", not logged(app2, "had to wait") and len(logged(app2, " done in ")) == 1)
before = app.version
app._progress("scan", 0, 5, 10)
check("progress from the driver no longer redraws the whole page", app.version == before and app.progress == {"phase": "scan", "done": 5, "total": 10})

print("C the lens is not moved when it is already there")
app, out = fresh(clean=True, archive=True)
calls = []
real = app.session.scan_frame
def spy(frame, **kw):
    calls.append(kw.get("focus")); return real(frame, **kw)
app.session.scan_frame = spy
app.scan([0, 1])
check("autofocus once per frame, then the pass holds the lens", calls == ["hold", "hold"] and app.session.focus_moves == 0 and len(logged(app, ": autofocus")) == 2, str(calls))
calls.clear(); app.scan([0])
check("back to a frame focused earlier: the lens is moved to its position (one move)", calls == [app.frame_focus[0]] and app.session.focus_moves == 1, str(calls))
calls.clear(); app.scan([0])
check("...and not again while it is still there", calls == ["hold"], str(calls))
app.settings.update(samples=4, dust_method="pass"); app.caps.max_samples = 1
calls.clear(); app.session.focus_moves = 0; app.scan([0])
check("4 averaged passes: no lens move in any of them", calls == ["hold"] * 4 and app.session.focus_moves == 0, str(calls))
app.settings.update(samples=1); calls.clear(); app.scan([0])
check("second pass for dust removal: no lens move", calls == ["hold", "hold"], str(calls))
app.settings.update(focus_mode="manual", focus_position=300); calls.clear(); app.scan([0, 1])
check("manual position: moved once, then held", calls[0] == 300 and set(calls[1:]) == {"hold"}, str(calls))
app.settings.update(focus_position=310); calls.clear()
app.session.focus_position = lambda: (_ for _ in ()).throw(RuntimeError("cannot say"))
app.scan([0])
check("a scanner that cannot say where its lens is: moved as before", calls[0] == 310, str(calls))

print("D strips and what is selected")
app, out = fresh()
first = app.state()["strip"]
check("6 frames on the first strip", len(app.state()["frames"]) == 6)
app.eject(); gone = app.state()["strip"]
app.load(); app.thumbnail(); st = app.state()
check("another strip is another strip to the page (so its selection goes)", first < gone < st["strip"] and len(st["frames"]) == 4, f"{first} {gone} {st['strip']} {len(st['frames'])}")
try:
    app.scan([4, 5]); refused = False
except RuntimeError as e:
    refused = "not on this film" in str(e)
check("frames 5 and 6 of a 4-frame strip are refused, nothing scanned", refused and not list(out.rglob("*.tif")))

print("E going back to a roll")
app, out = fresh(clean=False)
app.new_roll({"film_brand": "Kodak", "film_stock": "Portra", "film_iso": "400", "film_notes": "Anna", "film_description": "First roll\nLisbon"})
check("the first roll", app.settings["roll"] == "Roll 001 - Kodak Portra ISO 400, Anna", app.settings["roll"])
app.scan([0, 1])
app.new_roll({"film_brand": "Ilford", "film_stock": "HP5", "film_iso": "400", "film_description": "Second roll"})
app.scan([0])
check("rolls listed in order, the current one among them", app.rolls() == ["Roll 001 - Kodak Portra ISO 400, Anna", "Roll 002 - Ilford HP5 ISO 400"] and app.state()["rolls"] == app.rolls(), str(app.rolls()))
r = app.switch_roll("Roll 001 - Kodak Portra ISO 400, Anna")
check("back in the Kodak roll: film details and description are its own again", r == {"ok": True} and app._film_text() == "Kodak Portra ISO 400, Anna"
      and app.settings["film_description"] == "First roll\nLisbon" and app.settings["film_iso"] == "400", app._film_text())
check("the next picture counts on from what is in that roll", app.next_number() == 3 and app.state()["sample_name"] == f"{D}_003_R001.tif", app.state()["sample_name"])
files_before = sorted(str(p.relative_to(out)) for p in out.rglob("*"))
app.scan([2])
check("it is saved there as 003 with that roll's number", (out / "Roll 001 - Kodak Portra ISO 400, Anna" / f"{D}_003_R001.tif").is_file())
check("nothing was renamed or moved by going back", all(f in sorted(str(p.relative_to(out)) for p in out.rglob("*")) for f in files_before))
r = app.switch_roll("Roll 002 - Ilford HP5 ISO 400")
check("and forward again: Ilford details, next number 2", r.get("ok") and app._film_text() == "Ilford HP5 ISO 400" and app.next_number() == 2 and app.settings["film_description"] == "Second roll")
check("the same roll again: nothing to do", app.switch_roll("Roll 002 - Ilford HP5 ISO 400") == {"ok": True, "same": True})
check("a roll that is not there is refused", app.switch_roll("Roll 009").get("ok") is False and app.switch_roll("../Roll 002 - Ilford HP5 ISO 400").get("ok") is False
      and app.switch_roll("Earlier rolls").get("ok") is False and app.settings["roll"] == "Roll 002 - Ilford HP5 ISO 400")
(out / "Roll 005 - Fuji Superia ISO 200").mkdir()
r = app.switch_roll("Roll 005 - Fuji Superia ISO 200")
check("a roll from before the app kept film details: its folder name says the film", r.get("ok") and app._film_text() == "Fuji Superia ISO 200" and app.next_number() == 1 and app.settings["film_description"] == "", app._film_text())
app.switch_roll("Roll 002 - Ilford HP5 ISO 400")
r = app.renumber_roll(7)
app.switch_roll("Roll 001 - Kodak Portra ISO 400, Anna"); app.switch_roll("Roll 007 - Ilford HP5 ISO 400")
check("a renumbered roll keeps its film details", r.get("ok") and app._film_text() == "Ilford HP5 ISO 400" and "Roll 002 - Ilford HP5 ISO 400" not in app.settings["rolls"], str(sorted(app.settings["rolls"])))
app.job = "scan"
check("during a scan: busy, the roll stays", app.quick_job("changing roll", lambda: app.switch_roll("Roll 005 - Fuji Superia ISO 200")) == {"ok": False, "busy": True} and app.settings["roll"].startswith("Roll 007"))
app.job = None
app.save_settings(); a = cs.App(demo=True); a.load_settings()
check("the film details of every roll survive a restart", a.settings["rolls"] == app.settings["rolls"] and len(a.settings["rolls"]) >= 3)
print(); print("FAILED: " + ", ".join(bad) if bad else "all passed")
