"""Export: copies of the Library's pictures for use elsewhere, set up in a dialog laid out like Lightroom's.

Every copy has the picture's crop, turn, roll colour and date taken put in. The pictures themselves
are never changed. What is chosen in the dialog arrives here as one dict of options (see DEFAULTS):

  what to export    the finished pictures, their RAW scans (Archive mode's untouched scans, with the
                    infrared channel), or both; with or without the crop and turn; with or without
                    the roll's colour. The page sends ticked pictures, or asks for whole rolls.
  export location   a chosen folder (roll folders kept or not, an optional subfolder), or the
                    roll's own Edited folder; what to do when a file of that name is there
  file naming       a template of {name} {text} {seq} {date} {roll} {frame}
  file settings     TIFF 16-bit (the default), TIFF 8-bit, PNG, JPEG, HEIC or DNG; colour space
  image sizing      full size, or long edge / width and height / megapixels / percent
  metadata          everything, without people, date and caption only, or none; creator, copyright
  after export      nothing, show in Finder, open in Lightroom

Options are kept under names (presets) and the last ones used, in ~/.coolscan-export.json.
JPEG and HEIC are made by the Mac's own converter (sips) from a TIFF, so they work on a Mac only.
A DNG here is a "linear DNG": the finished picture in linear light with its colour matrix, not the
scanner's unprocessed data.
"""

import datetime
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import zlib
from pathlib import Path

import numpy as np

from .imaging import filmfix
from .imaging.colour import COLOUR_SPACES, _decode_srgb, _encode, _space_matrix, convert_planes, icc_profile
from .imaging.files import read_tiff16, read_tiff_info, write_tiff
from .imaging.tiffmeta import NotOurs, camera_parts, make_xmp, scanner_parts, update_tiff
from .library import CROPPED_FOLDER, _turn_rect, apply_edit, space_of

EXPORT_FILE = ".coolscan-export.json"   # in the home folder: the presets and the options used last
FORMATS = {"tiff16": (".tif", "TIFF 16-bit"), "tiff8": (".tif", "TIFF 8-bit"), "png": (".png", "PNG"),
           "jpeg": (".jpg", "JPEG"), "heic": (".heic", "HEIC"), "dng": (".dng", "DNG")}
SPACES = ("same", "sRGB IEC61966-2.1", "Adobe RGB (1998)", "ProPhoto RGB")
NAMINGS = {"name": "{name}", "text_seq": "{text}-{seq}", "date_seq": "{date}_{seq}", "roll_frame": "{roll}_{frame}",
           "text_name": "{text}_{name}"}
EXPORT_MOST = 20000                      # pictures in one export
DEFAULTS = {
    "source": "positive", "apply_crop": True, "apply_colour": True,
    "where": "folder", "dest": "", "subfolder": "", "keep_rolls": True, "existing": "rename",
    "naming": "name", "template": "{name}", "text": "", "start": 1,
    "format": "tiff16", "quality": 90, "space": "same",
    "resize": "none", "long": 3000, "width": 3000, "height": 3000, "mp": 6.0, "percent": 50, "enlarge": False, "dpi": 0,
    "metadata": "all", "creator": "", "copyright": "",
    "after": "nothing",
}
CHOICES = {"source": ("positive", "raw", "both"), "where": ("folder", "edited"), "existing": ("rename", "skip", "replace"), "naming": tuple(NAMINGS) + ("custom",),
           "format": tuple(FORMATS), "space": SPACES, "resize": ("none", "long", "wh", "mp", "percent"),
           "metadata": ("all", "nopeople", "basic", "none"), "after": ("nothing", "finder", "lightroom")}
BUILT_IN = {   # shown above the user's own presets; cannot be changed or deleted
    "For Lightroom": {"format": "tiff16", "space": "same", "resize": "none", "metadata": "all", "after": "lightroom"},
    "Full size TIFF": {"format": "tiff16", "space": "same", "resize": "none", "metadata": "all"},
    "JPEG to share": {"format": "jpeg", "quality": 85, "space": "sRGB IEC61966-2.1", "resize": "long", "long": 2400,
                      "metadata": "all"},
    "Small JPEG for email": {"format": "jpeg", "quality": 75, "space": "sRGB IEC61966-2.1", "resize": "long", "long": 1600,
                             "metadata": "nopeople"},
}
_BAD_NAME = re.compile(r'[/\\:\0-\x1f]')
_FRAME = re.compile(r"_(\d{2,4})_R\d+", re.I)


def tidy_options(o):
    """Export options with every key there and of the right kind; anything odd becomes the default."""
    o = o if isinstance(o, dict) else {}
    out = {}
    for k, d in DEFAULTS.items():
        v = o.get(k, d)
        if k in CHOICES:
            v = v if v in CHOICES[k] else d
        elif isinstance(d, bool):
            v = bool(v)
        elif isinstance(d, (int, float)):
            try:
                v = type(d)(float(v))
            except (TypeError, ValueError, OverflowError):
                v = d
            if not math.isfinite(v):
                v = d
        else:
            v = " ".join(str(v or "").split())[:300] if k != "dest" else str(v or "").strip()[:1000]
        out[k] = v
    out["start"] = min(999999, max(0, out["start"]))
    out["quality"] = min(100, max(10, out["quality"]))
    for k in ("long", "width", "height"):
        out[k] = min(30000, max(16, out[k]))
    out["mp"] = min(400.0, max(0.01, out["mp"]))
    out["percent"] = min(400, max(1, out["percent"]))
    out["dpi"] = min(20000, max(0, out["dpi"]))
    if out["naming"] != "custom":
        out["template"] = NAMINGS[out["naming"]]
    elif not out["template"]:
        out["template"] = "{name}"
    return out


# ---------------------------------------------------------------------- pixels
def out_size(rows, cols, o):
    """The size (rows, cols) a picture of rows x cols is exported at."""
    how = o["resize"]
    if how == "long":
        f = o["long"] / max(rows, cols)
    elif how == "wh":
        f = min(o["width"] / cols, o["height"] / rows)
    elif how == "mp":
        f = math.sqrt(o["mp"] * 1e6 / (rows * cols))
    elif how == "percent":
        f = o["percent"] / 100
    else:
        return rows, cols
    if f > 1 and not o["enlarge"]:
        return rows, cols
    return max(1, int(round(rows * f))), max(1, int(round(cols * f)))


def _resample_axis(a, n, axis):
    """Array a (float) made n long along an axis: averaged over what each new pixel covers when
    smaller, between neighbours when larger."""
    size = a.shape[axis]
    if n == size:
        return a
    a = np.moveaxis(a, axis, 0)
    if n < size:
        c = np.concatenate([np.zeros((1,) + a.shape[1:], np.float64), np.cumsum(a, axis=0, dtype=np.float64)])
        edges = np.linspace(0, size, n + 1)
        i = np.minimum(edges.astype(np.int64), size - 1)
        frac = (edges - i).reshape((-1,) + (1,) * (a.ndim - 1))
        at = c[i] + frac * a[i]
        out = (at[1:] - at[:-1]) * (n / size)
    else:
        x = (np.arange(n) + 0.5) * size / n - 0.5
        i = np.clip(np.floor(x).astype(np.int64), 0, size - 2) if size > 1 else np.zeros(n, np.int64)
        frac = np.clip(x - i, 0, 1).reshape((-1,) + (1,) * (a.ndim - 1))
        out = a[i] * (1 - frac) + a[np.minimum(i + 1, size - 1)] * frac
    return np.moveaxis(out, 0, axis)


def resize_planes(planes, rows, cols):
    """16-bit planes at a new size."""
    if planes[0].shape == (rows, cols):
        return planes
    out = []
    for p in planes:
        v = _resample_axis(_resample_axis(p.astype(np.float32), rows, 0), cols, 1)
        out.append(np.clip(v + 0.5, 0, 65535).astype(np.uint16))
    return out


def to_space(planes, icc, target):
    """Planes with the colour profile icc, converted into one of the app's colour spaces. Returns the
    planes and the profile to put in the file. A picture whose own colours are not known (no profile)
    is left as it is."""
    if len(planes) < 3 or target not in COLOUR_SPACES:
        return planes, icc
    src = space_of(icc)
    if src == target:
        return planes, icc
    if src is None:
        if not icc:
            return planes, icc
        try:   # a scanner's own profile (Nikon's): read through it
            got, prof = convert_planes(list(planes[:3]), target, scanner_icc=icc)
            return list(got), prof
        except Exception:  # noqa: BLE001 - a profile the app cannot read: the picture stays in it
            return planes, icc
    m = (np.linalg.inv(_space_matrix(target)[0]) @ _space_matrix(src)[0]).astype(np.float32)
    g_in, g_out = COLOUR_SPACES[src][2], COLOUR_SPACES[target][2]
    rows, cols = planes[0].shape
    out = [np.empty((rows, cols), np.uint16) for _ in range(3)]
    step = max(1, 1_000_000 // cols)
    for r0 in range(0, rows, step):
        rgb = np.stack([p[r0:r0 + step].reshape(-1) for p in planes[:3]], axis=1).astype(np.float32) / 65535
        lin = _decode_srgb(rgb) if g_in == "srgb" else np.power(rgb, g_in)
        res = (_encode(lin @ m.T, g_out) * 65535 + 0.5).astype(np.uint16)
        n = res.shape[0] // cols
        for c in range(3):
            out[c][r0:r0 + n] = res[:, c].reshape(n, cols)
    return out, icc_profile(target)


def _linear(planes, space):
    """Planes of a known colour space in linear light (for a DNG)."""
    g = COLOUR_SPACES[space][2]
    out = []
    for p in planes:
        v = p.astype(np.float32) / 65535
        v = _decode_srgb(v) if g == "srgb" else np.power(v, g)
        out.append((np.clip(v, 0, 1) * 65535 + 0.5).astype(np.uint16))
    return out


def _to8(planes):
    return [((p.astype(np.uint32) + 128) // 257).astype(np.uint8) for p in planes]


# ---------------------------------------------------------------------- files
_TYPES = {1: "B", 3: "H", 4: "I", 11: "f", 12: "d"}


def _tag_blob(typ, values):
    if typ in (2, 7):
        blob = values if isinstance(values, bytes) else str(values).encode("ascii", "replace")
        return (blob + b"\0", len(blob) + 1) if typ == 2 else (blob, len(blob))
    if typ in (5, 10):
        return b"".join(struct.pack("<II" if typ == 5 else "<ii", a, b) for a, b in values), len(values)
    values = list(values)
    return struct.pack("<%d%s" % (len(values), _TYPES[typ]), *values), len(values)


def _rational(v, den=1_000_000):
    return int(round(v * den)), den


def write_plain_tiff(path, planes, dpi, icc=None, photometric=None, tags=()):
    """A plain uncompressed TIFF of 8-bit or 16-bit planes, with extra tags [(tag, type, values)].
    (The app's own write_tiff writes the scans; this one writes the 8-bit and DNG exports.)"""
    data = np.dstack(planes) if len(planes) > 1 else planes[0]
    bits = 8 if data.dtype == np.uint8 else 16
    data = np.ascontiguousarray(data.astype("<u2" if bits == 16 else np.uint8))
    rows, cols = data.shape[:2]
    spp = len(planes)
    pixels = data.tobytes()
    if photometric is None:
        photometric = 2 if spp >= 3 else 1
    all_tags = [(254, 4, [0]), (256, 4, [cols]), (257, 4, [rows]), (258, 3, [bits] * spp), (259, 3, [1]),
                (262, 3, [photometric]), (273, 4, [8]), (274, 3, [1]), (277, 3, [spp]), (278, 4, [rows]),
                (279, 4, [len(pixels)]), (282, 5, [(int(dpi), 1)]), (283, 5, [(int(dpi), 1)]), (284, 3, [1]), (296, 3, [2])]
    if icc:
        all_tags.append((34675, 7, bytes(icc)))
    all_tags += [t for t in tags if t[2] not in (None, "", b"")]
    at = 8 + len(pixels) + (len(pixels) % 2)
    tail, entries = bytearray(), []
    for tag, typ, values in sorted(all_tags, key=lambda t: t[0]):
        blob, n = _tag_blob(typ, values)
        if len(blob) <= 4:
            entries.append(struct.pack("<HHI4s", tag, typ, n, blob.ljust(4, b"\0")))
        else:
            entries.append(struct.pack("<HHII", tag, typ, n, at + len(tail)))
            tail.extend(blob)
            if len(tail) % 2:
                tail.append(0)
    ifd = at + len(tail)
    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", ifd))
        f.write(pixels)
        if len(pixels) % 2:
            f.write(b"\0")
        f.write(tail)
        f.write(struct.pack("<H", len(entries)) + b"".join(entries) + b"\0\0\0\0")


def dng_tags(space, spp, model):
    """The tags that make a linear-light TIFF a DNG Lightroom opens: the colour matrix of the space the
    picture is in, white as it is, and a straight tone curve so the picture looks as it does here."""
    tags = [(50706, 1, [1, 4, 0, 0]), (50707, 1, [1, 1, 0, 0]),
            (50708, 2, f"{model or 'Film scanner'} (Coolscan app)"),
            (50714, 3, [0] * spp), (50717, 4, [65535] * spp),
            (50730, 10, [(0, 1)]), (51110, 4, [1])]
    if spp >= 3:
        m = np.linalg.inv(_space_matrix(space)[0])   # XYZ (D50) -> the space's linear RGB
        tags += [(50721, 10, [_rational(float(v)) for v in m.reshape(-1)]), (50778, 3, [23]),   # 23 = D50
                 (50728, 5, [(1, 1)] * 3),
                 (50936, 2, "Coolscan linear"), (50940, 11, [0.0, 0.0, 1.0, 1.0]), (50941, 4, [0])]
    return tags


def _png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path, planes8, dpi, icc=None, xmp=""):
    """An 8-bit PNG with its colour profile, resolution and (if given) XMP."""
    img = np.ascontiguousarray(np.dstack(planes8) if len(planes8) > 1 else planes8[0])
    rows, cols = img.shape[:2]
    bpp = 3 if img.ndim == 3 else 1
    flat = img.reshape(rows, cols * bpp)
    raw = np.empty((rows, cols * bpp + 1), np.uint8)
    raw[:, 0] = 1                                   # "Sub" filter: each byte less the pixel to its left
    raw[:, 1:bpp + 1] = flat[:, :bpp]
    raw[:, bpp + 1:] = flat[:, bpp:] - flat[:, :-bpp]
    per_m = int(round(dpi / 0.0254))
    chunks = [_png_chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, 2 if bpp == 3 else 0, 0, 0, 0))]
    if icc:
        chunks.append(_png_chunk(b"iCCP", b"ICC profile\0\0" + zlib.compress(bytes(icc), 9)))
    chunks.append(_png_chunk(b"pHYs", struct.pack(">IIB", per_m, per_m, 1)))
    if xmp:
        chunks.append(_png_chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + xmp.encode("utf-8")))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)))
    chunks.append(_png_chunk(b"IEND", b""))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"".join(chunks))


def sips_convert(src, target, fmt, quality):
    """A JPEG or HEIC of a TIFF, made by the Mac's own converter. '' when done, else why not."""
    sips = shutil.which("sips")
    if not sips:
        return f"{FORMATS[fmt][1]} is made by the Mac's own converter (sips), which is not on this computer"
    r = subprocess.run([sips, "-s", "format", fmt, "-s", "formatOptions", str(int(quality)), str(src), "--out", str(target)],
                       capture_output=True, timeout=300)
    if r.returncode or not Path(target).is_file() or not Path(target).stat().st_size:
        words = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()
        return f"the Mac's converter could not make the {FORMATS[fmt][1]}" + (f" ({words[-1][:160]})" if words else "")
    return ""


# ---------------------------------------------------------------------- the app's part
class ExportMixin:
    def _exp(self):
        st = self.__dict__.get("_export_state")
        if st is None:
            st = self.__dict__.setdefault("_export_state", {"lock": threading.Lock(), "job": None, "thread": None})
        return st

    # -- presets and the options used last
    @staticmethod
    def _export_file():
        return Path.home() / EXPORT_FILE

    def _export_saved(self):
        try:
            data = json.loads(self._export_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        presets = data.get("presets") if isinstance(data.get("presets"), dict) else {}
        return {"last": data.get("last") if isinstance(data.get("last"), dict) else None,
                "presets": {str(k): v for k, v in presets.items() if isinstance(v, dict)}}

    def _export_save(self, data):
        path = self._export_file()
        tmp = path.with_name(path.name + ".part")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def library_export_options(self):
        """What the Export dialog needs to draw itself."""
        data = self._export_saved()
        last = tidy_options(data["last"] or {"dest": "~/Pictures/Coolscan export"})
        base = tidy_options({})
        built = {}
        for name, v in BUILT_IN.items():   # a built-in preset sets the file, size and metadata; the place and names stay
            built[name] = {k: v.get(k, base[k]) for k in base if k not in ("source", "apply_crop", "apply_colour", "where", "dest", "subfolder", "keep_rolls",
                                                                             "existing", "naming", "template", "text",
                                                                             "start", "creator", "copyright")}
        return {"ok": True, "last": last, "used": data["last"] is not None,
                "presets": {k: tidy_options(v) for k, v in sorted(data["presets"].items())}, "built_in": built,
                "formats": [[k, v[1]] for k, v in FORMATS.items()],
                "spaces": [[s, "As scanned (the picture's own)" if s == "same" else s.replace(" IEC61966-2.1", "")] for s in SPACES],
                "can_convert": bool(shutil.which("sips")), "lightroom": bool(sys.platform == "darwin" and self._lightroom()),
                "mac": sys.platform == "darwin", "edited": CROPPED_FOLDER}

    def library_export_preset(self, action, name, o=None):
        name = " ".join(str(name or "").split())[:60]
        if not name:
            return {"ok": False, "error": "Give the preset a name first."}
        if name in BUILT_IN:
            return {"ok": False, "error": f'"{name}" is a built-in preset. Save yours under another name.'}
        data = self._export_saved()
        if action == "delete":
            if data["presets"].pop(name, None) is None:
                return {"ok": False, "error": "That preset is not there any more."}
            self.say(f'library: export preset "{name}" deleted')
        else:
            new = name not in data["presets"]
            data["presets"][name] = tidy_options(o)
            self.say(f'library: export preset "{name}" ' + ("saved" if new else "updated"))
        self._export_save(data)
        return {"ok": True, "name": name}

    # -- where each picture goes and what it is called
    @staticmethod
    def _export_items(key, files, items):
        if items:
            got = [(str(i[0] or ""), str(i[1] or "")) for i in items if isinstance(i, (list, tuple)) and len(i) == 2]
        else:
            got = [(str(key or ""), str(f)) for f in files or []]
        return got[:EXPORT_MOST]

    def library_export_scope(self, scope):
        """The pictures of whole rolls, for the page's "the whole roll" / "all rolls": [[key, file], ...] or None.
        scope: {"rolls": [folder keys]} or {"all": true}."""
        if not isinstance(scope, dict):
            return None
        keys = [str(k) for k in scope.get("rolls") or []]
        if scope.get("all"):
            keys = [r["key"] for r in self.library_rolls()["rolls"]]
        out = []
        for key in keys:
            out += [[key, p["file"]] for p in self.library_roll(key)["pictures"]]
        return out[:EXPORT_MOST]

    def _export_name(self, o, key, rel, n, digits, taken):
        stem = Path(rel).stem
        m = _FRAME.search(stem)
        seq = str(n).zfill(digits)
        roll = (key.split("/")[0] if key else "") or "Loose"
        words = {"name": stem, "text": o["text"] or "Untitled", "seq": seq, "date": (taken or "")[:10].replace("-", "") or "undated",
                 "roll": roll, "frame": m.group(1) if m else seq}
        name = re.sub(r"\{(\w+)\}", lambda mm: words.get(mm.group(1).lower(), mm.group(0)), o["template"])
        name = _BAD_NAME.sub("-", name).strip(" .")[:150]
        return name or stem

    def _export_plan(self, items, o):
        """For each copy to make: [(key, rel, "positive" or "raw", target Path / None when skipped / words when it cannot be)]"""
        base = self._lib_base()
        dest = None
        if o["where"] == "folder":
            dest = Path(os.path.expanduser(o["dest"]))
            if o["subfolder"]:
                dest = dest.joinpath(*[_BAD_NAME.sub("-", p).strip(" .") or "-" for p in re.split(r"[/\\]", o["subfolder"]) if p.strip(" .")])
        suffix = FORMATS[o["format"]][0]
        digits = max(3, len(str(o["start"] + len(items) - 1)))
        plan, used, n = [], set(), 0
        kinds = {"positive": ("positive",), "raw": ("raw",), "both": ("positive", "raw")}[o["source"]]
        for key, rel in items:
            folder, path = self._lib_file(key, rel)
            is_raw = rel.startswith("RAW/")
            raw = path if is_raw else self._lib_raw_of(folder, path, rel)
            for kind in kinds:
                if kind == "positive" and is_raw:
                    if o["source"] == "positive":
                        plan.append((key, rel, kind, "it has no finished picture, only a RAW scan (choose RAW scans under What to export)"))
                    continue
                if kind == "raw" and raw is None:
                    plan.append((key, rel, kind, "it has no RAW scan (only Archive mode keeps one)"))
                    continue
                src = raw if kind == "raw" else path
                parts = rel.split("/")
                if len(parts) > 1 and parts[0] in ("Positive", "RAW"):
                    parts = parts[1:]          # the finished pictures sit in the roll's own folder in the export
                if dest is None:
                    out_dir = folder / CROPPED_FOLDER
                elif o["keep_rolls"]:
                    out_dir = dest.joinpath(*([p for p in key.split("/") if p] + parts[:-1]))
                else:
                    out_dir = dest
                stem = self._export_name(o, key, rel, o["start"] + n, digits, self._lib_taken_of(folder, rel))
                if kind == "raw":              # RAW scans beside the pictures: a RAW folder in each roll, or _RAW in the name
                    if o["keep_rolls"] and dest is not None:
                        out_dir = out_dir / "RAW"
                    else:
                        stem += "_RAW"
                in_scans = base.resolve() in (out_dir.resolve(), *out_dir.resolve().parents)
                if dest is not None and in_scans and o["naming"] == "name":
                    stem += "_modified"        # among the scans themselves: never the same name as a scan
                target, k = out_dir / f"{stem}{suffix}", 2
                may_replace = o["existing"] == "replace" and (not in_scans or out_dir.name == CROPPED_FOLDER)

                def taken_already(t):
                    return str(t).lower() in used or t.resolve() == src.resolve() or (t.exists() and not may_replace)

                if o["existing"] == "skip" and target.exists() and str(target).lower() not in used:
                    plan.append((key, rel, kind, None))
                    continue
                while taken_already(target):
                    target, k = out_dir / f"{stem} ({k}){suffix}", k + 1
                used.add(str(target).lower())
                plan.append((key, rel, kind, target))
            n += 1
        return plan

    def _export_check(self, o):
        """'' when the options can be used, else what is wrong, in words."""
        if o["where"] == "folder":
            if not o["dest"]:
                return "Choose the folder to export to first."
            dest = Path(os.path.expanduser(o["dest"]))
            if not dest.is_absolute():
                return "Give the whole path of the folder to export to (or use Choose…)."
            if dest.exists() and not dest.is_dir():
                return f"{self._short_path(dest)} is a file, not a folder."
            if not dest.exists() and not dest.parent.is_dir():
                return f"There is no folder {self._short_path(dest.parent)} to make {dest.name} in."
        elif o["source"] != "positive":
            return f"RAW scans are exported to a chosen folder, not into the {CROPPED_FOLDER} folder."
        elif o["format"] != "tiff16":
            return f"The {CROPPED_FOLDER} folder takes TIFF 16-bit only (this app shows what is in it). Choose a folder for other kinds of file."
        if o["format"] in ("jpeg", "heic") and not shutil.which("sips"):
            return f"{FORMATS[o['format']][1]} is made by the Mac's own converter (sips), which is not on this computer."
        return ""

    def library_export_names(self, key, files, o, items=None):
        """The first names an export would give, for the dialog's example line."""
        o = tidy_options(o)
        items = self._export_items(key, files, items)
        why = self._export_check(o)
        if not items:
            return {"ok": True, "names": [], "count": 0, "problem": why}
        t = None
        for first in items[:40]:   # the first picture that can be exported this way
            t = next((x[3] for x in self._export_plan([first], dict(o, existing="rename")) if isinstance(x[3], Path)), None)
            if t is not None:
                break
        if t is None:
            return {"ok": True, "names": [], "count": len(items), "problem": why}
        return {"ok": True, "names": [t.name], "folder": self._short_path(t.parent), "count": len(items), "problem": why}

    # -- one picture
    def _export_meta(self, o, folder, rel, info):
        """(tags for the file's writer, what update_tiff / XMP gets, when taken) at the chosen metadata level."""
        level = o["metadata"]
        m = self._lib_marks(folder)["pictures"].get(rel) or {}
        t = self._lib_taken_of(folder, rel)
        when = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S") if t and level != "none" else None
        meta, xmp = {}, {}
        if level in ("all", "nopeople"):
            meta = {k: info[k] for k in ("description", "make", "model", "iso") if info.get(k)}
            if info.get("coolscan"):
                meta["coolscan"] = {**info["coolscan"], "edited_from": rel}
            xmp = {"caption": m.get("caption") or "", "tags": m.get("tags") or [],
                   "rating": -1 if m.get("flag") == "reject" else int(m.get("stars") or 0),
                   "date_note": m.get("date_note") or ""}
            # the camera the film was in, with the aperture and shutter speed, as a camera writes them
            camera = self._lib_camera(folder)
            if camera:
                stamped = scanner_parts(folder / rel)
                was = (info.get("make") or "", info.get("model") or "")
                xmp["scanner"] = stamped if any(stamped) else (was if was != camera_parts(camera) else ("", ""))
                meta["make"], meta["model"] = camera_parts(camera)
                xmp["camera"] = camera
            xmp["fnumber"] = m.get("aperture") or None
            xmp["exposure"] = m.get("shutter") or ""
            if level == "all":
                xmp["people"] = m.get("people") or []
        elif level == "basic":
            xmp = {"caption": m.get("caption") or ""}
        if when:
            meta["taken"] = when
            xmp["taken"] = when
        if o["creator"]:
            xmp["creator"] = o["creator"]
        if o["copyright"]:
            xmp["rights"] = o["copyright"]
        return meta, xmp, when

    @staticmethod
    def _export_xmp_into(target, xmp):
        if any(xmp.values()):
            update_tiff(target, **xmp)

    def _export_planes(self, o, folder, path, rel):
        """A finished picture with its crop and turn and the roll's colour, as far as each is asked for;
        None when nothing changes it."""
        e = self._lib_edit_of(folder, rel) if o["apply_crop"] else None
        fix = None
        if o["apply_colour"] and (filmfix.read_roll(folder) is not None or self._lib_own_colour(folder, path, rel)):
            fix = self._lib_fix(folder, path, rel)[0]
        if not e and not fix:
            return None
        planes = read_tiff16(path)
        if fix:
            got = filmfix.recolour([p.astype(np.float32) / 65535 for p in planes[:3]], fix["m"].get("space"), fix["m"], fix["numbers"])
            planes = [(np.clip(v, 0, 1) * 65535 + 0.5).astype(np.uint16) for v in got]
        return apply_edit(planes, e) if e else planes

    def _export_raw_planes(self, o, folder, path, rel, raw, info):
        """A RAW scan framed like its finished picture: the crop made when scanning, then the picture's crop
        and turn. None when nothing is asked for or there is nothing to do (the whole frame as scanned)."""
        if not o["apply_crop"]:
            return None
        e = self._lib_edit_of(folder, rel) or {"turns": 0, "crop": None}
        k = e.get("turns") or 0
        rect = [0.0, 0.0, 1.0, 1.0]
        crop = (info.get("coolscan") or {}).get("crop") if raw != path else None
        try:
            if crop and len(crop) == 4 and crop[2] - crop[0] > 0.02 and crop[3] - crop[1] > 0.02:
                pos = read_tiff_info(path)
                # only when the finished picture really is that part of the frame (not a crop-scanned or re-made one)
                if abs((crop[2] - crop[0]) * info["cols"] - pos["cols"]) <= 3 and abs((crop[3] - crop[1]) * info["rows"] - pos["rows"]) <= 3:
                    rect = [float(v) for v in crop]
        except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error):
            pass
        x0, y0, x1, y1 = _turn_rect(rect, k)
        if e.get("crop"):
            a0, b0, a1, b1 = e["crop"]
            x0, y0, x1, y1 = x0 + a0 * (x1 - x0), y0 + b0 * (y1 - y0), x0 + a1 * (x1 - x0), y0 + b1 * (y1 - y0)
        if not k and (x0, y0, x1, y1) == (0, 0, 1, 1):
            return None
        return apply_edit(read_tiff16(raw), {"turns": k, "crop": [x0, y0, x1, y1]})

    def _export_one(self, o, key, rel, target, kind="positive"):
        folder, path = self._lib_file(key, rel)
        marks_rel = rel
        if kind == "raw":
            raw = path if rel.startswith("RAW/") else self._lib_raw_of(folder, path, rel)
            info = read_tiff_info(raw)
            planes = self._export_raw_planes(o, folder, path, rel, raw, info)
            path = raw
        else:
            planes = self._export_planes(o, folder, path, rel)
            info = read_tiff_info(path)
        meta, xmp, when = self._export_meta(o, folder, marks_rel, info)
        icc = info.get("icc")
        fmt = o["format"]
        target.parent.mkdir(parents=True, exist_ok=True)
        same_space = o["space"] == "same" or space_of(icc) == o["space"]
        if planes is None and fmt == "tiff16" and o["resize"] == "none" and same_space and o["metadata"] == "all" and not o["dpi"]:
            shutil.copy2(path, target)     # nothing to change: the file as it is, tags and all
            try:   # a file with another program's metadata keeps it, and its own date
                self._export_xmp_into(target, xmp)
            except (NotOurs, OSError, ValueError, struct.error):
                pass
            return
        if planes is None:
            planes = read_tiff16(path)
        if not (fmt == "tiff16" and o["space"] == "same" and len(planes) == 4):   # a RAW scan as TIFF 16-bit keeps its infrared
            planes = list(planes[:3]) if len(planes) >= 3 else list(planes[:1])
        space = o["space"]
        if fmt == "dng" and len(planes) == 3 and (space if space != "same" else space_of(icc)) not in COLOUR_SPACES:
            space = "ProPhoto RGB"         # a DNG needs colours the app knows: a scanner's own are converted
        if space != "same":
            planes, icc = to_space(planes, icc, space)
        rows, cols = out_size(*planes[0].shape, o)
        planes = resize_planes(planes, rows, cols)
        dpi = o["dpi"] or info.get("dpi") or 2900
        meta["colorspace"] = 1 if icc and b"sRGB" in icc[:600] else 65535
        ascii_tags = [(270, 2, meta.get("description")), (271, 2, meta.get("make")), (272, 2, meta.get("model"))]
        if fmt == "tiff16":
            write_tiff(target, planes, dpi, icc, meta=meta)
            self._export_xmp_into(target, xmp)
        elif fmt == "tiff8":
            write_plain_tiff(target, _to8(planes), dpi, icc, tags=ascii_tags)
            self._export_xmp_into(target, xmp)
        elif fmt == "dng":
            now = space_of(icc)
            if len(planes) == 3 and now not in COLOUR_SPACES:
                raise ValueError("this picture has no colour profile the app knows, so a DNG cannot say what its colours are")
            lin = _linear(planes, now) if len(planes) == 3 else planes
            write_plain_tiff(target, lin, dpi, None, photometric=34892,
                             tags=ascii_tags + dng_tags(now, len(planes), info.get("model")))
            self._export_xmp_into(target, xmp)
        elif fmt == "png":
            write_png(target, _to8(planes), dpi, icc, make_xmp(**xmp) if any(xmp.values()) else "")
        else:   # JPEG, HEIC: from a TIFF with everything in it, by the Mac's converter
            with tempfile.TemporaryDirectory(prefix="coolscan-export-") as tmp:
                src = Path(tmp) / "picture.tif"
                write_tiff(src, planes, dpi, icc, meta=meta)
                self._export_xmp_into(src, xmp)
                why = sips_convert(src, target, fmt, o["quality"])
                if why:   # some converters want 8 bits to start from
                    write_plain_tiff(src, _to8(planes), dpi, icc, tags=ascii_tags)
                    self._export_xmp_into(src, xmp)
                    why = sips_convert(src, target, fmt, o["quality"])
                if why:
                    raise ValueError(why)
        if when and fmt not in ("tiff16",):
            try:
                os.utime(target, (when.timestamp(), when.timestamp()))   # like the scans: the file's date is the date taken
            except (OverflowError, OSError, ValueError):
                pass
        if o["where"] == "edited" and kind == "positive":   # a copy in the roll's Edited folder keeps its stars, tags and caption in the app too
            saved = str(target.relative_to(folder))
            with self.lib_lock:
                marks = self._lib_marks(folder)
                if rel in marks.get("pictures", {}):
                    marks["pictures"][saved] = {k: v for k, v in marks["pictures"][rel].items()
                                                if k not in ("edit", "redo", "rescan", "colour")}
                    self._lib_save_marks(folder, marks)

    # -- the whole export
    def library_export_run(self, key, files, o, items=None, job=None):
        """Export now and return how it went. (The page uses library_export_start, which runs this in the background.)"""
        o = tidy_options(o)
        items = self._export_items(key, files, items)
        job = job if job is not None else {}
        job.update(running=True, done=0, total=len(items), saved=[], skipped=0, problems=[], cancel=job.get("cancel", False),
                   current="", folder="", format=FORMATS[o["format"]][1])
        try:
            why = self._export_check(o) or ("" if items else "Tick the pictures to export first.")
            if why:
                job.update(ok=False, error=why)
                return job
            if o["where"] == "folder":
                Path(os.path.expanduser(o["dest"])).mkdir(exist_ok=True)
            plan = self._export_plan(items, o)
            targets = []
            job["total"] = len(plan)
            for key, rel, kind, target in plan:
                if job.get("cancel"):
                    break
                job["current"] = rel
                if target is None:
                    job["skipped"] += 1
                elif isinstance(target, str):
                    job["problems"].append(f"{rel.rsplit('/', 1)[-1]}: {target}")
                else:
                    try:
                        self._export_one(o, key, rel, target, kind)
                        targets.append(target)
                        job["saved"].append(target.name)
                        job["folder"] = self._short_path(target.parent)
                    except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error, subprocess.SubprocessError,
                            MemoryError) as ex:
                        try:
                            if target.is_file() and not target.stat().st_size:
                                target.unlink()
                        except OSError:
                            pass
                        job["problems"].append(f"{rel}: {getattr(ex, 'strerror', None) or ex}")
                job["done"] += 1
            job.update(ok=True, cancelled=bool(job.get("cancel")), current="")
            if o["where"] == "folder" and targets:
                job["folder"] = self._short_path(Path(os.path.expanduser(o["dest"])))
            data = self._export_saved()
            data["last"] = o
            try:
                self._export_save(data)
            except OSError:
                pass
            self.say(f"library: exported {len(targets)} picture(s) as {FORMATS[o['format']][1]} to {job['folder'] or 'nowhere'}"
                     + (f"; {job['skipped']} skipped (already there)" if job["skipped"] else "")
                     + (f"; {len(job['problems'])} could not be" if job["problems"] else "")
                     + ("; stopped before the end" if job["cancelled"] else ""))
            job["after"] = self._export_after(o, targets)
            if o["where"] == "edited" and targets:
                with self.lock:
                    self.version += 1
        finally:
            job["running"] = False
        return job

    def _export_after(self, o, targets):
        """Show in Finder / open in Lightroom when asked for. '' or what could not be done."""
        if o["after"] == "nothing" or not targets:
            return ""
        if sys.platform != "darwin":
            return "Showing the pictures afterwards works on the Mac only."
        try:
            if o["after"] == "finder":
                subprocess.Popen(["open", "-R", *map(str, targets[:50])])
                return ""
            app = self._lightroom()
            if not app:
                return "Lightroom was not found in Applications, so the pictures were not opened in it."
            subprocess.Popen(["open", "-a", app, *map(str, targets[:500])])
        except OSError as ex:
            return f"Could not open the pictures ({ex.strerror or ex})."
        return ""

    def library_export_start(self, key, files, o, items=None):
        st = self._exp()
        with st["lock"]:
            if st["job"] and st["job"].get("running"):
                return {"ok": False, "busy": True, "error": "An export is running already."}
            o = tidy_options(o)
            got = self._export_items(key, files, items)
            why = self._export_check(o) or ("" if got else "Tick the pictures to export first.")
            if why:
                return {"ok": False, "error": why}
            job = {"running": True, "done": 0, "total": len(got), "saved": [], "skipped": 0, "problems": [], "cancel": False}
            st["job"] = job
            t = threading.Thread(target=self._export_thread, args=(key, files, o, got, job), daemon=True)
            st["thread"] = t
            t.start()
        return {"ok": True, "total": len(got)}

    def _export_thread(self, key, files, o, items, job):
        try:
            self.library_export_run(key, files, o, [list(i) for i in items], job)
        except Exception as ex:  # noqa: BLE001 - the page must hear of it, whatever it was
            job.update(running=False, ok=False, error=f"The export stopped ({ex}).")

    def library_export_again(self, key, files, items=None):
        """Export with the options used last time, without the dialog."""
        last = self._export_saved()["last"]
        if not last:
            return {"ok": False, "error": "Nothing has been exported yet. Use Export… first."}
        return self.library_export_start(key, files, last, items)

    def library_export_status(self):
        job = self._exp()["job"]
        if not job:
            return {"ok": True, "running": False, "none": True}
        out = {k: v for k, v in job.items() if k != "cancel"}
        out["saved"] = len(job.get("saved") or [])
        out["names"] = (job.get("saved") or [])[:3]
        out.setdefault("ok", True)
        return out

    def library_export_cancel(self):
        job = self._exp()["job"]
        if job and job.get("running"):
            job["cancel"] = True
        return {"ok": True}
