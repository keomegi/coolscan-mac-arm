"""The roll's camera, and a picture's aperture and shutter speed: kept with the roll (Library.json), written
into the files as a camera writes them (Make, Model, FNumber, ExposureTime; pixels untouched, the scanner's
name kept), set at New roll, put into new scans and into exports."""
import sys, json, tempfile, pathlib, datetime
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import tifffile
import cs_next as cs
from coolscan_app.imaging.files import write_tiff, read_tiff16, read_tiff_info
from coolscan_app.imaging import tiffmeta

tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
bad = []


def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        bad.append(name)


rng = np.random.default_rng(3)


def picture(path, spp=3):
    planes = [(rng.random((200, 300)) * 60000).astype(np.uint16) for _ in range(spp)]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tiff(path, planes, 2900, None, meta={"taken": datetime.datetime(1988, 2, 6, 12, 0, 0), "description": "Film: test",
                                                 "make": "Nikon", "model": "Nikon COOLSCAN IV ED", "iso": 200})
    return planes


def tags(path):
    with tifffile.TiffFile(path) as t:
        page = t.pages[0]
        out = {k: page.tags[k].value for k in ("Make", "Model") if k in page.tags}
        exif = page.tags["ExifTag"].value if "ExifTag" in page.tags else {}
        out.update({k: exif[k] for k in ("FNumber", "ExposureTime", "DateTimeOriginal", "ISOSpeedRatings") if k in exif})
        return out


print("A what is typed")
ra, rs = tiffmeta.read_aperture, tiffmeta.read_shutter
check("aperture: 5.6, f/5.6, ƒ5,6, F 8", [ra("5.6"), ra("f/5.6"), ra("ƒ5,6"), ra("F 8")] == [5.6, 5.6, 5.6, 8.0])
check("aperture: nothing typed is none", ra("") is None and ra(None) is None)
check("shutter: 1/125, 1/125 s, 125 (as on the dial)", [rs("1/125"), rs("1/125 s"), rs("125")] == ["1/125"] * 3)
check("shutter: seconds 2s, 2\", 1, 0.5", [rs("2s"), rs('2"'), rs("1"), rs("0.5")] == ["2", "2", "1", "1/2"], str([rs("2s"), rs('2"'), rs("1"), rs("0.5")]))
for wrong, fn in (("abc", ra), ("0", ra), ("500", ra), ("fast", rs), ("1/0", rs), ("0s", rs)):
    try:
        fn(wrong)
        check(f"{wrong!r} is refused", False)
    except ValueError:
        check(f"{wrong!r} is refused", True)
check("camera: make is the first word, model the whole name", tiffmeta.camera_parts(" Nikon  F3 ") == ("Nikon", "Nikon F3"))

base = tmp / "Scans"
r1 = base / "Roll 001 - Fuji"
p1 = picture(r1 / "20261101_001_R001.tif")
picture(r1 / "20261101_002_R001.tif")
picture(r1 / "RAW" / "20261101_001_R001.tif", spp=4)
app = cs.App(demo=True)
app.settings.update(outdir=str(base), roll="Roll 001 - Fuji", use_roll=True)
key, f1, f2 = "Roll 001 - Fuji", "20261101_001_R001.tif", "20261101_002_R001.tif"

print("B the roll's camera, set in Scanned pictures")
check("no camera to start with", app.library_roll(key)["camera"] == "")
got = app.library_camera(key, "  Nikon   F3 ")
check("saved, both finished pictures written, the RAW left alone", got["ok"] and got["camera"] == "Nikon F3" and got["changed"] == 2 and not got["problems"], str(got))
check("kept in the roll's Library.json", json.loads((r1 / "Library.json").read_text())["camera"] == "Nikon F3")
check("the roll says it", app.library_roll(key)["camera"] == "Nikon F3")
t = tags(r1 / f1)
check("the file: Make Nikon, Model Nikon F3", t.get("Make") == "Nikon" and t.get("Model") == "Nikon F3", str(t))
check("the file keeps its date and film speed", t.get("DateTimeOriginal") == "1988:02:06 12:00:00" and t.get("ISOSpeedRatings") == 200, str(t))
check("pixels untouched", all(np.array_equal(a, b) for a, b in zip(read_tiff16(r1 / f1), p1)))
check("RAW file still names the scanner", tags(r1 / "RAW" / f1).get("Model") == "Nikon COOLSCAN IV ED")
info = app.library_info(key, f1)
check("picture details: the camera, and the scanner still known", info["camera"] == "Nikon F3" and info["scanner"] == "Nikon COOLSCAN IV ED", f'{info["camera"]} / {info["scanner"]}')
check("the scanning screen's roll has it too (current roll)", app.settings["film_camera"] == "Nikon F3")
xmp = tiffmeta.read_xmp(r1 / f1)
check("XMP: tiff:Make, tiff:Model", "<tiff:Make>Nikon</tiff:Make>" in xmp and "<tiff:Model>Nikon F3</tiff:Model>" in xmp)
check("search finds the roll's pictures by camera", len(app.library_search("f3")["pictures"]) >= 2)

print("C aperture and shutter speed of one picture")
got = app.library_edit(key, [f1], {"aperture": "f/5.6", "shutter": "125"})
check("saved in the app and in the file", got["ok"] and got["changed"] == 1 and not got["problems"], str(got))
p = next(x for x in app.library_roll(key)["pictures"] if x["file"] == f1)
check("the picture has ƒ/5.6 and 1/125", p["aperture"] == 5.6 and p["shutter"] == "1/125", f'{p["aperture"]} {p["shutter"]}')
t = tags(r1 / f1)
check("the file: FNumber 5.6", t.get("FNumber") and abs(t["FNumber"][0] / t["FNumber"][1] - 5.6) < 1e-6, str(t.get("FNumber")))
check("the file: ExposureTime 1/125", t.get("ExposureTime") == (1, 125), str(t.get("ExposureTime")))
check("the file: camera and date still there", t.get("Model") == "Nikon F3" and t.get("DateTimeOriginal") == "1988:02:06 12:00:00", str(t))
xmp = tiffmeta.read_xmp(r1 / f1)
check("XMP: exif:FNumber, exif:ExposureTime", "<exif:FNumber>28/5</exif:FNumber>" in xmp and "<exif:ExposureTime>1/125</exif:ExposureTime>" in xmp)
ri = read_tiff_info(r1 / f1)
check("the app's own reader sees them", abs(ri.get("fnumber", 0) - 5.6) < 1e-6 and abs(ri.get("exposure", 0) - 1 / 125) < 1e-9)
info = app.library_info(key, f1)
check("picture details show them", info["aperture"] == 5.6 and info["shutter"] == "1/125")
app.library_edit(key, [f1], {"caption": "At the lake"})
t = tags(r1 / f1)
check("a later caption keeps camera, aperture, shutter", t.get("Model") == "Nikon F3" and t.get("ExposureTime") == (1, 125) and "FNumber" in t, str(t))
check("the other picture has none", "FNumber" not in tags(r1 / f2) and tags(r1 / f2).get("Model") == "Nikon F3")
got = app.library_edit(key, [f1], {"aperture": "quick"})
check("a wrong aperture is refused with words", not got["ok"] and "aperture" in got["error"], str(got))
got = app.library_edit(key, [f1], {"shutter": "2s"})
check("whole seconds: 2s", tags(r1 / f1).get("ExposureTime") == (2, 1), str(tags(r1 / f1).get("ExposureTime")))
app.library_edit(key, [f1], {"shutter": "", "aperture": ""})
t = tags(r1 / f1)
check("emptied: out of the file again", "FNumber" not in t and "ExposureTime" not in t, str(t))
p = next(x for x in app.library_roll(key)["pictures"] if x["file"] == f1)
check("and out of the app's list", p["aperture"] is None and p["shutter"] == "")
app.library_edit(key, [f1], {"aperture": "8", "shutter": "1/60"})

print("D another camera, and none")
app.library_camera(key, "Зенит Е")
t = tags(r1 / f1)
check("a camera name in other letters", t.get("Model") == "Зенит Е" and t.get("Make") == "Зенит", str(t))
check("scanner still known after a change of camera", app.library_info(key, f1)["scanner"] == "Nikon COOLSCAN IV ED")
check("aperture kept through it", tags(r1 / f1).get("ExposureTime") == (1, 60))
app.library_camera(key, "")
t = tags(r1 / f1)
check("camera taken out: Make and Model say the scanner again", t.get("Make") == "Nikon" and t.get("Model") == "Nikon COOLSCAN IV ED", str(t))
check("and the roll has none", app.library_roll(key)["camera"] == "" and app.library_info(key, f1)["camera"] is None)
app.library_camera(key, "Nikon F3")

print("E a file another program wrote into")
theirs = r1 / "20261101_003_R001.tif"
picture(theirs)
tiffmeta.update_tiff(theirs, caption="x")
raw = theirs.read_bytes().replace(tiffmeta.MARK.encode(), b'xmlns:other="https://example.invalid/ns/1.0/"  ')
theirs.write_bytes(raw)
got = app.library_edit(key, [theirs.name], {"aperture": "2.8"})
check("kept in the app's list only, said so", got["ok"] and len(got["problems"]) == 1, str(got))
check("that file is unchanged", theirs.read_bytes() == raw)

print("F exports")
out = tmp / "Out"
from coolscan_app import exporting
for fmt in ("tiff16", "tiff8"):
    opts = exporting.tidy_options({**exporting.DEFAULTS, "format": fmt, "where": "folder", "folder": str(out / fmt), "metadata": "all"})
    target = out / fmt / "a.tif"
    app._export_one(opts, key, f1, target)
    t = tags(target)
    check(f"{fmt}: camera, aperture, shutter in the copy", t.get("Model") == "Nikon F3" and t.get("ExposureTime") == (1, 60) and t.get("FNumber") and t["FNumber"][0] / t["FNumber"][1] == 8, str(t))
    check(f"{fmt}: the copy still knows the scanner", tiffmeta.read_scanner(target) == "Nikon COOLSCAN IV ED", tiffmeta.read_scanner(target))
target = out / "theirs" / "a.tif"
opts = exporting.tidy_options({**exporting.DEFAULTS, "format": "tiff8", "where": "folder", "folder": str(out), "metadata": "all"})
app._export_one(opts, key, theirs.name, target)
t = tags(target)
check("a file that could not take them: the export has them", t.get("Model") == "Nikon F3" and t.get("FNumber") and abs(t["FNumber"][0] / t["FNumber"][1] - 2.8) < 1e-6, str(t))
target = out / "raw" / "a.tif"
app._export_one(opts, key, f1, target, kind="raw")
check("a RAW scan's export has the camera", tags(target).get("Model") == "Nikon F3", str(tags(target)))
target = out / "none" / "a.tif"
app._export_one(exporting.tidy_options({**opts, "metadata": "none"}), key, f1, target)
t = tags(target)
check("metadata 'none': no camera, aperture or shutter", "FNumber" not in t and t.get("Model") != "Nikon F3", str(t))
target = out / "png" / "a.png"
app._export_one(exporting.tidy_options({**opts, "format": "png"}), key, f1, target)
check("PNG: in its XMP", b"<tiff:Model>Nikon F3</tiff:Model>" in target.read_bytes() and b"<exif:FNumber>8/1</exif:FNumber>" in target.read_bytes())

print("G New roll, going back to a roll, new scans")
app.new_roll({"film_brand": "Kodak", "film_stock": "Gold", "film_iso": "200", "film_camera": "Olympus OM-1"})
new = app.settings["roll"]
check("the camera is not in the folder's name", "Olympus" not in new and new.startswith("Roll 002"), new)
check("kept with the new roll", json.loads((base / new / "Library.json").read_text()).get("camera") == "Olympus OM-1")
check("Scanned pictures shows it for the new roll", app.library_roll(new)["camera"] == "Olympus OM-1")
check("going back to roll 1 brings its camera", app.switch_roll(key)["ok"] and app.settings["film_camera"] == "Nikon F3", app.settings["film_camera"])
check("and forward again", app.switch_roll(new)["ok"] and app.settings["film_camera"] == "Olympus OM-1", app.settings["film_camera"])
app.new_roll({"film_brand": "Ilford"})
check("a new roll without a camera has none", app.settings["film_camera"] == "")
app.switch_roll(new)
fresh = base / new / "20261126_001_R002.tif"
picture(fresh)
app._stamp_camera(fresh)
t = tags(fresh)
check("a new scan gets the roll's camera", t.get("Make") == "Olympus" and t.get("Model") == "Olympus OM-1", str(t))
check("and its scanner is still known", app.library_info(new, fresh.name)["scanner"] == "Nikon COOLSCAN IV ED")
check("tifffile reads every changed file", all(tifffile.imread(p).shape[0] == 200 for p in (r1 / f1, r1 / f2, fresh)))

print("\n" + ("ALL PASS" if not bad else f"{len(bad)} FAILED: {bad}"))
sys.exit(1 if bad else 0)
