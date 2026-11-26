"""Auto crop that learns from the person's own crops.

The built-in auto crop (autocrop.find_picture) follows fixed rules. This learns, from crops the person
made by hand, which line along each side of a frame they cut at and how far past it they go, and then
crops new frames the same way.

What it learns from (each a small greyscale copy of the frame plus the crop):
  - crops drawn by hand on the scanning screen (kept as small files in STORE when they are made),
  - crops kept in Scanned pictures (each roll folder's Library.json; the picture is read when learning).

How: each side of a frame is looked at on its own, turned so that side is on the left. Every place a
cut could go, from the side to REACH in, gets a few numbers: how clear a straight line runs there
along the side (and just outside it, so a habit of cutting a little past a line can be learned), how
even the band outside is, how far in it is, and whether the built-in rule cuts there. A small neural
network (one hidden layer, one set of weights for all four sides) scores each place, and the best
place is the cut. It is trained by gradient descent in numpy (a few hundred crops take seconds).
(Until 2026-11-26 a plain weighted sum and a picture network (CNN, cropcnn.py) were tried beside it;
they were taken out to keep learning light: the small network was within a point of the CNN.)
What it learns is kept in models/ in the app's folder (see MODELS).

It only takes over from the built-in rule when, tried on crops it was not trained on (5-fold
cross-check), its edges land nearer the person's than the rule's do. Otherwise the rule is used.

Someone else who downloads the app gets the model in models/ as a starting point and it keeps adapting
to their own crops: it learns again starting from the shipped weights (pulled back towards them, so a
few crops cannot undo them), keeps that adapted model in their STORE (never over the shipped file) and
uses it only where it lands nearer their crops than the shipped model. Only the computer with a
models/.owner file (the person who makes the app) writes models/.

Raw scans of negatives are turned into positives first (their log brightness mirrored), so what is
learned from finished pictures in the Library also crops negatives on the scanning screen, and the
other way round. Frames are made the same size (SIDE) first, so crops on thumbnails, previews and scans
are learned from and made alike (crops are shares of the frame).
"""
import hashlib
import json
import math
import os
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from .autocrop import find_picture

# COOLSCAN_LEARN_DIR (the checks set it): samples and models kept there instead, so a check never
# reads or changes the person's own learning (their ~/.coolscan-crops or the app's models/)
_LEARN_DIR = os.environ.get("COOLSCAN_LEARN_DIR")
STORE = Path(_LEARN_DIR) / "store" if _LEARN_DIR else Path.home() / ".coolscan-crops"
# What is learned (numbers only: weights, the numbers they are scaled by, how near each model got, when
# and how long it took; no pictures, file names or crops) is kept in the app's own folder, in models/, so
# it goes to GitHub with the app. The small greyscale copies of frames and the crop steps it learns from
# stay in STORE, outside the app's folder, and never go to GitHub.
APP_MODELS = Path(__file__).resolve().parents[2] / "models"
MODELS = Path(_LEARN_DIR) / "models" if _LEARN_DIR else APP_MODELS
MODEL_FILE = "crop_model.json"
CNN_FILE = "crop_cnn.npz"    # the picture network's weights, from before it was taken out (only ever deleted now)
OLD_MODEL_FILE, OLD_CNN_FILE = "model.json", "cnn.npz"   # where they were kept before (in STORE)
OWNER_FILE = ".owner"   # in models/ on the app maker's computer only (not in git): learning there writes models/
MIN_ADAPT = 3           # someone else's crops: adapting the shipped model is tried from this many
FLIP_FILMS = ("negative", "mono", "chromo")   # raw scans of these are negatives: mirrored into positives
SIDE = 200              # a kept sample's longer side, in pixels
REACH = 0.30            # a cut is looked for this far in from each side, at most
BINS = 60               # places a cut can go on each side: every 0.5% of the frame
STEP = REACH / BINS
MIN_PICTURES = 8        # fewer crops than this: nothing is learned yet
RELEARN_EVERY = 10      # learn again by itself after this many new crops
SAMPLES_KEPT = 2000     # the oldest scanning-screen samples go beyond this
HISTORY_KEPT = 50
REJECT_WEIGHT = 0.5     # how strongly an earlier crop step moved away from counts as "not here"
STEPS_KEPT = 20000      # crop steps remembered (steps.jsonl)
AUTO_KEPT = 20000      # auto crops remembered (to leave them out of learning)
OFFSETS = (0, 1, 2, 3, 4, 6, 9)
VERSION = 1
SIDES = ("left", "top", "right", "bottom")
_TURN = {"left": 0, "top": 1, "right": 2, "bottom": 3}   # np.rot90 turns that bring the side to the left


# ------------------------------------------------------------------ pictures and crops
def small_luminance(planes, film=None, raw=False):
    """Log brightness of a frame, made SIDE on its longer side (a thumbnail, a preview and a scan of a
    frame come out alike). planes: 2-D arrays, 0..65535. A raw scan of a negative is mirrored into a
    positive (see flips)."""
    a = np.mean([np.asarray(p, np.float32) for p in planes], axis=0)
    h, w = a.shape
    f = max(1, int(math.ceil(max(h, w) / SIDE)))
    if f > 1:
        a = a[:h // f * f, :w // f * f].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    L = to_side(np.log2(np.clip(a, 0, None) + 16.0))
    return 20.0 - L if flips(film, raw) else L


def flips(film, raw):
    """Whether a frame is a negative as the scanner read it: then its log brightness is mirrored (20 - L
    keeps it in 4..16) so it looks like the finished positive, and what is learned on one crops both."""
    return bool(raw) and _film_word(film) in FLIP_FILMS


def _film_word(film):
    return {"positive": "slide", "kodachrome": "slide"}.get(film, film)


def to_side(L):
    """L resized (straight-line in between pixels) so its longer side is SIDE pixels."""
    h, w = L.shape
    s = SIDE / max(h, w)
    nh, nw = max(2, int(round(h * s))), max(2, int(round(w * s)))
    if (nh, nw) == (h, w):
        return L.astype(np.float32)
    y = (np.arange(nh) + 0.5) * h / nh - 0.5
    x = (np.arange(nw) + 0.5) * w / nw - 0.5
    y0 = np.clip(np.floor(y).astype(int), 0, h - 1); y1 = np.clip(y0 + 1, 0, h - 1); fy = np.clip(y - y0, 0, 1)[:, None]
    x0 = np.clip(np.floor(x).astype(int), 0, w - 1); x1 = np.clip(x0 + 1, 0, w - 1); fx = np.clip(x - x0, 0, 1)[None, :]
    r = L[y0] * (1 - fy) + L[y1] * fy
    return (r[:, x0] * (1 - fx) + r[:, x1] * fx).astype(np.float32)


FILMS = ("negative", "slide", "mono", "chromo")
CONTEXT_WORDS = ("colour negative", "slide", "B&W", "chromogenic B&W", "film unknown", "raw scan", "colourless")


def context_of(planes, film=None, raw=False):
    """What else the models are told about a frame: its film type (one of FILMS, or unknown), whether it
    is the raw scan (a negative as the scanner read it) or a finished picture, and whether it has no
    colour (measured, so a B&W frame is known as one even when the film type is not)."""
    flipped = flips(film, raw)   # a raw negative, mirrored into a positive: seen as a finished picture
    film = _film_word(film)
    ctx = [float(film == f) for f in FILMS] + [float(film not in FILMS), float(bool(raw) and not flipped)]
    ctx.append(_colourless(planes))
    return ctx


def _colourless(planes):
    if len(planes) < 3:
        return 1.0
    a = [np.log2(np.asarray(p, np.float32)[::4, ::4] + 16.0) for p in planes[:3]]
    spread = float(np.median(np.max(a, axis=0) - np.min(a, axis=0)))
    a = [x - np.median(x) for x in a]   # a negative's orange mask is not colour
    chroma = float(np.median(np.max(a, axis=0) - np.min(a, axis=0)))
    return float(chroma < 0.08)


def _pack(L):
    return np.clip(np.round((L - 4.0) * 21.0), 0, 255).astype(np.uint8)


def _unpack(q):
    return q.astype(np.float32) / 21.0 + 4.0


def cuts_of(rect):
    """(x0, y0, x1, y1) -> the share cut from each side, in SIDES order."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    return {"left": x0, "top": y0, "right": 1 - x1, "bottom": 1 - y1}


def rect_of(cuts):
    return (cuts["left"], cuts["top"], 1 - cuts["right"], 1 - cuts["bottom"])


# ------------------------------------------------------------------ what the model sees
def _sat(x, s):
    return x / (x + s)


def side_features(L, side, rule_cut, stats=None, context=None):
    """The numbers for every place a cut could go on one side: an array (BINS + 1, n). Row k is a cut
    of k * STEP of the frame. rule_cut: what the built-in rule cuts on this side (a share). context:
    context_of(); each part of it also goes in multiplied by a few of the place's numbers, so a weighted
    sum can learn, say, a different habit for B&W."""
    if stats is None:
        stats = _centre_stats(L)
    med, spread = stats
    V = np.rot90(L, _TURN[side])
    h, w = V.shape
    pieces = np.array_split(V, 8, axis=0)
    cols = np.arange(w, dtype=np.float32) + 0.5
    pos = np.arange(BINS + 3, dtype=np.float32) * STEP * w     # bin k sits k * STEP in from the side
    reach = min(w, int(math.ceil(pos[-1])) + 2)
    M = np.stack([np.interp(pos, cols[:reach], np.median(p[:, :reach], axis=0)) for p in pieces])
    M = (M - med) / spread
    col = np.median(M, axis=0)
    # a step at bin k: from k-1 to k+1 (on a small frame several bins share a pixel: look wider then)
    wide = max(1, int(round(1.0 / (STEP * w))))
    gp = np.zeros_like(M)
    gp[:, wide:-wide] = M[:, 2 * wide:] - M[:, :-2 * wide]
    gc = np.zeros_like(col)
    gc[wide:-wide] = col[2 * wide:] - col[:-2 * wide]
    sign = np.sign(gc)
    along = np.median(np.abs(gp) * (np.sign(gp) == sign), axis=0)        # a line along most of the side
    E = _sat(along, 0.15)
    G = _sat(np.abs(gc), 0.15)
    rk = rule_cut / STEP
    rows = []
    for k in range(BINS + 1):
        f = [E[k - o] if k - o >= 1 else 0.0 for o in OFFSETS]
        f += [G[k - 1] if k >= 1 else 0.0, G[k] if k >= 1 else 0.0]
        f.append(float(E[k + 1:k + 5].max()))                             # a line just inside: cut too soon
        if k >= 2:
            band = M[:, :k]
            f += [_sat(float(np.std(col[:k])), 0.1),                      # the band outside: even?
                  _sat(float(np.std(band.mean(axis=1))), 0.1),
                  _sat(abs(float(col[:k].mean())), 0.5)]                  # and unlike the middle of the frame
        else:
            f += [1.0, 1.0, 0.0]
        x = k / BINS
        f += [x, x * x, math.exp(-((k - rk) / 1.5) ** 2), float(k == 0), float(k == 0 and rk < 0.5)]
        key = (f[0], f[2], x, f[-3])     # a line here, a line just outside, how far in, the rule
        for c in (context if context is not None else [0.0] * len(CONTEXT_WORDS)):
            f += [c] + [c * v for v in key]
        rows.append(f)
    return np.asarray(rows, np.float32)


def _centre_stats(L):
    h, w = L.shape
    c = L[h // 4:3 * h // 4, w // 4:3 * w // 4]
    lo, mid, hi = np.percentile(c, [5, 50, 95])
    return float(mid), float(max(0.5, hi - lo))


def frame_sides(L, rule_rect, context=None):
    """All four sides' numbers for one frame: {side: array}."""
    rule = cuts_of(rule_rect) if rule_rect is not None else dict.fromkeys(SIDES, 0.0)
    stats = _centre_stats(L)
    return {s: side_features(L, s, rule[s], stats, context) for s in SIDES}


def rule_rect_of(L):
    """The built-in rule's crop on a kept sample (its log brightness turned back to levels)."""
    return find_picture([np.exp2(L) - 16.0])


# ------------------------------------------------------------------ the model
def _reject_grad(p, Rj):
    """The gradient (over the scores) of the chance given to places the person moved away from."""
    pr = (p * Rj).sum(axis=1, keepdims=True)
    return REJECT_WEIGHT * (p * Rj - p * pr)


def _fit(X, T, lam=0.03, steps=400, Rj=None, w0=None, sw=None):
    """Weights for a choice among the places on each side: X (sides, places, n), T (sides, places) the
    share each place should get (the person's cut). Softmax over the places; Adam; L2 on the weights.
    Rj (sides, places): places of earlier crop steps the person moved away from, made less likely.
    w0: weights to start from (the shipped model's); the L2 then pulls towards them, not towards 0."""
    n = X.shape[2]
    w = np.zeros(n, np.float64) if w0 is None else np.array(w0, np.float64)
    w_prior = np.zeros(n) if w0 is None else np.array(w0, np.float64)
    m = np.zeros(n)
    v = np.zeros(n)
    lr, b1, b2 = 0.05, 0.9, 0.999
    for t in range(1, steps + 1):
        s = X @ w
        s -= s.max(axis=1, keepdims=True)
        p = np.exp(s)
        p /= p.sum(axis=1, keepdims=True)
        d = p - T if Rj is None else p - T + _reject_grad(p, Rj)
        if sw is not None:   # how much each example counts (auto rotate: how surely it was checked)
            d = d * sw[:, None]
        g = np.einsum("spn,sp->n", X, d) / len(X) + lam * (w - w_prior)
        m = b1 * m + (1 - b1) * g
        v = b2 * v + (1 - b2) * g * g
        w -= lr * (m / (1 - b1 ** t)) / (np.sqrt(v / (1 - b2 ** t)) + 1e-8)
    return w


def _fit_mlp(X, T, hidden=16, lam=0.01, steps=600, seed=0, Rj=None, P0=None, sw=None):
    """The same choice made by a small neural network: each place's numbers go through one hidden layer
    (tanh) before they are scored, so it can learn habits a weighted sum cannot (such as "cut at the
    second line when the first is faint"). Plain numpy: at this size the GPU would not be faster.
    P0: weights to start from (the shipped model's), which the L2 then pulls towards."""
    rng = np.random.default_rng(seed)
    n = X.shape[2]
    if P0 is not None:
        P = {k: np.array(v, np.float64) for k, v in P0.items()}
        prior = {k: np.array(v, np.float64) for k, v in P0.items()}
    else:
        P = {"W1": rng.normal(0, 1 / math.sqrt(n), (n, hidden)), "b1": np.zeros(hidden), "w2": rng.normal(0, 0.1, hidden)}
        prior = {"W1": 0.0, "b1": 0.0, "w2": 0.0}
    M = {k: np.zeros_like(v) for k, v in P.items()}
    V = {k: np.zeros_like(v) for k, v in P.items()}
    lr, b1, b2 = 0.02, 0.9, 0.999
    S = len(X)
    for t in range(1, steps + 1):
        H = np.tanh(X @ P["W1"] + P["b1"])
        s = H @ P["w2"]
        s -= s.max(axis=1, keepdims=True)
        p = np.exp(s)
        p /= p.sum(axis=1, keepdims=True)
        dS = (p - T if Rj is None else p - T + _reject_grad(p, Rj)) / S
        if sw is not None:
            dS = dS * sw[:, None]
        dH = dS[..., None] * P["w2"] * (1 - H * H)
        G = {"w2": np.einsum("sph,sp->h", H, dS) + lam * (P["w2"] - prior["w2"]),
             "W1": np.einsum("spn,sph->nh", X, dH) + lam * (P["W1"] - prior["W1"]),
             "b1": dH.sum(axis=(0, 1))}
        for k in P:
            M[k] = b1 * M[k] + (1 - b1) * G[k]
            V[k] = b2 * V[k] + (1 - b2) * G[k] * G[k]
            P[k] -= lr * (M[k] / (1 - b1 ** t)) / (np.sqrt(V[k] / (1 - b2 ** t)) + 1e-8)
    return P


def _score(kind, params, Xs):
    if kind == "net":
        return np.tanh(Xs @ params["W1"] + params["b1"]) @ params["w2"]
    return Xs @ params


def _rejected(rejected, cuts, step=STEP, places=BINS + 1):
    """(sides, places): the places of earlier steps on each side, apart from where the side ended."""
    k = np.arange(places, dtype=np.float64)
    R = np.zeros((len(cuts), places))
    for i, (rej, c) in enumerate(zip(rejected, cuts)):
        for r in rej:
            if abs(r - c) > 1.5 * step:   # a step that ended where the final crop is: not moved away from
                R[i] = np.maximum(R[i], np.exp(-0.5 * ((k - r / step) / 0.8) ** 2))
        R[i] *= np.abs(k - c / step) > 1.5
    return R


def _targets(cuts):
    k = np.arange(BINS + 1, dtype=np.float64)
    T = np.exp(-0.5 * ((k[None, :] - np.asarray(cuts)[:, None] / STEP) / 0.8) ** 2)
    return T / T.sum(axis=1, keepdims=True)


def _pick(scores, step=STEP):
    """The cut (a share) for one side from its places' scores: the best place, between bins."""
    k = int(np.argmax(scores))
    off = 0.0
    if 0 < k < len(scores) - 1:
        a, b, c = scores[k - 1], scores[k], scores[k + 1]
        d = a - 2 * b + c
        if d < 0:
            off = max(-0.5, min(0.5, 0.5 * (a - c) / d))
    return max(0.0, (k + off) * step)


class Model:
    """A kept model: the small neural network on the numbers measured at each place."""

    def __init__(self, data, cnn_path=None):
        self.data = data
        small = data["small"]
        self.kind = small["kind"]
        if self.kind != "net" or data.get("kind") != "net":
            raise ValueError("kept by a version with other kinds of model: learn again")
        self.params = {k: np.asarray(v, np.float64) for k, v in small["w"].items()}
        self.mu = np.asarray(small["mu"], np.float64)
        self.sd = np.asarray(small["sd"], np.float64)
        self.net = None   # (no picture network any more; kept so older callers reading it still work)

    def usable(self):
        """Whether to crop with it: it did better than the rule."""
        return bool(self.data.get("use"))

    def cuts(self, L, rule_rect, context=None):
        sides = frame_sides(L, rule_rect, context)
        return {s: _pick(_score(self.kind, self.params, (X - self.mu) / self.sd)) for s, X in sides.items()}


def train(examples, cnn=False, start=None, start_cnn=None):
    """examples: [(L, user_rect, rule_rect, context[, earlier steps' rects])]. Returns (the model as a dict,
    JSON-able, with how well it did on crops it had not seen against the rule; None).
    start: a shipped Model to adapt (someone else's crops): trained again starting from its weights, and
    the result is kept only where it lands nearer these crops than the shipped model itself does.
    (cnn, start_cnn and the second result are from when there was a picture network: not used.)"""
    X, C, R, P, RJ = [], [], [], [], []   # places' numbers, the person's cut, the rule's, which picture, earlier steps
    for n, (L, user, rule, ctx, *more) in enumerate(examples):
        uc = cuts_of(user)
        earlier = [cuts_of(r) for r in (more[0] if more else [])]
        rc = cuts_of(rule) if rule is not None else dict.fromkeys(SIDES, 0.0)
        feats = frame_sides(L, rule, ctx)
        for s in SIDES:
            if uc[s] > REACH - 0.02:       # cut that far in: picked for the picture, not the border
                continue
            X.append(feats[s]); C.append(uc[s]); R.append(rc[s]); P.append(n)
            RJ.append([e[s] for e in earlier if e[s] <= REACH - 0.02])
    if not X:
        return None, None
    X = np.asarray(X, np.float64); C = np.asarray(C); R = np.asarray(R); P = np.asarray(P)
    pics = len(set(P.tolist()))
    if start is not None and len(start.mu) != X.shape[2]:
        start = None   # shipped by a version that measured other numbers: start afresh
    kind = "net"

    def fit(idx):
        ids = np.flatnonzero(idx) if np.asarray(idx).dtype == bool else np.asarray(idx)
        rj = [RJ[i] for i in ids] if any(RJ[i] for i in ids) else None
        Xi = X[idx]
        Rj = _rejected(rj, C[idx]) if rj else None
        if start is not None:   # adapting: the shipped scaling and weights, pulled back towards them
            mu, sd = start.mu, start.sd
            return _fit_mlp((Xi - mu) / sd, _targets(C[idx]), Rj=Rj, steps=250, P0=start.params), mu, sd
        mu = Xi.reshape(-1, Xi.shape[2]).mean(axis=0)
        sd = Xi.reshape(-1, Xi.shape[2]).std(axis=0)
        sd = np.where(sd < 1e-6, 1e6, sd)   # never varied in these crops (a raw scan when all were finished): left out
        return _fit_mlp((Xi - mu) / sd, _targets(C[idx]), Rj=Rj), mu, sd

    def predict(model, idx):
        params, mu, sd = model
        return np.array([_pick(_score(kind, params, (x - mu) / sd)) for x in X[idx]])

    # how near it gets on crops it was not trained on, against the rule on the same crops
    folds = min(5, pics)
    order = np.random.default_rng(0).permutation(sorted(set(P.tolist())))
    fold_of = {int(p): i % folds for i, p in enumerate(order)}
    errs = []
    for f in range(folds):
        test = np.array([fold_of[int(p)] == f for p in P])
        if test.all() or not test.any():
            continue
        errs.append(np.abs(predict(fit(~test), test) - C[test]))
    near = {kind: float(np.mean(np.concatenate(errs))) if errs else None}
    within = {kind: float(np.mean(np.concatenate(errs) <= 0.01)) if errs else None}
    rule = float(np.mean(np.abs(R - C)))
    within["rule"] = float(np.mean(np.abs(R - C) <= 0.01))
    everything = np.arange(len(X))
    if start is not None:   # the shipped model as it is, on all of these crops (it never saw them)
        sh = np.array([_pick(_score(start.kind, start.params, (x - start.mu) / start.sd)) for x in X])
        near["shipped"] = float(np.mean(np.abs(sh - C)))
        within["shipped"] = float(np.mean(np.abs(sh - C) <= 0.01))
    stepped = len(set(P[[i for i, r in enumerate(RJ) if r]].tolist())) if any(RJ) else 0
    params, mu, sd = fit(everything)
    w = {k: v.tolist() for k, v in params.items()}
    enough = pics >= (MIN_ADAPT if start is not None else MIN_PICTURES)
    bar = min(rule, near["shipped"]) if start is not None else rule   # adapted: must beat the shipped model too
    use = bool(enough and near[kind] is not None and near[kind] < bar)
    out = {"version": VERSION, "kind": kind, "small": {"kind": kind, "w": w, "mu": np.asarray(mu).tolist(), "sd": np.asarray(sd).tolist()},
           "pictures": pics, "sides": int(len(X)), "error_learned": near[kind], "error_rule": rule,
           "errors": near, "within_1pc": within, "use": use, "small_use": use,
           "cnn_tried": False, "pictures_with_steps": stepped,
           "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    if start is not None:
        out.update(adapted_from=start.data.get("made"), shipped_pictures=start.data.get("pictures"),
                   error_shipped=near["shipped"],
                   # the shipped model is no better than the rule on these crops: leave it too (enough crops to tell)
                   shipped_use=bool(pics < MIN_PICTURES or near["shipped"] < rule))
    return out, None


# ------------------------------------------------------------------ the store: samples and the model, kept between runs
def _peak_rss():
    """The app's largest memory use so far, in MB (macOS counts bytes, Linux kilobytes)."""
    try:
        import resource
        import sys
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)
    except Exception:  # noqa: BLE001
        return None


def _folder_mb(folder):
    try:
        return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file()) / 2 ** 20
    except OSError:
        return 0.0


def reset_to_shipped(learner, model_file, cnn_file):
    """Settings' "Reset to default" (crop learning and turn learning): drop what this computer learned
    from the person's own pictures, so the model that came with the app (from GitHub) is used again.
    The small copies kept to learn from stay (they have a button of their own), so it adapts again the
    next time it learns, unless learning is switched off. The app maker's computer (models/.owner)
    writes the shipped model itself: there is no other default to go back to there."""
    if learner.busy:
        return {"ok": False, "error": "It is learning just now. Try again when it is done."}
    if learner.owner():
        return {"ok": False, "owner": True,
                "error": "Nothing to reset on this Mac: it is the one that makes the app's models "
                         "(the models folder here is what goes to GitHub). On any other Mac this goes "
                         "back to the model that came from GitHub."}
    gone = 0
    for name in (model_file, cnn_file):
        try:
            (learner.store / name).unlink()
            gone += 1
        except OSError:
            continue
    try:
        state = learner._state()
        state["new"] = 0
        learner._save_state(state)
    except OSError:
        pass
    learner.last = None
    came = learner.shipped() is not None
    if not gone:
        text = "Already as it came: nothing learned from your pictures was in use."
    elif came:
        text = "Back to the model that came with the app. What was learned from your pictures is deleted."
    else:
        text = "What was learned from your pictures is deleted. No model came with the app, so the built-in rule is used."
    return {"ok": True, "reset": gone > 0, "shipped": came, "text": text}


class CropLearner:
    """The samples, the model and learning, for the app. All of it in `store` (STORE by default)."""

    def __init__(self, store=None, models=None):
        """store: the samples and steps (STORE). models: where what is learned is kept (MODELS, the app's
        models/ folder; with another store, that store unless given)."""
        self.store = Path(store) if store else STORE
        self.models = Path(models) if models else (MODELS if store is None else self.store)
        self.lock = threading.Lock()
        self._read_cache = {}
        self.busy = False
        self.last = None   # what the last learning said
        self.uses = {"n": 0, "t": 0.0}   # crops made with what it learned since the app started, and their time

    # -- samples
    def add_hand_crop(self, key, planes, rect, film=None, raw=True):
        """Keep a crop drawn by hand on the scanning screen. key: one per frame of a strip (a newer crop
        of the same frame replaces the older). Returns how many new crops wait to be learned from."""
        L = small_luminance(planes, film, raw)   # a raw negative: mirrored into a positive
        self.store.mkdir(exist_ok=True)
        name = "hand-" + hashlib.sha1(str(key).encode()).hexdigest()[:16] + ".npz"
        tmp = self.store / (name + ".part.npz")
        np.savez_compressed(tmp, L=_pack(L), rect=np.asarray(rect, np.float32), made=np.asarray(time.time()),
                            step_key=np.asarray("scan:" + str(key)), positive=np.asarray(1),
                            context=np.asarray(context_of(planes, film, raw), np.float32))
        tmp.replace(self.store / name)
        hand = sorted(self.store.glob("hand-*.npz"), key=lambda p: p.stat().st_mtime)
        for old in hand[:-SAMPLES_KEPT]:
            old.unlink()
        return self._note_new()

    # -- every crop step: auto crop's box, each crop kept, so the way to the final crop is known
    def note_step(self, key, rect, kind, name=""):
        """kind: "auto" (auto crop's crop), "saved" (kept in the Library) or "hand" (scanning screen)."""
        self.store.mkdir(exist_ok=True)
        line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "key": str(key), "kind": kind, "name": str(name),
                           "rect": [round(float(v), 4) for v in rect]})
        p = self.store / "steps.jsonl"
        with self.lock:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            try:
                if p.stat().st_size > STEPS_KEPT * 160:
                    lines = p.read_text(encoding="utf-8").splitlines()
                    if len(lines) > STEPS_KEPT:
                        p.write_text("\n".join(lines[-STEPS_KEPT // 2:]) + "\n", encoding="utf-8")
            except OSError:
                pass

    def steps(self):
        """{key: [step, ...]} oldest first."""
        out = {}
        try:
            lines = (self.store / "steps.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                st = json.loads(line)
                out.setdefault(st["key"], []).append(st)
            except (ValueError, KeyError, TypeError):
                continue
        return out

    @staticmethod
    def earlier_of(steps, final):
        """The rects of the steps before the final crop that differ from it (steps since the final crop was
        last reached are what led to it; a crop later set again from scratch starts its own way)."""
        out = []
        for st in steps:
            r = st.get("rect")
            if r and max(abs(float(a) - float(b)) for a, b in zip(r, final)) > 0.004:
                out.append(tuple(float(v) for v in r))
        return out[-8:]

    # -- crops auto crop made in the Library: kept unchanged, they are not the person's
    def _auto(self):
        try:
            return json.loads((self.store / "auto.json").read_text())
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _path_key(path):
        return hashlib.sha1(str(path).encode()).hexdigest()[:20]

    def note_auto(self, path, rect):
        auto = self._auto()
        auto.pop(self._path_key(path), None)
        auto[self._path_key(path)] = [round(float(v), 4) for v in rect]
        while len(auto) > AUTO_KEPT:
            auto.pop(next(iter(auto)))
        self.store.mkdir(exist_ok=True)
        (self.store / "auto.json").write_text(json.dumps(auto))

    def is_auto(self, path, rect, auto=None):
        got = (auto if auto is not None else self._auto()).get(self._path_key(path))
        return got is not None and max(abs(float(a) - float(b)) for a, b in zip(got, rect)) <= 0.002

    def drop_hand_crop(self, key):
        p = self.store / ("hand-" + hashlib.sha1(str(key).encode()).hexdigest()[:16] + ".npz")
        if p.exists():
            p.unlink()

    def _note_new(self):
        state = self._state()
        state["new"] = int(state.get("new", 0)) + 1
        self._save_state(state)
        return state["new"]

    def note_library_crop(self):
        return self._note_new()

    def _state(self):
        try:
            return json.loads((self.store / "state.json").read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        self.store.mkdir(exist_ok=True)
        (self.store / "state.json").write_text(json.dumps(state))

    def hand_examples(self):
        out = []
        steps = self.steps()
        for p in sorted(self.store.glob("hand-*.npz")):
            try:
                with np.load(p) as z:
                    rect = tuple(float(v) for v in z["rect"])
                    key = str(z["step_key"]) if "step_key" in z.files else None
                    L, ctx = to_side(_unpack(z["L"])), [float(v) for v in z["context"]]
                    if "positive" not in z.files and ctx[5] and (ctx[0] or ctx[2] or ctx[3]):
                        L, ctx[5] = 20.0 - L, 0.0   # kept before negatives were mirrored: mirror it now
                    out.append((L, rect, ctx, self.earlier_of(steps.get(key, []), rect) if key else []))
            except (OSError, ValueError, KeyError):
                continue
        return out

    def library_examples(self, base, read_small, apply_edit, tidy_edit, film_of=None, meta_name="Library.json"):
        """The crops kept in the Library under `base`: [(L, rect, context)], each picture read small once
        and kept (by its path, size and time) in STORE so learning again is quick. film_of(path) ->
        (film or None, raw scan or not)."""
        out = []
        base = Path(base)
        if not base.is_dir():
            return out
        cache = self.store / "library"
        auto = self._auto()
        steps = self.steps()
        used = set()
        for meta in [base / meta_name, *base.glob("*/" + meta_name), *base.glob("*/*/" + meta_name)]:
            try:
                pics = json.loads(meta.read_text(encoding="utf-8")).get("pictures") or {}
            except (OSError, ValueError, AttributeError):
                continue
            for rel, m in pics.items():
                e = tidy_edit((m or {}).get("edit")) if isinstance(m, dict) else None
                if not e or not e.get("crop") or str(rel).startswith("RAW/"):
                    continue
                path = meta.parent / rel
                try:
                    st = path.stat()
                except OSError:
                    continue
                if self.is_auto(path, e["crop"], auto):
                    continue   # auto crop's own crop, kept as it was: not the person's

                tag = hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}|{e['turns']}|{SIDE}".encode()).hexdigest()[:20]
                kept = cache / (tag + ".npz")
                used.add(kept.name)
                try:
                    with np.load(kept) as z:
                        L, ctx = _unpack(z["L"]), [float(v) for v in z["context"]]
                except (OSError, ValueError, KeyError):
                    try:
                        planes, _ = read_small(path, SIDE * 2)
                        planes = [p * 65535.0 for p in apply_edit(planes, {"turns": e["turns"], "crop": None}, crop=False)]
                        L = _unpack(_pack(small_luminance(planes)))   # as it is kept
                        film, raw = film_of(path) if film_of else (None, False)
                        ctx = context_of(planes, film, raw)
                        cache.mkdir(parents=True, exist_ok=True)
                        np.savez_compressed(kept, L=_pack(L), context=np.asarray(ctx, np.float32))
                    except Exception:  # noqa: BLE001 - a picture that cannot be read is left out
                        continue
                out.append((L, tuple(e["crop"]), ctx, self.earlier_of(steps.get("lib:" + self._path_key(path), []), e["crop"])))
        for old in cache.glob("*.npz") if cache.is_dir() else ():
            if old.name not in used:   # a picture since changed, gone, or no longer cropped
                try:
                    old.unlink()
                except OSError:
                    pass
        return out

    def delete_pictures(self):
        """Delete the small greyscale copies kept to learn from (scanning-screen crops and the Library's
        cache). What was learned stays. Library crops are read from the Library again when it next
        learns; scanning-screen crops are gone for good. Returns how many files and MB."""
        n, mb = 0, 0.0
        for p in [*self.store.glob("hand-*.npz"), *(self.store / "library").glob("*.npz")]:
            try:
                mb += p.stat().st_size / 2 ** 20
                p.unlink()
                n += 1
            except OSError:
                continue
        return {"ok": True, "deleted": n, "mb": round(mb, 2)}

    def reset_to_shipped(self):
        return reset_to_shipped(self, MODEL_FILE, CNN_FILE)

    def pictures_kept(self):
        files = [*self.store.glob("hand-*.npz"), *(self.store / "library").glob("*.npz")]
        return {"files": len(files), "mb": round(sum(p.stat().st_size for p in files if p.exists()) / 2 ** 20, 2)}

    # -- whose model: the app maker's computer writes models/; anyone else adapts it in their STORE
    def owner(self):
        return self.models == self.store or (self.models / OWNER_FILE).exists()

    def _where(self):
        """Where learning writes: models/ for the owner, the STORE (adapted model) for anyone else."""
        return self.models if self.owner() else self.store

    def _read(self, p, cnn):
        """A kept model file, read again only when it changes; None when there is none."""
        try:
            st = p.stat()
        except OSError:
            self._read_cache.pop(str(p), None)
            return None
        tag = (st.st_mtime, st.st_size)
        got = self._read_cache.get(str(p))
        if got is None or got[0] != tag:
            try:
                data = json.loads(p.read_text())
                m = Model(data, cnn) if data.get("version") == VERSION else None
            except (OSError, ValueError, KeyError):
                m = None
            got = (tag, m)
            self._read_cache[str(p)] = got
        return got[1]

    def shipped(self):
        """The model in models/ (shipped with the app), or None."""
        return self._read(self.models / MODEL_FILE, self.models / CNN_FILE)

    def adapted(self):
        """This person's adapted model (not the owner), when it was made from the shipped one there now."""
        if self.owner():
            return None
        m = self._read(self.store / MODEL_FILE, self.store / CNN_FILE)
        sh = self.shipped()
        if m is None or m.data.get("adapted_from", "-") != (sh.data.get("made") if sh else None):
            return None
        return m

    # -- learning
    def learn(self, examples, why="button"):
        """Learn from [(L, rect, context, earlier steps)] and keep the model. Returns what to tell the person, with what learning
        took (time, processor time, memory, GPU memory) and how near each model got."""
        with self.lock:
            if self.busy:
                return {"ok": False, "error": "Already learning."}
            self.busy = True
        try:
            t0, c0 = time.time(), time.process_time()
            rss0 = _peak_rss()
            rows = [(L, rect, rule_rect_of(L), ctx, *more) for L, rect, ctx, *more in examples]
            start = None if self.owner() else self.shipped()
            model, _ = train(rows, start=start) if rows else (None, None)
            state = self._state()
            state["new"] = 0
            self._save_state(state)
            if model is None:
                self.last = {"ok": True, "pictures": 0, "use": False, "text": self.describe(None, len(rows))}
                return self.last
            used = {"seconds": round(time.time() - t0, 2), "cpu_seconds": round(time.process_time() - c0, 2),
                    "memory_mb": _peak_rss(), "memory_added_mb": max(0.0, round(_peak_rss() - rss0, 1)),
                    "gpu_memory_mb": None, "device": "processor (numpy)"}
            model.update(used=used, why=why, examples=len(rows))
            where = self._where()
            where.mkdir(parents=True, exist_ok=True)
            if (where / CNN_FILE).exists():
                (where / CNN_FILE).unlink()   # an older picture network no longer in use
            tmp = where / (MODEL_FILE + ".part")
            tmp.write_text(json.dumps(model, indent=1))
            tmp.replace(where / MODEL_FILE)
            self._add_history(model)
            self.last = {"ok": True, **{k: model[k] for k in ("pictures", "use", "error_learned", "error_rule", "made")},
                         "used": used, "text": self.describe(model)}
            return self.last
        finally:
            self.busy = False

    def _add_history(self, model):
        """A line per time it learned (the last HISTORY_KEPT), to see it getting better as crops are added."""
        p = self.store / "history.json"
        try:
            runs = json.loads(p.read_text())
        except (OSError, ValueError):
            runs = []
        runs.append({k: model.get(k) for k in ("made", "why", "pictures", "sides", "kind", "use", "errors", "within_1pc",
                                               "error_rule", "used", "cnn_tried", "adapted_from", "error_shipped")})
        p.write_text(json.dumps(runs[-HISTORY_KEPT:]))

    def steps_report(self, recent=8):
        """The crop steps for the report: how many pictures took more than one step, and which way the
        final crop differs from auto crop's on each side (+ = cut further in than auto crop did)."""
        steps = self.steps()
        corr = {s: [] for s in SIDES}
        pics = []
        for key, sts in steps.items():
            saved = [x for x in sts if x["kind"] in ("saved", "hand")]
            if not saved:
                continue
            final = saved[-1]
            autos = [x for x in sts if x["kind"] == "auto" and x["t"] <= final["t"]]
            if autos:
                a, f = cuts_of(autos[-1]["rect"]), cuts_of(final["rect"])
                for s in SIDES:
                    corr[s].append(f[s] - a[s])
            pics.append({"name": final.get("name") or "", "t": final["t"], "steps": len(sts),
                         "from_auto": bool(autos), "kinds": [x["kind"] for x in sts][-12:]})
        pics.sort(key=lambda x: x["t"])
        moved = {s: (round(float(np.mean(v)), 4) if v else None) for s, v in corr.items()}
        return {"pictures": len(pics), "steps": sum(x["steps"] for x in pics),
                "several": sum(1 for x in pics if x["steps"] > 1), "from_auto": len(corr["left"]),
                "auto_moved": moved, "recent": pics[-recent:][::-1]}

    def history(self):
        try:
            return json.loads((self.store / "history.json").read_text())
        except (OSError, ValueError):
            return []

    def report(self):
        """Everything for the page's learning report."""
        m = self.model()
        uses = self.uses
        return {"ok": True, **self.status(), "model": ({k: v for k, v in m.data.items() if k not in ("small",)} if m else None),
                "running_kind": (m.kind if m else None),
                "steps": self.steps_report(), "history": self.history(), "store": str(self.store).replace(str(Path.home()), "~", 1),
                "model_file": self._short(self._where() / MODEL_FILE), "store_mb": round(_folder_mb(self.store), 2),
                "role": self.role(), "pictures_kept": self.pictures_kept(),
                "hand_crops": len(list(self.store.glob("hand-*.npz"))),
                "cnn_available": False,
                "crops_made": uses["n"], "ms_per_crop": round(1000 * uses["t"] / uses["n"], 1) if uses["n"] else None}

    def model(self):
        """The model auto crop uses: on the owner's computer the one in models/; for anyone else their
        adapted model where it beat the shipped one, else the shipped one (or neither, when on their crops
        the shipped one did no better than the rule). Before models/ was used it was kept in STORE as
        model.json: read from there until it learns again."""
        sh = self.shipped()
        if sh is None:
            sh = self._read(self.store / OLD_MODEL_FILE, self.store / OLD_CNN_FILE)
        ad = self.adapted()
        if ad is None:
            return sh
        if ad.usable() or not ad.data.get("shipped_use", True):
            return ad      # better than the shipped model on their crops (or, not usable, the rule then crops)
        return sh

    def role(self):
        """Who learns here, for the report."""
        sh = self.shipped()
        if self.owner():
            return {"owner": True, "text": "This computer's learning is kept in the app's models/ folder (it goes to GitHub with the app)."}
        ad = self.adapted()
        out = {"owner": False, "shipped": ({k: sh.data.get(k) for k in ("made", "pictures", "kind", "error_learned")} if sh else None),
               "adapted": ({k: ad.data.get(k) for k in ("made", "pictures", "use", "error_learned", "error_shipped", "error_rule")} if ad else None)}
        if sh is None:
            out["text"] = "No model came with the app: it learns from your crops alone (kept in " + self._short(self.store) + ")."
        elif ad is None:
            out["text"] = f"Starts from the model that came with the app ({sh.data.get('pictures')} crops). It adapts to your crops after {MIN_ADAPT}."
        elif ad.usable():
            out["text"] = (f"Adapted to your crops ({ad.data['pictures']}) from the model that came with the app: on your crops it was "
                           f"{ad.data['error_learned'] * 100:.1f}% from them, the app's model {ad.data['error_shipped'] * 100:.1f}%. Your version is used.")
        else:
            out["text"] = (f"Tried adapting to your crops ({ad.data['pictures']}); not better yet than the model that came with the app, "
                           "which is used" + ("." if ad.data.get("shipped_use", True) else " (no, it was no better than the built-in rule either: the rule crops)."))
        return out

    @staticmethod
    def _short(p):
        return str(p).replace(str(Path.home()), "~", 1)

    def status(self):
        m = self.model()
        state = self._state()
        return {"busy": self.busy, "pictures": m.data["pictures"] if m else 0, "use": bool(m and m.usable()),
                "new": int(state.get("new", 0)), "made": m.data.get("made") if m else None,
                "owner": self.owner(),
                "text": "Learning from your crops…" if self.busy else self.describe(m.data if m else None)}

    @staticmethod
    def describe(model, pictures=0):
        if not model:
            if pictures:
                return f"{pictures} crop(s) found, but none it can learn from yet."
            return f"Nothing learned yet. Crop at least {MIN_PICTURES} frames by hand (here or in Scanned pictures), then press Learn."
        n, el, er = model["pictures"], model.get("error_learned"), model.get("error_rule")
        if model.get("adapted_from") is not None and el is not None and model.get("error_shipped") is not None:
            es = model["error_shipped"]
            head = (f"Adapted the app's model to your {n} crops ({model['made'][:16]}): on crops it had not seen its edges were "
                    f"{el * 100:.1f}% from yours; the app's model {es * 100:.1f}%, the built-in rule {er * 100:.1f}%.")
            if model.get("use"):
                return head + " Auto crop uses your adapted version."
            if model.get("shipped_use", True):
                return head + " Not better yet, so auto crop keeps the app's model."
            return head + " Neither beats the rule on your crops yet, so auto crop keeps the built-in rule."
        if n < MIN_PICTURES:
            return f"Learned from {n} crop(s); it needs {MIN_PICTURES - n} more before it is used. Until then the built-in rule crops."
        near = f"On crops it had not seen, its edges were {el * 100:.1f}% of the frame from yours; the built-in rule's {er * 100:.1f}%."
        if model.get("use"):
            return f"Learned from {n} crops ({model['made'][:16]}). {near} Auto crop uses what it learned."
        return f"Learned from {n} crops ({model['made'][:16]}). {near} That is no better, so auto crop keeps the built-in rule for now."

    def due(self):
        """New crops enough to learn again by itself."""
        return not self.busy and int(self._state().get("new", 0)) >= RELEARN_EVERY

    # -- using it
    def crop(self, planes, rule_rect, film=None, raw=True):
        """The learned crop for a frame (x0, y0, x1, y1), or None to keep the rule's. planes: 0..65535."""
        m = self.model()
        if m is None or not m.usable():
            return None
        try:
            t0 = time.time()
            L = small_luminance(planes, film, raw)   # a raw negative: mirrored, like the crops it learned from
            if min(np.asarray(planes[0]).shape) < 24:
                return None
            rule = rule_rect_of(L) or rule_rect   # the rule's crop measured as it was when learning
            cuts = m.cuts(L, rule, context_of(planes, film, raw))
            self.uses["n"] += 1
            self.uses["t"] += time.time() - t0
        except Exception:  # noqa: BLE001 - an extra: the rule is still there
            traceback.print_exc()
            return None
        x0, y0, x1, y1 = rect_of(cuts)
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return None
        return tuple(round(v, 4) for v in (x0, y0, x1, y1))
