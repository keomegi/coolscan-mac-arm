"""What the app knows about each Coolscan model, beyond what the scanner itself says.

nkscan reads the scanner's own capability pages (resolution, multi-sampling, how frames are found,
film formats), and the app already sets its controls from those. This table adds what the pages do
not say: the model's own name, how it connects, which holders nkscan has run on real hardware
(its README, 0.20.0), and the frame sizes of the film formats, for the crop size estimate.

The six models nkscan drives are the six Nikon Scan 4 drove. The Coolscan III (LS-30) and older
units are not among them: nkscan does not know them.
"""

# model: what the app says about it. "tested" and "untested" are the holders nkscan lists as run on
# real hardware or not (README, 0.20.0). "roll": takes the SA-30 roll adapter, which feeds a whole
# uncut roll (up to 40 frames). "medium": also scans 120 film.
MODELS = {
    "LS-40": dict(name="Coolscan IV ED", dpi=2900, link="USB",
                  tested=["SA-21", "MA-20/21"], untested=["IA-20/21", "SA-30", "SF-210/200"], roll=True),
    "LS-50": dict(name="Coolscan V ED", dpi=4000, link="USB",
                  tested=["SA-21", "IA-20/21", "MA-20/21", "SA-30", "SF-210/200"], untested=[], roll=True),
    "LS-4000": dict(name="Super Coolscan 4000 ED", dpi=4000, link="FireWire",
                    tested=["SA-21", "SA-30"], untested=["IA-20/21", "MA-20/21", "SF-210/200"], roll=True),
    "LS-5000": dict(name="Super Coolscan 5000 ED", dpi=4000, link="USB",
                    tested=["SA-21", "MA-20/21", "SA-30"], untested=["IA-20/21", "SF-210/200"], roll=True),
    "LS-8000": dict(name="Super Coolscan 8000 ED", dpi=4000, link="FireWire", medium=True,
                    tested=["FH-869S", "FH-869G"], untested=["FH-835M", "FH-835S", "FH-869GR", "FH-869M", "FH-816", "FH-8G1"]),
    "LS-9000": dict(name="Super Coolscan 9000 ED", dpi=4000, link="FireWire", medium=True,
                    tested=["FH-835S", "FH-869S"], untested=["FH-835M", "FH-869G", "FH-869GR", "FH-869M", "FH-816", "FH-8G1"]),
}

# The long side of a frame of each film format, in inches, for the crop size estimate. 35 mm is left
# out on purpose: for it the page keeps its own estimate (4320 px at 2900 dpi), as it always had.
# 120 lengths are the ones nkscan scans (6x4.5 41.5 mm, 6x7 69.5 mm, 6x8 76 mm; 6x6 and 6x9 their gates)
FORMAT_LONG_IN = {
    "half": 18 / 25.4, "IX240": 30.2 / 25.4, "16": 14 / 25.4,
    "645": 41.5 / 25.4, "66": 56 / 25.4, "67": 69.5 / 25.4, "68": 76 / 25.4, "69": 83 / 25.4,
}

# what a format is called on the page
FORMAT_NAMES = {"135": "35 mm", "half": "Half frame", "IX240": "APS (IX240)", "16": "16 mm",
                "645": "6×4.5", "66": "6×6", "67": "6×7", "68": "6×8", "69": "6×9"}

# a strip longer than this many frames is shown as a long strip: one row of bigger frames that scrolls
LONG_STRIP = 6


def dpi_choices(caps):
    """The resolutions the page offers for this scanner: its optical dpi divided by 1 to 40, down to
    its lowest (the same list app.js makes). On the LS-40: 2900, 1450, 967, ..., 290, ..."""
    opt = int(getattr(caps, "optical_dpi", 0) or 2900)
    lo = (getattr(caps, "x_dpi_range", None) or (73, opt))[0]
    return [d for d in (round(opt / n) for n in range(1, 41)) if d >= lo]


def nearest_dpi(dpi, choices):
    """dpi if the scanner offers it, else the nearest one it does (2900 on a 4000 dpi scanner: 4000)."""
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        return choices[0]
    return dpi if dpi in choices else min(choices, key=lambda d: (abs(d - dpi), -d))


def model_key(caps):
    """'LS-40', 'LS-5000', ... for these capabilities, or None for a scanner the table does not know.
    nkscan gives the model as 'LS-40'; the product field says 'LS-40 ED' (longest name first, as
    'LS-5000 ED' holds 'LS-50')."""
    if caps is None:
        return None
    m = str(getattr(caps, "model", "") or "").strip()
    if m in MODELS:
        return m
    product = str(getattr(caps, "product", "") or "")
    for key in sorted(MODELS, key=len, reverse=True):
        if key in product:
            return key
    return None


def model_info(caps):
    """The table's entry for this scanner (with its key), or {} when it is not in the table."""
    key = model_key(caps)
    return dict(MODELS[key], key=key) if key else {}


def frame_long_in(fmt):
    """The long side of a frame of this format in inches, or None for 35 mm (and anything unknown):
    the page then keeps its 35 mm estimate."""
    if not fmt:
        return None
    fmt = str(fmt)
    if fmt in FORMAT_LONG_IN:
        return FORMAT_LONG_IN[fmt]
    try:  # a custom length in mm (LS-8000/9000)
        mm = float(fmt)
    except ValueError:
        return None
    return mm / 25.4 if 20 <= mm <= 90 else None


def about(caps):
    """One line for the Activity list on connecting, for a model other than the LS-40 (whose lines
    stay as they were): its name, how it connects and what nkscan has run it with. '' otherwise."""
    info = model_info(caps)
    if not info or info["key"] == "LS-40":
        return ""
    words = f"{info['key']} is the Nikon {info['name']}, {info['dpi']} dpi, {info['link']}"
    if info["tested"]:
        words += "; nkscan has run it with the " + ", ".join(info["tested"])
    if info["untested"]:
        words += " (not yet with the " + ", ".join(info["untested"]) + ")"
    if info.get("medium"):
        words += "; 35 mm and 120 film: pick the format under Film"
    if info["link"] == "FireWire":
        words += ". On a Mac without FireWire it needs the ASFireWire driver and an adapter"
    return words
