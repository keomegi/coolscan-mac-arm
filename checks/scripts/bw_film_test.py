"""B&W film: no dust removal on silver B&W (silver blocks infrared), chromogenic B&W (C-41) is cleaned
and the scanner is told it is colour negative."""
import sys, time, tempfile, pathlib
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
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
    app.settings.update(**{**dict(outdir=str(out), dpi=145, ir=False), **kw})
    app.connect(); app.thumbnail(); return app, out
def logged(app, word): return [l for l in app.log if word in l]

calls = []
real = cs.App.clean_planes
def spy(self, planes, ir, dpi, i, film=None, who=None, method=None):
    calls.append(film or self._film()); return real(self, planes, ir, dpi, i, film, who, method)
cs.App.clean_planes = spy

print("silver B&W with dust removal left on")
app, out = fresh(film="mono", clean=True, dust_method="builtin")
app.scan([0])
check("the cleaner is not run", not calls, str(calls))
check("nothing says dust was removed", not logged(app, "dust removal rebuilt"))
check("a picture is saved", any(out.rglob("*.tif")))
check("the guard in clean_planes itself (re-develop)",
      real(app, [1, 2, 3], object(), 2900, 0, film="mono") == [1, 2, 3] and logged(app, "no dust removal on B&W"))

print("silver B&W in Archive mode")
calls.clear(); app, out = fresh(film="mono", clean=True, archive=True)
app.scan([0])
check("the cleaner is not run", not calls, str(calls))

print("chromogenic B&W (C-41)")
calls.clear(); app, out = fresh(film="chromo", clean=True, dust_method="builtin")
check("the scanner is told colour negative", app._driver_film() == "negative")
app.scan([0])
check("the cleaner runs", calls == ["chromo"], str(calls))
check("a picture is saved", any(out.rglob("*.tif")))
check("other films are told as they are", all(fresh(film=f)[0]._driver_film() == f for f in ("negative", "positive", "kodachrome", "mono")))

print("all passed" if not bad else f"FAILED {bad}")
sys.exit(1 if bad else 0)
