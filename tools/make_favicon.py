"""
Cut the square Ajaia mark out of the wordmark, for the browser tab icon.

    python -m tools.make_favicon

Writes frontend/assets/ajaia-mark.png -- the white A-mark on brand navy that
every page names in its `<link rel="icon">`.

WHY IT IS DERIVED RATHER THAN A FILE SOMEBODY DROPPED IN. The mark is already
in the repository: it is the third glyph of assets/ajaia-logo.png, between the
J and the I. Cutting it from there means the tab icon and the header wordmark
are the same artwork by construction, and cannot drift apart when one of them
is updated and the other is forgotten. REPLACING THE WORDMARK MEANS RE-RUNNING
THIS -- it is the only thing that keeps the two in step, and nothing calls it
automatically.

The navy is read off the wordmark's own palette rather than typed here, for the
same reason.

Deliberately stdlib-only. This runs about once a year, and a Pillow dependency
in requirements.txt for it would be carried by every deploy, every container
build and every CI run in between. A palette PNG is a zlib stream and five
scanline filters; that is the whole of what is below.

The glyph is 130x117 and is placed at 1:1 -- NOT SCALED. Every pixel here is a
pixel of the original artwork, with its own antialiasing, and the browser does
the only resampling there is when it draws the thing at 16 or 32 px. Scaling it
here would mean resampling twice.
"""

import struct
import zlib
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
SOURCE = FRONTEND / "assets" / "ajaia-logo.png"
TARGET = FRONTEND / "assets" / "ajaia-mark.png"

# The mark's tight bounding box in the wordmark, in source pixels. Found by
# scanning for columns with no ink in them: the five glyphs of AJAIA come out
# as five runs, and this is the third. Hard-coded rather than re-derived on
# every run so that a wordmark whose spacing changed produces an obviously
# wrong crop to look at, instead of a subtly wrong one nobody checks.
GLYPH = (204, 333, 1, 117)          # x0, x1, y0, y1

# Canvas edge. 62% of it is mark, which is the proportion the brand square
# uses; the rest is the navy field. Bigger than the 16-32 px a tab actually
# draws, so the browser is always downsampling -- an icon upscaled from a
# smaller source is the one thing that would look wrong here.
SIZE = 208


def read_indexed(path):
    """(width, height, rows, palette, alpha) from an 8-bit palette PNG."""
    data = path.read_bytes()
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    if (depth, colour) != (8, 3):
        raise SystemExit(f"{path.name}: expected an 8-bit palette PNG, "
                         f"got depth {depth} colour type {colour}.")

    at, idat, plte, trns = 8, b"", b"", b""
    while at < len(data):
        length = struct.unpack(">I", data[at:at + 4])[0]
        kind = data[at + 4:at + 8]
        chunk = data[at + 8:at + 8 + length]
        if kind == b"IDAT":
            idat += chunk
        elif kind == b"PLTE":
            plte = chunk
        elif kind == b"tRNS":
            trns = chunk
        at += 12 + length

    raw = zlib.decompress(idat)
    rows, previous, at = [], bytearray(width), 0
    for _ in range(height):
        method = raw[at]
        at += 1
        line = bytearray(raw[at:at + width])
        at += width
        if method == 1:                                   # Sub
            for x in range(1, width):
                line[x] = (line[x] + line[x - 1]) & 255
        elif method == 2:                                 # Up
            for x in range(width):
                line[x] = (line[x] + previous[x]) & 255
        elif method == 3:                                 # Average
            for x in range(width):
                left = line[x - 1] if x else 0
                line[x] = (line[x] + ((left + previous[x]) >> 1)) & 255
        elif method == 4:                                 # Paeth
            for x in range(width):
                left = line[x - 1] if x else 0
                upleft = previous[x - 1] if x else 0
                up = previous[x]
                guess = left + up - upleft
                dl, du, dul = (abs(guess - left), abs(guess - up),
                               abs(guess - upleft))
                near = (left if dl <= du and dl <= dul
                        else up if du <= dul else upleft)
                line[x] = (line[x] + near) & 255
        rows.append(line)
        previous = line

    entries = len(plte) // 3
    palette = [tuple(plte[i * 3:i * 3 + 3]) for i in range(entries)]
    # tRNS may be shorter than the palette; everything past it is opaque.
    alpha = list(trns) + [255] * (entries - len(trns))
    return width, height, rows, palette, alpha


def write_rgb(path, size, pixels):
    """An 8-bit RGB PNG. No alpha: the mark sits on a solid navy field."""
    stride = size * 3
    raw = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride]
                   for y in range(size))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b""))


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"{SOURCE} is missing -- nothing to cut the mark from.")

    _, _, rows, palette, alpha = read_indexed(SOURCE)
    x0, x1, y0, y1 = GLYPH
    navy = palette[0]                       # the wordmark's own ink, #001D6B
    glyph_w, glyph_h = x1 - x0 + 1, y1 - y0 + 1
    left, top = (SIZE - glyph_w) // 2, (SIZE - glyph_h) // 2

    out = bytearray()
    for y in range(SIZE):
        for x in range(SIZE):
            sx, sy = x - left + x0, y - top + y0
            inside = x0 <= sx <= x1 and y0 <= sy <= y1
            # White mark over navy, mixed by the glyph's own antialiasing, so
            # the diagonals stay smooth instead of stepping.
            a = alpha[rows[sy][sx]] if inside else 0
            out += bytes(round(c + (255 - c) * a / 255) for c in navy)

    write_rgb(TARGET, SIZE, bytes(out))
    print(f"{TARGET.relative_to(FRONTEND.parent)}: {SIZE}x{SIZE}, "
          f"a {glyph_w}x{glyph_h} mark on #{'%02X%02X%02X' % navy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
