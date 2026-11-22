"""Writing PNG and TIFF files, and reading a TIFF back."""

import math
import os
import struct
import zlib

import numpy as np


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
        return off, len(text) + 1

    for tag, text in ascii_tags:
        if text:
            off, count = put_tail(text)
            tags.append((tag, 2, count, off))
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
    if tags.get(259, (1,))[0] != 1 or set(tags.get(258, (1,))) != {16} or spp < 3:
        raise ValueError("expected an uncompressed 16-bit RGB TIFF")
    pixels = b"".join(data[o:o + c] for o, c in zip(tags[273], tags[279]))
    if len(pixels) < rows * cols * spp * 2:
        raise ValueError("the TIFF is cut short")
    a = np.frombuffer(pixels, order + "u2", rows * cols * spp)
    if tags.get(284, (1,))[0] == 2:  # one plane after the other
        a = a.reshape(spp, rows, cols)
        return [a[k].astype(np.uint16) for k in range(3)]
    a = a.reshape(rows, cols, spp)
    return [np.ascontiguousarray(a[..., k]).astype(np.uint16) for k in range(3)]
