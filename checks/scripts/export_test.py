"""The Library's Export dialog (exporting.py): where the copies go and what they are called, the kinds of
file (TIFF 16/8-bit, PNG, DNG; JPEG and HEIC through a stand-in for the Mac's converter), sizes, colour
space, metadata levels, presets, the background job and its routes. The pictures themselves never change."""
import sys, os, json, struct, stat, tempfile, pathlib, datetime, threading, time, zlib, hashlib, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["HOME"] = str(tmp / "home")       # the presets file lives in the home folder: a throwaway one
(tmp / "home").mkdir()
import numpy as np
import tifffile
import cs_next as cs
from http.server import ThreadingHTTPServer
from coolscan_app.imaging.files import write_tiff, read_tiff16, read_tiff_info
from coolscan_app.imaging.colour import icc_profile
from coolscan_app.imaging import tiffmeta
from coolscan_app import exporting
from coolscan_app.library import icc_name

cs.SETTINGS_FILE = tmp / "settings.json"
bad = []


def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        bad.append(name)


rng = np.random.default_rng(3)


def picture(path, taken, rows=300, cols=450, grey=False):
    if grey:   # the same in all three channels: stays grey in any colour space
        g = (np.linspace(2000, 60000, cols)[None, :] * np.ones((rows, 1))).astype(np.uint16)
        planes = [g, g.copy(), g.copy()]
    else:
        planes = [(rng.random((rows, cols)) * 60000).astype(np.uint16) for _ in range(3)]
    meta = {"taken": taken, "description": "Film: test", "make": "Nikon", "model": "Nikon COOLSCAN IV ED", "iso": 200}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tiff(path, planes, 2900, icc_profile("ProPhoto RGB"), meta=meta)
    return planes


base = tmp / "Scans"
K = "Roll 001 - Fuji"
A, B, C = "20261101_001_R001.tif", "20261101_002_R001.tif", "20261101_003_R001.tif"
pa = picture(base / K / A, datetime.datetime(2004, 3, 5, 10, 0, 0))
pb = picture(base / K / B, datetime.datetime(2004, 3, 5, 10, 0, 7))
pc = picture(base / K / C, datetime.datetime(2004, 3, 5, 10, 0, 9), grey=True)
K2, D = "Roll 002", "Positive/20261102_001_R002.tif"
picture(base / K2 / D, datetime.datetime(1999, 1, 1, 9, 0, 0))
before = {p: hashlib.md5(p.read_bytes()).hexdigest() for p in base.rglob("*.tif")}

app = cs.App(demo=True)
app.settings.update(outdir=str(base), roll=K, use_roll=True)
app.library_edit(K, [A], {"caption": "Play & party", "add_people": ["Ilze Ozola"], "add_tags": ["school"], "stars": 4})
app.library_edit(K, [A], {"edit": {"turns": 1, "crop": [0.1, 0.2, 0.6, 0.8]}})
sums = {p: hashlib.md5(p.read_bytes()).hexdigest() for p in base.rglob("*.tif")}   # (marks are written into the files)
out = tmp / "Out"


def run(files, key=K, items=None, **o):
    o.setdefault("dest", str(out))
    return app.library_export_run(key, files, o, items)


def xmp_of(path):
    return tiffmeta.read_xmp(path) or ""


print("A options")
o = exporting.tidy_options({"format": "bmp", "quality": 900, "long": "abc", "naming": "date_seq", "template": "x", "start": -4})
check("an unknown kind of file becomes TIFF 16-bit", o["format"] == "tiff16")
check("numbers are kept in range", o["quality"] == 100 and o["long"] == 3000 and o["start"] == 0, str(o))
check("a named way of naming sets the template", o["template"] == "{date}_{seq}")
check("TIFF 16-bit, as scanned, full size, all metadata is the default",
      [exporting.DEFAULTS[k] for k in ("format", "space", "resize", "metadata")] == ["tiff16", "same", "none", "all"])
got = app.library_export_options()
check("the dialog is told the kinds of file, TIFF 16-bit first",
      [f[0] for f in got["formats"]] == ["tiff16", "tiff8", "png", "jpeg", "heic", "dng"], str(got["formats"]))
check("a For Lightroom preset is built in, first: TIFF 16-bit, then opened in Lightroom",
      list(got["built_in"])[0] == "For Lightroom" and got["built_in"]["For Lightroom"]["format"] == "tiff16"
      and got["built_in"]["For Lightroom"]["after"] == "lightroom")
check("no folder chosen: said in words", "Choose the folder" in run([A], dest="").get("error", ""))
check("nothing ticked: said in words", "Tick" in run([]).get("error", ""))

print("B TIFF 16-bit")
r = run([A, B])
ta, tb = out / K / A, out / K / B
check("two pictures exported into the roll's folder in the chosen folder", r["ok"] and r["saved"] == [A, B] and ta.is_file() and tb.is_file(), str(r))
ga = read_tiff16(ta)
want = [np.rot90(p, 1) for p in pa]
rows, cols = want[0].shape
want = [p[int(round(.2 * rows)):int(round(.8 * rows)), int(round(.1 * cols)):int(round(.6 * cols))] for p in want]
check("the crop and turn are in the copy's pixels", ga[0].shape == want[0].shape and all((x == y).all() for x, y in zip(ga, want)), str(ga[0].shape))
ia = read_tiff_info(ta)
check("the copy keeps the profile, resolution, date and film speed",
      icc_name(ia["icc"]) == "ProPhoto RGB" and ia["dpi"] == 2900 and ia["taken"] == datetime.datetime(2004, 3, 5, 10, 0, 0) and ia.get("iso") == 200, str({k: v for k, v in ia.items() if k != "icc"}))
x = xmp_of(ta)
check("caption, people, tags and stars are in the copy", "Play &amp; party" in x and "Ilze Ozola" in x and "school" in x and "<xmp:Rating>4" in x)
check("an unchanged picture is copied as it is", all((x == y).all() for x, y in zip(read_tiff16(tb), pb)))
check("a picture in Positive/ goes straight into its roll's folder", run([D], key=K2)["ok"] and (out / K2 / "20261102_001_R002.tif").is_file())

print("C names and files already there")
r = run([A, B])
check("a second export gets new names, nothing replaced", r["saved"] == ["20261101_001_R001 (2).tif", "20261101_002_R001 (2).tif"], str(r["saved"]))
r = run([A, B], existing="skip")
check("or the ones already there are skipped", r["saved"] == [] and r["skipped"] == 2, str(r))
old = ta.stat().st_mtime_ns
time.sleep(0.02)
r = run([A], existing="replace", metadata="none")
check("or replaced", r["saved"] == [A] and xmp_of(ta) == "", str(r))
r = run([A, B, C], naming="text_seq", text="Riga 2004", start=9, keep_rolls=False, subfolder="For Ilze")
check("custom name and number, from a start number, in a subfolder, without roll folders",
      r["saved"] == ["Riga 2004-009.tif", "Riga 2004-010.tif", "Riga 2004-011.tif"] and (out / "For Ilze" / "Riga 2004-010.tif").is_file(), str(r["saved"]))
r = run([A, B], naming="date_seq", keep_rolls=False)
check("date taken and number", r["saved"] == ["20040305_001.tif", "20040305_002.tif"], str(r["saved"]))
r = run([A], naming="roll_frame", keep_rolls=False)
check("roll and frame", r["saved"] == ["Roll 001 - Fuji_001.tif"], str(r["saved"]))
r = run([A], naming="custom", template="{date} {text}/{frame}:x", text="a", keep_rolls=False)
check("a template of one's own; no slashes or colons in names", r["saved"] == ["20040305 a-001-x.tif"], str(r["saved"]))
r = run([A, B], dest=str(base), existing="replace")
check("exported among the scans: _modified, and Replace never replaces a scan",
      r["saved"] == ["20261101_001_R001_modified.tif", "20261101_002_R001_modified.tif"], str(r["saved"]))
r = run([A], dest=str(base), existing="replace")
check("... nor an earlier export there", r["saved"] == ["20261101_001_R001_modified (2).tif"], str(r["saved"]))
for p in (base / K).glob("*_modified*.tif"):
    p.unlink()
n = app.library_export_names(K, [A, B], {"dest": str(out), "naming": "text_seq", "text": "Riga"})
check("the dialog's example name", n["names"] == ["Riga-001.tif"] and n["count"] == 2 and not n["problem"], str(n))

print("D TIFF 8-bit")
o8 = tmp / "Out8"
r = run([A, C], dest=str(o8), format="tiff8", creator="D. Ozols", copyright="(c) family")
t = tifffile.TiffFile(o8 / K / A)
a8 = t.asarray()
check("8 bits a channel, cropped and turned", r["ok"] and a8.dtype == np.uint8 and a8.shape == want[0].shape + (3,), str(a8.shape))
check("the same picture, rounded to 8 bits", np.abs(a8[..., 0].astype(int) - (want[0].astype(int) + 128) // 257).max() == 0)
tags = {tg.code: tg.value for tg in t.pages[0].tags}
check("profile, date, description, creator and copyright tags", icc_name(tags[34675]) == "ProPhoto RGB" and tags[306] == "2004:03:05 10:00:00"
      and tags[270] == "Film: test" and tags[315] == "D. Ozols" and tags[33432] == "(c) family", str({k: v for k, v in tags.items() if k in (306, 270, 315, 33432)}))
x = xmp_of(o8 / K / A)
check("caption, creator and copyright in the XMP", "Play &amp; party" in x and "<dc:creator>" in x and "(c) family" in x)

print("E PNG")
op = tmp / "OutPng"
r = run([A], dest=str(op), format="png", space="sRGB IEC61966-2.1")
raw = (op / K / "20261101_001_R001.png").read_bytes()
chunks, at = {}, 8
while at < len(raw):
    n, kind = struct.unpack(">I4s", raw[at:at + 8])
    chunks.setdefault(kind, []).append(raw[at + 8:at + 8 + n])
    check_crc = zlib.crc32(raw[at + 4:at + 8 + n]) & 0xFFFFFFFF == struct.unpack(">I", raw[at + 8 + n:at + 12 + n])[0]
    if not check_crc:
        check("PNG chunk " + kind.decode() + " checksum", False)
    at += 12 + n
w, h, bits, ctype = struct.unpack(">IIBB", chunks[b"IHDR"][0][:10])
check("a PNG of the cropped size, 8-bit RGB", raw[:8] == b"\x89PNG\r\n\x1a\n" and (h, w) == want[0].shape and (bits, ctype) == (8, 2), str((w, h, bits, ctype)))
prof = zlib.decompress(chunks[b"iCCP"][0].split(b"\0", 1)[1][1:])
check("with the sRGB profile and the XMP inside", icc_name(prof).startswith("sRGB") and b"Play &amp; party" in chunks[b"iTXt"][0])
rows8 = np.frombuffer(zlib.decompress(b"".join(chunks[b"IDAT"])), np.uint8).reshape(h, w * 3 + 1)
px = np.cumsum(rows8[:, 1:].reshape(h, w, 3), axis=1, dtype=np.uint8)
check("every row uses the Sub filter and unpacks to a picture", (rows8[:, 0] == 1).all() and px.std() > 10)

print("F sizes")
osz = tmp / "OutSize"
r = run([B], dest=str(osz), resize="long", long=150)
g = read_tiff16(osz / K / B)
check("long edge 150", g[0].shape == (100, 150), str(g[0].shape))
check("averaged, not picked: the mean stays, the noise drops", abs(g[0].mean() - pb[0].mean()) < 300 and g[0].std() < pb[0].std() * 0.6, f"{g[0].mean():.0f} {pb[0].mean():.0f} {g[0].std():.0f}")
r = run([B], dest=str(osz), resize="long", long=9000, existing="replace")
check("never larger unless asked", read_tiff16(osz / K / B)[0].shape == (300, 450))
r = run([B], dest=str(osz), resize="long", long=900, enlarge=True, existing="replace")
check("larger when asked", read_tiff16(osz / K / B)[0].shape == (600, 900))
check("width and height: fits inside both", exporting.out_size(300, 450, exporting.tidy_options({"resize": "wh", "width": 200, "height": 50})) == (50, 75))
check("megapixels", exporting.out_size(3000, 4500, exporting.tidy_options({"resize": "mp", "mp": 1.5})) == (1000, 1500))
check("percent", exporting.out_size(300, 450, exporting.tidy_options({"resize": "percent", "percent": 50})) == (150, 225))
r = run([B], dest=str(osz), dpi=300, existing="replace")
check("resolution written as asked", read_tiff_info(osz / K / B)["dpi"] == 300)

print("G colour space")
osp = tmp / "OutSpace"
r = run([C], dest=str(osp), space="sRGB IEC61966-2.1")
g = read_tiff16(osp / K / C)
i = read_tiff_info(osp / K / C)
check("converted to sRGB, profile changed", icc_name(i["icc"]).startswith("sRGB"), icc_name(i["icc"]))
check("grey stays grey", max(np.abs(g[0].astype(int) - g[k].astype(int)).max() for k in (1, 2)) <= 2)
lin = (pc[0][0].astype(float) / 65535) ** 1.8
srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055) * 65535
check("with sRGB's own tone curve", np.abs(g[1][0] - srgb).max() < 40, f"{np.abs(g[1][0] - srgb).max():.1f}")
r = run([C], dest=str(osp), space="ProPhoto RGB", existing="replace")
check("the same space as the picture: pixels untouched", all((x == y).all() for x, y in zip(read_tiff16(osp / K / C), pc)))

print("H DNG")
od = tmp / "OutDng"
r = run([A, C], dest=str(od), format="dng")
t = tifffile.TiffFile(od / K / "20261101_003_R001.dng")
tags = {tg.code: tg.value for tg in t.pages[0].tags}
check("a DNG: version, linear raw, 16 bits, a camera name of its own",
      r["ok"] and not r["problems"] and tuple(tags[50706]) == (1, 4, 0, 0) and tags[262] == 34892 and tags[258] == (16, 16, 16)
      and "Coolscan app" in tags[50708], str(r["problems"]) + str(tags.get(50708)))
cm = np.array(tags[50721], float).reshape(-1, 2)
cm = (cm[:, 0] / cm[:, 1]).reshape(3, 3)
white = np.linalg.inv(cm) @ np.ones(3)
check("its colour matrix takes white to D50 (ProPhoto's white)", np.abs(white - [0.9642, 1.0, 0.8249]).max() < 0.002 and tags[50778] == 23, str(white))
check("a straight tone curve, so it looks as it does here", [round(float(v), 3) for v in tags[50940]] == [0, 0, 1, 1])
d = t.asarray()
check("pixels in linear light", np.abs(d[0, :, 1] - lin * 65535).max() < 3, f"{np.abs(d[0, :, 1] - lin * 65535).max():.2f}")
check("date and caption in it too", "2004:03:05" in str(tags.get(306)) and "Play &amp; party" in xmp_of(od / K / "20261101_001_R001.dng"))

print("I metadata")
om = tmp / "OutMeta"
run([A], dest=str(om), metadata="nopeople")
x = xmp_of(om / K / A)
check("without people: caption and tags, no names", "Play &amp; party" in x and "school" in x and "Ilze" not in x)
run([A], dest=str(om), metadata="basic", existing="replace")
x, i = xmp_of(om / K / A), read_tiff_info(om / K / A)
check("date and caption only", "Play &amp; party" in x and "school" not in x and "Rating" not in x and i["taken"].year == 2004 and not i.get("description") and not i["coolscan"], x[-300:])
run([A], dest=str(om), metadata="none", existing="replace", copyright="(c) family")
x, i = xmp_of(om / K / A), read_tiff_info(om / K / A)
check("none: no date, caption or scanner; the typed copyright is there", "party" not in x and not i.get("taken") and not i.get("model") and "(c) family" in x, x[-200:])

print("J JPEG and HEIC")
oj = tmp / "OutJpeg"
real_path = os.environ.get("PATH", "")
os.environ["PATH"] = str(tmp / "nobin")
r = run([A], dest=str(oj), format="jpeg")
check("no Mac converter here: said in words, nothing written", "converter" in r.get("error", "") and not oj.exists(), str(r))
fake = tmp / "bin"
fake.mkdir()
(fake / "sips").write_text('#!/bin/sh\n# stand-in for the Mac\'s sips: -s format F -s formatOptions Q in --out out\necho "$3 $6" > "$9.args"\ncp "$7" "$9"\n')
(fake / "sips").chmod(0o755)
os.environ["PATH"] = str(fake) + os.pathsep + real_path
r = run([A], dest=str(oj), format="jpeg", quality=77, space="sRGB IEC61966-2.1", resize="long", long=100)
tj = oj / K / "20261101_001_R001.jpg"
check("JPEG: the converter is handed a TIFF and the quality", r["ok"] and r["saved"] == [tj.name] and (oj / K / (tj.name + ".args")).read_text().split() == ["jpeg", "77"], str(r))
src = tifffile.TiffFile(tj)   # (the stand-in copies what it was given)
check("that TIFF is resized, in sRGB, with the caption in it", max(src.pages[0].shape[:2]) == 100 and icc_name(src.pages[0].tags[34675].value).startswith("sRGB") and b"party" in tj.read_bytes())
r = run([A], dest=str(oj), format="heic")
check("HEIC the same way", r["saved"] == ["20261101_001_R001.heic"] and (oj / K / "20261101_001_R001.heic.args").read_text().split()[0] == "heic", str(r))
(fake / "sips").write_text("#!/bin/sh\necho 'Error: cannot write' >&2\nexit 1\n")
r = run([A], dest=str(oj), format="jpeg", existing="replace")
check("a converter that fails: the picture is listed as not exported, with why", r["ok"] and r["saved"] == [] and "cannot write" in r["problems"][0], str(r))
os.environ["PATH"] = real_path

print("K presets and the last options")
check("the last options used are remembered", app.library_export_options()["last"]["format"] == "jpeg" and app.library_export_options()["used"])
r = app.library_export_preset("save", "  For   Ilze ", {"format": "png", "resize": "long", "long": 1200, "dest": str(out)})
got = app.library_export_options()["presets"]
check("a preset saved under a tidy name", r == {"ok": True, "name": "For Ilze"} and got["For Ilze"]["format"] == "png" and got["For Ilze"]["long"] == 1200, str(got))
check("a built-in preset cannot be saved over or deleted", not app.library_export_preset("save", "For Lightroom", {})["ok"] and not app.library_export_preset("delete", "For Lightroom")["ok"])
check("a preset needs a name", not app.library_export_preset("save", " ", {})["ok"])
check("deleted", app.library_export_preset("delete", "For Ilze")["ok"] and "For Ilze" not in app.library_export_options()["presets"])
check("deleting one that is gone: said", not app.library_export_preset("delete", "For Ilze")["ok"])
check("kept in the home folder", (tmp / "home" / ".coolscan-export.json").is_file())

print("L the roll's Edited folder")
r = app.library_export_run(K, [A], {"where": "edited"})
e = base / K / "Edited" / A
check("a TIFF 16-bit copy in the roll's Edited folder", r["ok"] and e.is_file() and read_tiff16(e)[0].shape == want[0].shape, str(r))
m = json.loads((base / K / "Library.json").read_text())["pictures"]
check("with its stars and caption in the app, and no crop of its own", m["Edited/" + A].get("stars") == 4 and "edit" not in m["Edited/" + A], str(m.get("Edited/" + A)))
r = app.library_export_run(K, [A], {"where": "edited", "format": "png"})
check("other kinds of file are not put there", "TIFF 16-bit only" in r.get("error", ""))
e.unlink()

print("L2 what to export: RAW scans, both, crop and colour on or off, whole rolls")
K3 = "Roll 003"
R1, R2 = "20261103_001_R003.tif", "20261103_002_R003.tif"
note = {"film": "positive", "dpi": 2900, "crop": [0.1, 0.2, 0.9, 0.8]}
rawp = [(rng.random((500, 800)) * 60000).astype(np.uint16) for _ in range(4)]
(base / K3 / "RAW").mkdir(parents=True)
(base / K3 / "Positive").mkdir()
write_tiff(base / K3 / "RAW" / R1, rawp, 2900, None, meta={"taken": datetime.datetime(1990, 5, 1, 12, 0, 0), "coolscan": note})
posp = [p[100:400, 80:720] for p in rawp[:3]]       # the finished picture: that part of the frame
write_tiff(base / K3 / "Positive" / R1, posp, 2900, icc_profile("ProPhoto RGB"), meta={"taken": datetime.datetime(1990, 5, 1, 12, 0, 0)})
write_tiff(base / K3 / "RAW" / R2, rawp, 2900, None, meta={"coolscan": {"film": "negative"}})   # a RAW with no finished picture
app.library_edit(K3, ["Positive/" + R1], {"edit": {"turns": 1, "crop": [0.0, 0.0, 0.5, 1.0]}})
orw = tmp / "OutRaw"
r = run(["Positive/" + R1], key=K3, dest=str(orw), source="both")
gp, gr = read_tiff16(orw / K3 / R1), read_tiff16(orw / K3 / "RAW" / R1)
check("both: the picture in the roll's folder, its RAW scan in RAW/ there", r["ok"] and r["saved"] == [R1, R1] and not r["problems"], str(r))
check("the RAW scan keeps its infrared channel", len(gr) == 4 and len(gp) == 3)
check("and is framed like the picture: scan crop, then my crop and turn", gr[0].shape == gp[0].shape and all((a == b).all() for a, b in zip(gr[:3], gp)), f"{gr[0].shape} {gp[0].shape}")
r = run(["Positive/" + R1], key=K3, dest=str(orw), source="raw", apply_crop=False, existing="replace")
gr = read_tiff16(orw / K3 / "RAW" / R1)
check("RAW scans without the crop: the whole frame as scanned", gr[0].shape == (500, 800) and all((a == b).all() for a, b in zip(gr, rawp)))
check("... with the scan note still in it", read_tiff_info(orw / K3 / "RAW" / R1)["coolscan"].get("crop") == note["crop"])
r = run(["Positive/" + R1], key=K3, dest=str(orw), apply_crop=False, existing="replace")
check("pictures without the crop and turn: as saved", all((a == b).all() for a, b in zip(read_tiff16(orw / K3 / R1), posp)))
r = run(["Positive/" + R1, "RAW/" + R2], key=K3, dest=str(orw), source="raw", keep_rolls=False, naming="text_seq", text="neg")
check("RAW scans in one folder get _RAW in the name; a RAW-only frame is exported too", r["saved"] == ["neg-001_RAW.tif", "neg-002_RAW.tif"], str(r))
r = run(["RAW/" + R2, A], key=K3, items=[[K3, "RAW/" + R2], [K, A]], dest=str(orw), source="positive", existing="skip")
check("finished pictures asked for, only a RAW there: said, the rest exported", len(r["problems"]) == 1 and "no finished picture" in r["problems"][0] and r["saved"] == [A], str(r))
r = run([A], dest=str(orw), source="raw")
check("RAW scans asked for, none kept: said", r["saved"] == [] and "no RAW scan" in r["problems"][0], str(r))
check("RAW scans do not go into the Edited folder", "chosen folder" in app.library_export_run(K3, ["Positive/" + R1], {"where": "edited", "source": "raw"}).get("error", ""))
got = app.library_export_scope({"rolls": [K3]})
check("a whole roll: its pictures and RAW-only frames", got == [[K3, "Positive/" + R1], [K3, "RAW/" + R2]], str(got))
check("all rolls", len(app.library_export_scope({"all": True})) == len(got) + 4 and app.library_export_scope(None) is None, str(len(app.library_export_scope({"all": True}))))
check("the example name skips what cannot be exported", app.library_export_names(K3, ["RAW/" + R2, "Positive/" + R1], {"dest": str(tmp / "OutEx")})["names"] == [R1])
sums.update({p: hashlib.md5(p.read_bytes()).hexdigest() for p in (base / K3).rglob("*.tif")})

print("M in the background, and the routes")
server = ThreadingHTTPServer(("127.0.0.1", 0), cs.server.make_handler(app))
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}"


def post(path, body):
    req = urllib.request.Request(url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def wait():
    for _ in range(400):
        s = post("/api/library/export_status", {})
        if not s.get("running"):
            return s
        time.sleep(0.05)
    return s


r = post("/api/library/export_start", {"scope": {"rolls": [K3]}, "options": {"dest": str(tmp / "OutScope"), "source": "both"}})
s = wait()
check("a whole roll through the route: 1 picture and 2 RAW scans", r.get("ok") and s["saved"] == 3 and not s["problems"], str(s))
check("options route", post("/api/library/export_options", {})["built_in"]["Full size TIFF"]["format"] == "tiff16")
check("names route", post("/api/library/export_names", {"key": K, "files": [A], "options": {"dest": str(out), "format": "dng"}})["names"] == ["20261101_001_R001.dng"])
ob = tmp / "OutBg"
r = post("/api/library/export_start", {"items": [[K, A], [K, B], [K2, D]], "options": {"dest": str(ob), "format": "tiff8"}})
s = wait()
check("pictures of two rolls exported in the background", r == {"ok": True, "total": 3} and s["done"] == 3 and s["saved"] == 3 and not s["problems"]
      and (ob / K / B).is_file() and (ob / K2 / "20261102_001_R002.tif").is_file(), str(s))
r = post("/api/library/export_again", {"key": K, "files": [C]})
s = wait()
check("Export with previous: the same options again", r.get("ok") and s["saved"] == 1 and tifffile.imread(ob / K / C).dtype == np.uint8, str(s))
r = post("/api/library/export_preset", {"action": "save", "name": "Mine", "options": {"format": "dng"}})
check("preset route", r["ok"] and post("/api/library/export_options", {})["presets"]["Mine"]["format"] == "dng")
check("a wrong folder is said at once, nothing started", "no folder" in post("/api/library/export_start", {"key": K, "files": [A], "options": {"dest": str(tmp / "no" / "such" / "place")}}).get("error", ""))
slow = app._export_one
app._export_one = lambda *a: (time.sleep(0.3), slow(*a))[1]
r = post("/api/library/export_start", {"key": K, "files": [A, B, C], "options": {"dest": str(tmp / "OutStop")}})
check("a second export while one runs is refused", post("/api/library/export_start", {"key": K, "files": [A], "options": {"dest": str(out)}}).get("busy"))
for _ in range(100):   # (wait until the first picture is in hand)
    if post("/api/library/export_status", {}).get("current"):
        break
    time.sleep(0.02)
post("/api/library/export_cancel", {})
s = wait()
check("Cancel stops after the picture in hand", s.get("cancelled") and 1 <= s["saved"] < 3, str(s))
app._export_one = slow

print("N the pictures themselves")
check("no scan was changed by any export", all(hashlib.md5(p.read_bytes()).hexdigest() == v for p, v in sums.items()))

print("\nall passed" if not bad else f"\n{len(bad)} FAILED: {bad}")
sys.exit(1 if bad else 0)
