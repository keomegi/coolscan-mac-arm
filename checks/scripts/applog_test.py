"""The app log (JSON Lines in logs/), scanner codes in plain words, log history and Clear cache."""
import json, os, sys, tempfile, time, threading, traceback
from pathlib import Path
home = tempfile.mkdtemp(); os.environ["HOME"] = home
pkg = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2])
sys.path.insert(0, pkg)
from coolscan_app import applog as L
from coolscan_app.scanner import driver as D
import coolscan_app.app as A
fails = []
def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n); ok or fails.append(n)

folder = Path(home) / "logs"
log = L.AppLog(folder)
check("starts", log.start(days=30))
log.say("found 6 frame(s)")
log.say("thumbnail failed. The scanner stopped answering.")
def entries():
    return [json.loads(x) for x in log.path().read_text().splitlines()]
e = entries()
check("lines are JSON with time, level, kind", all({"t", "level", "kind"} <= set(x) for x in e))
check("a failure line is an error", e[1]["level"] == "error" and e[0]["level"] == "info")

class ScannerError(Exception):
    pass
feed = ScannerError("scanner fault: the adapter could not feed the film; check its perforations "
                    "(Some(04h-53h-00h-?? raw=[70, 00, 04]))")
c = L.scanner_code(feed)
check("feed failure decoded", c.get("code") == "04h-53h-00h" and "feed" in c["meaning"])
pulled = ScannerError("scanner fault: mechanical error (Some(02h-04h-03h-04h raw=[70, 00, 02]))")
c = L.scanner_code(pulled)
check("Nikon's fourth byte decoded", c.get("code") == "02h-04h-03h-04h" and "pulled out" in c["meaning"])
check("no sense, no code", L.scanner_code(RuntimeError("no film loaded")) == {})
check("unknown code still named", "does not name" in L.scanner_code(ScannerError("x (Some(0Eh-01h-02h ILI(3)-?? raw=[]))"))["meaning"])

words = D.explain(feed); D.explain(feed)  # explained twice, as a failed frame is
errs = [x for x in entries() if x["kind"] == "error"]
check("explain logs the failure once", len(errs) == 1)
check("with code, meaning and advice", errs[0]["error"]["code"] == "04h-53h-00h" and errs[0]["error"]["advice"] == words)
D.explain(D.Cancelled())
check("Stop is not logged as an error", len([x for x in entries() if x["kind"] == "error"]) == 1)
try:
    raise ValueError("something unexpected")
except ValueError:
    traceback.print_exc()
tb = [x for x in entries() if x["kind"] == "error" and x["error"]["type"] == "ValueError"]
check("print_exc goes in the log with its traceback", tb and "something unexpected" in tb[0]["traceback"])

# history: old files go, today's stays, 0 keeps all
for name in ("app-2026-01-01.jsonl", "driver-2026-01-01_10-00-00.log", "app-2099-01-01.jsonl", "notes.txt"):
    (folder / name).write_text("x\n")
check("0 days keeps everything", log.prune(0) == 0 and (folder / "app-2026-01-01.jsonl").exists())
gone = log.prune(30)
check("older than 30 days deleted", gone == 2 and not (folder / "app-2026-01-01.jsonl").exists()
      and not (folder / "driver-2026-01-01_10-00-00.log").exists())
check("today's and other files stay", log.path().exists() and (folder / "notes.txt").exists())
s = log.summary()
check("summary counts the logs", s["on"] and s["files"] >= 2 and s["bytes"] > 0)

# in the app, with the pretend scanner
A.STRIP_CACHE = Path(home) / ".coolscan-strip.cache"
app = A.App(demo=True)
app.applog = log
app.connect()
check("connect logs what the scanner says about itself",
      any(x["kind"] == "scanner" and x["scanner"].get("optical_dpi") for x in entries()))
def boom():
    raise feed.__class__(str(feed))
app.run_job("scan", boom)
for _ in range(50):
    if not app.job:
        break
    time.sleep(0.1)
time.sleep(0.2)
jobs = [x for x in entries() if x["kind"] == "job" and x["msg"] == "scan"]
check("job start and end logged", [j["state"] for j in jobs] == ["started", "ended"] and "seconds" in jobs[1])
check("the job's failure logged with its code", any(x["kind"] == "error" and x["error"].get("code") == "04h-53h-00h"
                                                     and x is not errs[0] for x in entries()[len(errs):]))
A.STRIP_CACHE.write_bytes(b"strip")
r = app.clear_cache()
check("clear cache removes the kept strip", r["ok"] and not A.STRIP_CACHE.exists() and any("strip" in g for g in r["cleared"]))
app.job = "scan"
check("clear cache refuses while busy", app.clear_cache().get("busy"))
app.job = None
app.set_log_days(7)
check("history setting applied", log.days == 7)
check("state has the log summary", app.state()["logs"]["on"])
check("every line is still valid JSON", all(isinstance(x, dict) for x in entries()))
print("all passed" if not fails else f"FAILED {fails}")
