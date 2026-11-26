import os, sys, tempfile, time
os.environ.setdefault("COOLSCAN_LEARN_DIR", tempfile.mkdtemp(prefix="coolscan-learn-"))   # learning: never the person's own
home = tempfile.mkdtemp(); os.environ["HOME"] = home
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from pathlib import Path
import coolscan_app.app as A
A.STRIP_CACHE = Path(home) / ".coolscan-strip.cache"
from coolscan_app.app import App
fails = []
def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name); ok or fails.append(name)

app = App(demo=True); app.connect(); app.session.load()
s = app.session
# 1. demo has no resume/cache: thumbnail works as before, no cache file, timing said
app.thumbnail(fresh=False)
check("pass taken without resume support", app.discovery is not None and len(app.discovery.frames) > 0)
check("timing line", any("thumbnail pass took" in l for l in app.log))
check("no cache file without Discovery.cache", not A.STRIP_CACHE.exists())

# 2. give the pretend scanner nkscan's cache/resume
orig = s.discover_frames
calls = {"disc": 0, "resume": 0}
def disc(**kw):
    calls["disc"] += 1
    d = orig(**kw); d.cache = b"STRIP-" + str(len(d.frames)).encode(); s._last = d; return d
def resume(data, format=None, film=None):
    calls["resume"] += 1
    return s._last if data.startswith(b"STRIP-") and s.medium_changes == s._mc else None
s.discover_frames = disc; s.resume_frames = resume
app.thumbnail(); s._mc = s.medium_changes
check("fresh pass writes cache", A.STRIP_CACHE.read_bytes().startswith(b"STRIP-"))
check("button (fresh) does not try resume", calls["resume"] == 0 and calls["disc"] == 1)
app.discovery = None
app.thumbnail(fresh=False)
check("restart with same strip: resumed, no pass", calls["disc"] == 1 and calls["resume"] == 1 and app.discovery is not None)
check("resume said", any("same strip as before" in l for l in app.log))
check("thumbnail picture shown after resume", app.thumb_png is not None)
# _ensure_discovery path
app.discovery = None; app._ensure_discovery()
check("ensure_discovery resumes", calls["disc"] == 1 and calls["resume"] == 2)
# 3. eject removes cache; new strip -> resume says None -> pass
app.eject()
check("eject removes cache", not A.STRIP_CACHE.exists())
s.load(); app.thumbnail(fresh=False)
check("after eject: a real pass", calls["disc"] == 2)
# 4. a broken resume falls back to a pass
def bad(*a, **k): raise RuntimeError("boom")
s.resume_frames = bad; app.discovery = None; app.thumbnail(fresh=False)
check("resume error falls back to a pass", calls["disc"] == 3)
print("all passed" if not fails else f"FAILED: {fails}")
