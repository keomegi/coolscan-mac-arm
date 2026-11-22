"""Dust and scratch removal from the infrared picture."""

import math
import os

import numpy as np


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
