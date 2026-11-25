"""Where each frame sits on the strip: the driver's frame positions, fitted to the gaps between the
negatives in the thumbnail, and moved by hand.

The driver (nkscan) finds the frames in its thumbnail pass and gives each one as scanner addresses
(top, left, bottom, right): top and bottom run along the strip (the feed), left and right across it.
Those positions are also the window the scanner reads when the frame is scanned. When they are a
little off, a frame's picture starts late or runs on into the gap and the next frame.

Strip wraps the driver's result so the rest of the app keeps using `discovery.frames` as before:
`frames` holds the windows in use (fitted and moved), `found` keeps the driver's own.
"""
import bisect

import numpy as np

MOST_MOVE = 0.15        # an edge is never moved further than this share of the frame's length
SEARCH = 0.25           # look for a gap this share of a frame's length either side of the driver's edge
NUDGE = 0.01            # one press of a nudge arrow moves a frame by this share of its length
NUDGE_MOST = 0.25       # and all the presses together by no more than this


class Strip:
    """The driver's discovery with the frame windows the app uses. Everything else is passed through."""

    def __init__(self, raw):
        self._raw = raw
        self.found = [tuple(int(v) for v in f) for f in raw.frames]
        self.fitted = None            # the windows fitted to the gaps, once worked out
        self.fit_note = ""
        self.shift = {}               # frame -> nudge presses (+ = later along the strip)
        self.frames = list(self.found)

    def __getattr__(self, name):      # thumbnail, column_addresses, base_density, ... as the driver gave them
        return getattr(self._raw, name)

    def windows(self, fit):
        """Work out `frames` from the driver's windows, the fitted ones (when fit is on) and the nudges."""
        base = self.fitted if (fit and self.fitted) else self.found
        lo, hi = _address_range(self._raw)
        out = []
        for i, (top, left, bottom, right) in enumerate(base):
            n = self.shift.get(i, 0)
            if n:
                move = int(round(n * NUDGE * (bottom - top)))
                if lo is not None:
                    move = max(lo - top, min(hi - bottom, move))
                top, bottom = top + move, bottom + move
            out.append((top, left, bottom, right))
        self.frames = out
        return out

    def state(self, i, fit):
        """How frame i's window was placed, for the page."""
        n = self.shift.get(i, 0)
        fitted = bool(fit and self.fitted and self.fitted[i] != self.found[i])
        return {"shift": n, "fitted": fitted,
                "moved": round(n * NUDGE * 100)}


def _address_range(d):
    cols = list(getattr(d, "column_addresses", None) or [])
    return (cols[0], cols[-1]) if cols else (None, None)


def _levels(thumbnail):
    """Per thumbnail column (along the strip): median log brightness across the middle of the film,
    and how much it varies there (small for clear film or the black between slides)."""
    planes = [np.asarray(p, np.float32) for k, p in thumbnail.items() if k in ("red", "green", "blue", "gray", "grey")]
    if not planes:
        planes = [np.asarray(p, np.float32) for p in thumbnail.values()]
    L = np.log2(np.mean(planes, axis=0) + 16.0)
    rows = L.shape[0]
    mid = L[int(rows * 0.15):max(int(rows * 0.85), int(rows * 0.15) + 1)]
    return np.median(mid, axis=0), np.percentile(mid, 90, axis=0) - np.percentile(mid, 10, axis=0)


def _by_spacing(gaps, slack):
    """Gaps not seen, placed from the ones that were: frames on a strip follow one another at a nearly
    fixed spacing, so the seen gaps' middles lie on a straight line over their number. Only when at least
    three were seen and they all lie within `slack` columns of that line; otherwise nothing is placed."""
    seen = [(k, g) for k, g in enumerate(gaps) if g is not None]
    missing = [k for k, g in enumerate(gaps) if g is None]
    if len(seen) < 3 or not missing:
        return {}
    ks = np.array([k for k, _ in seen], float)
    mids = np.array([(g0 + g1) / 2 for _, (g0, g1) in seen])
    slope, at0 = np.polyfit(ks, mids, 1)
    if np.max(np.abs(mids - (slope * ks + at0))) > slack:
        return {}
    width = int(round(np.median([g1 - g0 for _, (g0, g1) in seen])))
    out = {}
    for k in missing:
        mid = slope * k + at0
        g0 = int(round(mid - width / 2))
        out[k] = (g0, g0 + width)
    return out


def fit_frames(d):
    """Frame windows moved onto the gaps found in the thumbnail, and a note saying what moved.

    For each pair of neighbouring frames the gap between them is looked for near the driver's edges:
    columns that are even across the film and at least as bright as the brightest of the two pictures
    (clear film, between negatives) or as dark as their darkest (between slides). The earlier frame then
    ends where the gap starts and the later one starts where it ends. An edge with no clear gap near it,
    or one that would move too far, stays where the driver put it."""
    found = [tuple(int(v) for v in f) for f in d.frames]
    cols = list(getattr(d, "column_addresses", None) or [])
    thumb = getattr(d, "thumbnail", None)
    if thumb is None or len(cols) < 8 or len(found) < 2:
        return None, "no thumbnail to fit the frames to"
    m, even = _levels(thumb)
    ncols = len(m)
    if ncols != len(cols):
        return None, "the thumbnail and its addresses do not match"

    def col(address):
        return min(ncols - 1, max(0, bisect.bisect_left(cols, address)))

    order = sorted(range(len(found)), key=lambda i: found[i][0])
    new = [list(f) for f in found]

    def span(f):
        return col(f[0]), col(f[2])

    def runs_near(frames, centre, length):
        """Runs of gap columns within SEARCH of `centre`: even across the film (clear film or the black
        between slides is even; a picture hardly ever is) and as bright as the brightest, or as dark as the
        darkest, of the neighbouring pictures' middles."""
        inner = np.concatenate([m[c0 + (c1 - c0) // 5: c1 - (c1 - c0) // 5] for c0, c1 in map(span, frames)])
        if inner.size < 4:
            return []
        inner_even = np.concatenate([even[c0 + (c1 - c0) // 5: c1 - (c1 - c0) // 5] for c0, c1 in map(span, frames)])
        hi, lo = np.percentile(inner, 99), np.percentile(inner, 1)
        flat = min(0.25, 0.6 * float(np.percentile(inner_even, 10)))   # clearly more even than the pictures
        start = max(0, int(centre - length * SEARCH))
        stop = min(ncols, int(centre + length * SEARCH) + 1)
        w = slice(start, stop)
        gap = np.zeros(ncols, bool)
        gap[w] = (even[w] <= flat) & ((m[w] >= hi - 0.1) | (m[w] <= lo + 0.1) | (even[w] <= 0.5 * flat))
        runs, k = [], start
        while k < stop:
            if gap[k]:
                j = k
                while j + 1 < stop and gap[j + 1]:
                    j += 1
                runs.append((k, j))
                k = j + 1
            else:
                k += 1
        # a gap is clearly more even than the pictures beside it; with none like that, nothing is moved
        # and its level steps away from the pictures on both sides (a flat patch of sky or base at a
        # picture's edge runs on into the picture at the same level)
        def stands_out(r):
            level = float(np.median(m[r[0]:r[1] + 1]))
            before, after = m[max(0, r[0] - 3):r[0]], m[r[1] + 1:r[1] + 4]
            return all(s.size == 0 or abs(float(np.median(s)) - level) >= 0.3 for s in (before, after))
        runs = [r for r in runs if float(np.median(even[r[0]:r[1] + 1])) <= flat and stands_out(r)]
        def key(r):   # the flattest first (bare film, the black between slides), then the nearest
            very_flat = float(np.median(even[r[0]:r[1] + 1])) <= 0.5 * flat
            return (not very_flat, 0 if r[0] <= centre <= r[1] else min(abs(r[0] - centre), abs(r[1] - centre)))
        return sorted(runs, key=key)

    def set_edge(i, which, address):
        f = found[i]
        length = f[2] - f[0]
        if abs(address - f[which]) > MOST_MOVE * length:
            return False
        trial = list(new[i])
        trial[which] = address
        if trial[2] - trial[0] > 0.7 * length:
            new[i] = trial
            return True
        return False

    gaps = []                                   # per boundary: the gap's columns (g0, g1), or None
    for a, b in zip(order, order[1:]):          # the gap between two neighbouring frames
        (ca0, ca1), (cb0, cb1) = span(found[a]), span(found[b])
        runs = runs_near([found[a], found[b]], (ca1 + cb0) / 2, max(ca1 - ca0, cb1 - cb0, 4))
        gaps.append(None)
        if runs:
            g0, g1 = runs[0]
            ok_a, ok_b = set_edge(a, 2, cols[g0]), set_edge(b, 0, cols[min(ncols - 1, g1 + 1)])
            if ok_a and ok_b:
                gaps[-1] = (g0, g1)
    by_spacing = _by_spacing(gaps, max(1.0, 0.015 * float(np.median([c1 - c0 for c0, c1 in map(span, found)]))))
    for k, g in by_spacing.items():             # faint gaps: placed at the strip's frame spacing
        a, b = order[k], order[k + 1]
        set_edge(a, 2, cols[max(0, g[0])])
        set_edge(b, 0, cols[min(ncols - 1, g[1] + 1)])
    for i, which in ((order[0], 0), (order[-1], 2)):   # the outer ends of the first and the last frame
        c0, c1 = span(found[i])
        edge = c0 if which == 0 else c1
        runs = runs_near([found[i]], edge, max(c1 - c0, 4))
        if runs:
            g0, g1 = runs[0]
            if which == 0 and g0 <= edge + (c1 - c0) * SEARCH:
                set_edge(i, 0, cols[min(ncols - 1, g1 + 1)])
            elif which == 2:
                set_edge(i, 2, cols[g0])
    moved = [i for i in range(len(found)) if tuple(new[i]) != found[i]]
    fitted = [tuple(f) for f in new]
    if not moved:
        return fitted, "the frames already sit on the gaps"
    shares = []
    for i in sorted(moved):
        f, g = found[i], fitted[i]
        length = f[2] - f[0]
        shares.append(f"{i + 1} ({(g[0] - f[0]) / length:+.0%} / {(g[2] - f[2]) / length:+.0%})")
    return fitted, "fitted to the gaps: frame " + ", ".join(shares) + " (start / end)"
