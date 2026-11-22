"""Negative to positive, pictures for the screen, colour spaces and profiles."""

import math
import struct

import numpy as np

from .enhance import restore_colour

PREVIEW_MAX_SIDE = 1400  # pixels; previews are shrunk to this for the browser
HEADROOM = 0.02  # fraction of the range left free below black and above white in positives


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
