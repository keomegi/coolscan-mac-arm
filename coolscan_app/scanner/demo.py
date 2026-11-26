"""A pretend scanner, so the screens can be tried without hardware."""

import os
import threading
import time

import numpy as np


# Other Coolscan models and holders for the pretend scanner (--demo-model, or COOLSCAN_DEMO_MODEL).
# Without one it is the LS-40 with the SA-21 strip adapter, exactly as before. The numbers are what
# each model's pages are expected to say; none of these came from a real unit of that model.
DEMO_MODELS = {
    "LS-50": dict(product="LS-50 ED", model="LS-50", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                  max_frames=6, thumbnail_dpi=(100, 400), max_samples=16),
    "LS-50:SF-210": dict(product="LS-50 ED", model="LS-50", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                         max_frames=1, thumbnail_dpi=(0, 0), max_samples=16, framing="address", thumbnail=False,
                         film_format=None, frames=1),
    "LS-5000": dict(product="LS-5000 ED", model="LS-5000", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                    max_frames=6, thumbnail_dpi=(100, 400), max_samples=16, multi_line=True),
    "LS-5000:SA-30": dict(product="LS-5000 ED", model="LS-5000", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                          max_frames=0, thumbnail_dpi=(100, 400), max_samples=16, multi_line=True, frames=36),
    "LS-4000": dict(product="LS-4000 ED", model="LS-4000", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                    max_frames=0, thumbnail_dpi=(100, 400), max_samples=16, frames=40),
    "LS-40:MA-20": dict(max_frames=1, framing="published", thumbnail=False, frames=1),
    "LS-8000": dict(product="LS-8000 ED", model="LS-8000", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                    max_frames=0, thumbnail_dpi=(100, 400), max_samples=16, framing="thumbnail", film_format=None,
                    film_formats=["645", "66", "67", "68", "69"], frames=3, frame_in=(2.74, 2.2)),
    "LS-9000": dict(product="LS-9000 ED", model="LS-9000", optical_dpi=4000, x_dpi_range=(100, 4000), y_dpi_range=(100, 4000),
                    max_frames=0, thumbnail_dpi=(100, 400), max_samples=16, framing="thumbnail", film_format="135",
                    frames=12),
}
DEMO_MODEL_NAMES = sorted(DEMO_MODELS)
# the long side of a 120 frame in inches, by format, for a pass that names one (as in models.py)
_FORMAT_IN = {"645": 41.5 / 25.4, "66": 56 / 25.4, "67": 69.5 / 25.4, "68": 76 / 25.4, "69": 83 / 25.4}


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

    def __init__(self, model=None):
        self._caps = self.Caps()
        self.frame_in = (1.42, 0.94)  # a frame's long and short side in inches (35 mm)
        self.first_frames = 6
        if model:
            if model not in DEMO_MODELS:
                raise ValueError(f"no pretend {model}: pick one of {', '.join(DEMO_MODEL_NAMES)}")
            spec = dict(DEMO_MODELS[model])
            self.first_frames = spec.pop("frames", 6)
            self.frame_in = spec.pop("frame_in", self.frame_in)
            spec.setdefault("product", "LS-40 ED")
            spec["product"] += " (demo)"
            for k, v in spec.items():
                setattr(self._caps, k, v)
        self._busy = threading.Lock()  # like nkscan, the session is locked during a pass
        self.loaded = True
        self.focus = 250
        self.medium_changes = 0  # like nkscan: counts a strip or holder going in or out
        self.strip_frames = self.first_frames  # how many frames the strip in the scanner has
        self.focus_moves = 0     # how often a pass asked for the lens to be moved
        self.borders = bool(os.environ.get("COOLSCAN_DEMO_BORDERS"))  # True: the film's edges show as dark bands and the gaps as clear film (for Auto crop)

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

    @staticmethod
    def _edges(planes, gaps, share=0.04):
        """Dark bands where the film's edges are (top and bottom rows) and, with `gaps` (columns at
        the left and right), clear film where the frame ends: what Auto crop looks for."""
        for p in planes.values():
            rows = p.shape[0]
            band = max(1, int(rows * share))
            p[:band] = 400
            p[rows - band:] = 400
            if gaps:
                p[band:rows - band, :gaps[0]] = 65535
                p[band:rows - band, p.shape[1] - gaps[1]:] = 65535

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
            if self.first_frames > 1:  # the next strip is another length (a slide is a slide)
                self.strip_frames = self.first_frames - 2 if self.strip_frames == self.first_frames else self.first_frames
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
        if not self._caps.thumbnail:  # a slide: no thumbnail pass, the frame is the whole gate
            d.thumbnail, d.column_addresses, d.row_addresses = None, None, None
            d.frames = [(i * 14000 + 600, 50, i * 14000 + 13400, 900) for i in range(self.strip_frames)]
            d.base_density, d.thumbnail_complete = None, None
            return d
        cols, rows = max(6, self.strip_frames) * 140, 96
        d.thumbnail = self._film(rows, cols, 1)
        if self.borders:
            self._edges(d.thumbnail, None, 0.11)  # the thumbnail reaches past the frames: wider bands, so some shows in each frame
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
        dpi = dpi or self._caps.optical_dpi
        if isinstance(focus, int) and not isinstance(focus, bool):
            self.focus_moves += 1
            self.focus = focus
        r = self.Result()
        # like nkscan, the pass is the rectangle asked for: a crop of a frame comes back as just that
        # part. Columns run along the feed from the top address, rows along the sensor from the
        # highest X address down (nkscan's PYTHON.md)
        k = max(0, (int(frame[0]) - 600) // 14000)
        top, left, bottom, right = k * 14000 + 600, 50, k * 14000 + 13400, 900  # the whole frame
        long_in, short_in = self.frame_in
        if kw.get("format") in _FORMAT_IN and self.frame_in[1] > 2:  # 120 film: the format sets the length
            long_in = _FORMAT_IN[kw["format"]]
        full_rows, full_cols = int(short_in * dpi), int(long_in * dpi)
        c0 = int(round((int(frame[0]) - top) / (bottom - top) * full_cols))
        c1 = int(round((int(frame[2]) - top) / (bottom - top) * full_cols))
        r0 = int(round((right - int(frame[3])) / (right - left) * full_rows))
        r1 = int(round((right - int(frame[1])) / (right - left) * full_rows))
        c0, r0 = max(0, c0), max(0, r0)
        c1, r1 = min(full_cols, max(c0 + 8, c1)), min(full_rows, max(r0 + 8, r1))
        share = (c1 - c0) * (r1 - r0) / (full_cols * full_rows)  # less to send, so a shorter pass
        self._wait(progress, "scan", max(2, int(round((10 if dpi < 1000 else 30) * share))))
        rows, cols = r1 - r0, c1 - c0
        film = self._film(full_rows, full_cols, top % 7)
        if self.borders:
            self._edges(film, (int(full_cols * 0.03), int(full_cols * 0.025)))
        r.colors = {n: np.ascontiguousarray(p[r0:r1, c0:c1]) for n, p in film.items()}
        rng = np.random.default_rng()
        usual = {"red": 1200, "green": 900, "blue": 700}
        for name, plane in r.colors.items():  # noise that differs on every pass, like the real thing
            noise = rng.integers(-400, 401, plane.shape, dtype=np.int32)
            # a longer exposure reads more light off the same film, up to the ceiling; the noise stays as it is
            gain = (exposures or {}).get(name, usual.get(name, 1)) / usual.get(name, 1) if exposures else 1
            r.colors[name] = np.clip(plane.astype(np.float64) * gain + noise, 0, 65535).astype(np.uint16)
        r.ir = np.full((rows, cols), 50000, np.uint16) if (infrared or clean) else None
        # specks and a scratch that block the light, in the picture and in the infrared
        size = max(1, dpi // 500)
        spots = [((53 * n + 17) % max(1, rows - 8 * size), (211 * n + 40) % max(1, cols - 8 * size), size * (1 + n % 3))
                 for n in range(40)]
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
