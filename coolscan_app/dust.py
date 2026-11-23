"""Dust and scratch removal from the infrared picture."""

import math
import os

import numpy as np


#
# Dust and scratches block infrared light; film dyes hardly do. So a pixel whose
# infrared is clearly below clear film has something on it, and the size of the
# shortfall says how much light was lost.
#
# Everything is done in log space, D(v) = 4096 * log2(v + 1), where a loss of
# light is a plain subtraction. Three things happen:
#
#   * a pixel whose infrared is not clearly below the rest of the film is left
#     exactly as scanned (that is about nine pixels in ten),
#   * a light shortfall is added back to red, green and blue, which keeps the
#     picture's own detail and grain,
#   * the heavier the shortfall, the more the pixel is replaced by the average of
#     its clean neighbours instead, because under real dirt nothing of the
#     picture is left to brighten.
#
# The calibration (how much of the red picture shows in the infrared) is the one
# nkscan uses in its dust.rs (MIT/Apache-2.0, which follows a6o's openICE). The
# numbers below were tuned on colour negatives from an LS-40 at 2900 dpi until
# the result lined up with Nikon Scan's Digital ICE on the same scans.

_DUST_M = 65535.0
_DUST_TAU = 8847.23                 # infrared above this is film, below it is the holder or solid dirt
_DUST_LEAK = {True: 0.17, False: 0.14}   # red->infrared leakage when it cannot be measured (negative, positive)
_DUST_UNIT = (_DUST_M / 16) * math.log2(1.01)   # D per 1 % of light
_DUST_BELOW = 3.1                   # cleaning starts where the infrared is this many % under the frame's average
_DUST_LEAST = 5.86                  # % of light given back to a pixel that is only just over that line
_DUST_GAIN = 1.48                   # % of light given back for each further % of infrared missing
_DUST_FADE, _DUST_FADE_POWER = 9.69, 1.29    # how fast the pixel's own value gives way to its neighbours (%, shape)
_DUST_TRUST = 0.82                  # how strongly a doubtful neighbour is kept out of the average
_DUST_PASSES = 2                    # size of the neighbourhood at 2900 dpi: 1-2-1 averages, this many times
_DUST_SUPPORT = 0.3                 # least share of clean neighbours an average may rest on
_DUST_LUT = None


def _dust_lut():
    global _DUST_LUT
    if _DUST_LUT is None:
        _DUST_LUT = (np.log2(np.arange(65536, dtype=np.float64) + 1.0) * (_DUST_M / 16)).astype(np.float32)
    return _DUST_LUT


def _dust_level(d):
    """Back from D to a 16-bit level."""
    return np.clip(np.floor(np.exp2(d.astype(np.float64) * (16 / _DUST_M)) - 1.0 + 0.5), 0, 65535).astype(np.uint16)


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


def _dust_reference(red, ir, c):
    """The slope that takes the picture out of the infrared, and the level where cleaning starts."""
    lut = _dust_lut()
    slope = c / (1 - c)
    film = ir > _DUST_TAU
    if not film.any():
        return None
    q = lut[ir[film]].astype(np.float64) - slope * lut[red[film]]
    return slope, float(q.mean()) - _DUST_BELOW * _DUST_UNIT


def _dust_spread(a, passes):
    """Weighted sum over a neighbourhood: 1-2-1 across and down, `passes` times (weights add up to 16 ** passes)."""
    for axis in (1, 0):
        for _ in range(passes):
            b = a + a
            if axis:
                b[:, 1:] += a[:, :-1]
                b[:, :-1] += a[:, 1:]
            else:
                b[1:] += a[:-1]
                b[:-1] += a[1:]
            a = b
    return a


def _dust_blocks(a, n):
    """Sums over n x n blocks (the last block of a row or column may be short)."""
    rows, cols = a.shape
    pr, pc = -rows % n, -cols % n
    if pr or pc:
        a = np.pad(a, ((0, pr), (0, pc)))
    return a.reshape(a.shape[0] // n, n, a.shape[1] // n, n).sum(axis=(1, 3), dtype=np.float32)


def _dust_shortfall(d_ir, d_red, slope, start):
    """Light to give back to each pixel, in D, and which pixels are to be cleaned."""
    under = start - (d_ir - slope * d_red)                  # how far the infrared is under the starting line
    short = np.clip(_DUST_LEAST * _DUST_UNIT + _DUST_GAIN * under, -15 * _DUST_UNIT, 80 * _DUST_UNIT)
    return short, under > 0


def _dust_keep(short, fix):
    """How much of a pixel's own value to keep (1 = all of it), from the light it is owed."""
    over = np.maximum(short - _DUST_LEAST * _DUST_UNIT, 0) / (_DUST_FADE * _DUST_UNIT)
    return np.where(fix, np.exp(-over ** _DUST_FADE_POWER), 1.0).astype(np.float32)


def _dust_band(planes, ir, out, y0, y1, slope, start, passes, coarse, later):
    """Clean rows y0..y1 of the frame into `out`; returns how many pixels were changed.

    Also adds this band to the coarse picture (`coarse`, one cell per 4 x 4 pixels) and notes
    in `later` the pixels with too few clean neighbours close by, which are finished from it.
    """
    rows, cols = ir.shape
    lut = _dust_lut()
    halo = passes + 1
    r0, r1 = max(0, y0 - halo), min(rows, y1 + halo)
    own = slice(y0 - r0, y1 - r0)
    dens = [lut[p[r0:r1]] for p in planes]
    short, fix = _dust_shortfall(lut[ir[r0:r1]], dens[0], slope, start)
    keep = _dust_keep(short, fix)                            # 1 on clean film, towards 0 under dirt
    trust = keep ** _DUST_TRUST
    fix = fix[own]
    weight_sum = _dust_spread(trust, passes)[own]
    share = weight_sum / (_DUST_SUPPORT * 16.0 ** passes)    # 1 and over: enough clean neighbours close by
    near, far = fix & (share >= 1), fix & (share < 1)
    cells = slice(y0 // 4, (y1 + 3) // 4)
    coarse[0][cells] = _dust_blocks(trust[own], 4)
    coarse[4][cells] = _dust_blocks((ir[y0:y1] <= _DUST_TAU).astype(np.float32), 4)
    some, more = bool(near.any()), bool(far.any())
    close = []
    for ch in range(3):
        level = dens[ch] + short                             # the light given back: what clean film would read
        weighted = trust * level
        coarse[1 + ch][cells] = _dust_blocks(weighted[own], 4)
        if not (some or more):
            continue
        around = _dust_spread(weighted, passes)[own]
        if more:                                             # what the few neighbours there are say, for later
            close.append(around[far] / np.maximum(weight_sum[far], 1e-20))
        if some:
            x = around[near] / weight_sum[near]
            x = x + keep[own][near] * (level[own][near] - x)
            x = np.maximum(x, dens[ch][own][near])          # a cleaned pixel never comes out darker than it scanned
            out[ch][y0:y1][near] = np.maximum(_dust_level(x), planes[ch][y0:y1][near])
    if more:
        yy, xx = np.nonzero(far)
        later.append((yy + y0, xx, share[far], close))
    return int(near.sum()) if some else 0


def _dust_sample(grid, y, x, cell):
    """Read a coarse grid (one value per cell x cell pixels) at pixel centres, between its cells."""
    gy = np.clip((y + 0.5) / cell - 0.5, 0, grid.shape[0] - 1)
    gx = np.clip((x + 0.5) / cell - 0.5, 0, grid.shape[1] - 1)
    y0, x0 = np.floor(gy).astype(np.intp), np.floor(gx).astype(np.intp)
    y1, x1 = np.minimum(y0 + 1, grid.shape[0] - 1), np.minimum(x0 + 1, grid.shape[1] - 1)
    fy, fx = (gy - y0).astype(np.float32), (gx - x0).astype(np.float32)
    return ((grid[y0, x0] * (1 - fx) + grid[y0, x1] * fx) * (1 - fy)
            + (grid[y1, x0] * (1 - fx) + grid[y1, x1] * fx) * fy)


def _dust_fill_far(planes, ir, out, slope, start, coarse, later):
    """Finish the pixels inside bigger dirt, which have few clean neighbours or none; returns how many.

    Each gets a mix of three averages, by how much clean film each one rests on: its close
    neighbours, the coarse picture (4 x 4 pixels a cell) and a coarser one still (16 x 16).
    Mixing them is what keeps the patch from showing an edge.
    """
    y = np.concatenate([p[0] for p in later])
    x = np.concatenate([p[1] for p in later])
    share = np.clip(np.concatenate([p[2] for p in later]), 0, 1)
    close = [np.concatenate([p[3][ch] for p in later]) for ch in range(3)]
    lut = _dust_lut()
    wide4 = [_dust_spread(g, 2) for g in coarse]
    wide16 = [_dust_spread(_dust_blocks(g, 4), 2) for g in coarse]
    full4, full16 = 16.0 * 256.0, 256.0 * 256.0             # the sum over a neighbourhood with no dirt in it
    w4, w16 = _dust_sample(wide4[0], y, x, 4), _dust_sample(wide16[0], y, x, 16)
    share4 = np.clip(w4 / (_DUST_SUPPORT * full4), 0, 1)
    has16 = w16 > 0.02 * full16
    # a really wide dark area is the film holder or the edge of the frame: there is no picture under it
    holder = _dust_sample(wide16[4], y, x, 16) > 0.5 * full16
    done = ~holder & (has16 | (share4 >= 1) | (share >= 1))
    if not done.any():
        return 0
    y, x, share, share4, has16, w4, w16 = y[done], x[done], share[done], share4[done], has16[done], w4[done], w16[done]
    share4 = np.where(has16, share4, 1.0)                   # nothing coarser to lean on: the 4 x 4 picture as it is
    dens = [lut[p[y, x]] for p in planes]
    short, fix = _dust_shortfall(lut[ir[y, x]], dens[0], slope, start)
    keep = _dust_keep(short, fix)
    for ch in range(3):
        a4 = _dust_sample(wide4[1 + ch], y, x, 4) / np.maximum(w4, 1e-20)
        a16 = _dust_sample(wide16[1 + ch], y, x, 16) / np.maximum(w16, 1e-20)
        around = share4 * a4 + (1 - share4) * a16
        around = share * close[ch][done] + (1 - share) * around
        v = around + keep * (dens[ch] + short - around)
        out[ch][y, x] = np.maximum(_dust_level(np.maximum(v, dens[ch])), planes[ch][y, x])
    return int(y.size)


def dust_clean(planes, ir, dpi, negative=True, band=128, progress=None):
    """Dust and scratch removal from the infrared. Returns (cleaned planes, pixels changed).

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
    measured = _dust_reference(thin(planes[0]), thin(ir), cal[0])
    if measured is None:
        return None
    slope, start = measured
    # the neighbourhood keeps its size on the film: fewer passes for a coarser scan
    passes = max(1, min(6, round(_DUST_PASSES * (dpi / 2900.0) ** 2))) if dpi else _DUST_PASSES
    band = max(16, band - band % 16)
    out = [p.copy() for p in planes]
    coarse = [np.zeros(((rows + 3) // 4, (cols + 3) // 4), np.float32) for _ in range(5)]
    later = []
    starts = list(range(0, rows, band))
    count = done = 0
    # numpy lets other threads run while it works, so a few bands at a time is quicker
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as pool:
        for n in pool.map(lambda y0: _dust_band(planes, ir, out, y0, min(rows, y0 + band), slope, start,
                                                passes, coarse, later), starts):
            count += n
            done += 1
            if progress:
                progress("dust removal", 0, done, len(starts))
    if later:
        count += _dust_fill_far(planes, ir, out, slope, start, coarse, later)
    return out, count
