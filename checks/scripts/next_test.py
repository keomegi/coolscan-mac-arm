"""Demo tests for the parts of the next version that had not been run: passes averaged,
messages, the ICE readiness check, the roll number change and the film description."""
import sys, os, time, json, tempfile, pathlib, hashlib, threading, stat
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np, tifffile
import cs_next as cs

tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []


def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        bad.append(name)


def fresh(**kw):
    app = cs.App(demo=True)
    out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=725, name_date=False, basename="Scan"), **kw})
    app.connect(); app.thumbnail()
    return app, out


def counted(app):
    calls = []
    real = app.session.scan_frame
    def wrapper(frame, **kw):
        calls.append(kw)
        return real(frame, **kw)
    app.session.scan_frame = wrapper
    return calls


def logged(app, word):
    return [l[9:] for l in app.log if word in l]


print("A multi-sample by averaging passes")
app, out = fresh(samples=4, clean=False, negatives="negative")
app.caps.max_samples = 1
calls = counted(app)
app.scan([0])
check("4x on a one-reading scanner takes 4 passes", len(calls) == 4, f"{len(calls)} passes")
check("every pass asks the scanner for 1 sample", all(c["samples"] == 1 for c in calls))
check("later passes reuse the first one's exposure and focus",
      all(isinstance(c.get("exposures"), dict) and c.get("focus") not in (None, "auto") for c in calls[1:]),
      f"focus={calls[1].get('focus')!r}")
check("log says 4 averaged", bool(logged(app, "averaged 4 passes")), "; ".join(logged(app, "pass ")[:3]))
f4 = tifffile.imread(str(next(out.rglob("Scan_001_R001.tif")))).astype(np.float64)
app1, out1 = fresh(samples=1, clean=False, negatives="negative")
app1.caps.max_samples = 1
app1.scan([0])
f1 = tifffile.imread(str(next(out1.rglob("Scan_001_R001.tif")))).astype(np.float64)
app1b, out1b = fresh(samples=1, clean=False, negatives="negative"); app1b.caps.max_samples = 1; app1b.scan([0])
f1b = tifffile.imread(str(next(out1b.rglob("Scan_001_R001.tif")))).astype(np.float64)
app4b, out4b = fresh(samples=4, clean=False, negatives="negative"); app4b.caps.max_samples = 1; app4b.scan([0])
f4b = tifffile.imread(str(next(out4b.rglob("Scan_001_R001.tif")))).astype(np.float64)
n1, n4 = (f1 - f1b).std() / np.sqrt(2), (f4 - f4b).std() / np.sqrt(2)
check("noise is about half with 4 passes", 0.4 < n4 / n1 < 0.6, f"noise {n1:.0f} -> {n4:.0f} (ratio {n4 / n1:.2f})")

app, out = fresh(samples=4, clean=False)
app.caps.max_samples = 1
real = app.session.scan_frame
state = {"n": 0}
def shifting(frame, **kw):
    state["n"] += 1
    r = real(frame, **kw)
    if state["n"] == 3:
        r.colors = {k: np.roll(v, 2, axis=1) for k, v in r.colors.items()}
    return r
app.session.scan_frame = shifting
app.scan([0])
check("a pass 2 pixels off is left out", bool(logged(app, "left out")) and bool(logged(app, "averaged 3 passes")),
      "; ".join(logged(app, "left out") + logged(app, "averaged")))

app, out = fresh(samples=4, clean=False)
calls = counted(app)
app.scan([0])
check("a scanner that can read 4x itself gets one pass with samples=4", len(calls) == 1 and calls[0]["samples"] == 4)

app, out = fresh(samples=2, archive=True)
app.caps.max_samples = 1
calls = counted(app)
app.scan([0])
roll = next(out.glob("Roll*"))
check("archive + 2 passes: 2 passes, none cleaned by the driver", len(calls) == 2 and not any(c.get("clean") for c in calls))
check("archive + 2 passes: RAW, IR and Positive written",
      sorted(p.name for p in roll.rglob("*.tif")) == ["Scan_001_R001.tif", "Scan_001_R001.tif", "Scan_001_R001_IR.tif"],
      str(sorted(str(p.relative_to(roll)) for p in roll.rglob("*.tif"))))
check("archive + 2 passes: dust removal ran on the average", bool(logged(app, "rebuilt")), "; ".join(logged(app, "rebuilt")))

app, out = fresh(samples=2, dust_method="pass")
app.caps.max_samples = 1
calls = counted(app)
app.scan([0])
check("2 passes + 'driver second pass': cleaned here instead", len(calls) == 2 and not any(c.get("clean") for c in calls)
      and bool(logged(app, "rebuilt")))

app, out = fresh(samples=4, clean=False)
app.caps.max_samples = 1
real = app.session.scan_frame
def stop_after_first(frame, **kw):
    r = real(frame, **kw); app.cancel = True; return r
app.session.scan_frame = stop_after_first
try:
    app.scan([0]); stopped = False
except Exception as e:
    stopped = cs._stopped(e)
check("Stop between passes stops, nothing saved", stopped and not list(out.rglob("*.tif")))

print("B messages")
fake_bin = tmp / "bin"; fake_bin.mkdir()
record = tmp / "osascript.log"
(fake_bin / "osascript").write_text(f"""#!{sys.executable}
import sys, json, os
args = sys.argv[1:]
with open({str(record)!r}, "a") as f:
    f.write(json.dumps(args) + "\\n")
mode = os.environ.get("FAKE_OSA", "")
script = " ".join(args)
if mode == "old" and "account" in script:
    sys.stderr.write("execution error: Messages got an error: Can't get account. (-1728)\\n"); sys.exit(1)
if mode == "denied":
    sys.stderr.write("execution error: Not authorized to send Apple events to Messages. (-1743)\\n"); sys.exit(1)
""")
(fake_bin / "osascript").chmod(0o755)
old_path = os.environ["PATH"]
os.environ["PATH"] = f"{fake_bin}:{old_path}"

(fake_bin / "shortcuts").write_text(f"""#!{sys.executable}
import sys, json, os
args = sys.argv[1:]
text = open(args[args.index("--input-path") + 1]).read() if "--input-path" in args else ""
with open({str(record)!r}, "a") as f:
    f.write(json.dumps(args[:2] + [text]) + "\\n")
if args[:1] == ["list"]:
    print("Tell my phone\\nAnother one")
if os.environ.get("FAKE_OSA") == "denied":
    sys.stderr.write("Error: The shortcut could not be found.\\n"); sys.exit(1)
""")
(fake_bin / "shortcuts").chmod(0o755)
app, out = fresh(notify_mac=True, notify_shortcut="Tell my phone")
check("nothing switched on: nothing sent", cs.App(demo=True).notify("x", wait=True) is False)
record.write_text("")
app.notify("Strip done: 6 of 6 frames saved in Roll 001.", wait=True)
sent = [json.loads(l) for l in record.read_text().splitlines()]
check("banner: text handed over as an argument, not pasted into the script",
      len(sent) == 2 and sent[0][-2:] == ["Coolscan", "Strip done: 6 of 6 frames saved in Roll 001."]
      and "display notification" in " ".join(sent[0]) and "Strip done" not in " ".join(sent[0][:-2]))
check("shortcut: run by name, the message handed over as its input",
      sent[1] == ["run", "Tell my phone", "Coolscan: Strip done: 6 of 6 frames saved in Roll 001."], str(sent[1]))
check("log says what was sent", len(logged(app, "sent:")) == 2, "; ".join(logged(app, "sent:")))
check("the page can list the shortcuts", cs.list_shortcuts() == ["Another one", "Tell my phone"], str(cs.list_shortcuts()))
check("no iMessage left in the app", not any("imessage" in p.read_text().lower() for p in cs.PACKAGE.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html")))
os.environ["FAKE_OSA"] = "denied"
app = cs.App(demo=True); app.settings.update(notify_shortcut="Gone", notify_mac=True)
app.notify("hello", wait=True)
check("a refusal is reported with macOS's own words, and nothing breaks", len(logged(app, "could not send")) == 2,
      "; ".join(logged(app, "could not send")))
os.environ["FAKE_OSA"] = ""

got = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        got.append((self.path, self.headers.get("Title"), self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
app, out = fresh(notify_ntfy=f"http://127.0.0.1:{srv.server_port}/my-topic", clean=False)
app.new_roll({"film_brand": "Kodak", "film_stock": "Portra", "film_iso": "400", "film_notes": "Anna's wedding"})
app.scan([0, 1])
for _ in range(50):
    if got: break
    time.sleep(0.1)
check("a finished strip sends one message with the count", len(got) == 1 and "2 of 2 frames saved" in got[0][2], str(got))
check("what leaves the Mac for ntfy has the roll number but not the film notes",
      bool(got) and "Roll 001." in got[0][2] and "Anna" not in got[0][2] and "Kodak" not in got[0][2]
      and "Anna" in app.settings["roll"])
check("an ntfy topic name becomes an ntfy.sh address", True)  # the address is built in send_ntfy; not sent anywhere here

class TransportError(RuntimeError): pass
got.clear()
real = app.session.scan_frame
def unplugged(frame, **kw):
    raise TransportError("io error: device disconnected")
app.session.scan_frame = unplugged
try:
    app.scan([0, 1])
except Exception:
    pass
for _ in range(50):
    if got: break
    time.sleep(0.1)
check("a batch that stops on an error sends a message too", len(got) == 1 and "stopped after 0 of 2" in got[0][2], str(got))

os.environ["PATH"] = "/nonexistent"
app = cs.App(demo=True); app.settings.update(notify_mac=True)
app.notify("hello", wait=True)
check("no osascript (not a Mac): said plainly, no crash", bool(logged(app, "needs a Mac")), "; ".join(logged(app, "could not")))
os.environ["PATH"] = old_path

print("C Nikon ICE helper readiness")
helper = cs.ICE_HELPER
assert not helper.exists(), helper
try:
    app = cs.App(demo=True)
    check("no helper file: not offered", app.check_ice() is False and app.state()["ice_helper"] is False)
    helper.write_text("#!/bin/sh\necho '{\"ready\": false, \"missing\": [\"wine\"]}'\nexit 3\n"); helper.chmod(0o644)
    check("helper there but says not ready (exit 3): not offered", app.check_ice() is False and app.state()["ice_helper"] is False)
    app.settings["dust_method"] = "ice"
    check("...and a saved choice of ICE falls back to the built-in method", app._dust_method() == "builtin")
    helper.write_text("#!/bin/sh\nsleep 60\n"); helper.chmod(0o755)
    real_run = cs.subprocess.run
    cs.subprocess.run = lambda *a, **kw: real_run(*a, **{**kw, "timeout": 1})
    check("helper hangs: not offered", app.check_ice() is False)
    cs.subprocess.run = real_run
    real_helper = pathlib.Path("/mnt/project-files/ice-harness/mac/ice_clean")
    helper.write_bytes(real_helper.read_bytes()); helper.chmod(0o644)   # as copied: no "may be run" mark
    run = real_run(app._ice_cmd() + ["--status"], capture_output=True, text=True, timeout=30)
    check("the real ice_clean, without Wine: runs through Python, says not ready, not offered",
          run.returncode != 0 and app.check_ice() is False, f"exit {run.returncode}: {run.stdout.strip()[:120]}")
    helper.write_text("#!/bin/sh\n[ \"$1\" = --status ] && exit 0\nexit 3\n"); helper.chmod(0o755)
    check("helper says ready (exit 0): offered", app.check_ice() is True and app.state()["ice_helper"] is True
          and app._dust_method() == "ice")
finally:
    if helper.exists():
        helper.unlink()

print("D roll number")
def tree(base):
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
def digests(base):
    return sorted(hashlib.sha1(p.read_bytes()).hexdigest() for p in base.rglob("*.tif"))

app, out = fresh(archive=True)
app.scan([0, 1])                                             # Roll 001: two test frames
app.new_roll({"film_brand": "Old", "film_stock": "Test"}); app.scan([0])   # Roll 002 - Old Test
app.new_roll({"film_brand": "Kodak", "film_stock": "Portra", "film_iso": "400"})
app.describe("Leica M6, Lisbon.\nSecond line.")
app.scan([0, 1])                                             # Roll 003 - Kodak Portra ISO 400: the one being scanned
before, sums = tree(out), digests(out)
check("set-up: three rolls, the current one is 003", app.settings["roll"] == "Roll 003 - Kodak Portra ISO 400", app.settings["roll"])
r = app.quick_job("renumbering", lambda: app.renumber_roll(1))
check("Roll 001 taken: asks first, lists what is in the way with file counts",
      r.get("ok") is False and [d["name"] for d in r["in_the_way"]] == ["Roll 001", "Roll 002 - Old Test"]
      and r["in_the_way"][0]["files"] == 8, json.dumps(r))   # 6 pictures, the scan log, the film colour notes
check("...and nothing was changed", tree(out) == before and app.settings["roll"].startswith("Roll 003"))
r = app.quick_job("renumbering", lambda: app.renumber_roll(1, move_aside=True))
after = tree(out)
check("after the yes: this roll is Roll 001", r.get("ok") and app.settings["roll"] == "Roll 001 - Kodak Portra ISO 400", json.dumps(r))
check("its files now say _R001", sorted(p.name for p in (out / app.settings["roll"]).rglob("*.tif")) ==
      ["Scan_001_R001.tif", "Scan_001_R001.tif", "Scan_001_R001_IR.tif", "Scan_002_R001.tif", "Scan_002_R001.tif", "Scan_002_R001_IR.tif"])
check("older rolls moved whole into 'Earlier rolls', names inside untouched",
      "Earlier rolls/Roll 001/RAW/Scan_001_R001.tif" in after and "Earlier rolls/Roll 002 - Old Test/Positive/Scan_001_R002.tif" in after)
check("same number of files, every picture byte for byte the same", len(after) == len(before) and digests(out) == sums,
      f"{len(before)} files")
check("description and scan log came along", (out / app.settings["roll"] / "Description.txt").read_text() == "Leica M6, Lisbon.\nSecond line.\n"
      and "Roll number changed" in (out / app.settings["roll"] / "ScanLog.txt").read_text())
check("saved in the settings file", json.loads(cs.SETTINGS_FILE.read_text())["roll"] == app.settings["roll"])
app.scan([2])
check("the next frame carries on: Scan_003_R001", (out / app.settings["roll"] / "Positive" / "Scan_003_R001.tif").is_file())
app.new_roll({})
check("New roll is now Roll 002", app.settings["roll"] == "Roll 002", app.settings["roll"])

app, out = fresh()
app.scan([0])
(out / "Earlier rolls" / "Roll 001").mkdir(parents=True); (out / "Earlier rolls" / "Roll 001" / "keep.txt").write_text("x")
app.new_roll({}); app.scan([0])
r = app.renumber_roll(1, move_aside=True)
check("a name already used in 'Earlier rolls' is not overwritten", r.get("ok") and (out / "Earlier rolls" / "Roll 001 (2)" / "Scan_001_R001.tif").is_file()
      and (out / "Earlier rolls" / "Roll 001" / "keep.txt").read_text() == "x", str(tree(out)))

app, out = fresh()
app.new_roll({}); app.scan([0])            # Roll 002 (no folder for 001 yet)
r = app.renumber_roll(7)
check("free number: changed without questions", r.get("ok") and app.settings["roll"] == "Roll 007" and tree(out)[0] == "Roll 007/Description.txt"
      or (r.get("ok") and app.settings["roll"] == "Roll 007" and "Roll 007/Scan_001_R007.tif" in tree(out)), str(tree(out)))
(out / "Roll 007" / "Scan_001_R009.tif").write_text("someone else's file")
before = tree(out)
r = app.renumber_roll(9)
check("a file name that is already taken: refuses, nothing changed", r.get("ok") is False and "error" in r and tree(out) == before
      and app.settings["roll"] == "Roll 007", json.dumps(r))
(out / "Roll 007" / "Scan_001_R009.tif").unlink()
app.scan([1])
before, sums = tree(out), digests(out)
real_rename, n = os.rename, {"n": 0}
def failing(a, b):
    n["n"] += 1
    if n["n"] == 3:
        raise PermissionError(13, "Permission denied", str(a))
    return real_rename(a, b)
cs.os.rename = failing
r = app.renumber_roll(3)
cs.os.rename = real_rename
check("a rename fails half way: everything is put back", r.get("ok") is False and tree(out) == before and digests(out) == sums
      and app.settings["roll"] == "Roll 007", json.dumps(r))
check("a silly number is refused", app.renumber_roll(0).get("ok") is False and app.renumber_roll("abc").get("ok") is False
      and app.renumber_roll(1000).get("ok") is False)
check("same number, nothing in the way: nothing to do", app.renumber_roll(7).get("same") is True)
app.job = "scan"
check("during a scan: busy, nothing changes", app.quick_job("renumbering", lambda: app.renumber_roll(1)) == {"ok": False, "busy": True})
app.job = None

print("E film description")
app, out = fresh()
check("no description: no file, no folder made", app._write_description() is False and not list(out.iterdir()))
app.describe("  Olympus XA\r\nBrighton, August 2004  ")
f = out / "Roll 001" / "Description.txt"
check("typed in the app: Description.txt in the roll folder", f.read_text() == "Olympus XA\nBrighton, August 2004\n", repr(f.read_text()) if f.exists() else "no file")
app.describe("Olympus XA\nBrighton, August 2004\nRoll 3 of the trip")
check("edited: file updated", f.read_text().endswith("Roll 3 of the trip\n"))
app.job = "scan"; app.describe("Edited while scanning"); app.job = None
check("editing during a scan works", f.read_text() == "Edited while scanning\n")
app.new_roll({"film_brand": "Ilford", "film_stock": "HP5", "film_description": "Pushed to 800"})
check("New roll with a description: file in the new folder, old one left as it was",
      (out / "Roll 002 - Ilford HP5" / "Description.txt").read_text() == "Pushed to 800\n" and f.read_text() == "Edited while scanning\n")
app.new_roll({})
check("New roll without one: field empty, no file", app.settings["film_description"] == "" and not (out / "Roll 003").exists())
app.settings["roll"] = "Roll 002 - Ilford HP5"
(out / "Roll 002 - Ilford HP5" / "Description.txt").write_text("Changed in TextEdit\n")
app.read_description()
check("at start, a file edited by hand wins", app.settings["film_description"] == "Changed in TextEdit")
app.describe("")
check("emptied in the app: file emptied, not deleted", (out / "Roll 002 - Ilford HP5" / "Description.txt").read_text() == "")
ro = pathlib.Path(tempfile.mkdtemp()); app.settings["outdir"] = str(ro / "nope" / "\0bad")
try:
    app.describe("x"); crashed = False
except Exception as e:
    crashed = repr(e)
check("a folder that cannot be written: said in the log, no crash", crashed is False or "embedded null" in str(crashed), str(crashed))

print()
print("FAILED: " + ", ".join(bad) if bad else "all passed")
