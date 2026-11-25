"""Grain reduction, faded-colour restoration, and lining passes up for averaging."""

import math

import numpy as np

from . import gpu


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
    if gpu.ready(planes[0].size):
        done = gpu.run(gpu.reduce_grain, planes, k, progress)
        if done is not None:
            return done
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

def merge_exposures(short, long, factor):
    """One plane from two passes over the same frame, the second exposed `factor` times longer.

    The longer pass reads dense film (the shadows of a slide, the highlights of a negative) well
    clear of the scanner's noise, and burns out on thin film. So the result takes the longer pass
    wherever it stays under about three quarters of full scale, the normal pass where the longer
    one nears its ceiling, and a mix in between. The longer pass is brought down to the normal
    one's scale by what the two passes actually measure against each other (not by `factor`
    alone, since the lamp and the black level are never exact).

    Returns (merged plane uint16 on the normal pass's scale, share taken from the longer pass),
    or None when the passes do not fit together: then the caller keeps the normal pass.
    """
    if short.shape != long.shape:
        return None
    top = 65535.0
    step = max(1, int(math.sqrt(short.size / 500_000)))
    ss, ll = short[::step, ::step].astype(np.float64), long[::step, ::step].astype(np.float64)
    ok = (ll > 0.10 * top) & (ll < 0.75 * top)
    if int(ok.sum()) < 1000:
        return None  # the longer pass is burnt out (or black) nearly everywhere: nothing to gain
    gain, offset = np.polyfit(ll[ok], ss[ok], 1)
    if not 0.6 / factor < gain < 1.6 / factor:
        return None  # the second pass is not about `factor` times brighter: something went wrong with it
    lf = long.astype(np.float32)
    weight = np.clip((np.float32(0.90 * top) - lf) / np.float32(0.15 * top), 0, 1)
    merged = weight * (np.float32(gain) * lf + np.float32(offset)) + (1 - weight) * short.astype(np.float32)
    return np.clip(merged + 0.5, 0, top).astype(np.uint16), float(weight.mean())


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
