"""Where each frame sits on the strip: fitting the frames to the gaps in the thumbnail, nudging a
frame by hand, and Auto crop's Extra trim and Sides settings."""
import sys, tempfile, pathlib, types
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import numpy as np
import cs_next as cs
from coolscan_app.scanner.strip import Strip, fit_frames

tmp = pathlib.Path(tempfile.mkdtemp())
cs.SETTINGS_FILE = tmp / "settings.json"
cs._Demo._wait = lambda self, progress, phase, steps=10, dt=0: None
bad = []
def check(name, ok, extra=""):
    print(("   ok   " if ok else "   FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok: bad.append(name)

rng = np.random.default_rng(7)
def picture(rows, cols, slide=False):
    """A made-up picture: smooth shapes plus grain, uneven across the film like a real one."""
    y, x = np.mgrid[0:rows, 0:cols] / max(rows, cols)
    a = 0.5 + 0.25 * np.sin(9 * x + rng.uniform(0, 6)) * np.cos(7 * y + rng.uniform(0, 6)) + 0.2 * (y - 0.5)
    a = np.clip(a + rng.normal(0, 0.03, a.shape), 0.05, 0.95)
    v = (a * 30000 + 3000) if not slide else (a * 50000 + 8000)
    return np.repeat(v[..., None], 3, axis=2).astype(np.float32)

def strip(gap, gapw=5, rows=96, pw=130, offset=0.07, slide=False, n=6, noise=0.01):
    parts, spans, x = [], [], 0
    def g():
        return np.full((rows, gapw, 3), gap, np.float32) * (1 + rng.normal(0, noise, (rows, gapw, 1)))
    if gapw:
        parts.append(g()); x += gapw
    for _ in range(n):
        parts.append(picture(rows, pw, slide)); spans.append((x, x + pw)); x += pw
        if gapw:
            parts.append(g()); x += gapw
    S = np.concatenate(parts, 1)
    d = types.SimpleNamespace(thumbnail={k: S[..., j] for j, k in enumerate(("red", "green", "blue"))},
                              column_addresses=[c * 100 for c in range(S.shape[1])], row_addresses=[],
                              frames=[(int((s0 + offset * pw) * 100), 50, int((s1 + offset * pw) * 100), 900)
                                      for s0, s1 in spans])
    return d, spans

def errors(d, spans, fitted):
    return [abs(f[k] / 100 - s[j]) for f, s in zip(fitted or d.frames, spans) for k, j in ((0, 0), (2, 1))]

print("A fitting the frames to the gaps")
for label, kw in [("clear film between negatives", dict(gap=60000)),
                  ("dimmer film base", dict(gap=40000)),
                  ("black between slides", dict(gap=300, slide=True)),
                  ("driver 5% early", dict(gap=60000, offset=-0.05))]:
    d, spans = strip(**kw)
    fitted, note = fit_frames(d)
    e = errors(d, spans, fitted)
    check(f"{label}: every start and end lands on its gap", max(e) <= 1, f"max {max(e):.1f} cols; {note[:60]}")
for hide in ([2], [1, 3]):
    d, spans = strip(gap=60000)
    for h in hide:   # a gap the thumbnail does not show: the picture runs on over it
        s1 = spans[h][1]
        for k in d.thumbnail:
            d.thumbnail[k][:, s1:s1 + 5] = d.thumbnail[k][:, s1 - 5:s1]
    fitted, note = fit_frames(d)
    e = errors(d, spans, fitted)
    check(f"gap after frame {', '.join(str(h + 1) for h in hide)} not visible: placed at the strip's spacing",
          max(e) <= 1, f"max {max(e):.1f} cols")
from coolscan_app.scanner.strip import _by_spacing
check("gaps at an uneven spacing: the missing one is not guessed",
      _by_spacing([(100, 104), (240, 244), None, (520, 524), (640, 644)], 2) == {})
check("at an even spacing it is", _by_spacing([(100, 104), (235, 239), None, (505, 509), (640, 644)], 2) == {2: (370, 374)},
      str(_by_spacing([(100, 104), (235, 239), None, (505, 509), (640, 644)], 2)))
d, spans = strip(gap=0, gapw=0)
fitted, note = fit_frames(d)
check("pictures touching (no gaps): nothing moves", fitted == [tuple(f) for f in d.frames], note)
driver = max(errors(d, spans, None))
for noise in (0.06, 0.1):
    for kw in (dict(gap=60000), dict(gap=40000), dict(gap=300, slide=True)):
        d, spans = strip(noise=noise, **kw)
        fitted, _ = fit_frames(d)
        e = errors(d, spans, fitted)
        check(f"noisy gaps ({noise:.0%}, gap {kw['gap']}): no edge ends up further off than the driver had it",
              max(e) <= 0.07 * 130 + 0.5, f"max {max(e):.1f}")
d, spans = strip(gap=60000, offset=0.3)
fitted, _ = fit_frames(d)
check("a driver far off (30%): no edge moves more than 15% of a frame",
      all(abs(a - b) <= 0.15 * (f[2] - f[0]) for f, g in zip(d.frames, fitted) for a, b in zip(f, g)))
check("no thumbnail: nothing to fit", fit_frames(types.SimpleNamespace(frames=[(0, 0, 10, 10)] * 3, thumbnail=None))[0] is None)

print("B Strip: nudges")
d, spans = strip(gap=60000)
s = Strip(d)
check("Strip passes the driver's other values through", s.column_addresses is d.column_addresses)
s.shift[2] = 3
w = s.windows(False)
f = d.frames[2]
check("3 presses move a frame 3% of its length later", w[2] == (f[0] + 390, f[1], f[2] + 390, f[3]), str((w[2], f)))
check("other frames stay", [w[i] for i in (0, 1, 3)] == [d.frames[i] for i in (0, 1, 3)])
s.shift = {5: 25}
w = s.windows(False)
check("a nudge never goes past the end of the strip", w[5][2] <= d.column_addresses[-1], str(w[5]))
s.fitted, _ = fit_frames(d)
s.shift = {}
check("fit on uses the fitted windows, off the driver's", s.windows(True) == s.fitted and s.windows(False) == s.found)

print("C in the app (pretend scanner)")
app = cs.App(demo=True)
app.settings.update(outdir=str(tmp / "out"), dpi=290, ir=False, clean=False, scan_log=False)
app.connect(); app.thumbnail()
d = app.discovery
check("the discovery is wrapped", isinstance(d, Strip) and d.frames == d.found)
cs.STRIP_SAMPLES = tmp / "strips"
cs._keep_thumbnail(d)
kept = sorted(cs.STRIP_SAMPLES.glob("strip-*.npz"))
z = np.load(kept[0]) if kept else {}
check("the thumbnail pass is kept as a sample (planes, frames, addresses)",
      kept and "plane_red" in z and len(z["frames"]) == len(d.found) and len(z["column_addresses"]) > 0,
      str(list(z.keys()) if kept else "none"))
before = d.frames[1]
r = app.nudge_frame(1, 2)
check("nudge moves frame 2's window 2% later", d.frames[1][0] - before[0] == round(0.02 * (before[2] - before[0])), str((before, d.frames[1])))
check("the page is told", r.get("shift") == 2 and r.get("moved") == 2 and app.state()["tiles"][1]["window"]["shift"] == 2, str(r))
check("its tile is the thumbnail of the new window", "window moved" in app.preview_info.get(1, ""), app.preview_info.get(1, ""))
calls = []
real = app.session.scan_frame
app.session.scan_frame = lambda frame, *a, **kw: (calls.append(tuple(frame)), real(frame, *a, **kw))[1]
app.preview([1])
check("a pass over the frame reads the moved window", calls and calls[-1] == tuple(d.frames[1]), str(calls[-1:]))
app.nudge_frame(1, 1000)
check("the presses are capped at 25%", d.shift[1] == 25, str(d.shift))
app.nudge_frame(1, reset=True)
check("reset puts it back", d.frames[1] == before and 1 not in d.shift)
app.settings["frame_fit"] = True
app.frame_fit_setting_changed()
check("fit on without gaps in the thumbnail leaves the frames where the driver put them", d.frames == d.found, d.fit_note)

print("D Auto crop: Extra trim and Sides")
app.settings.update(auto_crop_trim=0.02, auto_crop_sides="all")
check("Extra trim cuts 2% further in on each side that was cut",
      app._auto_crop_options((0.05, 0.0, 0.95, 0.9)) == (0.07, 0.0, 0.93, 0.88))
app.settings.update(auto_crop_trim=0.5)
check("Extra trim is at most 3%", app._auto_crop_options((0.05, 0.05, 0.95, 0.95)) == (0.08, 0.08, 0.92, 0.92))
app.settings.update(auto_crop_trim=0, auto_crop_sides="ends")
check("Sides = left and right only keeps the full height", app._auto_crop_options((0.05, 0.1, 0.95, 0.9)) == (0.05, 0.0, 0.95, 1.0))
check("with only top/bottom found and Sides = ends there is nothing to crop", app._auto_crop_options((0.0, 0.1, 1.0, 0.9)) is None)
check("nothing found stays nothing", app._auto_crop_options(None) is None)

print()
print(f"{len(bad)} FAILED" if bad else "all passed")
sys.exit(1 if bad else 0)
