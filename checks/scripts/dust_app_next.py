import sys, time, tempfile, pathlib, os, stat
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np, tifffile
import cs_next as cs
tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None

def fresh(**kw):
    app = cs.App(demo=True)
    out = pathlib.Path(tempfile.mkdtemp())
    app.settings.update(**{**dict(outdir=str(out), dpi=1450, name_date=False, basename="Scan"), **kw})
    app.connect(); app.thumbnail()
    return app, out

def dust_left(path, raw_path=None):
    """Mean brightness at the pretend dust marks relative to their surroundings in a saved picture."""
    a = tifffile.imread(str(path)).astype(np.float64)[..., :3]
    rows, cols = a.shape[:2]
    size = max(1, 1450 // 500)
    y, x = rows // 3, slice(cols // 4, cols // 2)
    scratch = a[y:y + size, x].mean(); around = (a[y - 6:y - 3, x].mean() + a[y + size + 3:y + size + 6, x].mean()) / 2
    return scratch / around

print("1 archive, built-in, one pass")
app, out = fresh(archive=True)
calls = {"n": 0}; real = app.session.scan_frame
def counting(frame, **kw):
    calls["n"] += 1; return real(frame, **kw)
app.session.scan_frame = counting
app.scan([0])
print("   passes:", calls["n"], "| files:", sorted(str(f.relative_to(out)) for f in out.rglob("*.tif")))
roll = next(out.glob("Roll*"))
print("   scratch brightness vs surroundings: RAW %.2f (dust is %s there), Positive %.2f" % (
    dust_left(roll / "RAW/Scan_001_R001.tif"), "dark", dust_left(roll / "Positive/Scan_001_R001.tif")))
print("   " + "\n   ".join(l[9:] for l in app.log if "dust" in l))

print("2 archive, driver second pass (old way)")
app, out = fresh(archive=True, dust_method="pass")
calls["n"] = 0; real2 = app.session.scan_frame
def counting2(frame, **kw):
    calls["n"] += 1; return real2(frame, **kw)
app.session.scan_frame = counting2
app.scan([0]); print("   passes:", calls["n"])

print("3 ICE helper")
helper = cs.ICE_HELPER
print("   helper path:", helper, "| offered:", app.state()["ice_helper"])
helper.write_text(f"""#!{sys.executable}
import sys, numpy as np, tifffile
if sys.argv[1:] == ['--status']: sys.exit(0)
a = tifffile.imread(sys.argv[1]); assert a.shape[2] == 4 and a.dtype == np.uint16, a.shape
print('args', sys.argv[3:], file=sys.stderr)
rgb = a[..., :3].copy(); rgb[a[..., 3] < 20000] = 12345      # mark what it "cleaned"
tifffile.imwrite(sys.argv[2], rgb, photometric='rgb')
""")
helper.chmod(0o755)
try:
    app, out = fresh(archive=True, dust_method="ice"); app.check_ice()
    print("   offered now:", app.state()["ice_helper"], "| method:", app._dust_method())
    app.scan([0])
    roll = next(out.glob("Roll*"))
    raw = tifffile.imread(str(roll / "RAW/Scan_001_R001.tif"))
    print("   RAW still has dust (untouched):", bool((raw[..., 3] < 20000).any()), "|", [l[9:] for l in app.log if "ICE" in l])
    app, out = fresh(dust_method="ice")          # not archive
    app.scan([0]); print("   not archive:", [l[9:] for l in app.log if "ICE" in l or "saved" in l])
    helper.write_text("#!/bin/sh\n[ \"$1\" = --status ] && exit 0\necho 'wine: cannot find ICEDLL.dll' >&2\nexit 3\n")
    app, out = fresh(archive=True, dust_method="ice"); app.scan([0])
    print("   helper fails:", [l[9:] for l in app.log if "ICE" in l or "rebuilt" in l])
    helper.write_text("#!/bin/sh\n[ \"$1\" = --status ] && exit 0\nsleep 5\n"); cs.ICE_TIMEOUT = 1
    app, out = fresh(archive=True, dust_method="ice"); app.scan([0])
    print("   helper hangs:", [l[9:] for l in app.log if "ICE" in l or "rebuilt" in l])
finally:
    helper.unlink()
app, out = fresh(dust_method="ice"); print("   helper gone -> method:", app._dust_method())

print("4 read_tiff_rgb16 on big-endian and planar files")
rgb = (np.random.default_rng(0).integers(0, 65535, (40, 50, 3))).astype(np.uint16)
for kw in (dict(byteorder=">"), dict(planarconfig="separate"), dict(rowsperstrip=7)):
    f = tmp / "t.tif"
    data = rgb if "planarconfig" not in kw else np.moveaxis(rgb, 2, 0)
    tifffile.imwrite(str(f), data, photometric="rgb", **kw)
    back = np.dstack(cs.read_tiff_rgb16(f)); print("   ", kw, "ok" if (back == rgb).all() else "WRONG")
