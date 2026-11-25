"""Changing what a TIFF says about itself (the date the photo was taken, a caption, keywords, stars)
without touching its pixels.

How: a new directory of tags (IFD) is added at the end of the file and the file's header is pointed
at it. Everything else stays where it was: the pixels, and every other tag, which still points at its
old values. The old directory stays in the file unused (a few hundred bytes).

The caption, keywords and stars go in as XMP (the way Lightroom reads them: Metadata > Read Metadata
from Files); the date goes into EXIF (DateTimeOriginal, DateTimeDigitized) and the TIFF DateTime,
and into the XMP too, so both agree.
"""

import datetime
import os
import struct
from xml.sax.saxutils import escape

XMP_TAG = 700
EXIF_TAG = 34665
MARK = 'xmlns:coolscan="https://coolscan.local/ns/1.0/"'   # XMP the app wrote (any other is someone else's)
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


class NotOurs(ValueError):
    """The file has XMP from another program (Lightroom, ...): changing it here could lose that."""


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


def make_xmp(caption="", tags=(), rating=0, taken=None, people=(), date_note=""):
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
    body = "\n   ".join(parts)
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:exif="http://ns.adobe.com/exif/1.0/"'
            ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"'
            f' xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" {MARK}>\n'
            f'   {body}\n'
            '  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n<?xpacket end="w"?>')


def update_tiff(path, caption="", tags=(), rating=0, taken=None, force=False, people=(), date_note=""):
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

        xmp = make_xmp(caption, tags, rating, taken, people, date_note).encode("utf-8")
        keep = {e[0]: e for e in entries if e[0] not in (XMP_TAG,)}
        keep[XMP_TAG] = entry(XMP_TAG, 1, len(xmp), xmp)
        if taken:
            stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
            keep[306] = ascii_entry(306, stamp)
            ex = {e[0]: e for e in exif_entries}
            ex[36867] = ascii_entry(36867, stamp)
            ex[36868] = ascii_entry(36868, stamp)
            ex.setdefault(36864, [36864, 7, 4, b"0230"])
            ex_list = [ex[k] for k in sorted(ex)]
            blob = struct.pack(order + "H", len(ex_list)) + b"".join(
                struct.pack(order + "HHI", t, ty, n) + v for t, ty, n, v in ex_list) + struct.pack(order + "I", exif_next)
            keep[EXIF_TAG] = [EXIF_TAG, 4, 1, struct.pack(order + "I", put(blob))]
        ifd = [keep[k] for k in sorted(keep)]
        blob = struct.pack(order + "H", len(ifd)) + b"".join(
            struct.pack(order + "HHI", t, ty, n) + v for t, ty, n, v in ifd) + struct.pack(order + "I", nxt)
        new_ifd = put(blob)
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
