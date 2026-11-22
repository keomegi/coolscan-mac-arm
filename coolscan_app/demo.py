"""A pretend scanner, so the screens can be tried without hardware."""

import threading
import time

import numpy as np


class _Demo:
    """A pretend scanner so the screens can be tried without hardware."""

    class Caps:
        vendor, product, revision, model = "Nikon", "LS-40 ED (demo)", "1.00", "LS-40"
        optical_dpi, x_dpi_range, y_dpi_range = 2900, (73, 2900), (73, 2900)
        max_frames, thumbnail_dpi, focus_range, max_samples = 6, (73, 290), (0, 500), 16
        framing, thumbnail, film_format, film_formats = "perforation", True, "135", []
        multi_line, eject, autofocus, hardware_metering = False, True, True, False

    class Disc:
        pass

    class Result:
        pass

    def __init__(self):
        self._caps = self.Caps()
        self._busy = threading.Lock()  # like nkscan, the session is locked during a pass
        self.loaded = True
        self.focus = 250
        self.medium_changes = 0  # like nkscan: counts a strip or holder going in or out
        self.strip_frames = 6    # how many frames the strip in the scanner has
        self.focus_moves = 0     # how often a pass asked for the lens to be moved

    @property
    def capabilities(self):
        with self._busy:
            return self._caps

    @staticmethod
    def _film(rows, cols, seed):
        y, x = np.mgrid[0:rows, 0:cols].astype(np.float32)
        g = (np.sin(x / (cols / 7 + seed)) + np.cos(y / (rows / 5))) * 0.25 + 0.5
        base = {"red": 0.85, "green": 0.55, "blue": 0.35}  # orange-ish negative
        return {k: (np.clip(g * b + 0.05, 0, 1) * 60000).astype(np.uint16) for k, b in base.items()}

    def _wait(self, progress, phase, steps=10, dt=0.12):
        for i in range(steps):
            time.sleep(dt)
            if progress and progress(phase, 0, i + 1, steps) is False:
                raise RuntimeError("cancelled")

    def media_loaded(self):
        return self.loaded

    def load(self):
        if not self.loaded:
            self.medium_changes += 1
            self.strip_frames = 4 if self.strip_frames == 6 else 6  # the next strip is another length
        self.loaded = True
        return True

    def eject(self):
        if self.loaded:
            self.medium_changes += 1
        self.loaded = False
        return True

    def discover_frames(self, format=None, film=None, progress=None):
        with self._busy:
            self._wait(progress, "thumbnail", 25)
        d = self.Disc()
        cols, rows = 6 * 140, 96
        d.thumbnail = self._film(rows, cols, 1)
        d.column_addresses = [i * 100 for i in range(cols)]
        d.row_addresses = [rows * 10 - i * 10 for i in range(rows)]
        d.frames = [(i * 14000 + 600, 50, i * 14000 + 13400, 900) for i in range(self.strip_frames)]
        d.base_density = (0.2, 0.5, 0.8)
        d.thumbnail_complete = True
        return d

    def meter_frame(self, frame, infrared=False, progress=None, **kw):
        self._wait(progress, "meter", 5)
        e = {"red": 1200, "green": 900, "blue": 700}
        if infrared:
            e["infrared"] = 800
        return e

    def focus_frame(self, frame, focus=None):
        time.sleep(0.5)
        self.focus = 240 + (frame[0] // 14000) * 3  # each frame focuses a little differently
        return "focused", self.focus

    def focus_position(self):
        return self.focus

    def scan_frame(self, frame, dpi=None, infrared=False, clean=False, exposures=None,
                   focus=None, progress=None, **kw):
        dpi = dpi or 2900
        if isinstance(focus, int) and not isinstance(focus, bool):
            self.focus_moves += 1
            self.focus = focus
        self._wait(progress, "scan", 10 if dpi < 1000 else 30)
        r = self.Result()
        rows, cols = int(0.94 * dpi), int(1.42 * dpi)
        r.colors = self._film(rows, cols, frame[0] % 7)
        rng = np.random.default_rng()
        for name, plane in r.colors.items():  # noise that differs on every pass, like the real thing
            noise = rng.integers(-400, 401, plane.shape, dtype=np.int32)
            r.colors[name] = np.clip(plane.astype(np.int32) + noise, 0, 65535).astype(np.uint16)
        r.ir = np.full((rows, cols), 50000, np.uint16) if (infrared or clean) else None
        # specks and a scratch that block the light, in the picture and in the infrared
        size = max(1, dpi // 500)
        spots = [((53 * k + 17) % (rows - 8 * size), (211 * k + 40) % (cols - 8 * size), size * (1 + k % 3))
                 for k in range(40)]
        marks = [(slice(y, y + n), slice(x, x + n)) for y, x, n in spots]
        marks.append((slice(rows // 3, rows // 3 + size), slice(cols // 4, cols // 2)))
        dusty = 0
        for mark in marks:
            if r.ir is not None:
                r.ir[mark] = 7000
            if not clean:  # with clean=True the real driver hands back the picture already cleaned
                for plane in r.colors.values():
                    plane[mark] //= 5
            dusty += r.ir[mark].size if r.ir is not None else 0
        r.dpi, r.rows, r.cols = dpi, rows, cols
        r.exposures = exposures or {"red": 1200, "green": 900, "blue": 700}
        r.complete, r.cleaned = True, (dusty if clean else None)
        r.focused = "skipped" if focus == "hold" else "focused"
        r.focus_position = self.focus
        return r

    def nikon_profile(self, film):
        return None

    def close(self):
        pass
