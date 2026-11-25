"""Finding the picture inside a frame: the film's edges, the holder and the gap between frames."""
import numpy as np

MAX_SIDE = 0.2      # no side is cut by more than this share of the frame
MARGIN = 0.006      # cut this much further in than the edge found, past its soft rim


def _straight_edge(L):
    """How many columns to cut from the left of L (log brightness, the side to look at turned to the
    left) where the frame ends in a straight line from top to bottom, with an even band outside it:
    the end of a frame on a strip, the black line D sees beside the picture, whatever its brightness.

    The band is not always darker or brighter than every part of the picture (a dark, thin negative
    has shadows as dark as the gap), so this looks for the step instead: a clear change in brightness
    at the same column in nearly every piece of the frame's height, with an even band outside it.
    0 when there is none."""
    h, w = L.shape
    lim = int(w * MAX_SIDE)
    d = max(1, int(round(w * 0.003)))          # half the width of a soft edge
    if lim < 4 * d + 4 or h < 24:
        return 0
    pieces = 12
    M = np.stack([np.median(p, axis=0) for p in np.array_split(L[:, :lim + 2 * d + 1], pieces, axis=0)])
    col = np.median(M, axis=0)
    ks = np.arange(d, lim)                     # the band can be as narrow as one column on a thumbnail
    grad = col[ks + d] - col[ks - d]
    mag = np.abs(grad)
    # Look at the clearest steps, strongest first: with a sliver of the next frame outside the gap, the
    # strongest step can be the gap's outer line, and the line between gap and picture comes second.
    peaks = [i for i in range(len(ks)) if mag[i] >= 0.4 and mag[i] == mag[max(0, i - d):i + d + 1].max()]
    for n, i in enumerate(sorted(peaks, key=lambda i: -mag[i])[:4]):
        cut = _edge_at(M, col, ks, int(ks[i]), float(grad[i]), d, w, sliver_only=n > 0)
        if cut:
            return cut
    return 0


def _edge_at(M, col, ks, k, step, d, w, sliver_only=False):
    """Columns to cut for a step of the given size at column k, or 0 when it is not the edge of an even
    band: a straight line along most of the height, with the band outside it even. sliver_only: take
    it only as the inner line of a band with a sliver outside it (a weaker step than the strongest is
    otherwise too often a line in the picture, close to the gap)."""
    pieces = len(M)
    tol = max(1, int(round(w * 0.01)))          # a frame a little askew moves its edge along the height
    near = (ks >= k - tol) & (ks <= k + tol)
    where = []
    for row in M:                               # where the picture beside it is as dark (or bright) as the
        g = (row[ks + d] - row[ks - d]) * np.sign(step)   # band, a piece shows no step: it need not
        g = np.where(near, g, -np.inf)
        j = int(np.argmax(g))
        if g[j] >= max(0.3, 0.4 * abs(step)):
            where.append(int(ks[j]))
    if len(where) < pieces * 7 // 12:           # not a line along most of the side: part of the picture
        return 0
    if not sliver_only and _even(M[:, :max(1, k - d)]):
        return max(where) + d
    # The band need not reach the side: a sliver of the next frame (or of the holder) can lie outside the
    # gap. Then the band starts at a second straight line, nearer the side, and all of it goes.
    j = _band_start(col, M, k, step, d, w)
    if j is None or not _even(M[:, j:max(j + 1, k - d)]):
        return 0
    return max(where) + d


def _even(band):
    """The band is the same along the side and across it: film, not picture."""
    levels = np.median(band, axis=1)
    return np.std(levels) <= 0.15 and np.median(np.ptp(band, axis=1)) <= 0.5


def _band_start(col, M, k, step, d, w):
    """Where the even band outside the step at column k begins, counted from the side: 0 when it
    reaches the side, else the column of a second straight line no more than 3% in from the side,
    with something else (a sliver of the next frame, the holder) beyond it. None when there is no
    such band, or it is too narrow to trust."""
    end = max(1, k - d)
    ref = float(np.median(col[max(0, end - 2 * d - 1):end]))
    off = np.abs(col[:end] - ref) > 0.3
    if not off.any():
        return None    # reaches the side: judged as a whole already
    j = int(np.nonzero(off)[0][-1]) + 1      # the first column of the even run up to the step
    if j > int(w * 0.03) or end - j < max(2, int(w * 0.006)):
        return None
    # The outer line is straight as well, and the band stands out from both sides the same way (brighter
    # than both, or darker than both), like the clear gap between two frames: in most pieces of the
    # height, what lies beyond the outer line differs from the band as the picture does. A band that is
    # only a step on the way from the gap into the picture does not.
    outer = M[:, max(0, j - 2 * d - 1):max(1, j - d)]
    inside = M[:, j + d:end] if end > j + d else M[:, j:end]
    gap = (np.median(outer, axis=1) - np.median(inside, axis=1)) * np.sign(step)
    return j if (gap >= 0.3).sum() >= len(M) * 7 // 12 else None


def find_picture(planes):
    """The picture area of a frame as (x0, y0, x1, y1), fractions of the picture as given
    (x across the columns, y down the rows), or None when nothing clear was found.

    planes: the colour planes (2-D, same shape) as scanned. Works on negatives and slides: what
    is cut is a band along a side that is either much darker than anything in the middle of the
    frame (the holder, the film's edge, a slide mount, an unexposed rim) or bright and even across
    its whole length (the clear film between two negatives).
    """
    if not planes:
        return None
    a = np.mean([np.asarray(p, np.float32) for p in planes], axis=0)
    h, w = a.shape
    if h < 24 or w < 24:
        return None
    L = np.log2(a + 16.0)
    centre = L[h // 4:3 * h // 4, w // 4:3 * w // 4]
    lo, hi = np.percentile(centre, [1, 99])
    spread = max(0.5, hi - lo)
    dark = L < lo - max(0.6, 0.15 * spread)
    # Clear film lets the most light through: a band at or near the scanner's ceiling, even along its
    # length, is the gap between negatives. Below the ceiling (a thumbnail's or preview's exposure) the
    # gap is found by its straight edge instead (_straight_edge, below): measured against the picture
    # alone, the shadows of a thin negative look just like it and were cut away.
    top_level = np.log2(65535 + 16.0)
    bright_rows = bright_cols = L > min(max(hi, top_level - 0.35), top_level - 0.1)

    def line_is_border(k, axis, part=slice(None)):  # axis 0: row k, axis 1: column k; part: a piece of it
        d = (dark[k] if axis == 0 else dark[:, k])[part]
        b = (bright_rows[k] if axis == 0 else bright_cols[:, k])[part]
        line = (L[k] if axis == 0 else L[:, k])[part]
        if d.mean() >= 0.6:
            return True
        rest = line[~d]   # leave out where the line crosses a dark band (the film's edge)
        if rest.size and (b[~d]).mean() >= 0.6:
            q1, q3 = np.percentile(rest, [5, 95])
            return q3 - q1 < 0.5   # even along its length: clear film, not a bright part of the picture
        return False

    def depth(n, axis, order, part):
        limit = int(n * MAX_SIDE)
        last = -1   # index (counted from this side) of the last border line found
        misses = 0
        for step, k in enumerate(order[:limit]):
            if line_is_border(k, axis, part):
                last, misses = step, 0
            else:
                misses += 1
                if misses > max(2, n // 200):
                    break
        return last + 1

    def inward(n, axis, order, rows=slice(None)):
        """How many lines to cut from this side. A frame that sits a little askew has a border
        that is wider at one end of the side than the other: each half of the side is measured on
        its own and the wider one is cut, but never much more than the side as a whole shows."""
        m = w if axis == 0 else h   # length of a line
        whole = depth(n, axis, order, rows)
        halves = max(depth(n, axis, order, slice(0, m // 2)), depth(n, axis, order, slice(m // 2, m)))
        found = min(max(whole, halves), whole + int(round(n * 0.03)))
        return found + int(round(n * MARGIN)) if found else 0

    top = inward(h, 0, list(range(h)))
    bottom = inward(h, 0, list(range(h - 1, -1, -1)))
    # the columns are judged between the top and bottom borders found: the film's edges and the holder
    # there are not always darker than the picture, and would make the clear gap look uneven
    rows = slice(top, h - bottom) if h - top - bottom >= 24 else slice(None)
    left = inward(w, 1, list(range(w)), rows)
    right = inward(w, 1, list(range(w - 1, -1, -1)), rows)
    # the ends of the frame along the strip: also look for a straight edge with an even band outside
    # it (between the top and bottom borders found), however bright or dark that band is
    inner = L[top:h - bottom] if h - top - bottom >= 24 else L
    margin = int(round(w * MARGIN))
    for side in ("left", "right"):
        e = _straight_edge(inner if side == "left" else inner[:, ::-1])
        if e:
            cut = min(e + margin, int(w * MAX_SIDE) + margin)
            if side == "left":
                left = max(left, cut)
            else:
                right = max(right, cut)
    if top == bottom == left == right == 0:
        return None
    x0, x1 = left / w, 1 - right / w
    y0, y1 = top / h, 1 - bottom / h
    if x1 - x0 < 0.5 or y1 - y0 < 0.5:
        return None   # most of the frame would go: something is off, leave it to the person
    return (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))
