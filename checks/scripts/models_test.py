"""Other Coolscan models: the model table, the pretend scanner as each model, resolutions moved onto
another scanner's list, Superfine reaching the driver only where the sensor has it, long strips, and
the LS-40 left exactly as it was. Pretend scanner only: no real LS-50/4000/5000/8000/9000 was used."""
import os, sys, tempfile
home = tempfile.mkdtemp(); os.environ["HOME"] = home
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from pathlib import Path
import coolscan_app.app as A
A.STRIP_CACHE = Path(home) / ".coolscan-strip.cache"
A.STRIP_SAMPLES = Path(home) / ".coolscan-strips"
from coolscan_app.app import App
from coolscan_app.scanner import models as M
from coolscan_app.scanner.demo import DEMO_MODELS, _Demo
fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name); ok or fails.append(name)


def app_as(model):
    if model:
        os.environ["COOLSCAN_DEMO_MODEL"] = model
    else:
        os.environ.pop("COOLSCAN_DEMO_MODEL", None)
    a = App(demo=True)
    a.settings["outdir"] = str(Path(home) / "Pictures" / "Coolscan")
    a.connect(); a.session.load()
    return a


# 1. the table
for key in M.MODELS:
    class C: pass
    c = C(); c.model, c.product = key, key + " ED"
    check(f"{key}: known by its model name", M.model_key(c) == key)
    c.model = None
    check(f"{key}: known by its product name alone", M.model_key(c) == key)
class C: model, product = None, "LS-2000"
check("an unknown scanner is not in the table", M.model_key(C()) is None and M.model_info(C()) == {} and M.about(C()) == "")
check("the LS-40 adds no line on connecting", M.about(_Demo().capabilities) == "")
check("a FireWire model says it needs FireWire on a Mac", "ASFireWire" in M.about(_Demo("LS-4000").capabilities))
check("35 mm keeps the page's own estimate", M.frame_long_in("135") is None and M.frame_long_in("") is None)
check("6x7 is 69.5 mm long", abs(M.frame_long_in("67") * 25.4 - 69.5) < 0.01)
check("a custom length in mm", abs(M.frame_long_in("80") * 25.4 - 80) < 0.01 and M.frame_long_in("300") is None)
ls40 = M.dpi_choices(_Demo().capabilities)
check("LS-40 list has 2900 and 290", 2900 in ls40 and 290 in ls40)
ls50 = M.dpi_choices(_Demo("LS-50").capabilities)
check("LS-50 list starts at 4000", ls50[0] == 4000 and 2900 not in ls50)
check("nearest dpi", M.nearest_dpi(2900, ls50) == 2000 and M.nearest_dpi(4000, ls50) == 4000 and M.nearest_dpi(290, ls50) == 286)

# 2. the pretend LS-40 is the one it always was
d = _Demo()
c = d.capabilities
check("pretend LS-40 unchanged", (c.product, c.optical_dpi, c.max_samples, c.framing, c.thumbnail, c.multi_line, d.strip_frames)
      == ("LS-40 ED (demo)", 2900, 16, "perforation", True, False, 6))
a = app_as(None)
check("LS-40: saved resolutions stay", a.settings["dpi"] == 2900 and a.settings["preview_dpi"] == 290)
check("LS-40: no model line, no moved resolution", not any("nkscan has run" in l or "not offered" in l for l in a.log))
a.thumbnail()
st = a.state()
check("LS-40: strip of 6, holder text as before", len(st["frames"]) == 6 and st["caps"]["holder"] == "film strip (up to 6 frames)")
check("LS-40: no frame length sent (35 mm)", st["caps"]["frame_long_in"] is None)
seen = []
orig = a.session.scan_frame
a.session.scan_frame = lambda frame, **kw: (seen.append(kw), orig(frame, **kw))[1]
a.settings.update(dpi=290, superfine=True)
a.scan([0])
check("LS-40: Superfine never reaches the driver (its sensor has no such mode)", seen and all("superfine" not in kw for kw in seen))

# 3. each other model
for model in sorted(DEMO_MODELS):
    a = app_as(model)
    c = a.caps
    a.thumbnail()
    st = a.state()
    n = len(st["frames"])
    check(f"{model}: connected as {c.product}", "(demo)" in c.product and st["caps"]["model_name"])
    check(f"{model}: resolutions on its own list", a.settings["dpi"] in M.dpi_choices(c) and a.settings["preview_dpi"] in M.dpi_choices(c))
    check(f"{model}: frames found ({n})", n == DEMO_MODELS[model].get("frames", 6))
    if n > M.LONG_STRIP:
        check(f"{model}: called a long strip", "long strip" in st["caps"]["holder"])
    seen = []
    orig = a.session.scan_frame
    a.session.scan_frame = lambda frame, _o=orig, **kw: (seen.append(kw), _o(frame, **kw))[1]
    a.settings.update(dpi=M.nearest_dpi(400, M.dpi_choices(c)), superfine=True)
    if c.film_formats:
        a.settings["format"] = "67"
    last = n - 1
    a.scan([last])
    saved = sum(len(x.get("files", [])) for x in a.frame_saved.get(last, []))
    check(f"{model}: frame {last + 1} scanned and saved", saved > 0)
    check(f"{model}: Superfine passed on only where the sensor has it",
          seen and all(("superfine" in kw) == bool(c.multi_line) for kw in seen))
    if c.film_formats:
        check(f"{model}: the 120 format goes to the driver", all(kw.get("format") == "67" for kw in seen))
        check(f"{model}: 6x7 frame length sent to the page", abs(a.state()["caps"]["frame_long_in"] * 25.4 - 69.5) < 0.01)

# 4. saved settings from another scanner move onto this one's list when loaded
a = app_as("LS-5000")
a.settings["presets"]["from LS-40"] = {"dpi": 2900, "preview_dpi": 290}
a.load_preset("from LS-40")
check("preset from an LS-40 on an LS-5000: nearest resolutions", a.settings["dpi"] == 2000 and a.settings["preview_dpi"] == 286)
a = app_as(None)
a.settings["presets"]["mine"] = {"dpi": 1450, "preview_dpi": 290}
a.load_preset("mine")
check("preset on the LS-40: loaded as saved", a.settings["dpi"] == 1450 and a.settings["preview_dpi"] == 290)

print("all passed" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
