"""Which way up a picture goes, from a ready-made model trained on a great many photographs.

The model is "Deep Image Orientation Detection" by Duarte Barbosa (MIT licence),
https://github.com/duartebarbosadev/deep-image-orientation-detection : an EfficientNetV2-S that
answers "this picture was turned 0, 1, 2 or 3 quarters". On the 357 pictures of the first eleven
rolls it had the right way up for every one at preview size and above, and for about 94 in 100
at thumbnail size (docs/auto-turn.md has the numbers).

Its file (80 MB, ONNX) is NOT part of the app or of git: it is fetched once from the author's
GitHub release into the learning folder (~/.coolscan-crops/base) and checked against a known
SHA-256. It is run here on Apple MLX (the graphics chip), read by the small ONNX reader below,
so the app needs nothing new installed. Without MLX, or until the file is there, base() is None
and auto rotate falls back to what it learned from the person's own turns (turnlearn.py).

COOLSCAN_TURN_BASE=off in the environment switches it off; COOLSCAN_TURN_BASE=/path/to/file.onnx
uses that file (the checks do).
"""

import hashlib
import os
import struct
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import numpy as np

from . import croplearn as cl

try:
    import mlx.core as mx
except Exception:  # noqa: BLE001 - no MLX: the ready-made model is not used
    mx = None

NAME = "orientation_model_v2_0.9882.onnx"
URL = "https://github.com/duartebarbosadev/deep-image-orientation-detection/releases/download/v2/" + NAME
SHA256 = "cffe911c1dff47fbfbbd90110aaab9c07134645c460d35b3ae8832079bea91ba"
BYTES = 80578424
SIDE = 384               # the model's picture: SIDE x SIDE, cut from the middle of one made SIDE + PAD
PAD = 32
LOOK = 448               # a picture is made this small (longer side) before the model's own resize
SMALL = 250              # a picture with a longer side under this (a thumbnail piece): too few pixels to turn by itself
SURE = 0.85              # turns by itself only this sure (the model never says more than about 0.93)
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

_lock = threading.Lock()
_net = None              # the loaded model, or False when it could not be loaded
_fetch = {"state": None, "error": None, "got": 0}


# ------------------------------------------------------------------ the ONNX file, read without the onnx package
def _varint(b, i):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if c < 0x80:
            return n, i
        shift += 7


def _fields(b):
    """(field number, wire type, value) for each field of a protobuf message; value is an int, or the bytes."""
    i, end = 0, len(b)
    while i < end:
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError("not an ONNX file")
        yield f, w, v


def _ints(w, v):
    if w == 0:
        return [v]
    out, i = [], 0
    while i < len(v):
        n, i = _varint(v, i)
        out.append(n)
    return out


def _tensor(b):
    dims, kind, name, raw = [], 1, "", None
    for f, w, v in _fields(b):
        if f == 1:
            dims += _ints(w, v)
        elif f == 2:
            kind = v
        elif f == 8:
            name = bytes(v).decode()
        elif f == 9:
            raw = v
    if kind != 1 or raw is None:
        raise ValueError("the model holds numbers this reader does not know")
    return name, np.frombuffer(raw, "<f4").reshape(dims)


def _node(b):
    n = {"in": [], "out": [], "op": "", "attr": {}}
    for f, w, v in _fields(b):
        if f == 1:
            n["in"].append(bytes(v).decode())
        elif f == 2:
            n["out"].append(bytes(v).decode())
        elif f == 4:
            n["op"] = bytes(v).decode()
        elif f == 5:
            name, val = "", None
            for g, gw, gv in _fields(v):
                if g == 1:
                    name = bytes(gv).decode()
                elif g == 2:
                    val = struct.unpack("<f", gv)[0]
                elif g == 3:
                    val = gv
                elif g == 8:
                    val = (val if isinstance(val, list) else []) + _ints(gw, gv)
            n["attr"][name] = val
    return n


def read_onnx(path):
    """(nodes in order, {name: numpy array}) of an ONNX file."""
    data = memoryview(Path(path).read_bytes())
    graph = next(v for f, w, v in _fields(data) if f == 7 and w == 2)
    nodes, weights = [], {}
    for f, w, v in _fields(graph):
        if f == 1:
            nodes.append(_node(v))
        elif f == 5:
            name, a = _tensor(v)
            weights[name] = a
    return nodes, weights


OPS = {"Conv", "Sigmoid", "Mul", "Add", "GlobalAveragePool", "Flatten", "Gemm"}


class Net:
    """The model on MLX. Pictures go through as (n, rows, cols, 3); weights are turned to MLX's order once."""

    def __init__(self, path):
        nodes, weights = read_onnx(path)
        odd = {n["op"] for n in nodes} - OPS
        if odd:
            raise ValueError("the model uses steps this reader does not know: " + ", ".join(sorted(odd)))
        self.nodes = nodes
        conv = {n["in"][1] for n in nodes if n["op"] == "Conv"}
        self.w = {k: mx.array(np.ascontiguousarray(a.transpose(0, 2, 3, 1)) if k in conv else a) for k, a in weights.items()}
        self.input = next(x for x in nodes[0]["in"] if x not in self.w)
        self.output = nodes[-1]["out"][0]
        last = {}
        for k, n in enumerate(nodes):
            for x in n["in"]:
                last[x] = k
        self.last = last

    def __call__(self, x):
        """x: numpy (n, SIDE, SIDE, 3), prepared. Returns numpy (n, 4): the model's scores."""
        v = {self.input: mx.array(x)}
        get = lambda name: self.w[name] if name in self.w else v[name]
        for k, n in enumerate(self.nodes):
            op, a = n["op"], n["attr"]
            if op == "Conv":
                pads = a.get("pads") or [0, 0, 0, 0]
                y = mx.conv2d(get(n["in"][0]), get(n["in"][1]), stride=tuple(a.get("strides") or (1, 1)),
                              padding=(pads[0], pads[1]), dilation=tuple(a.get("dilations") or (1, 1)), groups=int(a.get("group") or 1))
                if len(n["in"]) > 2:
                    y = y + get(n["in"][2])
            elif op == "Sigmoid":
                y = mx.sigmoid(get(n["in"][0]))
            elif op == "Mul":
                y = get(n["in"][0]) * get(n["in"][1])
            elif op == "Add":
                y = get(n["in"][0]) + get(n["in"][1])
            elif op == "GlobalAveragePool":
                y = mx.mean(get(n["in"][0]), axis=(1, 2), keepdims=True)
            elif op == "Flatten":
                t = get(n["in"][0])
                y = t.reshape(t.shape[0], -1)
            else:  # Gemm
                wt = get(n["in"][1])
                y = get(n["in"][0]) @ (wt.T if a.get("transB") else wt) * float(a.get("alpha") or 1.0)
                if len(n["in"]) > 2:
                    y = y + get(n["in"][2]) * float(a.get("beta") or 1.0)
            v[n["out"][0]] = y
            for x in n["in"]:   # what no later step needs is let go
                if x in v and self.last.get(x) == k:
                    del v[x]
        out = v[self.output]
        mx.eval(out)
        return np.array(out, np.float64)


# ------------------------------------------------------------------ the file: where it is, fetching it
def folder():
    return cl.STORE / "base"


def path():
    want = os.environ.get("COOLSCAN_TURN_BASE", "").strip()
    if want and want.lower() not in ("0", "off", "no", "false", "on", "1"):
        return Path(want)
    return folder() / NAME


def switched_off():
    return os.environ.get("COOLSCAN_TURN_BASE", "").strip().lower() in ("0", "off", "no", "false")


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def fetch(say=None, wait=False):
    """Fetch the model's file once (in the background unless wait). Nothing happens when it is there,
    switched off, being fetched already, or when the checks run (COOLSCAN_LEARN_DIR)."""
    if switched_off() or mx is None or path().exists() or os.environ.get("COOLSCAN_LEARN_DIR") or os.environ.get("COOLSCAN_TURN_BASE"):
        return
    with _lock:
        if _fetch["state"] in ("fetching", "failed"):
            return
        _fetch.update(state="fetching", error=None, got=0)

    def run():
        tmp = folder() / (NAME + ".part")
        try:
            folder().mkdir(parents=True, exist_ok=True)
            if say:
                say("auto rotate: fetching its ready-made model (80 MB, once) from GitHub")
            try:
                with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "coolscan"}), timeout=60) as r, open(tmp, "wb") as f:
                    for part in iter(lambda: r.read(1 << 20), b""):
                        f.write(part)
                        _fetch["got"] += len(part)
            except Exception:  # noqa: BLE001 - e.g. this Python has no certificates of its own: the Mac's curl has
                import subprocess
                subprocess.run(["curl", "-fsSL", "--max-time", "900", "-o", str(tmp), URL], check=True, timeout=960)
                _fetch["got"] = tmp.stat().st_size
            if _sha(tmp) != SHA256:
                raise ValueError("the file fetched is not the one expected (its SHA-256 differs)")
            tmp.replace(folder() / NAME)
            _fetch["state"] = "done"
            if say:
                say("auto rotate: its ready-made model is in place")
        except Exception as e:  # noqa: BLE001 - an extra: auto rotate carries on with what it learned itself
            _fetch.update(state="failed", error=str(e) or type(e).__name__)
            try:
                tmp.unlink()
            except OSError:
                pass
            if say:
                say(f"auto rotate: its ready-made model could not be fetched ({_fetch['error']}); using what it learned from your turns")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    if wait:
        t.join()


def retry():
    with _lock:
        if _fetch["state"] == "failed":
            _fetch["state"] = None


def base():
    """The loaded model, or None (no MLX, switched off, file not there yet or not readable)."""
    global _net
    if mx is None or switched_off():
        return None
    if _net is None:
        with _lock:
            if _net is None:
                p = path()
                if not p.exists():
                    return None
                try:
                    _net = Net(p)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    _net = False
    return _net or None


def forget():
    """(checks) Load the file again next time."""
    global _net
    _net = None


def status():
    """{"state": "ready" | "off" | "no MLX" | "missing" | "fetching" | "failed", "text": in words}."""
    if switched_off():
        return {"state": "off", "text": "The ready-made model is switched off (COOLSCAN_TURN_BASE)."}
    if mx is None:
        return {"state": "no MLX", "text": "The ready-made model needs MLX (an Apple Silicon Mac); auto rotate uses only what it learned from your turns."}
    if path().exists():
        if _net is False:
            return {"state": "failed", "text": "The ready-made model's file could not be read; auto rotate uses only what it learned from your turns."}
        return {"state": "ready", "text": "Uses a ready-made model trained on a great many photographs (Deep Image Orientation Detection, MIT licence), on the "
                + _device() + "."}
    if _fetch["state"] == "fetching":
        return {"state": "fetching", "text": f"Fetching the ready-made model: {_fetch['got'] / 2 ** 20:.0f} of {BYTES / 2 ** 20:.0f} MB. Until then auto rotate uses what it learned from your turns."}
    if _fetch["state"] == "failed":
        return {"state": "failed", "text": f"The ready-made model could not be fetched ({_fetch['error']}). Auto rotate uses what it learned from your turns; it tries again when the app is next started."}
    return {"state": "missing", "text": "The ready-made model (80 MB) is fetched from GitHub the first time auto rotate looks at a picture."}


def _device():
    try:
        return "GPU (MLX)" if mx.default_device().type == mx.gpu else "processor (MLX)"
    except Exception:  # noqa: BLE001
        return "MLX"


# ------------------------------------------------------------------ pictures in, turns out
def _shrink(a, side):
    """a (rows, cols[, 3]) made no longer than about `side` by averaging whole blocks."""
    f = max(a.shape[0], a.shape[1]) // side
    if f > 1:
        r, c = a.shape[0] // f * f, a.shape[1] // f * f
        a = a[:r, :c].reshape(r // f, f, c // f, f, *a.shape[2:]).mean(axis=(1, 3))
    return a


def _resize(a, rows, cols):
    """a (r, c, 3) float32 resized, straight-line in between pixels."""
    r, c = a.shape[:2]
    y = np.clip((np.arange(rows) + 0.5) * r / rows - 0.5, 0, r - 1)
    x = np.clip((np.arange(cols) + 0.5) * c / cols - 0.5, 0, c - 1)
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    y1, x1 = np.minimum(y0 + 1, r - 1), np.minimum(x0 + 1, c - 1)
    fy, fx = (y - y0)[:, None, None].astype(np.float32), (x - x0)[None, :, None].astype(np.float32)
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bottom = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return top * (1 - fy) + bottom * fy


def stretch8(planes, negative=False):
    """Planes as the scanner read them (any scale) as an 8-bit picture to look at; a negative is turned
    round roughly, each colour on its own (the same as the Library does for a RAW file)."""
    planes = [np.asarray(p, np.float32) for p in planes]
    if negative:
        ends = [(float(np.percentile(p, 0.5)), float(np.percentile(p, 99.5))) for p in planes]
    else:
        both = (min(float(np.percentile(p, 0.5)) for p in planes), max(float(np.percentile(p, 99.5)) for p in planes))
        ends = [both] * len(planes)
    out = []
    for p, (lo, hi) in zip(planes, ends):
        v = np.clip((p - lo) / max(hi - lo, 1e-6), 0, 1)
        v = (1 - v) ** 1.6 if negative else v ** (1 / 2.2)
        out.append((v * 255 + 0.5).astype(np.uint8))
    return np.dstack(out) if len(out) > 1 else out[0]


def picture(planes, film=None):
    """A frame as the scanner sent it (colour planes, any size) as the small 8-bit picture the model looks at."""
    planes = [_shrink(np.asarray(p, np.float32), LOOK) for p in planes[:3]]
    return stretch8(planes, cl._film_word(film) in cl.FLIP_FILMS)


def prepare(rgb):
    """An 8-bit picture (rows, cols[, 3]) as the model wants it: (SIDE, SIDE, 3) float32."""
    a = np.asarray(rgb)
    if a.ndim == 2:
        a = np.dstack([a, a, a])
    a = _shrink(a[..., :3].astype(np.float32), LOOK)
    a = _resize(a, SIDE + PAD, SIDE + PAD)[PAD // 2:PAD // 2 + SIDE, PAD // 2:PAD // 2 + SIDE]
    return ((a / 255.0 - MEAN) / STD).astype(np.float32)


def is_small(rgb):
    return max(np.asarray(rgb).shape[:2]) < SMALL


def probs(rgb, votes=1):
    """How likely each turn (0..3 quarters to the left) makes the picture upright, or None without the
    model. votes=4: the picture is looked at in all four turns and the answers put together (slower, surer)."""
    net = base()
    if net is None:
        return None
    a = np.asarray(rgb)
    x = np.stack([prepare(np.rot90(a, r)) for r in range(4 if votes == 4 else 1)])
    z = net(x)
    z = z - z.max(axis=1, keepdims=True)
    lp = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
    # the model's class c: "this picture was turned c quarters to the left", so it wants (-c) % 4 more;
    # looked at already turned r, the picture itself wants t when the class is (r - t) % 4
    s = np.array([sum(lp[r, (r - t) % 4] for r in range(len(lp))) for t in range(4)])
    e = np.exp(s - s.max())
    return e / e.sum()


def timed(rgb):
    t = time.time()
    p = probs(rgb)
    return p, time.time() - t
