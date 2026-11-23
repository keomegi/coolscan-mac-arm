"""Multi-Exposure: a second pass exposed longer, merged into the normal one."""
import sys, os, json, time, tempfile, pathlib, threading, urllib.request
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import cs_next as cs
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)
def fresh(**kw):
    app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=145, ir=False, clean=False, scan_log=False), **kw})
    app.connect(); app.thumbnail(); return app, out
def logged(app, word): return [l[9:] for l in app.log if word in l]
class ScannerError(RuntimeError): pass
class TransportError(RuntimeError): pass

print("A putting two passes together")
rng = np.random.default_rng(2)
rows, cols = 400, 600
yy, xx = np.mgrid[0:rows, 0:cols]
light = 10 ** (-3.0 * xx / cols) * (0.9 + 0.1 * np.sin(yy / 9.0))          # from full light down to a thousandth of it
def scanned(times, lamp=1.0, lift=0.0):
    v = 60000 * light * times * lamp + lift + rng.normal(0, 60, light.shape)  # the scanner's own noise does not grow with the exposure
    return np.clip(v, 0, 65535).astype(np.uint16)
truth = 60000 * light
short = scanned(1)
for factor in (2, 4):
    long = scanned(factor, lamp=1.03, lift=40)                               # the lamp 3% off and a raised black level, as a real pass may be
    merged, share = cs.merge_exposures(short, long, factor)
    dark = truth < 0.03 * 65535
    err_one, err_two = float(np.abs(short[dark] - truth[dark]).mean()), float(np.abs(merged[dark] - truth[dark]).mean())
    check(f"{factor}x: the dark end of the scan is less noisy than one pass gives",
          err_two < err_one / (0.8 * factor), f"off by {err_one:.1f} levels with one pass, {err_two:.1f} merged")
    burnt = long >= 65000
    check(f"{factor}x: where the longer pass burns out, the picture is the normal pass untouched",
          burnt.any() and np.array_equal(merged[burnt], short[burnt]), f"{burnt.mean():.0%} of the picture")
    between = (long > 0.75 * 65535) & (long < 0.90 * 65535)
    seam = float(np.abs(merged[between].astype(np.float64) - truth[between]).mean())
    check(f"{factor}x: no step where the two passes meet", between.any() and seam < 80, f"off by {seam:.0f} levels there (the noise alone is about 48)")
    bias = float((merged[dark].astype(np.float64) - truth[dark]).mean())
    check(f"{factor}x: a lamp 3% off and a raised black level are measured and taken out", abs(bias) < 12, f"dark end off by {bias:+.1f} levels on average")
    check(f"{factor}x: it says how much came from the longer pass", 0.3 < share < 1.0, f"{share:.0%}")
check("a second pass that is not brighter: not used", cs.merge_exposures(short, scanned(1), 4) is None)
check("a second pass of another size: not used", cs.merge_exposures(short, long[:-1], 4) is None)
check("a second pass burnt out everywhere: not used", cs.merge_exposures(short, np.full_like(short, 65535), 4) is None)

print("B in the app")
def watch(app):
    calls = []
    real = app.session.scan_frame
    def scan_frame(frame, **kw):
        calls.append({k: kw.get(k) for k in ("infrared", "clean", "exposures", "focus")}); return real(frame, **kw)
    app.session.scan_frame = scan_frame
    return calls
app, out = fresh()
calls = watch(app)
app.scan([0])
check("off (the default): one pass per frame, as before", len(calls) == 1 and app.settings["multi_exposure"] == 0)
app.settings["multi_exposure"] = 4
calls.clear(); app.scan([1])
first, second = calls
check("4x: two passes, the second exposed four times longer in every colour",
      len(calls) == 2 and second["exposures"] == {"red": 4800, "green": 3600, "blue": 2800}, str(second["exposures"]))
check("...at the lens position of the first, without moving the lens", second["focus"] == "hold" or isinstance(second["focus"], int), str(second["focus"]))
check("the Activity list says what happened", any("second pass exposed 4 times longer" in l for l in app.log)
      and any("Multi-Exposure merged" in l and "% of the picture read from the longer pass" in l for l in app.log),
      str([l for l in logged(app, "Multi-Exposure")][-1:]))
app.settings.update(clean=True, ir=True, multi_exposure=2)
calls.clear(); app.scan([2])
check("with dust removal: the infrared is read once, in the normal pass",
      [c["infrared"] for c in calls] == [True, False] and "infrared" not in calls[1]["exposures"]
      and calls[1]["exposures"] == {"red": 2400, "green": 1800, "blue": 1400}, str(calls[1]))
check("...and the merged picture is cleaned in the app", any("frame 3: dust removal rebuilt" in l for l in app.log))
colour = json.loads((out / "Roll 001" / "FilmColour.json").read_text())["frames"]
check("the frame is noted with the exposure of its normal pass (film profiles stay right)",
      all(v["exposures"]["red"] == 1200 for v in colour.values()), str([v["exposures"] for v in colour.values()][-1]))
app.settings.update(dust_method="pass")
calls.clear(); app.scan([3])
check("Driver dust removal cannot clean two passes: the app's cleaner does it", [c["clean"] for c in calls] == [False, False])
app.settings.update(dust_method="builtin", clean=False, ir=False, multi_exposure=4)

# is the result really better? compare with what the pretend scanner's film holds
app, out = fresh(dpi=290, multi_exposure=4)
s, d = app.session, app.discovery
kw = dict(infrared=False, clean=False); base = dict(dpi=290, samples=1, progress=None); opts = app._scan_options(s, 0, d.frames[0], False)
one = s.scan_frame(d.frames[0], dpi=290)
both = app._multi_exposed(s, 0, d.frames[0], 4, kw, base, opts)
film = s._film(one.rows, one.cols, d.frames[0][0] % 7)
marks = one.colors["green"] < film["green"] // 3          # the pretend dust: not part of the comparison
dark = (film["green"] < 9000) & ~marks
e1 = float(np.abs(one.colors["green"][dark].astype(np.float64) - film["green"][dark]).mean())
e2 = float(np.abs(both.colors["green"][dark].astype(np.float64) - film["green"][dark]).mean())
check("the dark parts of a frame come out closer to the film than with one pass", dark.sum() > 1000 and e2 < 0.6 * e1,
      f"{e1:.0f} levels off with one pass, {e2:.0f} with Multi-Exposure, over {int(dark.sum())} pixels")
bright = (film["green"] > np.percentile(film["green"], 80)) & ~marks
e3 = float(np.abs(both.colors["green"][bright].astype(np.float64) - film["green"][bright]).mean())
check("the bright parts are no worse", bright.sum() > 1000 and e3 < 260, f"{e3:.0f} levels off (the noise of one pass is about 200)")
check("the result says it was exposed as the normal pass was", dict(both.exposures) == {"red": 1200, "green": 900, "blue": 700})

print("C when the second pass goes wrong")
def second_pass(app, what):
    real = app.session.scan_frame
    def scan_frame(frame, **kw):
        if kw.get("exposures") and kw["exposures"].get("red", 0) > 1200: return what(real, frame, kw)
        return real(frame, **kw)
    app.session.scan_frame = scan_frame
def refuse(real, frame, kw): raise ScannerError("scanner rejected the command: invalid field in parameter list (Sense)")
app, out = fresh(multi_exposure=4); second_pass(app, refuse)
app.scan([0])
check("the scanner refuses the longer exposure: the frame is saved from the normal pass, and the app says so",
      len(list((out / "Roll 001").glob("*.tif"))) == 1 and any("Multi-Exposure left out, the scanner would not take the longer exposure" in l for l in app.log),
      str(logged(app, "left out")[-1:]))
def gone(real, frame, kw): raise TransportError("io error: device disconnected")
app, out = fresh(multi_exposure=4); second_pass(app, gone)
try: app.scan([0]); stopped = False
except Exception as e: stopped = type(e).__name__
check("the scanner is unplugged during the second pass: the batch stops as it would in any pass", stopped == "TransportError", str(stopped))
def other_size(real, frame, kw):
    r = real(frame, **kw); r.colors = {k: v[:-3] for k, v in r.colors.items()}; return r
app, out = fresh(multi_exposure=2); second_pass(app, other_size)
app.scan([0])
check("a second pass of another size: left out, frame saved", any("came back a different size" in l for l in app.log) and len(list((out / "Roll 001").glob("*.tif"))) == 1)
def same_light(real, frame, kw): return real(frame, **dict(kw, exposures={"red": 1200, "green": 900, "blue": 700}))
app, out = fresh(multi_exposure=4); second_pass(app, same_light)
app.scan([0])
check("a second pass that did not come out brighter: left out, frame saved", any("do not fit together" in l for l in app.log))
def moved(real, frame, kw):
    r = real(frame, **kw); r.colors = {k: np.roll(v, 3, axis=1) for k, v in r.colors.items()}; return r
app, out = fresh(dpi=725, multi_exposure=2); second_pass(app, moved)
app.scan([0])
check("a second pass that sits 3 pixels off: left out, frame saved",
      any("pixel off the first" in l for l in app.log) and len(list((out / "Roll 001").glob("*.tif"))) == 1, str(logged(app, "left out")[-1:]))
def press_stop(real, frame, kw): raise cs.Cancelled()
app, out = fresh(multi_exposure=2); second_pass(app, press_stop)
try: app.scan([0]); stopped = False
except cs.Cancelled: stopped = True
check("Stop during the second pass stops", stopped)
def no_exposures(real, frame, kw):
    r = real(frame, **kw); r.exposures = {}; return r
app, out = fresh(multi_exposure=2)
real0 = app.session.scan_frame
app.session.scan_frame = lambda frame, **kw: no_exposures(real0, frame, kw)
app.scan([0])
check("a first pass that does not say its exposure: no second pass, frame saved",
      any("did not say what it was exposed at" in l for l in app.log) and len(list((out / "Roll 001").glob("*.tif"))) == 1)

print("D with the other settings")
app, out = fresh(multi_exposure=4, samples=2)
app.caps.max_samples = 1
app.session.capabilities.max_samples = 1
calls = watch(app); app.scan([0])
check("multi-sample by averaging passes and Multi-Exposure together: Multi-Exposure is left out, and the app says so",
      any("Multi-Exposure is left out while multi-sample averages passes" in l for l in app.log) and len(calls) == 2
      and calls[1]["exposures"] == {"red": 1200, "green": 900, "blue": 700}, str(len(calls)))
app, out = fresh(multi_exposure=2, archive=True, scan_log=True)
app.scan([0])
log_text = (out / "Roll 001" / "ScanLog.txt").read_text()
check("Archive mode and the scan log", (out / "Roll 001" / "RAW").is_dir() and "Multi-Exposure 2x" in log_text
      and any("archive scan" in l and "Multi-Exposure 2x" in l for l in app.log))
app.save_preset("Slides, deep shadows")
check("saved settings keep it", app.settings["presets"]["Slides, deep shadows"]["multi_exposure"] == 2)
app.settings["multi_exposure"] = 3
calls = watch(app); app.scan([1])
check("a value that is not 2 or 4 counts as off", len(calls) == 1)
import http.server
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cs.make_handler(app))
threading.Thread(target=server.serve_forever, daemon=True).start()
req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/settings", json.dumps({"multi_exposure": "4"}).encode(), {"Content-Type": "application/json"})
urllib.request.urlopen(req).read(); server.shutdown()
check("set from the page it is kept as a number", app.settings["multi_exposure"] == 4)

print()
print("all passed" if not bad else f"{len(bad)} FAILED: {bad}")
sys.exit(1 if bad else 0)
