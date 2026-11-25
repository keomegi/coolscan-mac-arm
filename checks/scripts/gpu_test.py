"""The graphics chip (Apple MLX) against numpy: same pictures, and how much quicker.

    python checks/scripts/gpu_test.py [RAW.tif]

Runs grain reduction, the colour-space conversions (negative and slide) and the
full-size screen picture twice, once with numpy and once with MLX, and compares every pixel.
With no file a made-up 4320 x 2870 negative with dust on it is used; give a RAW file from
Archive mode (red, green, blue and infrared) to check on a real scan.

A check FAILs when any pixel is more than one level apart (out of 65,535; out of 255 for the
screen picture), or when more than 1 value in 100 differs at all. Without MLX it says SKIP.
COOLSCAN_GPU=force runs MLX on the processor (for machines without a graphics chip).
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from coolscan_app.imaging import gpu  # noqa: E402
from coolscan_app.imaging.colour import convert_planes, invert_negative, to_srgb8  # noqa: E402
from coolscan_app.imaging.dust import dust_clean  # noqa: E402
from coolscan_app.imaging.enhance import reduce_grain  # noqa: E402
from coolscan_app.imaging.files import read_tiff16, read_tiff_info  # noqa: E402

failed = []


def made_up():
    rng = np.random.default_rng(7)
    rows, cols = 2870, 4320
    y, x = np.mgrid[0:rows, 0:cols].astype(np.float32)
    base = 20000 + 15000 * np.sin(x / 300) * np.cos(y / 200)
    planes = [np.clip(base * f + rng.normal(0, 600, (rows, cols)), 0, 65535).astype(np.uint16)
              for f in (1.6, 1.1, 0.7)]
    ir = np.clip(52000 + rng.normal(0, 400, (rows, cols)), 0, 65535)
    for _ in range(400):  # specks, a few hairs and some big blobs
        cy, cx, r = rng.integers(0, rows), rng.integers(0, cols), rng.integers(1, 30)
        ir[max(0, cy - r):cy + r, max(0, cx - r):cx + 2] *= rng.uniform(0.2, 0.9)
    ir[:, :40] = 3000  # the film holder down one side
    return planes, ir.astype(np.uint16), None


def both(name, fn, limit=1):
    """fn() with numpy, then with MLX; compare. Returns the numpy result."""
    gpu._state = "off (this check)"
    t = time.perf_counter()
    a = fn()
    cpu = time.perf_counter() - t
    gpu._state = None
    gpu._look()
    if not gpu.ready():
        print(f"SKIP {name}: {gpu.status()}")
        return a
    t = time.perf_counter()
    b = fn()
    on = time.perf_counter() - t
    if gpu._state != "on":
        failed.append(name)
        print(f"FAIL {name}: MLX failed ({gpu._state})")
        return a
    pa = a if isinstance(a, list) else [a]
    pb = b if isinstance(b, list) else [b]
    worst, differ, total = 0, 0, 0
    for u, v in zip(pa, pb):
        d = np.abs(u.astype(np.float64) - v.astype(np.float64))
        worst = max(worst, float(d.max()))
        differ += int((d > (1e-6 if u.dtype.kind == "f" else 0)).sum())
        total += d.size
    share = differ / total
    float_ok = pa[0].dtype.kind == "f" and worst <= 1.5 / 65535
    ok = float_ok or (worst <= limit and share <= 0.01)
    if not ok:
        failed.append(name)
    unit = "" if pa[0].dtype.kind == "f" else " levels"
    print(f"{'ok  ' if ok else 'FAIL'} {name}: numpy {cpu:.2f} s, MLX {on:.2f} s ({cpu / max(on, 1e-9):.1f}x); "
          f"largest difference {worst:.3g}{unit}, {share * 100:.4f}% of values differ")
    return a


def main():
    print(gpu.status())
    if not gpu.ready():
        print("SKIP: nothing to compare without MLX")
        return 0
    if len(sys.argv) > 1:
        info = read_tiff_info(sys.argv[1])
        p = read_tiff16(sys.argv[1])
        if len(p) != 4:
            sys.exit("need a RAW file with infrared (Archive mode)")
        planes, ir, icc = p[:3], p[3], info.get("icc")
        print(f"{sys.argv[1]}: {ir.shape[1]} x {ir.shape[0]}")
    else:
        planes, ir, icc = made_up()
        print("made-up negative 4320 x 2870")
    res = dust_clean(planes, ir, 2900, True)[0]  # dust removal stays on the processor
    pos = invert_negative(res, "flat")
    both("grain reduction (level 2)", lambda: reduce_grain(pos, 2), limit=0)
    both("grain reduction (level 4)", lambda: reduce_grain(pos, 4), limit=0)
    u16 = [(v * 65535 + 0.5).astype(np.uint16) for v in pos]
    pro = both("negative -> ProPhoto RGB", lambda: convert_planes(u16, "ProPhoto RGB")[0])
    both("negative -> Adobe RGB", lambda: convert_planes(u16, "Adobe RGB (1998)")[0])
    if icc and len(icc) > 10000:
        both("slide (Nikon profile) -> ProPhoto RGB", lambda: convert_planes(res, "ProPhoto RGB", icc)[0])
    else:
        print("SKIP slide conversion: no Nikon scanner profile in this file")
    both("screen picture, full size", lambda: to_srgb8(pro, "ProPhoto RGB"))
    print("FAILED: " + ", ".join(failed) if failed else "all match")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
