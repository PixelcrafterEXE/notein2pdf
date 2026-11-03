#!/usr/bin/env python3
"""Register my render onto the app's render (via the bright 'cream page' image) and report/visualise differences.
usage: compare_unbounded.py app.png mine.png out.png
Needs: numpy, pillow. Render both PDFs with `pdftoppm -r 100 -png` first."""
import sys
import numpy as np
from PIL import Image

def bright_bbox(a):
    m = (a[..., 0] > 235) & (a[..., 1] > 228) & (a[..., 2] > 215)
    cols, rows = m.sum(axis=0), m.sum(axis=1)
    xs = np.where(cols > 0.5 * cols.max())[0]; ys = np.where(rows > 0.5 * rows.max())[0]
    return xs.min(), ys.min(), xs.max(), ys.max()

app = np.array(Image.open(sys.argv[1]).convert("RGB")); mine_im = Image.open(sys.argv[2]).convert("RGB")
mine = np.array(mine_im)
ba, bm = bright_bbox(app), bright_bbox(mine)
sx = (ba[2] - ba[0]) / (bm[2] - bm[0]); sy = (ba[3] - ba[1]) / (bm[3] - bm[1])
print("landmark scale x %.4f y %.4f" % (sx, sy))
# affine: app = sx*mine + tx
tx = ba[0] - sx * bm[0]; ty = ba[1] - sy * bm[1]
h, w = app.shape[:2]

def content(a):
    return a.max(axis=2) > 130

def warp(sx_, sy_, tx_, ty_, size=None, img=mine_im):
    W_, H_ = size or (w, h)
    f = (w / W_)
    return np.array(img.transform((W_, H_), Image.AFFINE, (1 / sx_ * f, 0, -tx_ / sx_ * f, 0, 1 / sy_ * f, -ty_ / sy_ * f),
                                  resample=Image.BILINEAR, fillcolor=(0, 0, 0)))

# refine: measure local offsets (FFT cross-correlation) on content tiles, fit dx(x), dy(y) linearly
def tile_offsets(A_, B_, T=256, maxs=24):
    pts = []
    H_, W_ = A_.shape
    for y0 in range(0, H_ - T, T // 2):
        for x0 in range(0, W_ - T, T // 2):
            a_ = A_[y0:y0+T, x0:x0+T].astype(float); b_ = B_[y0:y0+T, x0:x0+T].astype(float)
            if a_.sum() < 400 or b_.sum() < 400:
                continue
            F = np.fft.fft2(a_ - a_.mean()) * np.conj(np.fft.fft2(b_ - b_.mean()))
            c = np.fft.fftshift(np.real(np.fft.ifft2(F)))
            cy, cx = np.unravel_index(np.argmax(c), c.shape)
            dy, dx = cy - T // 2, cx - T // 2
            if abs(dx) <= maxs and abs(dy) <= maxs:
                pts.append((x0 + T / 2, y0 + T / 2, dx, dy))
    return np.array(pts)

reg0 = warp(sx, sy, tx, ty)
P = tile_offsets(content(app), content(reg0))
if len(P) >= 6:
    # app_x = reg_x + dx ->  dx = ax*x + bx
    ax, bx = np.polyfit(P[:, 0], P[:, 2], 1); ay, by = np.polyfit(P[:, 1], P[:, 3], 1)
    print("final affine sx %.4f sy %.4f" % (sx*(1+ax), sy*(1+ay))); print("tile offsets: n=%d  dx=%.4f*x%+.1f  dy=%.4f*y%+.1f  (median |dx|,|dy| = %.1f, %.1f px)" %
          (len(P), ax, bx, ay, by, np.median(abs(P[:, 2])), np.median(abs(P[:, 3]))))
    # new total map: app = (1+ax)*(sx*mine+tx) + bx
    sx, tx = (1 + ax) * sx, (1 + ax) * tx + bx
    sy, ty = (1 + ay) * sy, (1 + ay) * ty + by
reg = mine_im.transform((w, h), Image.AFFINE, (1 / sx, 0, -tx / sx, 0, 1 / sy, -ty / sy), resample=Image.BILINEAR, fillcolor=(0, 0, 0))
reg = np.array(reg)
# metrics on "content" = pixels clearly different from the dark paper+grid
def content(a):
    return a.max(axis=2) > 130
A, B = content(app), content(reg)
from numpy.lib.stride_tricks import sliding_window_view as sw
def dil(M, r=3):
    P = np.pad(M, r); return sw(P, (2*r+1, 2*r+1)).any(axis=(2, 3))
cov_app = (A & dil(B)).sum() / max(A.sum(), 1); cov_mine = (B & dil(A)).sum() / max(B.sum(), 1)
print("app content covered by mine %.1f%%, mine covered by app %.1f%%  (A=%d B=%d)" % (100*cov_app, 100*cov_mine, A.sum(), B.sum()))
out = np.zeros((h, w, 3), np.uint8)
out[A & dil(B)] = (200, 200, 200); out[A & ~dil(B)] = (255, 60, 60); out[B & ~dil(A)] = (60, 140, 255)
Image.fromarray(out).save(sys.argv[3]); Image.fromarray(reg).save(sys.argv[3].replace(".png", "_reg.png"))
