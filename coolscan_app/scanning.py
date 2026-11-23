"""Scanning frames and saving them."""

import re
import struct
import subprocess
import time
import traceback
import types
from pathlib import Path

import numpy as np

from .colour import COLOUR_SPACES, NIKON_MODEL_NAMES, convert_planes, frame_colour, invert_negative, shrink
from .driver import Cancelled, _lost_scanner, _stopped, _this_frame_only, clock, explain
from .dust import dust_clean
from .enhance import merge_exposures, pass_shift, reduce_grain, restore_colour
from .imaging import read_tiff_rgb16, write_tiff


#
# If a program called ice_clean sits next to coolscan.py, it can be chosen for dust
# removal instead of the built-in method. The app hands it the scan as a 16-bit
# TIFF with four channels (red, green, blue, infrared; as scanned, not inverted,
# not rotated) and reads back a 16-bit RGB TIFF of the same size:
#
#   ice_clean INPUT.tif OUTPUT.tif --film negative --dpi 2900 --model "LS-40 ED"
#
# Exit code 0 means OUTPUT.tif is good. Anything else: the built-in method is used.

ICE_HELPER = Path(__file__).resolve().parent.parent / "ice_clean"
ICE_TIMEOUT = 600  # seconds per frame


class ScanMixin:
    def _meta(self, n):
        """EXIF for a saved file: the date set for the photos (if any) and when it was scanned."""
        import datetime
        c = self.caps
        product = (getattr(c, "product", None) or "Coolscan").strip()
        meta = {"make": "Nikon", "model": NIKON_MODEL_NAMES.get(product, product),
                "description": time.strftime("Scanned %Y-%m-%d %H:%M with Coolscan (nkscan)")}
        film = self._film_text()
        if film:
            meta["description"] = f"Film: {film}. " + meta["description"]
        if self.settings["roll"]:
            meta["description"] += f". Roll: {self.settings['roll']}"
        iso = re.sub(r"\D", "", str(self.settings["film_iso"]))
        if iso:
            meta["iso"] = int(iso)
        text = (self.settings["photo_date"] or "").strip()
        if text:
            try:
                taken = self._taken(text, n, datetime.datetime.now())
                last = self.last_taken
                if last is not None and datetime.timedelta(0) <= last - taken < datetime.timedelta(seconds=5):
                    taken = last + datetime.timedelta(seconds=1)  # frames a moment apart still sort in scan order
                meta["taken"] = self.last_taken = taken
            except (ValueError, IndexError, OverflowError):
                self.say(f"can't read the photo date {text!r}; use e.g. 2005 or 2005-06-14. Using today.")
        return meta

    @staticmethod
    def _taken(text, n, now):
        """The "date taken" for frame number n from what was typed, scanned at the moment `now`.

        Only a year: the day and time of the scan, in that year, so the files keep the real
        order and spacing of the scanning. A full date: that day at noon, one second per
        frame, so galleries keep the frames in order.
        """
        parts = [int(x) for x in re.split(r"[-./ ]", text) if x]
        if len(parts) == 1:
            now = now.replace(microsecond=0)
            try:
                return now.replace(year=parts[0])
            except ValueError:
                if (now.month, now.day) != (2, 29):
                    raise
                return now.replace(year=parts[0], day=28)  # 29 February, in a year that has none
        y, m, d = (parts + [1, 1])[:3]
        import datetime
        return datetime.datetime(y, m, d, 12, 0, 0) + datetime.timedelta(seconds=n)

    def _positive(self, planes, nikon_icc, exposures=None):
        """The finished picture in the chosen colour space, and the profile to embed.

        Slides go through Nikon's scanner profile into the colour space. Negatives
        are inverted first; that result is sRGB-like, so it is converted from sRGB.
        "Scanner RGB" keeps Nikon's profile on slides, and labels negatives sRGB.
        """
        space = self.settings["colour_space"]
        restore, grain = int(self.settings["restore"]), int(self.settings["grain"])
        if restore or grain:
            self.say(" and ".join(n for n, on in (("colour restoration", restore), ("grain reduction", grain)) if on)
                     + " on this frame")
        if self._film() in ("negative", "mono"):
            pos = invert_negative(planes, self.settings["tone"], self._film_profile(), exposures)
            pos = reduce_grain(restore_colour(pos, restore), grain, self._progress)
            pos = [(v * 65535 + 0.5).astype(np.uint16) for v in pos]
            if space not in COLOUR_SPACES:
                space = "sRGB IEC61966-2.1"
            return convert_planes(pos, space)
        if restore or grain:
            # slides: work on the square root of the scan, where tones and grain are spread more evenly
            work = [np.sqrt(p.astype(np.float32) * np.float32(1 / 65535)) for p in planes]
            work = reduce_grain(restore_colour(work, restore), grain, self._progress)
            planes = [(np.clip(v, 0, 1) ** 2 * 65535 + 0.5).astype(np.uint16) for v in work]
        if space not in COLOUR_SPACES or not nikon_icc:
            return planes, nikon_icc
        try:
            return convert_planes(planes, space, nikon_icc)
        except (ValueError, KeyError, struct.error) as e:
            self.say(f"could not convert to {space} ({e}); keeping Nikon's profile")
            return planes, nikon_icc

    def scan(self, indices):
        s = self.need_session()
        d = self._ensure_discovery()
        indices = [i for i in (indices or range(len(d.frames))) if 0 <= i < len(d.frames)]
        if not indices:
            raise RuntimeError("Those frames are not on this film. Press Thumbnail only, then pick the frames again.")
        st = self.settings
        outdir = self.roll_dir()
        archive = bool(st["archive"])
        raw_dir, pos_dir = (outdir / "RAW", outdir / "Positive") if archive else (outdir, outdir)
        folders = [raw_dir, pos_dir] if archive else [outdir]
        for f in folders:
            f.mkdir(parents=True, exist_ok=True)
        self._write_description()
        clean = bool(st["clean"])
        job = dict(archive=archive, clean=clean, infrared=archive or bool(st["ir"] or clean),
                   raw_dir=raw_dir, pos_dir=pos_dir, outdir=outdir)
        icc = s.nikon_profile(self._film())
        job["icc"] = bytes(icc) if icc else None
        if clean and st["dust_method"] == "ice" and not self.check_ice():
            self.say("the Nikon ICE helper is not ready (run nikon_ice/setup_mac.sh); using the built-in dust removal")
        log = self._scan_log_open(outdir, len(indices))
        name_folders = self._name_folders()
        self.batch_day = time.strftime("%Y%m%d")
        n = self.next_number()
        done, failed, ending = 0, [], "completed"
        strip_started = time.time()
        self.timing = {"strip_started": strip_started, "of": len(indices), "done": 0}
        roll = st["roll"] or outdir.name
        roll_plain = (re.match(r"Roll \d+", roll) or [roll])[0]
        try:
            for i in indices:
                if self.cancel:
                    raise Cancelled
                n = self._next_number(name_folders, n)
                stem = self._stem(folders, n)
                saved = []
                log(f"Frame {i + 1} started {time.strftime('%H:%M:%S')}")
                frame_started = time.time()
                self.frame_times = {}
                self.timing.update(frame=i + 1, frame_started=frame_started)
                try:
                    self._scan_one(s, d, i, n, stem, job, saved)
                except Exception as e:  # noqa: BLE001
                    if saved:
                        log("Saved as " + ", ".join(saved))
                    if _stopped(e):
                        log("Stopped before this frame was finished." if saved else "Stopped. The image was not saved.")
                        raise Cancelled from None
                    log(f"Scan failed: {explain(e)}")
                    if not saved:
                        log("The image was not saved.")
                    if st["stop_on_errors"] or not _this_frame_only(e):
                        raise
                    failed.append(i + 1)
                    self.say(f"frame {i + 1} failed, carrying on with the next one. {explain(e)}")
                    continue
                log("Saved as " + ", ".join(saved))
                done += 1
                n += 1
                took = time.time() - frame_started
                self.frame_saved.setdefault(i, []).append({"files": list(saved), "took": took})
                words, waited = self._frame_times_text(took)
                self.say(f"frame {i + 1} done in {words}")
                if waited:
                    self.say(f"frame {i + 1}: {waited}")
                log(f"Frame {i + 1} took {words}" + (f". {waited[0].upper() + waited[1:]}" if waited else ""))
                self.timing.update(done=done, last_frame=took, average=(time.time() - strip_started) / done)
        except Cancelled:
            ending = "stopped by the user"
            raise
        except Exception as e:
            ending = "aborted"
            words = f"Scanning stopped after {done} of {len(indices)} frames in %s. " + explain(e).split(" [")[0]
            self.notify(words % roll, words % roll_plain)
            raise
        finally:
            self.batch_day = None
            strip_took = time.time() - strip_started
            self.timing = {"last_strip": {"frames": done, "of": len(indices), "seconds": strip_took,
                                          "average": strip_took / done if done else None,
                                          "ended": time.strftime("%H:%M")}}
            if done and int(st["start"] or 0) > 0:
                st["start"] = 0  # a typed number is used once; after that the roll counts on by itself
                self.save_settings()
            missed = f", {len(failed)} failed (frame {', '.join(map(str, failed))})" if failed else ""
            each = f", {clock(strip_took / done)} per frame" if done else ""
            log(f"Batch scan {ending} {time.strftime('%H:%M:%S')}: {done} of {len(indices)} frame(s) saved{missed}."
                f" It took {clock(strip_took)}{each}")
            log("")
        self.say(f"scan finished: {done} of {len(indices)} frame(s) saved{missed}, in {clock(strip_took)}{each}")
        words = f"Strip done: {done} of {len(indices)} frames saved in %s{missed}."
        self.notify(words % roll, words % roll_plain)
        if st["eject_after"]:
            if failed:
                self.say("not ejecting, because a frame failed and you may want to scan it again")
            else:
                self.eject()

    def _frame_times_text(self, took):
        """How long a frame took, in words, and (when the driver said) whether the scanner had to wait."""
        t = self.frame_times
        parts = [f"{name} {clock(t[key])}" for key, name in (("scan", "scanning"), ("clean", "dust removal")) if t.get(key)]
        rest = took - sum(t.get(key, 0) for key in ("scan", "clean"))
        if parts and rest >= 1:
            parts.append(f"the rest {clock(rest)}")
        words = clock(took) + (f" ({', '.join(parts)})" if parts else "")
        waited = ""
        if t.get("passes"):
            w = t.get("waited", 0.0)
            if w < 0.05:
                waited = "the scanner never had to wait for the computer (nothing held its stage up)"
            else:
                waited = (f"the scanner had to wait {w:.1f} s for the computer"
                          + (" (when that happens its stage stops and starts)" if w >= 0.5 else " (too short to matter)"))
        return words, waited

    def _scan_log_open(self, folder, count):
        """Like Nikon Scan's ScanLog.txt: one text file per roll folder saying what each batch did.

        Returns a function that adds a line. Trouble writing the log never stops a scan.
        """
        st = self.settings
        if not st["scan_log"]:
            return lambda line: None
        path = folder / "ScanLog.txt"
        state = {"ok": True}

        def add(line):
            if not state["ok"]:
                return
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                state["ok"] = False
                self.say(f"could not write {path.name} ({e.strerror}); scanning goes on without the log")

        c = self.caps
        firmware = str(getattr(c, "revision", "") or "").strip()
        space = st["colour_space"] if st["colour_space"] in COLOUR_SPACES else "scanner RGB (Nikon profile)"
        film_names = {"negative": "colour negative", "positive": "slide", "kodachrome": "Kodachrome", "mono": "B&W negative"}
        add(f"Coolscan scan log, {time.strftime('%Y-%m-%d %H:%M:%S')}")
        add(f"Scanner: {getattr(c, 'product', 'unknown')}" + (f", firmware {firmware}" if firmware else ""))
        add(f"Folder: {folder}")
        add(f"Film: {film_names.get(self._film(), self._film())}" + (f", {self._film_text()}" if self._film_text() else ""))
        add(f"Settings: {st['dpi']} dpi, {st['samples']} sample(s), {space}, "
            f"dust removal {'on' if st['clean'] else 'off'}" + (", archive mode" if st["archive"] else "")
            + (f", Multi-Exposure {st['multi_exposure']}x" if int(st["multi_exposure"] or 0) in (2, 4) else "")
            + (f', film profile "{st["film_profile"]}"' if self._film_profile() is not None else ""))
        add(f"Frames to scan: {count}")
        return add

    def _dust_method(self):
        """How dust is removed: "builtin" (the app's own cleaner, on the scan in memory), "ice" (the
        helper) or "pass" (nkscan's cleaner, the older method)."""
        method = self.settings["dust_method"]
        if method not in ("builtin", "ice", "pass") or (method == "ice" and not self.ice_ready):
            method = "builtin"
        return method

    def _scan_one(self, s, d, i, n, stem, job, saved):
        """Scan frame i and write its files. Each file written is added to `saved`."""
        st = self.settings
        icc, clean, infrared = job["icc"], job["clean"], job["infrared"]
        raw_dir, pos_dir, outdir = job["raw_dir"], job["pos_dir"], job["outdir"]
        meta = self._meta(n)
        want = max(1, int(st["samples"] or 1))
        passes = 1
        if want > max(1, int(getattr(self.caps, "max_samples", 1) or 1)):
            passes, want = min(want, 4), 1  # the scanner reads a line once: average whole passes instead
        base = dict(dpi=int(st["dpi"]), samples=want, progress=self._progress)
        opts = self._scan_options(s, i, d.frames[i], infrared)
        method = self._dust_method() if clean else None
        longer = int(st["multi_exposure"] or 0)
        if longer not in (2, 4):
            longer = 0
        if longer and passes > 1:
            self.say(f"frame {i + 1}: Multi-Exposure is left out while multi-sample averages passes")
            longer = 0
        if (passes > 1 or longer) and method == "pass":
            method = "builtin"  # the driver can only clean one pass; what is put together here is cleaned here

        def take_now(**kw):
            if longer:
                return self._multi_exposed(s, i, d.frames[i], longer, kw, base, opts)
            if passes == 1:
                return self._driver_scan(s, d.frames[i], **kw, **base, **opts)
            return self._averaged(s, i, d.frames[i], passes, kw, base, opts)

        def take(**kw):
            cache = self.test_pass_cache
            if cache is None:
                return take_now(**kw)
            # test mode: runs that need the same kind of pass over the frame share one, so that only
            # what the runs do differently shows in the results (and in their times)
            def key_for(**how):
                return (i, base["dpi"], want, passes, longer, repr(sorted(opts.items())), tuple(sorted(how.items())))
            key = key_for(**kw)
            hit = cache.get(key)
            if hit is None and not kw.get("infrared"):
                hit = cache.get(key_for(**dict(kw, infrared=True)))  # the same pass, with the infrared read as well
            if hit is not None and not self.test_rescan:
                self.test_reused = True
                self.say(f"frame {i + 1}: using the pass an earlier run took (same resolution, samples, focus and exposure)")
                return hit
            r = take_now(**kw)
            if key not in cache:  # a run that asked for its own scan keeps it to itself: the shared pass stays the first
                for plane in list(r.colors.values()) + ([r.ir] if r.ir is not None else []):
                    plane.setflags(write=False)  # shared from here on: nothing may change it in place
                cache[key] = r
            return r

        def as_scanned(r):  # the colour planes the way the scanner sent them
            return [r.colors[k] for k in ("red", "green", "blue") if k in r.colors] or list(r.colors.values())

        def turned(planes):  # the way the person turned this frame
            return [self._rot(p, i) for p in planes]

        srgb_tag = lambda prof: 1 if prof and b"sRGB" in prof[:600] else 65535

        def ir_file():  # the infrared picture as a file of its own; in a test, once per pass and not once per run
            return bool(st["ir"]) and not (self.testing and self.test_reused)
        if job["archive"]:
            # one pass: the untouched scan with its infrared, then the finished picture made from it
            self.say(f"frame {i + 1}: archive scan at {st['dpi']} dpi (raw, with infrared)"
                     + (f", {passes} passes" if passes > 1 else "") + (f", Multi-Exposure {longer}x" if longer else ""))
            r = take(infrared=True, clean=False)
            raw = as_scanned(r)
            planes4 = turned(raw) + ([self._rot(r.ir, i)] if r.ir is not None else [])
            write_tiff(raw_dir / f"{stem}.tif", planes4, r.dpi, icc, meta=meta)
            saved.append(f"RAW/{stem}.tif")
            self.say(f"saved RAW/{stem}.tif" + ("" if r.complete else " (warning: pass came back short)"))
            if r.ir is None:
                self.say("warning: the scanner sent no infrared for this frame")
            elif ir_file():
                # the IR is already the 4th channel of the RAW file; this copy is easy to open
                write_tiff(raw_dir / f"{stem}_IR.tif", [self._rot(r.ir, i)], r.dpi, meta=meta)
                saved.append(f"RAW/{stem}_IR.tif")
                self.say(f"saved RAW/{stem}_IR.tif")
            finished = raw
            if method == "pass":
                finished = self.second_pass(s, i, d.frames[i], r, base, opts, as_scanned)
            elif method:
                finished = self.clean_planes(raw, r.ir, r.dpi, i)
            self._measure_colour(stem, finished, r)
            pos, pos_icc = self._positive(self._cropped(i, turned(finished)), icc, getattr(r, "exposures", None))
            write_tiff(pos_dir / f"{stem}.tif", pos, r.dpi, pos_icc, meta={**meta, "colorspace": srgb_tag(pos_icc)})
            saved.append(f"Positive/{stem}.tif")
            self.say(f"saved Positive/{stem}.tif" + self._profile_words(r) + self._crop_words(i, pos))
            self._show_scan(i, finished, r, f"Positive/{stem}.tif", pos)
            return
        # not archive: the built-in cleaner works on the scan in memory once the pass is in; only the
        # older method ("pass") has the driver clean inside the pass
        in_pass = method == "pass" and passes == 1
        self.say(f"frame {i + 1}: scanning at {st['dpi']} dpi"
                 + (f", {passes} passes" if passes > 1 else f", {want} sample(s)" if want > 1 else "")
                 + (f", Multi-Exposure {longer}x" if longer else "") + (", cleaning dust" if in_pass else ""))
        r = take(infrared=infrared, clean=in_pass)
        planes = as_scanned(r)
        if method and not in_pass:
            planes = self.clean_planes(planes, r.ir, r.dpi, i)
        whole = planes
        self._measure_colour(stem, whole, r)
        planes = self._cropped(i, turned(planes))
        note = "" if r.complete else " (warning: the pass came back short)"
        negative = self._film() in ("negative", "mono")
        if negative and st["negatives"] in ("negative", "both"):
            raw_name = f"{stem}.tif" if st["negatives"] == "negative" else f"{stem}_negative.tif"
            write_tiff(outdir / raw_name, planes, r.dpi, icc, meta=meta)  # with Nikon's profile
            saved.append(raw_name)
            self.say(f"saved {raw_name}{note}")
        if not negative or st["negatives"] in ("positive", "both"):
            pos, pos_icc = self._positive(planes, icc, getattr(r, "exposures", None))
            write_tiff(outdir / f"{stem}.tif", pos, r.dpi, pos_icc, meta={**meta, "colorspace": srgb_tag(pos_icc)})
            saved.append(f"{stem}.tif")
            self.say(f"saved {stem}.tif ({st['colour_space'] if st['colour_space'] in COLOUR_SPACES else 'scanner RGB'}){note}"
                     + self._profile_words(r) + self._crop_words(i, pos))
        if r.ir is not None and ir_file():
            write_tiff(outdir / f"{stem}_IR.tif", self._cropped(i, [self._rot(r.ir, i)]), r.dpi, meta=meta)
            saved.append(f"{stem}_IR.tif")
            self.say(f"saved {stem}_IR.tif")
        if r.cleaned is not None:
            self.say(f"dust removal rebuilt {r.cleaned} pixels")
        self._show_scan(i, whole, r, saved[0] if saved else "", planes)

    def _measure_colour(self, stem, planes, r):
        """Note what this frame's film measured, for making a film profile from the roll later."""
        if self._film() != "negative" or len(planes) != 3 or self.testing:
            return  # (a test run is not part of the roll)
        try:
            self._note_colour(stem, frame_colour(planes, getattr(r, "exposures", None)), getattr(r, "exposures", None))
        except Exception:  # noqa: BLE001 - an extra: the scan itself must go on
            traceback.print_exc()

    def _profile_words(self, r):
        """For the Activity list: which film profile coloured this frame, if one did."""
        if self._film_profile() is None:
            return ""
        if not getattr(r, "exposures", None):
            return ", film profile not used (the pass has no exposures)"
        return f', film profile "{self.settings["film_profile"]}"'

    def _crop_words(self, i, planes):
        if not self.crops.get(i):
            return ""
        rows, cols = planes[0].shape
        return f", cropped to {cols}x{rows} px"

    def _show_scan(self, i, planes, r, name, saved_planes):
        """After a frame is saved, the screen shows the scan itself in place of the thumbnail or preview."""
        if self.testing:
            return  # a test run is not a scan of the roll: the ordinary screen keeps what it showed
        try:
            names = ("red", "green", "blue") if len(planes) == 3 else ("gray",)
            rows, cols = saved_planes[0].shape
            self._show(i, {n: shrink(p) for n, p in zip(names, planes)}, "scan",
                       f"saved as {name}, {cols}x{rows} px at {r.dpi} dpi" + (", cropped" if self.crops.get(i) else ""),
                       exposures=getattr(r, "exposures", None))
        except Exception:  # noqa: BLE001 - the picture on screen is an extra: the files are already saved
            traceback.print_exc()

    def _multi_exposed(self, s, i, frame, factor, kw, base, opts):
        """Multi-Exposure: the normal pass, then one exposed `factor` times longer at the same lens
        position, merged so that dense film comes from the longer pass and thin film from the
        normal one. Whatever goes wrong with the second pass, the frame is kept from the first."""
        kw = dict(kw, clean=False)
        first = self._driver_scan(s, frame, **kw, **base, **opts)
        names = list(first.colors)
        key = "green" if "green" in first.colors else names[0]
        again = dict(opts)
        pos = first.focus_position
        again["focus"] = int(pos) if pos is not None else "hold"
        again["exposures"] = {k: int(v) * factor for k, v in dict(first.exposures).items() if k in first.colors}

        def first_alone(why):
            self.say(f"frame {i + 1}: Multi-Exposure left out, {why}; this frame is saved from the normal pass")
            return first

        if len(again["exposures"]) != len(names):
            return first_alone("the first pass did not say what it was exposed at")
        if self.cancel:
            raise Cancelled
        self.say(f"frame {i + 1}: Multi-Exposure, second pass exposed {factor} times longer (same focus)")
        try:
            second = self._driver_scan(s, frame, **dict(kw, infrared=False), **base, **again)
        except Exception as e:  # noqa: BLE001
            if _stopped(e) or _lost_scanner(e):
                raise
            return first_alone(f"the scanner would not take the longer exposure [{type(e).__name__}: {e}]")
        if any(n not in second.colors or second.colors[n].shape != first.colors[n].shape for n in names):
            return first_alone("the second pass came back a different size")
        off = pass_shift(first.colors[key], second.colors[key])
        if off is not None and off > 0.5:
            return first_alone(f"the second pass sits {off:.1f} pixel off the first")
        merged, shares = {}, []
        for n in names:
            got = merge_exposures(first.colors[n], second.colors[n], factor)
            if got is None:
                return first_alone(f"the two passes do not fit together in {n}")
            merged[n], share = got
            shares.append(share)
        self.say(f"frame {i + 1}: Multi-Exposure merged, {100 * sum(shares) / len(shares):.0f}% of the picture read "
                 f"from the longer pass" + (f", passes within {off:.2f} pixel" if off is not None else ""))
        return types.SimpleNamespace(
            colors=merged, ir=first.ir, dpi=first.dpi, rows=getattr(first, "rows", None),
            cols=getattr(first, "cols", None), exposures=first.exposures,
            complete=first.complete and second.complete, cleaned=None,
            focused=first.focused, focus_position=first.focus_position)

    def _averaged(self, s, i, frame, passes, kw, base, opts):
        """Multi-sample for a scanner that reads each line once: whole passes, averaged.

        Every pass after the first uses the first one's exposure and lens position.
        A pass that does not land on the first within half a pixel is left out.
        """
        kw = dict(kw, clean=False)
        first = self._driver_scan(s, frame, **kw, **base, **opts)
        names = list(first.colors)
        key = "green" if "green" in first.colors else names[0]
        total = {k: first.colors[k].astype(np.uint32) for k in names}
        ir_total = first.ir.astype(np.uint32) if first.ir is not None else None
        again = dict(opts)
        again["exposures"] = dict(first.exposures)
        pos = first.focus_position
        again["focus"] = int(pos) if pos is not None else "hold"
        used = 1
        for k in range(1, passes):
            if self.cancel:
                raise Cancelled
            self.say(f"frame {i + 1}: pass {k + 1} of {passes} (same focus and exposure)")
            r = self._driver_scan(s, frame, **kw, **base, **again)
            if r.colors[key].shape != first.colors[key].shape or (ir_total is not None and r.ir is None):
                self.say(f"frame {i + 1}: pass {k + 1} came back a different size, left out")
                continue
            off = pass_shift(first.colors[key], r.colors[key])
            if off is not None and off > 0.5:
                self.say(f"frame {i + 1}: pass {k + 1} sits {off:.1f} pixel off the first, left out")
                continue
            for name in names:
                total[name] += r.colors[name]
            if ir_total is not None:
                ir_total += r.ir
            used += 1
            self.say(f"frame {i + 1}: pass {k + 1} lines up"
                     + (f" within {off:.2f} pixel" if off is not None else " (too little detail to measure)"))

        def mean(v):
            return ((v + used // 2) // used).astype(np.uint16)

        self.say(f"frame {i + 1}: averaged {used} passes")
        return types.SimpleNamespace(
            colors={k: mean(v) for k, v in total.items()}, ir=mean(ir_total) if ir_total is not None else None,
            dpi=first.dpi, rows=getattr(first, "rows", None), cols=getattr(first, "cols", None),
            exposures=first.exposures, complete=first.complete, cleaned=None,
            focused=first.focused, focus_position=first.focus_position)

    def clean_planes(self, planes, ir, dpi, i):
        """Dust and scratch removal on a finished pass, as scanned. Returns the cleaned planes.

        The built-in cleaner (dust.py) runs here on the scan in memory, so the
        untouched scan can be kept as well. The Nikon ICE helper is used instead
        when it is there and chosen.
        """
        if ir is None or len(planes) != 3:
            self.say(f"frame {i + 1}: dust removal needs red, green, blue and infrared; left as scanned")
            return planes
        if self._dust_method() == "ice":
            try:
                return self._ice_clean(planes, ir, dpi, i)
            except Exception as e:  # noqa: BLE001 - any trouble with the helper: carry on without it
                reason = "it took too long" if type(e).__name__ == "TimeoutExpired" else str(e)
                self.say(f"frame {i + 1}: the Nikon ICE helper failed ({reason}); using the built-in dust removal")
        started = time.time()
        try:
            result = dust_clean(planes, ir, dpi, negative=self._film() in ("negative", "mono"),
                                progress=self._progress)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 - never lose the picture over the cleaning
            traceback.print_exc()
            self.say(f"frame {i + 1}: WARNING, dust removal failed ({type(e).__name__}: {e}); "
                     f"this picture is saved as scanned, with the dust still on it")
            return planes
        if result is None:
            self.say(f"frame {i + 1}: no clear film to measure the infrared against; dust removal skipped")
            return planes
        cleaned, count = result
        self.frame_times["clean"] = self.frame_times.get("clean", 0.0) + time.time() - started
        self.say(f"frame {i + 1}: dust removal rebuilt {count} pixels in {time.time() - started:.0f} s")
        return cleaned

    def _ice_clean(self, planes, ir, dpi, i):
        import tempfile
        self.say(f"frame {i + 1}: dust removal by the Nikon ICE helper")
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="coolscan-ice-") as tmp:
            src, dst = Path(tmp) / "input.tif", Path(tmp) / "output.tif"
            write_tiff(src, list(planes) + [ir], dpi)
            product = (getattr(self.caps, "product", None) or "").strip()
            run = subprocess.run(self._ice_cmd() + [str(src), str(dst), "--film", self._film(),
                                  "--dpi", str(int(dpi)), "--model", product],
                                 capture_output=True, text=True, timeout=ICE_TIMEOUT)
            if run.returncode != 0:
                lines = (run.stderr or run.stdout or "").strip().splitlines()
                raise RuntimeError(lines[-1][:200] if lines else f"exit code {run.returncode}")
            cleaned = read_tiff_rgb16(dst)
        if cleaned[0].shape != planes[0].shape:
            raise RuntimeError("its picture has a different size")
        self.frame_times["clean"] = self.frame_times.get("clean", 0.0) + time.time() - started
        self.say(f"frame {i + 1}: Nikon ICE finished in {time.time() - started:.0f} s")
        return cleaned

    def second_pass(self, s, i, frame, raw_result, base, opts, as_scanned):
        """The old way to clean in archive mode: scan again with the driver's cleaning on,
        at the first pass's exact exposure and lens position. Slower; kept as a fallback."""
        self.say(f"frame {i + 1}: second pass with dust removal (same focus and exposure)")
        again = dict(opts)
        again["exposures"] = dict(raw_result.exposures)
        pos = raw_result.focus_position
        again["focus"] = int(pos) if pos is not None else "hold"
        r = self._driver_scan(s, frame, infrared=True, clean=True, **base, **again)
        if r.cleaned is not None:
            self.say(f"dust removal rebuilt {r.cleaned} pixels")
        return as_scanned(r)
