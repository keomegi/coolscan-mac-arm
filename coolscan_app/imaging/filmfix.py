"""The colour of a roll's negatives, kept with the roll, and put onto pictures already saved.

Each roll folder can have a FilmProfile.json that says where the colour of its colour negatives
comes from:
    "frame"    each frame on its own (what the app always did; the same as having no file)
    "roll"     this roll's own frames, measured as they were scanned (FilmColour.json)
    "profile"  a saved film profile (its numbers are copied in, so the roll keeps them)
    "base"     the film base (the clear film between frames) picked on a picture in the Library

Scanning into the roll uses it, and the Library shows the roll's pictures with it without
changing the files (like a crop): a picture saved "each frame on its own" is put right with
the film's numbers. That needs the picture's RAW file (Archive mode), which says what the
film's densities really were: the saved picture and its RAW file are matched up colour by
colour, and every step from film density to the saved picture is undone and done again with
the roll's colour. Colour restoration and grain reduction cannot be undone this way; such a
picture is left as it is (the match says so).
"""

import json
import math
import os
import time

import numpy as np

from .colour import (COLOUR_SPACES, FRAME_QS, HEADROOM, NIKON_TONE, _curve_of, _decode_srgb, _encode, _space_matrix,
                     curve_apply, film_curve, film_profile_from)

ROLL_FILE = "FilmProfile.json"
MODES = ("frame", "roll", "profile", "base")
MODE_WORDS = {"frame": "each frame on its own", "roll": "this roll's own frames",
              "profile": "a saved film profile", "base": "the film base picked in the Library"}


# ---------------------------------------------------------------- the roll's file
def read_roll(folder):
    """The roll's FilmProfile.json as a dict with a known "mode", or None."""
    try:
        d = json.loads((folder / ROLL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("mode") not in MODES:
        return None
    return d


def write_roll(folder, d):
    d = {k: v for k, v in d.items() if v is not None}
    d["about"] = ("Coolscan: where the colour of this roll's colour negatives comes from. Scanning into this "
                  "roll and the Library use it. mode: " + ", ".join(f"{k} = {w}" for k, w in MODE_WORDS.items()))
    d["changed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = folder / (ROLL_FILE + ".part")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, folder / ROLL_FILE)
    return d


def _numbers(lo, hi, curve=None):
    try:
        lo, hi = [float(v) for v in lo], [float(v) for v in hi]
    except (TypeError, ValueError):
        return None
    if len(lo) != 3 or len(hi) != 3 or min(h - a for a, h in zip(lo, hi)) < 0.05:
        return None
    out = {"lo": [round(v, 4) for v in lo], "hi": [round(v, 4) for v in hi]}
    if curve and _curve_of({"curve": curve}) is not None:
        out["curve"] = curve   # how the film's colours run against each other (colour.film_curve)
    return out


def measured_frames(folder):
    """What the roll's scanned frames measured (FilmColour.json), as a list."""
    try:
        data = json.loads((folder / "FilmColour.json").read_text(encoding="utf-8"))
        return [v for v in data["frames"].values() if isinstance(v, dict) and not v.get("own")]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def frame_is_own(folder, stem):
    """True when a frame was scanned as another film than its roll's (FilmColour.json marks it "own")."""
    try:
        got = json.loads((folder / "FilmColour.json").read_text(encoding="utf-8"))["frames"].get(stem)
        return bool(isinstance(got, dict) and got.get("own"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


GAP_ONE, GAP_MORE = 0.20, 0.12   # how far a strip's base colour may be from the roll's: one frame, more frames
GAP_LEAST = 3                    # frames of the roll needed before a strip is compared with it


def base_colour(frames):
    """The colour of the film base as a roll's frames measured it: how much denser the thinnest film is
    in green and in blue than in red (the mask's colour), the median over the frames. None without frames."""
    good = [f["lo"] for f in frames if isinstance(f, dict) and len(f.get("lo", ())) == 3]
    if not good:
        return None
    return [float(np.median([lo[1] - lo[0] for lo in good])), float(np.median([lo[2] - lo[0] for lo in good]))]


def base_colour_gap(ref, cur):
    """How far the base colour of the frames `cur` (a strip) is from that of `ref` (the roll before it),
    in density; None when the roll has too few frames to say."""
    if len([f for f in ref if isinstance(f, dict)]) < GAP_LEAST:
        return None
    a, b = base_colour(ref), base_colour(cur)
    if a is None or b is None:
        return None
    return max(abs(x - y) for x, y in zip(a, b))


def roll_numbers(folder, d=None):
    """The film numbers ({"lo", "hi"} and, when the roll's frames give one, "curve") the roll's file asks
    for, or None (each frame on its own)."""
    d = d if d is not None else read_roll(folder)
    if not d or d["mode"] == "frame":
        return None
    if d["mode"] == "roll":
        p = film_profile_from(measured_frames(folder))
        return _numbers(p["lo"], p["hi"], p.get("curve")) if p else None
    if d["mode"] == "base":
        # the picked base says where the film starts; how its colours run from there is the roll's own
        try:
            return _numbers(d.get("lo"), d.get("hi"), film_curve(measured_frames(folder), d.get("lo")))
        except (TypeError, ValueError):
            return None
    return _numbers(d.get("lo"), d.get("hi"), d.get("curve"))


def frame_quantiles(planes01, exposures):
    """A frame's "q" (see colour.frame_colour) from its RAW planes (0..1), for frames measured before the
    app kept it."""
    out = []
    for v in density(planes01, exposures):
        out.append([round(float(x), 3) for x in np.percentile(_inner(v), FRAME_QS)])
    return out


def files_stamp(folder):
    """Changes whenever the roll's colour could change (for caches)."""
    out = []
    for name in (ROLL_FILE, "FilmColour.json"):
        try:
            st = (folder / name).stat()
            out.append((int(st.st_mtime_ns), st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


# ---------------------------------------------------------------- measuring the film base
def density(planes01, exposures):
    """Film density with the exposure taken out (the units of film profiles), from planes 0..1."""
    out = []
    for p, k in zip(planes01, ("red", "green", "blue")):
        v = -np.log10(np.maximum(np.asarray(p, np.float32), 1 / 65535))
        out.append(v + np.float32(math.log10(float(exposures[k]))))
    return out


def base_at(planes01, exposures, x, y, box=0.012):
    """The film's density around a spot (x, y fractions of the picture): its median in a small box.
    Returns ([r, g, b], how even the spot is: the spread of its densities)."""
    rows, cols = planes01[0].shape
    h = max(2, int(round(box * max(rows, cols))))
    r0 = int(min(max(0, y * rows - h), rows - 2 * h)) if rows > 2 * h else 0
    c0 = int(min(max(0, x * cols - h), cols - 2 * h)) if cols > 2 * h else 0
    d = density([p[r0:r0 + 2 * h, c0:c0 + 2 * h] for p in planes01], exposures)
    med = [float(np.median(v)) for v in d]
    spread = max(float(np.percentile(v, 90) - np.percentile(v, 10)) for v in d)
    return med, spread


# ---------------------------------------------------------------- putting the colour onto a saved picture
def _to_srgb_like(rgb, space):
    """(n, 3) values of a saved negative in `space` -> the app's own sRGB-like values before conversion."""
    if space not in COLOUR_SPACES or space == "sRGB IEC61966-2.1":
        return rgb
    gamma = COLOUR_SPACES[space][2]
    lin = _decode_srgb(rgb) if gamma == "srgb" else np.power(np.clip(rgb, 0, 1), gamma)
    m = np.linalg.inv(_space_matrix("sRGB IEC61966-2.1")[0]) @ _space_matrix(space)[0]
    lin = lin @ m.T.astype(np.float32)
    lin = np.maximum(lin, 0)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055).astype(np.float32)


def _from_srgb_like(rgb, space):
    if space not in COLOUR_SPACES or space == "sRGB IEC61966-2.1":
        return np.clip(rgb, 0, 1)
    m = np.linalg.inv(_space_matrix(space)[0]) @ _space_matrix("sRGB IEC61966-2.1")[0]
    lin = _decode_srgb(np.clip(rgb, 0, 1)) @ m.T.astype(np.float32)
    return _encode(lin, COLOUR_SPACES[space][2]).astype(np.float32)


def _untone(v, tone):
    if tone == "nikon":
        lift, gain, gamma = NIKON_TONE
        return np.power(np.maximum((v - lift) / gain, 0), 1 / gamma)
    return v


def _tone(x, tone):
    x = np.clip(x, 0, 1)
    if tone == "nikon":
        lift, gain, gamma = NIKON_TONE
        return np.clip(lift + gain * np.power(x, gamma), 0, 1)
    return x


def _usable(v, tone):
    low = NIKON_TONE[0] + 0.004 if tone == "nikon" else 0.004
    return (v > low) & (v < 0.996)


QS = np.arange(2.0, 98.5, 1.5)
AT_QS = np.arange(4.0, 97.0, 4.0)   # where a picture's values are, for telling whether a new colour changes it


def _inner(d):
    """Inside the frame, not its edges: where the app measures a frame's ends (colour._inner_sample)."""
    rows, cols = d.shape
    return d[rows // 20: rows - rows // 20, cols // 20: cols - cols // 20]


def _turned(d, k):
    """A plane turned k quarter turns, mirrored first when k >= 4."""
    return np.rot90(d[:, ::-1] if k >= 4 else d, k % 4)


def _fit_same(d, sample_at):
    """dens plane -> its values where the picture's sample points are (the two may differ a little in size)."""
    rows, cols, step = sample_at
    r = np.minimum((np.arange(0, rows, step) * (d.shape[0] / rows)).astype(int), d.shape[0] - 1)
    c = np.minimum((np.arange(0, cols, step) * (d.shape[1] / cols)).astype(int), d.shape[1] - 1)
    return _inner(d[np.ix_(r, c)]).reshape(-1).astype(np.float64)


def match(pos01, space, dens):
    """How a saved negative was made from its film: per colour x = a * density + b, where x is the
    picture's value before its tone and colour space. pos01: the saved picture's planes (0..1), dens:
    the film densities of the same part of the frame (exposure taken out). Finds the tone too.

    The two are compared spot by spot: the picture is the RAW file's own frame, so away from what the
    dust cleaner mended every spot follows the same straight line, and the mended spots (a scratched,
    thin frame has many) are left out of the fit. Only when the two do not lie on each other (the
    picture was turned or cut another way) are they compared by how much there is of each brightness.
    Returns {"tone", "a", "b", "err", "ends"} or None/{"bad": True} when the two do not match (other
    picture, or colour restoration or grain reduction were used)."""
    rows, cols = pos01[0].shape
    step = max(1, int(math.sqrt(rows * cols / 250_000)))
    small = [np.asarray(p[::step, ::step], np.float32) for p in pos01]
    shape = small[0].shape
    rgb = _to_srgb_like(np.stack([p.reshape(-1) for p in small], axis=1), space)
    v = [_inner(rgb[:, c].reshape(shape)).reshape(-1).astype(np.float64) for c in range(3)]
    where = (rows, cols, step)

    def close(a, b):
        return abs(a[0] / a[1] - b[0] / b[1]) < 0.04 * b[0] / b[1]

    turn, best_c = None, 0.5
    for k in range(8):
        g = _turned(dens[1], k)
        if not close(g.shape, (rows, cols)):
            continue
        x = _fit_same(g, where)
        if x.std() < 1e-6 or v[1].std() < 1e-6:
            continue
        cc = float(np.corrcoef(x[::3], v[1][::3])[0, 1])   # denser film is lighter in the picture
        if cc > best_c:
            turn, best_c = k, cc
        if k == 0 and cc > 0.7:
            break
    if turn is None:
        return _match_amounts(rgb, dens)
    best = None
    for tone in ("flat", "nikon"):
        a, b, errs, ends, at = [], [], [], [], []
        for c in range(3):
            d = _fit_same(_turned(dens[c], turn), where)
            ok0 = _usable(v[c], tone)
            if ok0.sum() < 500:
                break
            x = _untone(v[c], tone)
            mid = float(np.median(d[ok0]))
            ok = ok0
            for _ in range(5):
                ka, kb = np.polyfit(d[ok] - mid, x[ok], 1)
                res = np.abs(x - (ka * (d - mid) + kb))
                ok = ok0 & (res < max(3 * float(np.median(res[ok])), 0.004))
                if ok.sum() < 500:
                    break
            if ka <= 0 or ok.sum() < 500:
                break
            kb -= ka * mid
            a.append(float(ka)); b.append(float(kb)); errs.append(float(np.median(res[ok0])))
            # the film at the picture's own darkest and lightest 0.1% (the picture has its dust taken out)
            ends.append([float((e - kb) / ka) for e in np.percentile(x, (0.1, 99.9))])
            at.append([float(t) for t in np.percentile(x[ok0], AT_QS)])
        else:
            err = max(errs)
            if best is None or err < best["err"]:
                best = {"tone": tone, "a": a, "b": b, "err": err, "ends": ends, "at": at, "how": "spots"}
    line = _match_line(v, dens, turn, where)
    if best is not None and best["err"] <= 0.02 and (line is None or best["err"] <= 2 * line["err"] + 0.0005):
        return best
    if line is None or line["err"] > 0.012:
        return {**(best or line), "bad": True} if (best or line) else None
    return line


def _match_line(v, dens, turn, where):
    """match() for a picture that is not a straight line of its film's density: one saved with a film's
    colour curve. Each colour's own line through the spots is followed, whatever its shape: the middle
    density of the spots at every brightness of the picture (mended spots are few at any one brightness)."""
    tone = "nikon" if min(float(np.percentile(c, 0.5)) for c in v) > NIKON_TONE[0] - 0.01 else "flat"
    xk, dk, errs, ends, at = [], [], [], [], []
    for c in range(3):
        d = _fit_same(_turned(dens[c], turn), where)
        ok0 = _usable(v[c], tone)
        if ok0.sum() < 5000:
            return None
        x = _untone(v[c], tone)
        order = np.argsort(x[ok0])
        cuts = (np.concatenate([[0, 0.1, 0.3, 0.6, 1, 2, 3.5], np.arange(5, 96, 2.5), [96.5, 98, 99, 99.4, 99.7, 99.9, 100]])
                / 100 * len(order)).astype(int)
        parts = [order[i:j] for i, j in zip(cuts[:-1], cuts[1:]) if j - i >= 3]
        kd = np.array([np.median(d[ok0][q]) for q in parts])
        kx = np.array([np.median(x[ok0][q]) for q in parts])
        step_up = 1e-6 * np.arange(len(kd))
        kd = np.maximum.accumulate(kd - step_up) + step_up
        kx = np.maximum.accumulate(kx - step_up) + step_up
        errs.append(float(np.median(np.abs(x[ok0] - curve_apply(d[ok0], kd, kx)))))
        xk.append([float(t) for t in kx]); dk.append([float(t) for t in kd])
        ends.append([float(e) for e in curve_apply(np.percentile(x, (0.1, 99.9)), kx, kd)])
        at.append([float(t) for t in np.percentile(x[ok0], AT_QS)])
    line = {"tone": tone, "x": xk, "d": dk, "err": max(errs), "ends": ends, "at": at, "how": "line of spots"}
    return line


def _match_amounts(rgb, dens):
    """match() for a picture that does not lie on its RAW file spot by spot: by how much there is of each
    brightness in the two. rgb: the picture's (n, 3) values before the colour space."""
    best = None
    dq = [np.percentile(d, QS) for d in dens]
    for tone in ("flat", "nikon"):
        a, b, errs = [], [], []
        for c in range(3):
            vq = np.percentile(rgb[:, c], QS)
            ok = _usable(vq, tone)
            if ok.sum() < 12:
                break
            xq = _untone(vq[ok], tone)
            A = np.vstack([dq[c][ok], np.ones(ok.sum())]).T
            (ka, kb), *_ = np.linalg.lstsq(A, xq, rcond=None)
            if ka <= 0:
                break
            res = xq - (ka * dq[c][ok] + kb)
            a.append(float(ka)); b.append(float(kb)); errs.append(float(np.abs(res).max()))
        else:
            err = max(errs)
            if best is None or err < best["err"]:
                best = {"tone": tone, "a": a, "b": b, "err": err, "how": "amounts"}
    if best is None or best["err"] > 0.03:
        return best and {**best, "bad": True}
    best["ends"] = [[float(x) for x in np.percentile(_inner(d), (0.1, 99.9))] for d in dens]
    return best


def _mappers(m, numbers):
    """Per colour, a function from the saved picture's value (before tone and colour space) to the new one."""
    if "x" in m:   # the picture's value -> film density, along the picture's own line (match)
        tabs = [(np.asarray(m["x"][c], np.float32), np.asarray(m["d"][c], np.float32)) for c in range(3)]
        dens = [lambda x, t=t: curve_apply(x, t[0], t[1]) for t in tabs]
    else:
        dens = [lambda x, a=m["a"][c], b=m["b"][c]: (x - b) / a for c in range(3)]
    if numbers is None:   # each frame on its own: every colour stretched between its own ends
        out = []
        for c in range(3):
            e0, e1 = m["ends"][c]
            k = (1 - 2 * HEADROOM) / max(e1 - e0, 1e-6)
            out.append(lambda x, c=c, k=k, e0=e0: (dens[c](x) - e0) * k + HEADROOM)
        return out
    lo, hi = numbers["lo"], numbers["hi"]
    curve = _curve_of(numbers)
    if curve is not None:
        xs, y = curve
        ends = [curve_apply(np.asarray(e, np.float32) - lo[c], xs[c], y) for c, e in enumerate(m["ends"])]
        black, white = float(min(e[0] for e in ends)), float(max(e[1] for e in ends))
        k = (1 - 2 * HEADROOM) / max(white - black, 1e-6)
        return [lambda x, c=c: (curve_apply(dens[c](x) - lo[c], xs[c], y) - black) * k + HEADROOM for c in range(3)]
    s_ends = [((e[0] - a0) / (h - a0), (e[1] - a0) / (h - a0)) for e, a0, h in zip(m["ends"], lo, hi)]
    black, white = min(s[0] for s in s_ends), max(s[1] for s in s_ends)
    span = max(white - black, 1e-6)
    out = []
    for c in range(3):
        # x_new = (d - lo - black (hi - lo)) / ((hi - lo) span) * (1 - 2H) + H
        k = (1 - 2 * HEADROOM) / ((hi[c] - lo[c]) * span)
        out.append(lambda x, c=c, k=k: (dens[c](x) - lo[c] - black * (hi[c] - lo[c])) * k + HEADROOM)
    return out


def same_already(m, numbers):
    """True when the saved picture has this colour already (nothing to put on)."""
    grid = np.linspace(0.04, 0.96, 24, dtype=np.float32)
    at = m.get("at") or [grid] * 3
    return all(float(np.abs(f(np.asarray(x, np.float32)) - np.asarray(x, np.float32)).max()) < 0.018
               for f, x in zip(_mappers(m, numbers), at))


def recolour(pos01, space, m, numbers, rows_at_once=400):
    """The saved picture (planes 0..1) with the film's colour: new planes 0..1 (float32)."""
    maps = _mappers(m, numbers)
    tone = m["tone"]
    rows, cols = pos01[0].shape
    out = [np.empty((rows, cols), np.float32) for _ in range(3)]
    for r0 in range(0, rows, rows_at_once):
        rgb = np.stack([np.asarray(p[r0:r0 + rows_at_once], np.float32).reshape(-1) for p in pos01], axis=1)
        x = _untone(_to_srgb_like(rgb, space), tone)
        for c, f in enumerate(maps):
            x[:, c] = f(x[:, c])
        res = _from_srgb_like(_tone(x, tone), space)
        n = res.shape[0] // cols
        for c in range(3):
            out[c][r0:r0 + n] = res[:, c].reshape(n, cols)
    return out
