"""The app itself: the scanner session, thumbnail, preview, focus and exposure."""

import bisect
import math
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from .colour import FILM_PROFILE_LEAST, display_image, shrink
from .demo import _Demo
from .develop import DevelopMixin
from .driver import Cancelled, DriverLog, _lost_scanner, _stopped, clock, explain
from .imaging import encode_png
from .keepawake import KeepAwake
try:  # notify.py is optional: it is kept out of git, and the app runs without it
    from . import notify as notifier
except ImportError:
    notifier = None
from .rolls import RollsMixin
from .scanning import ICE_HELPER, ScanMixin
from .settings import SettingsMixin, default_settings
try:  # test mode is optional too: testmode.py and web/test.html, test.js are kept out of git
    from .testmode import TestMixin
    HAVE_TESTMODE = True
except ImportError:
    HAVE_TESTMODE = False

    class TestMixin:  # without it the app runs as usual; --test says test mode is not there
        pass

WATCH_SECONDS = 2  # how often the idle scanner is asked whether film is in
# The frames and thumbnail of the strip in the scanner, kept so that starting the app
# again (or reconnecting) with the same film still in needs no new thumbnail pass.
# nkscan checks it is the same scanner, holder, film type and strip before using it.
STRIP_CACHE = Path.home() / ".coolscan-strip.cache"


class App(SettingsMixin, RollsMixin, ScanMixin, TestMixin, DevelopMixin):
    def __init__(self, demo=False):
        self.demo = demo
        self.test_mode = False       # started with --test: the test page is served as well
        self.testing = False         # a test queue is running (its settings are swapped in for the time being)
        self.test_pass_cache = None  # while testing: passes already taken, so runs that need the same one share it
        self.test_rescan = False     # the run being tested wants its own pass whatever is there
        self.test_reused = False     # the run being tested used an earlier run's pass
        self.test_kept = None        # while testing: the settings as they were, put back when the queue is done
        self.test_hold = False       # the run being tested has "film profile from this strip": hold its frame back
        self.test_held = []          # while testing: frames held back until the strip's profile is made
        self.test_measured = {}      # while testing: frame -> what its film measured, for the strip's profile
        self.test_frame = None       # while testing: the frame being tested
        self.test = {"results": [], "folder": None, "frame": None, "frames": [], "crop": (0.5, 0.5), "running": None,
                     "totals": [], "strip_profile": None}
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
        self.frame_source = {}      # frame index -> where its picture comes from: "thumbnail", "preview" or "scan"
        self.frame_saved = {}       # frame index -> what was saved from it on this strip: [{"files": [...], "took": s}]
        self.picture_v = {}         # frame index -> a number that changes whenever its picture does
        self.picture_serial = 0
        self.thumb_v = 0
        self.crops = {}             # frame index -> (x0, y0, x1, y1), fractions of the frame as the scanner sees it
        self.frame_exposures = {}   # frame index -> the exposures its picture on screen was scanned with
        self.focus_lock = None      # dict(frame, position, result) from Autofocus
        self.frame_focus = {}       # frame index -> lens position, focused once per frame
        self.exposure_lock = None   # dict(frame, exposures, infrared, film) from Auto exposure
        self.medium_seen = None     # the driver's film-change count when the frames were found
        self.log = []
        self.keep_awake = KeepAwake(self.say)  # macOS caffeinate while a job runs (keepawake.py)
        self.reconnect_tries = 0               # after the Mac slept and the scanner dropped: tries left
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
        self._dev_reset()           # re-develop: RAW files made into finished pictures again (develop.py)

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
        tiles = {i: {"src": self.frame_source.get(i), "v": self.picture_v.get(i, 0),
                     "info": self.preview_info.get(i, ""), "saved": self.frame_saved.get(i, []),
                     "turns": self._turns(i), "crop": self.crops.get(i)} for i in frames}
        return {
            "demo": self.demo, "connected": s is not None, "caps": caps, "job": self.job,
            "awake": self.keep_awake.awake,
            "progress": self.progress, "log": self.log[-60:], "settings": self.settings,
            "frames": frames, "tiles": tiles, "thumb": self.thumb_png is not None, "thumb_v": self.thumb_v,
            "boxes": boxes, "presets": sorted(self.settings["presets"]), "preset": self.current_preset(),
            "film_profiles": self.film_profiles(), "roll_measured": len(self._roll_colour()),
            "profile_least": FILM_PROFILE_LEAST,
            "previews": sorted(self.previews),
            "turns": {i: self._turns(i) for i in frames}, "preview_info": self.preview_info,
            "focus_lock": self.focus_lock,
            "exposure_lock": self.exposure_lock, "version": self.version,
            "roll_path": self._short_path(self._base() / self.settings["roll"]) if self.settings["roll"] else None,
            "film_text": self._film_text(), "roll_number": self._roll_number(self.settings["roll"] or ""),
            "sample_name": self._stem(None, number) + ".tif", "next_number": number,
            "film_in": self.film_in if self.settings["auto_thumbnail"] else None,
            "ice_helper": self.ice_ready, "strip": self.strip_id, "notify": notifier is not None,
            "rolls": self.rolls(), "timing": dict(self.timing, now=time.time()),
            "test": self.test_state() if self.test_mode else None,
            "develop": self.develop_state(),
        }

    @staticmethod
    def _short_path(path):
        """A folder the way the Mac writes it for people: ~ for the home folder."""
        try:
            return "~/" + str(Path(path).relative_to(Path.home()))
        except ValueError:
            return str(path)

    def run_job(self, name, fn, *args):
        with self.lock:
            if self.job:
                return False
            self.job, self.cancel, self.progress = name, False, None
            self.version += 1
        self.keep_awake.hold()  # the Mac must not sleep in the middle of this
        started = time.time()

        def work():
            try:
                with self.session_lock:
                    fn(*args)
            except Exception as e:  # noqa: BLE001 - show every failure in the log
                if _stopped(e):
                    self.say(f"{name}: stopped")
                    return
                slept = self.keep_awake.slept_since(started)
                if _lost_scanner(e) and slept:
                    self.say(f"{name} failed: the Mac went to sleep while it ran and the scanner connection "
                             "dropped. This happens when the lid is closed, or the Mac is told to sleep. "
                             "The app connects again by itself; then do this step again.")
                else:
                    self.say(f"{name} failed. {explain(e)}" + (" (The Mac was asleep for a while during this.)"
                                                              if slept else ""))
                traceback.print_exc()
                if _lost_scanner(e):
                    self._drop_session()
                    if slept:
                        self.reconnect_tries = 3
            finally:
                with self.lock:
                    self.job, self.progress = None, None
                    self.version += 1
                self.keep_awake.release()
        threading.Thread(target=work, daemon=True).start()
        return True

    def _drop_session(self):
        """The scanner went away (switched off, unplugged, reset): the next button press connects again."""
        self.session, self.caps = None, None
        self.discovery, self.thumb_png, self.film_in = None, None, None
        self.strip_id += 1
        self._forget_pictures()
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
        if notifier is None:
            return False
        send_banner, send_shortcut, send_ntfy = notifier.send_banner, notifier.send_shortcut, notifier.send_ntfy
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
                self._after_sleep()
                self._watch_once()
            except Exception:  # noqa: BLE001 - the watcher must never die
                traceback.print_exc()

    def _after_sleep(self):
        """When the Mac wakes up, check the scanner is still there, and connect again if it dropped."""
        slept = self.keep_awake.check_sleep()
        if slept:
            self.say(f"the Mac was asleep for {clock(slept)}")
            if self.session is not None and not self.job and not self.demo:
                self.run_job("checking the scanner after sleep", self._check_after_sleep)
            return
        if self.reconnect_tries and self.session is None and not self.job:
            self.reconnect_tries -= 1
            self.say("connecting to the scanner again after the Mac slept")
            if not self.run_job("connect", self.connect):
                self.reconnect_tries += 1
        elif self.session is not None:
            self.reconnect_tries = 0

    def _check_after_sleep(self):
        s = self.session
        if s is None:
            return
        try:
            s.media_loaded()
        except Exception as e:  # noqa: BLE001
            if not _lost_scanner(e):
                return  # it answered, just not with film: the connection is fine
            self.say("the scanner connection dropped while the Mac slept")
            self._drop_session()
            self.reconnect_tries = 3
            return
        self.say("the scanner is still connected")

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
        self.thumbnail(fresh=False)
        d = self.discovery
        if d is None or not d.frames:
            return
        if d.thumbnail is None:
            if len(d.frames) == 1:
                self.preview([0])  # a slide has no thumbnail pass, so it gets a preview scan instead

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
        self._forget_pictures()
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
        self.discovery, self.thumb_png = None, None
        self._forget_pictures()
        self.film_armed = True
        self.strip_id += 1

    def eject(self):
        s = self.need_session()
        s.eject()
        self._forget_strip_cache()
        self.say("ejected")
        self.refresh_caps()
        self.discovery, self.thumb_png = None, None
        self._forget_pictures()
        self.frame_rot, self.frame_focus = {}, {}
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
        # a film profile needs the exposures of the pass, which a preview scan and a scan have and
        # the thumbnail pass has not: thumbnails are always converted each frame on its own
        exposures = self.frame_exposures.get(i) if i is not None else None
        img = display_image({k: self._rot(v, i) for k, v in planes.items()}, self._film(),
                            invert=self.settings["invert_preview"], tone=self.settings["tone"],
                            restore=self.settings["restore"],
                            profile=self._film_profile() if exposures else None, exposures=exposures)
        if i is not None and max(img.shape[:2]) < 600:
            # small thumbnail tiles are enlarged so they fill the preview box
            f = math.ceil(600 / max(img.shape[:2]))
            img = np.repeat(np.repeat(img, f, axis=0), f, axis=1)
        return encode_png(img)

    def rotate(self, indices, quarter):
        for i in indices:
            self.frame_rot[i] = (self.frame_rot.get(i, 0) + quarter) % 4
        self.redraw(indices)

    def redraw(self, only=None):
        """Make the pictures on screen again after a setting that changes their look (all of them, or `only`)."""
        if only is None and self.discovery is not None and self.discovery.thumbnail is not None:
            self.thumb_png = self._png(self.discovery.thumbnail)
            self.thumb_v += 1
        for i, planes in list(self.preview_raw.items()):
            if only is None or i in only:
                self.previews[i] = self._png(planes, i)
                self._new_picture(i)
        with self.lock:
            self.version += 1

    # -- the picture of each frame on screen: from the thumbnail pass, a preview scan, or the finished scan
    def _forget_pictures(self):
        self.previews, self.preview_info, self.preview_raw = {}, {}, {}
        self.frame_source, self.frame_saved, self.crops = {}, {}, {}
        self.frame_exposures = {}

    def _new_picture(self, i):
        self.picture_serial += 1
        self.picture_v[i] = self.picture_serial

    def _show(self, i, planes, source, info, exposures=None):
        """Put a picture of frame i on the screen and say where it comes from."""
        self.preview_raw[i] = planes
        self.frame_exposures.pop(i, None)
        if exposures:
            self.frame_exposures[i] = dict(exposures)
        self.previews[i] = self._png(planes, i)
        self.frame_source[i], self.preview_info[i] = source, info
        self._new_picture(i)
        with self.lock:
            self.version += 1

    def _show_thumbnail_tiles(self):
        """Right after the thumbnail pass every frame gets its piece of it, so there is something to look at."""
        d = self.discovery
        for i in range(len(d.frames)):
            b = self._thumb_tile_bounds(i)
            if b is not None:
                first, last, upper, lower = b
                tile = {k: p[upper:lower, first:last] for k, p in d.thumbnail.items()}
                self._show(i, tile, "thumbnail", f"from the thumbnail pass, {last - first}x{lower - upper} px")

    def set_crop(self, i, rect):
        """Keep (or with None, drop) the part of frame i to save. Fractions of the frame, as the scanner sees it."""
        d = self.discovery
        if d is None or not 0 <= i < len(d.frames):
            return {"ok": False}
        self.crops.pop(i, None)
        if rect:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in rect)
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if x1 - x0 >= 0.05 and y1 - y0 >= 0.05:
                self.crops[i] = (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))
        with self.lock:
            self.version += 1
        return {"ok": True, "crop": self.crops.get(i)}

    def _crop_box(self, i, shape):
        """Frame i's crop as (row0, row1, col0, col1) on a plane of `shape` turned the way the person sees it."""
        rect = self.crops.get(i)
        if not rect:
            return None
        corners = [(rect[0], rect[1]), (rect[2], rect[3])]
        for _ in range(self._turns(i)):  # the same quarter turns the picture gets
            corners = [(y, 1 - x) for x, y in corners]
        (xa, ya), (xb, yb) = corners
        rows, cols = shape
        r0, r1 = sorted((int(round(ya * rows)), int(round(yb * rows))))
        c0, c1 = sorted((int(round(xa * cols)), int(round(xb * cols))))
        if r1 - r0 < 16 or c1 - c0 < 16:
            return None
        return r0, r1, c0, c1

    def _cropped(self, i, planes):
        """Planes that are already turned, cut to frame i's crop (or as they are when there is none)."""
        box = self._crop_box(i, planes[0].shape) if planes else None
        if box is None:
            return planes
        r0, r1, c0, c1 = box
        return [np.ascontiguousarray(p[r0:r1, c0:c1]) for p in planes]

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

    def thumbnail(self, fresh=True):
        """Find the frames. fresh=False first tries the strip kept from last time (no pass at all)."""
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
        d = None if fresh else self._resume_strip(s)
        if d is None:
            self.say(f"thumbnail pass: finding the frames ({self._holder_name()})")
            self.driver_log.mark()
            started = time.time()
            d = s.discover_frames(format=self._format(), film=self._film(), progress=self._progress)
            took = time.time() - started
            waited = sum(p.get("starved_ms", 0) for p in self.driver_log.passes()) / 1000
            self.say(f"thumbnail pass took {clock(took)}" +
                     (f", of which the scanner waited {waited:.1f} s for the computer" if waited >= 0.5 else ""))
            self._keep_strip(d)
        else:
            self.say("same strip as before: used its frames and thumbnail from last time, no thumbnail pass "
                     "(press Thumbnail only to take a new one)")
        self.discovery = d
        self.strip_id += 1
        self.medium_seen = self._medium(s)
        self._forget_pictures()
        self.frame_rot = {}
        self.frame_focus = {}  # new film (or a new look at it): focus each frame again
        if d.thumbnail is not None:
            self.thumb_png = self._png(d.thumbnail)
            self.thumb_v += 1
            self._show_thumbnail_tiles()
            if d.thumbnail_complete is False:
                self.say("warning: the thumbnail pass came back short")
        else:
            self.thumb_png = None
            self.say("this holder publishes its frames, so the scanner took no thumbnail")
        self.say(f"found {len(d.frames)} frame(s)")

    def _resume_strip(self, s):
        """The kept discovery of this strip, or None (nkscan says no if anything changed)."""
        if not hasattr(s, "resume_frames"):
            return None
        try:
            data = STRIP_CACHE.read_bytes()
        except OSError:
            return None
        try:
            return s.resume_frames(data, format=self._format(), film=self._film())
        except Exception as e:  # noqa: BLE001 - fall back to a thumbnail pass
            if _lost_scanner(e):
                raise
            return None

    def _keep_strip(self, d):
        data = getattr(d, "cache", None)
        try:
            if data:
                STRIP_CACHE.write_bytes(bytes(data))
            else:
                self._forget_strip_cache()
        except OSError:
            pass

    def _forget_strip_cache(self):
        try:
            STRIP_CACHE.unlink()
        except OSError:
            pass

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
                self._forget_pictures()
        if self.discovery is None:
            self.thumbnail(fresh=False)
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

    def _point(self, frame=None, rect=None):
        """The focus point as nkscan takes it, or None for the centre.

        The setting is kept as fractions of the picture the scanner sends (across, down), the way
        the person clicked it with the turns taken out. In that picture the columns run along the
        feed (Y) and the rows run along the sensor from its highest X address down (nkscan's
        PYTHON.md: "the column addresses never decrease, and the row addresses decrease"). nkscan
        takes (x, y) as fractions of the rectangle from its left (lowest X) and top (lowest Y)
        edge, so across becomes y and down becomes x counted from the other end.

        With `rect` (a part of `frame`, as for a crop scanned on its own) the point is given as a
        fraction of `rect` instead, kept inside it.
        """
        p = self.settings["focus_point"]
        if not p:
            return None
        across, down = float(p[0]), float(p[1])
        fx, fy = 1.0 - down, across
        if frame is None or rect is None or tuple(rect) == tuple(frame):
            return (fx, fy)
        top, left, bottom, right = (int(v) for v in frame)
        x, y = left + fx * (right - left), top + fy * (bottom - top)
        rt, rl, rb, rr = (int(v) for v in rect)
        inside = lambda v: min(1.0, max(0.0, v))
        return (inside((x - rl) / max(1, rr - rl)), inside((y - rt) / max(1, rb - rt)))

    def _scan_rect(self, i, frame, archive=False):
        """The rectangle to scan for frame i: its crop when there is one, so the scanner only reads
        that part (less film to step over and much less to send over USB), or the whole frame.

        Returns (rect, windowed). The crop is kept as fractions of the picture the scanner sends:
        across = along the feed from the frame's top address, down = along the sensor from its
        right (highest X) edge. Archive mode keeps scanning the whole frame, because its RAW file
        is meant to hold the whole frame; "crop_scan" off does the same (the crop is then cut out
        when the file is saved, as before).
        """
        crop = self.crops.get(i)
        if not crop or archive or not self.settings.get("crop_scan", True):
            return frame, False
        x0, y0, x1, y1 = crop
        top, left, bottom, right = (int(v) for v in frame)
        h, w = bottom - top, right - left
        rect = (top + int(round(x0 * h)), right - int(round(y1 * w)),
                top + int(round(x1 * h)), right - int(round(y0 * w)))
        if rect == (top, left, bottom, right) or rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
            return frame, False
        return rect, True

    def _focus_for(self, s, i, frame, rect=None):
        """What `focus` to give a pass over frame i, following the focus setting.

        "frame" (the default): focus once on each frame, the first time it is
        previewed or scanned, and reuse that for its later passes. Curl differs
        between frames, so each frame gets its own focus; a new thumbnail pass,
        Load, or a new focus point focuses again.
        """
        st = self.settings
        override = getattr(self, "test_focus", None)
        if override is not None:  # test mode: a run that says autofocus on or off decides it itself
            return override
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
                result, position = self._timed_focus(s, frame)
                self.say(f"focus {result}, lens position {position}")
                if position is None:
                    return "hold"
                self.frame_focus[i] = int(position)
            return self.frame_focus[i]
        return self._point(frame, rect) or "auto"  # on a crop scanned on its own, "auto" is the crop's centre

    def _timed_focus(self, s, frame):
        """Autofocus on the frame (or the focus point), with the time it took kept with the frame's times."""
        started = time.time()
        try:
            return s.focus_frame(frame, focus=self._point())
        finally:
            self.frame_times["focus"] = self.frame_times.get("focus", 0.0) + time.time() - started

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

    def _scan_options(self, s, i, frame, infrared, rect=None):
        """film, focus, white balance and exposures for a pass over `frame` (or over `rect`, a crop
        of it scanned on its own)."""
        opts = {"film": self._film(), "format": self._format(),
                "base_density": getattr(self.discovery, "base_density", None),
                "lock_white_balance": self._lock_wb(), "focus": self._focus_for(s, i, frame, rect)}
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
        elif rect is not None and tuple(rect) != tuple(frame):
            # a crop scanned on its own is exposed for the whole frame, as its preview was, and not
            # metered on the crop alone (which would brighten a crop of dark sky, and so on)
            self.say(f"frame {i + 1}: metering the whole frame, then scanning only the crop")
            opts["exposures"] = dict(s.meter_frame(frame, infrared=infrared, film=self._film(), format=self._format(),
                                                   base_density=getattr(self.discovery, "base_density", None),
                                                   lock_white_balance=self._lock_wb(), progress=self._progress))
        return opts

    def preview(self, indices):
        """A sharper look at frames than the thumbnail pass gives: a quick low-resolution scan of each."""
        s = self.need_session()
        d = self._ensure_discovery()
        indices = [i for i in (indices or range(len(d.frames))) if 0 <= i < len(d.frames)]
        for i in indices:
            if self.cancel:
                raise Cancelled
            dpi = int(self.settings["preview_dpi"])
            self.say(f"frame {i + 1}: preview scan at {dpi} dpi")
            r = self._driver_scan(s, d.frames[i], dpi=dpi, progress=self._progress,
                                  **self._scan_options(s, i, d.frames[i], False))
            self._show(i, {k: shrink(v) for k, v in r.colors.items()}, "preview",
                       f"{r.cols}x{r.rows} px at {r.dpi} dpi, focus {r.focused} ({r.focus_position})",
                       exposures=getattr(r, "exposures", None))

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
        focus = self._point()
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
