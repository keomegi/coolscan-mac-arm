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
import subprocess
import sys
import threading
import time
import traceback
import types
import urllib.request
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is missing. Install it with:  pip install nkscan numpy")

FILMS = ["negative", "positive", "kodachrome", "mono"]
PREVIEW_MAX_SIDE = 1400
WATCH_SECONDS = 2  # how often the idle scanner is asked whether film is in
HEADROOM = 0.02  # fraction of the range left free below black and above white in positives
DESCRIPTION_FILE = "Description.txt"   # the film description, kept in each roll's folder
ASIDE_FOLDER = "Earlier rolls"          # where older roll folders go when their number is taken over
_NAME_END = re.compile(r"(?:_IR|_negative)?\.tiff?$", re.I)   # how a saved picture's name ends
_NAME_ROLL = re.compile(r"(?:_\d{8})?_R\d{2,3}$")              # the roll number before that, if any
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


def display_image(planes, film, invert=True, tone="nikon", restore=0):
    """Turn scanner planes (dict name -> uint16) into an 8-bit picture for the browser."""
    names = [n for n in ("red", "green", "blue") if n in planes] or [next(iter(planes))]
    stack = [planes[n] for n in names]
    rows, cols = stack[0].shape
    step = max(1, math.ceil(max(rows, cols) / PREVIEW_MAX_SIDE))
    out = []
    if film in ("negative", "mono") and invert:
        # the same conversion as "save as positive", so the preview shows what is saved
        for v in restore_colour(invert_negative([p[::step, ::step] for p in stack], tone), restore):
            out.append((v * 255 + 0.5).astype(np.uint8))
        return np.dstack(out) if len(out) == 3 else out[0]
    for p in stack:
        v = p[::step, ::step].astype(np.float32)
        lo, hi = np.percentile(v, (0.1, 99.9))
        v = (v - lo) / max(hi - lo, 1e-6)
        out.append(np.clip(v, 0, 1) ** np.float32(1 / 2.2))
    out = [(np.clip(v, 0, 1) * 255 + 0.5).astype(np.uint8) for v in restore_colour(out, restore)]
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


# ---------------------------------------------------------------- dust removal from the infrared
#
# The same method nkscan uses inside a pass (its dust.rs, MIT/Apache-2.0, which
# follows a6o's openICE), written for numpy so it can run on a scan that is
# already in memory. That is what lets Archive mode keep the untouched RAW and
# make the cleaned picture from one pass.
#
# Everything is done in log space, D(v) = 4096 * log2(v + 1). Dust blocks
# infrared and film dyes hardly do, so a pixel whose infrared is below clear
# film is rebuilt from its clean neighbours at three sizes, and the infrared
# shortfall is added back as light.

_DUST_M = 65535.0
_DUST_TAU = 8847.23                 # infrared above this is clear film
_DUST_LEAK = {True: 0.17, False: 0.14}   # red->infrared leakage when it cannot be measured (negative, positive)
_DUST_ALPHA = (0.015, 0.015, 0.025)      # grain put back into rebuilt pixels, per channel
# the LS-40 / LS-50 constants (nkscan's "kind 9")
_DUST_THETA, _DUST_RAMP, _DUST_CONTRAST_DPI = 1.0, -960.52, 2500
_DUST_A = (((2.210, 2.090), (2.170, 2.080), (2.040, 1.960)),
           ((2.230, 2.130), (2.140, 2.050), (1.930, 1.840)),
           ((2.130, 2.040), (2.080, 2.020), (1.970, 1.890)))
_DUST_LUT = None


def _dust_lut():
    global _DUST_LUT
    if _DUST_LUT is None:
        _DUST_LUT = (np.log2(np.arange(65536, dtype=np.float64) + 1.0) * (_DUST_M / 16)).astype(np.float32)
    return _DUST_LUT


def _dust_d(fraction):
    """D of the 16-bit level at `fraction` of full scale."""
    return float(_dust_lut()[int(_DUST_M * fraction)])


def dust_calibrate(red, ir, negative=True):
    """Measure how much of the red image leaks into the infrared and what clear film reads.

    red, ir: 2-D uint16 (a thinned-out copy of the frame is enough). Returns (c, ir_ref),
    or None when there is no clear film in view.
    """
    lut = _dust_lut()
    clear = ir > _DUST_TAU
    if not clear.any():
        return None
    d_r, d_ir = lut[red], lut[ir]
    w = ir[clear].astype(np.float64) ** 2
    r_ref = float((w * d_r[clear]).sum() / w.sum())
    ir_raw = float((w * d_ir[clear]).sum() / w.sum())
    rows8, cols8 = red.shape[0] // 8, red.shape[1] // 8
    num = den = 0.0
    if rows8 and cols8:
        def tiles(a):
            return a[:rows8 * 8, :cols8 * 8].reshape(rows8, 8, cols8, 8)
        whole = tiles(clear).all(axis=(1, 3))   # tiles that are clear film all through: what varies is dye
        tr, tir = tiles(d_r).astype(np.float64), tiles(d_ir).astype(np.float64)
        tile_r, tile_ir = tr.sum(axis=(1, 3)), tir.sum(axis=(1, 3))
        tile_raw = tiles(ir).astype(np.float64).sum(axis=(1, 3))
        for dy, dx in ((0, 0), (0, 4), (4, 0), (4, 4)):
            delta_r = tr[:, dy:dy + 4, :, dx:dx + 4].sum(axis=(1, 3)) / 16 - tile_r / 64
            delta_ir = tir[:, dy:dy + 4, :, dx:dx + 4].sum(axis=(1, 3)) / 16 - tile_ir / 64
            with np.errstate(divide="ignore", invalid="ignore"):
                slope = delta_ir / delta_r
            ok = whole & np.isfinite(slope) & (np.abs(slope) <= 0.2)
            weight = delta_r * delta_r * tile_raw * tile_raw
            num += float((slope[ok] * weight[ok]).sum())
            den += float(weight[ok].sum())
    c = num / den if den > 0 else _DUST_LEAK[bool(negative)]
    return c, (ir_raw - c * r_ref) / (1 - c)


def _dust_sums(a):
    """The three neighbourhood sums of nkscan's pyramid: 9x9 octagon, 5x5 octagon, 3x3 tent.

    Outside the array counts as zero.
    """
    rows, cols = a.shape
    p = np.zeros((rows + 8, cols + 8), np.float32)
    p[4:-4, 4:-4] = a

    def across(k):  # columns shifted by k, all padded rows kept
        return p[:, 4 + k:4 + k + cols]

    s3 = across(-1) + across(0) + across(1)
    tent_h = s3 + across(0)
    s5 = s3 + across(-2) + across(2)
    s7 = s5 + across(-3) + across(3)
    s9 = s7 + across(-4) + across(4)

    def down(s, k):
        return s[4 + k:4 + k + rows]

    box9 = down(s5, -4) + down(s5, 4) + down(s7, -3) + down(s7, 3)
    for k in (-2, -1, 0, 1, 2):
        box9 = box9 + down(s9, k)
    box5 = down(s3, -2) + down(s3, 2) + down(s5, -1) + down(s5, 0) + down(s5, 1)
    tent = down(tent_h, -1) + 2 * down(tent_h, 0) + down(tent_h, 1)
    return box9, box5, tent


def _dust_uniform(index, channel):
    """A fixed pseudo-random number in [0, 1) per pixel and channel (the same hash as nkscan)."""
    x = (index.astype(np.uint64) << np.uint64(2)) | np.uint64(channel)
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return ((x ^ (x >> np.uint64(31))) & np.uint64(0xFFFFFF)).astype(np.float32) / np.float32(1 << 24)


def _dust_band(planes, ir, out, y0, y1, c, ir_ref, dpi):
    """Clean rows y0..y1 of the frame into `out`; returns how many pixels were rebuilt."""
    rows, cols = ir.shape
    lut = _dust_lut()
    halo = 6
    r0, r1 = max(0, y0 - halo), min(rows, y1 + halo)
    own = slice(y0 - r0, y1 - r0)
    dens = [lut[p[r0:r1]] for p in planes]
    g = (lut[ir[r0:r1]] - c * dens[0]) / (1 - c) - _DUST_THETA
    gate = g
    if dpi > 550:  # the weight looks at the darkest of three pixels side by side
        gate = np.minimum(g, np.minimum(np.concatenate([g[:, :1], g[:, :-1]], axis=1),
                                        np.concatenate([g[:, 1:], g[:, -1:]], axis=1)))
    ramp_bias = ir_ref + _dust_d(0.98) - _DUST_M
    w = np.clip(1.0 + (ramp_bias - gate) / _DUST_RAMP, 0.02, 1.0).astype(np.float32)

    # which pixels to rebuild: infrared below clear film, and not inside a wide dark area
    # (the film holder, the frame edge), where there is nothing clean to rebuild from
    dark = g < _dust_d(0.065)
    n = r1 - r0
    padded = np.ones((n, cols + 8), bool)
    padded[:, 4:-4] = dark
    row_dark = np.ones((n, cols), bool)
    for k in range(9):
        row_dark &= padded[:, k:k + cols]
    yy = np.arange(y0, y1)
    above = row_dark[np.maximum(yy - 4, 0) - r0]
    below = row_dark[np.minimum(yy + 4, rows - 1) - r0]
    sides = np.ones((y1 - y0, cols), bool)
    for k in range(-4, 5):
        r = yy + k
        ok = (r >= 0) & (r < rows)
        sides[ok] &= dark[r[ok] - r0]
    xx = np.arange(cols)
    left, right = sides[:, np.maximum(xx - 4, 0)], sides[:, np.minimum(xx + 4, cols - 1)]
    mask = ~(above | below | left | right) & (w[own] < 1.0)
    if not mask.any():
        return 0

    # the pyramid: weighted means of the gate and of each colour over three neighbourhoods
    sw = _dust_sums(w)
    conf = [sw[0] / 69.0, sw[1] / 21.0, sw[2] / 16.0, w]
    inv = [np.where(s > 0, 1.0 / np.maximum(s, 1e-30), 0.0).astype(np.float32) for s in sw]
    sg = _dust_sums(w * g)
    pyr = [sg[k] * inv[k] for k in range(3)] + [g]
    lo, hi = [None], [None]
    cross = dpi > _DUST_CONTRAST_DPI
    for level in (1, 2, 3):
        d = pyr[level] - pyr[level - 1]
        if cross:  # the infrared contrast over the pixel and its four neighbours
            up = np.concatenate([d[:1], d[:-1]], axis=0)
            dn = np.concatenate([d[1:], d[-1:]], axis=0)
            lf = np.concatenate([d[:, :1], d[:, :-1]], axis=1)
            rt = np.concatenate([d[:, 1:], d[:, -1:]], axis=1)
            lo.append(np.minimum.reduce([d, up, dn, lf, rt])[own])
            hi.append(np.maximum.reduce([d, up, dn, lf, rt])[own])
        else:
            lo.append(d[own])
            hi.append(d[own])
    confidence = [None, np.minimum(2 * conf[1][own], 1.0), conf[2][own], conf[3][own] ** 2]
    base_gain = ir_ref - pyr[0][own]
    index = (yy[:, None] * cols + xx[None, :])[mask]
    eta_lo, eta_hi = _dust_d(0.01), _dust_d(0.99)
    rebuilt, keep = [], np.ones(index.size, bool)
    for ch in range(3):
        sd = _dust_sums(w * dens[ch])
        levels = [sd[k] * inv[k] for k in range(3)] + [dens[ch]]
        a = levels[0][own] + base_gain
        for level in (1, 2, 3):
            detail = (levels[level][own] - levels[level - 1][own]) * 1.25
            a_hi, a_lo = _DUST_A[ch][level - 1]
            swap = hi[level] < 0
            lo_t = np.where(swap, a_hi, a_lo) * lo[level]
            hi_t = np.where(swap, a_lo, a_hi) * hi[level]
            a = a + (np.minimum(detail - lo_t, 0) + np.maximum(detail - hi_t, 0)) * confidence[level]
        x = a[mask]
        d = (4.0 / (eta_hi - eta_lo) ** 2 * (x - eta_lo) * (eta_hi - x)
             * (_dust_uniform(index, ch) - 0.5) * _DUST_ALPHA[ch] * x)
        inside = (x > eta_lo) & (x < eta_hi) & (x + d > eta_lo) & (x + d < eta_hi)
        x = x + np.where(inside, d, 0)
        keep &= x > 0
        x = np.maximum(x, dens[ch][own][mask])  # a rebuilt pixel never comes out darker than it scanned
        v = np.clip(np.floor(np.exp2(x.astype(np.float64) * (16 / _DUST_M)) - 1.0 + 0.5), 0, 65535).astype(np.uint16)
        rebuilt.append(np.maximum(v, planes[ch][y0:y1][mask]))
    for ch in range(3):
        target = out[ch][y0:y1]
        values = target[mask]
        values[keep] = rebuilt[ch][keep]
        target[mask] = values
    return int(keep.sum())


def dust_clean(planes, ir, dpi, negative=True, band=128, progress=None):
    """Dust and scratch removal from the infrared. Returns (cleaned planes, pixels rebuilt).

    planes: [red, green, blue] uint16 as scanned (not rotated, not inverted); ir: the infrared
    of the same pass. Returns None when the frame has no clear film to measure against.
    """
    from concurrent.futures import ThreadPoolExecutor
    rows, cols = ir.shape
    step = max(1, math.isqrt((rows * cols) // 2_500_000))

    def thin(a):
        return a[:rows - rows % step:step, :cols - cols % step:step]

    cal = dust_calibrate(thin(planes[0]), thin(ir), negative) or dust_calibrate(planes[0], ir, negative)
    if cal is None:
        return None
    c, ir_ref = cal
    out = [p.copy() for p in planes]
    starts = list(range(0, rows, band))
    count = done = 0
    # numpy lets other threads run while it works, so a few bands at a time is quicker
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as pool:
        for n in pool.map(lambda y0: _dust_band(planes, ir, out, y0, min(rows, y0 + band), c, ir_ref, dpi), starts):
            count += n
            done += 1
            if progress:
                progress("dust removal", 0, done, len(starts))
    return out, count


# ---------------------------------------------------------------- grain reduction and colour restoration
#
# Our own stand-ins for Nikon Scan's GEM (grain) and ROC (faded colours). Both
# work on the positive picture as float32 planes, values 0..1, before it is
# converted to the output colour space, and both are off unless asked for.

GRAIN_LEVELS = {1: 0.65, 2: 1.0, 3: 1.45, 4: 2.0}   # how far above the measured grain a detail must stand to be kept whole
RESTORE_LEVELS = {1: 0.4, 2: 0.7, 3: 1.0}         # low, medium, high


def _smooth(a, scale):
    """Blur with the (1 4 6 4 1)/16 kernel spread out by 2**scale, across and down."""
    step = 1 << scale
    for axis in (0, 1):
        n = a.shape[axis]
        pad = [(0, 0), (0, 0)]
        pad[axis] = (2 * step, 2 * step)
        p = np.pad(a, pad, mode="reflect" if n > 2 * step else "edge")

        def take(k):
            sl = [slice(None), slice(None)]
            sl[axis] = slice((2 + k) * step, (2 + k) * step + n)
            return p[tuple(sl)]

        a = (take(-2) + take(2)) * np.float32(1 / 16) + (take(-1) + take(1)) * np.float32(4 / 16) \
            + take(0) * np.float32(6 / 16)
    return a


def _box3(a):
    """The mean over each pixel's 3x3 surroundings."""
    p = np.pad(a, 1, mode="edge")
    rows = p[:-2] + p[1:-1] + p[2:]
    return (rows[:, :-2] + rows[:, 1:-1] + rows[:, 2:]) * np.float32(1 / 9)


def _grain_size(d):
    """How strong the grain is in one layer of detail: its spread in the smoothest parts of the picture."""
    tile = 32
    rows, cols = (d.shape[0] // tile) * tile, (d.shape[1] // tile) * tile
    if rows < 4 * tile or cols < 4 * tile:
        return float(np.median(np.abs(d))) / 0.6745
    t = np.abs(d[:rows, :cols]).reshape(rows // tile, tile, cols // tile, tile)
    # every 4th tile each way is plenty; the calmest fifth of them are grain with little picture detail
    spread = np.median(t[::4, :, ::4, :].transpose(0, 2, 1, 3).reshape(-1, tile * tile), axis=1)
    return float(np.percentile(spread, 20)) / 0.6745


def reduce_grain(planes, strength, progress=None):
    """Smooth film grain and keep edges and texture. planes: float32 0..1; returns new planes.

    The picture is split into brightness and two colour differences, each into
    four layers of detail from fine to coarse. In every layer the grain is
    measured in the calm parts of the picture; detail no bigger than the grain
    is faded out, detail that stands above it is kept.
    """
    k = GRAIN_LEVELS.get(int(strength or 0))
    if not k:
        return planes
    if len(planes) == 3:
        r, g, b = planes
        parts = [((r + g + b) * np.float32(1 / 3), k), (r - b, 1.6 * k), ((r + b) * np.float32(0.5) - g, 1.6 * k)]
    else:
        parts = [(planes[0], k)]
    done = []
    steps = len(parts) * 4
    for n, (a, kk) in enumerate(parts):
        smooth, kept = a, None
        for scale in range(4):
            nxt = _smooth(smooth, scale)
            d = smooth - nxt
            t = np.float32(kk * _grain_size(d))
            # Detail that stands above the grain passes whole, detail the size of the grain fades
            # away. Judged over each pixel's 3x3 surroundings, so an edge (strong all along)
            # stays and a lone bright grain does not.
            d *= np.maximum(np.float32(0), 1 - (t * t) / (_box3(d * d) + np.float32(1e-12)))
            kept = d if kept is None else kept + d
            smooth = nxt
            if progress:
                progress("grain reduction", 0, n * 4 + scale + 1, steps)
        done.append(smooth + kept)
    if len(done) == 1:
        return [np.clip(done[0], 0, 1)]
    y, c1, c2 = done
    third = c2 * np.float32(1 / 3)
    return [np.clip(y + third + c1 * np.float32(0.5), 0, 1), np.clip(y - 2 * third, 0, 1),
            np.clip(y + third - c1 * np.float32(0.5), 0, 1)]


def restore_colour(planes, strength):
    """Bring back faded colours. planes: float32 0..1 (three channels); returns new planes.

    Film dyes fade at different rates, which leaves a colour cast, flat contrast
    and dull colours. Each channel gets its own black point, white point and
    midtone so the three line up again, then the colours are strengthened.
    A picture that really is one colour all over (a sunset) loses some of it:
    that is the price of any automatic restoration, hence the strength setting.
    """
    amount = RESTORE_LEVELS.get(int(strength or 0))
    if not amount or len(planes) != 3:
        return planes
    rows, cols = planes[0].shape
    inner = (slice(rows // 20, rows - rows // 20), slice(cols // 20, cols - cols // 20))
    step = max(1, int(math.sqrt(planes[0][inner].size / 400_000)))
    stats = [np.percentile(p[inner][::step, ::step], (0.5, 50, 99.5)) for p in planes]
    lo_t = min(0.02, min(s[0] for s in stats))
    hi_t = max(0.98, max(s[2] for s in stats))
    mid_t = float(np.mean([(s[1] - s[0]) / max(s[2] - s[0], 1e-6) for s in stats]))  # where the midtones sit, on average
    mid_t = min(max(mid_t, 0.05), 0.95)
    out = []
    for p, (lo, mid, hi) in zip(planes, stats):
        span = max(hi - lo, 1e-6)
        m = min(max((mid - lo) / span, 0.02), 0.98)
        gamma = min(max(math.log(mid_t) / math.log(m), 0.5), 2.0)
        v = np.clip((p - np.float32(lo)) / np.float32(span), 0, 1)
        np.power(v, np.float32(gamma), out=v)
        v = v * np.float32(hi_t - lo_t) + np.float32(lo_t)
        out.append(p + np.float32(amount) * (v - p))
    y = (out[0] + out[1] + out[2]) * np.float32(1 / 3)
    boost = np.float32(1 + 0.35 * amount)
    return [np.clip(y + boost * (v - y), 0, 1) for v in out]


# ---------------------------------------------------------------- multi-sample by averaging passes
#
# The LS-4000 and LS-5000 can read each line several times in one pass. A scanner
# that reads it once (the LS-40) gets the same effect from whole passes taken at
# one focus and exposure and averaged, as long as the passes land on each other.

def pass_shift(a, b, tile=256):
    """How far pass b sits from pass a, in pixels, at the worst of nine spots.

    None when the picture has too little detail to tell. a, b: uint16 planes of one size.
    """
    rows, cols = a.shape
    if rows < 2 * tile or cols < 2 * tile:
        return None
    window = np.outer(np.hanning(tile), np.hanning(tile)).astype(np.float32)
    worst = None

    def prepared(plane, y, x):
        t = np.log(plane[y:y + tile, x:x + tile].astype(np.float32) + 1)
        t = t - _box3(_box3(_box3(t)))  # fine detail only: broad tones say nothing about position
        return (t - t.mean()) * window

    def fraction(before, peak, after):  # where a parabola through three points tops out
        bend = before - 2 * peak + after
        return 0.0 if bend == 0 else 0.5 * (before - after) / bend

    for fy in (0.25, 0.5, 0.75):
        for fx in (0.2, 0.5, 0.8):
            y, x = int(fy * rows) - tile // 2, int(fx * cols) - tile // 2
            ta, tb = prepared(a, y, x), prepared(b, y, x)
            energy = math.sqrt(float((ta * ta).sum()) * float((tb * tb).sum()))
            if energy <= 0:
                continue
            cc = np.fft.irfft2(np.fft.rfft2(ta) * np.conj(np.fft.rfft2(tb)), s=ta.shape)
            py, px = np.unravel_index(int(np.argmax(cc)), cc.shape)
            if cc[py, px] / energy < 0.2:
                continue  # little in common here (a flat area): no reading
            dy = py + fraction(cc[py - 1, px], cc[py, px], cc[(py + 1) % tile, px])
            dx = px + fraction(cc[py, px - 1], cc[py, px], cc[py, (px + 1) % tile])
            dy, dx = (dy + tile / 2) % tile - tile / 2, (dx + tile / 2) % tile - tile / 2
            far = math.hypot(dy, dx)
            worst = far if worst is None else max(worst, far)
    return worst


# ---------------------------------------------------------------- telling the person a strip is done

def _osascript(lines, *args):
    """Run a few lines of AppleScript with arguments (so no text needs escaping)."""
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    run = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=30)
    if run.returncode != 0:
        lines = (run.stderr or "").strip().splitlines()
        raise RuntimeError(lines[-1][:200] if lines else f"osascript ended with code {run.returncode}")


def send_banner(title, text):
    """A notification banner on this Mac."""
    _osascript(["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"],
               title, text)


def send_imessage(recipient, text):
    """An iMessage from the Mac's Messages app, for example to the person's own phone."""
    script = ["on run argv", 'tell application "Messages"',
              "set theAccount to 1st account whose service type = iMessage",
              "send (item 2 of argv) to participant (item 1 of argv) of theAccount", "end tell", "end run"]
    try:
        _osascript(script, recipient, text)
    except RuntimeError:
        # the wording older versions of Messages understand
        script[2] = "set theAccount to 1st service whose service type = iMessage"
        script[3] = "send (item 2 of argv) to buddy (item 1 of argv) of theAccount"
        _osascript(script, recipient, text)


def send_ntfy(topic, title, text):
    """A push through ntfy (https://ntfy.sh, or the person's own server when a full address is given)."""
    topic = topic.strip()
    url = topic if topic.startswith(("https://", "http://")) else "https://ntfy.sh/" + quote(topic, safe="")
    req = urllib.request.Request(url, data=text.encode("utf-8"), headers={"Title": title}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as reply:
        reply.read()


# ---------------------------------------------------------------- a cleaning helper (Nikon ICE)
#
# If a program called ice_clean sits next to this file, it can be chosen for dust
# removal instead of the built-in method. The app hands it the scan as a 16-bit
# TIFF with four channels (red, green, blue, infrared; as scanned, not inverted,
# not rotated) and reads back a 16-bit RGB TIFF of the same size:
#
#   ice_clean INPUT.tif OUTPUT.tif --film negative --dpi 2900 --model "LS-40 ED"
#
# Exit code 0 means OUTPUT.tif is good. Anything else: the built-in method is used.

ICE_HELPER = Path(__file__).resolve().with_name("ice_clean")
ICE_TIMEOUT = 600  # seconds per frame


def read_tiff_rgb16(path):
    """Read an uncompressed 16-bit RGB TIFF as three planes (what a cleaning helper hands back)."""
    with open(path, "rb") as f:
        data = f.read()
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None or struct.unpack(order + "H", data[2:4])[0] != 42:
        raise ValueError("not a TIFF file")
    ifd = struct.unpack(order + "I", data[4:8])[0]
    tags = {}
    for k in range(struct.unpack(order + "H", data[ifd:ifd + 2])[0]):
        at = ifd + 2 + 12 * k
        tag, typ, n = struct.unpack(order + "HHI", data[at:at + 8])
        if typ not in (3, 4):  # only the whole-number tags are needed
            continue
        code, size = ("H", 2) if typ == 3 else ("I", 4)
        raw = data[at + 8:at + 12]
        if size * n > 4:
            off = struct.unpack(order + "I", raw)[0]
            raw = data[off:off + size * n]
        tags[tag] = struct.unpack(order + code * n, raw[:size * n])
    cols, rows = tags[256][0], tags[257][0]
    spp = tags.get(277, (1,))[0]
    if tags.get(259, (1,))[0] != 1 or set(tags.get(258, (1,))) != {16} or spp < 3:
        raise ValueError("expected an uncompressed 16-bit RGB TIFF")
    pixels = b"".join(data[o:o + c] for o, c in zip(tags[273], tags[279]))
    if len(pixels) < rows * cols * spp * 2:
        raise ValueError("the TIFF is cut short")
    a = np.frombuffer(pixels, order + "u2", rows * cols * spp)
    if tags.get(284, (1,))[0] == 2:  # one plane after the other
        a = a.reshape(spp, rows, cols)
        return [a[k].astype(np.uint16) for k in range(3)]
    a = a.reshape(rows, cols, spp)
    return [np.ascontiguousarray(a[..., k]).astype(np.uint16) for k in range(3)]


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
        self.batch_day = None  # the date in the file names of the batch being scanned
        self.last_taken = None  # the "date taken" given to the last frame
        self.version = 0
        self.settings = {
            "film": "negative", "format": "", "preview_mode": "scan", "preview_dpi": 290,
            "dpi": 2900, "samples": 1, "ir": True, "clean": True,
            "focus_mode": "frame", "focus_point": None, "focus_position": 0,
            "exposure_mode": "auto", "white_balance": "film", "rotate180": False,
            "invert_preview": True, "negatives": "positive", "outdir": str(Path.home() / "Pictures" / "Coolscan"),
            "basename": "Scan", "suffix": "", "digits": 3, "start": 0, "archive": False, "photo_date": "",
            "colour_space": "ProPhoto RGB", "tone": "flat", "defaults_version": 4,
            "stop_on_errors": False, "scan_log": True, "eject_after": False, "auto_thumbnail": True,
            "dust_method": "builtin", "grain": 0, "restore": 0,
            "notify_mac": False, "notify_imessage": "", "notify_ntfy": "",
            "roll": "", "roll_tag": "overall", "name_date": True, "roll_day": "", "roll_day_count": 1, "film_brand": "", "film_stock": "", "film_iso": "", "film_notes": "",
            "film_description": "",
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
            "ice_helper": self.ice_ready,
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
        if st["notify_imessage"].strip():
            ways.append((f"iMessage to {st['notify_imessage'].strip()}",
                         lambda: send_imessage(st["notify_imessage"].strip(), "Coolscan: " + text)))
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
        """The name without ".tif": prefix, scan date, picture number (and suffix), roll number,
        e.g. Scan_20261122_005_R001. A part that is left out brings no underscore."""
        st = self.settings
        digits = min(6, max(1, int(st["digits"] or 3)))
        date, roll = self._name_parts()
        return (self._prefix() + (date + "_" if date else "") + f"{n:0{digits}d}"
                + self._clean_name(st["suffix"]) + ("_" + roll if roll else ""))

    @staticmethod
    def _clean_name(text):
        return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", str(text or "").strip())

    def _prefix(self):
        """The optional prefix. When there is one it brings its own "_": Scan_001, and just 001 without."""
        prefix = self._clean_name(self.settings["basename"])
        return prefix + "_" if prefix and prefix[-1] not in "_- ." else prefix

    def _name_folders(self):
        roll = self.roll_dir()
        return [roll, roll / "RAW", roll / "Positive"]

    def _highest_number(self):
        """The highest frame number among the pictures already in this roll, whatever their prefix."""
        st = self.settings
        prefix, suffix = self._clean_name(st["basename"]), self._clean_name(st["suffix"])
        keep = min(6, max(3, int(st["digits"] or 3)))
        highest = 0
        for folder in self._name_folders():
            try:
                names = [e.name for e in os.scandir(folder) if e.is_file() and not e.name.startswith(".")]
            except OSError:
                continue
            for name in names:
                end = _NAME_END.search(name)
                if not end:
                    continue
                core = _NAME_ROLL.sub("", name[:end.start()])
                if suffix and core.endswith(suffix):
                    core = core[:-len(suffix)]
                rest = core[len(prefix):].lstrip("_- .") if prefix and core.startswith(prefix) else ""
                if rest.isdigit():
                    highest = max(highest, int(rest))
                    continue
                found = re.search(r"(\d+)$", core)
                if found:
                    text = found.group(1)
                    highest = max(highest, int(text if len(text) <= 6 else text[-keep:]))
        return highest

    def next_number(self):
        """The number the next scan gets: one past the highest in the roll, or the number typed in.
        Either way it moves on past any name that is already taken, so nothing is overwritten."""
        try:
            typed = int(self.settings["start"] or 0)
        except (TypeError, ValueError):
            typed = 0
        return self._next_number(self._name_folders(), typed if typed > 0 else self._highest_number() + 1)

    def _name_parts(self):
        """The scan date that goes before the picture number and the roll number that goes after it
        ("" for a part that is switched off)."""
        st = self.settings
        # the day of the scan; one batch keeps one date even if it runs past midnight
        date = (self.batch_day or time.strftime("%Y%m%d")) if st["name_date"] else ""
        mode = st["roll_tag"]
        roll = ""
        if mode == "overall":
            roll = f"R{self._roll_number(st['roll'] or '') or 1:03d}"
        elif mode in ("day", "date_day"):
            roll = f"R{int(st['roll_day_count'] or 1):02d}"
        return date, roll

    def _next_number(self, folders, n):
        """n, or the first number after it whose name is still free in every one of the folders."""
        taken = set()
        for f in folders:
            try:
                for e in os.scandir(f):
                    end = _NAME_END.search(e.name)
                    if end:
                        taken.add(e.name[:end.start()].lower())  # the Mac's disk does not tell A from a
            except OSError:
                pass
        while self._stem(folders, n).lower() in taken:
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
        st["film_description"] = self._tidy_text(details.get("film_description", ""))
        base = self._base()
        used = [self._roll_number(d.name) for d in base.glob("Roll *")] if base.is_dir() else []
        used.append(self._roll_number(st["roll"] or ""))
        name = f"Roll {max(used + [0]) + 1:03d}"
        film = self._film_text()
        if film:
            name += " - " + re.sub(r'[\\/:*?"<>|]', "-", film)[:80].strip()
        st["roll"] = name
        st["start"] = 0
        today = time.strftime("%Y%m%d")
        st["roll_day_count"] = st["roll_day_count"] + 1 if st["roll_day"] == today else 1
        st["roll_day"] = today
        self.save_settings()
        self.say(f"new roll: saving to {base / name}, numbering starts again")
        self._write_description()
        with self.lock:
            self.version += 1

    # -- film description: free text, kept as a text file in the roll's folder
    @staticmethod
    def _tidy_text(text):
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:20000]

    def _write_description(self):
        """Make Description.txt in the roll folder say what the app has. True if the file was written."""
        st = self.settings
        folder = self.roll_dir()
        path = folder / DESCRIPTION_FILE
        text = st["film_description"].strip()
        try:
            old = path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else None
            if old == text or (old is None and not text):
                return False
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n" if text else "", encoding="utf-8")
        except (OSError, ValueError) as e:
            self.say(f"could not write {path} ({getattr(e, 'strerror', None) or e})")
            return False
        return True

    def read_description(self):
        """At start: if Description.txt was edited by hand, the file is what counts."""
        st = self.settings
        path = self.roll_dir() / DESCRIPTION_FILE
        try:
            if path.is_file():
                st["film_description"] = self._tidy_text(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            pass

    def describe(self, text):
        """The description was edited in the app. Safe during a scan: it only writes the text file."""
        st = self.settings
        st["film_description"] = self._tidy_text(text)
        self.save_settings()
        if self._write_description():
            self.say(f"film description saved in {st['roll']}/{DESCRIPTION_FILE}")
        with self.lock:
            self.version += 1

    # -- changing the roll number
    def _other_rolls(self, from_number):
        """Other roll folders numbered from_number or higher, lowest first."""
        base, mine = self._base(), self.settings["roll"]
        if not base.is_dir():
            return []
        rolls = [d for d in base.glob("Roll *") if d.is_dir() and d.name != mine
                 and self._roll_number(d.name) >= from_number]
        return sorted(rolls, key=lambda d: (self._roll_number(d.name), d.name))

    def renumber_roll(self, number, move_aside=False):
        """Give the roll being scanned another number: its folder and the _R... part of its file names.

        Nothing is deleted or overwritten. Other rolls with that number or a higher one would
        get in the way of counting on from here, so they are listed and nothing is changed;
        only when the person then says so (move_aside) are they moved, whole and untouched,
        into "Earlier rolls". Returns what the page needs to show.
        """
        st = self.settings
        base = self._base()
        try:
            number = int(number)
        except (TypeError, ValueError):
            number = 0
        if not 1 <= number <= 999:
            return {"ok": False, "error": "Pick a roll number from 1 to 999."}
        old_name = st["roll"] or self.roll_dir().name
        old_number = self._roll_number(old_name)
        others = self._other_rolls(number)
        if number == old_number and not others:
            return {"ok": True, "same": True}
        if others and not move_aside:
            return {"ok": False, "aside": ASIDE_FOLDER, "in_the_way": [
                {"name": d.name, "files": sum(1 for p in d.rglob("*") if p.is_file())} for d in others]}
        new_name = re.sub(r"^Roll \d+", f"Roll {number:03d}", old_name) if old_number else f"Roll {number:03d}"
        folder = base / old_name
        # work out every change first, and stop before touching anything if one of them could not be made
        # R003 as a part of its own: at the end (20261122_005_R003.tif, ..._R003_IR.tif) or, in names
        # from one earlier version, before the picture number (20261122_R003_005.tif)
        tag = re.compile(rf"(?:(?<=_)|^)R{old_number:03d}(?=_\d+[^/]*\.tiff?$|(?:_[A-Za-z]+)?\.tiff?$)", re.I)
        renames = []
        if folder.is_dir() and number != old_number:
            for p in sorted(folder.rglob("*")):
                hits = list(tag.finditer(p.name)) if p.is_file() else []
                if hits:
                    last = hits[-1]
                    renames.append((p, p.with_name(p.name[:last.start()] + f"R{number:03d}" + p.name[last.end():])))
        targets = [t for _, t in renames]
        if len(set(targets)) != len(targets) or any(t.exists() for t in targets):
            return {"ok": False, "error": "Some files in this roll already have that roll number in their name. Nothing was changed."}
        moves = []
        aside = base / ASIDE_FOLDER
        taken = set()
        for d in others:
            to, k = aside / d.name, 2
            while to.exists() or to in taken:
                to, k = aside / f"{d.name} ({k})", k + 1
            taken.add(to)
            moves.append((d, to))
        done = []
        try:
            if moves:
                aside.mkdir(exist_ok=True)
            for a, b in moves + renames:
                os.rename(a, b)
                done.append((a, b))
            if folder.is_dir() and new_name != old_name:
                if (base / new_name).exists():
                    raise FileExistsError(17, "there is already a folder with that name", str(base / new_name))
                os.rename(folder, base / new_name)
        except OSError as e:
            for a, b in reversed(done):  # put everything back as it was
                try:
                    os.rename(b, a)
                except OSError:
                    pass
            return {"ok": False, "error": f"Could not rename ({e.strerror}). Nothing was changed."}
        st["roll"] = new_name
        self.save_settings()
        words = f"roll number changed: {old_name} is now {new_name}"
        if renames:
            words += f", {len(renames)} file(s) renamed to R{number:03d}"
        self.say(words)
        for d, to in moves:
            self.say(f"moved aside, untouched: {d.name} is now in {ASIDE_FOLDER}/{to.name}")
        log = base / new_name / "ScanLog.txt"
        if log.is_file():
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Roll number changed: {old_name} is now {new_name}."
                            f" Earlier lines use the old names.\n\n")
            except OSError:
                pass
        return {"ok": True, "roll": new_name, "renamed": len(renames), "moved": [d.name for d, _ in moves]}

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
        if not isinstance(saved.get("defaults_version"), int) or saved["defaults_version"] < 3:
            # the start number used to sit at 1 for good; now the roll counts on by itself
            if self.settings["start"] <= 1:
                self.settings["start"] = 0
        if self.settings["roll_tag"] == "date_day":
            # the date now has its own switch
            self.settings["roll_tag"], self.settings["name_date"] = "day", True
        self.settings["defaults_version"] = 4
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

    def _positive(self, planes, nikon_icc):
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
            pos = invert_negative(planes, self.settings["tone"])
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
        except Exception as e:
            ending = "aborted"
            words = f"Scanning stopped after {done} of {len(indices)} frames in %s. " + explain(e).split(" [")[0]
            self.notify(words % roll, words % roll_plain)
            raise
        finally:
            self.batch_day = None
            if done and int(st["start"] or 0) > 0:
                st["start"] = 0  # a typed number is used once; after that the roll counts on by itself
                self.save_settings()
            missed = f", {len(failed)} failed (frame {', '.join(map(str, failed))})" if failed else ""
            log(f"Batch scan {ending} {time.strftime('%H:%M:%S')}: {done} of {len(indices)} frame(s) saved{missed}")
            log("")
        self.say(f"scan finished: {done} of {len(indices)} frame(s) saved{missed}")
        words = f"Strip done: {done} of {len(indices)} frames saved in %s{missed}."
        self.notify(words % roll, words % roll_plain)
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

    def _dust_method(self):
        """How dust is removed: "builtin" (from the scan in memory), "ice" (the helper) or "pass"."""
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
        if passes > 1 and method == "pass":
            method = "builtin"  # the driver can only clean one pass; the average is cleaned here

        def take(**kw):
            if passes == 1:
                return s.scan_frame(d.frames[i], **kw, **base, **opts)
            return self._averaged(s, i, d.frames[i], passes, kw, base, opts)

        def as_scanned(r):  # the colour planes the way the scanner sent them
            return [r.colors[k] for k in ("red", "green", "blue") if k in r.colors] or list(r.colors.values())

        def turned(planes):  # the way the person turned this frame
            return [self._rot(p, i) for p in planes]

        srgb_tag = lambda prof: 1 if prof and b"sRGB" in prof[:600] else 65535
        if job["archive"]:
            # one pass: the untouched scan with its infrared, then the finished picture made from it
            self.say(f"frame {i + 1}: archive scan at {st['dpi']} dpi (raw, with infrared)"
                     + (f", {passes} passes" if passes > 1 else ""))
            r = take(infrared=True, clean=False)
            raw = as_scanned(r)
            planes4 = turned(raw) + ([self._rot(r.ir, i)] if r.ir is not None else [])
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
            finished = raw
            if method == "pass":
                finished = self.second_pass(s, i, d.frames[i], r, base, opts, as_scanned)
            elif method:
                finished = self.clean_planes(raw, r.ir, r.dpi, i)
            pos, pos_icc = self._positive(turned(finished), icc)
            write_tiff(pos_dir / f"{stem}.tif", pos, r.dpi, pos_icc, meta={**meta, "colorspace": srgb_tag(pos_icc)})
            saved.append(f"Positive/{stem}.tif")
            self.say(f"saved Positive/{stem}.tif")
            return
        # not archive: the driver removes dust inside the pass (the same method as the built-in one)
        in_pass = method in ("builtin", "pass") and passes == 1
        self.say(f"frame {i + 1}: scanning at {st['dpi']} dpi"
                 + (f", {passes} passes" if passes > 1 else f", {want} sample(s)" if want > 1 else "")
                 + (", cleaning dust" if in_pass else ""))
        r = take(infrared=infrared, clean=in_pass)
        planes = as_scanned(r)
        if method and not in_pass:
            planes = self.clean_planes(planes, r.ir, r.dpi, i)
        planes = turned(planes)
        note = "" if r.complete else " (warning: the pass came back short)"
        negative = self._film() in ("negative", "mono")
        if negative and st["negatives"] in ("negative", "both"):
            raw_name = f"{stem}.tif" if st["negatives"] == "negative" else f"{stem}_negative.tif"
            write_tiff(outdir / raw_name, planes, r.dpi, icc, meta=meta)  # with Nikon's profile
            saved.append(raw_name)
            self.say(f"saved {raw_name}{note}")
        if not negative or st["negatives"] in ("positive", "both"):
            pos, pos_icc = self._positive(planes, icc)
            write_tiff(outdir / f"{stem}.tif", pos, r.dpi, pos_icc, meta={**meta, "colorspace": srgb_tag(pos_icc)})
            saved.append(f"{stem}.tif")
            self.say(f"saved {stem}.tif ({st['colour_space'] if st['colour_space'] in COLOUR_SPACES else 'scanner RGB'}){note}")
        if r.ir is not None and st["ir"]:
            write_tiff(outdir / f"{stem}_IR.tif", [self._rot(r.ir, i)], r.dpi, meta=meta)
            saved.append(f"{stem}_IR.tif")
            self.say(f"saved {stem}_IR.tif")
        if r.cleaned is not None:
            self.say(f"dust removal rebuilt {r.cleaned} pixels")

    def _averaged(self, s, i, frame, passes, kw, base, opts):
        """Multi-sample for a scanner that reads each line once: whole passes, averaged.

        Every pass after the first uses the first one's exposure and lens position.
        A pass that does not land on the first within half a pixel is left out.
        """
        kw = dict(kw, clean=False)
        first = s.scan_frame(frame, **kw, **base, **opts)
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
            r = s.scan_frame(frame, **kw, **base, **again)
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

        The built-in method is nkscan's, run here on the scan in memory, so the
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
        r = s.scan_frame(frame, infrared=True, clean=True, **base, **again)
        if r.cleaned is not None:
            self.say(f"dust removal rebuilt {r.cleaned} pixels")
        return as_scanned(r)


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
textarea{display:block;width:100%;min-height:64px;padding:6px;margin:4px 0;resize:vertical;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;font:inherit}
#rollno{width:64px}.warn{color:var(--warn)}
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
<label>Roll number<span><input type="number" id="rollno" min="1" max="999"> <button id="b-rollno">Change</button></span></label>
<div id="rollnodlg" hidden><p class="hint warn" id="rollnomsg"></p>
<button class="primary" id="b-rollnoyes" hidden>Move them and renumber</button><button id="b-rollnono">Cancel</button></div>
<div>Film description</div>
<textarea id="rolldesc" placeholder="Anything about this film: camera, place, dates, people…"></textarea>
<p class="hint" id="rolldeschint">Saved as Description.txt in the roll folder, and updated whenever you change it.</p>
<button id="b-newroll">New roll…</button>
<div id="rolldlg" hidden style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px">
<p class="hint">Start a new folder for the next roll? Film details are optional; they name the folder and go into the files.</p>
<label>Brand<input type="text" id="r-film_brand" placeholder="e.g. Kodak"></label>
<label>Film<input type="text" id="r-film_stock" placeholder="e.g. Portra"></label>
<label>ISO<input type="text" id="r-film_iso" placeholder="e.g. 400"></label>
<label>Notes<input type="text" id="r-film_notes" placeholder="e.g. Summer 2005"></label>
<div>Film description</div>
<textarea id="r-film_description" placeholder="Optional. Saved as Description.txt in the new roll's folder."></textarea>
<button class="primary" id="b-rollok">Start new roll</button><button id="b-rollcancel">Cancel</button>
</div></section>
<section><h2>When a strip is done</h2>
<label>iMessage to<input type="text" id="s-notify_imessage" placeholder="your number or Apple ID"></label>
<label>Banner on this Mac<input type="checkbox" id="s-notify_mac"></label>
<label>ntfy topic<input type="text" id="s-notify_ntfy" placeholder="optional"></label>
<p class="hint">Sends a message when a batch finishes or stops on an error. The iMessage goes out through this Mac's Messages app; the first time, macOS asks you to allow it. ntfy is a free phone app, if you prefer that.</p>
<button id="b-notifytest">Send a test</button>
</section>
<section><h2>Scan</h2>
<label>Resolution (dpi)<select id="s-dpi"></select></label>
<label>Multi-sample<select id="s-samples"></select></label>
<p class="hint" id="mshint"></p>
<label>Also save the infrared picture (_IR.tif)<input type="checkbox" id="s-ir"></label>
<p class="hint">The infrared picture shows only the dust and scratches. Dust removal uses it either way; tick this only if you want it as an extra file next to each picture.</p>
<label>Dust &amp; scratch removal<input type="checkbox" id="s-clean"></label>
<label id="dustrow">Dust removal by<select id="s-dust_method"><option value="builtin">Built in (one pass)</option><option value="ice">Nikon ICE (helper)</option><option value="pass">Driver, second pass (slow)</option></select></label>
<label>Grain reduction<select id="s-grain"><option value="0">Off</option><option value="1">1 (light)</option><option value="2">2</option><option value="3">3</option><option value="4">4 (strong)</option></select></label>
<label>Restore faded colours<select id="s-restore"><option value="0">Off</option><option value="1">Low</option><option value="2">Medium</option><option value="3">High</option></select></label>
<label>Archive mode<input type="checkbox" id="s-archive"></label>
<p class="hint" id="archint">Archive mode saves two folders from one pass: RAW (untouched scan with infrared as a 4th channel) and Positive (finished picture, dust removed if ticked).</p>
<label id="negrow">Negatives saved as<select id="s-negatives"><option value="positive">Positive</option><option value="negative">Negative (raw)</option><option value="both">Both</option></select></label>
<label>Folder<input type="text" id="s-outdir"></label>
<label>Negative tone<select id="s-tone"><option value="flat">Flat (for editing)</option><option value="nikon">Nikon Scan look</option></select></label>
<label>Colour space<select id="s-colour_space"><option value="ProPhoto RGB">ProPhoto RGB</option><option value="Adobe RGB (1998)">Adobe RGB (1998)</option><option value="sRGB IEC61966-2.1">sRGB IEC61966-2.1</option><option value="Apple RGB">Apple RGB</option><option value="ColorMatch RGB">ColorMatch RGB</option><option value="Bruce RGB">Bruce RGB</option><option value="Wide Gamut RGB">Wide Gamut RGB</option><option value="scanner">Scanner RGB (Nikon profile)</option></select></label>
<label>File name prefix<input type="text" id="s-basename" placeholder="optional"></label>
<label>Suffix<input type="text" id="s-suffix"></label>
<label>Scan date in name<input type="checkbox" id="s-name_date"></label>
<label>Roll number in name<select id="s-roll_tag"><option value="overall">Overall roll (R017)</option><option value="day">Roll of the day (R02)</option><option value="none">None</option></select></label>
<label>Counter digits<input type="number" id="s-digits" min="1" max="6"></label>
<label>Next number<input type="number" id="s-start" min="1"></label>
<p class="hint">Counts on by itself from the pictures already in the roll folder. Type another number to carry on from there (empty it to count by itself again). A name that is taken is skipped, never overwritten.</p>
<label>Photo taken (year, or year-month-day)<input type="text" id="s-photo_date" placeholder="e.g. 2005 or 2005-06-14"></label>
<p class="hint">Galleries sort the scans by this date instead of today's. With only a year, the files get today's day and time in that year. The real scan date stays in the file's description. Leave empty to use today.</p>
<p class="hint" id="nameex"></p>
<p class="hint">Names are built as prefix, scan date, picture number, roll number. A part you leave out brings no "_" with it.</p>
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
const keys=['film','format','preview_mode','preview_dpi','invert_preview','rotate180','focus_mode','focus_position','exposure_mode','white_balance','dpi','samples','ir','clean','negatives','archive','outdir','basename','suffix','digits','start','photo_date','colour_space','roll_tag','name_date','tone','stop_on_errors','eject_after','scan_log','auto_thumbnail','dust_method','grain','restore','notify_mac','notify_imessage','notify_ntfy'];
let picking=false;
keys.forEach(k=>{const e=$('s-'+k);e.addEventListener('change',()=>{const v=e.type=='checkbox'?e.checked:e.value;api('/api/settings',{[k]:v})})});
$('b-fpoint').onclick=()=>{if(S.settings.focus_point){api('/api/settings',{focus_point:null});return}picking=!picking;document.body.classList.toggle('picking',picking);$('fpoint').textContent=picking?'click a preview…':'centre'};
$('b-connect').onclick=()=>api('/api/connect');$('b-load').onclick=()=>api('/api/load');$('b-eject').onclick=()=>{sel.clear();api('/api/eject')};
$('b-thumb').onclick=()=>{sel.clear();api('/api/thumbnail')};$('b-preview').onclick=()=>api('/api/preview',{frames:selList()});
$('b-cal').onclick=()=>api('/api/calibrate',{frame:selList()[0]??0});$('b-af').onclick=()=>api('/api/autofocus',{frame:selList()[0]??0});
$('b-ae').onclick=()=>api('/api/autoexpose',{frame:selList()[0]??0});$('b-unlock').onclick=()=>api('/api/unlock');
const rollKeys=['film_brand','film_stock','film_iso','film_notes','film_description'];
let descHold=0;
$('rolldesc').addEventListener('change',()=>{descHold=Date.now()+1500;api('/api/describe',{text:$('rolldesc').value})});
function rollnoDone(){$('rollnodlg').hidden=true;$('b-rollnoyes').hidden=true}
async function rollno(move){const n=+$('rollno').value;const r=await api('/api/rollnumber',{number:n,move_aside:move});
 if(r.ok){rollnoDone();return}
 $('rollnodlg').hidden=false;$('b-rollnoyes').hidden=!r.in_the_way;
 if(r.in_the_way)$('b-rollnoyes').textContent=r.in_the_way.length>1?'Move them and renumber':'Move it and renumber';
 if(r.busy)$('rollnomsg').textContent='Wait until the scan finishes, then try again.';
 else if(r.in_the_way){const pad=String(n).padStart(3,'0');
  $('rollnomsg').textContent='Nothing was changed yet. To count on from Roll '+pad+', '+(r.in_the_way.length>1?'these older folders':'this older folder')+' would be moved into "'+r.aside+'" (nothing is deleted or renamed inside): '+r.in_the_way.map(d=>d.name+' ('+d.files+' file'+(d.files==1?'':'s')+')').join(', ')+'.'}
 else $('rollnomsg').textContent=r.error||'That did not work.'}
$('b-rollno').onclick=()=>rollno(false);$('b-rollnoyes').onclick=()=>rollno(true);
$('b-rollnono').onclick=()=>{rollnoDone();$('rollno').value=S.roll_number||''};
$('b-newroll').onclick=()=>{rollKeys.forEach(k=>$('r-'+k).value='');$('rolldlg').hidden=false;$('b-newroll').hidden=true;$('r-film_brand').focus()};
$('b-rollcancel').onclick=()=>{$('rolldlg').hidden=true;$('b-newroll').hidden=false};
$('b-rollok').onclick=async()=>{const b={};rollKeys.forEach(k=>b[k]=$('r-'+k).value);
 const r=await api('/api/newroll',b);if(r.busy){$('rollfilm').textContent='Wait until the scan finishes, then try again.';return}$('rolldlg').hidden=true;$('b-newroll').hidden=false};
$('b-notifytest').onclick=()=>api('/api/notifytest');
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
 keys.forEach(k=>{const e=$('s-'+k);if(document.activeElement===e)return;if(e.type=='checkbox')e.checked=!!st[k];else if(k=='start')e.value=S.next_number;else if(!['dpi','preview_dpi','samples','format'].includes(k))e.value=st[k]??''});
 const opt=c?c.optical_dpi:2900, lo=c&&c.x_dpi_range?c.x_dpi_range[0]:73;
 const dpis=[];for(let n=1;n<=40;n++){const d=Math.round(opt/n);if(d>=lo)dpis.push(d)}
 fillSelect('s-dpi',dpis,st.dpi);fillSelect('s-preview_dpi',dpis.filter(d=>d<=1000),st.preview_dpi);
 const ms=c?(c.max_samples||1):1;fillSelect('s-samples',[1,2,4,8,16].filter(n=>n<=Math.max(ms,4)),st.samples,v=>v==1?'Off':(v<=ms?v+'x (one pass)':v+'x ('+v+' passes)'));
 $('mshint').textContent=st.samples>ms?('Cleaner shadows by averaging '+Math.min(st.samples,4)+' passes. Each frame takes that many times longer.'):'';
 const fmts=c&&c.film_formats?c.film_formats:[];$('fmtrow').hidden=!fmts.length;fillSelect('s-format',[''].concat(fmts),st.format,v=>v||'From holder');
 for(const id of ['b-load','b-eject','b-thumb','b-preview','b-cal','b-af','b-ae','b-unlock','b-scan','b-connect','b-rl','b-rr','b-newroll','b-rollok','b-rollno','b-rollnoyes'])$(id).disabled=busy;$('b-stop').disabled=!busy;
 if(c&&!c.thumbnail){$('s-preview_mode').value='scan';$('s-preview_mode').disabled=true}else $('s-preview_mode').disabled=false;
 $('negrow').hidden=!!st.archive;$('dustrow').hidden=!st.clean;
 const iceopt=$('s-dust_method').querySelector('option[value=ice]');iceopt.hidden=iceopt.disabled=!S.ice_helper;
 const nf=S.frames.length, ns=sel.size;
 $('b-scan').textContent=ns?`Scan ${ns} selected frame${ns>1?'s':''}`:(nf?`Scan all ${nf} frame${nf>1?'s':''}`:'Scan all frames');
 $('b-preview').textContent=ns?`Preview ${ns} selected`:'Preview all';
 $('rollname').textContent=st.roll||'';$('rollfilm').textContent=(S.film_text?('Film: '+S.film_text):'No film details')+' · saving in '+(S.roll_path||'')+(st.archive?' (RAW/ and Positive/)':'');
 if(document.activeElement!==$('rollno')&&$('rollnodlg').hidden)$('rollno').value=S.roll_number||'';
 const dsc=$('rolldesc');if(document.activeElement!==dsc&&Date.now()>descHold)dsc.value=st.film_description||'';
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
            if path == "/api/describe":
                app.describe(body.get("text", ""))
                return self._send({"ok": True})
            if path == "/api/rollnumber":
                return self._send(app.quick_job("renumbering", lambda: app.renumber_roll(
                    body.get("number"), bool(body.get("move_aside")))))
            if path == "/api/notifytest":
                if not app.notify("Test message. This is how you will hear that a strip is done."):
                    app.say("nothing to send to yet: fill in a number or Apple ID, or tick the banner")
                return self._send({"ok": True})
            if path == "/api/unlock":
                app.focus_lock = app.exposure_lock = None
                app.settings.update(focus_mode="frame", exposure_mode="auto")
                app.frame_focus = {}
                app.say("focus and exposure reset: the next pass focuses again, and each frame is exposed on its own")
                return self._send({"ok": True})
            if path == "/api/settings":
                for k, v in body.items():
                    if k in ("roll", "film_description"):
                        continue  # these have their own buttons, which also look after the folder
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
                if ("rotate180" in body or "invert_preview" in body or "tone" in body or "restore" in body) and not app.job:
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
    app.read_description()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    except OSError:
        sys.exit(f"Port {args.port} is busy: Coolscan is probably still running from before.\n"
                 f"Stop it with:  pkill -9 -f coolscan.py   (or start with --port {args.port + 1})")
    url = f"http://127.0.0.1:{args.port}"
    print(f"Coolscan is running at {url}  (Ctrl-C to quit)")
    app.run_job("connect", app.connect)
    threading.Thread(target=app.check_ice, daemon=True).start()
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
