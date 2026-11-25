"""The picture network (CNN) for the learning auto crop: it looks at the pixels along each side of a
frame and scores every place a cut could go, learning from the person's crops what a border looks
like to them. Runs on MLX (the Mac's GPU; MLX's processor build elsewhere). Without MLX it is simply
not offered: croplearn's smaller models still learn.

One side at a time, turned so the side is on the left: the outer SPAN of the frame, made into a small
strip (ROWS x COLS) of log brightness measured against the middle of the frame, plus a channel for
where the built-in rule cuts and one for how far in each column is. Three 3x3 convolutions over the
strip's pixels, with the rows pooled away as it goes (so every column keeps its place), then, with
the rule, the place and the context beside them, three 1-D convolutions give each column a score. The best-scored column is the cut. All four sides share the weights, and the
strip is also learned upside down (a side looks the same either way), so each crop gives 8 examples.
"""
import math
import os

import numpy as np

try:
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
except Exception:  # noqa: BLE001 - no MLX: no picture network
    mx = None

ROWS, COLS = 48, 96
SPAN = 0.32                 # the strip reaches this far in
PLACES = 91                 # a cut can go at columns 0..90: up to 30% in
COL = SPAN / COLS           # one column, as a share of the frame
MIN_PICTURES = 12           # fewer crops: not worth trying
STEPS = int(os.environ.get("COOLSCAN_CNN_STEPS") or 600)   # learning steps (checks run fewer)


def available():
    return mx is not None


def strip(V, med, spread, rule_cut, context=()):
    """One side's strip (ROWS, COLS, 3 + len(context)) from V (the frame's log brightness, side on the
    left): the pixels, then per column where the rule cuts, how far in it is, and the frame's context
    (film type, raw scan or finished picture, colourless)."""
    h, w = V.shape
    reach = max(2, int(math.ceil(SPAN * w)))
    band = V[:, :reach]
    # rows: averaged into ROWS bands; columns: sampled at the strip's places
    rows = np.stack([b.mean(axis=0) for b in np.array_split(band, ROWS, axis=0)]) if h >= ROWS else \
        band[np.linspace(0, h - 1, ROWS).round().astype(int)]
    x = (np.arange(COLS) + 0.5) * COL * w
    S = np.stack([np.interp(x, np.arange(reach) + 0.5, r) for r in rows])
    S = np.clip((S - med) / spread, -4, 4).astype(np.float32)
    rule = np.exp(-(((np.arange(COLS) - rule_cut / COL) / 1.5) ** 2)).astype(np.float32)
    pos = (np.arange(COLS) / COLS).astype(np.float32)
    chans = [S, np.broadcast_to(rule, S.shape), np.broadcast_to(pos, S.shape)]
    chans += [np.full(S.shape, float(v), np.float32) for v in context]
    return np.stack(chans, axis=-1)


if mx is not None:
    class Net(nn.Module):
        def __init__(self, extras):
            super().__init__()
            self.c1 = nn.Conv2d(1, 8, 3, padding=1)
            self.c2 = nn.Conv2d(8, 16, 3, padding=1)
            self.c3 = nn.Conv2d(16, 16, 3, padding=1)
            self.d1 = nn.Conv1d(32 + extras, 32, 5, padding=2)
            self.d2 = nn.Conv1d(32, 32, 5, padding=2)
            self.d3 = nn.Conv1d(32, 1, 3, padding=1)

        def __call__(self, x):                       # x: (B, ROWS, COLS, 1 + extras)
            img, extra = x[..., :1], x[:, 0, :, 1:]   # the pixels; per column: rule, place, film...
            x = nn.relu(self.c1(img))
            x = _pool_rows(x, 4)
            x = nn.relu(self.c2(x))
            x = _pool_rows(x, 2)
            x = nn.relu(self.c3(x))
            x = mx.concatenate([x.mean(axis=1), x.max(axis=1), extra], axis=-1)   # (B, COLS, 32 + extras)
            x = nn.relu(self.d1(x))
            x = nn.relu(self.d2(x))
            return self.d3(x)[..., 0][:, :PLACES]   # (B, PLACES)

    def _pool_rows(x, f):
        b, h, w, c = x.shape
        return x[:, :h // f * f].reshape(b, h // f, f, w, c).max(axis=2)


def device():
    try:
        d = mx.default_device()
        return "GPU (MLX)" if d.type == mx.gpu else "processor (MLX)"
    except Exception:  # noqa: BLE001
        return "MLX"


def _targets(cuts):
    k = np.arange(PLACES, dtype=np.float32)
    T = np.exp(-0.5 * ((k[None, :] - np.asarray(cuts, np.float32)[:, None] / COL) / 1.0) ** 2)
    return T / T.sum(axis=1, keepdims=True)


def fit(strips, cuts, epochs=None, seed=0):
    """A trained Net from strips (N, ROWS, COLS, 3) and the person's cuts (N shares)."""
    mx.random.seed(seed)
    rng = np.random.default_rng(seed)
    X = np.concatenate([strips, strips[:, ::-1]])         # a side upside down is still that side
    T = _targets(np.concatenate([cuts, cuts]))
    net = Net(strips.shape[-1] - 1)
    opt = optim.Adam(learning_rate=2e-3)

    def loss_fn(model, xb, tb):
        s = model(xb)
        return -(tb * (s - mx.logsumexp(s, axis=1, keepdims=True))).sum(axis=1).mean()

    step = nn.value_and_grad(net, loss_fn)
    n = len(X)
    batch = 32
    if epochs is None:   # about STEPS steps whatever the number of crops
        epochs = max(1, int(math.ceil(STEPS / max(1, math.ceil(n / batch)))))
    for _ in range(epochs):
        order = rng.permutation(n)
        for i in range(0, n, batch):
            idx = order[i:i + batch]
            xb = X[idx].copy()
            xb[..., 0] += rng.normal(0, 0.03, xb.shape[:3]).astype(np.float32)
            loss, grads = step(net, mx.array(xb), mx.array(T[idx]))
            opt.update(net, grads)
            mx.eval(net.parameters(), opt.state)
    return net


def scores(net, strips):
    out = []
    for i in range(0, len(strips), 256):
        out.append(np.array(net(mx.array(np.asarray(strips[i:i + 256], np.float32)))))
    return np.concatenate(out) if out else np.zeros((0, PLACES), np.float32)


def save(net, path):
    from mlx.utils import tree_flatten
    np.savez(path, **{k: np.array(v) for k, v in tree_flatten(net.parameters())})


def load(path):
    from mlx.utils import tree_unflatten
    with np.load(path) as z:
        net = Net(int(z["d1.weight"].shape[-1]) - 32)
        net.update(tree_unflatten([(k, mx.array(z[k])) for k in z.files]))
    mx.eval(net.parameters())
    return net
