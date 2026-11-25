"""Library: folders of scans already made, descriptions, stars/flags/tags/people/captions and dates written
into the files (pixels untouched), bulk date changes, search, cropped and turned copies, and the routes."""
import sys, json, struct, tempfile, pathlib, datetime, threading, urllib.request, urllib.parse
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import cs_next as cs
from http.server import ThreadingHTTPServer
from coolscan_app.imaging.files import write_tiff, read_tiff16, read_tiff_info
from coolscan_app.imaging.colour import icc_profile
from coolscan_app.imaging import tiffmeta

tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
bad = []


def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        bad.append(name)


rng = np.random.default_rng(1)


def picture(path, taken, rows=300, cols=450, note=None, spp=3):
    planes = [(rng.random((rows, cols)) * 60000).astype(np.uint16) for _ in range(spp)]
    meta = {"taken": taken, "description": "Film: test", "make": "Nikon", "model": "Nikon COOLSCAN IV ED", "iso": 200}
    if note:
        meta["coolscan"] = note
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tiff(path, planes, 2900, icc_profile("ProPhoto RGB") if not note else None, meta=meta)
    return planes


base = tmp / "Scans"
r1 = base / "Roll 001 - Fuji"
p1 = picture(r1 / "20261101_001_R001.tif", datetime.datetime(2004, 3, 5, 10, 0, 0))
p2 = picture(r1 / "20261101_002_R001.tif", datetime.datetime(2004, 3, 5, 10, 0, 7))
p3 = picture(r1 / "20261101_003_R001.tif", datetime.datetime(2004, 3, 5, 10, 0, 9))
(r1 / "Description.txt").write_text("Fuji 200, Riga\nmore\n")
r2 = base / "Roll 002"
picture(r2 / "Positive" / "20261102_001_R002.tif", datetime.datetime(1999, 1, 1, 9, 0, 0))
picture(r2 / "RAW" / "20261102_001_R002.tif", datetime.datetime(1999, 1, 1, 9, 0, 0),
        note={"film": "negative", "dpi": 2900, "exposures": {"red": 1, "green": 2, "blue": 3}, "turns": 0}, spp=4)
picture(r2 / "RAW" / "20261102_002_R002.tif", datetime.datetime(1999, 1, 1, 9, 0, 5), note={"film": "negative"}, spp=4)
picture(base / "loose_001.tif", datetime.datetime(2010, 6, 1, 8, 0, 0))
(base / "Tests").mkdir()

app = cs.App(demo=True)
app.settings.update(outdir=str(base), roll="Roll 001 - Fuji", use_roll=True)

print("A folders")
got = app.library_rolls()
names = [r["name"] for r in got["rolls"]]
check("loose pictures first, then the rolls; Tests is not a roll", names == ["Loose pictures", "Roll 001 - Fuji", "Roll 002"], str(names))
r002 = next(r for r in got["rolls"] if r["name"] == "Roll 002")
check("a roll counts a RAW file with no picture beside it", r002["count"] == 2 and r002["raw"], str(r002))
check("description's first line in the list", got["rolls"][1]["description"] == "Fuji 200, Riga")
roll = app.library_roll("Roll 002")
files = [p["file"] for p in roll["pictures"]]
check("Archive roll: Positive/ pictures plus RAW-only frames", files == ["Positive/20261102_001_R002.tif", "RAW/20261102_002_R002.tif"], str(files))
check("a picture knows its RAW file", roll["pictures"][0]["raw"] == "RAW/20261102_001_R002.tif")
for key, rel in (("..", "x.tif"), ("Roll 001 - Fuji", "../loose_001.tif"), ("Roll 001 - Fuji/..", "loose_001.tif"), ("", "/etc/passwd")):
    try:
        app._lib_file(key, rel)
        check(f"refused {key!r} {rel!r}", False)
    except ValueError:
        check(f"refused {key!r} {rel!r}", True)

print("B pictures and what they say")
info = app.library_info("Roll 002", "Positive/20261102_001_R002.tif")
check("size, dpi, profile, taken, ISO, scanner", info["pixels"] == [450, 300] and info["dpi"] == 2900 and info["profile"] == "ProPhoto RGB"
      and info["taken"] == "1999-01-01 09:00" and info["iso"] == 200 and info["scanner"] == "Nikon COOLSCAN IV ED", str(info))
check("how it was scanned comes from the RAW file", info["scan"].get("exposures") == {"red": 1, "green": 2, "blue": 3} and info["raw"] == "RAW/20261102_001_R002.tif", str(info["scan"]))
for big in (False, True):
    png = app.library_png("Roll 001 - Fuji", "20261101_001_R001.tif", big=big)
    check(f"{'big' if big else 'small'} picture is a PNG", png[:8] == b"\x89PNG\r\n\x1a\n")
check("a RAW file can be shown too", app.library_png("Roll 002", "RAW/20261102_002_R002.tif")[:4] == b"\x89PNG")

print("C description")
app.library_describe("Roll 002", "Kodak Gold\nwith Ilze")
check("written to Description.txt", (r2 / "Description.txt").read_text() == "Kodak Gold\nwith Ilze\n")
app.library_describe("Roll 001 - Fuji", "the current roll")
check("the current roll's goes through the scanning screen's copy too", app.settings["film_description"] == "the current roll"
      and (r1 / "Description.txt").read_text().strip() == "the current roll")

print("D marks written into the files, pixels untouched")
K, A, B, C = "Roll 001 - Fuji", "20261101_001_R001.tif", "20261101_002_R001.tif", "20261101_003_R001.tif"
res = app.library_edit(K, [A, B, C], {"add_people": ["Ilze Ozola", "Jānis"], "add_tags": ["school", "School"], "caption": "Play & party <1>"})
check("three pictures changed, no problems", res["ok"] and res["changed"] == 3 and not res["problems"], str(res))
res = app.library_edit(K, [A], {"stars": 4, "flag": "pick", "remove_people": ["jānis"]})
roll = app.library_roll(K)
pa = next(p for p in roll["pictures"] if p["file"] == A)
check("the folder's list has it", pa["stars"] == 4 and pa["flag"] == "pick" and pa["people"] == ["Ilze Ozola"] and pa["tags"] == ["school"]
      and pa["caption"] == "Play & party <1>", str(pa))
xmp = tiffmeta.read_xmp(r1 / A)
check("the file's XMP has caption, keywords (people too), person, rating", "<xmp:Rating>4</xmp:Rating>" in xmp and "Play &amp; party &lt;1&gt;" in xmp
      and "<rdf:li>Ilze Ozola</rdf:li></rdf:Bag></Iptc4xmpExt:PersonInImage>" in xmp and "<rdf:li>school</rdf:li>" in xmp, xmp)
check("pixels are exactly as they were", all(np.array_equal(a, b) for a, b in zip(read_tiff16(r1 / A), p1)))
app.library_edit(K, [C], {"flag": "reject"})
check("rejected = rating -1 (how Lightroom stores it)", "<xmp:Rating>-1</xmp:Rating>" in tiffmeta.read_xmp(r1 / C))

print("E dates, for a whole roll")
app.library_edit(K, [A, B, C], {"date": {"how": "year", "value": "1987"}})
t = [read_tiff_info(r1 / f)["taken"] for f in (A, B, C)]
check("year only: month, day and time stay", [x.strftime("%Y-%m-%d %H:%M:%S") for x in t] == ["1987-03-05 10:00:00", "1987-03-05 10:00:07", "1987-03-05 10:00:09"], str(t))
check("the file's date follows", abs(pathlib.Path(r1 / A).stat().st_mtime - t[0].timestamp()) < 2)
app.library_edit(K, [A, B, C], {"date": {"how": "date", "value": "1986-07-14"}})
t = [read_tiff_info(r1 / f)["taken"].strftime("%Y-%m-%d %H:%M:%S") for f in (A, B, C)]
check("day: each time of day stays", t == ["1986-07-14 10:00:00", "1986-07-14 10:00:07", "1986-07-14 10:00:09"], str(t))
app.library_edit(K, [C, A, B], {"date": {"how": "exact", "value": "1985-12-24T18:30"}, "date_note": "about Christmas 1985"})
t = [read_tiff_info(r1 / f)["taken"].strftime("%Y-%m-%d %H:%M:%S") for f in (A, B, C)]
check("exact: one second apart, in name order", t == ["1985-12-24 18:30:00", "1985-12-24 18:30:01", "1985-12-24 18:30:02"], str(t))
marks = json.loads((r1 / "Library.json").read_text())["pictures"][A]
check("the first date is remembered", marks.get("taken_was") == "2004-03-05 10:00:00" and marks["date_note"] == "about Christmas 1985", str(marks))
check("a wrong date is refused", not app.library_edit(K, [A], {"date": {"how": "year", "value": "abc"}})["ok"])
check("stars and people still there after date changes", "<xmp:Rating>4</xmp:Rating>" in tiffmeta.read_xmp(r1 / A))
check("pixels still exactly as they were", all(np.array_equal(a, b) for a, b in zip(read_tiff16(r1 / B), p2)))

print("F a file with someone else's XMP is not changed")
other = base / "Roll 003"
picture(other / "x.tif", datetime.datetime(2001, 1, 1))
tiffmeta.update_tiff(other / "x.tif", caption="Lightroom's", force=True)
raw = (other / "x.tif").read_bytes().replace(tiffmeta.MARK.encode(), b'xmlns:lr="http://ns.adobe.com/lightroom/1.0/"'.ljust(len(tiffmeta.MARK), b" "))
(other / "x.tif").write_bytes(raw)
before = (other / "x.tif").read_bytes()
res = app.library_edit("Roll 003", ["x.tif"], {"stars": 2})
check("kept in the app's list only, said so", res["ok"] and res["problems"] and (other / "x.tif").read_bytes() == before, str(res))

print("G search, tags, people")
s = app.library_search("ilze 1985")
check("search finds by person and year", sorted(p["file"] for p in s["pictures"]) == [A, B, C], str([p["file"] for p in s["pictures"]]))
check("search by roll description", any(r["name"] == "Roll 002" for r in app.library_search("kodak")["rolls"]))
check("every word must match", app.library_search("jānis kodak")["pictures"] == [])
t = app.library_tags()
check("tags and people lists", t["tags"] == ["school"] and "Ilze Ozola" in t["people"], str(t))

print("H crop and turn: kept as settings, the file never changed; Export makes copies")
before, beforeB = (r1 / A).read_bytes(), (r1 / B).read_bytes()
res = app.library_crop(K, A, [0.1, 0.2, 0.6, 0.8])
check("crop kept as a setting", res["ok"] and res["edit"] == {"turns": 0, "crop": [0.1, 0.2, 0.6, 0.8]}, str(res))
check("the picture itself unchanged", (r1 / A).read_bytes() == before)
check("no file made by cropping", not (r1 / "Edited").exists())
pa = next(p for p in app.library_roll(K)["pictures"] if p["file"] == A)
check("the folder knows the crop", pa["edit"] == {"turns": 0, "crop": [0.1, 0.2, 0.6, 0.8]}, str(pa.get("edit")))
app2 = cs.App(demo=True)
app2.settings.update(outdir=str(base), roll="Roll 001 - Fuji", use_roll=True)
check("remembered after the app is opened again", app2.library_info(K, A)["edit"] == pa["edit"])
def png_size(b):
    return struct.unpack(">II", b[16:24])
check("shown cropped", png_size(app.library_png(K, A, big=True)) == (225, 180), str(png_size(app.library_png(K, A, big=True))))
check("original shown whole", png_size(app.library_png(K, A, big=True, view="original")) == (450, 300))
res = app.library_turn(K, A, 1)
check("turning turns the crop with it", res["ok"] and res["edit"] == {"turns": 1, "crop": [0.2, 0.4, 0.8, 0.9]}, str(res))
check("shown turned and cropped", png_size(app.library_png(K, A, big=True)) == (180, 225), str(png_size(app.library_png(K, A, big=True))))
check("turned, not cut, for cropping", png_size(app.library_png(K, A, big=True, view="turned")) == (300, 450))
check("small picture turned and cropped too", png_size(app.library_png(K, A)) == (90, 112), str(png_size(app.library_png(K, A))))
app.library_turn(K, A, 3)
check("turned back, crop back", app._lib_edit_of(r1, A) == {"turns": 0, "crop": [0.1, 0.2, 0.6, 0.8]}, str(app._lib_edit_of(r1, A)))
check("still unchanged", (r1 / A).read_bytes() == before)
res = app.library_export(K, [A, C])
check("export: a copy with the crop, unchanged pictures left out", res["ok"] and res["saved"] == ["Edited/" + A] and res["unchanged"] == [C], str(res))
ci = read_tiff_info(r1 / res["saved"][0])
check("exported copy has the crop in its pixels", (ci["cols"], ci["rows"]) == (225, 180))
check("the copy keeps date, ISO and profile, and the marks", ci["taken"] == read_tiff_info(r1 / A)["taken"] and ci["iso"] == 200
      and ci.get("icc") and "<xmp:Rating>4</xmp:Rating>" in tiffmeta.read_xmp(r1 / res["saved"][0]))
check("the copy is not cropped a second time", app._lib_edit_of(r1, res["saved"][0]) is None)
res2 = app.library_export(K, [A])
check("a second export gets a number", res2["saved"] == ["Edited/20261101_001_R001 (2).tif"], str(res2))
res = app.library_turn(K, B, 1)
app.library_export(K, [B])
ti = read_tiff_info(r1 / "Edited" / B)
check("turned export", (ti["cols"], ti["rows"]) == (300, 450))
check("B unchanged", (r1 / B).read_bytes() == beforeB)
app.library_edit(K, [C], {"redo": True, "edit": {"turns": 2}})
res = app.library_export(K, [C])
check("export of a picture with no other marks", res["ok"] and res["saved"] == ["Edited/" + C], str(res))
app.library_edit(K, [C], {"redo": False, "edit": None})
pb = next(p for p in app.library_roll(K)["pictures"] if p["file"] == B)
check("copies listed as versions of the picture", pb["versions"] == ["Edited/" + B], str(pb["versions"]))
check("Edited is not a roll", "Edited" not in [r["name"] for r in app.library_rolls()["rolls"]])
app.library_edit(K, [B], {"edit": None})
check("reset takes the turn off", app._lib_edit_of(r1, B) is None and png_size(app.library_png(K, B, big=True)) == (450, 300))
# auto crop on a frame with a dark holder band on the left and right
fr = base / "Roll 009"
planes = [np.full((300, 450), 30000, np.uint16) for _ in range(3)]
for p in planes:
    p[:, :40] = 300
    p[:, -40:] = 300
    p[60:240, 100:350] = (rng.random((180, 250)) * 20000 + 25000).astype(np.uint16)
fr.mkdir(parents=True)
write_tiff(fr / "f.tif", planes, 2900, icc_profile("ProPhoto RGB"), meta={"taken": datetime.datetime(2000, 1, 1)})
fb = (fr / "f.tif").read_bytes()
res = app.library_autocrop("Roll 009", ["f.tif"], save=True)
e = app._lib_edit_of(fr, "f.tif")
check("auto crop finds the holder and keeps it as a crop", res["found"] == 1 and e and e["crop"][0] > 0.05 and e["crop"][2] < 0.95, str(res))
check("auto crop leaves the file alone", (fr / "f.tif").read_bytes() == fb)
# D's scans: white lines at the top and bottom, black at the sides
y, x = np.mgrid[0:400, 0:600]
wb = [np.clip((np.sin(x / 37) + np.cos(y / 23) + 2) * 12000 + 4000 + rng.random((400, 600)) * 4000, 0, 65535) for _ in range(3)]
for p in wb:
    p[:24] = 62000
    p[-24:] = 62000
    p[:, :30] = 400
    p[:, -30:] = 400
write_tiff(fr / "wb.tif", [p.astype(np.uint16) for p in wb], 2900, icc_profile("ProPhoto RGB"))
res = app.library_autocrop("Roll 009", ["wb.tif"], save=False)
r = res["pictures"][0]["rect"]
check("white top and bottom, black sides: all four cut", r and r[0] >= 0.05 and r[2] <= 0.95 and r[1] >= 0.06 and r[3] <= 0.94, str(res))
plain = base / "Roll 009" / "g.tif"
write_tiff(plain, [(rng.random((300, 450)) * 60000).astype(np.uint16) for _ in range(3)], 2900, icc_profile("ProPhoto RGB"))
res = app.library_autocrop("Roll 009", ["g.tif"], save=True)
check("nothing to cut: no crop", res["found"] == 0 and app._lib_edit_of(fr, "g.tif") is None, str(res))
check("RAW files are not cropped here", not app.library_crop("Roll 002", "RAW/20261102_001_R002.tif", [0.1, 0.1, 0.9, 0.9])["ok"])

print("I re-develop from the RAW, Mac-only buttons")
res = app.library_redevelop("Roll 002", "Positive/20261102_001_R002.tif")
check("the Re-develop page is set on the RAW file", res.get("ok") and app.dev["current"] == "20261102_001_R002.tif"
      and str(app.dev["folder"]).endswith("Roll 002/RAW"), str(res))
check("no RAW: said so", not app.library_redevelop(K, A)["ok"])

print("I2 tag pictures for re-development")
P2 = "Positive/20261102_001_R002.tif"
f2 = app._lib_base() / "Roll 002" / P2
before = f2.read_bytes()
res = app.library_edit("Roll 002", [P2], {"redo": True})
check("tagging works", res["ok"] and res["changed"] == 1 and not res["problems"], str(res))
check("the tag stays in the app's list, the file is not touched", f2.read_bytes() == before)
check("the picture shows the tag", next(p for p in app.library_roll("Roll 002")["pictures"] if p["file"] == P2)["redo"])
check("the folder list counts it", next(r for r in app.library_rolls()["rolls"] if r["key"] == "Roll 002")["redo"] == 1)
app.library_edit(K, [A], {"redo": True})   # a picture without a RAW file can be tagged, but is listed without one
rd = app.library_redo()
r2 = next((r for r in rd["rolls"] if r["key"] == "Roll 002"), None)
check("the Re-develop page's list", rd["ok"] and rd["total"] == 2 and r2 and r2["pictures"][0]["raw"] == "20261102_001_R002.tif"
      and r2["raw_count"] == 1 and r2["path"].endswith("Roll 002/RAW"), str(rd))
check("no RAW: listed without one", any(r["key"] == K and r["raw_count"] == 0 for r in rd["rolls"]), str(rd))
app.library_edit(K, [A], {"redo": False})
check("untagging works", all(r["key"] != K for r in app.library_redo()["rolls"]))
app.develop_folder_now(path=r2["path"])
app.develop_set({"how": {"clean": False}})
app.develop_run(["20261102_001_R002.tif"])
check("developing it takes the tag off", app.library_redo()["total"] == 0 and app.dev["results"].get("untagged") == 1,
      str(app.dev["results"]))
app.library_edit("Roll 002", [P2], {"redo": True})
app.develop_set({"untag": False})
app.develop_run(["20261102_001_R002.tif"])
check("with untag off the tag stays", app.library_redo()["total"] == 1)
app.develop_set({"untag": True})
app.library_edit("Roll 002", [P2], {"redo": False})
if sys.platform != "darwin":
    check("Finder / Lightroom / JPEG say they need the Mac", not app.library_reveal(K, [A])["ok"]
          and not app.library_lightroom(K, [A])["ok"] and not app.library_jpeg(K, A, 2048)["ok"])

print("J the routes")
server = ThreadingHTTPServer(("127.0.0.1", 0), cs.server.make_handler(app))
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}"


def get(path):
    with urllib.request.urlopen(url + path) as r:
        return r.read()


def post(path, body):
    req = urllib.request.Request(url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


check("page served", b"Coolscan library" in get("/library") and b"loadRolls" in get("/library.js"))
check("rolls route", len(json.loads(get("/api/library/rolls"))["rolls"]) == 5)
qs = urllib.parse.urlencode({"key": K, "file": A})
check("picture route", get("/library/small.png?" + qs)[:4] == b"\x89PNG")
check("search route", len(json.loads(get("/api/library/search?q=ilze"))["pictures"]) >= 3)
check("edit route", post("/api/library/edit", {"key": K, "files": [B], "stars": 5})["ok"])
check("re-develop list route", json.loads(get("/api/library/redo"))["ok"])
check("original picture route", get("/library/big.png?" + qs + "&view=original")[:4] == b"\x89PNG")
check("export route", post("/api/library/export", {"key": K, "files": [C]})["ok"])
try:
    get("/api/library/info?" + urllib.parse.urlencode({"key": "..", "file": "x"}))
    check("bad folder route refused", False)
except urllib.error.HTTPError as e:
    check("bad folder route refused", e.code == 404)
server.shutdown()

print()
print("FAILED: " + ", ".join(bad) if bad else "all library checks passed")
sys.exit(1 if bad else 0)
