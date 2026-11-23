"""Re-develop: make the finished pictures again from RAW files (Archive mode), without the scanner.

A RAW file is the untouched scan: red, green, blue and the infrared as a fourth channel, with
Nikon's scanner profile. Everything that happens after the scan can be done again from it with
other settings: the film type, the colour of negatives (each frame on its own, or a film
profile), the tone, the colour space, colour restoration, grain reduction, turning, cropping
and dust removal from the infrared. The driver's second-pass dust removal cannot: it scans
the film again.

The page (/develop) shows one file four ways, so they can be compared: as scanned, with the
dust removed, with film correction only, and with both (what is saved).

Nothing is ever overwritten. Each run saves into a new folder of its own:
    <roll folder>/Re-developed/<date time>/<same name as the RAW file>.tif
with a "Develop log.txt" that says what was done.
"""

import json
import os
import time
import traceback
from pathlib import Path

import numpy as np

from .colour import COLOUR_SPACES, shrink, to_srgb8
from .driver import Cancelled, clock, explain
from .imaging import encode_png, read_tiff16, read_tiff_info, write_tiff
from .rolls import COLOUR_FILE

DEVELOP_FOLDER = "Re-developed"
VIEWS = ("raw", "clean", "film", "final")
VIEW_WORDS = {"raw": "As scanned", "clean": "Dust removed", "film": "Film correction only",
              "final": "Dust removed + film correction"}
FILMS = ("negative", "positive", "kodachrome", "mono")
PIECE = (360, 480)   # rows, columns of the piece shown at full size
FILES_MOST = 500


def _turn(plane, k):
    return np.rot90(plane, k) if k % 4 else plane


def _cut(planes, rect):
    """Planes (already turned) cut to rect = [x0, y0, x1, y1], fractions; as they are without one."""
    if not rect:
        return planes
    rows, cols = planes[0].shape
    r0, r1 = sorted((int(round(rect[1] * rows)), int(round(rect[3] * rows))))
    c0, c1 = sorted((int(round(rect[0] * cols)), int(round(rect[2] * cols))))
    if r1 - r0 < 16 or c1 - c0 < 16:
        return planes
    return [np.ascontiguousarray(p[r0:r1, c0:c1]) for p in planes]


def _tidy_rect(rect):
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in rect)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


class DevelopMixin:
    def _dev_reset(self):
        self.dev = {
            "folder": None,       # the folder the RAW files are in
            "files": [],          # [{name, film, film_known, exposures, ir, size, scanned, turns, crop}]
            "current": None,      # the file on show
            "frames": {},         # file name -> {"turns": quarter turns on top of the file, "crop": rect or None}
            "how": {"film": "", "tone": self.settings["tone"], "film_profile": self.settings["film_profile"],
                    "colour_space": self.settings["colour_space"], "restore": int(self.settings["restore"]),
                    "grain": int(self.settings["grain"]), "clean": True,
                    "dust_method": "ice" if self.settings["dust_method"] == "ice" else "builtin"},
            "view": "final",
            "piece": [0.5, 0.5],
            "stale": False,       # the pictures on show do not match the settings yet
            "v": 0,               # goes up whenever the pictures change
            "notes": [],          # what the page should say about the file on show
            "results": None,      # what the last run saved
            "extras": False,      # also save the in-between steps
        }
        self.dev_cache = {}       # the file on show, read and worked on, so switching views is quick

    # ------------------------------------------------------------------ for the page
    def develop_state(self):
        d = self.dev
        return {"folder": self._short_path(d["folder"]) if d["folder"] else None, "files": d["files"],
                "current": d["current"], "frames": d["frames"], "how": d["how"], "view": d["view"],
                "piece": d["piece"], "stale": d["stale"], "v": d["v"], "notes": d["notes"],
                "results": d["results"], "extras": d["extras"], "views": list(VIEWS), "view_words": VIEW_WORDS,
                # the versions ready to show: only once they are of the file on show and up to date
                "have": sorted(self.dev_cache.get("pictures", {}))
                if self.dev_cache.get("name") == d["current"] and not d["stale"] else [],
                "ice_helper": self.ice_ready}

    def _dev_bump(self):
        with self.lock:
            self.version += 1

    # ------------------------------------------------------------------ choosing files
    def develop_folder(self, roll=None, path=None):
        """Look for RAW files: in a roll of the target folder (its RAW folder), or in a folder typed in."""
        if path:
            folder = Path(os.path.expanduser(str(path).strip()))
        elif roll:
            folder = self._base() / str(roll)
        else:
            folder = self.roll_dir()
        if not folder.is_dir():
            return {"ok": False, "error": f"There is no folder {self._short_path(folder)}."}
        if (folder / "RAW").is_dir():
            folder = folder / "RAW"
        files = []
        names = sorted(e.name for e in os.scandir(folder)
                       if e.is_file() and e.name.lower().endswith((".tif", ".tiff")) and not e.name.startswith(".")
                       and not e.name.lower().endswith(("_ir.tif", "_ir.tiff")))
        for name in names[:FILES_MOST]:
            try:
                info = read_tiff_info(folder / name)
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                continue
            if info["channels"] not in (3, 4):
                continue
            entry = self._dev_entry(folder, name, info)
            if entry:
                files.append(entry)
        if not files:
            return {"ok": False, "error": f"No RAW files in {self._short_path(folder)}. RAW files are made by "
                                          f"Archive mode, in each roll's RAW folder."}
        d = self.dev
        if not d["files"]:  # the first time: start from the settings in use on the scanning screen
            keep = d["how"]["clean"]
            self._dev_reset()
            d = self.dev
            d["how"]["clean"] = keep
        d["folder"], d["files"], d["frames"], d["results"] = folder, files, {}, None
        for f in files:
            d["frames"][f["name"]] = {"turns": 0, "crop": f.get("crop")}
        self.dev_cache = {}
        d["current"] = files[0]["name"]
        d["stale"] = True
        self.say(f"re-develop: {len(files)} file(s) in {self._short_path(folder)}")
        self._dev_bump()
        return {"ok": True, "files": len(files)}

    def _dev_entry(self, folder, name, info):
        """One file for the list: what it says about itself, and what can be done with it."""
        note = info.get("coolscan") or {}
        stem = name.rsplit(".", 1)[0]
        exposures = note.get("exposures") or self._dev_measured(folder).get(stem, {}).get("exposures") or {}
        ir = info["channels"] == 4 or (folder / f"{stem}_IR.tif").is_file()
        film = note.get("film") if note.get("film") in FILMS else None
        return {"name": name, "film": film, "exposures": bool(exposures), "ir": ir,
                "size": [info["cols"], info["rows"]], "dpi": info.get("dpi"),
                "scanned": note.get("scanned"), "crop": _tidy_rect(note["crop"]) if note.get("crop") else None,
                "note": bool(note)}

    def _dev_roll_folder(self, folder=None):
        folder = Path(folder or self.dev["folder"])
        return folder.parent if folder.name == "RAW" else folder

    def _dev_measured(self, folder):
        """The roll's FilmColour.json (what each scanned negative measured, with the pass's exposures)."""
        try:
            data = json.loads((self._dev_roll_folder(folder) / COLOUR_FILE).read_text(encoding="utf-8"))
            return {str(k): v for k, v in data["frames"].items() if isinstance(v, dict)}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    # ------------------------------------------------------------------ settings from the page
    def develop_set(self, body):
        """Change what the page shows or how files are developed. Pictures are made again only when needed."""
        d = self.dev
        again = False
        names = {f["name"] for f in d["files"]}
        if body.get("current") in names and body["current"] != d["current"]:
            d["current"], again = body["current"], True
        if isinstance(body.get("how"), dict):
            how = dict(d["how"])
            for k, v in body["how"].items():
                if k == "film" and (v in FILMS or v == ""):
                    how[k] = v
                elif k == "tone" and v in ("flat", "nikon"):
                    how[k] = v
                elif k == "film_profile" and (v == "" or v in self.settings["film_profiles"]):
                    how[k] = v
                elif k == "colour_space" and (v in COLOUR_SPACES or v == "scanner"):
                    how[k] = v
                elif k in ("restore", "grain") and str(v).isdigit() and 0 <= int(v) <= (3 if k == "restore" else 4):
                    how[k] = int(v)
                elif k == "clean":
                    how[k] = bool(v)
                elif k == "dust_method" and v in ("builtin", "ice"):
                    how[k] = v
            if how != d["how"]:
                d["how"], again = how, True
        frame = body.get("frame")
        if isinstance(frame, dict) and frame.get("name") in names:
            f = d["frames"].setdefault(frame["name"], {"turns": 0, "crop": None})
            if "turns" in frame:
                k = int(frame["turns"] or 0) % 4
                if k != f["turns"]:
                    f["turns"], f["crop"] = k, None  # a crop was marked on the picture as it was turned before
            if "crop" in frame:
                f["crop"] = _tidy_rect(frame["crop"]) if frame["crop"] else None
            if frame["name"] == d["current"]:
                d["v"] += 1
        if body.get("view") in VIEWS:
            d["view"] = body["view"]
        if isinstance(body.get("piece"), (list, tuple)) and len(body["piece"]) == 2:
            try:
                d["piece"] = [min(1.0, max(0.0, float(v))) for v in body["piece"]]
                d["v"] += 1
            except (TypeError, ValueError):
                pass
        if "extras" in body:
            d["extras"] = bool(body["extras"])
        if again:
            d["stale"] = True
        self._dev_bump()
        return {"ok": True, "stale": d["stale"]}

    # ------------------------------------------------------------------ the pictures on show
    def _dev_film(self, entry):
        """The film a file is developed as: the one chosen on the page, else what the file says, else the
        film set on the scanning screen. Second value: words for the page when it was a guess."""
        how = self.dev["how"]
        if how["film"]:
            return how["film"], ""
        if entry.get("film"):
            return entry["film"], ""
        return self._film(), (f"This file does not say which film it is (it was scanned before the app wrote that "
                              f"down), so it is developed as {self._film()}, the film set on the scanning screen. "
                              f"Pick the film above if that is wrong.")

    def _dev_read(self, name):
        """Read a file: its colour planes, its infrared (or None) and what it says about itself."""
        folder = self.dev["folder"]
        path = folder / name
        info = read_tiff_info(path)
        planes = read_tiff16(path)
        ir = planes[3] if len(planes) >= 4 else None
        planes = planes[:3]
        stem = name.rsplit(".", 1)[0]
        if ir is None and (folder / f"{stem}_IR.tif").is_file():
            try:
                other = read_tiff16(folder / f"{stem}_IR.tif")[0]
                ir = other if other.shape == planes[0].shape else None
            except (OSError, ValueError, KeyError):
                ir = None
        note = info.get("coolscan") or {}
        exposures = note.get("exposures") or self._dev_measured(folder).get(stem, {}).get("exposures") or None
        return planes, ir, info, exposures

    def _dev_clean(self, planes, ir, info, film, name):
        """Dust removal on a file as it is stored. A RAW file is stored turned the way the frame was turned
        when it was scanned; the cleaner works on the scan the way the scanner read it, so it is turned
        back for the cleaning (that way a frame comes out exactly as it did when it was scanned)."""
        k = int((info.get("coolscan") or {}).get("turns") or 0) % 4
        back = (lambda p: np.ascontiguousarray(np.rot90(p, -k))) if k else (lambda p: p)
        self.frame_times = {}
        out = self.clean_planes([back(p) for p in planes], back(ir), info.get("dpi") or 2900, 0, film=film,
                                who=name, method=self.dev["how"]["dust_method"])
        return [np.ascontiguousarray(np.rot90(p, k)) for p in out] if k else out

    def _dev_how(self, film):
        """develop settings -> what _positive takes."""
        how = self.dev["how"]
        profile = None
        if film == "negative" and how["film_profile"]:
            p = self.settings["film_profiles"].get(how["film_profile"])
            profile = p if isinstance(p, dict) else None
        return {"film": film, "tone": how["tone"], "profile": profile, "colour_space": how["colour_space"],
                "restore": how["restore"], "grain": how["grain"]}

    def develop_look(self):
        """Make the four pictures of the file on show (a job: it reads ~100 MB and cleans the dust)."""
        d = self.dev
        while d["stale"]:
            d["stale"] = False
            name = d["current"]
            entry = next((f for f in d["files"] if f["name"] == name), None)
            if entry is None:
                return
            cache = self.dev_cache
            if cache.get("name") != name:
                self.dev_cache = cache = {"name": name}
                self.say(f"re-develop: reading {name}")
                cache["planes"], cache["ir"], cache["info"], cache["exposures"] = self._dev_read(name)
            film, guess = self._dev_film(entry)
            how = d["how"]
            notes = [guess] if guess else []
            planes, ir, info, exposures = cache["planes"], cache["ir"], cache["info"], cache["exposures"]
            want_clean = how["clean"] and ir is not None
            key_clean = (film, how["dust_method"])
            if want_clean and cache.get("clean_key") != key_clean:
                cache["clean"] = self._dev_clean(planes, ir, info, film, name)
                cache["clean_key"] = key_clean
            if ir is None:
                notes.append("This file has no infrared, so there is no dust removal to show.")
            elif not how["clean"]:
                notes.append("Dust removal is off: the two dust views show the file without it.")
            notes += self._dev_profile_notes(film, exposures)
            if self.cancel:
                raise Cancelled
            hw = self._dev_how(film)
            icc = info.get("icc")
            clean = cache["clean"] if want_clean else planes
            pics = {"raw": ("scanned", planes), "clean": ("scanned", clean)}
            film_pos = self._positive(planes, icc, exposures, hw)
            final_pos = film_pos if clean is planes else self._positive(clean, icc, exposures, hw)
            space = hw["colour_space"] if hw["colour_space"] in COLOUR_SPACES else (
                "sRGB IEC61966-2.1" if film in ("negative", "mono") else "scanner")
            pics["film"] = ("positive", film_pos[0])
            pics["final"] = ("positive", final_pos[0])
            # one stretch for both views as scanned, so that only what the dust removal did differs; the
            # same for all three colours, so the film looks as it is (a colour negative stays orange)
            small = [shrink(p) for p in planes]
            got = [np.percentile(p, (0.1, 99.9)) for p in small]
            ends = [(min(g[0] for g in got), max(g[1] for g in got))] * len(planes)
            cache["pictures"] = pics
            cache["space"], cache["ends"], cache["film"] = space, ends, film
            cache["small"] = {}
            d["notes"] = notes
            d["v"] += 1
            self._dev_bump()

    def _dev_profile_notes(self, film, exposures):
        how = self.dev["how"]
        if film != "negative" or not how["film_profile"]:
            return []
        if how["film_profile"] not in self.settings["film_profiles"]:
            return ["That film profile is not there any more: each frame is coloured on its own."]
        if not exposures:
            return [f'The film profile "{how["film_profile"]}" is not used for this file: it needs the exposures '
                    f'of the scan, and this file was scanned before the app kept them (they are kept for every '
                    f'scan from now on). The colour is worked out from this frame on its own.']
        return []

    def _dev_8bit(self, view, planes=None, small=False):
        """A version of the file on show as an 8-bit sRGB picture (whole, or the planes given)."""
        cache = self.dev_cache
        kind, full = cache["pictures"][view]
        planes = planes if planes is not None else full
        if kind == "scanned":
            out = []
            for p, (lo, hi) in zip(planes, cache["ends"]):
                v = (p.astype(np.float32) - lo) / max(hi - lo, 1e-6)
                out.append(np.clip(v, 0, 1) ** np.float32(1 / 2.2))
            return np.dstack([(v * 255 + 0.5).astype(np.uint8) for v in out])
        return to_srgb8(planes, cache["space"])

    def develop_png(self, view, piece=False):
        """The picture of one version of the file on show, turned as asked: whole (at most 1600 px) or a
        piece at full size around the spot picked."""
        cache = self.dev_cache
        if view not in VIEWS or "pictures" not in cache:
            return None
        k = self.dev["frames"].get(cache["name"], {}).get("turns", 0)
        full = cache["pictures"][view][1]
        if not piece:
            got = cache["small"].get(view)
            if got is None:
                got = cache["small"][view] = self._dev_8bit(view, [shrink(p) for p in full])
            return encode_png(np.ascontiguousarray(np.rot90(got, k)) if k else got)
        turned = [_turn(p, k) for p in full]
        rows, cols = turned[0].shape
        h, w = min(PIECE[0], rows), min(PIECE[1], cols)
        x, y = self.dev["piece"]
        r0 = int(min(max(0, y * rows - h / 2), rows - h))
        c0 = int(min(max(0, x * cols - w / 2), cols - w))
        bit = [np.ascontiguousarray(p[r0:r0 + h, c0:c0 + w]) for p in turned]
        return encode_png(self._dev_8bit(view, bit))

    # ------------------------------------------------------------------ saving
    def _dev_out_folder(self):
        base = self._dev_roll_folder() / DEVELOP_FOLDER
        stamp = time.strftime("%Y-%m-%d %H.%M.%S")
        out, n = base / stamp, 2
        while out.exists():
            out, n = base / f"{stamp} ({n})", n + 1
        out.mkdir(parents=True)
        return out

    @staticmethod
    def _free_name(folder, name):
        path = folder / name
        n = 2
        while path.exists():
            stem, dot, ext = name.rpartition(".")
            path, n = folder / f"{stem} ({n}).{ext}", n + 1
        return path

    def _dev_words(self, film):
        how = self.dev["how"]
        space = how["colour_space"] if how["colour_space"] in COLOUR_SPACES else "scanner RGB"
        parts = [f"developed as {film}", space]
        if film in ("negative", "mono"):
            parts.append("Nikon Scan tone" if how["tone"] == "nikon" else "flat tone")
        if film == "negative":
            parts.append(f'film profile "{how["film_profile"]}"' if how["film_profile"] else "colour of each frame on its own")
        parts.append(("dust removal by the Nikon ICE helper" if how["dust_method"] == "ice" and self.ice_ready
                      else "built-in dust removal") if how["clean"] else "no dust removal")
        if how["restore"]:
            parts.append(f"colour restoration {how['restore']}")
        if how["grain"]:
            parts.append(f"grain reduction {how['grain']}")
        return ", ".join(parts)

    def develop_run(self, names):
        """Develop the files named (all of them when none are) into a new folder of their own."""
        d = self.dev
        if not d["files"]:
            raise RuntimeError("Pick a roll or a folder with RAW files first.")
        wanted = [f for f in d["files"] if not names or f["name"] in names]
        if not wanted:
            raise RuntimeError("None of those files is in the list any more.")
        out = self._dev_out_folder()
        logf = out / "Develop log.txt"
        extras = d["extras"]

        def log(line):
            try:
                with open(logf, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

        log(f"Coolscan re-develop, {time.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"From: {d['folder']}")
        log(f"Files: {len(wanted)}" + (" (with the in-between steps as well)" if extras else ""))
        started = time.time()
        saved, failed = [], []
        d["results"] = {"folder": self._short_path(out), "saved": saved, "failed": failed, "running": True}
        self._dev_bump()
        try:
            for k, entry in enumerate(wanted):
                if self.cancel:
                    raise Cancelled
                name = entry["name"]
                self.progress = {"phase": f"{name} ({k + 1} of {len(wanted)})", "done": k, "total": len(wanted)}
                try:
                    files = self._dev_one(entry, out, extras, log)
                    saved.extend(files)
                except Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
                    traceback.print_exc()
                    failed.append(name)
                    log(f"{name}: FAILED, {explain(e)}")
                    self.say(f"re-develop: {name} failed, carrying on. {explain(e)}")
                self._dev_bump()
        except Cancelled:
            log("Stopped by the user.")
            raise
        finally:
            took = time.time() - started
            log(f"Done: {len(wanted) - len(failed)} of {len(wanted)} file(s) in {clock(took)}")
            d["results"]["running"] = False
            d["results"]["took"] = clock(took)
            self._dev_bump()
        self.say(f"re-develop finished: {len(wanted) - len(failed)} of {len(wanted)} file(s) saved in "
                 f"{self._short_path(out)}, in {clock(took)}")

    def _dev_one(self, entry, out, extras, log):
        name = entry["name"]
        stem = name.rsplit(".", 1)[0]
        cache = self.dev_cache
        if cache.get("name") == name:
            planes, ir, info, exposures = cache["planes"], cache["ir"], cache["info"], cache["exposures"]
        else:
            planes, ir, info, exposures = self._dev_read(name)
        film, guess = self._dev_film(entry)
        how = self.dev["how"]
        frame = self.dev["frames"].get(name, {"turns": 0, "crop": None})
        icc = info.get("icc")
        dpi = info.get("dpi") or 2900
        clean = planes
        if how["clean"] and ir is not None:
            key = (film, how["dust_method"])
            if cache.get("name") == name and cache.get("clean_key") == key:
                clean = cache["clean"]
            else:
                clean = self._dev_clean(planes, ir, info, film, name)
        hw = self._dev_how(film)
        k = frame["turns"]

        def ready(p):  # turned and cut the way the page shows it
            return _cut([np.ascontiguousarray(_turn(x, k)) for x in p], frame["crop"])

        words = self._dev_words(film)
        meta = {"make": info.get("make") or "Nikon", "model": info.get("model"),
                "description": f"Re-developed {time.strftime('%Y-%m-%d %H:%M')} from {name} ({words}). "
                               + (info.get("description") or ""),
                "taken": info.get("taken"), "iso": info.get("iso")}
        srgb_tag = lambda prof: 1 if prof and b"sRGB" in prof[:600] else 65535
        files = []
        pos, pos_icc = self._positive(ready(clean), icc, exposures, hw)
        path = self._free_name(out, f"{stem}.tif")
        write_tiff(path, pos, dpi, pos_icc, meta={**meta, "colorspace": srgb_tag(pos_icc)})
        files.append(path.name)
        if extras:
            if clean is not planes:
                p2 = self._free_name(out, f"{stem}_dust-removed-as-scanned.tif")
                write_tiff(p2, ready(clean), dpi, icc, meta=meta)
                files.append(p2.name)
                p3 = self._free_name(out, f"{stem}_no-dust-removal.tif")
                alt, alt_icc = self._positive(ready(planes), icc, exposures, hw)
                write_tiff(p3, alt, dpi, alt_icc, meta={**meta, "colorspace": srgb_tag(alt_icc)})
                files.append(p3.name)
        rows, cols = pos[0].shape
        extra_words = [w for w in self._dev_profile_notes(film, exposures)] + ([guess] if guess else [])
        log(f"{name} -> {', '.join(files)}: {words}, {cols}x{rows} px"
            + (f", turned {k * 90} degrees" if k else "") + (", cropped" if frame["crop"] else "")
            + ("" if not extra_words else ". " + " ".join(extra_words)))
        self.say(f"re-develop: {name} saved as {', '.join(files)}")
        return files
