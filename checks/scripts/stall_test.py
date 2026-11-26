"""The driver log (in the app folder, last run kept, app lines in it) and the warning when the scanner goes quiet mid-pass."""
import os, sys, tempfile, time, threading
os.environ.setdefault("COOLSCAN_LEARN_DIR", tempfile.mkdtemp(prefix="coolscan-learn-"))   # learning: never the person's own
home = tempfile.mkdtemp(); os.environ["HOME"] = home
pkg = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, pkg)
import coolscan_app.app as A
from coolscan_app.scanner import driver as D
from pathlib import Path
fails = []
def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n); ok or fails.append(n)

# driver log: in <pkg>/logs, previous kept, app lines in it
logdir = Path(home) / "logs"  # never the real app folder
D.LOG_DIR, D.DRIVER_LOG = logdir, logdir / "driver.log"
dl = D.DriverLog(); check("log starts", dl.start())
check("log in app folder", dl.path == logdir / "driver.log")
dl.note("first run line"); 
dl2 = D.DriverLog(); dl2.start()
check("previous run kept", "first run line" in (logdir / "driver.prev.log").read_text())
check("new log fresh", "first run line" not in (logdir / "driver.log").read_text())

A.STALL_SECONDS = 2
app = A.App(demo=True); app.driver_log = dl2
app.say("hello from the app")
check("app line in driver log", "APP " in (logdir / "driver.log").read_text())
# a pass that stops sending at 20%
gate = threading.Event()
def stuck():
    app._progress("discover", 0, 130530, 658350)
    gate.wait(20)
app.run_job("thumbnail", stuck); time.sleep(0.3)
app._check_stall(); check("no warning before the limit", not app.stall_said)
time.sleep(2.2); app._check_stall()
check("warning after the limit", app.stall_said and any("sent nothing" in l and "19%" in l for l in app.log))
n = sum("sent nothing" in l for l in app.log); app._check_stall()
check("said once", sum("sent nothing" in l for l in app.log) == n)
gate.set(); time.sleep(0.3)
# a finished pass with long processing after it: no warning
def processing():
    app._progress("scan", 0, 100, 100); time.sleep(3)
app.run_job("scan", processing); time.sleep(2.5); app._check_stall()
check("no warning at 100% (processing)", not app.stall_said)
time.sleep(1)
# a slow but moving pass: no warning
def moving():
    for k in range(1, 6):
        app._progress("scan", 0, k, 10); time.sleep(0.8)
app.run_job("scan", moving)
for _ in range(5):
    time.sleep(0.8); app._check_stall()
check("no warning while pieces keep coming", not app.stall_said)
print("all passed" if not fails else f"FAILED {fails}")
