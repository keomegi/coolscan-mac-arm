"""The roll's colour of negatives (FilmProfile.json in the roll folder): set in the Library or on the scanning
screen, shown and exported in the Library without changing the files, the film base picked on a RAW file,
and later scans of the roll using it."""
import sys, json, tempfile, pathlib, datetime, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cs_next as cs
from coolscan_app.imaging.files import write_tiff, read_tiff16
from coolscan_app.imaging.colour import invert_negative, convert_planes, frame_colour, icc_profile
from coolscan_app.imaging import filmfix

tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
bad = []


def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        bad.append(name)


rng = np.random.default_rng(3)
EXP = {"red": 900, "green": 1300, "blue": 2700}
MASK = [0.30, 0.62, 1.05]   # a yellowish-orange mask, denser than usual in blue (Orwocolor-like)


def negative(rows=320, cols=480, seed=0, cast=(0, 0, 0)):
    """A frame as the scanner reads it: clear film (base only) around the picture, a picture inside."""
    g = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols] / np.array([rows, cols])[:, None, None]
    out = []
    for c, k in enumerate(("red", "green", "blue")):
        scene = 0.15 + 0.7 * (0.5 + 0.5 * np.sin(3 * xx + 2 * yy + c + seed)) + cast[c]
        d = MASK[c] + 0.65 * (np.log10(np.clip(scene, 0.03, 1)) + 1.6)
        d[:, :30] = MASK[c]          # clear film along the left edge
        d[:, -30:] = MASK[c]
        d += g.normal(0, 0.004, d.shape)
        t = 10 ** (-d) * EXP[k] / 1000
        out.append(np.clip(t * 60000, 1, 65535).astype(np.uint16))
    return out


CROP = [0.08, 0.0, 0.92, 1.0]     # the crop asked for when scanning: the picture without the clear film


def positive_of(planes, how_tone="flat", space="ProPhoto RGB"):
    rows, cols = planes[0].shape
    c0, c1 = int(round(CROP[0] * cols)), int(round(CROP[2] * cols))
    cut = [p[:, c0:c1] for p in planes]
    pos = invert_negative(cut, how_tone)
    return convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in pos], space)


base = tmp / "Scans"
roll = base / "Roll 007 - Orwocolor"
(roll / "RAW").mkdir(parents=True)
(roll / "Positive").mkdir()
frames = {}
for n in range(1, 5):
    stem = f"20261125_{n:03d}_R007"
    neg = negative(seed=n, cast=(0.2, 0, -0.1) if n == 2 else (0, 0, 0))
    note = {"film": "negative", "dpi": 2900, "exposures": EXP, "turns": 0, "crop": CROP, "roll": roll.name}
    write_tiff(roll / "RAW" / f"{stem}.tif", neg + [np.full_like(neg[0], 60000)], 2900, None,
               meta={"coolscan": note, "description": "Film: Orwocolor"})
    pos, icc = positive_of(neg)
    write_tiff(roll / "Positive" / f"{stem}.tif", pos, 2900, icc, meta={"description": "Film: Orwocolor"})
    frames[stem] = {**frame_colour(neg, EXP), "exposures": EXP}
(roll / "FilmColour.json").write_text(json.dumps({"frames": frames}))
# a picture with no RAW file
old = base / "Roll 006"
old.mkdir()
write_tiff(old / "x.tif", positive_of(negative(seed=9))[0], 2900, icc_profile("ProPhoto RGB"))

app = cs.App(demo=True)
app.settings.update(outdir=str(base), roll=roll.name, use_roll=True, film="negative")
key, rel = roll.name, "Positive/20261125_001_R007.tif"
pos_file = roll / rel
before = pos_file.read_bytes()

print("A no file: pictures as saved")
r = app.library_roll(key)
check("roll says each frame on its own, 4 measured", r["film_colour"]["mode"] == "frame" and r["film_colour"]["measured"] == 4, str(r["film_colour"]))
check("no colour stamp on pictures", not any(p.get("fc") for p in r["pictures"]))
check("scanning screen: each frame on its own", app._film_profile() is None and app.roll_colour_state()["mode"] == "frame")
png_saved = app.library_png(key, rel, big=True)

print("B this roll's own frames")
res = app.library_film_colour(key, "roll")
check("set", res.get("ok"), str(res))
d = json.loads((roll / filmfix.ROLL_FILE).read_text())
check("FilmProfile.json in the roll folder says roll", d["mode"] == "roll" and len(d["lo"]) == 3, str(d))
r = app.library_roll(key)
check("pictures with a RAW file get a colour stamp", all(p.get("fc") for p in r["pictures"]), str([p.get("fc") for p in r["pictures"]]))
info = app.library_info(key, rel)
check("the picture is shown with it", info["film_colour"]["applied"] and info["film_colour"]["tone"] == "flat", str(info["film_colour"]))
png_roll = app.library_png(key, rel, big=True)
check("the picture on show changes", png_roll != png_saved)
check("Show original is as saved", app.library_png(key, rel, big=True, view="original") == png_saved)
check("the file itself is untouched", pos_file.read_bytes() == before)
check("scanning does not put the roll's numbers on by itself", app._film_profile() is None and filmfix.roll_numbers(roll) is not None, str(app._film_profile()))
check("scanning screen shows it", app.roll_colour_state()["mode"] == "roll")

# what the Library shows is what scanning with the roll's numbers would have made
neg = read_tiff16(roll / "RAW" / "20261125_001_R007.tif")[:3]
nums = filmfix.roll_numbers(roll)
cols = neg[0].shape[1]
cut = [p[:, int(round(CROP[0] * cols)):int(round(CROP[2] * cols))] for p in neg]
want = convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(cut, "flat", nums, EXP)], "ProPhoto RGB")[0]
fix, why = app._lib_fix(roll, pos_file, rel)
got = filmfix.recolour([p / 65535 for p in read_tiff16(pos_file)], fix["m"]["space"], fix["m"], fix["numbers"])
err = max(float(np.median(np.abs(g - w / 65535))) for g, w in zip(got, want))
err99 = max(float(np.percentile(np.abs(g - w / 65535), 99)) for g, w in zip(got, want))
check("recoloured picture matches a scan made with the roll's numbers", err < 0.002 and err99 < 0.01, f"median {err:.4f}, p99 {err99:.4f}")

print("C export puts it in, picture untouched")
ex = app.library_export(key, [rel])
check("exported a copy", ex["ok"] and len(ex["saved"]) == 1, str(ex))
cp = read_tiff16(roll / ex["saved"][0])
check("the copy has the new colour", max(float(np.abs(c.astype(float) - w.astype(float)).mean()) for c, w in zip(cp, want)) < 200)
check("the file itself is still untouched", pos_file.read_bytes() == before)

print("D film base picked on the RAW file")
neg_png = app.library_png(key, rel, big=True, view="negative")
check("the frame as scanned can be shown", neg_png[:4] == b"\x89PNG")
bad_spot = app.library_film_base(key, rel, 0.5, 0.5)
check("a spot in the picture is refused or warned about", (not bad_spot.get("ok")) or bool(bad_spot.get("warn")), str(bad_spot))
res = app.library_film_base(key, rel, 0.03, 0.5)
check("clear film edge accepted", res.get("ok") and not res.get("warn"), str(res))
check("base measured right", res.get("ok") and max(abs(b - (m + 3 - np.log10(60000 / 65535))) for b, m in zip(res["base"], MASK)) < 0.02, str(res.get("base")))
d = json.loads((roll / filmfix.ROLL_FILE).read_text())
check("FilmProfile.json says base", d["mode"] == "base" and d["from"] == rel)
check("Library state offers it", res["film_colour"]["mode"] == "base" and res["film_colour"]["has_base"])
check("scanning is told of the base, and does not use it by itself", app._film_profile() is None and app.roll_colour_state()["mode"] == "base")
check("roll's 'each frame' then back to base keeps the base", app.library_film_colour(key, "frame")["ok"] and app.library_film_colour(key, "base")["ok"]
      and filmfix.read_roll(roll)["lo"] == d["lo"])

print("E film profile from the scanning screen is kept with the roll")
app.settings["film_profiles"]["Orwo test"] = {"lo": [3.3, 3.8, 4.5], "hi": [4.6, 5.1, 5.9], "frames": 9}
res = app.use_film_profile("Orwo test")
d = json.loads((roll / filmfix.ROLL_FILE).read_text())
check("picked on the scanning screen -> roll file", res.get("ok") and d["mode"] == "profile" and d["name"] == "Orwo test" and d["lo"] == [3.3, 3.8, 4.5], str(d))
check("settings follow", app.settings["film_profile"] == "Orwo test")
app.delete_film_profile("Orwo test")
check("the roll keeps the numbers after the profile is deleted", filmfix.roll_numbers(roll) == {"lo": [3.3, 3.8, 4.5], "hi": [4.6, 5.1, 5.9]})
check("Library offers the kept one", app.library_roll(key)["film_colour"]["name"] == "Orwo test")
res = app.use_film_profile("@roll")
check("@roll from the scanning screen", res.get("ok") and filmfix.read_roll(roll)["mode"] == "roll")
res = app.use_film_profile("")
check("each frame from the scanning screen", res.get("ok") and filmfix.read_roll(roll)["mode"] == "frame" and app._film_profile() is None)

print("F pictures it cannot be put on")
app.library_film_colour(key, "roll")
fix, why = app._lib_fix(roll, roll / "RAW" / "20261125_001_R007.tif", "RAW/20261125_001_R007.tif")
check("RAW files are shown as they are", fix is None and why)
fix, why = app._lib_fix(old, old / "x.tif", "x.tif")
check("a folder without the file: nothing", fix is None and why == "")
(old / filmfix.ROLL_FILE).write_text(json.dumps({"mode": "profile", "lo": [3.3, 3.8, 4.5], "hi": [4.6, 5.1, 5.9]}))
fix, why = app._lib_fix(old, old / "x.tif", "x.tif")
check("a picture with no RAW file says why", fix is None and "RAW" in why, why)
check("and is shown as saved", app.library_png("Roll 006", "x.tif", big=True)[:4] == b"\x89PNG")
# a picture made with grain reduction does not match its RAW file
stem = "20261125_003_R007"
p3 = read_tiff16(roll / "Positive" / f"{stem}.tif")
noisy = [np.clip(p.astype(np.int32) + (rng.normal(0, 1, p.shape) * 4000).astype(np.int32), 0, 65535).astype(np.uint16)[:, ::-1] for p in p3]
write_tiff(roll / "Positive" / f"{stem}.tif", noisy, 2900, icc_profile("ProPhoto RGB"))
fix, why = app._lib_fix(roll, roll / "Positive" / f"{stem}.tif", f"Positive/{stem}.tif")
check("a picture that does not match its RAW file is left as saved (or still matches)", fix is None or fix["m"]["err"] < 0.03, why)

print("G switching rolls brings it back")
other = base / "Roll 008"
other.mkdir()
app.switch_roll("Roll 008")
check("another roll: each frame on its own", app._film_profile() is None)
app.switch_roll(roll.name)
check("back: the roll's own colour again", filmfix.roll_numbers(roll) is not None and app.roll_colour_state()["mode"] == "roll")

print("I one picture on its own colour")
app.library_film_colour(key, "roll")
rel2 = "Positive/20261125_002_R007.tif"
shown_roll = app.library_png(key, rel2, big=True)
saved2 = app.library_png(key, rel2, big=True, view="original")
check("with the roll's colour it differs from as saved", shown_roll != saved2)
res = app.library_edit(key, [rel2], {"colour": "own"})
check("own colour set on one picture", res.get("ok") and json.loads((roll / "Library.json").read_text())["pictures"][rel2]["colour"] == "own", str(res))
info = app.library_info(key, rel2)
check("it is shown as saved again (it was saved on its own colour)", info["film_colour"]["own"] and not info["film_colour"]["applied"]
      and app.library_png(key, rel2, big=True) == saved2, str(info["film_colour"]))
check("the others keep the roll's colour", app.library_info(key, "Positive/20261125_004_R007.tif")["film_colour"]["applied"])
check("the roll lists it as own", next(p for p in app.library_roll(key)["pictures"] if p["file"] == rel2)["own_colour"])
# a picture that was SAVED with the roll's colour, then set to own: put back to its own colour
neg4 = read_tiff16(roll / "RAW" / "20261125_004_R007.tif")[:3]
cols = neg4[0].shape[1]
cut4 = [p[:, int(round(CROP[0] * cols)):int(round(CROP[2] * cols))] for p in neg4]
nums = filmfix.roll_numbers(roll)
with_roll = convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(cut4, "flat", nums, EXP)], "ProPhoto RGB")
rel4 = "Positive/20261125_004_R007.tif"
write_tiff(roll / rel4, with_roll[0], 2900, with_roll[1])
fix, why = app._lib_fix(roll, roll / rel4, rel4)
check("saved with the roll's colour already: nothing to put on", fix is None and "already" in why, why)
app.library_edit(key, [rel4], {"colour": "own"})
fix, why = app._lib_fix(roll, roll / rel4, rel4)
want_own = convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(cut4, "flat")], "ProPhoto RGB")[0]
got = filmfix.recolour([p / 65535 for p in with_roll[0]], fix["m"]["space"], fix["m"], fix["numbers"]) if fix else None
err = max(float(np.median(np.abs(g - w / 65535))) for g, w in zip(got, want_own)) if got else 9
check("set to own: shown as each frame on its own would have made it", fix is not None and fix["numbers"] is None and err < 0.003, f"{why} median {err:.4f}")
app.library_edit(key, [rel2, rel4], {"colour": "roll"})
check("back to the roll's colour", app.library_png(key, rel2, big=True) == shown_roll)

print("J another film scanned into the roll by mistake")
app.switch_roll("Roll 008"); app.switch_roll(roll.name)
app.library_film_colour(key, "roll")
before_n = len(filmfix.measured_frames(roll))
app.strip_id += 1


def meas(shift=(0, 0, 0), seed=0):
    g = np.random.default_rng(seed)
    lo = [3.34 + shift[0] + g.normal(0, 0.01), 3.66 + shift[1] + g.normal(0, 0.01), 4.09 + shift[2] + g.normal(0, 0.01)]
    return {"lo": [round(v, 4) for v in lo], "hi": [round(v + 1.1, 4) for v in lo]}


app._note_colour("s2_001", meas(seed=1), EXP)
check("the same film on a new strip: no warning", app.film_warning_state() is None)
app.strip_id += 1
app._note_colour("s3_001", meas((0, 0.02, -0.38), seed=2), EXP)   # an orange-masked film after the yellow one
w = app.film_warning_state()
check("another film: the screen is told", w is not None and w["frames"] == 1, str(w))
check("...the log says so", any("looks like another film" in l for l in app.log[-4:]))
check("...the strip is coloured each frame on its own meanwhile", app._film_profile() is None)
fc = json.loads((roll / "FilmColour.json").read_text())["frames"]
check("...its frame is marked own and kept out of the roll's measurements", fc["s3_001"].get("own") is True and len(filmfix.measured_frames(roll)) == before_n + 1)
app._note_colour("s3_002", meas((0, 0.02, -0.38), seed=3), EXP)
check("the next frame of the strip too", json.loads((roll / "FilmColour.json").read_text())["frames"]["s3_002"].get("own") is True)
check("state() carries the warning", app.state().get("film_warning") is not None)
res = app.film_warning_answer("own")
check("answered own: bar gone, strip stays on its own", res["ok"] and app.film_warning_state() is None and app._film_profile() is None)
res = app.film_warning_answer("same")
fc = json.loads((roll / "FilmColour.json").read_text())["frames"]
check("answered same film: marks taken off, the roll's colour again", res["ok"] and not fc["s3_001"].get("own") and not fc["s3_002"].get("own"))
app._note_colour("s3_003", meas((0, 0.02, -0.38), seed=4), EXP)
check("...and not asked again for this strip", app.film_warning_state() is None)
app.strip_id += 1
app._note_colour("s4_001", meas((0, 0.3, 0.5), seed=5), EXP)
check("a third film on the next strip is caught again", app.film_warning_state() is not None)
app.new_roll({"film_brand": "Kodak"})
check("a new roll clears it", app.film_warning_state() is None)
app.switch_roll(roll.name)

print("H routes")
import threading, urllib.request
from http.server import ThreadingHTTPServer
httpd = ThreadingHTTPServer(("127.0.0.1", 0), cs.server.make_handler(app))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]


def post(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


try:
    r = post("/api/library/film_colour", {"key": key, "mode": "frame"})
    check("POST film_colour", r.get("ok") and filmfix.read_roll(roll)["mode"] == "frame", str(r))
    r = post("/api/library/film_base", {"key": key, "file": rel, "x": 0.97, "y": 0.4})
    check("POST film_base", r.get("ok") and filmfix.read_roll(roll)["mode"] == "base", str(r))
except Exception as e:  # noqa: BLE001
    check("routes answer", False, repr(e))
httpd.shutdown()

print("K the roll's colour curve (a film whose colours do not run alike), scratched frames")
from coolscan_app.imaging.colour import film_curve, film_profile_from


def crossed(seed, bright=1.0, rows=300, cols=450):
    """A frame of a film whose blue layer gains density twice as fast in its thin parts as red and green
    (old film): a grey scene of many brightnesses, clear film along one edge."""
    g = np.random.default_rng(seed)
    grey = np.clip(g.uniform(0.02, 1.0, (rows // 10, cols // 10)).repeat(10, 0).repeat(10, 1) * bright, 0.004, 1)
    e = np.log10(grey) + 2.4                      # exposure above the film's threshold
    out = []
    for c, k in enumerate(("red", "green", "blue")):
        d = MASK[c] + (0.55 * e if c < 2 else 0.55 * e + 0.5 * (1 - np.exp(-2.5 * e)))
        d[:, :24] = MASK[c]
        d = d + g.normal(0, 0.003, d.shape)
        out.append(np.clip(10 ** (-d) * EXP[k] / 1000 * 60000, 1, 65535).astype(np.uint16))
    return out


def tint(planes01):
    """How far from grey a picture of a grey scene is: the mean gap between its colours, in the picture area."""
    r, g_, b = [p[20:-20, 40:-20] for p in planes01]
    return float(np.mean(np.abs(b - r)) + np.mean(np.abs(g_ - r)))


negs = [crossed(n, bright) for n, bright in enumerate((1.0, 0.9, 0.5, 0.12, 0.05, 1.0))]
meas = [frame_colour(n_, EXP) for n_ in negs]
check("frames are measured with how much film there is at each density", all(len(m_["q"]) == 3 and len(m_["q"][0]) == 100 for m_ in meas))
prof = film_profile_from(meas)
check("the roll's profile carries a curve", prof is not None and "curve" in prof and len(prof["curve"]["x"]) == 3)
plain = {"lo": prof["lo"], "hi": prof["hi"]}
thin = negs[3]
t_plain = tint(invert_negative(thin, "flat", plain, EXP))
t_curve = tint(invert_negative(thin, "flat", prof, EXP))
t_own = tint(invert_negative(thin, "flat"))
check("a thin frame: no colour cast with the curve, a strong one with two numbers per colour",
      t_curve < 0.03 and t_plain > 3 * t_curve, f"curve {t_curve:.3f}, numbers only {t_plain:.3f}, on its own {t_own:.3f}")
t_all = [tint(invert_negative(n_, "flat", prof, EXP)) for n_ in negs]
check("every frame of the roll comes out grey", max(t_all) < 0.04, str([round(t, 3) for t in t_all]))
basenum = filmfix._numbers(MASK_D := [m_ + np.log10(EXP[k]) for m_, k in zip(MASK, ("red", "green", "blue"))], prof["hi"],
                           film_curve(meas, MASK_D))
check("the same from a picked film base", basenum is not None and "curve" in basenum
      and tint(invert_negative(thin, "flat", basenum, EXP)) < 0.03)
# a scratched, thin frame: the saved picture had its scratches mended, the RAW file has them
scr = [p.copy() for p in thin]
for r0 in range(30, 280, 9):
    for p in scr:
        p[r0:r0 + 2, 30:] = (p[r0:r0 + 2, 30:] * 0.25).astype(np.uint16)   # dense lines: a fifth of the frame
saved = convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(thin, "flat")], "ProPhoto RGB")[0]
m_s = filmfix.match([p / 65535 for p in saved], "ProPhoto RGB", filmfix.density([p / 65535 for p in scr], EXP))
check("a scratched frame still matches its RAW file", m_s is not None and not m_s.get("bad"), str(m_s and (m_s.get("err"), m_s.get("how"))))
got = filmfix.recolour([p / 65535 for p in saved], "ProPhoto RGB", m_s, prof) if m_s and not m_s.get("bad") else None
want = convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(thin, "flat", prof, EXP)], "ProPhoto RGB")[0]
err = max(float(np.median(np.abs(g_ - w / 65535))) for g_, w in zip(got, want)) if got else 9
check("...and is recoloured as a scan with the roll's curve would be", err < 0.006, f"median {err:.4f}")
# a picture saved with the curve: shown as it is, and can be put back on its own colour
m_c = filmfix.match([w / 65535 for w in want], "ProPhoto RGB", filmfix.density([p / 65535 for p in thin], EXP))
check("a picture saved with the curve is followed along its own line", m_c is not None and not m_c.get("bad") and filmfix.same_already(m_c, prof),
      str(m_c and (m_c.get("err"), m_c.get("how"))))
back = filmfix.recolour([w / 65535 for w in want], "ProPhoto RGB", m_c, None) if m_c and not m_c.get("bad") else None
err = max(float(np.median(np.abs(g_ - w / 65535))) for g_, w in zip(back, saved)) if back else 9
check("...and can be put back to its own colour", err < 0.006, f"median {err:.4f}")
# a roll measured before the app kept "q": the Library looks at its RAW files once
fc_file = roll / "FilmColour.json"
data = json.loads(fc_file.read_text())
for f_ in data["frames"].values():
    f_.pop("q", None)
fc_file.write_text(json.dumps(data))
app.__dict__.pop("lib_curve_tried", None)
check("an older roll has no curve at first", "curve" not in (filmfix.roll_numbers(roll) or {}))
nums_k = app._lib_numbers(roll)
again = json.loads(fc_file.read_text())["frames"]
check("the Library measures its RAW files once and the roll gets its curve", nums_k is not None and "curve" in nums_k
      and sum(1 for f_ in again.values() if f_.get("q")) >= 3, str(sorted(k_ for k_, f_ in again.items() if f_.get("q"))))

print("L film base kept, then used on one picture or the whole roll")
(roll / filmfix.ROLL_FILE).unlink(missing_ok=True)
(roll / "Library.json").unlink(missing_ok=True)
app.__dict__.pop("lib_match", None)
rel2, rel3 = "Positive/20261125_002_R007.tif", "Positive/20261125_003_R007.tif"
res = app.library_film_use(key, "pictures", [rel])
check("nothing picked yet: refused, with what to do", not res.get("ok") and "Film base" in res.get("error", ""), str(res))
saved1 = app.library_png(key, rel, big=True)
res = app.library_film_base(key, rel, 0.03, 0.5, use="")
d = filmfix.read_roll(roll)
check("picking only keeps the film base: nothing is put on", res.get("ok") and d["mode"] == "frame" and d.get("base") and d.get("base_lo")
      and app.library_png(key, rel, big=True) == saved1 and not any(p.get("film") for p in app.library_roll(key)["pictures"]), str(d))
check("the page is told a base is kept", res["film_colour"]["has_base"] and res["film_colour"]["mode"] == "frame")
res = app.library_film_use(key, "pictures", [rel], base=True)
r = app.library_roll(key)
on = {p["file"]: bool(p.get("film")) for p in r["pictures"]}
check("this picture only: on for it, the others as saved", res.get("ok") and on[rel] and not on[rel2] and not on[rel3]
      and filmfix.read_roll(roll)["scope"] == "pictures" and filmfix.read_roll(roll)["mode"] == "base", str(on))
check("...the count for the page", r["film_colour"]["used"] == 1 and r["film_colour"]["of"] == 4 and r["film_colour"]["scope"] == "pictures", str(r["film_colour"]))
i1, i2 = app.library_info(key, rel)["film_colour"], app.library_info(key, rel2)["film_colour"]
check("...the picture is shown with it, another one is not", i1["on"] and i1["applied"] and not i2["on"] and not i2["applied"]
      and app.library_png(key, rel, big=True) != saved1, str((i1, i2)))
saved2 = app.library_png(key, rel2, big=True, view="original")
check("...the other picture looks as saved", app.library_png(key, rel2, big=True) == saved2)
res = app.library_film_use(key, "pictures", [rel2])
check("a second picture switched on", res.get("ok") and app.library_roll(key)["film_colour"]["used"] == 2)
app.library_edit(key, [rel2], {"colour": None})
check("...and off again", app.library_roll(key)["film_colour"]["used"] == 1 and not app.library_info(key, rel2)["film_colour"]["on"])
res = app.library_film_base(key, rel, 0.04, 0.5, use="")
check("picking again keeps it on the same pictures", res.get("ok") and filmfix.read_roll(roll)["scope"] == "pictures" and app.library_roll(key)["film_colour"]["used"] == 1)
res = app.library_film_use(key, "roll", base=True)
r = app.library_roll(key)
check("whole roll: every picture with a RAW file", res.get("ok") and r["film_colour"]["used"] == 4 and r["film_colour"]["scope"] == "roll"
      and "scope" not in filmfix.read_roll(roll) and all(p.get("film") for p in r["pictures"]), str(r["film_colour"]))
app.library_edit(key, [rel3], {"colour": "own"})
check("one picture switched off in a whole roll", app.library_roll(key)["film_colour"]["used"] == 3 and not app.library_info(key, rel3)["film_colour"]["on"])
res = app.library_film_use(key, "pictures", [rel3])
check("...and on again", res.get("ok") and app.library_roll(key)["film_colour"]["used"] == 4 and filmfix.read_roll(roll).get("scope") != "pictures")
res = app.library_film_colour(key, "frame")
r = app.library_roll(key)
check("off for the whole roll: no picture has it, the base is kept", res.get("ok") and r["film_colour"]["used"] == 0 and r["film_colour"]["has_base"]
      and app.library_png(key, rel, big=True) == saved1)
res = app.library_film_use(key, "pictures", [rel2], base=True)
r = app.library_roll(key)
check("one picture after that: only it (marks from before do not count)", res.get("ok") and r["film_colour"]["used"] == 1
      and [p["file"] for p in r["pictures"] if p.get("film")] == [rel2], str([p["file"] for p in r["pictures"] if p.get("film")]))
check("no picture file was changed", pos_file.read_bytes() == before)

print()
print("all good" if not bad else f"{len(bad)} FAILED: {bad}")
sys.exit(1 if bad else 0)
