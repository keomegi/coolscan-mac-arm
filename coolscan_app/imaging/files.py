"""Writing PNG and TIFF files, and reading a TIFF back."""

import datetime
import json
import math
import os
import struct
import zlib

import numpy as np


THUMB_TAG = 65001     # a private TIFF tag in RAW files: the frame's piece of the thumbnail pass (see pack_thumb_pass)
COOLSCAN_TAG = 65000  # a private TIFF tag: the app's note on how the scan was made, as JSON text


def _png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(img):
    """img: uint8 array, (rows, cols) or (rows, cols, 3)."""
    img = np.ascontiguousarray(img)
    rows, cols = img.shape[:2]
    colour = 2 if img.ndim == 3 else 0
    raw = np.zeros((rows, img.strides[0] + 1), dtype=np.uint8)
    raw[:, 1:] = img.reshape(rows, -1)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 8, colour, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + _png_chunk(b"IEND", b""))


def pack_thumb_pass(planes):
    """A frame's piece of the thumbnail pass as bytes: "CSTH", rows, cols, planes (uint16 each), then the
    planes as 16-bit numbers, one after the other."""
    planes = [np.ascontiguousarray(np.clip(np.asarray(p, np.float64), 0, 65535).astype("<u2")) for p in planes]
    rows, cols = planes[0].shape
    return b"CSTH" + struct.pack("<HHH", rows, cols, len(planes)) + b"".join(p.tobytes() for p in planes)


def unpack_thumb_pass(blob):
    if not blob or blob[:4] != b"CSTH" or len(blob) < 10:
        return None
    rows, cols, n = struct.unpack("<HHH", blob[4:10])
    if len(blob) < 10 + rows * cols * n * 2 or not rows * cols * n:
        return None
    a = np.frombuffer(blob, "<u2", rows * cols * n, 10).reshape(n, rows, cols)
    return [a[k].copy() for k in range(n)]


def read_thumb_pass(path):
    """The thumbnail pass kept inside a RAW file (planes turned like the file), or None."""
    try:
        return read_tiff_info(path, thumb=True).get("thumb_pass")
    except (OSError, ValueError, KeyError, struct.error):
        return None


def write_tiff(path, planes, dpi, icc=None, meta=None):
    """Write an uncompressed 16-bit TIFF. planes: uint16 2-D arrays, 1 (grey), 3 (RGB)
    or 4 (RGB plus infrared as a fourth channel, as SilverFast's RGBI archive files do).

    meta (optional): {"taken": datetime, "description": str, "make": str, "model": str}.
    "taken" becomes the EXIF date the photo was taken, which galleries sort by, and
    the file's modification time."""
    meta = meta or {}
    data = np.dstack(planes) if len(planes) > 1 else planes[0]
    data = np.ascontiguousarray(data.astype("<u2"))
    rows, cols = data.shape[:2]
    spp = len(planes)
    pixels = data.tobytes()
    rows_per_strip = max(1, (1 << 20) // (cols * spp * 2))
    strip_bytes = cols * spp * 2 * rows_per_strip
    nstrips = math.ceil(rows / rows_per_strip)
    counts = [min(strip_bytes, len(pixels) - i * strip_bytes) for i in range(nstrips)]

    extra = bytearray()  # out-of-line values, placed after the 8-byte header
    base = 8

    def put(blob):
        off = base + len(extra)
        extra.extend(blob)
        if len(extra) % 2:
            extra.append(0)
        return off

    bits_off = put(struct.pack("<%dH" % spp, *([16] * spp))) if spp > 1 else None
    res_off = put(struct.pack("<II", int(dpi), 1))
    icc_off = put(icc) if icc else None
    counts_off = put(struct.pack("<%dI" % nstrips, *counts)) if nstrips > 1 else None
    offsets_pos = len(extra)
    put(b"\0" * 4 * nstrips) if nstrips > 1 else None
    pixel_off = base + len(extra)
    offsets = [pixel_off + i * strip_bytes for i in range(nstrips)]
    if nstrips > 1:
        extra[offsets_pos:offsets_pos + 4 * nstrips] = struct.pack("<%dI" % nstrips, *offsets)

    tags = [
        (256, 4, 1, cols), (257, 4, 1, rows),
        (258, 3, spp, bits_off if spp > 1 else 16),
        (259, 3, 1, 1), (262, 3, 1, 2 if spp >= 3 else 1),
        (273, 4, nstrips, offsets_pos + base if nstrips > 1 else pixel_off),
        (277, 3, 1, spp), (278, 4, 1, rows_per_strip),
        (279, 4, nstrips, counts_off if nstrips > 1 else counts[0]),
        (282, 5, 1, res_off), (283, 5, 1, res_off), (284, 3, 1, 1), (296, 3, 1, 2),
    ]
    if spp == 4:
        tags.append((338, 3, 1, 0))  # ExtraSamples: the fourth channel is unspecified data (IR)
    if icc:
        tags.append((34675, 7, len(icc), icc_off))
    exif = [(36864, b"0230"), (40961, int(meta.get("colorspace", 65535)))]  # ExifVersion, ColorSpace
    tags.append((274, 3, 1, 1))  # Orientation: as stored
    ascii_tags = [(270, meta.get("description")), (271, meta.get("make")), (272, meta.get("model"))]
    if meta.get("coolscan"):  # how the scan was made (film, exposures, ...), so it can be developed again later
        ascii_tags.append((COOLSCAN_TAG, json.dumps(meta["coolscan"], separators=(",", ":"))))
    taken = meta.get("taken")
    if taken:
        stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
        ascii_tags.append((306, stamp))
        exif += [(36867, stamp), (36868, stamp)]  # DateTimeOriginal, DateTimeDigitized
    if meta.get("iso"):
        exif.append((34855, int(meta["iso"])))  # ISOSpeedRatings: the film's speed
    ifd_off = pixel_off + len(pixels)
    ifd_off += ifd_off % 2
    # strings go after the pixels too, so the pixel offsets above stay put
    tail = bytearray()

    def put_tail(text):
        blob = text.encode("ascii", "replace") + b"\0"
        if len(blob) <= 4:
            blob = blob.ljust(5, b"\0")
        off = ifd_off + len(tail)
        tail.extend(blob)
        if len(tail) % 2:
            tail.append(0)
        return off, len(blob)  # (a short text is padded so it never fits in the entry itself)

    for tag, text in ascii_tags:
        if text:
            off, count = put_tail(text)
            tags.append((tag, 2, count, off))
    if meta.get("thumb_pass"):  # the frame as the thumbnail pass saw it, kept inside the file (auto rotate learns from it)
        blob = pack_thumb_pass(meta["thumb_pass"])
        tags.append((THUMB_TAG, 7, len(blob), ifd_off + len(tail)))
        tail.extend(blob)
        if len(tail) % 2:
            tail.append(0)
    exif_entries = []
    for tag, value in exif:
        if isinstance(value, bytes):
            exif_entries.append((tag, 7, len(value), value))
        elif isinstance(value, int):
            exif_entries.append((tag, 3, 1, min(value, 65535)))
        else:
            off, count = put_tail(value)
            exif_entries.append((tag, 2, count, off))
    ifd_off += len(tail)
    if exif_entries:
        ntags = len(tags) + 1
        tags.append((34665, 4, 1, ifd_off + 2 + 12 * ntags + 4))
    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", ifd_off))
        f.write(extra)
        f.write(pixels)
        if f.tell() % 2:
            f.write(b"\0")
        f.write(tail)
        for entries in (tags, exif_entries):
            if not entries:
                continue
            f.write(struct.pack("<H", len(entries)))
            for tag, typ, count, value in sorted(entries):
                if isinstance(value, bytes):
                    f.write(struct.pack("<HHI4s", tag, typ, count, value))
                elif typ == 3 and count == 1:
                    f.write(struct.pack("<HHIHH", tag, typ, count, value, 0))
                else:
                    f.write(struct.pack("<HHII", tag, typ, count, value))
            f.write(b"\0\0\0\0")
    if taken:
        try:
            ts = taken.timestamp()
            os.utime(path, (ts, ts))
        except (OverflowError, OSError, ValueError):
            pass


def read_tiff_rgb16(path):
    """Read an uncompressed 16-bit RGB TIFF as three planes (what a cleaning helper hands back)."""
    planes = read_tiff16(path)
    if len(planes) < 3:
        raise ValueError("expected an uncompressed 16-bit RGB TIFF")
    return planes[:3]


def read_tiff16(path):
    """Read an uncompressed 16-bit TIFF as its planes: one (grey), three (RGB) or four (RGB and infrared)."""
    with open(path, "rb") as f:
        data = f.read()
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None or struct.unpack(order + "H", data[2:4])[0] != 42:
        raise ValueError("not a TIFF file")
    ifd = struct.unpack(order + "I", data[4:8])[0]
    tags = {}
    for k in range(struct.unpack(order + "H", data[ifd:ifd + 2])[0]):
        at = ifd + 2 + 12 * k
        tag, typ, n = struct.unpack(order + "HHI", data[at:at + 8])
        if typ not in (3, 4):  # only the whole-number tags are needed
            continue
        code, size = ("H", 2) if typ == 3 else ("I", 4)
        raw = data[at + 8:at + 12]
        if size * n > 4:
            off = struct.unpack(order + "I", raw)[0]
            raw = data[off:off + size * n]
        tags[tag] = struct.unpack(order + code * n, raw[:size * n])
    cols, rows = tags[256][0], tags[257][0]
    spp = tags.get(277, (1,))[0]
    if tags.get(259, (1,))[0] != 1 or set(tags.get(258, (1,))) != {16}:
        raise ValueError("expected an uncompressed 16-bit TIFF")
    pixels = b"".join(data[o:o + c] for o, c in zip(tags[273], tags[279]))
    if len(pixels) < rows * cols * spp * 2:
        raise ValueError("the TIFF is cut short")
    a = np.frombuffer(pixels, order + "u2", rows * cols * spp)
    if tags.get(284, (1,))[0] == 2:  # one plane after the other
        a = a.reshape(spp, rows, cols)
        return [a[k].astype(np.uint16) for k in range(spp)]
    a = a.reshape(rows, cols, spp)
    return [np.ascontiguousarray(a[..., k]).astype(np.uint16) for k in range(spp)]


def read_tiff_info(path, thumb=False):
    """What a TIFF says about itself, without reading its pixels: size, channels, resolution, the
    embedded colour profile, the EXIF dates and film speed, and the app's own note (COOLSCAN_TAG).

    Returns a dict; anything the file does not have is left out (the note is {} then)."""
    with open(path, "rb") as f:
        head = f.read(8)
        order = {b"II": "<", b"MM": ">"}.get(head[:2])
        if order is None or len(head) < 8 or struct.unpack(order + "H", head[2:4])[0] != 42:
            raise ValueError("not a TIFF file")
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}

        def ifd(at):
            f.seek(at)
            count = struct.unpack(order + "H", f.read(2))[0]
            raw = f.read(12 * count)
            out = {}
            for k in range(count):
                tag, typ, n = struct.unpack(order + "HHI", raw[12 * k:12 * k + 8])
                if typ not in sizes:
                    continue
                size = sizes[typ] * n
                value = raw[12 * k + 8:12 * k + 12]
                out[tag] = (typ, n, value, size)
            return out

        def value(entry):
            typ, n, value, size = entry
            if size > 4:
                if size > 4 << 20:
                    return None
                f.seek(struct.unpack(order + "I", value)[0])
                value = f.read(size)
            value = value[:size]
            if typ == 2:
                value = value.split(b"\0", 1)[0]
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    return value.decode("latin-1")
            if typ in (1, 7):
                return value
            if typ == 5:
                a, b = struct.unpack(order + "II", value[:8])
                return a / b if b else None
            code = "H" if typ == 3 else "I"
            return struct.unpack(order + code * n, value)

        tags = ifd(struct.unpack(order + "I", head[4:8])[0])
        info = {"cols": value(tags[256])[0], "rows": value(tags[257])[0],
                "channels": value(tags[277])[0] if 277 in tags else 1}
        if 282 in tags:
            dpi = value(tags[282])
            if dpi:
                info["dpi"] = int(round(dpi))
        if 34675 in tags:
            info["icc"] = bytes(value(tags[34675]) or b"") or None
        for tag, key in ((270, "description"), (271, "make"), (272, "model"), (306, "datetime")):
            if tag in tags:
                info[key] = value(tags[tag])
        info["coolscan"] = {}
        if COOLSCAN_TAG in tags:
            try:
                note = json.loads(value(tags[COOLSCAN_TAG]) or "{}")
                info["coolscan"] = note if isinstance(note, dict) else {}
            except ValueError:
                pass
        if THUMB_TAG in tags:
            info["has_thumb_pass"] = True
            if thumb:
                info["thumb_pass"] = unpack_thumb_pass(value(tags[THUMB_TAG]))
        if 34665 in tags:
            exif = ifd(value(tags[34665])[0])
            if 36867 in exif:
                info["datetime"] = value(exif[36867])
            if 34855 in exif:
                info["iso"] = value(exif[34855])[0]
            for tag, key in ((33434, "exposure"), (33437, "fnumber")):   # as a camera writes them
                if tag in exif and exif[tag][0] == 5:
                    info[key] = value(exif[tag])
        stamp = info.pop("datetime", None)
        if stamp:
            try:
                info["taken"] = datetime.datetime.strptime(stamp.strip(), "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass
    return info
