#!/usr/bin/env python3
"""Compare two PDFs page by page (same page size): ink coverage in both directions with a small tolerance.

usage: compare_pages.py app.pdf mine.pdf [dpi] [overlay_prefix]
Needs: numpy, pillow, poppler's `pdftoppm`.
Overlay colours: black = both, red = only in the app's PDF, blue = only in mine.
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as sw
from PIL import Image


def _dilate(m, r=2):
    return sw(np.pad(m, r), (2 * r + 1, 2 * r + 1)).any(axis=(2, 3))


def compare(app_pdf, mine_pdf, dpi="60", overlay_prefix=None):
    """Return [(page, app_ink, mine_ink, app_covered, mine_covered), ...]; coverages are 0..1."""
    d = tempfile.mkdtemp()
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", app_pdf, d + "/a"], check=True)
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", mine_pdf, d + "/m"], check=True)
    rows = []
    for f in sorted(x for x in os.listdir(d) if x.startswith("a-")):
        a_im = Image.open(f"{d}/{f}").convert("L")
        b_im = Image.open(f"{d}/m-{f[2:]}").convert("L")
        h, w = min(a_im.height, b_im.height), min(a_im.width, b_im.width)
        a = np.array(a_im)[:h, :w] < 128
        b = np.array(b_im)[:h, :w] < 128
        ca = (a & _dilate(b)).sum() / max(a.sum(), 1)
        cb = (b & _dilate(a)).sum() / max(b.sum(), 1)
        rows.append((f[2:-4], int(a.sum()), int(b.sum()), float(ca), float(cb)))
        if overlay_prefix:
            out = np.full(a.shape + (3,), 255, np.uint8)
            out[b] = (170, 170, 170)
            out[a & _dilate(b)] = (0, 0, 0)
            out[a & ~_dilate(b)] = (255, 0, 0)
            out[b & ~_dilate(a)] = (0, 120, 255)
            Image.fromarray(out).save(f"{overlay_prefix}{f[2:-4]}.png")
    return rows


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows = compare(argv[0], argv[1], argv[2] if len(argv) > 2 else "60", argv[3] if len(argv) > 3 else None)
    for page, ai, mi, ca, cb in rows:
        print("page %s  app-ink %6d mine-ink %6d  app covered %.1f%%  mine covered %.1f%%" % (page, ai, mi, 100 * ca, 100 * cb))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
