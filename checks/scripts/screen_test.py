"""The screen batch: a picture per frame and where it comes from, crop, saved settings,
and the built-in dust cleaner on ordinary scans."""
import sys, json, time, tempfile, pathlib, threading, urllib.request
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import tifffile
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
def size(path):
    a = tifffile.imread(path)
    return a.shape[:2] if a.shape[-1] in (3, 4) or a.ndim == 2 else a.shape[1:]
def only(out, pattern="*.tif"):
    return sorted(p for p in out.rglob(pattern))

print("A a picture for every frame, and where it comes from")
app, out = fresh()
t = app.state()["tiles"]
check("after the thumbnail pass every frame has a picture, marked Thumbnail",
      sorted(t) == list(range(6)) and all(x["src"] == "thumbnail" and x["v"] > 0 for x in t.values()), str({i: x["src"] for i, x in t.items()}))
check("...and says so in words", t[0]["info"].startswith("from the thumbnail pass, "), t[0]["info"])
v0 = {i: x["v"] for i, x in t.items()}
app.preview([2])
t = app.state()["tiles"]
check("a preview scan changes that frame only, to Preview",
      [t[i]["src"] for i in range(6)] == ["thumbnail"] * 2 + ["preview"] + ["thumbnail"] * 3
      and t[2]["v"] > v0[2] and all(t[i]["v"] == v0[i] for i in (0, 1, 3, 4, 5)), t[2]["info"])
check("the setting that swapped preview scans for thumbnail cuts is gone", "preview_mode" not in app.settings)
app.scan([2])
t = app.state()["tiles"]
check("after scanning the frame shows the scan itself", t[2]["src"] == "scan" and t[2]["info"].startswith("saved as "), t[2]["info"])
check("...with the files it was saved as and how long it took",
      len(t[2]["saved"]) == 1 and t[2]["saved"][0]["files"] and t[2]["saved"][0]["took"] >= 0, str(t[2]["saved"]))
check("frames that were not scanned keep their thumbnail", t[0]["src"] == "thumbnail" and t[0]["saved"] == [])
app.scan([2])
check("scanned twice: both lots of files are listed", len(app.state()["tiles"][2]["saved"]) == 2)
vv = app.state()["tiles"][2]["v"]
app.rotate([2], 1)
t = app.state()["tiles"]
check("turning a frame makes its picture again and notes the turn", t[2]["turns"] == 1 and t[2]["v"] > vv and t[0]["turns"] == 0)
app.set_crop(1, (0.1, 0.1, 0.9, 0.9))
app.thumbnail()
t = app.state()["tiles"]
check("a new thumbnail pass starts over: thumbnails again, nothing saved, no crop, no turn",
      all(x["src"] == "thumbnail" and x["saved"] == [] and x["crop"] is None and x["turns"] == 0 for x in t.values()))
app.eject()
check("after Eject there are no frames and no pictures", app.state()["tiles"] == {} and app.previews == {})
big = np.arange(4000 * 2600, dtype=np.uint32).reshape(2600, 4000).astype(np.uint16)
small = cs.shrink(big)
check("a full scan is made smaller for the screen (longer side 1600 at most), averaged, not thinned",
      max(small.shape) <= 1600 and small.dtype == np.uint16 and abs(float(small[:100, :100].mean()) - float(big[:300, :300].mean())) < 2,
      str(small.shape))
check("a small picture is left as it is", cs.shrink(big[:500, :700]) is not None and cs.shrink(big[:500, :700]).shape == (500, 700))

print("B crop")
app, out = fresh()
check("a crop is kept as fractions of the frame, sorted and rounded",
      app.set_crop(0, (0.75, 0.8, 0.25, 0.2))["crop"] == (0.25, 0.2, 0.75, 0.8) and app.state()["tiles"][0]["crop"] == (0.25, 0.2, 0.75, 0.8))
check("numbers outside the frame are pulled in", app.set_crop(0, (-1, -1, 2, 2))["crop"] == (0.0, 0.0, 1.0, 1.0))
check("a crop too small to be meant is dropped", app.set_crop(0, (0.5, 0.5, 0.52, 0.9))["crop"] is None and 0 not in app.crops)
app.set_crop(0, (0.2, 0.2, 0.8, 0.8))
check("no rectangle clears the crop", app.set_crop(0, None)["crop"] is None)
check("a frame that is not there: refused, nothing kept", app.set_crop(9, (0.1, 0.1, 0.9, 0.9))["ok"] is False and app.crops == {})
app.scan([0]); whole = size(only(out)[0])
app.set_crop(1, (0.25, 0.0, 0.75, 1.0))
app.scan([1]); cut = size(only(out)[1])
check("a cropped frame is saved cropped (half the width here), an uncropped one whole",
      cut[0] == whole[0] and abs(cut[1] - whole[1] / 2) <= 1, f"whole {whole}, cropped {cut}")
check("the log and the picture on screen say it was cropped",
      any(", cropped to " in l for l in logged(app, "saved ")) and ", cropped" in app.state()["tiles"][1]["info"], app.state()["tiles"][1]["info"])
app.rotate([2], 1)
app.set_crop(2, (0.25, 0.0, 0.75, 1.0))   # the same part of the film, on a frame turned a quarter
app.scan([2]); turned = size(only(out)[2])
check("on a turned frame the crop turns with it", turned[1] == whole[0] and abs(turned[0] - whole[1] / 2) <= 1, f"{turned}")
app, out = fresh(ir=True, clean=True)
app.set_crop(0, (0.1, 0.2, 0.6, 0.9))
app.scan([0])
files = {p.name: size(p) for p in only(out)}
names = sorted(files)
check("the infrared file is cropped the same as the picture", len(names) == 2 and files[names[0]] == files[names[1]], str(files))
app, out = fresh(archive=True, ir=False)
app.set_crop(0, (0.25, 0.25, 0.75, 0.75))
app.scan([0])
raw, pos = size(only(out / "Roll 001" / "RAW")[0]), size(only(out / "Roll 001" / "Positive")[0])
check("Archive mode: RAW keeps the whole frame, Positive is cropped",
      raw == whole and abs(pos[0] - whole[0] / 2) <= 1 and abs(pos[1] - whole[1] / 2) <= 1, f"RAW {raw}, Positive {pos}")
app, out = fresh(negatives="both")
app.set_crop(0, (0.0, 0.0, 0.5, 1.0))
app.scan([0])
sizes = {p.name: size(p) for p in only(out)}
check("negative and positive of the same frame are both cropped", len(sizes) == 2 and len(set(sizes.values())) == 1
      and abs(list(sizes.values())[0][1] - whole[1] / 2) <= 1, str(sizes))

print("C saved settings")
app, out = fresh()
check("no name: refused", app.save_preset("  ")["ok"] is False and app.settings["presets"] == {})
check("nothing saved yet: no name shown", app.state()["preset"] is None and app.state()["presets"] == [])
app.settings.update(dpi=1450, samples=4, colour_space="sRGB IEC61966-2.1")
r = app.save_preset("  Quick   look ")
check("saved under a tidy name", r == {"ok": True, "name": "Quick look"} and app.state()["presets"] == ["Quick look"])
check("the screen shows which saved settings are in use", app.state()["preset"] == "Quick look")
held = app.settings["presets"]["Quick look"]
check("what is saved is how to scan and save, not the folder, the roll or who to tell",
      set(held) == set(cs.PRESET_KEYS) and not {"outdir", "roll", "notify_ntfy", "notify_shortcut", "presets", "rolls"} & set(held))
app.settings.update(dpi=2900, samples=1)
check("change a setting and the name goes away (shown as not saved)", app.state()["preset"] is None)
app.save_preset("Full quality")
roll, folder = app.settings["roll"], app.settings["outdir"]
app.settings["notify_ntfy"] = "my-topic"
r = app.load_preset("Quick look")
check("loading brings the saved values back", r["ok"] and app.settings["dpi"] == 1450 and app.settings["samples"] == 4 and app.state()["preset"] == "Quick look")
check("...and leaves the folder, the roll and the message settings alone",
      app.settings["roll"] == roll and app.settings["outdir"] == folder and app.settings["notify_ntfy"] == "my-topic")
app.settings["dpi"] = 725
app.save_preset("Quick look")
check("saving under a name that exists updates it", app.settings["presets"]["Quick look"]["dpi"] == 725 and len(app.settings["presets"]) == 2
      and any('"Quick look" updated' in l for l in app.log))
a2 = cs.App(demo=True); a2.load_settings()
check("saved settings are still there after a restart", sorted(a2.settings["presets"]) == ["Full quality", "Quick look"]
      and a2.settings["presets"]["Full quality"]["dpi"] == 2900)
data = json.loads(cs.SETTINGS_FILE.read_text())
data["presets"]["Old one"] = {"dpi": "fast", "samples": 2, "gone_setting": 1}
cs.SETTINGS_FILE.write_text(json.dumps(data))
a3 = cs.App(demo=True); a3.load_settings(); before = a3.settings["dpi"]
r = a3.load_preset("Old one")
check("saved settings from another version: what fits is used, what does not is left",
      r["ok"] and a3.settings["samples"] == 2 and a3.settings["dpi"] == before and "gone_setting" not in a3.settings)
check("deleting removes the name and leaves the settings in use alone",
      app.delete_preset("Quick look")["ok"] and app.state()["presets"] == ["Full quality"] and app.settings["dpi"] == 725)
check("deleting or loading a name that is not there: said, nothing breaks",
      app.delete_preset("Nope")["ok"] is False and app.load_preset("Nope")["ok"] is False)
app.thumbnail(); v = app.state()["tiles"][0]["v"]
app.settings["tone"] = "nikon"; app.save_preset("Nikon look"); app.settings["tone"] = "flat"
app.load_preset("Nikon look")
check("loading saved settings draws the pictures again in the new look", app.state()["tiles"][0]["v"] > v)

print("D the built-in dust cleaner on ordinary scans")
app, out = fresh(clean=True, ir=False)
asked = []
real = app.session.scan_frame
def scan_frame(frame, **kw):
    asked.append({k: kw.get(k) for k in ("infrared", "clean")}); return real(frame, **kw)
app.session.scan_frame = scan_frame
app.scan([0])
check("IR Clean: the pass is taken with its infrared and cleaned in the app, not by the driver",
      asked[-1] == {"infrared": True, "clean": False} and any("dust removal rebuilt" in l and " in " in l for l in logged(app, "frame 1")), str(asked[-1]))
check("...and no infrared file is saved when that is unticked", [p.name for p in only(out)] == [p.name for p in only(out) if "_IR" not in p.name])
app.settings["dust_method"] = "pass"
app.scan([1])
check("nkscan: the driver cleans inside the pass", asked[-1] == {"infrared": True, "clean": True}, str(asked[-1]))
app.settings.update(dust_method="builtin", clean=False)
app.scan([2])
check("dust removal off: no cleaning, no infrared asked for", asked[-1] == {"infrared": False, "clean": False}, str(asked[-1]))
rng = np.random.default_rng(3)
film = [np.clip(rng.normal(30000, 600, (240, 360)), 0, 65535).astype(np.uint16) for _ in range(3)]
ir = np.clip(rng.normal(50000, 300, (240, 360)), 0, 65535).astype(np.uint16)
dirty = [p.copy() for p in film]; d_ir = ir.copy()
dirty[0][100:103, 50:300] //= 3; dirty[1][100:103, 50:300] //= 3; dirty[2][100:103, 50:300] //= 3; d_ir[100:103, 50:300] //= 3
cleaned, count = cs.dust_clean(dirty, d_ir, 2900)
mask = np.zeros(ir.shape, bool); mask[100:103, 50:300] = True
untouched = float(np.mean([(c == d)[~mask].mean() for c, d in zip(cleaned, dirty)]))
gone = float(np.mean([abs(float(c[mask].mean()) - 30000) for c in cleaned]))
check("the cleaner: a scratch is filled, and clean film comes back as scanned",
      gone < 1500 and untouched > 0.85 and all((c >= d).all() for c, d in zip(cleaned, dirty)),
      f"scratch now {gone:.0f} from the film's level, {untouched:.1%} of clean film untouched, {count} pixels changed")
check("no infrared to go by: nothing is done", cs.dust_clean(dirty, np.zeros_like(ir), 2900) is None)

print("E the page's own addresses")
import http.server
app, out = fresh()
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cs.make_handler(app))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
def get(path):
    with urllib.request.urlopen(base + path) as r: return r.status, r.read()
def post(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r: return json.loads(r.read())
status, png = get("/frame/0.png")
check("the picture of a frame is served", status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n")
check("a crop set from the page is kept", post("/api/crop", {"frame": 1, "rect": [0.1, 0.1, 0.9, 0.9]}).get("crop") == [0.1, 0.1, 0.9, 0.9]
      and app.crops[1] == (0.1, 0.1, 0.9, 0.9))
check("...and cleared", post("/api/crop", {"frame": 1, "rect": None}).get("ok") and 1 not in app.crops)
check("settings saved from the page", post("/api/preset", {"action": "save", "name": "From the page"}).get("ok") and "From the page" in app.settings["presets"])
post("/api/preset", {"action": "delete", "name": "From the page"})
deadline = time.time() + 5
while "From the page" in app.settings["presets"] and time.time() < deadline: time.sleep(0.05)
check("...and deleted from the page", "From the page" not in app.settings["presets"])
state = json.loads(get("/api/state")[1])
check("the page is told about pictures, saved settings and crops", all(k in state for k in ("tiles", "presets", "preset", "thumb_v")))
check("saved settings cannot be overwritten through the ordinary settings address",
      "presets" in app.settings and post("/api/settings", {"presets": {"x": {}}}) is not None and app.settings["presets"] == {})
server.shutdown()

print()
print("all passed" if not bad else f"{len(bad)} FAILED: {bad}")
sys.exit(1 if bad else 0)
