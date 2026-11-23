import sys, time, tempfile, pathlib, threading, json
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import cs_next as cs
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs.WATCH_SECONDS = 0.05

class ScannerError(RuntimeError): pass
class TransientError(ScannerError): pass
class TransportError(TransientError): pass

in_pass = {"on": False, "bad": 0, "polls": 0}
orig_wait = cs._Demo._wait
def slow_wait(self, progress, phase, steps=10, dt=0.02):
    in_pass["on"] = True
    try:
        for i in range(steps):
            time.sleep(dt)
            if progress and progress(phase, 0, i + 1, steps) is False:
                raise RuntimeError("cancelled")
    finally:
        in_pass["on"] = False
cs._Demo._wait = slow_wait
orig_ml = cs._Demo.media_loaded
def ml(self):
    if threading.current_thread().name == "watcher":
        in_pass["polls"] += 1
        if in_pass["on"]:
            in_pass["bad"] += 1
    return orig_ml(self)
cs._Demo.media_loaded = ml

def wait(cond, t=10):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.02)
    return False
def idle(app): return wait(lambda: not app.job)

app = cs.App(demo=True)
app.settings.update(outdir=str(tmp / "out"), dpi=145)
print("defaults:", app.settings["colour_space"], app.settings["tone"], app.settings["auto_thumbnail"])
app.run_job("connect", app.connect)
threading.Thread(target=app.watch, daemon=True, name="watcher").start()

print("1 film already in at start -> thumbnail by itself:", wait(lambda: app.discovery is not None and not app.job))
print("  thumb png:", app.thumb_png is not None, "film_in:", app.film_in, "armed:", app.film_armed)

n0 = len(app.log); time.sleep(0.5)
print("2 no repeat while idle (no new log lines):", len(app.log) == n0)

app.run_job("eject", app.eject); idle(app)
print("3 after Eject: discovery", app.discovery, "| armed right after eject:", app.film_armed)
print("  watcher sees film out:", wait(lambda: app.film_in is False and app.film_armed))
time.sleep(0.3); print("  nothing started with no film:", app.discovery is None and not app.job)
app.session.loaded = True          # a new strip goes in
print("4 new strip in -> thumbnail by itself:", wait(lambda: app.discovery is not None and not app.job))

print("5 eject, but the scanner keeps saying film is in -> strip not taken back in")
app.session.eject = lambda: True   # pretend the strip still reads as loaded after eject
app.run_job("eject", app.eject); idle(app); time.sleep(0.5)
print("  discovery stays empty:", app.discovery is None, "| armed:", app.film_armed)
app.run_job("load", app.load); idle(app)
print("  Load pressed -> thumbnail by itself:", wait(lambda: app.discovery is not None and not app.job))

print("6 long scan: watcher must not ask the scanner during a pass")
in_pass["bad"] = 0
app.run_job("scan", lambda: app.scan([0, 1])); time.sleep(0.1); idle(app)
print("  watcher questions during a pass:", in_pass["bad"], "| polls so far:", in_pass["polls"])

print("7 thumbnail fails once -> not retried by itself")
app.session.loaded = False; wait(lambda: app.film_in is False)
app.discovery = None
calls = {"n": 0}
real_d = app.session.discover_frames
def bad(**kw):
    calls["n"] += 1
    raise ScannerError("scanner fault: hardware error (Sense)")
app.session.discover_frames = bad
app.session.loaded = True
wait(lambda: calls["n"] >= 1); idle(app); time.sleep(0.5)
print("  discover calls:", calls["n"], "| last log:", app.log[-1][9:80])
app.session.discover_frames = real_d

print("8 a new strip: every frame gets its picture from the thumbnail pass by itself")
app._forget_pictures()
app.session.loaded = False; wait(lambda: app.film_in is False and app.film_armed)
app.session.loaded = True
wait(lambda: len(app.previews) == 6 and not app.job)
print("  pictures:", sorted(app.previews), "| from:", sorted(set(app.frame_source.values())))

print("9 switch off -> no more questions")
app.settings["auto_thumbnail"] = False; time.sleep(0.2); p0 = in_pass["polls"]; time.sleep(0.4)
print("  polls while off:", in_pass["polls"] - p0, "| state film_in:", app.state()["film_in"])
app.settings["auto_thumbnail"] = True

print("10 scanner switched off while idle")
def dead(): raise TransportError("io error: device disconnected")
app.session.media_loaded = dead
wait(lambda: app.session is None)
time.sleep(0.3)
print("  session dropped:", app.session is None); print("   " + "\n   ".join(l[9:150] for l in app.log[-2:]))

print("11 saved settings from before are moved over once")
cs.SETTINGS_FILE.write_text(json.dumps({"colour_space": "Adobe RGB (1998)", "tone": "nikon", "basename": "Scan", "roll": "Roll 002 - Kodak"}))
a2 = cs.App(demo=True); a2.load_settings()
print("  first load:", a2.settings["colour_space"], a2.settings["tone"], a2.settings["roll"])
a2.settings["colour_space"] = "sRGB IEC61966-2.1"; a2.settings["tone"] = "nikon"; a2.save_settings()
a3 = cs.App(demo=True); a3.load_settings()
print("  later choice kept:", a3.settings["colour_space"], a3.settings["tone"])
