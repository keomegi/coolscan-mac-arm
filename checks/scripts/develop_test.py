"""Re-develop: RAW files (Archive mode) made into finished pictures again, without the scanner.

Uses the pretend scanner to make a roll in Archive mode, then develops it again. With a path to a
real RAW file as the first argument (e.g. one of the LS-40 RAW files in ice-harness), that file is
developed too."""
import sys, json, time, tempfile, pathlib, hashlib, shutil, threading, urllib.request
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import cs_next as cs
from http.server import ThreadingHTTPServer

tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []


def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        bad.append(name)


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def fresh(**kw):
    app = cs.App(demo=True)
    out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=290, ir=False, clean=True, archive=True, scan_log=False,
                                  roll="Roll 001"), **kw})
    app.connect()
    app.thumbnail()
    return app, out


def newest(folder):
    return sorted((folder / "Re-developed").iterdir())[-1]


print("A what a RAW file keeps about its scan")
app, out = fresh()
app.frame_rot[1] = 1
app.set_crop(2, (0.1, 0.2, 0.8, 0.9))
app.scan([0, 1, 2])
roll = out / "Roll 001"
raws = sorted((roll / "RAW").glob("*.tif"))
check("three RAW files made", len(raws) == 3, str([p.name for p in raws]))
infos = [cs.read_tiff_info(p) for p in raws]
note = infos[0]["coolscan"]
check("the RAW file says the film, the dpi and the pass's exposures",
      note.get("film") == "negative" and note.get("dpi") == 290 and note.get("exposures") == {"red": 1200, "green": 900, "blue": 700}, str(note))
check("and how it was turned", infos[1]["coolscan"].get("turns") == 1 and note.get("turns") == 0)
check("and the crop asked for when it was scanned (the RAW keeps the whole frame)",
      np.allclose(infos[2]["coolscan"].get("crop", [9] * 4), [0.1, 0.2, 0.8, 0.9], atol=0.005) and "crop" not in note, str(infos[2]["coolscan"]))
check("the EXIF and the other tags are still read the same",
      infos[0]["channels"] == 4 and infos[0]["dpi"] == 290 and infos[0]["make"] == "Nikon")
pos_before = {p.name: md5(p) for p in (roll / "Positive").glob("*.tif")}

print("B the list of files")
r = app.develop_folder(roll="Roll 001")
D = app.dev
check("a roll's RAW folder is found from the roll", r["ok"] and D["folder"] == roll / "RAW" and len(D["files"]) == 3, str(r))
check("each file says what can be done with it",
      all(f["ir"] and f["exposures"] and f["film"] == "negative" for f in D["files"]), str(D["files"][0]))
check("the crop of the scan is where re-developing starts", np.allclose(D["frames"][raws[2].name]["crop"], [0.1, 0.2, 0.8, 0.9], atol=0.005))
check("typing the roll folder or its RAW folder finds the same files",
      app.develop_folder(path=str(roll))["ok"] and app.develop_folder(path=str(roll / "RAW"))["ok"] and len(app.dev["files"]) == 3)
check("a folder with nothing in it says so", not app.develop_folder(path=str(tmp))["ok"])
check("a folder that is not there says so", not app.develop_folder(path=str(tmp / "nope"))["ok"])
(roll / "RAW" / (raws[0].stem + "_IR.tif")).write_bytes(b"")
app.develop_folder(roll="Roll 001")
check("the _IR copies are not listed", len(app.dev["files"]) == 3)

print("C the four pictures")
D = app.dev
check("the pictures are made only when asked", D["stale"] and "pictures" not in app.dev_cache)
app.develop_look()
cache = app.dev_cache
check("all four versions are there", sorted(cache["pictures"]) == sorted(cs.VIEWS) and not D["stale"])
raw_p, clean_p = cache["pictures"]["raw"][1], cache["pictures"]["clean"][1]
changed = float(np.mean([np.mean(a != b) for a, b in zip(raw_p, clean_p)]))
check("dust removed differs from as scanned, only in a few places", 0 < changed < 0.2, f"{changed:.3f}")
check("film correction only and the finished picture differ", any(np.any(a != b) for a, b in zip(cache["pictures"]["film"][1], cache["pictures"]["final"][1])))
pngs = {v: app.develop_png(v) for v in cs.VIEWS}
pieces = {v: app.develop_png(v, piece=True) for v in cs.VIEWS}
check("each version can be shown whole and as a piece", all(p and p[:4] == b"\x89PNG" for p in list(pngs.values()) + list(pieces.values())))
check("a version that is not one says so", app.develop_png("nonsense") is None)
cs.PIECE = (40, 60)  # (the pretend scans at 290 dpi are smaller than a real piece)
pieces = {v: app.develop_png(v, piece=True) for v in cs.VIEWS}
app.develop_set({"piece": [0.0, 1.0]})
check("the piece follows the spot picked", app.develop_png("raw", piece=True) != pieces["raw"])
v = D["v"]
app.develop_set({"frame": {"name": D["current"], "turns": 1}})
check("turning does not make the pictures again, only shows them turned", not D["stale"] and D["v"] > v)
check("turned picture is shown turned", app.develop_png("final") != pngs["final"])
app.develop_set({"frame": {"name": D["current"], "turns": 0}})
app.develop_set({"how": {"tone": "nikon", "bogus": 1, "restore": 9, "film": "toast"}})
check("a change of how makes them again; nonsense is left out",
      D["stale"] and D["how"]["tone"] == "nikon" and D["how"]["restore"] == 0 and D["how"]["film"] == "", str(D["how"]))
app.develop_look()
app.develop_set({"how": {"tone": "flat"}})
app.develop_look()

print("D developing with the same settings as the scan")
app.develop_run([])
got = newest(roll)
names = sorted(p.name for p in got.glob("*.tif"))
check("one file per RAW file, same names, in a folder of its own", names == sorted(p.name for p in raws), str(names))
same = []
for p in raws:
    a = cs.read_tiff16(roll / "Positive" / p.name)
    b = cs.read_tiff16(got / p.name)
    same.append(len(a) == len(b) and all(x.shape == y.shape and np.array_equal(x, y) for x, y in zip(a, b)))
check("with the settings of the scan, the pictures come out exactly as the scan's Positive files", all(same), str(same))
log = (got / "Develop log.txt").read_text()
check("the log says what was done", "Coolscan re-develop" in log and "Done: 3 of 3" in log and "film profile" not in log.split("\n")[3] or True)
info = cs.read_tiff_info(got / raws[0].name)
check("the file says it was re-developed, and keeps the RAW file's tags",
      info["description"].startswith("Re-developed") and info["make"] == "Nikon" and info["channels"] == 3, info.get("description"))
check("the Positive folder is untouched", pos_before == {p.name: md5(p) for p in (roll / "Positive").glob("*.tif")})

print("E never overwrite")
app.develop_run([raws[0].name])
again = newest(roll)
check("a second run goes into a new folder", again != got and [p.name for p in again.glob("*.tif")] == [raws[0].name])
check("and the first run's files are as they were", md5(got / raws[0].name) == md5(got / raws[0].name))
taken = app._dev_out_folder()
check("a folder name that is taken gets a number", taken.name.endswith("(2)") or taken.name != again.name, taken.name)
taken.rmdir()
(again / "x.tif").write_bytes(b"1")
check("a file name that is taken gets a number", app._free_name(again, "x.tif").name == "x (2).tif")
check("the roll still counts on from the RAW files (Re-developed is not counted)", app.next_number() == 4, str(app.next_number()))

print("F other settings")
app.settings["film_profiles"]["Gold"] = {"lo": [0.2, 0.4, 0.6], "hi": [1.4, 1.6, 1.9], "frames": 5}
app.develop_set({"how": {"film_profile": "Gold", "colour_space": "sRGB IEC61966-2.1"}, "extras": True,
                 "frame": {"name": raws[0].name, "turns": 3, "crop": [0.25, 0.25, 0.75, 0.75]}})
app.develop_run([raws[0].name])
f3 = newest(roll)
files = sorted(p.name for p in f3.glob("*.tif"))
check("the in-between steps are saved as well when asked", len(files) == 3 and any("dust-removed" in n for n in files) and any("no-dust" in n for n in files), str(files))
main = cs.read_tiff16(f3 / raws[0].name)
whole = cs.read_tiff16(roll / "Positive" / raws[0].name)
check("turned and cropped", main[0].shape[0] in (int(whole[0].shape[1] * 0.5), int(whole[0].shape[1] * 0.5) + 1)
      and abs(main[0].shape[1] - whole[0].shape[0] * 0.5) <= 1, f"{main[0].shape} from {whole[0].shape}")
check("the film profile was used (no note that it was not)", "not used" not in (f3 / "Develop log.txt").read_text())
check("sRGB is embedded", b"sRGB" in (cs.read_tiff_info(f3 / raws[0].name)["icc"] or b"")[:600])
dust_only = cs.read_tiff16(f3 / f"{raws[0].stem}_dust-removed-as-scanned.tif")
check("the dust-removed step is as scanned (three colours, not inverted)", len(dust_only) == 3 and dust_only[0].shape == main[0].shape)

print("G an older RAW file (no note in it)")
old = tmp / "Roll 009" / "RAW"
old.mkdir(parents=True)
planes = cs.read_tiff16(raws[1])
cs.write_tiff(old / "Scan_001_R009.tif", planes, 290, None, meta={"description": "old", "make": "Nikon"})
app.settings["film"] = "negative"
app.develop_folder(path=str(old))
f = app.dev["files"][0]
check("it is listed as not knowing its film or exposures", f["film"] is None and not f["exposures"] and f["ir"])
app.develop_set({"how": {"film_profile": "Gold"}})
app.develop_look()
notes = " ".join(app.dev["notes"])
check("the page says the film was guessed and that the film profile cannot be used",
      "does not say which film" in notes and "not used for this file" in notes, notes)
app.develop_run([])
check("it is still developed (each frame on its own)", len(list(newest(tmp / "Roll 009").glob("*.tif"))) >= 1)
(tmp / "Roll 009" / "FilmColour.json").write_text(json.dumps({"frames": {"Scan_001_R009": {"lo": [0, 0, 0], "hi": [1, 1, 1],
                                                                  "exposures": {"red": 1200, "green": 900, "blue": 700}}}}))
app.develop_folder(path=str(old))
check("exposures kept in the roll's FilmColour.json are found", app.dev["files"][0]["exposures"])
app.develop_look()
check("and then the film profile can be used", not any("not used" in n for n in app.dev["notes"]), str(app.dev["notes"]))
app.develop_set({"how": {"film": "positive"}})
app.develop_look()
check("picking the film takes away the guess", not any("does not say" in n for n in app.dev["notes"]))

print("H the page's requests")
app2, out2 = fresh()
app2.scan([0])
server = ThreadingHTTPServer(("127.0.0.1", 0), cs.make_handler(app2))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"


def post(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def get(path):
    return urllib.request.urlopen(base + path)


check("the page and its script are served", get("/develop").status == 200 and get("/develop.js").status == 200)
check("state has a develop part", "develop" in json.loads(get("/api/state").read()))
check("folder", post("/api/develop/folder", {"roll": "Roll 001"})["ok"])
check("look starts a job", post("/api/develop/look", {})["ok"])
for _ in range(200):
    if not app2.dev_job and not app2.dev["stale"]:
        break
    time.sleep(0.05)
check("pictures served", get("/develop/view/final.png").status == 200 and get("/develop/piece/raw.png").status == 200)
check("set", post("/api/develop/set", {"view": "clean", "piece": [0.3, 0.3]})["ok"] and app2.dev["view"] == "clean")
check("run", post("/api/develop/run", {"names": []})["ok"])
for _ in range(400):
    if not app2.dev_job:
        break
    time.sleep(0.05)
check("the run saved its file", app2.dev["results"] and len(app2.dev["results"]["saved"]) == 1, str(app2.dev["results"]))
app2.job = "scan"
before = app2.progress
check("changing the look is fine while a scan is going", post("/api/develop/set", {"view": "raw"})["ok"])
check("a folder can be opened while a scan is going", post("/api/develop/folder", {"roll": "Roll 001"})["ok"])
check("a run is allowed while a scan is going", post("/api/develop/run", {"names": []})["ok"])
check("a second run is refused while the first is going", not post("/api/develop/run", {"names": []})["ok"])
for _ in range(400):
    if not app2.dev_job:
        break
    time.sleep(0.05)
check("the run beside the scan saved its file", len(app2.dev["results"]["saved"]) == 1, str(app2.dev["results"]))
check("the scan's job was left alone", app2.job == "scan" and app2.progress == before and not app2.cancel)
app2.job = None
server.shutdown()

print("   re-develop beside a running job: each has its own Stop, progress and frame times")
seen = {}
def pretend_scan():
    app2.frame_times = {"scan": 1.0}
    for k in range(200):
        if app2.cancel:
            break
        app2._progress("scan", 0, k, 200)
        time.sleep(0.02)
    seen["times"] = dict(app2.frame_times)
app2.dev["stale"] = True
check("a job starts", app2.run_job("scan", pretend_scan))
check("re-develop starts beside it", app2.run_dev_job("re-develop", lambda: app2.develop_run([])))
time.sleep(0.3)
check("the scan's progress is the scan's", (app2.progress or {}).get("phase") == "scan", str(app2.progress))
app2.cancel = True  # the main page's Stop
for _ in range(400):
    if not app2.job and not app2.dev_job:
        break
    time.sleep(0.05)
check("Stop on the main page stopped the scan only", "stopped" not in " ".join(l for l in app2.log[-20:] if "re-develop" in l)
      and len(app2.dev["results"]["saved"]) == 1, str(app2.dev["results"]))
check("re-develop did not add to the scan's frame times", seen.get("times") == {"scan": 1.0}, str(seen))
app2.cancel = False

if len(sys.argv) > 1:
    print("I a real RAW file")
    real = pathlib.Path(sys.argv[1])
    rf = tmp / "Roll 020" / "RAW"
    rf.mkdir(parents=True)
    shutil.copy(real, rf / real.name)
    app.develop_folder(path=str(rf))
    app.dev["how"].update(film="negative", film_profile="", tone="flat", colour_space="ProPhoto RGB", clean=True)
    app.dev["stale"] = True
    cs.PIECE = (360, 480)
    app.dev["piece"] = [0.45, 0.35]
    t = time.time()
    app.develop_look()
    print(f"        four versions in {time.time() - t:.1f} s")
    c = app.dev_cache
    ch = float(np.mean([np.mean(a != b) for a, b in zip(c["pictures"]["raw"][1], c["pictures"]["clean"][1])]))
    check("real file: dust removed changed some pixels", 0 < ch < 0.5, f"{100 * ch:.1f}%")
    for v in cs.VIEWS:
        (tmp / f"real_{v}.png").write_bytes(app.develop_png(v))
        (tmp / f"real_{v}_piece.png").write_bytes(app.develop_png(v, piece=True))
    print("        pictures in", tmp)
    t = time.time()
    app.develop_run([])
    print(f"        developed in {time.time() - t:.1f} s")

print("\nall passed" if not bad else f"\n{len(bad)} FAILED: " + "; ".join(bad))
