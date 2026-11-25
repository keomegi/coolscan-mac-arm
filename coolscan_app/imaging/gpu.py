"""The heavy picture work on the Mac's graphics chip (Apple MLX), when MLX is installed.

Without MLX nothing here is used and the app works as before, on the processor with numpy.
With it, three jobs move to the graphics chip on full-size frames:

    * grain reduction (enhance.py),
    * conversion into the output colour space, slides through Nikon's scanner profile too (colour.py),
    * the full-size 8-bit picture for the screen (colour.py).

Dust removal stays on the processor: on an M1 Pro the graphics chip was no quicker at it
(0.4 to 0.5 s a frame either way), so the tuned cleaner keeps running exactly as before.

Each function here does the same sums in the same order as its numpy twin, so the pictures come
out the same. The graphics chip works in 32-bit floating point where numpy sometimes uses 64-bit,
so a few pixels can end up one level apart out of 65,535 (checks/scripts/gpu_test.py measures it).

COOLSCAN_GPU=0 (or off) in the environment keeps everything on the processor. COOLSCAN_GPU=force
runs these on MLX even without a graphics chip (MLX's own processor back end): only for checks.
If anything here fails, the app says so once and carries on with numpy for the rest of the run.
"""

import os
import sys
import threading

import numpy as np

SMALLEST = 1_000_000   # pixels; smaller pictures (previews, thumbnails) are quicker on the processor

_mx = None
_state = None          # None: not looked at yet; "on"; otherwise why it is off
_lock = threading.Lock()


def _look():
    global _mx, _state
    want = os.environ.get("COOLSCAN_GPU", "").strip().lower()
    if want in ("0", "off", "no", "false", "cpu"):
        _state = "off (COOLSCAN_GPU is set to off)"
        return
    try:
        import mlx.core as mx
    except Exception:  # noqa: BLE001 - not installed, or installed for another Python
        _state = "off (MLX is not installed)"
        return
    try:
        metal = bool(mx.metal.is_available())
    except Exception:  # noqa: BLE001
        metal = False
    if not metal and want != "force":
        _state = "off (no graphics chip MLX can use)"
        return
    _mx = mx
    _state = "on"


def status():
    """In words: is the graphics chip in use, and if not, why not."""
    if _state is None:
        _look()
    if _state != "on":
        return f"graphics chip {_state}; picture work on the processor"
    where = "graphics chip" if _mx.default_device() == _mx.gpu else "MLX on the processor (forced, for checks)"
    return f"picture work on the {where} (Apple MLX {getattr(_mx, '__version__', '?')})"


def ready(pixels=SMALLEST):
    """True when MLX is there and a picture of this many pixels is worth sending to it."""
    if _state is None:
        _look()
    return _state == "on" and pixels >= SMALLEST


def run(fn, *args, **kw):
    """fn(*args) on MLX. Returns None when it fails (the caller then does it with numpy).

    One job at a time: the app's jobs run in threads, and MLX is happiest fed from one.
    A Cancelled from a progress callback goes through untouched.
    """
    global _state
    with _lock:
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001 - never lose a picture over the graphics chip
            if type(e).__name__ == "Cancelled":
                raise
            _state = f"off after an error ({type(e).__name__}: {e})"
            print(f"coolscan: the graphics chip failed ({type(e).__name__}: {e}); "
                  f"carrying on with the processor", file=sys.stderr)
            return None


# ---------------------------------------------------------------------------- grain reduction

def _smooth(a, scale):
    """enhance._smooth on MLX."""
    mx = _mx
    step = 1 << scale
    for axis in (0, 1):
        n = a.shape[axis]
        w = 2 * step
        idx = np.pad(np.arange(n, dtype=np.int32), w, mode="reflect" if n > w else "edge")
        p = mx.take(a, mx.array(idx), axis=axis)

        def take(k):
            s = (2 + k) * step
            return p[s:s + n] if axis == 0 else p[:, s:s + n]

        a = (take(-2) + take(2)) * np.float32(1 / 16) + (take(-1) + take(1)) * np.float32(4 / 16) \
            + take(0) * np.float32(6 / 16)
    return a


def _box3(a):
    mx = _mx
    p = mx.pad(a, 1, mode="edge")
    rows = p[:-2] + p[1:-1] + p[2:]
    return (rows[:, :-2] + rows[:, 1:-1] + rows[:, 2:]) * np.float32(1 / 9)


def _grain_size(d):
    """enhance._grain_size: the thinned-out tiles are taken here, the medians with numpy (they are small)."""
    from . import enhance
    tile = 32
    rows, cols = (d.shape[0] // tile) * tile, (d.shape[1] // tile) * tile
    if rows < 4 * tile or cols < 4 * tile:
        return enhance._grain_size(np.array(d))
    t = _mx.abs(d[:rows, :cols]).reshape(rows // tile, tile, cols // tile, tile)
    t = np.array(t[::4, :, ::4, :])
    spread = np.median(t.transpose(0, 2, 1, 3).reshape(-1, tile * tile), axis=1)
    return float(np.percentile(spread, 20)) / 0.6745


def reduce_grain(planes, k, progress=None):
    """enhance.reduce_grain on MLX, from where it has worked out the level k."""
    mx = _mx
    planes = [mx.array(np.asarray(p, np.float32)) for p in planes]
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
            d = d * mx.maximum(np.float32(0), 1 - (t * t) / (_box3(d * d) + np.float32(1e-12)))
            kept = d if kept is None else kept + d
            smooth = nxt
            mx.eval(smooth, kept)
            if progress:
                progress("grain reduction", 0, n * 4 + scale + 1, steps)
        done.append(smooth + kept)
    if len(done) == 1:
        return [np.array(mx.clip(done[0], 0, 1))]
    y, c1, c2 = done
    third = c2 * np.float32(1 / 3)
    res = [mx.clip(y + third + c1 * np.float32(0.5), 0, 1), mx.clip(y - 2 * third, 0, 1),
           mx.clip(y + third - c1 * np.float32(0.5), 0, 1)]
    return [np.array(v) for v in res]


# ---------------------------------------------------------------------------- colour spaces

def _encode(v, gamma):
    mx = _mx
    v = mx.clip(v, 0, 1)
    if gamma == "srgb":
        return mx.where(v <= 0.0031308, v * 12.92, 1.055 * mx.power(v, 1 / 2.4) - 0.055)
    return mx.power(v, 1 / gamma)


_tables = {}


def _decode_table(gamma):
    """Linear light for every 16-bit level (worked out in float64, as colour.py does)."""
    key = ("decode", gamma)
    if key not in _tables:
        from . import colour
        v = np.arange(65536, dtype=np.float64) / 65535
        lin = colour._decode_srgb(v) if gamma == "srgb" else np.power(v, gamma)
        _tables[key] = _mx.array(lin.astype(np.float32))
    return _tables[key]


def _rows(rows, cols, pixels=2_000_000):
    step = max(1, pixels // cols)
    return [(r0, min(rows, r0 + step)) for r0 in range(0, rows, step)]


def _matmul3(rgb, m):
    """(3 planes) x 3x3 matrix -> 3 planes; m in float64 is rounded once to float32."""
    m = m.astype(np.float32)
    return [rgb[0] * float(m[i, 0]) + rgb[1] * float(m[i, 1]) + rgb[2] * float(m[i, 2]) for i in range(3)]


def convert_srgb(planes, space):
    """colour.convert_planes for the app's own sRGB positives (negatives) into `space`: the new planes."""
    from . import colour
    mx = _mx
    m, _ = colour._space_matrix(space)
    to_rgb = np.linalg.inv(m)
    gamma = colour.COLOUR_SPACES[space][2]
    whole = to_rgb @ colour._space_matrix("sRGB IEC61966-2.1")[0]
    dec = _decode_table("srgb")
    rows, cols = planes[0].shape
    out = [np.empty((rows, cols), np.uint16) for _ in range(3)]
    for r0, r1 in _rows(rows, cols):
        lin = [mx.take(dec, mx.array(p[r0:r1])) for p in planes]
        res = [(_encode(v, gamma) * 65535 + 0.5).astype(mx.uint16) for v in _matmul3(lin, whole)]
        mx.eval(res)
        for c in range(3):
            out[c][r0:r1] = np.array(res[c])
    return out


def _uniform_interp(x, table):
    """np.interp(x, linspace(0, 1, n), table) for x in 0..1, on MLX."""
    mx = _mx
    n = table.shape[0]
    pos = mx.clip(x, 0, 1) * (n - 1)
    i = mx.clip(mx.floor(pos), 0, n - 2).astype(mx.int32)
    f = pos - i.astype(mx.float32)
    lo, hi = mx.take(table, i), mx.take(table, i + 1)
    return lo + f * (hi - lo)


def _profile_tables(prof):
    """For each input colour and 16-bit level: the grid cell and where in it (worked out in float64)."""
    key = ("prof", id(prof))
    if key not in _tables:
        mx = _mx
        g = prof.grid - 1
        v16 = np.arange(65536, dtype=np.float64) / 65535
        cell, frac = [], []
        for c in range(3):
            v = prof._curve(prof.inc[c], v16) * g
            i0 = np.clip(np.floor(v).astype(np.int32), 0, g - 1)
            cell.append(mx.array(i0))
            frac.append(mx.array((v - i0).astype(np.float32)))
        clut = mx.array(prof.clut.reshape(-1, prof.clut.shape[-1]).astype(np.float32))
        outc = [mx.array(prof.outc[c].astype(np.float32)) for c in range(3)]
        _tables.clear()  # one scanner profile at a time is plenty
        _tables[key] = (cell, frac, clut, outc, prof)
    return _tables[key]


def convert_scanner(planes, space, prof):
    """colour.convert_planes for slides: scanner RGB through Nikon's profile (`prof`, a ScannerProfile)."""
    from . import colour
    mx = _mx
    cell, frac, clut, outc, _ = _profile_tables(prof)
    G = prof.grid
    m, _ = colour._space_matrix(space)
    whole = np.linalg.inv(m)
    if prof.adapt is not None:
        whole = whole @ prof.adapt
    whole = whole @ np.diag(colour._D50)
    gamma = colour.COLOUR_SPACES[space][2]
    e, k = 216 / 24389, 24389 / 27
    rows, cols = planes[0].shape
    out = [np.empty((rows, cols), np.uint16) for _ in range(3)]
    for r0, r1 in _rows(rows, cols, 1_000_000):
        px = [mx.array(p[r0:r1]) for p in planes]
        i0 = [mx.take(cell[c], px[c]) for c in range(3)]
        f = [mx.take(frac[c], px[c]) for c in range(3)]
        lab = [None, None, None]
        for dr in (0, 1):
            wr = f[0] if dr else 1 - f[0]
            for dg in (0, 1):
                wg = f[1] if dg else 1 - f[1]
                for db in (0, 1):
                    wb = f[2] if db else 1 - f[2]
                    w = wr * wg * wb
                    idx = ((i0[0] + dr) * G + (i0[1] + dg)) * G + (i0[2] + db)
                    corner = mx.take(clut, idx.reshape(-1), axis=0).reshape(*idx.shape, -1)
                    for c in range(3):
                        term = w * corner[..., c]
                        lab[c] = term if lab[c] is None else lab[c] + term
        enc = [_uniform_interp(lab[c], outc[c]) * 65535.0 for c in range(3)]
        L, A, B = enc[0] / 652.80, enc[1] / 256.0 - 128.0, enc[2] / 256.0 - 128.0
        fy = (L + 16) / 116
        fx, fz = fy + A / 500, fy - B / 200

        def inv(t):
            t3 = t * t * t
            return mx.where(t3 > e, t3, (116 * t - 16) / k)

        xyz = [inv(fx), inv(fy), inv(fz)]
        res = [(_encode(v, gamma) * 65535 + 0.5).astype(mx.uint16) for v in _matmul3(xyz, whole)]
        mx.eval(res)
        for c in range(3):
            out[c][r0:r1] = np.array(res[c])
    return out


def to_srgb8(planes, space):
    """colour.to_srgb8 for three 16-bit planes in a known colour space other than sRGB."""
    from . import colour
    mx = _mx
    gamma = colour.COLOUR_SPACES[space][2]
    dec = _decode_table(gamma)
    m = np.linalg.inv(colour._space_matrix("sRGB IEC61966-2.1")[0]) @ colour._space_matrix(space)[0]
    rows, cols = planes[0].shape
    img = np.empty((rows, cols, 3), np.uint8)
    for r0, r1 in _rows(rows, cols):
        lin = [mx.take(dec, mx.array(p[r0:r1])) for p in planes]
        res = mx.stack([(mx.clip(_encode(v, "srgb"), 0, 1) * 255 + 0.5).astype(mx.uint8)
                        for v in _matmul3(lin, m)], axis=-1)
        img[r0:r1] = np.array(res)
    return img
