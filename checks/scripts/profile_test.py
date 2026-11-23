"""Film profiles: the colour of a film measured from the frames of a roll, and used for later frames."""
import sys, os, json, time, tempfile, pathlib, threading, urllib.request
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
import roll_sim as R
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
def spread(pos, where): v = [float(p[where].mean()) for p in pos]; return max(v) - min(v)

print("A the sums, on a made-up roll (orange mask, a different contrast per colour, each frame exposed on its own)")
rng = np.random.default_rng(5)
kinds = ["neutral"] * 8 + ["foliage", "sunset", "sky", "neutral"]
roll = []
for kind in kinds:
    sc, card = R.scene(kind, rng); planes, exposures = R.scan(sc, rng); roll.append((kind, planes, exposures, card))
measured = [cs.frame_colour(p, e) for _, p, e, _ in roll]
sc, card = R.scene("neutral", np.random.default_rng(9))
p1, e1 = R.scan(sc, np.random.default_rng(1)); p2, e2 = R.scan(sc, np.random.default_rng(1), {k: int(v * 0.6) for k, v in e1.items()})
m1, m2 = cs.frame_colour(p1, e1), cs.frame_colour(p2, e2)
check("a frame measures the same whatever it was exposed at",
      max(abs(a - b) for a, b in zip(m1["lo"] + m1["hi"], m2["lo"] + m2["hi"])) < 0.02, f"{m1} / {m2}")
check("no exposures, or not three colours: nothing is measured", cs.frame_colour(p1, None) is None
      and cs.frame_colour(p1[:1], e1) is None and cs.frame_colour(p1, {"red": 1, "green": 1}) is None)
prof = cs.film_profile_from(measured)
ranges = [prof["hi"][c] - prof["lo"][c] for c in range(3)]
check("the profile finds how much more contrast each colour has (within 5%)",
      all(abs(ranges[c] / ranges[1] - R.GAMMA[c] / R.GAMMA[1]) < 0.05 for c in range(3)),
      f"found {[round(r / ranges[1], 3) for r in ranges]}, the film has {[round(g / R.GAMMA[1], 3) for g in R.GAMMA]}")
check("...and the orange mask (the film base of each colour, within 0.05 density)",
      all(abs((prof["lo"][c] - prof["lo"][1]) - (R.BASE[c] - R.BASE[1])) < 0.05 for c in range(3)),
      f"found {[round(prof['lo'][c] - prof['lo'][1], 3) for c in range(3)]}, the film has {[round(R.BASE[c] - R.BASE[1], 3) for c in range(3)]}")
check("it says how many frames it rests on", prof["frames"] == 12)
check("fewer than three frames: no profile", cs.film_profile_from(measured[:2]) is None and cs.film_profile_from([]) is None)
check("frames with no range to speak of: no profile", cs.film_profile_from([{"lo": [1, 1, 1], "hi": [1.01, 1.01, 1.01]}] * 5) is None)
holed = [dict(m) for m in measured]; holed[3] = {"lo": [v - 2.0 for v in holed[3]["lo"]], "hi": holed[3]["hi"]}
p_holed = cs.film_profile_from(holed)
check("one frame with a hole in it (far too thin) does not move the profile",
      max(abs(a - b) for a, b in zip(p_holed["lo"], prof["lo"])) < 0.05, f"{p_holed['lo']} against {prof['lo']}")
for kind, planes, exposures, card in roll[8:11]:
    auto, with_p = spread(cs.invert_negative(planes, "flat"), card), spread(cs.invert_negative(planes, "flat", prof, exposures), card)
    check(f"a grey card in a frame full of one colour ({kind}): coloured on its own, grey with the profile",
          auto > 0.2 and with_p < 0.03, f"colours apart by {auto:.3f} on its own, {with_p:.3f} with the profile")
kind, planes, exposures, card = roll[0]
check("an ordinary frame stays as neutral as it was", spread(cs.invert_negative(planes, "flat", prof, exposures), card) < 0.03)
a = cs.invert_negative(p1, "flat", prof, e1); b = cs.invert_negative(p2, "flat", prof, e2)
check("the same frame exposed differently comes out the same", max(float(np.abs(x - y).mean()) for x, y in zip(a, b)) < 0.01)
plain = cs.invert_negative(p1, "flat")
check("a profile without the exposures of the pass is not used (the frame is converted on its own)",
      all(np.array_equal(x, y) for x, y in zip(plain, cs.invert_negative(p1, "flat", prof, None))))
check("a broken profile is not used either",
      all(np.array_equal(x, y) for x, y in zip(plain, cs.invert_negative(p1, "flat", {"lo": [1, 2], "hi": "x"}, e1))))
clipped = float(np.mean([((v <= 0) | (v >= 1)).mean() for v in a]))
check("the brightness is stretched without cutting a colour off at either end", clipped < 0.005, f"{clipped:.4%} of values at an end")
nik = cs.invert_negative(p1, "nikon", prof, e1)
check("the Nikon Scan tone still applies on top", float(np.abs(nik[0] - a[0]).mean()) > 0.01)

print("B measuring while scanning")
app, out = fresh()
check("nothing measured before anything is scanned", app.state()["roll_measured"] == 0 and app.state()["film_profiles"] == [])
app.preview([0])
check("a preview scan does not count", app.state()["roll_measured"] == 0)
app.scan([0, 1])
colour = json.loads((out / "Roll 001" / "FilmColour.json").read_text())
check("each scanned frame is measured and kept with its roll", app.state()["roll_measured"] == 2 and len(colour["frames"]) == 2
      and all(len(v["lo"]) == 3 and len(v["hi"]) == 3 and v["exposures"] for v in colour["frames"].values()), str(list(colour["frames"])))
r = app.save_film_profile("Kodak Gold 200")
check("two frames are too few for a profile, and the app says how many it has", r["ok"] is False and "2 measured" in r["error"], r.get("error", ""))
check("no name: refused", app.save_film_profile("  ")["ok"] is False)
app.scan([2, 3, 4, 5])
r = app.save_film_profile("  Kodak   Gold 200 ")
st = app.state()
check("after a strip the roll's film can be saved under a name", r == {"ok": True, "name": "Kodak Gold 200", "frames": 6}
      and [p["name"] for p in st["film_profiles"]] == ["Kodak Gold 200"] and st["film_profiles"][0]["roll"] == "Roll 001"
      and st["film_profiles"][0]["made"] == time.strftime("%Y-%m-%d"), str(st["film_profiles"]))
check("saving it does not switch it on", app.settings["film_profile"] == "")
a2 = cs.App(demo=True); a2.load_settings(); a2.settings["outdir"] = str(out)
check("the profile and the roll's measurements are still there after a restart",
      "Kodak Gold 200" in a2.settings["film_profiles"] and a2.state()["roll_measured"] == 6)

print("C using a profile")
app.preview([0]); prev_before = app.previews[0]
check("a name that is not there: refused", app.use_film_profile("Nope")["ok"] is False and app.settings["film_profile"] == "")
r = app.use_film_profile("Kodak Gold 200")
check("picking it switches it on and says so", r["ok"] and app.settings["film_profile"] == "Kodak Gold 200"
      and any('film profile "Kodak Gold 200"' in l for l in logged(app, "colour of negatives")))
check("the preview scan on screen is drawn again with the film's colour",
      app.previews[0] != prev_before and bool(app.frame_exposures.get(0)))
app.thumbnail(); thumb = app.previews[1]
app.use_film_profile(""); thumb_off = app.previews[1]
app.use_film_profile("Kodak Gold 200")
check("thumbnails look the same with and without a profile (the thumbnail pass has no exposures)", thumb == thumb_off == app.previews[1])
n0 = len(app.log)
app.scan([0])
line = [l[9:] for l in app.log[n0:] if "saved " in l]
check("a scan says which film profile coloured it", line and 'film profile "Kodak Gold 200"' in line[0], str(line))
files = sorted((out / "Roll 001").glob("*.tif"))
with_p = tifffile.imread(files[-1])
app.use_film_profile("")
app.scan([0]); files = sorted((out / "Roll 001").glob("*.tif"))
without = tifffile.imread(files[-1])
check("...and the saved picture differs from the same frame converted on its own", with_p.shape == without.shape
      and float(np.abs(with_p.astype(np.float64) - without).mean()) > 50, f"{float(np.abs(with_p.astype(np.float64) - without).mean()):.0f} levels apart on average")
app.use_film_profile("Kodak Gold 200")
app.settings["film"] = "mono"
check("black-and-white film: the profile is left out", app._film_profile() is None)
m0 = app.state()["roll_measured"]; app.scan([1])
check("...and nothing is measured", app.state()["roll_measured"] == m0)
app.settings["film"] = "positive"
check("slides: the profile is left out", app._film_profile() is None)
app.settings["film"] = "negative"
app.settings.update(scan_log=True, archive=True)
app.scan([2])
log_text = (out / "Roll 001" / "ScanLog.txt").read_text()
check("the scan log names the profile, and Archive mode uses it for the Positive",
      'film profile "Kodak Gold 200"' in log_text and any('Positive/' in l and 'film profile "Kodak Gold 200"' in l for l in logged(app, "saved ")))
app.settings.update(scan_log=False, archive=False)

print("D rolls, saved settings and deleting")
app.save_preset("Gold, full")
check("saved settings keep the film profile", app.settings["presets"]["Gold, full"]["film_profile"] == "Kodak Gold 200")
app.new_roll({"film_brand": "Fuji", "film_stock": "Superia"})
check("a new roll switches the profile off (it may be another film) and says so",
      app.settings["film_profile"] == "" and any("switched off for the new roll" in l for l in app.log))
check("the new roll starts with nothing measured", app.state()["roll_measured"] == 0)
app.switch_roll("Roll 001")
check("going back to the earlier roll brings its profile and its measurements back",
      app.settings["film_profile"] == "Kodak Gold 200" and app.state()["roll_measured"] >= 6)
app.thumbnail(); app.preview([0]); shown = app.previews[0]
r = app.delete_film_profile("Kodak Gold 200")
check("deleting the profile in use goes back to each frame on its own, and draws the pictures again",
      r["ok"] and app.settings["film_profile"] == "" and app.state()["film_profiles"] == [] and app.previews[0] != shown)
check("deleting one that is not there: said, nothing breaks", app.delete_film_profile("Nope")["ok"] is False)
r = app.load_preset("Gold, full")
check("saved settings that name a deleted profile load without it", r["ok"] and app.settings["film_profile"] == "")
app.switch_roll("Roll 002 - Fuji Superia"); app.switch_roll("Roll 001")
check("...and so does a roll that had it", app.settings["film_profile"] == "")
(out / "Roll 001" / "FilmColour.json").write_text("{ this is not json")
a3 = cs.App(demo=True); a3.settings.update(outdir=str(out), roll="Roll 001")
check("a damaged measurements file: counted as nothing, no crash", a3.state()["roll_measured"] == 0)
a3.settings.update(dpi=145, ir=False, clean=False, scan_log=False); a3.connect(); a3.thumbnail(); a3.scan([0])
check("...and the next scan starts it afresh", a3.state()["roll_measured"] == 1 and "frames" in json.loads((out / "Roll 001" / "FilmColour.json").read_text()))
real_replace = os.replace
def no_replace(a, b):
    if str(b).endswith("FilmColour.json"): raise PermissionError(13, "Permission denied")
    return real_replace(a, b)
os.replace = no_replace
try:
    n0 = len(a3.log); a3.scan([1, 2])
finally:
    os.replace = real_replace
warned = [l for l in a3.log[n0:] if "could not keep this frame's film colour" in l]
check("measurements that cannot be written: said once, and both frames are still saved",
      len(warned) == 1 and len([l for l in a3.log[n0:] if "saved " in l]) == 2, str(len(warned)))

print("E from the page")
import http.server
app, out = fresh()
app.scan([0, 1, 2])
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cs.make_handler(app))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
def post(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r: return json.loads(r.read())
r = post("/api/film_profile", {"action": "save", "name": "Portra 400"})
check("saved from the page", r.get("ok") and r.get("frames") == 3 and "Portra 400" in app.settings["film_profiles"])
check("switched on from the page", post("/api/film_profile", {"action": "use", "name": "Portra 400"}).get("ok") and app.settings["film_profile"] == "Portra 400")
post("/api/settings", {"film_profile": "", "film_profiles": {}})
check("the ordinary settings address cannot change or wipe profiles", app.settings["film_profile"] == "Portra 400" and "Portra 400" in app.settings["film_profiles"])
state = json.loads(urllib.request.urlopen(base + "/api/state").read())
check("the page is told the profiles and how many frames this roll has measured",
      state["film_profiles"][0]["name"] == "Portra 400" and state["roll_measured"] == 3 and state["profile_least"] == 3)
check("deleted from the page", post("/api/film_profile", {"action": "delete", "name": "Portra 400"}).get("ok") and app.settings["film_profiles"] == {})
server.shutdown()

print()
print("all passed" if not bad else f"{len(bad)} FAILED: {bad}")
sys.exit(1 if bad else 0)
