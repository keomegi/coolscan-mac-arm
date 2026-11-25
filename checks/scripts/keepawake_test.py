"""Keeping the Mac awake during jobs, and noticing a sleep (no Mac needed: a pretend caffeinate)."""
import os, sys, tempfile, time, threading
import xml.sax.saxutils  # noqa: F401 - loaded now: imported later with sys.platform faked to "darwin", it looks for _scproxy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
home = tempfile.mkdtemp()
os.environ["HOME"] = home
fake = Path(tempfile.mkdtemp())
log = fake / "calls.txt"
(fake / "caffeinate").write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexec sleep 600\n")
(fake / "caffeinate").chmod(0o755)
os.environ["PATH"] = f"{fake}:{os.environ['PATH']}"

real_platform = sys.platform
sys.platform = "darwin"          # so the app looks for caffeinate
from coolscan_app import keepawake
from coolscan_app.keepawake import KeepAwake
sys.platform = real_platform

fails, n = [], 0
def check(what, ok):
    global n
    n += 1
    if not ok:
        fails.append(what)
    print(("ok   " if ok else "FAIL ") + what)

said = []
keepawake.LINGER_SECONDS = 1
sys.platform = "darwin"
k = KeepAwake(said.append)
sys.platform = real_platform
check("found the pretend caffeinate", k.tool == str(fake / "caffeinate"))
check("not awake before a job", not k.awake)
k.hold()
check("awake during a job", k.awake)
time.sleep(0.3)
args = log.read_text().split()
check("caffeinate gets -i -s -m and -w with the app's pid", args[:4] == ["-i", "-s", "-m", "-w"] and args[4] == str(os.getpid()))
check("says once what it does", len(said) == 1 and "will not go to sleep" in said[0])
k.hold(); k.release()
check("still awake while another job runs", k.awake)
first = k.proc
k.release()
check("still awake right after the last job (lingers)", k.awake)
k.hold()
check("a new job in the linger keeps the same caffeinate", k.proc is first)
k.release()
time.sleep(1.5)
check("lets go after the linger", not k.awake and first.poll() is not None)
check("only one caffeinate was ever started", len(log.read_text().splitlines()) == 1)
k.hold(); k.stop()
check("stop() lets go at once", not k.awake)
check("said only once in all", len(said) == 1)

# no caffeinate (Linux, or missing): nothing happens, nothing breaks
k2 = KeepAwake(said.append); k2.tool = None
k2.hold(); k2.release()
check("without caffeinate a job still runs, quietly off the Mac", not k2.awake)

# sleep detection: the wall clock jumps, the running clock does not
k3 = KeepAwake()
t0 = time.time()
check("no sleep seen normally", k3.check_sleep() == 0 and not k3.slept_since(t0))
k3._wall -= 300
gap = k3.check_sleep()
check("a 5-minute jump is a sleep", 290 < gap < 310)
check("slept_since a time before it", k3.slept_since(t0 - 1))
check("not slept_since a later time", not k3.slept_since(time.time() + 1))

# in the app (pretend scanner)
sys.platform = "darwin"
from coolscan_app.app import App
sys.platform = real_platform
app = App(demo=True)
app.keep_awake.tool = str(fake / "caffeinate")
app.say = lambda m, _s=app.say: (said.append(m), _s(m))
app.run_job("connect", app.connect)
for _ in range(50):
    if not app.job: break
    time.sleep(0.1)
check("the app connected (demo)", app.session is not None)
check("state says awake after a job", app.state()["awake"] is True)

gate = threading.Event()
def lost():
    app.keep_awake._wall -= 600       # the Mac slept in the middle of this job
    app.keep_awake.check_sleep()
    gate.wait(2)
    e = type("TransportError", (Exception,), {})("device disconnected")
    raise e
said.clear()
app.run_job("scan", lost)
check("a second job is refused while one runs", not app.run_job("preview", lambda: None))
gate.set()
for _ in range(50):
    if not app.job: break
    time.sleep(0.1)
check("lost scanner after sleep: says the Mac went to sleep", any("went to sleep" in m for m in said))
check("lost scanner after sleep: session dropped", app.session is None)
check("lost scanner after sleep: will reconnect", app.reconnect_tries == 3)
app._after_sleep()
for _ in range(50):
    if not app.job: break
    time.sleep(0.1)
check("reconnected by itself", app.session is not None)
app._after_sleep()
check("no more tries once connected", app.reconnect_tries == 0)

# the Mac slept while idle: the watcher checks the scanner
said.clear()
app.keep_awake._wall -= 120
app._after_sleep()
for _ in range(50):
    if not app.job: break
    time.sleep(0.1)
check("idle sleep noticed", any("asleep for 2:00" in m for m in said))
app.keep_awake.stop()

print(f"{n - len(fails)} of {n} passed" + ("" if fails else ", all passed"))
sys.exit(1 if fails else 0)
