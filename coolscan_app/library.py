"""Library: the scans already made, folder by folder, to look through and tidy up.

The page (/library) lists the folders in the scans folder (settings "outdir"): every roll folder,
any other folder with pictures in it, and the scans folder itself when pictures were saved there
without a roll. In one folder it shows the description (Description.txt, which can be edited
here), the pictures as small pictures, and one picture big with what the file says about itself.

A picture's crop can be changed in two ways, and neither overwrites anything:
  * from its RAW file (Archive mode keeps the whole frame): the Re-develop page opens on it, so
    the crop can be made larger as well as smaller; it saves into <folder>/Re-developed/<date time>/
  * from the picture itself: only smaller, saved as a copy in <folder>/Cropped/ with the same
    tags (dates, film speed, colour profile).

Small pictures are kept in a cache folder (~/Library/Caches/Coolscan), so a folder opens quickly
the second time. A changed file gets a new small picture.
"""

import datetime
import hashlib
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
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .imaging import filmfix
from .imaging.autocrop import find_picture
from .imaging.colour import COLOUR_SPACES, film_profile_from, frame_colour, shrink, to_srgb8
from .imaging.files import encode_png, read_tiff16, read_tiff_info, write_tiff
from .imaging.tiffmeta import NotOurs, camera_parts, read_aperture, read_scanner, read_shutter, update_tiff
from .rolls import COLOUR_FILE, DESCRIPTION_FILE

CROPPED_FOLDER = "Edited"          # cropped and turned copies
META_FILE = "Library.json"         # stars, flags, tags, captions and changed dates, kept in each folder
JPEG_FOLDER = "JPEG"
WRITE_FILES = True                 # also write tags, captions, stars and dates into the picture files (for Lightroom)
DEVELOPED_FOLDER = "Re-developed"
SMALL_SIDE = 360          # the small pictures in a folder's grid
BIG_SIDE = 1600           # the picture on show
PICTURES_MOST = 2000      # per folder
SKIP_FOLDERS = {"RAW", "Positive", DEVELOPED_FOLDER, CROPPED_FOLDER, JPEG_FOLDER, "Cropped", "Tests", "Earlier rolls"}
_SIDE_FILE = re.compile(r"_(IR|negative)\.tiff?$", re.I)
_TIF = (".tif", ".tiff")


def _cache_dir():
    lib = Path.home() / "Library" / "Caches"
    return (lib if lib.is_dir() else Path.home() / ".cache") / "Coolscan" / "library"


def _is_picture(name):
    return name.lower().endswith(_TIF) and not name.startswith(".") and not _SIDE_FILE.search(name)


def _tifs(folder):
    try:
        return sorted(e.name for e in os.scandir(folder) if e.is_file() and _is_picture(e.name))
    except OSError:
        return []


def icc_name(icc):
    """The description written inside a colour profile ('ProPhoto RGB', 'Nikon LS-40 ...'), or ''."""
    try:
        count = struct.unpack(">I", icc[128:132])[0]
        for k in range(min(count, 100)):
            sig, off, size = struct.unpack(">4sII", icc[132 + 12 * k:144 + 12 * k])
            if sig != b"desc":
                continue
            blob = icc[off:off + size]
            if blob[:4] == b"desc":
                n = struct.unpack(">I", blob[8:12])[0]
                return blob[12:12 + n].split(b"\0", 1)[0].decode("latin-1").strip()
            if blob[:4] == b"mluc":
                recs, rsize = struct.unpack(">II", blob[8:16])
                if recs:
                    ln, lo = struct.unpack(">II", blob[20:28])
                    return blob[lo:lo + ln].decode("utf-16-be", "replace").strip("\0 ")
    except (struct.error, TypeError, IndexError):
        pass
    return ""


def space_of(icc):
    """Which of the app's colour spaces a profile is, or None (a scanner profile, or none at all)."""
    name = icc_name(icc) if icc else ""
    low = name.lower()
    for key in COLOUR_SPACES:
        k = key.lower()
        if low == k or low.startswith(k) or k.startswith(low) and low:
            return key
    for key in COLOUR_SPACES:  # looser: "ProPhoto" in "ProPhoto RGB (Coolscan)", "Adobe RGB (1998)" ...
        if key.split()[0].lower() in low:
            return key
    return None


def _layout(path):
    """Where a TIFF's pixels are: (rows, cols, spp, bits, planar, offset of contiguous pixels or None, order)."""
    with open(path, "rb") as f:
        head = f.read(8)
        order = {b"II": "<", b"MM": ">"}.get(head[:2])
        if order is None or struct.unpack(order + "H", head[2:4])[0] != 42:
            raise ValueError("not a TIFF file")
        f.seek(struct.unpack(order + "I", head[4:8])[0])
        count = struct.unpack(order + "H", f.read(2))[0]
        raw = f.read(12 * count)
        tags = {}
        for k in range(count):
            tag, typ, n = struct.unpack(order + "HHI", raw[12 * k:12 * k + 8])
            if typ not in (3, 4):
                continue
            code, size = ("H", 2) if typ == 3 else ("I", 4)
            val = raw[12 * k + 8:12 * k + 12]
            if size * n > 4:
                if size * n > 1 << 20:
                    continue
                f.seek(struct.unpack(order + "I", val)[0])
                val = f.read(size * n)
            tags[tag] = struct.unpack(order + code * n, val[:size * n])
    cols, rows = tags[256][0], tags[257][0]
    spp = tags.get(277, (1,))[0]
    bits = set(tags.get(258, (1,)))
    if tags.get(259, (1,))[0] != 1 or len(bits) != 1 or bits.pop() not in (8, 16):
        raise ValueError("compressed, or not 8 or 16 bits")
    bits = tags[258][0] if 258 in tags else 1
    offs, counts = tags.get(273), tags.get(279)
    start = None
    if offs and counts and all(offs[i] + counts[i] == offs[i + 1] for i in range(len(offs) - 1)):
        start = offs[0]
    return rows, cols, spp, bits, tags.get(284, (1,))[0], start, order, tags


def read_small(path, side):
    """A TIFF's planes made small (longer side about `side`), reading as little of the file as it can.
    Returns (planes as float32 0..1, the full size (cols, rows))."""
    rows, cols, spp, bits, planar, start, order, _ = _layout(path)
    f = max(1, math.ceil(max(rows, cols) / side))
    dtype = np.dtype(order + ("u2" if bits == 16 else "u1"))
    top = 65535.0 if bits == 16 else 255.0
    if start is not None and os.path.getsize(path) >= start + rows * cols * spp * dtype.itemsize:
        shape = (spp, rows, cols) if planar == 2 else (rows, cols, spp)
        a = np.memmap(path, dtype=dtype, mode="r", offset=start, shape=shape)
        planes = [a[k] for k in range(spp)] if planar == 2 else [a[..., k] for k in range(spp)]
        out = []
        r = rows // f * f
        c = cols // f * f
        for p in planes[:3]:
            if f <= 1:
                out.append(np.asarray(p, dtype=np.float32) / top)
                continue
            if f <= 4:  # the picture on show: every pixel, averaged in blocks
                small = np.asarray(p[:r, :c], dtype=np.float32).reshape(r // f, f, c // f, f).mean(axis=(1, 3))
            else:  # a small picture: every f-th line (that is most of the reading saved), averaged across
                lines = np.asarray(p[f // 2:r:f, :c], dtype=np.float32)
                small = lines.reshape(lines.shape[0], c // f, f).mean(axis=2)
            out.append(small / top)
        del a
        return out, (cols, rows)
    planes = read_tiff16(path)[:3]
    return [shrink(p, side).astype(np.float32) / 65535.0 for p in planes], (cols, rows)


def _stretch8(planes, negative=False):
    """A picture as the scanner read it (no colour space): stretched to be seen. A negative is turned
    round roughly, each colour on its own, so it can be recognised (the real develop is on Re-develop)."""
    out = []
    if negative:
        ends = [(float(np.percentile(p, 0.5)), float(np.percentile(p, 99.5))) for p in planes]
    else:
        both = (min(float(np.percentile(p, 0.5)) for p in planes), max(float(np.percentile(p, 99.5)) for p in planes))
        ends = [both] * len(planes)
    for p, (lo, hi) in zip(planes, ends):
        v = np.clip((p - lo) / max(hi - lo, 1e-6), 0, 1)
        v = (1 - v) ** 1.6 if negative else v ** (1 / 2.2)
        out.append((v * 255 + 0.5).astype(np.uint8))
    return np.dstack(out) if len(out) > 1 else out[0]


def picture_png(path, side, edit=None, crop=True, fix=None, as_scanned=False):
    """A picture file as a PNG for the page, colours right for the screen. A file as scanned (a RAW or
    negative file, which carries the app's scan note) is stretched to be seen, like on the Re-develop page.
    edit: the crop and turn kept in the Library (shown, never written into the file).
    fix: the roll's film colour put onto the picture (filmfix), also never written into the file.
    as_scanned: a RAW file the way the scanner read it, all colours stretched alike (a negative stays
    orange), for picking the film base."""
    planes, _ = read_small(path, side)
    if fix:
        planes = filmfix.recolour(planes, fix["m"].get("space"), fix["m"], fix["numbers"])
    if as_scanned:
        return encode_png(np.ascontiguousarray(_stretch8(planes[:3], False)))
    planes = apply_edit(planes, edit, crop)
    try:
        info = read_tiff_info(path)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        info = {}
    note = info.get("coolscan") or {}
    if note:
        img = _stretch8(planes, note.get("film") in ("negative", "mono", "chromo"))
    elif len(planes) == 3:
        space = space_of(info.get("icc"))
        img = to_srgb8([(np.clip(p, 0, 1) * 65535 + 0.5).astype(np.uint16) for p in planes],
                       space or "scanner")  # an unknown space is shown as it is
    else:
        img = (np.clip(planes[0], 0, 1) * 255 + 0.5).astype(np.uint8)
    return encode_png(np.ascontiguousarray(img))


def _turn_rect(rect, k):
    """A crop (x0, y0, x1, y1 fractions) of a picture, on the picture turned k quarters to the left."""
    x0, y0, x1, y1 = rect
    for _ in range(k % 4):
        x0, y0, x1, y1 = y0, 1 - x1, y1, 1 - x0
    return [round(v, 4) for v in (x0, y0, x1, y1)]


def tidy_edit(e):
    """A picture's crop and turn as kept in Library.json: {"turns": 0..3 quarters left, "crop": rect on
    the turned picture or None}, or None when there is nothing to change."""
    if not isinstance(e, dict):
        return None
    try:
        k = int(e.get("turns") or 0) % 4
    except (TypeError, ValueError):
        k = 0
    crop = e.get("crop")
    if crop is not None:
        try:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in crop)
        except (TypeError, ValueError):
            crop = None
        else:
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            crop = None if x1 - x0 < 0.05 or y1 - y0 < 0.05 or (x0, y0, x1, y1) == (0, 0, 1, 1) else \
                [round(v, 4) for v in (x0, y0, x1, y1)]
    return {"turns": k, "crop": crop} if (k or crop) else None


def apply_edit(planes, e, crop=True):
    """Planes (2-D arrays) turned and cut as the edit says."""
    if not e:
        return planes
    k = e.get("turns") or 0
    if k:
        planes = [np.rot90(p, k) for p in planes]
    if crop and e.get("crop"):
        rows, cols = planes[0].shape
        x0, y0, x1, y1 = e["crop"]
        r0, r1 = int(round(y0 * rows)), int(round(y1 * rows))
        c0, c1 = int(round(x0 * cols)), int(round(x1 * cols))
        planes = [p[r0:max(r1, r0 + 1), c0:max(c1, c0 + 1)] for p in planes]
    return [np.ascontiguousarray(p) for p in planes]


def _words_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


class LibraryMixin:
    def _lib_reset(self):
        self.lib_big = OrderedDict()      # (path, mtime, size) -> PNG of the picture on show, the last few
        self.lib_lock = threading.Lock()
        self.lib_taken = {}               # (path, mtime, size) -> "YYYY-MM-DD HH:MM:SS" or "" (read from the file's tags)

    # ------------------------------------------------------------------ folders
    def _lib_base(self):
        return Path(os.path.expanduser(self.settings["outdir"]))

    def _lib_folder(self, key):
        """A folder of the library from the page's key (its path inside the scans folder; "" = the scans
        folder itself). Never anything outside the scans folder."""
        base = self._lib_base()
        key = str(key or "").strip("/")
        if any(part in ("..", ".", "") for part in key.split("/")) and key or "\\" in key or "\0" in key:
            raise ValueError("bad folder")
        folder = base / key if key else base   # ".." is refused above, so this stays inside the scans folder
        if not folder.is_dir():
            raise ValueError("no such folder")
        return folder

    def _lib_file(self, key, rel):
        """A file in a library folder, by its path inside that folder."""
        folder = self._lib_folder(key)
        rel = str(rel or "")
        if not rel or rel.startswith("/") or any(p in ("..", ".", "") for p in rel.split("/")) or "\\" in rel or "\0" in rel:
            raise ValueError("bad file")
        path = folder / rel   # ".." is refused above (a picture may be a link to a file elsewhere)
        if not path.is_file():
            raise ValueError("no such file")
        return folder, path

    @staticmethod
    def _lib_pictures_dir(folder):
        """Where a folder's finished pictures are: Positive/ in Archive mode, else the folder itself."""
        pos = folder / "Positive"
        return pos if pos.is_dir() and _tifs(pos) else folder

    @staticmethod
    def _lib_pictures(folder):
        """A folder's finished pictures as (file, name), in name order: those in Positive/ (Archive mode)
        and those in the folder itself. A roll scanned partly with Archive mode and partly without has
        both; a picture that is in both places is listed once, from Positive/."""
        pos = folder / "Positive"
        out = {n: "Positive/" + n for n in _tifs(pos)} if pos.is_dir() else {}
        stems = {n.rsplit(".", 1)[0] for n in out}
        for n in _tifs(folder):
            if n.rsplit(".", 1)[0] not in stems:
                out[n] = n
        return [(out[n], n) for n in sorted(out)]

    def _lib_summary(self, folder, key, name):
        both = self._lib_pictures(folder)
        pics = [n for _, n in both]
        raw = folder / "RAW"
        stems = {n.rsplit(".", 1)[0] for n in pics} | ({n.rsplit(".", 1)[0] for n in _tifs(raw)} if raw.is_dir() else set())
        desc = ""
        try:
            desc = (folder / DESCRIPTION_FILE).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
        times = []
        for rel, _ in (both[:1] + both[-1:]):
            try:
                times.append((folder / rel).stat().st_mtime)
            except OSError:
                pass
        allm = self._lib_marks(folder)["pictures"].values()
        rescan = sum(1 for m in allm if m.get("rescan"))
        return {"key": key, "name": name, "count": len(stems), "raw": raw.is_dir() and bool(_tifs(raw)), "rescan": rescan,
                "description": desc.split("\n", 1)[0][:160],
                "first": time.strftime("%Y-%m-%d", time.localtime(min(times))) if times else None,
                "last": time.strftime("%Y-%m-%d", time.localtime(max(times))) if times else None,
                "current": bool(self.settings["use_roll"]) and name == self.settings["roll"]}

    def library_rolls(self):
        """Every folder with pictures in the scans folder: roll folders first (by number), then the rest."""
        base = self._lib_base()
        out = []
        if not base.is_dir():
            return {"ok": True, "base": self._short_path(base), "rolls": [], "missing": True}
        try:
            subs = [e.name for e in os.scandir(base) if e.is_dir() and not e.name.startswith(".")]
        except OSError:
            subs = []
        for name in subs:
            if name in SKIP_FOLDERS:
                continue
            folder = base / name
            is_roll = bool(re.match(r"Roll \d+", name))
            if not is_roll and not _tifs(self._lib_pictures_dir(folder)) and not (folder / "RAW").is_dir():
                continue
            out.append(self._lib_summary(folder, name, name))
        out.sort(key=lambda r: (0 if re.match(r"Roll \d+", r["name"]) else 1, self._roll_number(r["name"]), r["name"].lower()))
        loose = self._lib_summary(base, "", "Loose pictures")
        if loose["count"] or loose["raw"]:
            loose["loose"] = True
            out.insert(0, loose)
        return {"ok": True, "base": self._short_path(base), "rolls": out}

    def library_roll(self, key):
        """One folder: its description, its pictures and what else is in it."""
        folder = self._lib_folder(key)
        raw_dir = folder / "RAW"
        raws = set(_tifs(raw_dir)) if raw_dir.is_dir() else set()
        versions = self._lib_versions(folder)
        pictures = []
        for rel, name in self._lib_pictures(folder)[:PICTURES_MOST]:
            stem = name.rsplit(".", 1)[0]
            pics_dir = (folder / rel).parent
            try:
                st = (folder / rel).stat()
            except OSError:
                continue
            raw = name if name in raws else next((r for r in raws if r.rsplit(".", 1)[0] == stem), None)
            pictures.append({"file": rel, "name": name, "bytes": st.st_size, "v": int(st.st_mtime),
                             "raw": ("RAW/" + raw) if raw else None,
                             "ir": (pics_dir / f"{stem}_IR.tif").is_file() or (raw_dir / f"{stem}_IR.tif").is_file(),
                             "versions": versions.get(stem, [])})
        # a RAW file with no finished picture beside it is still a picture of the roll
        if raws:
            have = {p["name"].rsplit(".", 1)[0] for p in pictures}
            for raw in sorted(raws):
                stem = raw.rsplit(".", 1)[0]
                if stem not in have:
                    st = (raw_dir / raw).stat()
                    pictures.append({"file": "RAW/" + raw, "name": raw, "bytes": st.st_size, "v": int(st.st_mtime),
                                     "raw": "RAW/" + raw, "ir": True, "versions": versions.get(stem, []), "only_raw": True})
            pictures.sort(key=lambda p: p["name"])
        marks = self._lib_marks(folder)
        colour = self._lib_colour_state(folder)
        fc = colour["stamp"] if colour["numbers"] else ""
        own_frames = {}
        try:
            own_frames = {k: bool(v.get("own")) for k, v in json.loads((folder / COLOUR_FILE).read_text(encoding="utf-8"))["frames"].items() if isinstance(v, dict)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        for p in pictures:
            p.update(self._lib_marks_of(marks, p["file"]))
            if p["raw"] and not p.get("only_raw"):
                own = p.get("colour") == "own" or (p.get("colour") is None and own_frames.get(p["name"].rsplit(".", 1)[0]))
                p["own_colour"] = bool(own)
                if fc or own:
                    p["fc"] = (fc or "") + ("o" if own else "")
            p["taken"] = self._lib_taken_of(folder, p["file"], marks["pictures"].get(p["file"]))
        text = ""
        try:
            text = (folder / DESCRIPTION_FILE).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
        name = folder.name if key else "Loose pictures"
        if self.settings["use_roll"] and key and key == self.settings["roll"]:
            text = self.settings["film_description"] or text  # the scanning screen's copy is the newest
        return {"ok": True, "key": key, "name": name, "path": self._short_path(folder), "description": text,
                "camera": self._lib_camera(folder, marks),
                "pictures": pictures, "scan_log": (folder / "ScanLog.txt").is_file(), "film_colour": colour,
                "current": bool(self.settings["use_roll"]) and key == self.settings["roll"]}

    @staticmethod
    def _lib_versions(folder):
        """Other versions of a folder's pictures: stem -> ["Re-developed/<when>/<name>", "Cropped/<name>", ...]."""
        out = {}

        def add(rel, name):
            stem = re.sub(r" \(\d+\)$", "", name.rsplit(".", 1)[0])
            out.setdefault(stem, []).append(rel)
        dev = folder / DEVELOPED_FOLDER
        if dev.is_dir():
            for run in sorted(d.name for d in dev.iterdir() if d.is_dir()):
                for name in _tifs(dev / run):
                    add(f"{DEVELOPED_FOLDER}/{run}/{name}", name)
        cropped = folder / CROPPED_FOLDER
        for name in _tifs(cropped) if cropped.is_dir() else []:
            add(f"{CROPPED_FOLDER}/{name}", name)
        return out

    # ------------------------------------------------------------------ one picture
    def library_info(self, key, rel):
        """What a picture's file says about itself, and about how it was scanned (from its RAW file)."""
        folder, path = self._lib_file(key, rel)
        info = read_tiff_info(path)
        st = path.stat()
        cols, rows, dpi = info["cols"], info["rows"], info.get("dpi")
        out = {"ok": True, "file": rel, "name": path.name, "folder": self._short_path(path.parent),
               "bytes": st.st_size, "size_words": _words_size(st.st_size),
               "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
               "pixels": [cols, rows], "megapixels": round(cols * rows / 1e6, 1), "channels": info["channels"],
               "dpi": dpi, "profile": icc_name(info.get("icc") or b"") or None,
               "taken": info["taken"].strftime("%Y-%m-%d %H:%M") if info.get("taken") else None,
               "iso": info.get("iso"), "scanner": read_scanner(path) or self._lib_scanner(info),
               "description": info.get("description")}
        if dpi:
            out["film_mm"] = [round(cols / dpi * 25.4, 1), round(rows / dpi * 25.4, 1)]
            out["print_cm_300"] = [round(cols / 300 * 2.54, 1), round(rows / 300 * 2.54, 1)]
        note = info.get("coolscan") or {}
        raw_rel = None
        stem = path.name.rsplit(".", 1)[0]
        for cand in (folder / "RAW" / path.name, folder / "RAW" / f"{stem}.tif"):
            if cand.is_file():
                raw_rel = str(cand.relative_to(folder))
                if not note:
                    try:
                        note = read_tiff_info(cand).get("coolscan") or {}
                    except (OSError, ValueError, KeyError, IndexError, TypeError):
                        note = {}
                break
        if not note:  # an older scan: the roll's FilmColour.json may still know its exposures
            try:
                data = json.loads((folder / COLOUR_FILE).read_text(encoding="utf-8"))
                got = data["frames"].get(stem)
                if isinstance(got, dict) and got.get("exposures"):
                    note = {"exposures": got["exposures"], "from_roll_file": True}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
        out["scan"] = {k: note[k] for k in ("film", "dpi", "exposures", "turns", "crop", "focus_position",
                                             "scanned", "roll", "from_roll_file") if k in note}
        out["raw"] = raw_rel
        marks = self._lib_marks(folder)
        mine = self._lib_marks_of(marks, rel)
        out["camera"] = self._lib_camera(folder, marks) or None
        if out["camera"] and out["scanner"] == camera_parts(out["camera"])[1] and raw_rel and raw_rel != rel:
            try:   # a picture made from the RAW after the camera was set: the RAW file still names the scanner
                out["scanner"] = self._lib_scanner(read_tiff_info(folder / raw_rel)) or out["scanner"]
            except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
                pass
        out["aperture"], out["shutter"] = mine["aperture"], mine["shutter"]
        out["edit"] = self._lib_edit_of(folder, rel)
        fix, why = self._lib_fix(folder, path, rel)
        mode = (filmfix.read_roll(folder) or {}).get("mode", "frame")
        out["film_colour"] = {"mode": mode, "words": filmfix.MODE_WORDS[mode], "applied": bool(fix), "why": why,
                              "own": self._lib_own_colour(folder, path, rel),
                              "tone": fix["m"]["tone"] if fix else None}
        out["versions"] = self._lib_versions(folder).get(stem, [])
        return out

    @staticmethod
    def _lib_scanner(info):
        make, model = (info.get("make") or "").strip(), (info.get("model") or "").strip()
        return (model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()) or None

    def library_png(self, key, rel, big=False, view="edited"):
        """A picture for the page: small (cached on disk) or the one on show (kept for the last few).
        view: "edited" (with the Library's crop and turn), "turned" (turned, not cut: for cropping) or "original"."""
        folder, path = self._lib_file(key, rel)
        if view == "negative":   # the picture's RAW file as scanned, to pick the film base on
            raw = self._lib_raw_of(folder, path, rel)
            if raw is None:
                raise ValueError("no RAW file")
            return picture_png(raw, BIG_SIDE, as_scanned=True)
        st = path.stat()
        e = None if view == "original" else self._lib_edit_of(folder, rel)
        crop = view != "turned"
        fix = None
        if view != "original" and (filmfix.read_roll(folder) is not None or self._lib_own_colour(folder, path, rel)):
            fix = self._lib_fix(folder, path, rel)[0]
        tag = (str(path), int(st.st_mtime), st.st_size) + ((repr(e), crop) if e else ()) + \
            (("film", hashlib.sha1(repr(fix["numbers"]).encode()).hexdigest(), fix["m"]["tone"]) if fix else ())
        if big:
            with self.lib_lock:
                png = self.lib_big.get(tag)
                if png is not None:
                    self.lib_big.move_to_end(tag)
                    return png
            png = picture_png(path, BIG_SIDE, e, crop, fix)
            with self.lib_lock:
                self.lib_big[tag] = png
                while len(self.lib_big) > 6:
                    self.lib_big.popitem(last=False)
            return png
        name = hashlib.sha1(repr(tag + (SMALL_SIDE,)).encode()).hexdigest() + ".png"
        cache = _cache_dir()
        try:
            return (cache / name).read_bytes()
        except OSError:
            pass
        png = picture_png(path, SMALL_SIDE, e, crop, fix)
        try:
            cache.mkdir(parents=True, exist_ok=True)
            tmp = cache / (name + ".part")
            tmp.write_bytes(png)
            tmp.replace(cache / name)
        except OSError:
            pass
        return png

    # ------------------------------------------------------------------ changes
    def library_describe(self, key, text):
        """Save a folder's description. The current roll goes through the scanning screen's own way,
        so the two never disagree."""
        folder = self._lib_folder(key)
        text = self._tidy_text(text)
        if self.settings["use_roll"] and key and key == self.settings["roll"]:
            self.describe(text)
            return {"ok": True}
        path = folder / DESCRIPTION_FILE
        try:
            if text or path.is_file():
                path.write_text(text + "\n" if text else "", encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": f"Could not save the description ({e.strerror or e})."}
        self.say(f"library: description of {folder.name if key else 'the scans folder'} saved")
        with self.lock:
            self.version += 1
        return {"ok": True}

    def _lib_copy(self, folder, path, rel, planes, info, how):
        """Save changed planes as a new file in <folder>/Edited/ with the same tags, and the same stars,
        tags and caption as the picture it came from. Returns the new file's path inside the folder."""
        out_dir = folder / CROPPED_FOLDER
        out_dir.mkdir(parents=True, exist_ok=True)
        name = path.name if path.suffix.lower() == ".tif" else path.stem + ".tif"
        target, n = out_dir / name, 2
        while target.exists():
            target, n = out_dir / f"{Path(name).stem} ({n}).tif", n + 1
        meta = {k: info[k] for k in ("taken", "description", "make", "model", "iso") if info.get(k)}
        t = self._lib_taken_of(folder, rel)
        if t:
            meta["taken"] = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        icc = info.get("icc")
        meta["colorspace"] = 1 if icc and b"sRGB" in icc[:600] else 65535
        if info.get("coolscan"):
            meta["coolscan"] = {**info["coolscan"], "edited_from": rel, "edit": how}
        write_tiff(target, planes, info.get("dpi") or 2900, icc, meta=meta)
        saved = str(target.relative_to(folder))
        with self.lib_lock:
            marks = self._lib_marks(folder)
            if rel in marks.get("pictures", {}):   # the copy has the crop and turn in its pixels already
                m = {k: v for k, v in marks["pictures"][rel].items() if k not in ("edit", "redo", "rescan", "colour")}
                marks["pictures"][saved] = m
                self._lib_save_marks(folder, marks)   # (an entry with nothing left in it is not kept)
                self._lib_write_file(folder, saved, m)
        return saved

    # ------------------------------------------------------------------ crop and turn: kept as settings, files untouched
    def _lib_set_edit(self, key, rel, e):
        folder, _ = self._lib_file(key, rel)
        if rel.startswith("RAW/"):
            return {"ok": False, "error": "A RAW file is cropped on the Re-develop page."}
        e = tidy_edit(e)
        res = self.library_edit(key, [rel], {"edit": e})
        return {**res, "edit": e}

    def library_crop(self, key, rel, rect):
        """Keep a crop for a picture (rect on the picture as turned; None takes it off). The file stays as it is."""
        folder, _ = self._lib_file(key, rel)
        e = self._lib_edit_of(folder, rel) or {"turns": 0, "crop": None}
        if rect is not None:
            try:
                x0, y0, x1, y1 = (float(v) for v in rect)
            except (TypeError, ValueError):
                return {"ok": False, "error": "That crop is not right."}
            if abs(x1 - x0) < 0.05 or abs(y1 - y0) < 0.05:
                return {"ok": False, "error": "The crop is too small."}
        res = self._lib_set_edit(key, rel, {"turns": e["turns"], "crop": rect})
        if rect is not None and res.get("ok") and hasattr(self, "croplearn_library_crop"):
            self.croplearn_library_crop(self._lib_file(key, rel)[1], res.get("edit"))   # learned from (croplearning.py)
        if rect is not None and res.get("ok") and hasattr(self, "turnlearn_library_crop"):
            self.turnlearn_library_crop(self._lib_file(key, rel)[1], res.get("edit"))   # its turn too (turnlearning.py)
        return res

    def library_turn(self, key, rel, quarter, by_hand=True):
        """Turn a picture a quarter (1 = to the left, 3 = to the right, 2 = upside down), kept as a setting.
        by_hand: learned from by auto rotate (turnlearning.py); auto rotate's own turns are not."""
        folder, _ = self._lib_file(key, rel)
        k = int(quarter or 0) % 4
        if not k:
            return {"ok": False, "error": "Nothing to turn."}
        e = self._lib_edit_of(folder, rel) or {"turns": 0, "crop": None}
        crop = _turn_rect(e["crop"], k) if e["crop"] else None
        res = self._lib_set_edit(key, rel, {"turns": (e["turns"] + k) % 4, "crop": crop})
        if by_hand and res.get("ok") and hasattr(self, "turnlearn_library_turn"):
            self.turnlearn_library_turn(self._lib_file(key, rel)[1], res.get("edit") or {"turns": 0})
        return res

    def library_autocrop(self, key, files, save=False):
        """Auto crop: find the picture inside each file (film edges, holder, gap between frames left out),
        on the picture as turned. save: keep it as the picture's crop; else only say where it is."""
        files = [str(f) for f in files or []][:PICTURES_MOST]
        out, found = [], 0
        for rel in files:
            folder, path = self._lib_file(key, rel)
            if rel.startswith("RAW/"):
                out.append({"file": rel, "found": False, "why": "RAW file"})
                continue
            e = self._lib_edit_of(folder, rel) or {"turns": 0, "crop": None}
            try:
                planes, _ = read_small(path, 1200)
                planes = apply_edit(planes, e, crop=False)
                planes = [p * 65535.0 for p in planes]
                rect = find_picture(planes)
                learned = self.learned_crop(planes, rect, *self._lib_film_of(path)) if hasattr(self, "learned_crop") else None
                if (learned or rect) is not None and hasattr(self, "_auto_crop_options"):
                    rect = self._auto_crop_options(learned or rect, trim=learned is None)
                elif learned:
                    rect = learned
            except Exception as ex:  # noqa: BLE001 - an extra: say so, never break the page
                out.append({"file": rel, "found": False, "why": str(ex)})
                continue
            rect = [round(float(v), 4) for v in rect] if rect is not None else None
            out.append({"file": rel, "found": rect is not None, "rect": rect, "learned": bool(rect is not None and learned)})
            if rect is not None and hasattr(self, "croplearn_note_auto"):
                self.croplearn_note_auto(path, rect)   # an auto crop kept as it is teaches nothing: not learned from
            if rect is not None:
                found += 1
                if save:
                    self.library_edit(key, [rel], {"edit": {"turns": e["turns"], "crop": rect}})
        if save:
            self.say(f"library: auto crop set a crop on {found} of {len(files)} picture(s)")
        return {"ok": True, "found": found, "pictures": out, **({"rect": out[0].get("rect"), "found_one": out[0]["found"], "learned": out[0].get("learned", False)} if len(out) == 1 else {})}

    def _lib_edited_planes(self, folder, path, rel):
        """The full picture with its crop and turn (and the roll's film colour), and the file's tags;
        None when nothing is changed."""
        e = self._lib_edit_of(folder, rel)
        fix = self._lib_fix(folder, path, rel)[0] if (filmfix.read_roll(folder) is not None or self._lib_own_colour(folder, path, rel)) else None
        if not e and not fix:
            return None, None, None
        planes = read_tiff16(path)
        if fix:
            got = filmfix.recolour([p.astype(np.float32) / 65535 for p in planes[:3]], fix["m"].get("space"), fix["m"], fix["numbers"])
            planes = [(np.clip(v, 0, 1) * 65535 + 0.5).astype(np.uint16) for v in got]
        e = dict(e or {"turns": 0, "crop": None}, film=bool(fix))
        return apply_edit(planes, e), read_tiff_info(path), e

    def library_export(self, key, files):
        """New TIFF copies with the crop and turn put into the pixels, in <folder>/Edited/. The pictures
        themselves stay as they are; a picture with nothing changed is left out."""
        files = [str(f) for f in files or []][:PICTURES_MOST]
        saved, same = [], []
        for rel in files:
            folder, path = self._lib_file(key, rel)
            planes, info, e = self._lib_edited_planes(folder, path, rel)
            if planes is None:
                same.append(rel)
                continue
            how = ", ".join(w for w in ((f"turn {e['turns']}" if e["turns"] else ""), ("crop" if e["crop"] else ""),
                                        ("roll's film colour" if e.get("film") else "")) if w)
            saved.append(self._lib_copy(folder, path, rel, planes, info, how))
        if saved:
            self.say(f"library: exported {len(saved)} picture(s) with their crop, turn and film colour into {CROPPED_FOLDER}/")
        return {"ok": True, "saved": saved, "unchanged": same, "folder": CROPPED_FOLDER}

    def library_export_to(self, key, files, dest):
        """Copies of pictures in a folder of the user's choosing, with their crop and turn put in. The roll
        folders are kept (Roll 005/...); a picture in a roll's Positive folder goes straight into the roll's
        folder there. Exported into the scans folder itself, names get _modified. Nothing is overwritten."""
        dest = Path(os.path.expanduser(str(dest or "").strip()))
        if not str(dest).strip() or not dest.is_absolute():
            return {"ok": False, "error": "Choose the folder to export to first."}
        if not dest.is_dir():
            return {"ok": False, "error": f"There is no folder {self._short_path(dest)}."}
        files = [str(f) for f in files or []][:PICTURES_MOST]
        base = self._lib_base()
        same_root = dest.resolve() == base.resolve()
        saved, problems = [], []
        for rel in files:
            folder, path = self._lib_file(key, rel)
            parts = rel.split("/")
            if len(parts) > 1 and parts[0] == "Positive":
                parts = parts[1:]          # the finished pictures sit in the roll's own folder in the export
            out_dir = dest.joinpath(*([p for p in str(key or "").split("/") if p] + parts[:-1]))
            stem, suffix = Path(parts[-1]).stem, ".tif"
            if same_root or out_dir.resolve() == path.parent.resolve():
                stem += "_modified"        # beside the scans themselves: never the same name
            target, n = out_dir / f"{stem}{suffix}", 2
            while target.exists() or target.resolve() == path.resolve():
                target, n = out_dir / f"{stem} ({n}){suffix}", n + 1
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                planes, info, e = self._lib_edited_planes(folder, path, rel)
                t = self._lib_taken_of(folder, rel)
                when = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S") if t else None
                if planes is None:
                    shutil.copy2(path, target)  # nothing changed: the file as it is, tags and all
                    if when and t != self._lib_taken(path):   # the date is in the app's list only: the copy gets it
                        m = self._lib_marks(folder)["pictures"].get(rel) or {}
                        try:   # a file with another program's metadata keeps it, and its own date
                            update_tiff(target, caption=m.get("caption") or "", tags=m.get("tags") or [],
                                        rating=-1 if m.get("flag") == "reject" else int(m.get("stars") or 0), taken=when,
                                        people=m.get("people") or [], date_note=m.get("date_note") or "", **self._lib_exposure_of(folder, m))
                        except (NotOurs, OSError, ValueError, struct.error):
                            pass
                else:
                    meta = {k: info[k] for k in ("taken", "description", "make", "model", "iso") if info.get(k)}
                    if when:
                        meta["taken"] = when
                    icc = info.get("icc")
                    meta["colorspace"] = 1 if icc and b"sRGB" in icc[:600] else 65535
                    if info.get("coolscan"):
                        meta["coolscan"] = {**info["coolscan"], "edited_from": rel}
                    write_tiff(target, planes, info.get("dpi") or 2900, icc, meta=meta)
                    m = self._lib_marks(folder)["pictures"].get(rel) or {}
                    if WRITE_FILES and m:
                        rating = -1 if m.get("flag") == "reject" else int(m.get("stars") or 0)
                        update_tiff(target, caption=m.get("caption") or "", tags=m.get("tags") or [], rating=rating,
                                    taken=datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S") if t else None,
                                    people=m.get("people") or [], date_note=m.get("date_note") or "", **self._lib_exposure_of(folder, m))
                saved.append(str(target.relative_to(dest)))
            except (OSError, ValueError, struct.error) as ex:
                problems.append(f"{rel}: {getattr(ex, 'strerror', None) or ex}")
        self.say(f"library: exported {len(saved)} picture(s) to {self._short_path(dest)}"
                 + (f"; {len(problems)} could not be" if problems else ""))
        return {"ok": True, "saved": saved, "problems": problems, "folder": self._short_path(dest), "modified": same_root}

    # ------------------------------------------------------------------ stars, flags, tags, captions, dates
    def _lib_marks(self, folder):
        """The folder's Library.json: {"pictures": {file inside the folder: {stars, flag, tags, caption, taken}}}."""
        try:
            data = json.loads((folder / META_FILE).read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("pictures"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"pictures": {}}

    @staticmethod
    def _lib_save_marks(folder, marks):
        path = folder / META_FILE
        for v in marks["pictures"].values():
            v.pop("redo", None)   # the old re-develop tag is gone
        marks["pictures"] = {k: v for k, v in marks["pictures"].items() if any(v.get(x) for x in ("stars", "flag", "tags", "caption", "taken", "people", "date_note", "rescan", "edit", "colour", "aperture", "shutter"))}
        tmp = folder / (META_FILE + ".part")
        tmp.write_text(json.dumps(marks, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _lib_marks_of(marks, rel):
        m = marks.get("pictures", {}).get(rel) or {}
        return {"stars": int(m.get("stars") or 0), "flag": m.get("flag") or None, "tags": list(m.get("tags") or []),
                "people": list(m.get("people") or []), "caption": m.get("caption") or "", "date_note": m.get("date_note") or "",
                "rescan": bool(m.get("rescan")), "edit": tidy_edit(m.get("edit")), "taken_own": bool(m.get("taken_own")),
                "colour": m.get("colour") if m.get("colour") in ("own", "roll") else None,
                "aperture": m.get("aperture") or None, "shutter": m.get("shutter") or ""}

    def _lib_camera(self, folder, marks=None):
        """The camera the folder's film was in: kept in its Library.json; for the roll being scanned into,
        what the scanning screen has when the folder's list says nothing yet."""
        if marks is None:
            marks = self._lib_marks(folder)
        camera = marks.get("camera")
        if not isinstance(camera, str):
            st = self.settings
            camera = st.get("film_camera", "") if st["use_roll"] and st["roll"] and folder == self._base() / st["roll"] else ""
        return camera.strip()

    def _lib_exposure_of(self, folder, m, camera=None):
        """What update_tiff / make_xmp get for the camera, aperture and shutter speed of a picture."""
        return {"camera": self._lib_camera(folder) if camera is None else camera,
                "fnumber": m.get("aperture") or None, "exposure": m.get("shutter") or ""}

    def library_camera(self, key, text):
        """Set the camera for a whole folder: kept in its Library.json and written into its pictures' tags
        (Make and Model, so Lightroom shows it as the camera). RAW files are left as they are."""
        folder = self._lib_folder(key)
        camera = re.sub(r"\s+", " ", str(text or "")).strip()[:80]
        files = [rel for rel, _ in self._lib_pictures(folder)[:PICTURES_MOST]]
        problems, changed = [], 0
        with self.lib_lock:
            marks = self._lib_marks(folder)
            marks["camera"] = camera
            self._lib_save_marks(folder, marks)
            for rel in files:
                why = self._lib_write_file(folder, rel, marks["pictures"].get(rel) or {}, camera=camera)
                if why:
                    problems.append(f"{rel}: {why}")
                else:
                    changed += 1
        st = self.settings
        if st["use_roll"] and key and key == st["roll"]:
            st["film_camera"] = camera
        if isinstance(st["rolls"].get(key), dict):
            st["rolls"][key]["film_camera"] = camera
        self.save_settings()
        self.say(f"library: camera {'set to ' + camera if camera else 'taken out'} for {folder.name if key else 'the scans folder'}"
                 f" ({changed} file(s) written" + (f"; {len(problems)} kept in the app's list only" if problems else "") + ")")
        with self.lock:
            self.version += 1
        return {"ok": True, "camera": camera, "changed": changed, "problems": problems}

    def _lib_edit_of(self, folder, rel):
        """The crop and turn kept for a picture, or None."""
        return tidy_edit((self._lib_marks(folder)["pictures"].get(rel) or {}).get("edit"))

    def _lib_taken(self, path):
        """When the photo was taken, as the file's tags say ("YYYY-MM-DD HH:MM:SS"), or ""."""
        try:
            st = path.stat()
        except OSError:
            return ""
        tag = (str(path), int(st.st_mtime), st.st_size)
        got = self.lib_taken.get(tag)
        if got is None:
            try:
                t = read_tiff_info(path).get("taken")
                got = t.strftime("%Y-%m-%d %H:%M:%S") if t else ""
            except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
                got = ""
            if len(self.lib_taken) > 20000:
                self.lib_taken.clear()
            self.lib_taken[tag] = got
        return got

    def _lib_taken_of(self, folder, rel, m=None):
        """When the photo was taken: what the file says, or the app's own list (Library.json) for a picture
        whose file could not take the date (a RAW file, a file Lightroom has written into)."""
        if m is None:
            m = self._lib_marks(folder)["pictures"].get(rel)
        if m and m.get("taken_app") and m.get("taken"):
            return str(m["taken"])
        return self._lib_taken(folder / rel)

    def _lib_write_file(self, folder, rel, m, taken=None, camera=None):
        """Put a picture's marks into the file itself (XMP + EXIF), so Lightroom sees them. '' if done, else why not."""
        if not WRITE_FILES:
            return ""
        path = folder / rel
        if rel.startswith("RAW/"):
            return "RAW files are left as they are"
        if taken is None:
            t = self._lib_taken(path)
            taken = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S") if t else None
        rating = -1 if m.get("flag") == "reject" else int(m.get("stars") or 0)
        try:
            update_tiff(path, caption=m.get("caption") or "", tags=m.get("tags") or [], rating=rating, taken=taken,
                        people=m.get("people") or [], date_note=m.get("date_note") or "",
                        **self._lib_exposure_of(folder, m, camera))
        except NotOurs as e:
            return str(e)
        except (OSError, ValueError, struct.error) as e:
            return f"could not write into the file ({getattr(e, 'strerror', None) or e})"
        return ""

    @staticmethod
    def _new_taken(old, how, value, n):
        """A picture's new date taken. how: "year" (only the year changes), "date" (the day changes, the
        time of day stays, so the order stays), "exact" (this date and time, one second apart per picture)."""
        if how == "year":
            year = int(value)
            if not 1800 <= year <= 2100:
                raise ValueError("year")
            base = old or datetime.datetime(year, 1, 1, 12, 0, 0)
            try:
                return base.replace(year=year)
            except ValueError:  # 29 February
                return base.replace(year=year, day=28)
        if how == "date":
            day = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
            base = old or datetime.datetime(day.year, day.month, day.day, 12, 0, 0)
            return base.replace(year=day.year, month=day.month, day=day.day)
        if how == "exact":
            text = str(value).replace("T", " ")
            start = datetime.datetime.strptime(text[:16], "%Y-%m-%d %H:%M")
            return start + datetime.timedelta(seconds=n)
        raise ValueError("how")

    def library_edit(self, key, files, body):
        """Change stars, flag, caption, tags or the date taken of one or more pictures of a folder."""
        folder = self._lib_folder(key)
        files = [str(f) for f in files or []][:PICTURES_MOST]
        for rel in files:
            self._lib_file(key, rel)
        if not files:
            return {"ok": False, "error": "No pictures picked."}
        date = body.get("date") if isinstance(body.get("date"), dict) else None
        problems, changed, left = [], 0, 0
        own = False
        if date and not date.get("roll"):   # a date for some of the folder's pictures only is their own date
            try:
                own = len(set(files)) < len(self.library_roll(key)["pictures"])
            except (OSError, ValueError):
                own = len(files) == 1
        only_app = set(body) - {"key", "file", "files"} <= {"rescan", "edit", "colour"}   # nothing to write into the files then
        exposure = {}
        try:   # as typed: f/5.6, 5,6 ... and 1/125, 125, 2s ...
            if "aperture" in body:
                exposure["aperture"] = read_aperture(body["aperture"])
            if "shutter" in body:
                exposure["shutter"] = read_shutter(body["shutter"])
        except ValueError as e:
            return {"ok": False, "error": "That aperture is not one. Type it like 5.6 or f/5.6." if str(e) == "aperture"
                    else "That shutter speed is not one. Type it like 1/125, or 2s for whole seconds."}
        with self.lib_lock:
            marks = self._lib_marks(folder)
            pics = marks["pictures"]
            camera = self._lib_camera(folder, marks)
            for n, rel in enumerate(sorted(files)):
                m = dict(pics.get(rel) or {})
                if "stars" in body:
                    m["stars"] = max(0, min(5, int(body["stars"] or 0)))
                if "flag" in body:
                    m["flag"] = body["flag"] if body["flag"] in ("pick", "reject") else None
                if "caption" in body:
                    m["caption"] = str(body["caption"] or "").strip()[:2000]
                if "date_note" in body:
                    m["date_note"] = str(body["date_note"] or "").strip()[:200]
                m.update(exposure)   # aperture (5.6) and shutter speed ("1/125", or seconds: "2")
                if "rescan" in body:   # tagged to be scanned again from the film (kept in the app's list only)
                    m["rescan"] = bool(body["rescan"])
                if "colour" in body:   # this picture's own colour or the roll's: kept in the app's list only
                    m["colour"] = body["colour"] if body["colour"] in ("own", "roll") else None
                if "edit" in body:   # crop and turn: kept in the app's list only, the file is never changed
                    m["edit"] = tidy_edit(body["edit"])
                for kind in ("tags", "people"):   # people: who is in the picture (also written as keywords)
                    words = list(m.get(kind) or [])
                    for t in body.get("add_" + kind) or []:
                        t = re.sub(r"\s+", " ", str(t)).strip()[:80]
                        if t and t.lower() not in (x.lower() for x in words):
                            words.append(t)
                    drop = {str(t).strip().lower() for t in body.get("remove_" + kind) or []}
                    m[kind] = [t for t in words if t.lower() not in drop]
                taken = None
                if date:
                    if date.get("roll") and m.get("taken_own") and not date.get("all"):
                        left += 1      # a date for the whole roll leaves pictures that were given their own
                        continue
                    t = self._lib_taken_of(folder, rel, m)
                    old = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S") if t else None
                    try:
                        taken = self._new_taken(old, date.get("how"), date.get("value"), n)
                    except (ValueError, TypeError):
                        return {"ok": False, "error": "That date is not right."}
                    m.setdefault("taken_was", t or None)
                    m["taken"] = taken.strftime("%Y-%m-%d %H:%M:%S")
                    m["taken_own"] = own   # changed by itself or with a few others: its own date
                pics[rel] = m
                why = "" if only_app else self._lib_write_file(folder, rel, m, taken, camera)
                if date:
                    m["taken_app"] = bool(why)   # the file could not take it: the app's list is where the date is
                if why:
                    problems.append(f"{rel}: {why}")
                changed += 1
            self._lib_save_marks(folder, marks)
        what = ", ".join(dict.fromkeys(w for w, k in (("stars", "stars"), ("flag", "flag"), ("caption", "caption"),
                                         ("tags", "add_tags"), ("tags", "remove_tags"), ("people", "add_people"),
                                         ("people", "remove_people"), ("date taken", "date"), ("date note", "date_note"),
                                         ("aperture", "aperture"), ("shutter speed", "shutter"),
                                         ("rescan tag", "rescan"), ("crop and turn", "edit"),
                                         ("own or roll colour", "colour")) if k in body))
        self.say(f"library: {what or 'nothing'} changed for {changed} picture(s) in {folder.name if key else 'the scans folder'}"
                 + (f"; {len(problems)} kept in the app's list only" if problems else ""))
        return {"ok": True, "changed": changed, "problems": problems, "left": left}

    # ------------------------------------------------------------------ search
    def library_search(self, text):
        """Pictures in every folder whose words match: folder name, description, file name, tags, caption,
        date taken. Every word typed must be found somewhere."""
        words = [w.lower() for w in str(text or "").split() if w.strip()][:10]
        if not words:
            return {"ok": True, "pictures": [], "rolls": []}
        out, rolls = [], []
        for r in self.library_rolls()["rolls"]:
            try:
                roll = self.library_roll(r["key"])
            except (OSError, ValueError):
                continue
            head = f"{roll['name']} {roll['description']} {roll.get('camera') or ''}".lower()
            if all(w in head for w in words):
                rolls.append({"key": r["key"], "name": r["name"], "count": len(roll["pictures"])})
            for p in roll["pictures"]:
                hay = " ".join([head, p["name"].lower(), " ".join(p["tags"]).lower(), " ".join(p["people"]).lower(),
                                p["caption"].lower(), p["date_note"].lower(), p["taken"],
                                "starred" if p["stars"] else "", p["flag"] or ""])
                if all(w in hay for w in words):
                    out.append({**p, "key": r["key"], "roll": r["name"]})
                    if len(out) >= 1000:
                        return {"ok": True, "pictures": out, "rolls": rolls, "more": True}
        return {"ok": True, "pictures": out, "rolls": rolls}

    def library_tags(self):
        """Every tag used anywhere in the library, with how often, most used first."""
        count, people = {}, {}
        for r in self.library_rolls()["rolls"]:
            try:
                folder = self._lib_folder(r["key"])
            except ValueError:
                continue
            for m in self._lib_marks(folder)["pictures"].values():
                for t in m.get("tags") or []:
                    count[t] = count.get(t, 0) + 1
                for t in m.get("people") or []:
                    people[t] = people.get(t, 0) + 1
        return {"ok": True, "tags": sorted(count, key=lambda t: (-count[t], t.lower())),
                "people": sorted(people, key=lambda t: (-people[t], t.lower()))}

    # ------------------------------------------------------------------ the Mac: Finder, Lightroom, JPEG
    def _lib_paths(self, key, files):
        return [self._lib_file(key, f)[1] for f in (files or [])][:200]

    def library_reveal(self, key, files):
        paths = self._lib_paths(key, files)
        if sys.platform != "darwin":
            return {"ok": False, "error": "Show in Finder works on the Mac only."}
        subprocess.Popen(["open", "-R", *map(str, paths)])
        return {"ok": True}

    @staticmethod
    def _lightroom():
        """The Lightroom app on this Mac, or None."""
        for name in ("Adobe Lightroom Classic", "Adobe Lightroom"):
            for where in (Path("/Applications") / name / f"{name}.app", Path("/Applications") / f"{name}.app",
                          Path.home() / "Applications" / f"{name}.app"):
                if where.is_dir():
                    return str(where)
        return None

    def library_lightroom(self, key, files):
        paths = self._lib_paths(key, files)
        if sys.platform != "darwin":
            return {"ok": False, "error": "Open in Lightroom works on the Mac only."}
        app = self._lightroom()
        if not app:
            return {"ok": False, "error": "Lightroom was not found in Applications."}
        subprocess.Popen(["open", "-a", app, *map(str, paths)])
        self.say(f"library: {len(paths)} picture(s) handed to {Path(app).stem}")
        return {"ok": True, "app": Path(app).stem}

    def library_jpeg(self, key, rel, side):
        """A small sRGB JPEG of a picture for sharing, in <folder>/JPEG/, made by macOS's sips."""
        folder, path = self._lib_file(key, rel)
        sips = shutil.which("sips")
        if not sips:
            return {"ok": False, "error": "Making JPEGs needs macOS (its sips tool)."}
        side = max(400, min(8000, int(side or 2048)))
        out_dir = folder / JPEG_FOLDER
        out_dir.mkdir(parents=True, exist_ok=True)
        target, n = out_dir / f"{path.stem}.jpg", 2
        while target.exists():
            target, n = out_dir / f"{path.stem} ({n}).jpg", n + 1
        srgb = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"
        cmd = [sips, "-s", "format", "jpeg", "-s", "formatOptions", "85", "-Z", str(side)]
        if Path(srgb).is_file():
            cmd += ["-m", srgb]
        planes, info, _ = self._lib_edited_planes(folder, path, rel)
        tmpdir = None
        if planes is not None:   # the crop and turn go into the JPEG; the picture file stays as it is
            tmpdir = Path(tempfile.mkdtemp(prefix="coolscan-jpeg-"))
            src = tmpdir / path.name
            write_tiff(src, planes, info.get("dpi") or 2900, info.get("icc"))
            path = src
        try:
            r = subprocess.run(cmd + [str(path), "--out", str(target)], capture_output=True, text=True, timeout=120)
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)
        if r.returncode or not target.is_file():
            return {"ok": False, "error": "sips could not make the JPEG: " + (r.stderr or r.stdout).strip()[-300:]}
        return {"ok": True, "saved": str(target.relative_to(folder)), "folder": self._short_path(out_dir)}

    # ------------------------------------------------------------------ the roll's film colour (FilmProfile.json in its folder)
    def _lib_raw_of(self, folder, path, rel):
        """The RAW file of a finished picture (Positive/<name> or a picture in the folder itself), or None."""
        parts = rel.split("/")
        if len(parts) > 2 or (len(parts) == 2 and parts[0] != "Positive"):
            return None   # RAW files themselves, and other versions (Re-developed, Edited), are left as they are
        stem = path.name.rsplit(".", 1)[0]
        for cand in (folder / "RAW" / path.name, folder / "RAW" / f"{stem}.tif"):
            if cand.is_file():
                return cand
        return None

    def _lib_exposures(self, folder, stem, note):
        if note.get("exposures"):
            return note["exposures"]
        try:
            got = json.loads((folder / COLOUR_FILE).read_text(encoding="utf-8"))["frames"].get(stem)
            return got.get("exposures") if isinstance(got, dict) else None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _lib_fix(self, folder, path, rel):
        """The roll's film colour for one picture: ({"numbers", "m"}, "") when it can be put on, else
        (None, why not). m is how the saved picture was made from its RAW file (filmfix.match), kept."""
        nums = self._lib_numbers(folder)
        own = self._lib_own_colour(folder, path, rel)
        if nums is None and not own:
            return None, ""
        if own:
            nums = None   # this picture on its own, whatever the roll says
        raw = self._lib_raw_of(folder, path, rel)
        if raw is None:
            return None, ("RAW files and other versions are shown as they are." if "/" in rel and not rel.startswith("Positive/")
                          else "It has no RAW file (only Archive mode keeps one), which the film colour is worked out from.")
        try:
            ps, rs = path.stat(), raw.stat()
        except OSError as e:
            return None, str(e)
        key = (str(path), ps.st_mtime_ns, ps.st_size, rs.st_mtime_ns, rs.st_size)
        cache = self.__dict__.setdefault("lib_match", OrderedDict())
        m = cache.get(key)
        if m is None:
            try:
                note = read_tiff_info(raw).get("coolscan") or {}
                if note.get("film") not in (None, "negative"):
                    m = {"why": "It is not a colour negative."}
                else:
                    exposures = self._lib_exposures(folder, raw.name.rsplit(".", 1)[0], note)
                    if not exposures:
                        m = {"why": "It was scanned before the app kept the scanner's exposures, which the film colour needs."}
                    else:
                        pos, _ = read_small(path, 1000)
                        if len(pos) != 3:
                            m = {"why": "It is not a colour picture."}
                        else:
                            rp, _ = read_small(raw, 1000)
                            crop = note.get("crop")
                            if crop and len(crop) == 4:
                                rows, cols = rp[0].shape
                                r0, r1 = int(round(crop[1] * rows)), int(round(crop[3] * rows))
                                c0, c1 = int(round(crop[0] * cols)), int(round(crop[2] * cols))
                                if r1 - r0 > 16 and c1 - c0 > 16:
                                    rp = [p[r0:r1, c0:c1] for p in rp]
                            space = space_of(read_tiff_info(path).get("icc"))
                            m = filmfix.match(pos, space, filmfix.density(rp[:3], exposures)) or {"bad": True}
                            m["space"] = space
                            if m.get("bad"):
                                m["why"] = ("The picture does not match its RAW file closely enough (colour restoration or "
                                            "grain reduction was used, or it was made another way), so it is shown as saved.")
            except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error) as e:
                m = {"why": f"Its RAW file could not be read ({e})."}
            cache[key] = m
            while len(cache) > 400:
                cache.popitem(last=False)
        if m.get("why"):
            return None, m["why"]
        if filmfix.same_already(m, nums):
            return None, ("It was saved with its own colour already." if own else "It was saved with this colour already.")
        return {"numbers": nums, "m": m}, ""

    def _lib_numbers(self, folder, d=None):
        """The roll's film numbers. A roll measured before the app kept how much film there is at each
        density has its frames looked at once more here (their RAW files, read small), so the roll gets
        its colour curve; that is done once and kept in FilmColour.json."""
        nums = filmfix.roll_numbers(folder, d)
        if nums is None or "curve" in nums or (d or filmfix.read_roll(folder) or {}).get("mode") not in ("roll", "base"):
            return nums
        tried = self.__dict__.setdefault("lib_curve_tried", {})
        stamp = filmfix.files_stamp(folder)
        if tried.get(str(folder)) == stamp:
            return nums
        with self.__dict__.setdefault("lib_curve_lock", threading.Lock()):
            if tried.get(str(folder)) != filmfix.files_stamp(folder):
                added = self._lib_measure_roll(folder)
                tried[str(folder)] = filmfix.files_stamp(folder)
                if added:
                    self.say(f"library: looked at {added} RAW file(s) of {folder.name} for the roll's colour curve")
        return filmfix.roll_numbers(folder, d)

    def _lib_measure_roll(self, folder):
        """Add "q" (how much film at each density) to the roll's measured frames that have none, from their
        RAW files. Returns how many were added."""
        try:
            data = json.loads((folder / COLOUR_FILE).read_text(encoding="utf-8"))
            frames = data["frames"]
        except (OSError, ValueError, KeyError, TypeError):
            return 0
        added = 0
        for stem, f in list(frames.items()) if isinstance(frames, dict) else []:
            if not isinstance(f, dict) or f.get("q") or not f.get("exposures"):
                continue
            raw = folder / "RAW" / f"{stem}.tif"
            try:
                if not raw.is_file() or (read_tiff_info(raw).get("coolscan") or {}).get("film") not in (None, "negative"):
                    continue
                planes, _ = read_small(raw, 1400)
                if len(planes) < 3:
                    continue
                f["q"] = filmfix.frame_quantiles(planes[:3], f["exposures"])
                added += 1
            except (OSError, ValueError, KeyError, IndexError, TypeError, struct.error):
                continue
        if added:
            try:
                tmp = folder / (COLOUR_FILE + ".part")
                tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
                os.replace(tmp, folder / COLOUR_FILE)
            except OSError:
                return 0
            cache = getattr(self, "_colour_cache", None)
            if cache is not None and cache[0] == str(folder):
                self._colour_cache = None   # the scanning side reads the file again
        return added

    def _lib_own_colour(self, folder, path, rel):
        """True when a picture goes by its own colour, not the roll's: set on the picture in the Library
        ("colour" in Library.json: "own" or "roll"), else what scanning noted (another film on that strip)."""
        c = (self._lib_marks(folder)["pictures"].get(rel) or {}).get("colour")
        if c in ("own", "roll"):
            return c == "own"
        return filmfix.frame_is_own(folder, path.name.rsplit(".", 1)[0])

    def _lib_colour_state(self, folder):
        """For the page: where the folder's film colour comes from, and what can be picked."""
        d = filmfix.read_roll(folder) or {"mode": "frame"}
        nums = self._lib_numbers(folder, d) if d["mode"] != "frame" else None
        measured = len(filmfix.measured_frames(folder))
        st = filmfix.files_stamp(folder)
        return {"mode": d["mode"], "name": d.get("name") or "", "words": filmfix.MODE_WORDS[d["mode"]],
                "numbers": {k: nums[k] for k in ("lo", "hi")} if nums else None, "curve": bool(nums and "curve" in nums),
                "measured": measured, "least": 3, "base_from": d.get("from") if d.get("base") else None,
                "has_base": bool(d.get("base")),
                "profiles": sorted(n for n, p in self.settings["film_profiles"].items() if isinstance(p, dict)),
                "stamp": hashlib.sha1(repr(st).encode()).hexdigest()[:10],
                "raw": (folder / "RAW").is_dir()}

    def _lib_colour_changed(self, folder):
        """The scanning screen goes by the same file: when it is the roll being scanned into, show it there."""
        if hasattr(self, "roll_colour_changed"):
            self.roll_colour_changed(folder)
        with self.lock:
            self.version += 1

    def library_film_colour(self, key, mode, name=""):
        """Where a folder's film colour comes from, kept in its FilmProfile.json (no picture file is changed)."""
        folder = self._lib_folder(key)
        mode = str(mode or "")
        if mode not in filmfix.MODES:
            return {"ok": False, "error": "That is not a way to colour negatives."}
        old = filmfix.read_roll(folder) or {}
        d = {"mode": mode}
        if mode == "profile":
            p = self.settings["film_profiles"].get(str(name or ""))
            if not isinstance(p, dict):
                return {"ok": False, "error": "That film profile is not there any more."}
            d.update(name=str(name), lo=p["lo"], hi=p["hi"], frames=p.get("frames"), curve=p.get("curve"))
        elif mode == "roll":
            p = film_profile_from(filmfix.measured_frames(folder))
            if p is None:
                n = len(filmfix.measured_frames(folder))
                return {"ok": False, "error": f"This roll has {n} measured frame(s); its own colour needs at least 3 "
                                              "(frames are measured when they are scanned as colour negatives)."}
            d.update(lo=p["lo"], hi=p["hi"], frames=p["frames"])
        elif mode == "base":
            if old.get("mode") != "base" and not old.get("base"):
                return {"ok": False, "error": "Pick the film base on a picture first: open a picture and press Film base…"}
            d.update({k: old[k] for k in ("lo", "hi", "base", "from", "spot") if k in old})
        try:
            if mode == "frame" and not old:
                pass   # no file is the same as each frame on its own
            else:
                if old.get("base") and mode != "base":
                    d.update({"base": old["base"], "from": old.get("from"), "spot": old.get("spot"),
                              "base_lo": old.get("lo") if old.get("mode") == "base" else old.get("base_lo"),
                              "base_hi": old.get("hi") if old.get("mode") == "base" else old.get("base_hi")})
                if mode == "base" and not d.get("lo"):
                    d["lo"], d["hi"] = old.get("base_lo"), old.get("base_hi")
                filmfix.write_roll(folder, d)
        except OSError as e:
            return {"ok": False, "error": f"Could not save the film colour ({e.strerror or e})."}
        self.say(f"library: colour of negatives in {folder.name}: {filmfix.MODE_WORDS[mode]}"
                 + (f' "{name}"' if mode == "profile" else ""))
        self._lib_colour_changed(folder)
        return {"ok": True, "film_colour": self._lib_colour_state(folder)}

    def library_film_base(self, key, rel, x, y):
        """Measure the film base (the clear film between or beside the frames) at a spot of a picture's RAW
        file, and colour the folder's negatives from it."""
        folder, path = self._lib_file(key, rel)
        raw = self._lib_raw_of(folder, path, rel) if not rel.startswith("RAW/") else path
        if raw is None:
            return {"ok": False, "error": "The film base is measured on the RAW file, and this picture has none."}
        note = read_tiff_info(raw).get("coolscan") or {}
        if note.get("film") not in (None, "negative"):
            return {"ok": False, "error": "The film base is for colour negatives."}
        exposures = self._lib_exposures(folder, raw.name.rsplit(".", 1)[0], note)
        if not exposures:
            return {"ok": False, "error": "This picture was scanned before the app kept the scanner's exposures, so its "
                                          "film base cannot be compared with other frames. Try a picture scanned later."}
        try:
            x, y = min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Click on the picture."}
        planes, _ = read_small(raw, 1600)
        base, spread = filmfix.base_at(planes[:3], exposures, x, y)
        frame = frame_colour([(np.clip(p, 0, 1) * 65535).astype(np.uint16) for p in planes[:3]], exposures)
        measured = film_profile_from(filmfix.measured_frames(folder))
        hi = (measured or frame or {}).get("hi")
        if not hi:
            return {"ok": False, "error": "Could not measure this frame."}
        if min(h - b for b, h in zip(base, hi)) < 0.2:
            return {"ok": False, "error": "That spot is as dense as the picture itself, so it is not clear film. Click on "
                                          "the film between the frames or along its edge (in a negative it is the "
                                          "lightest, orange or yellow part), not on the holder."}
        warn = []
        if spread > 0.12:
            warn.append("The spot is uneven (an edge or dust?): if the colours look wrong, click a plainer bit of clear film.")
        if frame and any(b > f + 0.15 for b, f in zip(base, frame["lo"])):
            warn.append("Parts of the picture are thinner than that spot, so it may not be clear film.")
        d = {"mode": "base", "lo": [round(v, 4) for v in base], "hi": [round(float(v), 4) for v in hi],
             "base": [round(v, 4) for v in base], "from": rel, "spot": [round(x, 4), round(y, 4)]}
        try:
            filmfix.write_roll(folder, d)
        except OSError as e:
            return {"ok": False, "error": f"Could not save the film colour ({e.strerror or e})."}
        self.say(f"library: film base of {folder.name} picked on {path.name}")
        self._lib_colour_changed(folder)
        return {"ok": True, "warn": " ".join(warn), "base": d["base"], "film_colour": self._lib_colour_state(folder)}

    # ------------------------------------------------------------------ ticked pictures to the Re-develop page
    def library_send_to_develop(self, key, files):
        """Hand the ticked pictures of a folder to the Re-develop page: it opens their RAW folder with
        their RAW files ticked. Kept until the page takes it (once)."""
        folder = self._lib_folder(key)
        raw_dir = folder / "RAW"
        raws = {n.rsplit(".", 1)[0]: n for n in _tifs(raw_dir)} if raw_dir.is_dir() else {}
        pics = []
        for rel in sorted({str(f) for f in files or []}):
            stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            pics.append({"file": rel, "name": rel.rsplit("/", 1)[-1], "raw": raws.get(stem)})
        n = sum(1 for x in pics if x["raw"])
        if not n:
            return {"ok": False, "error": "None of these pictures has a RAW file (only Archive mode keeps one), so they cannot be developed again."}
        self.lib_handoff = {"key": key, "name": folder.name, "pictures": pics, "path": self._short_path(raw_dir), "raw_count": n}
        return {"ok": True, "raw_count": n, "without_raw": len(pics) - n}

    def library_redo(self, take=False):
        """What the Library handed to the Re-develop page (none, or one folder's pictures); take clears it."""
        h = getattr(self, "lib_handoff", None)
        if take:
            self.lib_handoff = None
        return {"ok": True, "rolls": [h] if h else [], "total": len(h["pictures"]) if h else 0}

    def library_rescan(self):
        """Every picture tagged to be scanned again, by roll, with its frame number and the film, so the
        strips can be found and put back in the scanner."""
        out = []
        for r in self.library_rolls()["rolls"]:
            if not r.get("rescan"):
                continue
            try:
                folder = self._lib_folder(r["key"])
            except ValueError:
                continue
            pics, film = [], ""
            for rel, m in sorted(self._lib_marks(folder)["pictures"].items()):
                if not (m.get("rescan") and (folder / rel).is_file()):
                    continue
                name = rel.rsplit("/", 1)[-1]
                f = re.search(r"_(\d{1,4})_R\d+", name) or re.search(r"_(\d{1,4})(?:_[A-Za-z]+)?\.tiff?$", name)
                pics.append({"file": rel, "name": name, "frame": int(f.group(1)) if f else None,
                             "caption": m.get("caption") or "", "v": int((folder / rel).stat().st_mtime), "edit": tidy_edit(m.get("edit"))})
                if not film:
                    try:
                        d = read_tiff_info(folder / rel).get("description") or ""
                        g = re.search(r"Film:\s*([^.\n]+)", d)
                        film = g.group(1).strip() if g else ""
                    except (OSError, ValueError):
                        pass
            if pics:
                pics.sort(key=lambda p: (p["frame"] is None, p["frame"] or 0, p["name"]))
                out.append({"key": r["key"], "name": r["name"], "film": film, "description": r.get("description") or "",
                            "path": self._short_path(folder), "pictures": pics})
        return {"ok": True, "rolls": out, "total": sum(len(r["pictures"]) for r in out)}

    def library_redevelop(self, key, rel):
        """Open the Re-develop page on a picture's RAW file, so its crop can be changed from the whole frame."""
        folder, path = self._lib_file(key, rel)
        raw = folder / "RAW"
        stem = path.name.rsplit(".", 1)[0]
        name = next((n for n in (path.name, f"{stem}.tif") if (raw / n).is_file()), None)
        if name is None:
            return {"ok": False, "error": "This picture has no RAW file (only Archive mode keeps one)."}
        got = self.develop_folder_now(path=str(raw))
        if not got.get("ok"):
            return got
        return {**self.develop_set({"current": name}), "name": name}
