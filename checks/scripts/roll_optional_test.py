"""Roll folders switched off (save straight into the folder) and "Allow overwriting" with its confirm step."""
import sys, os, json, time, tempfile, pathlib, threading, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    app.settings.update(**{**dict(outdir=str(out), dpi=145, ir=False, basename=""), **kw})
    app.connect(); app.thumbnail(); return app, out
def names(out): return sorted(str(p.relative_to(out)) for p in out.rglob("*.tif"))

print("A roll folders on (the default) work as before")
app, out = fresh(); app.scan([0])
check("default is a roll folder and _R001", app.settings["use_roll"] is True and names(out) == [f"Roll 001/{D}_001_R001.tif"], str(names(out)))

print("B roll folders off: straight into the folder, no roll number")
app, out = fresh(use_roll=False); app.scan([0, 1])
check("files in the folder itself", names(out) == [f"{D}_001.tif", f"{D}_002.tif"], str(names(out)))
check("no Roll folder made", not any(p.is_dir() and p.name.startswith("Roll") for p in out.iterdir()))
check("the page says where", app.state()["roll_path"] == app._short_path(out) and app.state()["sample_name"] == f"{D}_003.tif", app.state()["sample_name"])
app.scan([2])
check("counts on in the folder", f"{D}_003.tif" in names(out), str(names(out)))
app.settings["film_description"] = "should not be written"
check("no Description.txt in a plain folder", not app._write_description() and not (out / "Description.txt").exists())
app, out = fresh(use_roll=False, archive=True, ir=True); app.scan([0])
check("Archive mode: RAW/ and Positive/ in the folder", names(out) == [f"Positive/{D}_001.tif", f"RAW/{D}_001.tif", f"RAW/{D}_001_IR.tif"], str(names(out)))
app, out = fresh(); app.scan([0]); app.settings["use_roll"] = False; app.scan([1])
check("switching off mid-way: next file goes to the folder", f"{D}_001.tif" in names(out) and f"Roll 001/{D}_001_R001.tif" in names(out), str(names(out)))
app.settings["use_roll"] = True
check("switching back on: the same roll carries on", app.state()["sample_name"] == f"{D}_002_R001.tif", app.state()["sample_name"])

print("C overwriting")
app, out = fresh(use_roll=False); app.scan([0, 1, 2])
first = (out / f"{D}_002.tif").stat().st_mtime_ns
app.settings["start"] = 2
check("off: a typed number in use is skipped, as before", app.next_number() == 4 and app.would_overwrite(2) == [])
app.settings["overwrite"] = True
check("on: the typed number is used as it is", app.next_number() == 2)
check("the names that would be replaced are listed", app.would_overwrite(3) == [f"{D}_002", f"{D}_003"], str(app.would_overwrite(3)))
time.sleep(0.02); app.scan([0, 1])
check("the files are replaced, nothing new made", names(out) == [f"{D}_001.tif", f"{D}_002.tif", f"{D}_003.tif"] and (out / f"{D}_002.tif").stat().st_mtime_ns != first, str(names(out)))
check("the typed number is used once", app.settings["start"] == 0 and app.next_number() == 4)
check("without a typed number nothing is overwritten", app.would_overwrite(5) == [])

print("D the server asks before a scan that would overwrite")
app, out = fresh(use_roll=False); app.scan([0, 1])
app.settings.update(start=1, overwrite=True)
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.server.make_handler(app)); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"
def post(path, body):
    req = urllib.request.Request(url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())
r = post("/api/scan", {"frames": [0]})
check("frame 1 was scanned with these settings already: asked about that first", r.get("confirm_again") == [0] and app.job is None, str(r))
r = post("/api/scan", {"frames": [0], "again_ok": True})
check("scan answers with the names, no job started", r.get("confirm_overwrite") == [f"{D}_001"] and not r.get("ok") and app.job is None, str(r))
r = post("/api/scan", {"frames": [0], "again_ok": True, "overwrite_ok": True})
for _ in range(200):
    if app.job is None: break
    time.sleep(0.05)
check("confirmed: scans and replaces", r.get("ok") and names(out) == [f"{D}_001.tif", f"{D}_002.tif"], str(r) + str(names(out)))
r = post("/api/settings", {"use_roll": True, "overwrite": False})
check("both switches save through the settings call", app.settings["use_roll"] is True and app.settings["overwrite"] is False)
r = post("/api/choose_folder", {})
check("folder picker: a clear answer off the Mac", (r.get("ok") is False and "Mac" in r.get("error", "")) if sys.platform != "darwin" else "ok" in r, str(r))
srv.shutdown()
check("roll switches are not part of saved settings", "use_roll" not in cs.PRESET_KEYS and "overwrite" not in cs.PRESET_KEYS)
print(); print("FAILED: " + ", ".join(bad) if bad else "all passed")
