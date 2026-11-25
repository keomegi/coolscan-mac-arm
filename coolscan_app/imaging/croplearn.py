"""Auto crop that learns from the person's own crops.

The built-in auto crop (autocrop.find_picture) follows fixed rules. This learns, from crops the person
made by hand, which line along each side of a frame they cut at and how far past it they go, and then
crops new frames the same way.

What it learns from (each a small greyscale copy of the frame plus the crop):
  - crops drawn by hand on the scanning screen (kept as small files in STORE when they are made),
  - crops kept in the Library (each roll folder's Library.json; the picture is read when learning).

How: each side of a frame is looked at on its own, turned so that side is on the left. Every place a
cut could go, from the side to REACH in, gets a few numbers: how clear a straight line runs there
along the side (and just outside it, so a habit of cutting a little past a line can be learned), how
even the band outside is, how far in it is, and whether the built-in rule cuts there. Two small models
score each place (one set of weights for all four sides), and the best place is the cut: a weighted
sum of those numbers, and a small neural network (one hidden layer) that can learn habits a weighted
sum cannot. Both are trained by gradient descent in numpy (a few hundred crops take seconds) and the
one that lands nearer the person's crops on crops it was not trained on is kept. What it learns is one small JSON file, MODEL_FILE in STORE.

It only takes over from the built-in rule when, tried on crops it was not trained on (5-fold
cross-check), its edges land nearer the person's than the rule's do. Otherwise the rule is used.
"""
import hashlib
import json
import math
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from . import cropcnn
from .autocrop import find_picture

STORE = Path.home() / ".coolscan-crops"
MODEL_FILE = "model.json"
CNN_FILE = "cnn.npz"         # the picture network's weights, when it is the one kept
SIDE = 200              # a kept sample's longer side, in pixels
REACH = 0.30            # a cut is looked for this far in from each side, at most
BINS = 60               # places a cut can go on each side: every 0.5% of the frame
STEP = REACH / BINS
MIN_PICTURES = 8        # fewer crops than this: nothing is learned yet
RELEARN_EVERY = 10      # learn again by itself after this many new crops
SAMPLES_KEPT = 2000     # the oldest scanning-screen samples go beyond this
HISTORY_KEPT = 50
OFFSETS = (0, 1, 2, 3, 4, 6, 9)
VERSION = 1
SIDES = ("left", "top", "right", "bottom")
_TURN = {"left": 0, "top": 1, "right": 2, "bottom": 3}   # np.rot90 turns that bring the side to the left


# ------------------------------------------------------------------ pictures and crops
def small_luminance(planes):
    """Log brightness of a frame, made small (longer side about SIDE). planes: 2-D arrays, 0..65535."""
    a = np.mean([np.asarray(p, np.float32) for p in planes], axis=0)
    h, w = a.shape
    f = max(1, int(math.ceil(max(h, w) / SIDE)))
    if f > 1:
        a = a[:h // f * f, :w // f * f].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    return np.log2(np.clip(a, 0, None) + 16.0)


FILMS = ("negative", "slide", "mono", "chromo")
CONTEXT_WORDS = ("colour negative", "slide", "B&W", "chromogenic B&W", "film unknown", "raw scan", "colourless")


def context_of(planes, film=None, raw=False):
    """What else the models are told about a frame: its film type (one of FILMS, or unknown), whether it
    is the raw scan (a negative as the scanner read it) or a finished picture, and whether it has no
    colour (measured, so a B&W frame is known as one even when the film type is not)."""
    film = {"positive": "slide", "kodachrome": "slide"}.get(film, film)
    ctx = [float(film == f) for f in FILMS] + [float(film not in FILMS), float(bool(raw))]
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
def _fit(X, T, lam=0.03, steps=400):
    """Weights for a choice among the places on each side: X (sides, places, n), T (sides, places) the
    share each place should get (the person's cut). Softmax over the places; Adam; L2 on the weights."""
    n = X.shape[2]
    w = np.zeros(n, np.float64)
    m = np.zeros(n)
    v = np.zeros(n)
    lr, b1, b2 = 0.05, 0.9, 0.999
    for t in range(1, steps + 1):
        s = X @ w
        s -= s.max(axis=1, keepdims=True)
        p = np.exp(s)
        p /= p.sum(axis=1, keepdims=True)
        g = np.einsum("spn,sp->n", X, p - T) / len(X) + lam * w
        m = b1 * m + (1 - b1) * g
        v = b2 * v + (1 - b2) * g * g
        w -= lr * (m / (1 - b1 ** t)) / (np.sqrt(v / (1 - b2 ** t)) + 1e-8)
    return w


def _fit_mlp(X, T, hidden=16, lam=0.01, steps=600, seed=0):
    """The same choice made by a small neural network: each place's numbers go through one hidden layer
    (tanh) before they are scored, so it can learn habits a weighted sum cannot (such as "cut at the
    second line when the first is faint"). Plain numpy: at this size the GPU would not be faster."""
    rng = np.random.default_rng(seed)
    n = X.shape[2]
    P = {"W1": rng.normal(0, 1 / math.sqrt(n), (n, hidden)), "b1": np.zeros(hidden), "w2": rng.normal(0, 0.1, hidden)}
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
        dS = (p - T) / S
        dH = dS[..., None] * P["w2"] * (1 - H * H)
        G = {"w2": np.einsum("sph,sp->h", H, dS) + lam * P["w2"],
             "W1": np.einsum("spn,sph->nh", X, dH) + lam * P["W1"],
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
    """A kept model: the small one (weighted sum or neural network on measured numbers) and, when it was
    the best, the picture network (its weights in a file beside the JSON)."""

    def __init__(self, data, cnn_path=None):
        self.data = data
        small = data["small"]
        self.kind = small["kind"]
        w = small["w"]
        self.params = {k: np.asarray(v, np.float64) for k, v in w.items()} if self.kind == "net" else np.asarray(w, np.float64)
        self.mu = np.asarray(small["mu"], np.float64)
        self.sd = np.asarray(small["sd"], np.float64)
        self.net = None
        if data.get("kind") == "cnn" and cnn_path is not None and cropcnn.available():
            try:
                self.net = cropcnn.load(cnn_path)
            except Exception:  # noqa: BLE001 - the small model is still there
                traceback.print_exc()

    def usable(self):
        """Whether to crop with it: it did better than the rule (the small model alone did, when the
        picture network is the best but cannot run here)."""
        if self.data.get("kind") == "cnn" and self.net is None:
            return bool(self.data.get("small_use"))
        return bool(self.data.get("use"))

    def cuts(self, L, rule_rect, context=None):
        if self.net is not None:
            strips = np.stack([s for _, s in _side_strips(L, rule_rect, context)])
            got = cropcnn.scores(self.net, strips)
            return {side: _pick(sc, cropcnn.COL) for side, sc in zip(SIDES, got)}
        sides = frame_sides(L, rule_rect, context)
        return {s: _pick(_score(self.kind, self.params, (X - self.mu) / self.sd)) for s, X in sides.items()}


def _side_strips(L, rule_rect, context=None):
    rule = cuts_of(rule_rect) if rule_rect is not None else dict.fromkeys(SIDES, 0.0)
    med, spread = _centre_stats(L)
    ctx = context if context is not None else [0.0] * len(CONTEXT_WORDS)
    return [(s, cropcnn.strip(np.rot90(L, _TURN[s]), med, spread, rule[s], ctx)) for s in SIDES]


def train(examples, cnn=True):
    """examples: [(L, user_rect, rule_rect, context)]. Returns (the model as a dict, JSON-able, with how well each
    kind did on crops it had not seen against the rule; the picture network when it is the one kept)."""
    X, C, R, P, ST = [], [], [], [], []   # places' numbers, the person's cut, the rule's, which picture, strips
    for n, (L, user, rule, ctx) in enumerate(examples):
        uc = cuts_of(user)
        rc = cuts_of(rule) if rule is not None else dict.fromkeys(SIDES, 0.0)
        feats = frame_sides(L, rule, ctx)
        strips = dict(_side_strips(L, rule, ctx)) if cnn and cropcnn.available() else {}
        for s in SIDES:
            if uc[s] > REACH - 0.02:       # cut that far in: picked for the picture, not the border
                continue
            X.append(feats[s]); C.append(uc[s]); R.append(rc[s]); P.append(n)
            if strips:
                ST.append(strips[s])
    if not X:
        return None, None
    X = np.asarray(X, np.float64); C = np.asarray(C); R = np.asarray(R); P = np.asarray(P)
    ST = np.asarray(ST, np.float32) if ST else None
    pics = len(set(P.tolist()))
    kinds = ["simple", "net"] + (["cnn"] if ST is not None and pics >= cropcnn.MIN_PICTURES else [])

    def fit(kind, idx):
        if kind == "cnn":
            return cropcnn.fit(ST[idx], C[idx]), None, None
        Xi = X[idx]
        mu = Xi.reshape(-1, Xi.shape[2]).mean(axis=0)
        sd = Xi.reshape(-1, Xi.shape[2]).std(axis=0) + 1e-6
        return {"simple": _fit, "net": _fit_mlp}[kind]((Xi - mu) / sd, _targets(C[idx])), mu, sd

    def predict(kind, model, idx):
        params, mu, sd = model
        if kind == "cnn":
            return np.array([_pick(sc, cropcnn.COL) for sc in cropcnn.scores(params, ST[idx])])
        return np.array([_pick(_score(kind, params, (x - mu) / sd)) for x in X[idx]])

    # how near each kind gets on crops it was not trained on, against the rule on the same crops
    folds = min(5, pics)
    order = np.random.default_rng(0).permutation(sorted(set(P.tolist())))
    fold_of = {int(p): i % folds for i, p in enumerate(order)}
    errs = {k: [] for k in kinds}
    for f in range(folds):
        test = np.array([fold_of[int(p)] == f for p in P])
        if test.all() or not test.any():
            continue
        for kind in kinds:
            errs[kind].append(np.abs(predict(kind, fit(kind, ~test), test) - C[test]))
    near = {k: (float(np.mean(np.concatenate(v))) if v else None) for k, v in errs.items()}
    within = {k: (float(np.mean(np.concatenate(v) <= 0.01)) if v else None) for k, v in errs.items()}
    rule = float(np.mean(np.abs(R - C)))
    within["rule"] = float(np.mean(np.abs(R - C) <= 0.01))
    # a bigger model only when it did clearly better (by a tenth) than the smaller one: it is easier to fool
    small = "net" if near["net"] is not None and near["simple"] is not None and near["net"] < 0.9 * near["simple"] else "simple"
    kind = "cnn" if near.get("cnn") is not None and near[small] is not None and near["cnn"] < 0.9 * near[small] else small
    everything = np.arange(len(X))
    params, mu, sd = fit(small, everything)
    w = {k: v.tolist() for k, v in params.items()} if small == "net" else params.tolist()
    net = fit("cnn", everything)[0] if kind == "cnn" else None
    enough = pics >= MIN_PICTURES
    return {"version": VERSION, "kind": kind, "small": {"kind": small, "w": w, "mu": mu.tolist(), "sd": sd.tolist()},
            "pictures": pics, "sides": int(len(X)), "error_learned": near[kind], "error_rule": rule,
            "errors": near, "within_1pc": within, "use": bool(enough and near[kind] is not None and near[kind] < rule),
            "small_use": bool(enough and near[small] is not None and near[small] < rule),
            "cnn_tried": "cnn" in kinds,
            "made": time.strftime("%Y-%m-%d %H:%M:%S")}, net


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


def _gpu_peak_mb():
    if not cropcnn.available():
        return None
    try:
        return round(cropcnn.mx.get_peak_memory() / 2 ** 20, 1)
    except Exception:  # noqa: BLE001
        return None


def _folder_mb(folder):
    try:
        return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file()) / 2 ** 20
    except OSError:
        return 0.0


class CropLearner:
    """The samples, the model and learning, for the app. All of it in `store` (STORE by default)."""

    def __init__(self, store=None):
        self.store = Path(store) if store else STORE
        self.lock = threading.Lock()
        self._model = None
        self._model_tag = None
        self.busy = False
        self.last = None   # what the last learning said
        self.uses = {"n": 0, "t": 0.0}   # crops made with what it learned since the app started, and their time

    # -- samples
    def add_hand_crop(self, key, planes, rect, film=None, raw=True):
        """Keep a crop drawn by hand on the scanning screen. key: one per frame of a strip (a newer crop
        of the same frame replaces the older). Returns how many new crops wait to be learned from."""
        L = small_luminance(planes)
        self.store.mkdir(exist_ok=True)
        name = "hand-" + hashlib.sha1(str(key).encode()).hexdigest()[:16] + ".npz"
        tmp = self.store / (name + ".part.npz")
        np.savez_compressed(tmp, L=_pack(L), rect=np.asarray(rect, np.float32), made=np.asarray(time.time()),
                            context=np.asarray(context_of(planes, film, raw), np.float32))
        tmp.replace(self.store / name)
        hand = sorted(self.store.glob("hand-*.npz"), key=lambda p: p.stat().st_mtime)
        for old in hand[:-SAMPLES_KEPT]:
            old.unlink()
        return self._note_new()

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
        for p in sorted(self.store.glob("hand-*.npz")):
            try:
                with np.load(p) as z:
                    out.append((_unpack(z["L"]), tuple(float(v) for v in z["rect"]), [float(v) for v in z["context"]]))
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
                tag = hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}|{e['turns']}".encode()).hexdigest()[:20]
                kept = cache / (tag + ".npz")
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
                out.append((L, tuple(e["crop"]), ctx))
        return out

    # -- learning
    def learn(self, examples, why="button"):
        """Learn from [(L, rect, context)] and keep the model. Returns what to tell the person, with what learning
        took (time, processor time, memory, GPU memory) and how near each model got."""
        with self.lock:
            if self.busy:
                return {"ok": False, "error": "Already learning."}
            self.busy = True
        try:
            t0, c0 = time.time(), time.process_time()
            rss0 = _peak_rss()
            if cropcnn.available():
                try:
                    cropcnn.mx.reset_peak_memory()
                except Exception:  # noqa: BLE001 - older MLX: not measured
                    pass
            rows = [(L, rect, rule_rect_of(L), ctx) for L, rect, ctx in examples]
            model, net = train(rows) if rows else (None, None)
            state = self._state()
            state["new"] = 0
            self._save_state(state)
            if model is None:
                self.last = {"ok": True, "pictures": 0, "use": False, "text": self.describe(None, len(rows))}
                return self.last
            used = {"seconds": round(time.time() - t0, 2), "cpu_seconds": round(time.process_time() - c0, 2),
                    "memory_mb": _peak_rss(), "memory_added_mb": max(0.0, round(_peak_rss() - rss0, 1)),
                    "gpu_memory_mb": _gpu_peak_mb(), "device": cropcnn.device() if cropcnn.available() else "processor (numpy)"}
            model.update(used=used, why=why, examples=len(rows))
            self.store.mkdir(exist_ok=True)
            if net is not None:
                cropcnn.save(net, self.store / "cnn.part.npz")
                (self.store / "cnn.part.npz").replace(self.store / CNN_FILE)
            tmp = self.store / (MODEL_FILE + ".part")
            tmp.write_text(json.dumps(model))
            tmp.replace(self.store / MODEL_FILE)
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
                                               "error_rule", "used", "cnn_tried")})
        p.write_text(json.dumps(runs[-HISTORY_KEPT:]))

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
                "running_kind": (("cnn" if m.net is not None else m.kind) if m else None),
                "history": self.history(), "store": str(self.store).replace(str(Path.home()), "~", 1), "store_mb": round(_folder_mb(self.store), 2),
                "hand_crops": len(list(self.store.glob("hand-*.npz"))),
                "cnn_available": cropcnn.available(),
                "crops_made": uses["n"], "ms_per_crop": round(1000 * uses["t"] / uses["n"], 1) if uses["n"] else None}

    def model(self):
        """The kept model, read again when the file changes; None when there is none."""
        p = self.store / MODEL_FILE
        try:
            st = p.stat()
        except OSError:
            self._model, self._model_tag = None, None
            return None
        tag = (st.st_mtime, st.st_size)
        if tag != self._model_tag:
            try:
                data = json.loads(p.read_text())
                self._model = Model(data, self.store / CNN_FILE) if data.get("version") == VERSION else None
            except (OSError, ValueError, KeyError):
                self._model = None
            self._model_tag = tag
        return self._model

    def status(self):
        m = self.model()
        state = self._state()
        return {"busy": self.busy, "pictures": m.data["pictures"] if m else 0, "use": bool(m and m.usable()),
                "new": int(state.get("new", 0)), "made": m.data.get("made") if m else None,
                "text": "Learning from your crops…" if self.busy else self.describe(m.data if m else None)}

    @staticmethod
    def describe(model, pictures=0):
        if not model:
            if pictures:
                return f"{pictures} crop(s) found, but none it can learn from yet."
            return f"Nothing learned yet. Crop at least {MIN_PICTURES} frames by hand (here or in the Library), then press Learn."
        n, el, er = model["pictures"], model.get("error_learned"), model.get("error_rule")
        if n < MIN_PICTURES:
            return f"Learned from {n} crop(s); it needs {MIN_PICTURES - n} more before it is used. Until then the built-in rule crops."
        near = f"On crops it had not seen, its edges were {el * 100:.1f}% of the frame from yours; the built-in rule's {er * 100:.1f}%."
        if model.get("use"):
            how = {"cnn": "the picture network", "net": "a small neural network"}.get(model.get("kind"), "the simple model")
            return f"Learned from {n} crops ({model['made'][:16]}), with {how}. {near} Auto crop uses what it learned."
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
            L = small_luminance(planes)
            if min(L.shape) < 24:
                return None
            t0 = time.time()
            cuts = m.cuts(L, rule_rect, context_of(planes, film, raw))
            self.uses["n"] += 1
            self.uses["t"] += time.time() - t0
        except Exception:  # noqa: BLE001 - an extra: the rule is still there
            traceback.print_exc()
            return None
        x0, y0, x1, y1 = rect_of(cuts)
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return None
        return tuple(round(v, 4) for v in (x0, y0, x1, y1))
