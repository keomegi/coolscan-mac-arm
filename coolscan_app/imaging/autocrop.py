"""Finding the picture inside a frame: the film's edges, the holder and the gap between frames."""
import numpy as np

MAX_SIDE = 0.2      # no side is cut by more than this share of the frame
MARGIN = 0.006      # cut this much further in than the edge found, past its soft rim


def _lines(L, axis_lines):
    """Rows (axis_lines=0) or columns (1) of L as lines, each a 1-D array."""
    return L if axis_lines == 0 else L.T


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
    top_level = np.log2(65535 + 16.0)
    bright = L > min(max(hi, top_level - 0.35), top_level - 0.1)   # at or near the scanner's ceiling

    def line_is_border(k, axis, part=slice(None)):  # axis 0: row k, axis 1: column k; part: a piece of it
        d = (dark[k] if axis == 0 else dark[:, k])[part]
        b = (bright[k] if axis == 0 else bright[:, k])[part]
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

    def inward(n, axis, order):
        """How many lines to cut from this side. A frame that sits a little askew has a border
        that is wider at one end of the side than the other: each half of the side is measured on
        its own and the wider one is cut, but never much more than the side as a whole shows."""
        m = w if axis == 0 else h   # length of a line
        whole = depth(n, axis, order, slice(None))
        halves = max(depth(n, axis, order, slice(0, m // 2)), depth(n, axis, order, slice(m // 2, m)))
        found = min(max(whole, halves), whole + int(round(n * 0.03)))
        return found + int(round(n * MARGIN)) if found else 0

    top = inward(h, 0, list(range(h)))
    bottom = inward(h, 0, list(range(h - 1, -1, -1)))
    left = inward(w, 1, list(range(w)))
    right = inward(w, 1, list(range(w - 1, -1, -1)))
    if top == bottom == left == right == 0:
        return None
    x0, x1 = left / w, 1 - right / w
    y0, y1 = top / h, 1 - bottom / h
    if x1 - x0 < 0.5 or y1 - y0 < 0.5:
        return None   # most of the frame would go: something is off, leave it to the person
    return (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))
