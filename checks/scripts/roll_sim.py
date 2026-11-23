"""A made-up roll of colour negative film, for checking film profiles without a scanner.

The film has an orange mask (a different base density for red, green and blue) and a different
contrast for each colour, as real film does. The scanner exposes each frame on its own, one colour
at a time, the way the LS-40 does for negatives."""
import numpy as np

BASE = (0.25, 0.60, 0.95)      # film base density: the orange mask
GAMMA = (0.55, 0.62, 0.70)     # density per unit of log exposure, per colour


def scene(kind, rng, rows=200, cols=300):
    """Log exposure of a scene for red, green, blue (0 = deepest shadow, about 2.6 = brightest),
    with a grey card in the middle. Returns the three planes and where the grey card is."""
    yy, xx = np.mgrid[0:rows, 0:cols]
    light = 0.4 + 1.8 * (xx / cols) + 0.15 * np.sin(yy / 17.0)          # a ramp from dark to bright
    tint = {"neutral": (0, 0, 0), "foliage": (-0.35, 0.25, -0.45), "sunset": (0.45, 0.0, -0.5),
            "sky": (-0.4, -0.05, 0.4)}[kind]
    planes = [light + t + 0.02 * rng.standard_normal((rows, cols)) for t in tint]
    if kind == "neutral":                                              # an ordinary picture: every tone, some colour
        for c in range(3):
            planes[c][: rows // 3] += rng.uniform(-0.3, 0.3)
        planes = [np.where(xx < cols // 12, 0.05, p) for p in planes]  # a deep shadow
        planes = [np.where(xx > cols - cols // 12, 2.55, p) for p in planes]  # a bright highlight
    card = (slice(rows // 2 - 15, rows // 2 + 15), slice(cols // 2 - 20, cols // 2 + 20))
    for p in planes:
        p[card] = 1.3 + 0.01 * rng.standard_normal(p[card].shape)      # the grey card: the same light in all three
    return [np.clip(p, 0, 2.7) for p in planes], card


def scan(planes, rng, exposures=None):
    """What the scanner reads off the negative: uint16 planes and the exposures it used."""
    dens = [b + g * p for b, g, p in zip(BASE, GAMMA, planes)]
    if exposures is None:   # auto exposure: each colour so that the thinnest film reads about 85% of full scale
        exposures = {n: int(20000 * 0.85 * 10 ** float(d.min()) * rng.uniform(0.9, 1.1))
                     for n, d in zip(("red", "green", "blue"), dens)}
    out = []
    for n, d in zip(("red", "green", "blue"), dens):
        v = 65535 * exposures[n] / 20000 * 10 ** (-d)
        out.append(np.clip(v + rng.normal(0, 20, v.shape), 0, 65535).astype(np.uint16))
    return out, exposures
