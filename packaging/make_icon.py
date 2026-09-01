"""
Draws the application icon and writes AppIcon.icns.

Written by hand so the build needs no image libraries: a small PNG writer plus a
rounded-rectangle rasteriser, sampled four times over in each direction so the
edges come out smooth. The mark is a pair of wiki-link brackets, which is what
the tool spends its time putting into chapters.
"""

import os
import struct
import subprocess
import sys
import tempfile
import zlib

BACKGROUND = (31, 95, 79)      # the deep green used throughout the interface
FOREGROUND = (251, 249, 245)   # the same warm paper colour

SAMPLES = 4


def rounded_rect_coverage(px, py, x0, y0, x1, y1, radius):
    """How much of the pixel sample at (px, py) lies inside a rounded rectangle."""
    if radius <= 0:
        return 1.0 if x0 <= px <= x1 and y0 <= py <= y1 else 0.0
    cx = min(max(px, x0 + radius), x1 - radius)
    cy = min(max(py, y0 + radius), y1 - radius)
    if px < x0 or px > x1 or py < y0 or py > y1:
        return 0.0
    dx, dy = px - cx, py - cy
    return 1.0 if dx * dx + dy * dy <= radius * radius else 0.0


class Canvas:
    def __init__(self, size):
        self.size = size
        self.pixels = [[(0, 0, 0, 0)] * size for _ in range(size)]

    def fill_rounded(self, x0, y0, x1, y1, radius, colour):
        step = 1.0 / SAMPLES
        offset = step / 2.0
        lo_y = max(0, int(y0) - 2)
        hi_y = min(self.size, int(y1) + 3)
        lo_x = max(0, int(x0) - 2)
        hi_x = min(self.size, int(x1) + 3)
        for y in range(lo_y, hi_y):
            row = self.pixels[y]
            for x in range(lo_x, hi_x):
                hits = 0
                for sy in range(SAMPLES):
                    py = y + offset + sy * step
                    for sx in range(SAMPLES):
                        px = x + offset + sx * step
                        if rounded_rect_coverage(px, py, x0, y0, x1, y1, radius):
                            hits += 1
                if not hits:
                    continue
                alpha = hits / float(SAMPLES * SAMPLES)
                row[x] = self._blend(row[x], colour, alpha)

    @staticmethod
    def _blend(under, colour, alpha):
        ur, ug, ub, ua = under
        r, g, b = colour
        out_a = alpha + ua * (1 - alpha)
        if out_a <= 0:
            return (0, 0, 0, 0)
        nr = (r * alpha + ur * ua * (1 - alpha)) / out_a
        ng = (g * alpha + ug * ua * (1 - alpha)) / out_a
        nb = (b * alpha + ub * ua * (1 - alpha)) / out_a
        return (int(round(nr)), int(round(ng)), int(round(nb)), out_a)

    def to_png(self, path):
        raw = bytearray()
        for row in self.pixels:
            raw.append(0)
            for r, g, b, a in row:
                raw += bytes((r, g, b, int(round(a * 255))))

        def chunk(tag, data):
            out = struct.pack(">I", len(data)) + tag + data
            return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.size, self.size, 8, 6, 0, 0, 0)
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", header)
               + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + chunk(b"IEND", b""))
        with open(path, "wb") as fh:
            fh.write(png)


def draw(size):
    """One icon, drawn in proportion so it works at every size."""
    c = Canvas(size)
    u = size / 1024.0

    # The rounded square Apple expects, inset a little as their own icons are.
    inset = 92 * u
    c.fill_rounded(inset, inset, size - inset, size - inset, 200 * u, BACKGROUND)

    # Two pairs of brackets: [[ ]]
    bar = 46 * u          # thickness of a stroke
    stub = 104 * u        # how far the top and bottom arms reach
    top = 330 * u
    bottom = size - 330 * u
    gap = 34 * u

    left_start = 268 * u
    for i in range(2):
        x = left_start + i * (bar + gap + 26 * u)
        c.fill_rounded(x, top, x + bar, bottom, bar / 2, FOREGROUND)
        c.fill_rounded(x, top, x + stub, top + bar, bar / 2, FOREGROUND)
        c.fill_rounded(x, bottom - bar, x + stub, bottom, bar / 2, FOREGROUND)

    right_start = size - left_start
    for i in range(2):
        x = right_start - i * (bar + gap + 26 * u)
        c.fill_rounded(x - bar, top, x, bottom, bar / 2, FOREGROUND)
        c.fill_rounded(x - stub, top, x, top + bar, bar / 2, FOREGROUND)
        c.fill_rounded(x - stub, bottom - bar, x, bottom, bar / 2, FOREGROUND)

    return c


def main(out_path):
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "AppIcon.iconset")
        os.makedirs(iconset)
        rendered = {}
        for s in sizes:
            rendered[s] = os.path.join(tmp, f"{s}.png")
            draw(s).to_png(rendered[s])

        pairs = [(16, "16x16", 1), (32, "16x16", 2), (32, "32x32", 1),
                 (64, "32x32", 2), (128, "128x128", 1), (256, "128x128", 2),
                 (256, "256x256", 1), (512, "256x256", 2),
                 (512, "512x512", 1), (1024, "512x512", 2)]
        for px, name, scale in pairs:
            suffix = "" if scale == 1 else "@2x"
            dest = os.path.join(iconset, f"icon_{name}{suffix}.png")
            with open(rendered[px], "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())

        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out_path],
                       check=True)
    print(f"icon written: {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.icns")
