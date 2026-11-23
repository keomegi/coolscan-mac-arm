import sys, time, tempfile, pathlib, shutil
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import cs_next as cs
cs.SETTINGS_FILE = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: [progress(phase, 0, i + 1, steps) for i in range(steps)] and None if progress else None

class ScannerError(RuntimeError): pass
class TransientError(ScannerError): pass
class TransportError(TransientError): pass
class DeviceBusy(TransientError): pass
class DeviceNotFound(ScannerError): pass
class MediaError(ScannerError): pass
class UnsupportedError(ScannerError): pass
class ScanCancelled(ScannerError): pass

print("== explain")
for e in [TransportError("io error: device disconnected"), TransportError("timed out after 30s"),
          DeviceNotFound("no such scanner"), DeviceBusy("the scanner is busy"),
          DeviceBusy("another initiator holds the scanner"), MediaError("No film or holder is loaded"),
          MediaError("The adapter is ejected"), MediaError("SA-21/SA-30: The adapter is pulled out a little in the locked status"),
          MediaError("SA-21/SA-30: a nonstandard film was inserted"), MediaError("The adapter has nothing left to load"),
          MediaError("User intervention required with no additional details"),
          ScannerError("scanner fault: autofocus did not reach focus (Sense { key: 1 })"),
          ScannerError("scanner fault: the adapter could not feed the film; check its perforations (Sense)"),
          ScannerError("scanner fault: hardware error (Sense)"),
          ScannerError("scanner rejected the command: frame beyond the number of frames in the film (Sense)"),
          ScannerError("scanner rejected the command: invalid field in CDB (Sense)"),
          UnsupportedError("clean is not supported: no infrared on this unit"),
          RuntimeError("no film loaded"), RuntimeError("no Nikon scanner found. Is it on and plugged in?"),
          RuntimeError("this unit does not autofocus"), PermissionError(13, "Permission denied", "/x/y.tif"),
          ValueError("boom")]:
    print(f"  {type(e).__name__:16} -> {cs.explain(e)}")

def fresh(**settings):
    app = cs.App(demo=True)
    out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(outdir=str(out), dpi=145, **settings)
    app.connect()
    return app, out

def show(out):
    for f in sorted(out.rglob("*")):
        if f.is_file():
            print("   file:", f.relative_to(out))
    for f in out.rglob("ScanLog.txt"):
        print("   --- ScanLog.txt\n" + "".join("   | " + l for l in open(f)))

print("== 1 normal batch, 3 frames")
app, out = fresh()
app.scan([0, 1, 2]); show(out)

print("== 2 frame 2 fails autofocus, carry on")
app, out = fresh(archive=True)
real = app.session.scan_frame
calls = {"n": 0}
def flaky(frame, **kw):
    calls["n"] += 1
    if frame == app.discovery.frames[1]:
        raise ScannerError("scanner fault: autofocus did not reach focus (Sense { key: 1 })")
    return real(frame, **kw)
app.session.scan_frame = flaky
app.thumbnail()
app.scan([0, 1, 2]); show(out)

print("== 3 same, Stop on errors")
app, out = fresh(stop_on_errors=True)
real3 = app.session.scan_frame
app.thumbnail()
def flaky3(frame, **kw):
    if frame == app.discovery.frames[1]:
        raise ScannerError("scanner fault: autofocus did not reach focus (Sense { key: 1 })")
    return real3(frame, **kw)
app.session.scan_frame = flaky3
try:
    app.scan([0, 1, 2])
except Exception as e:
    print("   raised:", type(e).__name__)
show(out)

print("== 4 scanner unplugged at frame 2 (through run_job)")
app, out = fresh()
real4 = app.session.scan_frame
app.thumbnail()
def gone(frame, **kw):
    if frame == app.discovery.frames[1]:
        raise TransportError("io error: device disconnected")
    return real4(frame, **kw)
app.session.scan_frame = gone
app.run_job("scan", lambda: app.scan([0, 1, 2]))
while app.job: time.sleep(0.05)
print("   session dropped:", app.session is None); show(out)

print("== 5 Stop pressed during frame 2 of archive scan")
app, out = fresh(archive=True)
real5 = app.session.scan_frame
app.thumbnail()
seen = {"n": 0}
def stopper(frame, **kw):
    seen["n"] += 1
    if seen["n"] == 4:   # the clean pass of frame 2
        raise ScanCancelled("scan cancelled")
    return real5(frame, **kw)
app.session.scan_frame = stopper
app.run_job("scan", lambda: app.scan([0, 1, 2]))
while app.job: time.sleep(0.05)
show(out)

print("== 6 film moved between thumbnail and preview")
app, out = fresh()
app.thumbnail()
app.preview([0]); print("   shown:", [app.frame_source.get(i) for i in range(6)])
app.session.medium_changes += 1
app.preview([1]); print("   shown after the change:", [app.frame_source.get(i) for i in range(6)], "seen", app.medium_seen)
app.preview([2]); print("   shown next:", [app.frame_source.get(i) for i in range(6)])

print("== 7 strip let go (not loaded), then Scan; and eject when done")
app, out = fresh(eject_after=True, scan_log=False)
app.thumbnail()
app.session.eject()
app.scan([0]); show(out); print("   loaded after batch:", app.session.loaded, "discovery:", app.discovery)

print("== 8 no film at all")
app, out = fresh()
app.session.loaded = False
app.session.load = lambda: False
app.run_job("thumbnail", app.thumbnail)
while app.job: time.sleep(0.05)
print("\n".join("   " + l for l in app.log[-2:]))
