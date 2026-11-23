"""A quick check of dust.py that needs no scanner and no scans: python checks/scripts/check_dust.py

It makes a small pretend negative with grain, puts a hair, some specks and a blob of dirt on
it (darkening the picture and the infrared together, as real dirt does), cleans it, and checks
that the dirt is gone and that most of the clean film came back exactly as it went in. (Some
clean pixels are always touched: the ones whose infrared happens to read low. On real film
that is about one pixel in ten, the same as Nikon's Digital ICE.)
"""
import sys
import time

import numpy as np

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from coolscan_app.imaging import dust  # noqa: E402


def pretend_scan(rows=600, cols=900, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols]
    picture = 0.25 + 0.5 * (0.5 + 0.5 * np.sin(xx / 70.0) * np.cos(yy / 50.0))      # 0.25 .. 0.75 of full scale
    clean = [np.clip(picture * gain * (1 + 0.03 * rng.standard_normal((rows, cols))), 0.02, 0.98)
             for gain in (1.0, 0.8, 0.6)]
    ir = 0.6 * clean[0] ** 0.15 * (1 + 0.012 * rng.standard_normal((rows, cols)))    # a little of the red picture leaks in
    dirt = np.ones((rows, cols))
    dirt[np.abs(yy - (200 + 0.2 * xx)) < 1.5] = 0.55                                 # a hair
    for _ in range(150):                                                             # specks
        y, x = rng.integers(5, rows - 5), rng.integers(5, cols - 5)
        dirt[y:y + rng.integers(1, 3), x:x + rng.integers(1, 3)] = rng.uniform(0.5, 0.9)
    dirt[((yy - 420) / 14.0) ** 2 + ((xx - 600) / 22.0) ** 2 < 1] = 0.03              # a blob of solid dirt
    to16 = lambda a: np.clip(a * 65535, 0, 65535).astype(np.uint16)
    return [to16(c) for c in clean], [to16(c * dirt) for c in clean], to16(ir * dirt), dirt < 1


def main():
    clean, dirty, ir, mask = pretend_scan()
    started = time.time()
    result = dust.dust_clean(dirty, ir, 2900, negative=True)
    assert result is not None, "the cleaner found no clear film"
    cleaned, count = result
    took = time.time() - started
    err = lambda planes, where: float(np.mean([np.abs(np.log2((p[where] + 1.0) / (c[where] + 1.0))).mean()
                                               for p, c in zip(planes, clean)]) * 100)
    before, after = err(dirty, mask), err(cleaned, mask)
    touched_clean = float(np.mean([(p != d)[~mask].mean() for p, d in zip(cleaned, dirty)]) * 100)
    print(f"dirt pixels: off by {before:.1f}% of a stop before, {after:.1f}% after")
    print(f"clean film changed: {touched_clean:.2f}% of pixels; {count} pixels changed in all; {took:.2f} s")
    assert all(p.dtype == np.uint16 and p.shape == ir.shape for p in cleaned)
    assert after < 0.15 * before, "the dirt is still there"
    assert touched_clean < 20.0, "too much clean film was changed"
    assert all((p >= d).all() for p, d in zip(cleaned, dirty)), "a pixel came out darker than it scanned"
    assert dust.dust_clean(dirty, np.zeros_like(ir), 2900) is None, "no infrared at all should mean: nothing to do"
    print("OK")


if __name__ == "__main__":
    sys.exit(main())
