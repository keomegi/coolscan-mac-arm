"""Auto rotate that learns from the person's own turns: which way up a frame goes (as it is, a quarter to
the left, upside down, a quarter to the right), learned from the turns they make on the scanning
screen (thumbnails and previews) and in the Library.

What it learns from (each a small greyscale copy of the frame, as it was shown, and the turn kept):
  - frames turned by hand on the scanning screen, and frames cropped by hand there (looked at closely:
    the turn they have was kept on purpose),
  - Library pictures turned by hand, or cropped by hand (the same).
A turn auto rotate made itself and the person left as it was is not learned from (else it would learn
from its own guesses). Raw scans of negatives are mirrored into positives first (as crop learning
does), so turns learned in the Library also work on the scanning screen.

How: the four ways a frame can be turned are each looked at, and a model scores how "the right way up"
each looks; the best is the turn, and its share of the four (softmax) is how sure it is. Models:
  - simple: a weighted sum of measured numbers (brightness in a grid and in bands from top to bottom,
    where the fine detail is, upright or lying, and which turn it is: people turn one way more often);
  - net: a small neural network on the same numbers (on the GPU with MLX, else numpy);
  - cnn: a picture network that looks at the pixels of each turned view (MLX, on the GPU), with the
    same numbers beside it. Tried from CNN_MIN pictures.
5-fold check by picture; a bigger model only when clearly better. It is used only when it beats
"leave it as it is" on pictures it was not trained on, and it turns a frame by itself only when it is
sure (SURE) and, on the check, its sure answers were right (AUTO_RIGHT); otherwise it only suggests.

What is learned is kept like crop learning's: models/turn_model.json (numbers only) and turn_cnn.npz in
the app's folder (written only where models/.owner exists); anyone else adapts it in their own store.
"""
import hashlib
import json
import math
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from . import croplearn as cl
from . import turnbase as tb

try:
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
except Exception:  # noqa: BLE001 - no MLX: numpy on the processor
    mx = None

STORE = cl.STORE / "turns"
MODELS = cl.MODELS
MODEL_FILE = "turn_model.json"
CNN_FILE = "turn_cnn.npz"
MIN_PICTURES = 12       # fewer: learned but not used
MIN_ADAPT = 5
CNN_MIN = 24
RELEARN_EVERY = 10
SAMPLES_KEPT = 4000
HISTORY_KEPT = 50
# how much a picture left as it was counts, against one turned or cropped by hand (1)
W_SCANNED = 0.4         # scanned and saved without a turn
W_SCANNED_STRIP = 0.7   # ...on a strip where other frames were turned by hand (so these were looked at too)
W_CORRECTED = 1.5       # a turn auto rotate made itself, put right by hand: the clearest sign of its mistake
W_SEEN = 0.3            # opened in the Library viewer and left as it was
SURE = 0.9              # turns by itself only this sure...
AUTO_RIGHT = 0.95       # ...and when sure answers were right this often on the check (from AUTO_LEAST of them)
AUTO_LEAST = 5
VIEW = 40               # the picture network's view of a frame: VIEW x VIEW
VERSION = 1
BASE_WORDS = ("It turns a preview scan, a scan and a Library picture by itself when it is sure. A thumbnail has too few pixels for that: "
              "there it only suggests a turn, and the scan itself is looked at again before its files are saved.")
TURN_WORDS = {0: "as it is", 1: "a quarter to the left", 2: "upside down", 3: "a quarter to the right"}
STEPS = int(__import__("os").environ.get("COOLSCAN_CNN_STEPS") or 600)   # the picture network's learning steps (checks run fewer)


def gpu():
    return mx is not None


def device():
    if mx is None:
        return "processor (numpy)"
    try:
        return "GPU (MLX)" if mx.default_device().type == mx.gpu else "processor (MLX)"
    except Exception:  # noqa: BLE001
        return "MLX"


# ------------------------------------------------------------------ what is measured on each view
def _norm(L):
    med = float(np.median(L))
    spread = float(np.percentile(L, 90) - np.percentile(L, 10)) + 0.05
    return (L - med) / spread


def _grid(z, rows, cols):
    return np.array([[b.mean() for b in np.array_split(r, cols, axis=1)] for r in np.array_split(z, rows, axis=0)])


def view_features(U):
    """Numbers for one view U (log brightness as it would be shown): about "is this the right way up"."""
    z = np.clip(_norm(U), -4, 4)
    h, w = z.shape
    g = _grid(z, 4, 4).ravel()
    bands = _grid(z, 6, 1).ravel()
    gy = np.abs(np.diff(z, axis=0))
    gx = np.abs(np.diff(z, axis=1))
    t3, b3 = z[: h // 3], z[2 * h // 3:]
    tx = lambda a: float(np.abs(np.diff(a, axis=1)).mean() + np.abs(np.diff(a, axis=0)).mean())   # fine detail
    detail = _grid(gx[:-1, :] + gy[:, :-1], 3, 1).ravel()
    return np.concatenate([
        g, bands, detail,
        [z[: h // 2].mean() - z[h // 2:].mean(),            # brighter above (sky)?
         abs(z[:, : w // 2].mean() - z[:, w // 2:].mean()),  # (left and right: no way up in it)
         gx.mean() - gy.mean(),                              # upright lines against lying ones
         tx(t3) - tx(b3),                                    # more detail below than above?
         float(h > w)]])                                     # standing (portrait)


def candidates(L, context=None):
    """(4, n): the numbers for each of the four turns of L, with which turn it is (people turn one way
    more than another) and that turn crossed with "the frame stands now"."""
    out = []
    for r in range(4):
        f = view_features(np.rot90(L, r))
        one = np.eye(4)[r]
        stands = float(L.shape[0] > L.shape[1])
        out.append(np.concatenate([f, one, one * stands]))
    return np.asarray(out, np.float64)


def view_pixels(L):
    """(4, VIEW, VIEW, 2): each turn of L made small and square, with a channel for "it stands"."""
    z = np.clip(_norm(L), -4, 4).astype(np.float32)
    out = []
    for r in range(4):
        v = np.rot90(z, r)
        sq = _square(v)
        out.append(np.stack([sq, np.full_like(sq, float(v.shape[0] > v.shape[1]))], axis=-1))
    return np.asarray(out, np.float32)


def _square(v):
    h, w = v.shape
    y = np.clip(((np.arange(VIEW) + 0.5) * h / VIEW).astype(int), 0, h - 1)
    x = np.clip(((np.arange(VIEW) + 0.5) * w / VIEW).astype(int), 0, w - 1)
    # each cell the mean of its block where the picture is big enough, else nearest
    if h >= VIEW and w >= VIEW:
        return _grid(v, VIEW, VIEW).astype(np.float32)
    return v[np.ix_(y, x)]


# ------------------------------------------------------------------ the models on the GPU (MLX)
if mx is not None:
    class TurnNet(nn.Module):
        """The picture network: small convolutions over each turned view, then with the measured numbers
        beside them, a score for the view. All four views share the weights."""

        def __init__(self, n):
            super().__init__()
            self.c1 = nn.Conv2d(2, 8, 3, padding=1)
            self.c2 = nn.Conv2d(8, 16, 3, padding=1)
            self.c3 = nn.Conv2d(16, 24, 3, padding=1)
            self.d1 = nn.Linear(24 * 5 * 5 + n, 32)
            self.d2 = nn.Linear(32, 1)

        def __call__(self, V, X):        # V (B, 4, VIEW, VIEW, 2), X (B, 4, n) -> (B, 4)
            b = V.shape[0]
            x = V.reshape(b * 4, VIEW, VIEW, 2)
            x = _pool(nn.relu(self.c1(x)))
            x = _pool(nn.relu(self.c2(x)))
            x = _pool(nn.relu(self.c3(x)))
            x = mx.concatenate([x.reshape(b * 4, -1), X.reshape(b * 4, -1)], axis=-1)
            return self.d2(nn.relu(self.d1(x))).reshape(b, 4)

    def _pool(x):
        b, h, w, c = x.shape
        return x[:, : h // 2 * 2, : w // 2 * 2].reshape(b, h // 2, 2, w // 2, 2, c).max(axis=(2, 4))


def _train_mlx(model, inputs, T, steps, lr, lam, prior=None, seed=0, sw=None):
    """Softmax over the four views; Adam; L2 (towards `prior` weights when adapting)."""
    mx.random.seed(seed)
    rng = np.random.default_rng(seed)
    opt = optim.Adam(learning_rate=lr)
    flat_prior = None
    if prior is not None:
        from mlx.utils import tree_flatten
        flat_prior = dict(tree_flatten(prior))

    def loss_fn(m, *xs_t):
        *xs, w, t = xs_t
        s = m(*xs)
        logp = s - mx.logsumexp(s, axis=1, keepdims=True)
        loss = -((t * logp).sum(axis=1) * w).mean()   # w: how much each example counts
        if lam:
            from mlx.utils import tree_flatten
            reg = 0.0
            for k, v in tree_flatten(m.parameters()):
                p0 = flat_prior.get(k) if flat_prior is not None else None
                reg = reg + (((v - p0) ** 2).sum() if p0 is not None else (v ** 2).sum())
            loss = loss + 0.5 * lam * reg
        return loss

    step = nn.value_and_grad(model, loss_fn)
    n = len(T)
    SW = np.ones(n, np.float32) if sw is None else np.asarray(sw, np.float32)
    batch = min(64, n)
    for _ in range(steps):
        idx = rng.choice(n, batch, replace=False) if n > batch else np.arange(n)
        _, grads = step(model, *[mx.array(a[idx]) for a in inputs], mx.array(SW[idx]), mx.array(T[idx]))
        opt.update(model, grads)
        mx.eval(model.parameters(), opt.state)
    return model


def _save_cnn(net, path):
    from mlx.utils import tree_flatten
    np.savez(path, **{k: np.array(v) for k, v in tree_flatten(net.parameters())})


def _load_cnn(path, n):
    from mlx.utils import tree_unflatten
    with np.load(path) as z:
        if int(z["d1.weight"].shape[-1]) != 24 * 5 * 5 + n:
            return None
        net = TurnNet(n)
        net.update(tree_unflatten([(k, mx.array(z[k])) for k in z.files]))
    mx.eval(net.parameters())
    return net


def _softmax(s):
    s = s - s.max(axis=-1, keepdims=True)
    e = np.exp(s)
    return e / e.sum(axis=-1, keepdims=True)


def _targets(k):
    T = np.full((len(k), 4), 0.01)
    T[np.arange(len(k)), k] = 0.97
    return T


# ------------------------------------------------------------------ learning
def balance(K, W):
    """How much each example counts: how surely the person checked it (W), and its kind of turn evened
    out (square root: halfway), so a great many pictures left as they were do not swamp the few turned.
    Mean 1."""
    W = np.asarray(W, np.float64)
    total = W.sum()
    present = [c for c in range(4) if W[K == c].sum() > 0]
    f = {c: math.sqrt(total / (len(present) * W[K == c].sum())) for c in present}
    SW = W * np.array([f.get(int(k), 1.0) for k in K])
    return SW / SW.mean()


def train(examples, start=None, start_cnn=None):
    """examples: [(L, turn[, weight])] where L is the frame as it was shown (log brightness, positive),
    turn the quarter turns (to the left) the person kept, and weight how surely they checked it (1: turned
    or cropped by hand; less for a picture only scanned or looked at and left as it was). Returns (model
    dict, picture network or None). start: a shipped TurnModel to adapt from (someone else's turns)."""
    if not examples:
        return None, None
    X = np.stack([candidates(e[0]) for e in examples])
    K = np.array([int(e[1]) % 4 for e in examples])
    W = np.array([float(e[2]) if len(e) > 2 else 1.0 for e in examples])
    SW = balance(K, W)
    V = np.stack([view_pixels(e[0]) for e in examples]) if gpu() else None
    n = X.shape[2]
    pics = len(K)
    if start is not None and len(start.mu) != n:
        start = None
    # Only the picture network is learned (since 2026-11-26: the simple model and the small neural network
    # that were tried beside it are gone; on real turns the picture network was clearly the best). It
    # needs MLX and CNN_MIN pictures (or the shipped network to adapt): without, nothing is learned yet.
    if start is not None and start_cnn is None:
        start = None
    can = gpu() and (start is not None or pics >= CNN_MIN)
    kinds = ["cnn"] if can else []

    def scale(idx):
        if start is not None:
            return start.mu, start.sd
        Xi = X[idx]
        mu = Xi.reshape(-1, n).mean(axis=0)
        sd = Xi.reshape(-1, n).std(axis=0)
        return mu, np.where(sd < 1e-6, 1e6, sd)

    def fit(kind, idx):
        Ti = _targets(K[idx])
        mu, sd = scale(idx)
        Z = ((X[idx] - mu) / sd).astype(np.float32)
        net = _load_cnn(start_cnn, n) if start_cnn is not None else None
        prior = _load_cnn(start_cnn, n).parameters() if net is not None else None
        net = net or TurnNet(n)
        _train_mlx(net, [V[idx], Z], Ti, steps=STEPS // 3 if prior is not None else STEPS,
                   lr=5e-4 if prior is not None else 2e-3, lam=1e-4, prior=prior, sw=SW[idx])
        return net, mu, sd

    def probs(kind, model, idx):
        params, mu, sd = model
        Z = ((X[idx] - mu) / sd)
        return _softmax(np.array(params(mx.array(V[idx]), mx.array(Z.astype(np.float32))), np.float64))

    folds = min(5, pics)
    order = np.random.default_rng(0).permutation(pics)
    fold = np.zeros(pics, int)
    fold[order] = np.arange(pics) % max(1, folds)
    P = {k: np.zeros((pics, 4)) for k in kinds}
    tried = np.zeros(pics, bool)
    if folds >= 2:
        for f in range(folds):
            test = fold == f
            if test.all() or not test.any():
                continue
            for kind in kinds:
                P[kind][test] = probs(kind, fit(kind, ~test), test)
            tried |= test
    if start is not None:   # the shipped model as it is, on these pictures (it never saw them)
        P["shipped"] = start.probs_many(X, V)
    # how often right, counted as learned (how surely checked, kinds evened out): leaving all as they are
    # is no longer right just because most pictures need no turn
    def wright(ok):
        return float(np.sum(SW[tried] * ok) / np.sum(SW[tried])) if tried.any() else None
    acc = {k: wright(np.argmax(p[tried], axis=1) == K[tried]) for k, p in P.items()}
    keep = wright(K[tried] == 0) if tried.any() else float(np.mean(K == 0))
    kind = "cnn" if kinds else "none"
    sure = {}
    for k, p in P.items():   # sure answers that would turn a picture (sure "as it is" changes nothing)
        s = tried & (p.max(axis=1) >= SURE) & (np.argmax(p, axis=1) != 0)
        sure[k] = {"n": int(s.sum()), "right": float(np.mean(np.argmax(p[s], axis=1) == K[s])) if s.any() else None}
    net, mu, sd = fit("cnn", np.arange(pics)) if kinds else (None, *scale(np.arange(pics)))
    enough = pics >= (MIN_ADAPT if start is not None else MIN_PICTURES)
    bar = max(keep, acc["shipped"]) if start is not None and acc.get("shipped") is not None else keep
    use = bool(enough and acc.get(kind) is not None and acc[kind] > bar)
    sk = sure.get(kind) or {"n": 0, "right": None}
    out = {"version": VERSION, "kind": kind, "small": {"kind": "none", "w": [], "mu": np.asarray(mu).tolist(), "sd": np.asarray(sd).tolist()},
           "pictures": pics, "turned": int(np.sum(K != 0)), "by_turn": [int(np.sum(K == t)) for t in range(4)],
           "left_as_they_were": int(np.sum(W < 1)),
           "right": acc, "right_keep": keep, "sure": sure,
           "use": use, "small_use": False,
           "auto": bool(use and sk["n"] >= AUTO_LEAST and (sk["right"] or 0) >= AUTO_RIGHT),
           "small_auto": False,
           "cnn_tried": "cnn" in kinds, "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    if start is not None:
        out.update(adapted_from=start.data.get("made"), right_shipped=acc.get("shipped"),
                   shipped_use=bool(pics < MIN_PICTURES or (acc.get("shipped") or 0) > keep))
    return out, net


class TurnModel:
    def __init__(self, data, cnn_path=None):
        self.data = data
        small = data["small"]
        self.kind = "cnn"   # (the "small" entry now only holds the scaling of the measured numbers)
        self.params = None
        self.mu = np.asarray(small["mu"], np.float64)
        self.sd = np.asarray(small["sd"], np.float64)
        self.net = None
        self.mlp = None
        if gpu():
            try:
                if data.get("kind") == "cnn" and cnn_path is not None and Path(cnn_path).exists():
                    self.net = _load_cnn(cnn_path, len(self.mu))
            except Exception:  # noqa: BLE001 - then it leaves pictures as they are
                traceback.print_exc()

    def usable(self):
        return self.net is not None and bool(self.data.get("use"))

    def may_turn(self):
        return self.net is not None and bool(self.data.get("auto"))

    def probs_many(self, X, V=None):
        Z = (X - self.mu) / self.sd
        if self.net is not None and V is not None:
            return _softmax(np.array(self.net(mx.array(V), mx.array(Z.astype(np.float32))), np.float64))
        out = np.zeros((len(X), 4))   # the picture network cannot run here (no MLX): leave as it is
        out[:, 0] = 1.0
        return out

    def probs(self, L):
        return self.probs_many(candidates(L)[None], view_pixels(L)[None] if self.net is not None else None)[0]


# ------------------------------------------------------------------ the store
class TurnLearner:
    """Samples, the model and learning, for the app. Shares models/ (and its .owner file) with crop learning."""

    def __init__(self, store=None, models=None):
        self.store = Path(store) if store else STORE
        self.models = Path(models) if models else (MODELS if store is None else self.store)
        self.lock = threading.Lock()
        self._cache = {}
        self.busy = False
        self.last = None
        self.uses = {"n": 0, "t": 0.0, "turned": 0, "suggested": 0}
        self.say = None          # the app's log line, for the ready-made model's fetching (turnbase.py)

    # -- whose model
    def owner(self):
        return self.models == self.store or (self.models / cl.OWNER_FILE).exists()

    def _where(self):
        return self.models if self.owner() else self.store

    def _read(self, p, cnn):
        try:
            st = p.stat()
        except OSError:
            self._cache.pop(str(p), None)
            return None
        tag = (st.st_mtime, st.st_size)
        got = self._cache.get(str(p))
        if got is None or got[0] != tag:
            try:
                data = json.loads(p.read_text())
                m = TurnModel(data, cnn) if data.get("version") == VERSION else None
            except (OSError, ValueError, KeyError):
                m = None
            got = (tag, m)
            self._cache[str(p)] = got
        return got[1]

    def shipped(self):
        return self._read(self.models / MODEL_FILE, self.models / CNN_FILE)

    def adapted(self):
        if self.owner():
            return None
        m = self._read(self.store / MODEL_FILE, self.store / CNN_FILE)
        sh = self.shipped()
        if m is None or m.data.get("adapted_from", "-") != (sh.data.get("made") if sh else None):
            return None
        return m

    def model(self):
        sh, ad = self.shipped(), self.adapted()
        if ad is None:
            return sh
        if ad.usable() or not ad.data.get("shipped_use", True):
            return ad
        return sh

    # -- samples
    def add(self, key, planes, turn, film=None, raw=False, source="scan", new=True, weight=1.0, thumb=None):
        """Keep a frame (planes as shown before the turn, 0..65535) and the turn the person kept (quarters
        to the left). key: one per frame (a newer one replaces it). Returns how many new ones wait."""
        L = cl.small_luminance(planes, film, raw)
        T = cl.small_luminance(thumb, film, raw) if thumb else None
        return self.add_small(key, L, turn, source, new, weight, T=T)

    def _name(self, key):
        return "turn-" + hashlib.sha1(str(key).encode()).hexdigest()[:16] + ".npz"

    def has(self, key):
        return (self.store / self._name(key)).exists()

    # -- one picture, one example: a frame scanned with a turn kept by hand becomes a Library picture;
    # a later turn (or crop) of it in the Library replaces the scanning screen's example
    def _links(self):
        try:
            return json.loads((self.store / "links.json").read_text())
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _where_of(path):
        try:
            return str(Path(path).expanduser().resolve())
        except OSError:
            return str(path)

    def link(self, paths, key):
        """The files saved from a frame whose example is `key` (scanning screen)."""
        links = self._links()
        for p in paths:
            links[self._where_of(p)] = str(key)
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "links.json").write_text(json.dumps(dict(list(links.items())[-20000:])))

    def is_linked(self, path):
        return self._where_of(path) in self._links()

    def drop_linked(self, path):
        """A Library picture now has its own example: the scanning screen's one for it (if any) goes."""
        links = self._links()
        key = links.pop(self._where_of(path), None)
        if key is None:
            return False
        for p, k in list(links.items()):   # (its other files point at the same example)
            if k == key:
                links.pop(p)
        (self.store / "links.json").write_text(json.dumps(links))
        try:
            (self.store / self._name(key)).unlink()
        except OSError:
            pass
        return True

    def add_small(self, key, L, turn, source="scan", new=True, weight=1.0, T=None):
        """weight: how surely the person checked it: 1 turned or cropped by hand; less for a picture only
        scanned, or opened, and left as it was (W_SCANNED, W_SCANNED_STRIP, W_SEEN). A weaker one never
        replaces an example already kept for the same picture."""
        if weight < 1 and self.has(key):
            return int(self._state().get("new", 0))
        self.store.mkdir(parents=True, exist_ok=True)
        name = self._name(key)
        tmp = self.store / (name + ".part.npz")
        more = {"T": cl._pack(T)} if T is not None and min(T.shape) >= 16 else {}   # the same frame as its thumbnail pass saw it
        np.savez_compressed(tmp, L=cl._pack(L), turn=np.asarray(int(turn) % 4), made=np.asarray(time.time()),
                            source=np.asarray(source), weight=np.asarray(float(weight)), **more)
        tmp.replace(self.store / name)
        kept = sorted(self.store.glob("turn-*.npz"), key=lambda p: p.stat().st_mtime)
        for old in kept[:-SAMPLES_KEPT]:
            old.unlink()
        return self.note_new() if new else int(self._state().get("new", 0))

    def note_new(self):
        state = self._state()
        state["new"] = int(state.get("new", 0)) + 1
        self._save_state(state)
        return state["new"]

    def examples(self, views=False):
        """One per picture kept; views: also the same picture as its thumbnail pass saw it (fewer pixels),
        where that was kept, for learning."""
        out = []
        for p in sorted(self.store.glob("turn-*.npz")):
            try:
                with np.load(p) as z:
                    out.append((cl.to_side(cl._unpack(z["L"])), int(z["turn"]),
                                float(z["weight"]) if "weight" in z.files else 1.0))
                    if views and "T" in z.files:
                        out.append((cl.to_side(cl._unpack(z["T"])), out[-1][1], out[-1][2]))
            except (OSError, ValueError, KeyError):
                continue
        return out

    def counts(self):
        by = {"scan": 0, "library": 0}
        for p in self.store.glob("turn-*.npz"):
            try:
                with np.load(p) as z:
                    s = str(z["source"]) if "source" in z.files else "scan"
                by[s if s in by else "scan"] += 1
            except (OSError, ValueError, KeyError):
                continue
        return by

    # -- auto rotates (not learned from unless changed)
    def _auto(self):
        try:
            return json.loads((self.store / "auto.json").read_text())
        except (OSError, ValueError):
            return {}

    def note_auto(self, key, turn):
        a = self._auto()
        a[str(key)] = int(turn) % 4
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "auto.json").write_text(json.dumps(dict(list(a.items())[-20000:])))

    def corrects_auto(self, key, turn):
        """Auto rotate turned this picture itself, and it is now turned otherwise (by hand)."""
        a = self._auto().get(str(key))
        return a is not None and a != int(turn) % 4

    def is_auto(self, key, turn):
        return self._auto().get(str(key)) == int(turn) % 4

    def _state(self):
        try:
            return json.loads((self.store / "state.json").read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "state.json").write_text(json.dumps(state))

    def due(self):
        return not self.busy and int(self._state().get("new", 0)) >= RELEARN_EVERY

    def reset_to_shipped(self):
        return cl.reset_to_shipped(self, MODEL_FILE, CNN_FILE)

    def delete_pictures(self):
        n, mb = 0, 0.0
        for p in self.store.glob("turn-*.npz"):
            try:
                mb += p.stat().st_size / 2 ** 20
                p.unlink()
                n += 1
            except OSError:
                continue
        return {"deleted": n, "mb": round(mb, 2)}

    # -- learning
    def learn(self, examples, why="button"):
        with self.lock:
            if self.busy:
                return {"ok": False, "error": "Already learning."}
            self.busy = True
        try:
            t0, c0 = time.time(), time.process_time()
            if gpu():
                try:
                    mx.reset_peak_memory()
                except Exception:  # noqa: BLE001
                    pass
            start = None if self.owner() else self.shipped()
            start_cnn = None
            if start is not None and start.data.get("kind") == "cnn" and gpu() and (self.models / CNN_FILE).exists():
                start_cnn = self.models / CNN_FILE
            model, net = train(examples, start=start, start_cnn=start_cnn)
            state = self._state()
            state["new"] = 0
            self._save_state(state)
            if model is None:
                self.last = {"ok": True, "pictures": 0, "use": False, "text": self.describe(None)}
                return self.last
            gpu_mb = None
            if gpu():
                try:
                    gpu_mb = round(mx.get_peak_memory() / 2 ** 20, 1)
                except Exception:  # noqa: BLE001
                    pass
            used = {"seconds": round(time.time() - t0, 2), "cpu_seconds": round(time.process_time() - c0, 2),
                    "memory_mb": cl._peak_rss(), "gpu_memory_mb": gpu_mb, "device": device()}
            model.update(used=used, why=why)
            where = self._where()
            where.mkdir(parents=True, exist_ok=True)
            if net is not None:
                _save_cnn(net, where / "turn_cnn.part.npz")
                (where / "turn_cnn.part.npz").replace(where / CNN_FILE)
            elif (where / CNN_FILE).exists():
                (where / CNN_FILE).unlink()
            tmp = where / (MODEL_FILE + ".part")
            tmp.write_text(json.dumps(model, indent=1))
            tmp.replace(where / MODEL_FILE)
            self._add_history(model)
            self.last = {"ok": True, "pictures": model["pictures"], "use": model["use"], "used": used, "text": self.describe(model)}
            return self.last
        finally:
            self.busy = False

    def _add_history(self, model):
        p = self.store / "history.json"
        try:
            runs = json.loads(p.read_text())
        except (OSError, ValueError):
            runs = []
        runs.append({k: model.get(k) for k in ("made", "why", "pictures", "turned", "kind", "use", "auto", "right",
                                               "right_keep", "used", "adapted_from", "right_shipped")})
        p.write_text(json.dumps(runs[-HISTORY_KEPT:]))

    def history(self):
        try:
            return json.loads((self.store / "history.json").read_text())
        except (OSError, ValueError):
            return []

    @staticmethod
    def describe(model):
        if not model:
            return f"Nothing learned yet. Turn (or crop) at least {MIN_PICTURES} pictures by hand, then press Learn."
        n, acc = model["pictures"], (model.get("right") or {}).get(model["kind"])
        keep = model.get("right_keep") or 0
        if acc is None:
            if not gpu():
                return f"{n} picture(s) kept. Learning from your turns needs MLX (an Apple Silicon Mac), which is not installed here."
            return f"{n} picture(s) kept; it learns from {CNN_MIN} pictures on."
        head = (f"Learned from {n} pictures ({model['made'][:16]}): on pictures it had not seen it had the right way up "
                f"{acc * 100:.0f}% of the time (leaving every picture as it is: {keep * 100:.0f}%).")
        if model.get("adapted_from") is not None and model.get("right_shipped") is not None:
            head += f" The app's model as it came: {model['right_shipped'] * 100:.0f}%."
        if not model.get("use"):
            return head + " That is not better yet, so nothing is turned or suggested."
        if model.get("auto"):
            return head + f" It turns frames by itself when it is {SURE * 100:.0f}% sure, and suggests a turn when less sure."
        return head + " It suggests turns, but does not turn by itself yet (its sure answers were not right often enough)."

    def status(self):
        m = self.model()
        b = tb.status()
        if b["state"] == "ready":
            return {"busy": self.busy, "pictures": m.data["pictures"] if m else 0, "use": True, "auto": True,
                    "new": int(self._state().get("new", 0)), "owner": self.owner(), "base": b,
                    "text": "Learning from your turns…" if self.busy else b["text"] + " " + BASE_WORDS}
        return {"base": b, **self._status_learned(m)}

    def _status_learned(self, m):
        return {"busy": self.busy, "pictures": m.data["pictures"] if m else 0, "use": bool(m and m.usable()),
                "auto": bool(m and m.usable() and m.may_turn()), "new": int(self._state().get("new", 0)),
                "owner": self.owner(), "text": "Learning from your turns…" if self.busy else self.describe(m.data if m else None)}

    def report(self):
        m = self.model()
        u = self.uses
        files = list(self.store.glob("turn-*.npz"))
        return {"ok": True, **self.status(), "model": ({k: v for k, v in m.data.items() if k != "small"} if m else None),
                "running": ("Picture network on the " + device() if m.net is not None else "nothing (the picture network cannot run here)") if m else None,
                "device": device(), "history": self.history(), "counts": self.counts(),
                "store": cl.CropLearner._short(self.store), "model_file": cl.CropLearner._short(self._where() / MODEL_FILE),
                "pictures_kept": {"files": len(files), "mb": round(sum(p.stat().st_size for p in files if p.exists()) / 2 ** 20, 2)},
                "frames_looked": u["n"], "turned_by_itself": u["turned"], "suggested": u["suggested"],
                "ms_per_frame": round(1000 * u["t"] / u["n"], 1) if u["n"] else None, "sure": SURE, "role": self.role(),
                "base_sure": tb.SURE, "base_small": tb.SMALL, "learned_text": self.describe(m.data if m else None)}

    def role(self):
        if self.owner():
            return {"owner": True, "text": "What this computer learns is kept in the app's models/ folder (it goes to GitHub with the app)."}
        sh, ad = self.shipped(), self.adapted()
        if sh is None:
            return {"owner": False, "text": "No turn model came with the app: it learns from your turns alone."}
        if ad is None:
            return {"owner": False, "text": f"Starts from the model that came with the app ({sh.data.get('pictures')} pictures); adapts to your turns after {MIN_ADAPT}."}
        return {"owner": False, "text": ("Adapted to your turns from the model that came with the app; your version is used."
                                         if ad.usable() else "Tried adapting to your turns; not better yet than the model that came with the app, which is used.")}

    # -- using it
    def base_guess(self, planes=None, film=None, rgb=None):
        """The ready-made model's guess (turnbase.py) for a frame as the scanner sent it (`planes`) or an
        8-bit picture (`rgb`); None without the model (it is fetched the first time, in the background).
        "auto": it may turn the picture by itself: sure, and not a thumbnail-sized picture ("small")."""
        try:
            if tb.base() is None:
                tb.fetch(self.say)
                return None
            if rgb is None:
                if not planes:
                    return None
                rgb = tb.picture(planes, film)
            if min(np.asarray(rgb).shape[:2]) < 16:
                return None
            t0 = time.time()
            p = tb.probs(rgb)
            self.uses["n"] += 1
            self.uses["t"] += time.time() - t0
        except Exception:  # noqa: BLE001 - an extra: never in the way
            traceback.print_exc()
            return None
        if p is None:
            return None
        k, small = int(np.argmax(p)), bool(tb.is_small(rgb))
        return {"turn": k, "p": round(float(p[k]), 3), "auto": bool(k and not small and p[k] >= tb.SURE),
                "sure": bool(p[k] >= tb.SURE), "small": small, "by": "base", "words": TURN_WORDS[k],
                "probs": [round(float(v), 4) for v in p]}

    def guess(self, planes, film=None, raw=False, L=None, rgb=None):
        """{"turn": 0..3, "p": how sure, "auto": may turn it by itself} for a frame shown as `planes`, or None.
        The ready-made model answers when it is there (it looks at the colours: `planes` or `rgb`);
        otherwise what was learned from the person's own turns does (from `planes` or the small `L`)."""
        if planes or rgb is not None:
            g = self.base_guess(planes, film, rgb)
            if g is not None:
                return g
        m = self.model()
        if m is None or not m.usable():
            return None
        try:
            t0 = time.time()
            L = L if L is not None else cl.small_luminance(planes, film, raw)
            if min(L.shape) < 16:
                return None
            p = m.probs(L)
            self.uses["n"] += 1
            self.uses["t"] += time.time() - t0
        except Exception:  # noqa: BLE001 - an extra: never in the way
            traceback.print_exc()
            return None
        k = int(np.argmax(p))
        return {"turn": k, "p": round(float(p[k]), 3), "auto": bool(k and m.may_turn() and p[k] >= SURE),
                "words": TURN_WORDS[k]}
