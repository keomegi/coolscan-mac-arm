#!/usr/bin/env python3
"""Coolscan: a small Mac front end for Nikon Coolscan scanners, built on nkscan.

Run:   python3 coolscan.py            (then open http://127.0.0.1:8765)
Try:   python3 coolscan.py --demo     (a pretend scanner, to try the screens)

Needs Python 3.13+ and:  pip install nkscan numpy

What it does
  * Thumbnail: one fast pass over the whole strip, shown with the frames found.
  * Preview: a low resolution scan of each frame (like Nikon Scan's preview),
    or, with "Fast preview from thumbnail", each frame cut out of the thumbnail
    so no extra pass is needed.
  * Calibrate: autofocus and meter the exposure on one frame, then (optionally)
    reuse that focus and exposure for every preview and scan.
  * Scan: the chosen frames at full quality, saved as 16-bit TIFF with Nikon's
    ICC profile, plus the infrared plane when asked for.

Previews of negatives are inverted roughly so they are easy to judge. The saved
TIFFs are left exactly as the scanner produced them.
"""

import argparse
import bisect
import json
import math
import os
import re
import struct
import sys
import threading
import time
import traceback
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is missing. Install it with:  pip install nkscan numpy")

FILMS = ["negative", "positive", "kodachrome", "mono"]
PREVIEW_MAX_SIDE = 1400
WATCH_SECONDS = 2  # how often the idle scanner is asked whether film is in
HEADROOM = 0.02  # fraction of the range left free below black and above white in positives
SETTINGS_FILE = Path.home() / ".coolscan.json"  # settings and the current roll, kept between runs  # pixels; previews are shrunk to this for the browser


# ---------------------------------------------------------------- image output

def _png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(img):
    """img: uint8 array, (rows, cols) or (rows, cols, 3)."""
    img = np.ascontiguousarray(img)
    rows, cols = img.shape[:2]
    colour = 2 if img.ndim == 3 else 0
    raw = np.zeros((rows, img.strides[0] + 1), dtype=np.uint8)
    raw[:, 1:] = img.reshape(rows, -1)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, colour, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + _png_chunk(b"IEND", b""))


def write_tiff(path, planes, dpi, icc=None, meta=None):
    """Write an uncompressed 16-bit TIFF. planes: uint16 2-D arrays, 1 (grey), 3 (RGB)
    or 4 (RGB plus infrared as a fourth channel, as SilverFast's RGBI archive files do).

    meta (optional): {"taken": datetime, "description": str, "make": str, "model": str}.
    "taken" becomes the EXIF date the photo was taken, which galleries sort by, and
    the file's modification time."""
    meta = meta or {}
    data = np.dstack(planes) if len(planes) > 1 else planes[0]
    data = np.ascontiguousarray(data.astype("<u2"))
    rows, cols = data.shape[:2]
    spp = len(planes)
    pixels = data.tobytes()
    rows_per_strip = max(1, (1 << 20) // (cols * spp * 2))
    strip_bytes = cols * spp * 2 * rows_per_strip
    nstrips = math.ceil(rows / rows_per_strip)
    counts = [min(strip_bytes, len(pixels) - i * strip_bytes) for i in range(nstrips)]

    extra = bytearray()  # out-of-line values, placed after the 8-byte header
    base = 8

    def put(blob):
        off = base + len(extra)
        extra.extend(blob)
        if len(extra) % 2:
            extra.append(0)
        return off

    bits_off = put(struct.pack("<%dH" % spp, *([16] * spp))) if spp > 1 else None
    res_off = put(struct.pack("<II", int(dpi), 1))
    icc_off = put(icc) if icc else None
    counts_off = put(struct.pack("<%dI" % nstrips, *counts)) if nstrips > 1 else None
    offsets_pos = len(extra)
    put(b"\0" * 4 * nstrips) if nstrips > 1 else None
    pixel_off = base + len(extra)
    offsets = [pixel_off + i * strip_bytes for i in range(nstrips)]
    if nstrips > 1:
        extra[offsets_pos:offsets_pos + 4 * nstrips] = struct.pack("<%dI" % nstrips, *offsets)

    tags = [
        (256, 4, 1, cols), (257, 4, 1, rows),
        (258, 3, spp, bits_off if spp > 1 else 16),
        (259, 3, 1, 1), (262, 3, 1, 2 if spp >= 3 else 1),
        (273, 4, nstrips, offsets_pos + base if nstrips > 1 else pixel_off),
        (277, 3, 1, spp), (278, 4, 1, rows_per_strip),
        (279, 4, nstrips, counts_off if nstrips > 1 else counts[0]),
        (282, 5, 1, res_off), (283, 5, 1, res_off), (284, 3, 1, 1), (296, 3, 1, 2),
    ]
    if spp == 4:
        tags.append((338, 3, 1, 0))  # ExtraSamples: the fourth channel is unspecified data (IR)
    if icc:
        tags.append((34675, 7, len(icc), icc_off))
    exif = [(36864, b"0230"), (40961, int(meta.get("colorspace", 65535)))]  # ExifVersion, ColorSpace
    tags.append((274, 3, 1, 1))  # Orientation: as stored
    ascii_tags = [(270, meta.get("description")), (271, meta.get("make")), (272, meta.get("model"))]
    taken = meta.get("taken")
    if taken:
        stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
        ascii_tags.append((306, stamp))
        exif += [(36867, stamp), (36868, stamp)]  # DateTimeOriginal, DateTimeDigitized
    if meta.get("iso"):
        exif.append((34855, int(meta["iso"])))  # ISOSpeedRatings: the film's speed
    ifd_off = pixel_off + len(pixels)
    ifd_off += ifd_off % 2
    # strings go after the pixels too, so the pixel offsets above stay put
    tail = bytearray()

    def put_tail(text):
        blob = text.encode("ascii", "replace") + b"\0"
        if len(blob) <= 4:
            blob = blob.ljust(5, b"\0")
        off = ifd_off + len(tail)
        tail.extend(blob)
        if len(tail) % 2:
            tail.append(0)
        return off, len(text) + 1

    for tag, text in ascii_tags:
        if text:
            off, count = put_tail(text)
            tags.append((tag, 2, count, off))
    exif_entries = []
    for tag, value in exif:
        if isinstance(value, bytes):
            exif_entries.append((tag, 7, len(value), value))
        elif isinstance(value, int):
            exif_entries.append((tag, 3, 1, min(value, 65535)))
        else:
            off, count = put_tail(value)
            exif_entries.append((tag, 2, count, off))
    ifd_off += len(tail)
    if exif_entries:
        ntags = len(tags) + 1
        tags.append((34665, 4, 1, ifd_off + 2 + 12 * ntags + 4))
    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", ifd_off))
        f.write(extra)
        f.write(pixels)
        if f.tell() % 2:
            f.write(b"\0")
        f.write(tail)
        for entries in (tags, exif_entries):
            if not entries:
                continue
            f.write(struct.pack("<H", len(entries)))
            for tag, typ, count, value in sorted(entries):
                if isinstance(value, bytes):
                    f.write(struct.pack("<HHI4s", tag, typ, count, value))
                elif typ == 3 and count == 1:
                    f.write(struct.pack("<HHIHH", tag, typ, count, value, 0))
                else:
                    f.write(struct.pack("<HHII", tag, typ, count, value))
            f.write(b"\0\0\0\0")
    if taken:
        try:
            ts = taken.timestamp()
            os.utime(path, (ts, ts))
        except (OverflowError, OSError, ValueError):
            pass


# Nikon Scan's tone for negatives, measured on five frames scanned in both programs:
# nikon = lift + gain * v ** gamma, where v is the plain density stretch below (rms error 1.3%)
NIKON_TONE = (0.10, 0.985, 1.615)
# the model name Nikon Scan writes into its files
NIKON_MODEL_NAMES = {"LS-40 ED": "Nikon COOLSCAN IV ED", "LS-50 ED": "Nikon COOLSCAN V ED",
                     "LS-4000 ED": "Nikon SUPER COOLSCAN 4000 ED", "LS-5000 ED": "Nikon SUPER COOLSCAN 5000 ED"}


def invert_negative(planes, tone="nikon"):
    """Turn negative planes (uint16) into a positive picture, values 0..1 (float32).

    Each channel becomes film density, stretched between its own darkest and
    lightest 0.2%, measured inside the frame (not on the clear edges). The
    per-channel stretch takes off the orange mask. Density already follows
    brightness as the eye sees it, so no gamma is added.
    """
    out = []
    for p in planes:
        rows, cols = p.shape
        inner = p[rows // 20: rows - rows // 20, cols // 20: cols - cols // 20]
        step = max(1, int(math.sqrt(inner.size / 400_000)))
        sample = -np.log10(np.maximum(inner[::step, ::step].astype(np.float32), 1.0) / 65535.0)
        lo, hi = np.percentile(sample, (0.1, 99.9))
        v = -np.log10(np.maximum(p.astype(np.float32), 1.0) / 65535.0)
        v -= lo
        v /= max(hi - lo, 1e-6) / (1 - 2 * HEADROOM)
        v += HEADROOM  # keep a little room at both ends, as Nikon Scan does, for editing later
        np.clip(v, 0, 1, out=v)
        if tone == "nikon":
            lift, gain, gamma = NIKON_TONE
            np.power(v, gamma, out=v)
            v *= gain
            v += lift
            np.clip(v, 0, 1, out=v)
        out.append(v)
    return out


def display_image(planes, film, invert=True, tone="nikon"):
    """Turn scanner planes (dict name -> uint16) into an 8-bit picture for the browser."""
    names = [n for n in ("red", "green", "blue") if n in planes] or [next(iter(planes))]
    stack = [planes[n] for n in names]
    rows, cols = stack[0].shape
    step = max(1, math.ceil(max(rows, cols) / PREVIEW_MAX_SIDE))
    out = []
    if film in ("negative", "mono") and invert:
        # the same conversion as "save as positive", so the preview shows what is saved
        for v in invert_negative([p[::step, ::step] for p in stack], tone):
            out.append((v * 255 + 0.5).astype(np.uint8))
        return np.dstack(out) if len(out) == 3 else out[0]
    for p in stack:
        v = p[::step, ::step].astype(np.float32)
        lo, hi = np.percentile(v, (0.1, 99.9))
        v = (v - lo) / max(hi - lo, 1e-6)
        v = np.clip(v, 0, 1) ** (1 / 2.2)
        out.append((np.clip(v, 0, 1) * 255 + 0.5).astype(np.uint8))
    return np.dstack(out) if len(out) == 3 else out[0]


# ---------------------------------------------------------------- colour spaces
#
# Nikon Scan let you pick the colour space the scans are saved in. Here the
# scanner's own profile (Nikon's LS-40 profile, a lookup table from scanner RGB
# to Lab) is applied directly, then Lab goes to the chosen space and that
# space's profile is embedded. Relative colorimetric, no extra library needed.

_D50 = np.array([0.9642, 1.0, 0.8249])
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])

# name: (primaries xy, white xy, gamma or "srgb")
COLOUR_SPACES = {
    "sRGB IEC61966-2.1": (((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), (0.3127, 0.3290), "srgb"),
    "Adobe RGB (1998)": (((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)), (0.3127, 0.3290), 563 / 256),
    "Apple RGB": (((0.625, 0.34), (0.28, 0.595), (0.155, 0.07)), (0.3127, 0.3290), 1.8),
    "ColorMatch RGB": (((0.63, 0.34), (0.295, 0.605), (0.15, 0.075)), (0.3457, 0.3585), 1.8),
    "Bruce RGB": (((0.64, 0.33), (0.28, 0.65), (0.15, 0.06)), (0.3127, 0.3290), 2.2),
    "Wide Gamut RGB": (((0.7347, 0.2653), (0.1152, 0.8264), (0.1566, 0.0177)), (0.3457, 0.3585), 2.2),
    "ProPhoto RGB": (((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)), (0.3457, 0.3585), 1.8),
}


def _xy_to_xyz(x, y):
    return np.array([x / y, 1.0, (1 - x - y) / y])


def _space_matrix(name):
    """RGB (linear) -> XYZ adapted to D50, and the space's own white XYZ."""
    prim, white, _ = COLOUR_SPACES[name]
    w = _xy_to_xyz(*white)
    p = np.stack([_xy_to_xyz(*c) for c in prim], axis=1)
    m = p * np.linalg.solve(p, w)
    cone_src, cone_dst = _BRADFORD @ w, _BRADFORD @ _D50
    adapt = np.linalg.inv(_BRADFORD) @ np.diag(cone_dst / cone_src) @ _BRADFORD
    return adapt @ m, w


def _encode(v, gamma):
    v = np.clip(v, 0, 1)
    if gamma == "srgb":
        return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)
    return np.power(v, 1 / gamma)


def _decode_srgb(v):
    return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))


def _s15(v):
    return struct.pack(">i", int(round(v * 65536)))


def icc_profile(name):
    """A small ICC v2 matrix/TRC profile for one of COLOUR_SPACES."""
    m, white = _space_matrix(name)
    gamma = COLOUR_SPACES[name][2]
    desc = name.encode("ascii")

    def xyz(v):
        return b"XYZ \0\0\0\0" + b"".join(_s15(c) for c in v)

    if gamma == "srgb":
        lin = _decode_srgb(np.linspace(0, 1, 1024))  # curv maps encoded -> linear
        curv = b"curv\0\0\0\0" + struct.pack(">I", 1024) + struct.pack(">1024H", *np.round(lin * 65535).astype(int))
    else:
        curv = b"curv\0\0\0\0" + struct.pack(">IH", 1, int(round(gamma * 256))) + b"\0\0"
    tags = [
        (b"desc", b"desc\0\0\0\0" + struct.pack(">I", len(desc) + 1) + desc + b"\0" + b"\0" * 8 + b"\0\0\0" + b"\0" * 67),
        (b"cprt", b"text\0\0\0\0" + b"No copyright, use freely\0"),
        (b"wtpt", xyz(white)),
        (b"rXYZ", xyz(m[:, 0])), (b"gXYZ", xyz(m[:, 1])), (b"bXYZ", xyz(m[:, 2])),
        (b"rTRC", curv), (b"gTRC", curv), (b"bTRC", curv),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, data, seen = b"", b"", {}
    for sig, body in tags:
        while len(body) % 4:
            body += b"\0"
        if body in seen:  # the three TRCs share one copy
            off = seen[body]
        else:
            off = offset + len(data)
            seen[body] = off
            data += body
        table += sig + struct.pack(">II", off, len(body))
    size = offset + len(data)
    header = (struct.pack(">I", size) + b"\0\0\0\0" + bytes([2, 0x10, 0, 0]) + b"mntrRGB XYZ "
              + struct.pack(">6H", 2026, 1, 1, 0, 0, 0) + b"acsp" + b"APPL" + b"\0" * 4 + b"\0" * 4
              + b"\0" * 4 + b"\0" * 8 + b"\0" * 4 + b"".join(_s15(c) for c in _D50) + b"\0" * 4)
    header += b"\0" * (128 - len(header))
    return header + struct.pack(">I", len(tags)) + table + data


class ScannerProfile:
    """The A2B0 lookup table of a Nikon scanner profile (mft2, Lab out)."""

    def __init__(self, icc):
        count = struct.unpack(">I", icc[128:132])[0]
        tags = {}
        for k in range(count):
            sig, off, size = struct.unpack(">4sII", icc[132 + 12 * k:144 + 12 * k])
            tags[sig] = icc[off:off + size]
        lut = tags[b"A2B0"]
        if lut[:4] != b"mft2" or icc[20:24] != b"Lab ":
            raise ValueError("not a Lab lookup-table profile")
        cin, cout, grid = lut[8], lut[9], lut[10]
        nin, nout = struct.unpack(">HH", lut[48:52])
        at = 52
        self.inc = np.frombuffer(lut, ">u2", cin * nin, at).reshape(cin, nin) / 65535.0
        at += 2 * cin * nin
        self.clut = np.frombuffer(lut, ">u2", grid ** cin * cout, at).reshape(grid, grid, grid, cout) / 65535.0
        at += 2 * grid ** cin * cout
        self.outc = np.frombuffer(lut, ">u2", cout * nout, at).reshape(cout, nout) / 65535.0
        self.grid = grid
        # Relative colorimetric, as LittleCMS does for v2 profiles: the profile's
        # media white becomes the PCS white (D50)
        w = tags.get(b"wtpt")
        self.adapt = None
        if w and w[:4] == b"XYZ ":
            white = np.array(struct.unpack(">3i", w[8:20])) / 65536.0
            if np.abs(white - _D50).max() > 1e-3:
                cone = _BRADFORD @ white
                self.adapt = np.linalg.inv(_BRADFORD) @ np.diag((_BRADFORD @ _D50) / cone) @ _BRADFORD

    @staticmethod
    def _curve(table, v):
        return np.interp(v, np.linspace(0, 1, table.size), table)

    def to_lab(self, rgb):
        """rgb: (n, 3) floats 0..1 -> Lab (n, 3)."""
        g = self.grid - 1
        v = np.stack([self._curve(self.inc[c], rgb[:, c]) for c in range(3)], axis=1) * g
        i0 = np.clip(np.floor(v).astype(np.int32), 0, g - 1)
        f = v - i0
        out = np.zeros((rgb.shape[0], 3))
        for dr in (0, 1):
            wr = f[:, 0] if dr else 1 - f[:, 0]
            for dg in (0, 1):
                wg = f[:, 1] if dg else 1 - f[:, 1]
                for db in (0, 1):
                    wb = f[:, 2] if db else 1 - f[:, 2]
                    out += (wr * wg * wb)[:, None] * self.clut[i0[:, 0] + dr, i0[:, 1] + dg, i0[:, 2] + db]
        out = np.stack([self._curve(self.outc[c], out[:, c]) for c in range(3)], axis=1)
        # ICC v2 Lab encoding: L 0..0xFF00 = 0..100, a/b 0x8000 = 0, 256 counts per unit
        enc = out * 65535.0
        return np.stack([enc[:, 0] / 652.80, enc[:, 1] / 256.0 - 128.0, enc[:, 2] / 256.0 - 128.0], axis=1)


def _lab_to_xyz(lab):
    fy = (lab[:, 0] + 16) / 116
    fx, fz = fy + lab[:, 1] / 500, fy - lab[:, 2] / 200
    e, k = 216 / 24389, 24389 / 27

    def inv(t):
        return np.where(t ** 3 > e, t ** 3, (116 * t - 16) / k)

    return np.stack([inv(fx), inv(fy), inv(fz)], axis=1) * _D50


def convert_planes(planes, space, scanner_icc=None):
    """Convert 16-bit RGB planes into `space`; returns new planes and the profile to embed.

    With scanner_icc the planes are scanner RGB, read through Nikon's profile.
    Without it they are taken as sRGB (the app's own negative-to-positive output).
    """
    if space not in COLOUR_SPACES or len(planes) != 3:
        return planes, None
    m, _ = _space_matrix(space)
    to_rgb = np.linalg.inv(m)
    gamma = COLOUR_SPACES[space][2]
    prof = ScannerProfile(scanner_icc) if scanner_icc else None
    if prof is None and space == "sRGB IEC61966-2.1":
        return planes, icc_profile(space)
    srgb_m = None if prof else _space_matrix("sRGB IEC61966-2.1")[0]
    rows, cols = planes[0].shape
    out = [np.empty((rows, cols), np.uint16) for _ in range(3)]
    step = max(1, 500_000 // cols)
    for r0 in range(0, rows, step):
        rgb = np.stack([p[r0:r0 + step].reshape(-1) for p in planes], axis=1).astype(np.float64) / 65535
        if prof:
            xyz = _lab_to_xyz(prof.to_lab(rgb))
            if prof.adapt is not None:
                xyz = xyz @ prof.adapt.T
        else:
            xyz = _decode_srgb(rgb) @ srgb_m.T
        res = (_encode(xyz @ to_rgb.T, gamma) * 65535 + 0.5).astype(np.uint16)
        n = res.shape[0] // cols
        for c in range(3):
            out[c][r0:r0 + n] = res[:, c].reshape(n, cols)
    return out, icc_profile(space)


# ---------------------------------------------------------------- demo scanner

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
        d.frames = [(i * 14000 + 600, 50, i * 14000 + 13400, 900) for i in range(6)]
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
        return "focused", self.focus

    def scan_frame(self, frame, dpi=None, infrared=False, clean=False, exposures=None,
                   focus=None, progress=None, **kw):
        dpi = dpi or 2900
        self._wait(progress, "scan", 10 if dpi < 1000 else 30)
        r = self.Result()
        rows, cols = int(0.94 * dpi), int(1.42 * dpi)
        r.colors = self._film(rows, cols, frame[0] % 7)
        r.ir = np.full((rows, cols), 50000, np.uint16) if (infrared or clean) else None
        r.dpi, r.rows, r.cols = dpi, rows, cols
        r.exposures = exposures or {"red": 1200, "green": 900, "blue": 700}
        r.complete, r.cleaned = True, (1234 if clean else None)
        r.focused = "skipped" if focus == "hold" else "focused"
        r.focus_position = self.focus
        return r

    def nikon_profile(self, film):
        return None

    def close(self):
        pass


# ---------------------------------------------------------------- the app

class Cancelled(Exception):
    pass


# Like Nikon Scan, a failure is shown as what happened and what to do next,
# never as a bare code. Each entry: words found in the driver's message, then
# the advice. The first match wins, so the specific ones come first.
_ADVICE = (
    (("no nikon scanner found", "no such scanner"),
     "No scanner found. Check that it is switched on and the USB cable is plugged in, then press Connect."),
    (("disconnected", "timed out", "io error"),
     "The scanner stopped answering. Check that it is switched on and plugged in, then press Connect. "
     "If this keeps happening, another device on the same USB port or hub may be the cause."),
    (("another initiator",),
     "Another program is using the scanner. Quit it (Nikon Scan, VueScan, a Windows virtual machine), "
     "then press Connect."),
    (("scanner is busy",),
     "The scanner is still busy (moving the film or warming up). Wait until its light stops blinking, "
     "then try again."),
    (("nothing left to load",),
     "The scanner has no film waiting. Feed the strip into the slot until the scanner takes it."),
    (("no film",),
     "There is no film in the scanner. Feed the strip in (or push the slide in) and try again. "
     "The scanner can rewind a strip by itself after sitting idle for a while."),
    (("adapter is ejected", "pulled out"),
     "The film adapter is not fully in. Take the film out, push the adapter in until it locks, and try again."),
    (("undefined adapter", "undefined holder", "mask is not set", "door is not"),
     "The scanner does not accept this adapter or holder as it is. Take it out and put it in again."),
    (("nonstandard film", "6 frames or more"),
     "The scanner cannot use this strip: it has too few or too many frames. "
     "The strip adapter takes strips of 2 to 6 frames."),
    (("could not feed",),
     "The scanner could not feed the film. The strip may be too short, too long, curled, or have torn "
     "perforations. Take it out and feed it in again."),
    (("frame beyond",),
     "The scanner could not find that frame on the strip. Press Thumbnail only to find the frames again."),
    (("did not reach focus",),
     "Autofocus failed. Press Pick and click a part of the preview with clear detail "
     "(not sky or a blank area), then try again."),
    (("does not autofocus", "is not supported"),
     "This scanner or adapter cannot do that."),
    (("command aborted",),
     "A data transfer error. Try again. Another device on the same USB port or hub may be the cause."),
    (("rejected the command",),
     "The scanner refused that command. Press Thumbnail only and try again. If it repeats, "
     "it is a fault in the app or the driver, not in your scanner."),
    (("hardware error", "mechanical error", "medium error", "unrecognized sense", "unexpected status",
      "scanner fault"),
     "The scanner reports a hardware error. Switch it off, wait a few seconds, switch it on again, "
     "then press Connect."),
)


def explain(e):
    """A failure in plain words with what to do, then the driver's own wording in brackets."""
    kind, text = type(e).__name__, str(e)
    low = text.lower()
    if isinstance(e, OSError):
        where = f" ({e.filename})" if e.filename else ""
        return (f"Could not write the file{where}: {e.strerror or text}. "
                "Check that the folder exists, can be written to, and the disk has room.")
    for words, advice in _ADVICE:
        if any(w in low for w in words):
            return advice if type(e) is RuntimeError else f"{advice} [{kind}: {text}]"
    if kind == "MediaError":
        return ("The scanner needs something put right by hand. Check that the adapter is pushed in fully "
                f"and the film is fed in straight, then try again. [{kind}: {text}]")
    return text if type(e) is RuntimeError else f"{kind}: {text}"


def _stopped(e):
    """The person pressed Stop (the driver reports that as its own error)."""
    return isinstance(e, Cancelled) or type(e).__name__ == "ScanCancelled" or str(e) in ("cancelled", "scan cancelled")


def _lost_scanner(e):
    return "disconnected" in str(e) or type(e).__name__ in ("TransportError", "DeviceNotFound")


def _this_frame_only(e):
    """A failure that the next frame may not have (focus, one bad pass), so a batch can carry on."""
    return type(e).__name__ in ("ScannerError", "TransientError", "UnsupportedError") and not _lost_scanner(e)


class App:
    def __init__(self, demo=False):
        self.demo = demo
        self.lock = threading.Lock()
        # One thread at a time talks to the scanner. A job holds this for its whole
        # length; the film watcher only looks when nobody holds it.
        self.session_lock = threading.Lock()
        self.film_in = None         # what the scanner last said: film in (True), out (False), not asked (None)
        self.film_armed = True      # the next time film is seen in, show its thumbnail
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
        self.version = 0
        self.settings = {
            "film": "negative", "format": "", "preview_mode": "scan", "preview_dpi": 290,
            "dpi": 2900, "samples": 1, "ir": True, "clean": True,
            "focus_mode": "frame", "focus_point": None, "focus_position": 0,
            "exposure_mode": "auto", "white_balance": "film", "rotate180": False,
            "invert_preview": True, "negatives": "positive", "outdir": str(Path.home() / "Pictures" / "Coolscan"),
            "basename": "Scan", "suffix": "", "digits": 3, "start": 1, "archive": False, "photo_date": "",
            "colour_space": "ProPhoto RGB", "tone": "flat", "defaults_version": 2,
            "stop_on_errors": False, "scan_log": True, "eject_after": False, "auto_thumbnail": True,
            "roll": "", "roll_tag": "overall", "roll_day": "", "roll_day_count": 1, "film_brand": "", "film_stock": "", "film_iso": "", "film_notes": "",
        }

    # -- bookkeeping
    def say(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True)
        with self.lock:
            self.log.append(line)
            self.log = self.log[-300:]
            self.version += 1

    def _progress(self, phase, pass_number, done, total):
        with self.lock:
            self.progress = {"phase": phase, "done": done, "total": total}
            self.version += 1
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
            "film_text": self._film_text(),
            "sample_name": self._stem(None, int(self.settings["start"] or 0)) + ".tif",
            "film_in": self.film_in if self.settings["auto_thumbnail"] else None,
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
        self.say("the next button you press (or Connect) connects to the scanner again")

    # -- watching for film
    def watch(self):
        """Its own thread: while the scanner is idle, notice film going in and show its thumbnail."""
        while True:
            time.sleep(WATCH_SECONDS)
            try:
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
        self.discovery, self.thumb_png, self.medium_seen = None, None, None
        self.previews, self.preview_info = {}, {}
        self.film_in, self.film_armed = None, True

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

    def eject(self):
        s = self.need_session()
        s.eject()
        self.say("ejected")
        self.refresh_caps()
        self.discovery, self.thumb_png, self.previews, self.preview_info = None, None, {}, {}
        self.preview_raw, self.frame_rot, self.frame_focus = {}, {}, {}
        self.focus_lock = self.exposure_lock = None
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
                            invert=self.settings["invert_preview"], tone=self.settings["tone"])
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
            r = s.scan_frame(d.frames[i], dpi=dpi, progress=self._progress,
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

    def _stem(self, folders, n):
        """Nikon Scan's naming: prefix + zero-padded counter + suffix, e.g. Scan001."""
        st = self.settings
        digits = min(6, max(1, int(st["digits"] or 3)))
        return f"{st['basename']}{n:0{digits}d}{st['suffix']}{self._roll_tag()}"

    def _roll_tag(self):
        """The roll number at the end of the name, e.g. _R02 for the 2nd roll today."""
        st = self.settings
        mode = st["roll_tag"]
        if mode == "overall":
            return f"_R{self._roll_number(st['roll'] or '') or 1:03d}"
        if mode in ("day", "date_day"):
            date = st["roll_day"] or time.strftime("%Y%m%d")
            day = f"_{date}" if mode == "date_day" else ""
            return f"{day}_R{int(st['roll_day_count'] or 1):02d}"
        return ""

    def _next_number(self, folders, n):
        while any(next(f.glob(self._stem(folders, n) + "*.tif"), None) for f in folders):
            n += 1
        return n

    def _film_text(self):
        st = self.settings
        iso = str(st["film_iso"]).strip()
        parts = [st["film_brand"].strip(), st["film_stock"].strip(),
                 (f"ISO {iso}" if iso and iso.isdigit() else iso)]
        text = " ".join(p for p in parts if p)
        if st["film_notes"].strip():
            text = (text + ", " if text else "") + st["film_notes"].strip()
        return text

    def _base(self):
        return Path(os.path.expanduser(self.settings["outdir"]))

    @staticmethod
    def _roll_number(name):
        m = re.match(r"Roll (\d+)", name)
        return int(m.group(1)) if m else 0

    def roll_dir(self):
        """The current roll's folder. Strips keep going into it until New roll."""
        st = self.settings
        if not st["roll"]:
            base = self._base()
            existing = sorted((d.name for d in base.glob("Roll *") if d.is_dir()),
                              key=self._roll_number) if base.is_dir() else []
            st["roll"] = existing[-1] if existing else "Roll 001"
            st["roll_day"] = st["roll_day"] or time.strftime("%Y%m%d")
            self.save_settings()
        return self._base() / st["roll"]

    def new_roll(self, details):
        """Start a new folder for the next roll. Called only after the person confirms."""
        st = self.settings
        for k in ("film_brand", "film_stock", "film_iso", "film_notes"):
            st[k] = str(details.get(k, "")).strip()
        base = self._base()
        used = [self._roll_number(d.name) for d in base.glob("Roll *")] if base.is_dir() else []
        used.append(self._roll_number(st["roll"] or ""))
        name = f"Roll {max(used + [0]) + 1:03d}"
        film = self._film_text()
        if film:
            name += " - " + re.sub(r'[\\/:*?"<>|]', "-", film)[:80].strip()
        st["roll"] = name
        today = time.strftime("%Y%m%d")
        st["roll_day_count"] = st["roll_day_count"] + 1 if st["roll_day"] == today else 1
        st["roll_day"] = today
        self.save_settings()
        self.say(f"new roll: saving to {base / name}, numbering starts again")
        with self.lock:
            self.version += 1

    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=1))
        except OSError:
            pass

    def load_settings(self):
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return
        for k, v in saved.items():
            if k in self.settings and type(v) is type(self.settings[k]):
                self.settings[k] = v
        if not isinstance(saved.get("defaults_version"), int) or saved["defaults_version"] < 2:
            # settings saved before ProPhoto RGB and the flat tone became the defaults:
            # move them over once. Whatever is picked after that is kept.
            self.settings["colour_space"] = "ProPhoto RGB"
            self.settings["tone"] = "flat"
        self.settings["defaults_version"] = 2
        self.save_settings()

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
                parts = [int(x) for x in re.split(r"[-./ ]", text) if x]
                y, m, d = (parts + [1, 1])[:3]
                # frames of one roll get one second apart, so galleries keep them in order
                meta["taken"] = datetime.datetime(y, m, d, 12, 0, 0) + datetime.timedelta(seconds=n)
            except (ValueError, IndexError):
                self.say(f"can't read the photo date {text!r}; use e.g. 2005 or 2005-06-14. Using today.")
        return meta

    def _positive(self, planes, nikon_icc):
        """The finished picture in the chosen colour space, and the profile to embed.

        Slides go through Nikon's scanner profile into the colour space. Negatives
        are inverted first; that result is sRGB-like, so it is converted from sRGB.
        "Scanner RGB" keeps Nikon's profile on slides, and labels negatives sRGB.
        """
        space = self.settings["colour_space"]
        if self._film() in ("negative", "mono"):
            pos = [(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(planes, self.settings["tone"])]
            if space not in COLOUR_SPACES:
                space = "sRGB IEC61966-2.1"
            return convert_planes(pos, space)
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
        clean = bool(st["clean"])
        job = dict(archive=archive, clean=clean, infrared=archive or bool(st["ir"] or clean),
                   raw_dir=raw_dir, pos_dir=pos_dir, outdir=outdir)
        icc = s.nikon_profile(self._film())
        job["icc"] = bytes(icc) if icc else None
        log = self._scan_log_open(outdir, len(indices))
        n = max(0, int(st["start"] or 0))
        done, failed, ending = 0, [], "completed"
        try:
            for i in indices:
                if self.cancel:
                    raise Cancelled
                n = self._next_number(folders, n)
                stem = self._stem(folders, n)
                saved = []
                log(f"Frame {i + 1} started {time.strftime('%H:%M:%S')}")
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
        except Cancelled:
            ending = "stopped by the user"
            raise
        except Exception:
            ending = "aborted"
            raise
        finally:
            missed = f", {len(failed)} failed (frame {', '.join(map(str, failed))})" if failed else ""
            log(f"Batch scan {ending} {time.strftime('%H:%M:%S')}: {done} of {len(indices)} frame(s) saved{missed}")
            log("")
        self.say(f"scan finished: {done} of {len(indices)} frame(s) saved{missed}")
        if st["eject_after"]:
            if failed:
                self.say("not ejecting, because a frame failed and you may want to scan it again")
            else:
                self.eject()

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
            f"dust removal {'on' if st['clean'] else 'off'}" + (", archive mode" if st["archive"] else ""))
        add(f"Frames to scan: {count}")
        return add

    def _scan_one(self, s, d, i, n, stem, job, saved):
        """Scan frame i and write its files. Each file written is added to `saved`."""
        st = self.settings
        icc, clean, infrared = job["icc"], job["clean"], job["infrared"]
        raw_dir, pos_dir, outdir = job["raw_dir"], job["pos_dir"], job["outdir"]
        meta = self._meta(n)
        base = dict(dpi=int(st["dpi"]), samples=int(st["samples"]), progress=self._progress)
        opts = self._scan_options(s, i, d.frames[i], infrared)
        colours = lambda r: [self._rot(r.colors[k], i) for k in ("red", "green", "blue") if k in r.colors] \
            or [self._rot(p, i) for p in r.colors.values()]
        if job["archive"]:
            # 1) the raw pass: no cleaning, infrared kept as a fourth channel
            self.say(f"frame {i + 1}: archive scan at {st['dpi']} dpi (raw, with infrared)")
            r = s.scan_frame(d.frames[i], infrared=True, clean=False, **base, **opts)
            raw = colours(r)
            planes4 = raw + ([self._rot(r.ir, i)] if r.ir is not None else [])
            write_tiff(raw_dir / f"{stem}.tif", planes4, r.dpi, icc, meta=meta)
            saved.append(f"RAW/{stem}.tif")
            self.say(f"saved RAW/{stem}.tif" + ("" if r.complete else " (warning: pass came back short)"))
            if r.ir is None:
                self.say("warning: the scanner sent no infrared for this frame")
            elif st["ir"]:
                # the IR is already the 4th channel of the RAW file; this copy is easy to open
                write_tiff(raw_dir / f"{stem}_IR.tif", [self._rot(r.ir, i)], r.dpi, meta=meta)
                saved.append(f"RAW/{stem}_IR.tif")
                self.say(f"saved RAW/{stem}_IR.tif")
            # 2) the finished picture, cleaned with the infrared
            finished = raw
            if clean:
                finished = self.clean_frame(s, i, d.frames[i], r, base, opts, colours)
            pos, pos_icc = self._positive(finished, icc)
            write_tiff(pos_dir / f"{stem}.tif", pos, r.dpi, pos_icc, meta={**meta, "colorspace": 1 if pos_icc and b"sRGB" in pos_icc[:600] else 65535})
            saved.append(f"Positive/{stem}.tif")
            self.say(f"saved Positive/{stem}.tif")
            return
        self.say(f"frame {i + 1}: scanning at {st['dpi']} dpi, {st['samples']} sample(s)"
                 + (", cleaning dust" if clean else ""))
        r = s.scan_frame(d.frames[i], infrared=infrared, clean=clean, **base, **opts)
        planes = colours(r)
        note = "" if r.complete else " (warning: the pass came back short)"
        negative = self._film() in ("negative", "mono")
        if negative and st["negatives"] in ("negative", "both"):
            raw_name = f"{stem}.tif" if st["negatives"] == "negative" else f"{stem}_negative.tif"
            write_tiff(outdir / raw_name, planes, r.dpi, icc, meta=meta)  # with Nikon's profile
            saved.append(raw_name)
            self.say(f"saved {raw_name}{note}")
        if not negative or st["negatives"] in ("positive", "both"):
            pos, pos_icc = self._positive(planes, icc)
            write_tiff(outdir / f"{stem}.tif", pos, r.dpi, pos_icc, meta={**meta, "colorspace": 1 if pos_icc and b"sRGB" in pos_icc[:600] else 65535})
            saved.append(f"{stem}.tif")
            self.say(f"saved {stem}.tif ({st['colour_space'] if st['colour_space'] in COLOUR_SPACES else 'scanner RGB'}){note}")
        if r.ir is not None and st["ir"]:
            write_tiff(outdir / f"{stem}_IR.tif", [self._rot(r.ir, i)], r.dpi, meta=meta)
            saved.append(f"{stem}_IR.tif")
            self.say(f"saved {stem}_IR.tif")
        if r.cleaned is not None:
            self.say(f"dust removal rebuilt {r.cleaned} pixels")

    def clean_frame(self, s, i, frame, raw_result, base, opts, colours):
        """Dust and scratch removal for archive mode; returns cleaned colour planes.

        nkscan only cleans inside a pass, so this takes a second pass with
        cleaning on, at the first pass's exact exposure and lens position so
        the two match. Swap this function for another cleaner (for example
        Nikon's Digital ICE) that works from the raw RGB and infrared instead.
        """
        self.say(f"frame {i + 1}: second pass with dust removal (same focus and exposure)")
        again = dict(opts)
        again["exposures"] = dict(raw_result.exposures)
        pos = raw_result.focus_position
        again["focus"] = int(pos) if pos is not None else "hold"
        r = s.scan_frame(frame, infrared=True, clean=True, **base, **again)
        if r.cleaned is not None:
            self.say(f"dust removal rebuilt {r.cleaned} pixels")
        return colours(r)


# ---------------------------------------------------------------- web page

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coolscan</title>
<style>
:root{--bg:#f4f4f2;--card:#fff;--ink:#1d1d1f;--mute:#6e6e73;--line:#d8d8d4;--acc:#0a66d8;--warn:#b25000}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1d;--card:#252528;--ink:#ececee;--mute:#9a9aa0;--line:#3a3a3e;--acc:#4c9bff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px -apple-system,system-ui,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{font-size:17px;margin:0}#status{color:var(--mute)}
main{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;padding:16px}
@media (max-width:800px){main{grid-template-columns:minmax(0,1fr)}}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin:0 0 8px}
label{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:6px 0}
select,input[type=number],input[type=text]{width:150px;padding:4px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px}
button{padding:6px 10px;border-radius:7px;border:1px solid var(--line);background:var(--bg);color:var(--ink);cursor:pointer;margin:3px 3px 3px 0}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}button:disabled{opacity:.45;cursor:default}
.hint{color:var(--mute);font-size:12px;margin:4px 0;overflow-wrap:anywhere}
#thumbwrap{position:relative;overflow-x:auto}#thumbwrap .inner{position:relative;display:inline-block}
#thumb{display:block;max-width:none;height:160px}
.box{position:absolute;border:2px solid var(--acc);color:#fff;font-size:11px;cursor:pointer}
.box span{background:var(--acc);padding:0 3px}.box.sel{border-color:#f5b400}.box.sel span{background:#f5b400;color:#000}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.frame{border:2px solid var(--line);border-radius:8px;padding:6px;cursor:pointer}.frame.sel{border-color:#f5b400}
.imgbox{height:220px;display:flex;align-items:center;justify-content:center;background:#000}
.frame img{display:block;max-width:100%;max-height:220px;image-orientation:none}[hidden]{display:none!important}
.frame .cap{font-size:12px;color:var(--mute);margin-top:4px}
#log{font:12px ui-monospace,Menlo,monospace;white-space:pre-wrap;max-height:220px;overflow:auto;color:var(--mute)}
progress{width:100%}.imgw{position:relative;display:inline-block;line-height:0}.mark{position:absolute;width:14px;height:14px;margin:-7px;border:2px solid #f5b400;border-radius:50%;pointer-events:none}
body.picking .frame img{cursor:crosshair}
</style></head><body>
<header><h1>Coolscan</h1><span id="status">starting…</span><span style="flex:1"></span>
<button id="b-connect">Connect</button><button id="b-load">Load</button><button id="b-eject">Eject</button></header>
<main><div>
<section><h2>Film</h2>
<label>Film type<select id="s-film"><option value="negative">Colour negative</option><option value="positive">Slide</option><option value="kodachrome">Kodachrome</option><option value="mono">B&amp;W negative</option></select></label>
<label id="fmtrow">Format<select id="s-format"><option value="">From holder</option></select></label>
</section>
<section><h2>Preview</h2>
<button class="primary" id="b-thumb">Thumbnail only</button><button id="b-preview">Preview</button>
<label>Preview from<select id="s-preview_mode"><option value="scan">Preview scan (sharper)</option><option value="thumbnail">Thumbnail (fast)</option></select></label>
<label>Preview scan dpi<select id="s-preview_dpi"></select></label>
<label>Invert negatives in preview<input type="checkbox" id="s-invert_preview"></label>
<label>Thumbnail when film goes in<input type="checkbox" id="s-auto_thumbnail"></label>
<label>Rotate all 180°<input type="checkbox" id="s-rotate180"></label>
<div>Rotate selected <button id="b-rl" title="90° left">⟲ 90°</button><button id="b-rr" title="90° right">⟳ 90°</button></div>
<p class="hint">Click a frame to select it, ⌘-click to add or remove one, Shift-click for a range. Rotation applies to the previews and to the saved scans.</p>
<p class="hint">Thumbnail only: one quick pass over the strip. "Thumbnail (fast)" cuts each frame out of that pass instead of scanning it again.</p>
</section>
<section><h2>Focus &amp; exposure</h2>
<button class="primary" id="b-cal">Calibrate</button><button id="b-af">Autofocus</button><button id="b-ae">Auto exposure</button><button id="b-unlock">Reset</button>
<p class="hint">These work on the selected frame (or frame 1). Calibrate does both, then keeps that focus and exposure for every frame.</p>
<label>Focus<select id="s-focus_mode"><option value="frame">Autofocus each frame once</option><option value="auto">Autofocus on every pass</option><option value="same">Same focus for all frames</option><option value="manual">Manual lens position</option></select></label>
<label id="posrow">Lens position<input type="number" id="s-focus_position"></label>
<label>Focus point<span><span id="fpoint">centre</span> <button id="b-fpoint">Pick</button></span></label>
<label>Exposure<select id="s-exposure_mode"><option value="auto">Auto exposure every frame</option><option value="same">Keep exposure from Auto exposure</option></select></label>
<label>White balance<select id="s-white_balance"><option value="film">Film default</option><option value="unlock">Per channel (removes cast)</option><option value="lock">Locked (keeps cast)</option></select></label>
<p class="hint" id="calinfo"></p>
</section>
<section><h2>Roll</h2>
<div id="rollname" style="font-weight:600"></div><div class="hint" id="rollfilm"></div>
<button id="b-newroll">New roll…</button>
<div id="rolldlg" hidden style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px">
<p class="hint">Start a new folder for the next roll? Film details are optional; they name the folder and go into the files.</p>
<label>Brand<input type="text" id="r-film_brand" placeholder="e.g. Kodak"></label>
<label>Film<input type="text" id="r-film_stock" placeholder="e.g. Portra"></label>
<label>ISO<input type="text" id="r-film_iso" placeholder="e.g. 400"></label>
<label>Notes<input type="text" id="r-film_notes" placeholder="e.g. Summer 2005"></label>
<button class="primary" id="b-rollok">Start new roll</button><button id="b-rollcancel">Cancel</button>
</div></section>
<section><h2>Scan</h2>
<label>Resolution (dpi)<select id="s-dpi"></select></label>
<label>Multi-sample<select id="s-samples"></select></label>
<label>Save infrared<input type="checkbox" id="s-ir"></label>
<label>Dust &amp; scratch removal<input type="checkbox" id="s-clean"></label>
<label>Archive mode<input type="checkbox" id="s-archive"></label>
<p class="hint" id="archint">Archive mode saves two folders: RAW (untouched scan with infrared as a 4th channel) and Positive (finished picture, dust removed if ticked).</p>
<label id="negrow">Negatives saved as<select id="s-negatives"><option value="positive">Positive</option><option value="negative">Negative (raw)</option><option value="both">Both</option></select></label>
<label>Folder<input type="text" id="s-outdir"></label>
<label>Negative tone<select id="s-tone"><option value="flat">Flat (for editing)</option><option value="nikon">Nikon Scan look</option></select></label>
<label>Colour space<select id="s-colour_space"><option value="ProPhoto RGB">ProPhoto RGB</option><option value="Adobe RGB (1998)">Adobe RGB (1998)</option><option value="sRGB IEC61966-2.1">sRGB IEC61966-2.1</option><option value="Apple RGB">Apple RGB</option><option value="ColorMatch RGB">ColorMatch RGB</option><option value="Bruce RGB">Bruce RGB</option><option value="Wide Gamut RGB">Wide Gamut RGB</option><option value="scanner">Scanner RGB (Nikon profile)</option></select></label>
<label>File name prefix<input type="text" id="s-basename"></label>
<label>Suffix<input type="text" id="s-suffix"></label>
<label>Roll number in name<select id="s-roll_tag"><option value="overall">Overall roll (_R017)</option><option value="day">Roll of the day (_R02)</option><option value="date_day">Date + roll of the day (_20261122_R02)</option><option value="none">None</option></select></label>
<label>Counter digits<input type="number" id="s-digits" min="1" max="6"></label>
<label>Start number<input type="number" id="s-start" min="0"></label>
<label>Photo taken (year, or year-month-day)<input type="text" id="s-photo_date" placeholder="e.g. 2005 or 2005-06-14"></label>
<p class="hint">Galleries sort the scans by this date instead of today's. The real scan date stays in the file's description. Leave empty to use today.</p>
<p class="hint" id="nameex"></p>
<label>Stop on errors<input type="checkbox" id="s-stop_on_errors"></label>
<label>Eject film when done<input type="checkbox" id="s-eject_after"></label>
<label>Write scan log<input type="checkbox" id="s-scan_log"></label>
<p class="hint">If a frame fails (for example autofocus), the rest of the strip is still scanned unless "Stop on errors" is ticked. The scan log is ScanLog.txt in the roll folder.</p>
<button class="primary" id="b-scan">Scan all frames</button><button id="b-stop">Stop</button>
<p class="hint">Scans the whole strip in one go. To scan only some frames, select them first (click, ⌘-click, Shift-click) and the button scans just those.</p>
</section></div>
<div><section><h2>Thumbnail</h2><div id="thumbwrap"><div class="inner"><img id="thumb" alt=""></div></div><p class="hint" id="nothumb">No thumbnail yet.</p></section>
<section><h2>Frames</h2><div id="grid"></div></section>
<section><h2>Activity</h2><div class="hint" id="progtxt" hidden></div><progress id="prog" value="0" max="1" hidden></progress><div id="log"></div></section></div></main>
<script>
let S=null, sel=new Set(), lastV=-1, stamp=Date.now();
const $=id=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
function selList(){return [...sel].sort((a,b)=>a-b)}
const keys=['film','format','preview_mode','preview_dpi','invert_preview','rotate180','focus_mode','focus_position','exposure_mode','white_balance','dpi','samples','ir','clean','negatives','archive','outdir','basename','suffix','digits','start','photo_date','colour_space','roll_tag','tone','stop_on_errors','eject_after','scan_log','auto_thumbnail'];
let picking=false;
keys.forEach(k=>{const e=$('s-'+k);e.addEventListener('change',()=>{const v=e.type=='checkbox'?e.checked:e.value;api('/api/settings',{[k]:v})})});
$('b-fpoint').onclick=()=>{if(S.settings.focus_point){api('/api/settings',{focus_point:null});return}picking=!picking;document.body.classList.toggle('picking',picking);$('fpoint').textContent=picking?'click a preview…':'centre'};
$('b-connect').onclick=()=>api('/api/connect');$('b-load').onclick=()=>api('/api/load');$('b-eject').onclick=()=>{sel.clear();api('/api/eject')};
$('b-thumb').onclick=()=>{sel.clear();api('/api/thumbnail')};$('b-preview').onclick=()=>api('/api/preview',{frames:selList()});
$('b-cal').onclick=()=>api('/api/calibrate',{frame:selList()[0]??0});$('b-af').onclick=()=>api('/api/autofocus',{frame:selList()[0]??0});
$('b-ae').onclick=()=>api('/api/autoexpose',{frame:selList()[0]??0});$('b-unlock').onclick=()=>api('/api/unlock');
$('b-newroll').onclick=()=>{['film_brand','film_stock','film_iso','film_notes'].forEach(k=>$('r-'+k).value='');$('rolldlg').hidden=false;$('b-newroll').hidden=true;$('r-film_brand').focus()};
$('b-rollcancel').onclick=()=>{$('rolldlg').hidden=true;$('b-newroll').hidden=false};
$('b-rollok').onclick=async()=>{const b={};['film_brand','film_stock','film_iso','film_notes'].forEach(k=>b[k]=$('r-'+k).value);
 const r=await api('/api/newroll',b);if(r.busy){$('rollfilm').textContent='Wait until the scan finishes, then try again.';return}$('rolldlg').hidden=true;$('b-newroll').hidden=false};
$('b-scan').onclick=()=>api('/api/scan',{frames:selList()});$('b-stop').onclick=()=>api('/api/stop');
let anchor=null;
function toggle(i,ev){ev=ev||{};
 if(ev.shiftKey&&anchor!==null){const [a,b]=[Math.min(anchor,i),Math.max(anchor,i)];if(!(ev.metaKey||ev.ctrlKey))sel.clear();for(let k=a;k<=b;k++)sel.add(k)}
 else if(ev.metaKey||ev.ctrlKey){sel.has(i)?sel.delete(i):sel.add(i);anchor=i}
 else{const only=sel.size===1&&sel.has(i);sel.clear();if(!only)sel.add(i);anchor=i}
 render(true)}
// focus point: stored in the scanner's orientation, shown in each frame's orientation
function toScreen(p,k){let [x,y]=p;for(let n=0;n<k;n++)[x,y]=[y,1-x];return [x,y]}
function toScanner(p,k){let [x,y]=p;for(let n=0;n<k;n++)[x,y]=[1-y,x];return [x,y]}
$('b-rl').onclick=()=>api('/api/rotate',{frames:selList().length?selList():S.frames,quarter:1});
$('b-rr').onclick=()=>api('/api/rotate',{frames:selList().length?selList():S.frames,quarter:3});
function fillSelect(id,vals,cur,lab){const e=$(id);const want=vals.map(String).join();if(e.dataset.v!==want){e.innerHTML=vals.map(v=>`<option value="${v}">${lab?lab(v):v}</option>`).join('');e.dataset.v=want}e.value=String(cur)}
function render(force){
 if(!S)return; const st=S.settings, c=S.caps, busy=!!S.job;
 $('status').title=c&&c.revision?'Firmware '+c.revision:'';$('status').textContent=(S.demo?'DEMO · ':'')+(c?(c.product+' · '+c.optical_dpi+' dpi'+(c.holder?' · '+c.holder:'')+(S.film_in===false?' · no film':'')):'not connected')+(busy?' · '+S.job+'…':'');
 keys.forEach(k=>{const e=$('s-'+k);if(document.activeElement===e)return;if(e.type=='checkbox')e.checked=!!st[k];else if(!['dpi','preview_dpi','samples','format'].includes(k))e.value=st[k]??''});
 const opt=c?c.optical_dpi:2900, lo=c&&c.x_dpi_range?c.x_dpi_range[0]:73;
 const dpis=[];for(let n=1;n<=40;n++){const d=Math.round(opt/n);if(d>=lo)dpis.push(d)}
 fillSelect('s-dpi',dpis,st.dpi);fillSelect('s-preview_dpi',dpis.filter(d=>d<=1000),st.preview_dpi);
 const ms=c?c.max_samples:16;fillSelect('s-samples',[1,2,4,8,16].filter(n=>n<=ms),st.samples);
 const fmts=c&&c.film_formats?c.film_formats:[];$('fmtrow').hidden=!fmts.length;fillSelect('s-format',[''].concat(fmts),st.format,v=>v||'From holder');
 for(const id of ['b-load','b-eject','b-thumb','b-preview','b-cal','b-af','b-ae','b-unlock','b-scan','b-connect','b-rl','b-rr','b-newroll','b-rollok'])$(id).disabled=busy;$('b-stop').disabled=!busy;
 if(c&&!c.thumbnail){$('s-preview_mode').value='scan';$('s-preview_mode').disabled=true}else $('s-preview_mode').disabled=false;
 $('negrow').hidden=!!st.archive;
 const nf=S.frames.length, ns=sel.size;
 $('b-scan').textContent=ns?`Scan ${ns} selected frame${ns>1?'s':''}`:(nf?`Scan all ${nf} frame${nf>1?'s':''}`:'Scan all frames');
 $('b-preview').textContent=ns?`Preview ${ns} selected`:'Preview all';
 $('rollname').textContent=st.roll||'';$('rollfilm').textContent=(S.film_text?('Film: '+S.film_text):'No film details')+' · saving in '+(S.roll_path||'')+(st.archive?' (RAW/ and Positive/)':'');
 $('nameex').textContent='Sample: '+(st.archive?'RAW/ and Positive/':'')+S.sample_name;
 const fl=S.focus_lock, el=S.exposure_lock, af=!c||c.autofocus;
 $('b-af').hidden=!af;$('posrow').hidden=st.focus_mode!=='manual';
 if(c&&c.focus_range){$('s-focus_position').min=c.focus_range[0];$('s-focus_position').max=c.focus_range[1]}
 if(!picking)$('fpoint').textContent=st.focus_point?st.focus_point.map(v=>v.toFixed(2)).join(', '):'centre';$('b-fpoint').textContent=st.focus_point?'Clear':(picking?'Cancel':'Pick');
 $('calinfo').textContent=[fl?`Focus: ${fl.result} at lens position ${fl.position}${fl.frame!=null?' (frame '+(fl.frame+1)+')':''}.`:(st.focus_mode=='frame'?'Focus: each frame is focused on its first preview or scan.':'Focus: not set yet.'),
  el?`Exposure: ${Object.entries(el.exposures).map(([k,v])=>k[0].toUpperCase()+' '+v).join(', ')}${el.infrared?' (with IR)':''}${el.frame!=null?' from frame '+(el.frame+1):''}.`:'Exposure: not set yet.'].join(' ');
 if(force||S.version!==lastV){
  $('thumb').hidden=!S.thumb;$('nothumb').hidden=S.thumb;
  if(S.thumb)$('thumb').src='/thumb.png?'+S.version;
  const inner=document.querySelector('#thumbwrap .inner');inner.querySelectorAll('.box').forEach(b=>b.remove());
  for(const b of S.boxes){const d=document.createElement('div');d.className='box'+(sel.has(b.i)?' sel':'');Object.assign(d.style,{left:b.x*100+'%',top:b.y*100+'%',width:b.w*100+'%',height:b.h*100+'%'});d.innerHTML='<span>'+(b.i+1)+'</span>';d.onclick=ev=>toggle(b.i,ev);inner.appendChild(d)}
  const fp=st.focus_point;
  $('grid').innerHTML=S.frames.map(i=>`<div class="frame${sel.has(i)?' sel':''}" data-i="${i}">${S.previews.includes(i)?`<div class="imgbox"><div class="imgw"><img src="/preview/${i}.png?${S.version}">${fp?(q=>`<div class="mark" style="left:${q[0]*100}%;top:${q[1]*100}%"></div>`)(toScreen(fp,S.turns[i]||0)):''}</div></div>`:'<div class="hint" style="padding:30px 6px">No preview yet</div>'}<div class="cap">Frame ${i+1}${S.preview_info[i]?' · '+S.preview_info[i]:''}</div></div>`).join('')||'<p class="hint">Load film and press Thumbnail only or Preview.</p>';
  $('grid').querySelectorAll('.frame').forEach(e=>e.onclick=ev=>{
   if(picking&&ev.target.tagName=='IMG'){const r=ev.target.getBoundingClientRect();picking=false;document.body.classList.remove('picking');
    const i=+e.dataset.i;api('/api/settings',{focus_point:toScanner([(ev.clientX-r.left)/r.width,(ev.clientY-r.top)/r.height],S.turns[i]||0)});return}
   toggle(+e.dataset.i,ev)});
  $('log').textContent=S.log.join('\n');$('log').scrollTop=1e9;lastV=S.version}
 const p=S.progress;$('prog').hidden=!p;$('progtxt').hidden=!p;if(p){$('prog').max=p.total||1;$('prog').value=p.done;$('progtxt').textContent=`${S.job}: ${p.phase} ${p.total?Math.round(100*p.done/p.total):0}%`}
}
async function poll(){try{S=await (await fetch('/api/state')).json();render(false)}catch(e){$('status').textContent='app stopped'}setTimeout(poll,500)}
poll();
</script></body></html>"""


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self._send(PAGE, "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(app.state())
            if path == "/thumb.png" and app.thumb_png:
                return self._send(app.thumb_png, "image/png")
            if path.startswith("/preview/") and path.endswith(".png"):
                try:
                    png = app.previews.get(int(path[9:-4]))
                except ValueError:
                    png = None
                if png:
                    return self._send(png, "image/png")
            self._send({"error": "not found"}, code=404)

        def do_POST(self):
            # Only accept requests from this page, not from other web sites
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and urlparse(origin).netloc != host:
                return self._send({"error": "forbidden"}, code=403)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            path = urlparse(self.path).path
            frames = [int(i) for i in body.get("frames", [])]
            jobs = {
                "/api/connect": ("connect", app.connect),
                "/api/load": ("load", app.load),
                "/api/eject": ("eject", app.eject),
                "/api/thumbnail": ("thumbnail", app.thumbnail),
                "/api/preview": ("preview", lambda: app.preview(frames)),
                "/api/calibrate": ("calibrate", lambda: app.calibrate(body.get("frame"))),
                "/api/autofocus": ("autofocus", lambda: app.autofocus(body.get("frame"))),
                "/api/autoexpose": ("auto exposure", lambda: app.auto_expose(body.get("frame"))),
                "/api/scan": ("scan", lambda: app.scan(frames)),
            }
            if path in jobs:
                name, fn = jobs[path]
                ok = app.run_job(name, fn)
                return self._send({"ok": ok, "busy": not ok})
            if path == "/api/rotate":
                app.rotate(frames, int(body.get("quarter", 1)))
                with app.lock:
                    app.version += 1
                return self._send({"ok": True})
            if path == "/api/stop":
                app.cancel = True
                app.say("stopping after the current step…")
                return self._send({"ok": True})
            if path == "/api/newroll":
                if app.job:
                    return self._send({"ok": False, "busy": True})
                app.new_roll(body)
                return self._send({"ok": True})
            if path == "/api/unlock":
                app.focus_lock = app.exposure_lock = None
                app.settings.update(focus_mode="frame", exposure_mode="auto")
                app.frame_focus = {}
                app.say("focus and exposure reset: the next pass focuses again, and each frame is exposed on its own")
                return self._send({"ok": True})
            if path == "/api/settings":
                for k, v in body.items():
                    if k in app.settings:
                        old = app.settings[k]
                        if isinstance(old, bool):
                            v = bool(v)
                        elif isinstance(old, int):
                            v = int(v or 0)
                        app.settings[k] = v
                app.save_settings()
                if "focus_point" in body:
                    app.focus_lock = None  # like Nikon Scan: a new focus point means focus again
                    app.frame_focus = {}
                if ("rotate180" in body or "invert_preview" in body or "tone" in body) and not app.job:
                    app.redraw()
                with app.lock:
                    app.version += 1
                return self._send({"ok": True})
            self._send({"error": "not found"}, code=404)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--demo", action="store_true", help="use a pretend scanner")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not args.demo:
        if sys.version_info < (3, 13):
            sys.exit("nkscan needs Python 3.13 or newer. Turn the environment on first:  source .venv/bin/activate")
        try:
            import nkscan  # noqa: F401
        except ImportError:
            sys.exit("nkscan is missing. Turn the environment on (source .venv/bin/activate), "
                     "or install it with:  pip install nkscan numpy")
    app = App(demo=args.demo)
    if not args.demo:
        app.load_settings()
    app.roll_dir()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    except OSError:
        sys.exit(f"Port {args.port} is busy: Coolscan is probably still running from before.\n"
                 f"Stop it with:  pkill -9 -f coolscan.py   (or start with --port {args.port + 1})")
    url = f"http://127.0.0.1:{args.port}"
    print(f"Coolscan is running at {url}  (Ctrl-C to quit)")
    app.run_job("connect", app.connect)
    threading.Thread(target=app.watch, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.session is not None:
            try:
                app.session.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
