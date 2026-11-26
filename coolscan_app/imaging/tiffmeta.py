"""Changing what a TIFF says about itself (the date the photo was taken, a caption, keywords, stars)
without touching its pixels.

How: a new directory of tags (IFD) is added at the end of the file and the file's header is pointed
at it. Everything else stays where it was: the pixels, and every other tag, which still points at its
old values. The old directory stays in the file unused (a few hundred bytes).

The caption, keywords and stars go in as XMP (the way Lightroom reads them: Metadata > Read Metadata
from Files); the date goes into EXIF (DateTimeOriginal, DateTimeDigitized) and the TIFF DateTime,
and into the XMP too, so both agree.

The camera the film was in, the aperture and the shutter speed go in the way a digital camera writes
them (Make, Model, FNumber, ExposureTime, in EXIF and XMP), so photo programs show them as the
camera's. The scanner's name, which Make and Model held, is kept in the XMP (coolscan:ScannerMake,
coolscan:ScannerModel) and comes back if the camera is taken out again.
"""

import datetime
import os
import re
import struct
from fractions import Fraction
from xml.sax.saxutils import escape, unescape

XMP_TAG = 700
EXIF_TAG = 34665
MARK = 'xmlns:coolscan="https://coolscan.local/ns/1.0/"'   # XMP the app wrote (any other is someone else's)
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


class NotOurs(ValueError):
    """The file has XMP from another program (Lightroom, ...): changing it here could lose that."""


def camera_parts(text):
    """("Make", "Model") for a camera as typed: "Nikon F3" -> ("Nikon", "Nikon F3"); ("", "") for none."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:80]
    return (text.split(" ", 1)[0], text) if text else ("", "")


def read_aperture(text):
    """The f-number typed as 5.6, f/5.6, ƒ5,6 or F 5.6. None for nothing typed; ValueError if it is not one."""
    text = str(text if text is not None else "").strip().lower().replace(",", ".")
    text = re.sub(r"^[fƒ]\s*/?\s*", "", text)
    if not text:
        return None
    if not re.fullmatch(r"\d+(\.\d+)?", text) or not 0.5 <= float(text) <= 128:
        raise ValueError("aperture")
    return round(float(text), 1)


def read_shutter(text):
    """The shutter speed typed as 1/125, 1/125 s, 125 (as on the camera's dial: 1/125), 2s, 2" or 0.5.
    Returns "1/125" or the seconds ("2", "0.5"); None for nothing typed; ValueError if it is not one."""
    text = str(text if text is not None else "").strip().lower().replace(",", ".")
    if not text:
        return None
    m = re.fullmatch(r"1\s*/\s*(\d+(?:\.\d+)?)\s*(?:s|sec)?", text)
    if m:
        n = float(m.group(1))
    else:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|\"|″|”)?", text)
        if not m:
            raise ValueError("shutter")
        v = float(m.group(1))
        if m.group(2) or "." in m.group(1) or v < 2:      # seconds: said so, a decimal, or 1
            if not 0 < v <= 3600:
                raise ValueError("shutter")
            return f"{v:g}" if v >= 1 or v != 1 / round(1 / v) else f"1/{round(1 / v)}"
        n = v                                              # a whole number as on the dial: 125 is 1/125
    if not 1 <= n <= 16000:
        raise ValueError("shutter")
    return "1" if n == 1 else f"1/{n:g}"


def _fraction(text):
    return Fraction(str(text)).limit_denominator(100000)


def nice_aperture(f):
    return f"ƒ/{float(f):g}" if f else ""


def nice_shutter(s):
    return f"{s} s" if s else ""


def scanner_parts(path):
    """(Make, Model) of the scanner, kept in the XMP of a file whose Make and Model now say the camera."""
    try:
        return _scanner_of(read_xmp(path).encode("utf-8"))
    except (OSError, ValueError, struct.error):
        return ("", "")


def read_scanner(path):
    """The same as one name ("Nikon COOLSCAN IV ED"), or ''."""
    make, model = scanner_parts(path)
    return (model if model.lower().startswith(make.lower()) else f"{make} {model}").strip()


def _scanner_of(xmp):
    out = []
    for key in (b"ScannerMake", b"ScannerModel"):
        m = re.search(b"<coolscan:" + key + b">(.*?)</coolscan:" + key + b">", xmp, re.S)
        out.append(unescape(m.group(1).decode("utf-8", "replace")) if m else "")
    return tuple(out)


def _read_ifd(f, order, at):
    f.seek(at)
    count = struct.unpack(order + "H", f.read(2))[0]
    raw = f.read(12 * count)
    nxt = struct.unpack(order + "I", f.read(4))[0]
    entries = []
    for k in range(count):
        tag, typ, n = struct.unpack(order + "HHI", raw[12 * k:12 * k + 8])
        entries.append([tag, typ, n, raw[12 * k + 8:12 * k + 12]])
    return entries, nxt


def _value(f, order, entry):
    tag, typ, n, val = entry
    size = _SIZES.get(typ, 1) * n
    if size > 4:
        f.seek(struct.unpack(order + "I", val)[0])
        return f.read(size)
    return val[:size]


def read_xmp(path):
    """The file's XMP packet as text, or ''."""
    with open(path, "rb") as f:
        order, ifd0 = _head(f)
        entries, _ = _read_ifd(f, order, ifd0)
        for e in entries:
            if e[0] == XMP_TAG:
                return _value(f, order, e).decode("utf-8", "replace")
    return ""


def _head(f):
    head = f.read(8)
    order = {b"II": "<", b"MM": ">"}.get(head[:2])
    if order is None or len(head) < 8 or struct.unpack(order + "H", head[2:4])[0] != 42:
        raise ValueError("not a TIFF file (or a BigTIFF, which is not changed here)")
    return order, struct.unpack(order + "I", head[4:8])[0]


def make_xmp(caption="", tags=(), rating=0, taken=None, people=(), date_note="", creator="", rights="",
             camera="", fnumber=None, exposure="", scanner=None):
    """An XMP packet with what Lightroom shows as Caption, Keywords, Rating and Capture Time."""
    parts = []
    if rating:
        parts.append(f"<xmp:Rating>{int(rating)}</xmp:Rating>")
    if taken:
        stamp = taken.strftime("%Y-%m-%dT%H:%M:%S")
        parts.append(f"<exif:DateTimeOriginal>{stamp}</exif:DateTimeOriginal>")
        parts.append(f"<photoshop:DateCreated>{stamp}</photoshop:DateCreated>")
    if caption:
        parts.append('<dc:description><rdf:Alt><rdf:li xml:lang="x-default">' + escape(caption)
                     + "</rdf:li></rdf:Alt></dc:description>")
    words = list(tags) + [p for p in people if p not in tags]   # people are keywords too, so Lightroom can find them
    if words:
        parts.append("<dc:subject><rdf:Bag>" + "".join(f"<rdf:li>{escape(t)}</rdf:li>" for t in words)
                     + "</rdf:Bag></dc:subject>")
    if people:
        parts.append("<Iptc4xmpExt:PersonInImage><rdf:Bag>" + "".join(f"<rdf:li>{escape(t)}</rdf:li>" for t in people)
                     + "</rdf:Bag></Iptc4xmpExt:PersonInImage>")
    if date_note:
        parts.append(f"<coolscan:DateNote>{escape(date_note)}</coolscan:DateNote>")
    if creator:
        parts.append(f"<dc:creator><rdf:Seq><rdf:li>{escape(creator)}</rdf:li></rdf:Seq></dc:creator>")
    if rights:
        parts.append('<dc:rights><rdf:Alt><rdf:li xml:lang="x-default">' + escape(rights) + "</rdf:li></rdf:Alt></dc:rights>")
    make, model = camera_parts(camera)
    if model:
        parts.append(f"<tiff:Make>{escape(make)}</tiff:Make>")
        parts.append(f"<tiff:Model>{escape(model)}</tiff:Model>")
        for key, text in zip(("ScannerMake", "ScannerModel"), scanner or ()):
            if text:
                parts.append(f"<coolscan:{key}>{escape(text)}</coolscan:{key}>")
    if fnumber:
        f = _fraction(fnumber)
        parts.append(f"<exif:FNumber>{f.numerator}/{f.denominator}</exif:FNumber>")
    if exposure:
        e = _fraction(exposure)
        parts.append(f"<exif:ExposureTime>{e.numerator}/{e.denominator}</exif:ExposureTime>")
    body = "\n   ".join(parts)
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:exif="http://ns.adobe.com/exif/1.0/"'
            ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/"'
            f' xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" {MARK}>\n'
            f'   {body}\n'
            '  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n<?xpacket end="w"?>')


def update_tiff(path, caption="", tags=(), rating=0, taken=None, force=False, people=(), date_note="", creator="", rights="",
                camera="", fnumber=None, exposure="", scanner=None):
    """Write caption, keywords, stars and (if given) the date taken into a TIFF. Pixels are not touched.

    A file whose XMP was written by another program is refused (NotOurs) unless force: that XMP may
    hold edits that would be lost."""
    before = os.stat(path)
    with open(path, "r+b") as f:
        order, ifd0 = _head(f)
        entries, nxt = _read_ifd(f, order, ifd0)
        old_xmp = next((_value(f, order, e) for e in entries if e[0] == XMP_TAG), b"")
        if old_xmp and MARK.encode() not in old_xmp and not force:
            raise NotOurs("this file already has metadata from another program (Lightroom?)")
        exif_entries, exif_next = [], 0
        exif_at = next((struct.unpack(order + "I", e[3])[0] for e in entries if e[0] == EXIF_TAG), None)
        if exif_at:
            exif_entries, exif_next = _read_ifd(f, order, exif_at)
        f.seek(0, os.SEEK_END)
        end = f.tell()
        tail = bytearray(b"\0" if end % 2 else b"")
        start = end + len(tail)

        def put(blob):  # an out-of-line value at the end of the file; returns where it is
            at = start + len(tail)
            tail.extend(blob)
            if len(tail) % 2:
                tail.append(0)
            return at

        def entry(tag, typ, n, blob):
            if len(blob) <= 4:
                return [tag, typ, n, blob.ljust(4, b"\0")]
            return [tag, typ, n, struct.pack(order + "I", put(blob))]

        def ascii_entry(tag, text):
            blob = text.encode("ascii", "replace") + b"\0"
            return entry(tag, 2, len(blob), blob)

        def text_entry(tag, text):   # a camera's name may not be plain ASCII; UTF-8 is what readers expect then
            blob = text.encode("utf-8") + b"\0"
            return entry(tag, 2, len(blob), blob)

        def text_of(tag):
            e = next((e for e in entries if e[0] == tag and e[1] == 2), None)
            return _value(f, order, e).split(b"\0", 1)[0].decode("utf-8", "replace").strip() if e else ""

        keep = {e[0]: e for e in entries if e[0] not in (XMP_TAG,)}
        # Make and Model: the camera when there is one; the scanner they named is kept in the XMP
        cam, now = camera_parts(camera), (text_of(271), text_of(272))
        kept = _scanner_of(old_xmp) if old_xmp and MARK.encode() in old_xmp else ("", "")
        if scanner is None:
            scanner = kept if any(kept) else (now if cam[1] and now != cam else ("", ""))
        scanner = tuple(scanner) if cam[1] and tuple(scanner) != cam else ("", "")
        if cam[1]:
            keep[271], keep[272] = text_entry(271, cam[0]), text_entry(272, cam[1])
        elif any(kept):               # the camera was taken out: Make and Model say the scanner again
            for tag, text in zip((271, 272), kept):
                if text:
                    keep[tag] = text_entry(tag, text)
                else:
                    keep.pop(tag, None)
        xmp = make_xmp(caption, tags, rating, taken, people, date_note, creator, rights,
                       camera, fnumber, exposure, scanner).encode("utf-8")
        keep[XMP_TAG] = entry(XMP_TAG, 1, len(xmp), xmp)
        if creator:
            keep[315] = ascii_entry(315, creator)        # Artist
        if rights:
            keep[33432] = ascii_entry(33432, rights)     # Copyright
        ex = {e[0]: e for e in exif_entries}
        was = (33434 in ex, 33437 in ex)
        for tag, value in ((33434, exposure), (33437, fnumber)):   # ExposureTime, FNumber
            if value:
                fr = _fraction(value)
                ex[tag] = entry(tag, 5, 1, struct.pack(order + "II", fr.numerator, fr.denominator))
            else:
                ex.pop(tag, None)
        if taken:
            stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
            keep[306] = ascii_entry(306, stamp)
            ex[36867] = ascii_entry(36867, stamp)
            ex[36868] = ascii_entry(36868, stamp)
        if taken or exposure or fnumber or any(was):
            ex.setdefault(36864, [36864, 7, 4, b"0230"])
            ex_list = [ex[k] for k in sorted(ex)]
            blob = struct.pack(order + "H", len(ex_list)) + b"".join(
                struct.pack(order + "HHI", t, ty, n) + v for t, ty, n, v in ex_list) + struct.pack(order + "I", exif_next)
            keep[EXIF_TAG] = [EXIF_TAG, 4, 1, struct.pack(order + "I", put(blob))]
        ifd = [keep[k] for k in sorted(keep)]
        blob = struct.pack(order + "H", len(ifd)) + b"".join(
            struct.pack(order + "HHI", t, ty, n) + v for t, ty, n, v in ifd) + struct.pack(order + "I", nxt)
        new_ifd = put(blob)
        f.seek(end)   # (reading old values above moved the place in the file)
        f.write(bytes(tail))
        f.flush()
        os.fsync(f.fileno())
        f.seek(4)   # the one byte range of the old file that changes: where the tags are
        f.write(struct.pack(order + "I", new_ifd))
        f.flush()
        os.fsync(f.fileno())
    if taken:
        t = taken.timestamp()
        os.utime(path, (t, t))   # like a fresh scan: the file's date is the date taken, which galleries sort by
    else:
        os.utime(path, (before.st_atime, before.st_mtime))   # only the tags changed: the file keeps its date
