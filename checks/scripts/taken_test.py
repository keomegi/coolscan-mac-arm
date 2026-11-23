import sys, os, time, datetime, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import tifffile
import cs_next as cs
cs.SETTINGS_FILE = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)
T = cs.App._taken
now = datetime.datetime(2026, 11, 22, 20, 59, 12, 345678)
check("only a year: today's day and time in that year", T("2005", 7, now) == datetime.datetime(2005, 11, 22, 20, 59, 12), str(T("2005", 7, now)))
check("a full date is kept: noon, one second per frame", T("2005-06-14", 7, now) == datetime.datetime(2005, 6, 14, 12, 0, 7))
check("year and month: first of the month, as before", T("2005.06", 3, now) == datetime.datetime(2005, 6, 1, 12, 0, 3))
check("29 February scanned, year without one: 28 February", T("2005", 1, datetime.datetime(2028, 2, 29, 9, 30, 0)) == datetime.datetime(2005, 2, 28, 9, 30, 0))
check("29 February scanned, leap year typed: stays", T("2004", 1, datetime.datetime(2028, 2, 29, 9, 30, 0)) == datetime.datetime(2004, 2, 29, 9, 30, 0))
for wrong in ("abcd", "0", "2005-13-01", "20050614x"):
    try:
        T(wrong, 1, now); ok = False
    except (ValueError, IndexError, OverflowError):
        ok = True
    check(f"nonsense {wrong!r} is refused", ok)

app = cs.App(demo=True); out = pathlib.Path(tempfile.mkdtemp())
app.settings.update(outdir=str(out), dpi=145, photo_date="2005", archive=True, basename="")
app.connect(); app.thumbnail()
before = datetime.datetime.now().replace(microsecond=0)
app.scan([0, 1])
after = datetime.datetime.now()
files = sorted(out.rglob("*.tif"))
stamps = {}
for f in files:
    with tifffile.TiffFile(str(f)) as t:
        page = t.pages[0]
        exif = page.tags["ExifTag"].value
        stamps[str(f.relative_to(out))] = (page.tags["DateTime"].value, exif["DateTimeOriginal"], exif["DateTimeDigitized"],
                                           datetime.datetime.fromtimestamp(f.stat().st_mtime))
for k, v in stamps.items(): print("     ", k, v[0], "| file time", v[3])
def parse(s): return datetime.datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
ok = all(parse(v[0]) == parse(v[1]) == parse(v[2]) == v[3].replace(microsecond=0) for v in stamps.values())
check("EXIF date, date taken, date digitised and the file's own time agree", ok)
ok = all(before.replace(year=2005) <= parse(v[0]) <= after.replace(year=2005) for v in stamps.values())
check("they are the moment of the scan with the year 2005", ok)
frames = {}
for k, v in stamps.items(): frames.setdefault(k.split("_")[1], set()).add(v[0])
check("RAW, IR and Positive of one frame share one time", all(len(s) == 1 for s in frames.values()) and len(frames) == 2, str(frames))
check("two frames scanned within the same second still get different times, in order", max(frames["001"]) < min(frames["002"]), str(frames))
check("the name still carries today's scan date", all(pathlib.Path(k).name.startswith(time.strftime("%Y%m%d")) for k in stamps))
check("the scan date stays in the description", "Scanned " + time.strftime("%Y-%m-%d") in tifffile.TiffFile(str(files[0])).pages[0].tags["ImageDescription"].value)
app.settings["photo_date"] = ""; app.scan([2])
f = sorted(out.rglob("*003_R001.tif"))[0]
with tifffile.TiffFile(str(f)) as t:
    check("no photo date: no made-up date in the file", "DateTime" not in t.pages[0].tags)
print(); print("FAILED: " + ", ".join(bad) if bad else "all passed")
