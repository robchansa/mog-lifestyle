"""A tiny pure-Python PNG reader/writer.

Only what the asset pipeline needs: 8-bit non-interlaced PNGs, arbitrary
cropping, and turning a dark-on-light logo into an alpha mask.  `sips` handles
resizing and lossy encoding; this handles the pixel work in between.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


@dataclass
class Image:
    width: int
    height: int
    channels: int
    pixels: bytearray          # row-major, `channels` bytes per pixel

    def at(self, x: int, y: int) -> tuple[int, ...]:
        start = (y * self.width + x) * self.channels
        return tuple(self.pixels[start:start + self.channels])

    def crop(self, left: int, top: int, right: int, bottom: int) -> "Image":
        left, top = max(0, left), max(0, top)
        right, bottom = min(self.width, right), min(self.height, bottom)
        width, height = right - left, bottom - top
        out = bytearray(width * height * self.channels)
        stride = self.width * self.channels
        row_bytes = width * self.channels
        for y in range(height):
            src = (top + y) * stride + left * self.channels
            out[y * row_bytes:(y + 1) * row_bytes] = self.pixels[src:src + row_bytes]
        return Image(width, height, self.channels, out)


def decode(data: bytes) -> Image:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, compressed = 8, bytearray()
    width = height = channels = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if depth != 8 or interlace or colour not in CHANNELS:
                raise ValueError(f"unsupported PNG (depth={depth}, colour={colour})")
            channels = CHANNELS[colour]
        elif kind == b"IDAT":
            compressed += body
        elif kind == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    out = bytearray(width * height * channels)
    previous = bytearray(stride)
    offset = 0
    for y in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        _unfilter(filter_type, line, previous, channels)
        out[y * stride:(y + 1) * stride] = line
        previous = line
    return Image(width, height, channels, out)


def _unfilter(kind: int, line: bytearray, prior: bytearray, bpp: int) -> None:
    if kind == 0:
        return
    for i in range(len(line)):
        left = line[i - bpp] if i >= bpp else 0
        up = prior[i]
        if kind == 1:
            line[i] = (line[i] + left) & 0xFF
        elif kind == 2:
            line[i] = (line[i] + up) & 0xFF
        elif kind == 3:
            line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
        elif kind == 4:
            upper_left = prior[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + _paeth(left, up, upper_left)) & 0xFF
        else:
            raise ValueError(f"unknown filter {kind}")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def encode(image: Image) -> bytes:
    colour = {1: 0, 2: 4, 3: 2, 4: 6}[image.channels]
    stride = image.width * image.channels
    raw = bytearray()
    for y in range(image.height):
        raw.append(0)                      # filter: None
        raw += image.pixels[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", image.width, image.height, 8, colour, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


# --------------------------------------------------------------- alpha mask

def to_alpha_mask(image: Image, *, background: tuple[int, int, int] | None = None,
                  ink: tuple[int, int, int] = (26, 26, 24)) -> Image:
    """Turn dark-on-light artwork into black pixels with a computed alpha.

    The result is used as a CSS `mask-image`, so only the alpha channel
    matters -- which lets the logo take its colour from the theme instead of
    being baked light or dark.
    """
    if background is None:
        background = image.at(1, 1)[:3]
    bg = _luminance(*background)
    fg = _luminance(*ink)
    span = max(1.0, bg - fg)

    out = bytearray(image.width * image.height * 4)
    for index in range(image.width * image.height):
        src = index * image.channels
        r, g, b = image.pixels[src], image.pixels[src + 1], image.pixels[src + 2]
        alpha = (bg - _luminance(r, g, b)) / span
        out[index * 4 + 3] = max(0, min(255, round(alpha * 255)))
    return Image(image.width, image.height, 4, out)


def alpha_bounds(image: Image, threshold: int = 24) -> tuple[int, int, int, int]:
    """Tight (left, top, right, bottom) around everything above `threshold`."""
    left, top = image.width, image.height
    right = bottom = 0
    for y in range(image.height):
        row = y * image.width * 4
        for x in range(image.width):
            if image.pixels[row + x * 4 + 3] >= threshold:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    if right < left:
        return 0, 0, image.width, image.height
    return left, top, right + 1, bottom + 1


def alpha_row_gaps(image: Image, threshold: int = 24) -> list[tuple[int, int]]:
    """Vertical bands of content, separated by empty rows."""
    rows = []
    for y in range(image.height):
        row = y * image.width * 4
        rows.append(any(
            image.pixels[row + x * 4 + 3] >= threshold for x in range(image.width)
        ))
    bands, start = [], None
    for y, filled in enumerate(rows):
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, image.height))
    return bands


def _luminance(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
