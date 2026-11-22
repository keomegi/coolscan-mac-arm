"""The app itself: the scanner session, thumbnail, preview, focus and exposure."""

import bisect
import math
import os
import subprocess
import sys
import threading
import time
import traceback

import numpy as np

from .colour import display_image
from .demo import _Demo
from .driver import Cancelled, DriverLog, _lost_scanner, _stopped, explain
from .imaging import encode_png
from .notify import send_banner, send_ntfy, send_shortcut
from .rolls import RollsMixin
from .scanning import ICE_HELPER, ScanMixin
from .settings import SettingsMixin, default_settings

PREVIEW_MAX_SIDE = 1400  # pixels; previews are shrunk to this for the browser
WATCH_SECONDS = 2  # how often the idle scanner is asked whether film is in


class App(SettingsMixin, RollsMixin, ScanMixin):
    def __init__(self, demo=False):
        self.demo = demo
        self.lock = threading.Lock()
        # One thread at a time talks to the scanner. A job holds this for its whole
        # length; the film watcher only looks when nobody holds it.
        self.session_lock = threading.Lock()
        self.film_in = None         # what the scanner last said: film in (True), out (False), not asked (None)
        self.film_armed = True      # the next time film is seen in, show its thumbnail
        self.ice_ready = False      # the Nikon ICE helper is there and says it can run
        self.ice_checked = 0.0
        self.session = None
        self.caps = None            # a copy of the capabilities, refreshed on the worker thread
        self.discovery = None
        self.thumb_png = None
        self.previews = {}          # frame index -> png bytes
        self.frame_rot = {}         # frame index -> extra quarter turns, counterclockwise
        self.preview_raw = {}       # frame index -> planes, to redraw after a setting changes
        self.preview_info = {}      # frame index -> text
        self.focus_lock = None      # dict(frame, position, result) from Autofocus
        self.frame_focus = {}       # frame index -> lens position, focused once per frame
        self.exposure_lock = None   # dict(frame, exposures, infrared, film) from Auto exposure
        self.medium_seen = None     # the driver's film-change count when the frames were found
        self.log = []
        self.job = None
        self.progress = None
        self.cancel = False
        self.driver_log = DriverLog()
        self.frame_times = {}  # seconds spent on the frame being scanned: scan, clean, waited (see _driver_scan)
        self.timing = {}       # for the screen: when the strip and the frame started, how long the last ones took
        self.strip_id = 0      # goes up whenever the frames on screen are from another strip
        self.batch_day = None  # the date in the file names of the batch being scanned
        self.last_taken = None  # the "date taken" given to the last frame
        self.version = 0
        self.settings = default_settings()

    # -- bookkeeping
    def say(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True)
        with self.lock:
            self.log.append(line)
            self.log = self.log[-300:]
            self.version += 1

    def _progress(self, phase, pass_number, done, total):
        # Called by the driver between the pieces of a pass, so it has to be quick:
        # anything slow here keeps the scanner waiting. The page reads this on its own.
        self.progress = {"phase": phase, "done": done, "total": total}
        return not self.cancel

    def state(self):
        # Never touch the session here. This runs on a web thread, and the nkscan
        # session is locked while a pass runs: reading it would wait for the pass
        # while holding Python's lock, and the pass needs that lock for its
        # progress callback, so both would wait forever.
        s = self.session
        caps = None
        c = self.caps
        if c is not None:
            caps = {k: getattr(c, k, None) for k in (
                "product", "model", "optical_dpi", "x_dpi_range", "max_samples", "framing",
                "thumbnail", "film_format", "film_formats", "autofocus", "eject", "max_frames",
                "focus_range", "revision")}
            caps["holder"] = self._holder_name()
        d = self.discovery
        frames = []
        boxes = []
        try:
            number = self.next_number()
        except Exception:  # noqa: BLE001 - the page must still load if the folder cannot be read
            number = 1
        if d is not None:
            frames = list(range(len(d.frames)))
            boxes = self._thumb_boxes()
        return {
            "demo": self.demo, "connected": s is not None, "caps": caps, "job": self.job,
            "progress": self.progress, "log": self.log[-60:], "settings": self.settings,
            "frames": frames, "thumb": self.thumb_png is not None, "boxes": boxes,
            "previews": sorted(self.previews),
            "turns": {i: self._turns(i) for i in frames}, "preview_info": self.preview_info,
            "focus_lock": self.focus_lock,
            "exposure_lock": self.exposure_lock, "version": self.version,
            "roll_path": str(self._base() / self.settings["roll"]) if self.settings["roll"] else None,
            "film_text": self._film_text(), "roll_number": self._roll_number(self.settings["roll"] or ""),
            "sample_name": self._stem(None, number) + ".tif", "next_number": number,
            "film_in": self.film_in if self.settings["auto_thumbnail"] else None,
            "ice_helper": self.ice_ready, "strip": self.strip_id,
            "rolls": self.rolls(), "timing": dict(self.timing, now=time.time()),
        }

    def run_job(self, name, fn, *args):
        with self.lock:
            if self.job:
                return False
            self.job, self.cancel, self.progress = name, False, None
            self.version += 1

        def work():
            try:
                with self.session_lock:
                    fn(*args)
            except Exception as e:  # noqa: BLE001 - show every failure in the log
                if _stopped(e):
                    self.say(f"{name}: stopped")
                    return
                self.say(f"{name} failed. {explain(e)}")
                traceback.print_exc()
                if _lost_scanner(e):
                    self._drop_session()
            finally:
                with self.lock:
                    self.job, self.progress = None, None
                    self.version += 1
        threading.Thread(target=work, daemon=True).start()
        return True

    def _drop_session(self):
        """The scanner went away (switched off, unplugged, reset): the next button press connects again."""
        self.session, self.caps = None, None
        self.discovery, self.thumb_png, self.film_in = None, None, None
        self.strip_id += 1
        self.say("the next button you press (or Connect) connects to the scanner again")

    def _ice_cmd(self):
        # a copied file can lose its "may be run" mark; a Python helper still runs through Python
        return [str(ICE_HELPER)] if os.access(ICE_HELPER, os.X_OK) else [sys.executable, str(ICE_HELPER)]

    def check_ice(self):
        """Ask the Nikon ICE helper whether it can run (it needs Wine and Nikon's own library)."""
        ready = False
        if ICE_HELPER.exists():
            try:
                ready = subprocess.run(self._ice_cmd() + ["--status"], capture_output=True, timeout=30).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ready = False
        self.ice_checked = time.time()
        if ready != self.ice_ready:
            self.ice_ready = ready
            with self.lock:
                self.version += 1
        return ready

    def notify(self, text, plain=None, wait=False):
        """Tell the person, in every way they switched on. `plain` (no film notes) is what leaves the Mac for ntfy."""
        st = self.settings
        ways = []
        if st["notify_mac"]:
            ways.append(("banner on this Mac", lambda: send_banner("Coolscan", text)))
        shortcut = st["notify_shortcut"].strip()
        if shortcut:
            ways.append((f'shortcut "{shortcut}"', lambda: send_shortcut(shortcut, "Coolscan: " + text)))
        if st["notify_ntfy"].strip():
            ways.append(("ntfy", lambda: send_ntfy(st["notify_ntfy"], "Coolscan", plain or text)))

        def send():
            for name, way in ways:
                try:
                    way()
                    self.say(f"sent: {name}")
                except FileNotFoundError:
                    self.say(f"could not send ({name}): this needs a Mac")
                except Exception as e:  # noqa: BLE001 - a message that fails must never stop scanning
                    self.say(f"could not send ({name}): {e}")

        if not ways:
            return False
        if wait:
            send()
        else:
            threading.Thread(target=send, daemon=True).start()
        return True

    # -- watching for film
    def watch(self):
        """Its own thread: while the scanner is idle, notice film going in and show its thumbnail."""
        while True:
            time.sleep(WATCH_SECONDS)
            try:
                if time.time() - self.ice_checked > 60:
                    self.ice_checked = time.time()
                    threading.Thread(target=self.check_ice, daemon=True).start()
                self._watch_once()
            except Exception:  # noqa: BLE001 - the watcher must never die
                traceback.print_exc()

    def _watch_once(self):
        if self.job or self.session is None or not self.settings["auto_thumbnail"]:
            return
        # Never wait for the scanner here: a pass in another thread holds it, and
        # asking during a pass is what froze the app once. If it is taken, skip.
        if not self.session_lock.acquire(blocking=False):
            return
        try:
            s = self.session
            if s is None or self.job:
                return
            try:
                loaded = bool(s.media_loaded())
            except Exception as e:  # noqa: BLE001
                if _lost_scanner(e):
                    self.say(f"{explain(e)}")
                    self._drop_session()
                    return
                if type(e).__name__ != "MediaError":
                    return  # busy, or something passing: ask again next time
                loaded = False  # adapter out or not ready for film: nothing is loaded
            if loaded != self.film_in:
                try:
                    self.caps = s.capabilities  # the holder may have changed with the film
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self.session_lock.release()
        if loaded != self.film_in:
            if self.film_in is not None:
                self.say("film is in the scanner" if loaded else "no film in the scanner now")
            self.film_in = loaded
            with self.lock:
                self.version += 1
        if not loaded:
            self.film_armed = True
        elif self.film_armed and self.discovery is None:
            if self.run_job("thumbnail", self.auto_show):
                self.film_armed = False  # once per film: a failure is not tried again by itself

    def auto_show(self):
        """What happens by itself when film goes in, like Nikon Scan: find the frames and show them."""
        self.say("film is in: showing its thumbnail")
        self.thumbnail()
        d = self.discovery
        if d is None or not d.frames:
            return
        if d.thumbnail is None:
            if len(d.frames) == 1:
                self.preview([0])  # a slide has no thumbnail pass, so it gets a preview scan instead
        elif self.settings["preview_mode"] == "thumbnail":
            self.preview([])  # "Thumbnail (fast)": the frames are cut from the pass just made

    # -- scanner steps
    def connect(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:  # noqa: BLE001
                pass
            self.session = None
        if self.demo:
            self.session = _Demo()
        else:
            import nkscan
            devices = nkscan.list_devices()
            if not devices:
                raise RuntimeError("no Nikon scanner found. Is it on and plugged in?")
            self.say(f"found {devices[0]!r}")
            self.session = nkscan.Session.open(devices[0])
        c = self.refresh_caps()
        firmware = str(getattr(c, "revision", "") or "").strip()
        self.say(f"connected to {c.product}{', firmware ' + firmware if firmware else ''} "
                 f"({c.optical_dpi} dpi, frames found by {c.framing}, "
                 f"thumbnail pass: {'yes' if c.thumbnail else 'no'})")
        many = int(getattr(c, "max_samples", 1) or 1)
        self.say(f"multi-sample: this scanner reads each line up to {many} times in one pass" if many > 1 else
                 "multi-sample: this scanner reads each line once, so 2x and 4x average that many passes")
        self.discovery, self.thumb_png, self.medium_seen = None, None, None
        self.previews, self.preview_info = {}, {}
        self.film_in, self.film_armed = None, True
        self.strip_id += 1

    def refresh_caps(self):
        """Copy the capabilities. Only call this on the worker thread."""
        self.caps = self.session.capabilities
        with self.lock:
            self.version += 1
        return self.caps

    def _holder_name(self):
        c = self.caps
        if c is None:
            return None
        if c.framing == "perforation":
            return f"film strip (up to {c.max_frames or 6} frames)" if c.max_frames != 1 else "film strip"
        if c.max_frames == 1:
            return "slide"
        if c.max_frames:
            return f"holder with {c.max_frames} frames"
        return {"published": "slide or holder", "address": "slide or holder",
                "thumbnail": "film strip"}.get(c.framing, c.framing)

    def need_session(self):
        if self.session is None:
            self.connect()
        return self.session

    def load(self):
        s = self.need_session()
        self.say("loaded" if s.load() else "nothing waiting to load")
        self.refresh_caps()
        self.discovery, self.thumb_png, self.previews, self.preview_info = None, None, {}, {}
        self.film_armed = True
        self.strip_id += 1

    def eject(self):
        s = self.need_session()
        s.eject()
        self.say("ejected")
        self.refresh_caps()
        self.discovery, self.thumb_png, self.previews, self.preview_info = None, None, {}, {}
        self.preview_raw, self.frame_rot, self.frame_focus = {}, {}, {}
        self.focus_lock = self.exposure_lock = None
        self.strip_id += 1
        # do not take the same strip straight back in: wait until the scanner says it is out
        self.film_armed = False

    def _film(self):
        return self.settings["film"]

    def _turns(self, i=None):
        """Quarter turns counterclockwise for frame i (None: the thumbnail)."""
        k = 2 if self.settings["rotate180"] else 0
        return (k + (self.frame_rot.get(i, 0) if i is not None else 0)) % 4

    def _rot(self, plane, i=None):
        k = self._turns(i)
        return np.ascontiguousarray(np.rot90(plane, k)) if k else plane

    def _png(self, planes, i=None):
        img = display_image({k: self._rot(v, i) for k, v in planes.items()}, self._film(),
                            invert=self.settings["invert_preview"], tone=self.settings["tone"],
                            restore=self.settings["restore"])
        if i is not None and max(img.shape[:2]) < 600:
            # small thumbnail tiles are enlarged so they fill the preview box
            f = math.ceil(600 / max(img.shape[:2]))
            img = np.repeat(np.repeat(img, f, axis=0), f, axis=1)
        return encode_png(img)

    def rotate(self, indices, quarter):
        for i in indices:
            self.frame_rot[i] = (self.frame_rot.get(i, 0) + quarter) % 4
        self.redraw()

    def redraw(self):
        if self.discovery is not None and self.discovery.thumbnail is not None:
            self.thumb_png = self._png(self.discovery.thumbnail)
        for i, planes in list(self.preview_raw.items()):
            self.previews[i] = self._png(planes, i)

    def _lock_wb(self):
        wb = self.settings["white_balance"]
        return None if wb == "film" else (wb == "lock")

    def _format(self):
        return self.settings["format"] or None

    def _medium(self, s):
        """The driver's count of a strip or holder going in or out. Worker thread only."""
        try:
            return int(s.medium_changes)
        except (AttributeError, TypeError, ValueError):
            return None  # an older nkscan without the counter

    def thumbnail(self):
        s = self.need_session()
        if not s.media_loaded():
            # the scanner may be holding a strip it let go of (it rewinds after sitting idle)
            try:
                taken = s.load()
            except Exception as e:  # noqa: BLE001
                if _lost_scanner(e):
                    raise
                taken = False
            if not taken or not s.media_loaded():
                raise RuntimeError("no film loaded")
            self.say("the scanner took the film in again")
        self.refresh_caps()
        self.say(f"thumbnail pass: finding the frames ({self._holder_name()})")
        d = s.discover_frames(format=self._format(), film=self._film(), progress=self._progress)
        self.discovery = d
        self.strip_id += 1
        self.medium_seen = self._medium(s)
        self.previews, self.preview_info, self.preview_raw, self.frame_rot = {}, {}, {}, {}
        self.frame_focus = {}  # new film (or a new look at it): focus each frame again
        if d.thumbnail is not None:
            self.thumb_png = self._png(d.thumbnail)
            if d.thumbnail_complete is False:
                self.say("warning: the thumbnail pass came back short")
        else:
            self.thumb_png = None
            self.say("this holder publishes its frames, so the scanner took no thumbnail")
        self.say(f"found {len(d.frames)} frame(s)")

    def _ensure_discovery(self):
        s = self.session
        if self.discovery is not None and self.medium_seen is not None and s is not None:
            # Nikon Scan asks to reload when "the film has been rewound due to
            # inactivity". The scanner reports a film change to the next command,
            # so ask it something, then see whether the driver counted one.
            s.media_loaded()
            now = self._medium(s)
            if now is not None and now != self.medium_seen:
                self.say("the film was taken out or moved since the frames were found (the scanner "
                         "rewinds a strip by itself after sitting idle): finding the frames again")
                self.discovery, self.thumb_png = None, None
                self.previews, self.preview_info, self.preview_raw = {}, {}, {}
        if self.discovery is None:
            self.thumbnail()
        return self.discovery

    def _thumb_tile_bounds(self, i):
        """Columns and rows of frame i on the thumbnail, or None when they can't be placed."""
        d = self.discovery
        if d is None or d.thumbnail is None or not d.column_addresses:
            return None
        cols = d.column_addresses
        top, left, bottom, right = d.frames[i]
        first, last = bisect.bisect_left(cols, top), bisect.bisect_left(cols, bottom)
        nrows = next(iter(d.thumbnail.values())).shape[0]
        if d.row_addresses:
            down = [-x for x in d.row_addresses]
            upper, lower = bisect.bisect_right(down, -right), bisect.bisect_right(down, -left)
        else:
            upper, lower = 0, nrows
        if last <= first or lower <= upper:
            return None
        return first, last, upper, lower

    def _thumb_boxes(self):
        d = self.discovery
        if d is None or d.thumbnail is None:
            return []
        nrows, ncols = next(iter(d.thumbnail.values())).shape
        boxes = []
        for i in range(len(d.frames)):
            b = self._thumb_tile_bounds(i)
            if b:
                first, last, upper, lower = b
                x, w = first / ncols, (last - first) / ncols
                y, h = upper / nrows, (lower - upper) / nrows
                if self.settings["rotate180"]:
                    x, y = 1 - x - w, 1 - y - h
                boxes.append({"i": i, "x": x, "w": w, "y": y, "h": h})
        return boxes

    def _point(self):
        p = self.settings["focus_point"]
        return (float(p[0]), float(p[1])) if p else None  # kept in the scanner's orientation

    def _focus_for(self, s, i, frame):
        """What `focus` to give a pass over frame i, following the focus setting.

        "frame" (the default): focus once on each frame, the first time it is
        previewed or scanned, and reuse that for its later passes. Curl differs
        between frames, so each frame gets its own focus; a new thumbnail pass,
        Load, or a new focus point focuses again.
        """
        st = self.settings
        mode = st["focus_mode"]
        if mode == "manual":
            return int(st["focus_position"])
        if not self.caps.autofocus:
            return "hold"
        if mode == "same":
            if self.focus_lock and self.focus_lock["position"] is not None:
                return int(self.focus_lock["position"])
            self.say("no focus set yet (press Autofocus or Calibrate), so focusing this frame")
            mode = "frame"
        if mode == "frame":
            if self.frame_focus.get(i) is None:
                self.say(f"frame {i + 1}: autofocus")
                result, position = s.focus_frame(frame, focus=self._point())
                self.say(f"focus {result}, lens position {position}")
                if position is None:
                    return "hold"
                self.frame_focus[i] = int(position)
            return self.frame_focus[i]
        return self._point() or "auto"

    def _driver_scan(self, s, frame, **kw):
        """One scan_frame call. Leaves the lens alone when it is already where the pass wants it
        (nkscan: "to scan at this focus, give the scan hold"), and keeps the time it took and
        what the driver noted about the scanner waiting for the computer."""
        focus = kw.get("focus")
        if isinstance(focus, int) and not isinstance(focus, bool):
            try:
                at = s.focus_position
                if int(at() if callable(at) else at) == focus:
                    kw["focus"] = "hold"
            except Exception:  # noqa: BLE001 - an older nkscan, or the scanner cannot say: move as before
                pass
        self.driver_log.mark()
        started = time.time()
        r = s.scan_frame(frame, **kw)
        t = self.frame_times
        t["scan"] = t.get("scan", 0.0) + time.time() - started
        for p in self.driver_log.passes():
            t["waited"] = t.get("waited", 0.0) + p.get("starved_ms", 0) / 1000
            t["passes"] = t.get("passes", 0) + 1
        return r

    def _scan_options(self, s, i, frame, infrared):
        """film, focus, white balance and exposures for a pass."""
        opts = {"film": self._film(), "format": self._format(),
                "base_density": getattr(self.discovery, "base_density", None),
                "lock_white_balance": self._lock_wb(), "focus": self._focus_for(s, i, frame)}
        if self.settings["exposure_mode"] == "same":
            lock = self.exposure_lock
            if not lock or lock["film"] != self._film() or (infrared and not lock["infrared"]):
                self.auto_expose_frame(s, frame, infrared)
                lock = self.exposure_lock
            exp = lock["exposures"]
            if lock["infrared"] and not infrared:
                # a pass without infrared reuses the colour exposures only
                exp = {k: v for k, v in exp.items() if k in ("red", "green", "blue", "gray", "grey")}
            opts["exposures"] = exp
        return opts

    def preview(self, indices):
        s = self.need_session()
        d = self._ensure_discovery()
        indices = indices or list(range(len(d.frames)))
        mode = self.settings["preview_mode"]
        if mode == "thumbnail" and d.thumbnail is None:
            self.say("no thumbnail on this holder, so previews are scanned instead")
            mode = "scan"
        for i in indices:
            if self.cancel:
                raise Cancelled
            if mode == "thumbnail":
                b = self._thumb_tile_bounds(i)
                if b is None:
                    self.say(f"frame {i + 1}: can't place it on the thumbnail, scanning instead")
                else:
                    first, last, upper, lower = b
                    tile = {k: p[upper:lower, first:last] for k, p in d.thumbnail.items()}
                    self.preview_raw[i] = tile
                    self.previews[i] = self._png(tile, i)
                    self.preview_info[i] = f"from thumbnail, {last - first}x{lower - upper} px"
                    self.say(f"frame {i + 1}: preview cut from the thumbnail")
                    with self.lock:
                        self.version += 1
                    continue
            dpi = int(self.settings["preview_dpi"])
            self.say(f"frame {i + 1}: preview scan at {dpi} dpi")
            r = self._driver_scan(s, d.frames[i], dpi=dpi, progress=self._progress,
                                  **self._scan_options(s, i, d.frames[i], False))
            self.preview_raw[i] = {k: v[::4, ::4] if max(v.shape) > 2 * PREVIEW_MAX_SIDE else v
                                   for k, v in r.colors.items()}
            self.previews[i] = self._png(self.preview_raw[i], i)
            self.preview_info[i] = (f"scanned, {r.cols}x{r.rows} px at {r.dpi} dpi, "
                                    f"focus {r.focused} ({r.focus_position})")
            with self.lock:
                self.version += 1

    def _pick(self, index):
        d = self._ensure_discovery()
        if not d.frames:
            raise RuntimeError("No frames were found on the film. Press Thumbnail only to look again.")
        index = index if index is not None and 0 <= index < len(d.frames) else 0
        return index, d.frames[index]

    def autofocus(self, index):
        """Nikon Scan's Autofocus button: focus on the frame (or the chosen point) and keep it."""
        s = self.need_session()
        index, frame = self._pick(index)
        if not self.caps.autofocus:
            raise RuntimeError("this unit does not autofocus")
        point = self.settings["focus_point"]
        focus = None
        if point:
            x, y = point
            focus = (float(x), float(y))
        self.say(f"frame {index + 1}: autofocus " + (f"at {point[0]:.2f}, {point[1]:.2f}" if point else "at the centre"))
        result, position = s.focus_frame(frame, focus=focus)
        self.focus_lock = {"frame": index, "position": position, "result": result}
        if position is not None:
            self.frame_focus[index] = int(position)
        self.say(f"focus {result}, lens position {position}")
        if position is not None:
            self.settings["focus_position"] = int(position)

    def auto_expose_frame(self, s, frame, infrared, index=None):
        self.say("auto exposure: metering " + (f"frame {index + 1}" if index is not None else "this frame")
                 + (" with infrared" if infrared else ""))
        exposures = s.meter_frame(frame, infrared=infrared, film=self._film(), format=self._format(),
                                  base_density=getattr(self.discovery, "base_density", None),
                                  lock_white_balance=self._lock_wb(), progress=self._progress)
        self.exposure_lock = {"frame": index, "exposures": dict(exposures),
                              "infrared": infrared, "film": self._film()}
        self.say(f"exposure {dict(exposures)}")

    def auto_expose(self, index):
        """Nikon Scan's AE: meter the frame and keep that exposure."""
        s = self.need_session()
        index, frame = self._pick(index)
        infrared = bool(self.settings["ir"] or self.settings["clean"])
        self.auto_expose_frame(s, frame, infrared, index)

    def calibrate(self, index):
        """Autofocus and auto exposure on one frame, then use both for every frame."""
        s = self.need_session()
        if self.caps.autofocus:
            self.autofocus(index)
        self.auto_expose(index)
        if self.caps.autofocus:
            self.settings["focus_mode"] = "same"
        self.settings["exposure_mode"] = "same"
        self.say("calibrated: every preview and scan now uses this focus and exposure")

    def quick_job(self, name, fn):
        """Run something short right now, but never while a scan or another job is running."""
        with self.lock:
            if self.job:
                return {"ok": False, "busy": True}
            self.job, self.progress = name, None
            self.version += 1
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return {"ok": False, "error": f"That did not work ({e}). Check the folder before trying again."}
        finally:
            with self.lock:
                self.job = None
                self.version += 1
