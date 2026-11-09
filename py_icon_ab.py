# -*- coding: utf-8 -*-
"""A/B 对比：纯缩放 vs 手绘。单张小图，双线性放大。

## 为什么要严格用双线性

前几版检查脚本都栽在放大环节上，白白浪费了几轮：

  最近邻放大           -> 每个输入像素行被复制成 N 条同色横带
  面积平均"重复采样"   -> 放大时每个输出行只覆盖 1 个输入行，等价最近邻

两种都会在图上制造满屏横向条纹，看起来像"图标糊了/断了"，
实际是查看工具的假象。这里用真正的双线性（4 邻域加权），
并**预乘 alpha** 插值，避免透明区黑色渗进笔画边缘。
"""
import os
import struct
import sys
import zlib

import py_icon

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets", "icon_source.png")

CELL = 110
PAD = 12
GAP = 10


def upscale_bilinear(rgba, w, h, nw, nh):
    """真双线性放大。整数倍 1:1 时退化为原图。"""
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        sy = (y + 0.5) * h / nh - 0.5
        y0 = int(sy // 1)
        if sy < 0:
            y0 = 0
        y1 = y0 + 1
        fy = sy - y0
        if y0 < 0:
            y0 = 0
        if y0 > h - 1:
            y0 = h - 1
        if y1 < 0:
            y1 = 0
        if y1 > h - 1:
            y1 = h - 1
        fy = max(0.0, min(1.0, fy))
        for x in range(nw):
            sx = (x + 0.5) * w / nw - 0.5
            x0 = int(sx // 1)
            if sx < 0:
                x0 = 0
            x1 = x0 + 1
            fx = sx - x0
            if x0 < 0:
                x0 = 0
            if x0 > w - 1:
                x0 = w - 1
            if x1 < 0:
                x1 = 0
            if x1 > w - 1:
                x1 = w - 1
            fx = max(0.0, min(1.0, fx))

            i00 = (y0 * w + x0) * 4
            i01 = (y0 * w + x1) * 4
            i10 = (y1 * w + x0) * 4
            i11 = (y1 * w + x1) * 4
            wts = ((1 - fx) * (1 - fy), fx * (1 - fy),
                   (1 - fx) * fy, fx * fy)
            idx = (i00, i01, i10, i11)

            A = sum(rgba[i + 3] * wt for i, wt in zip(idx, wts))
            di = (y * nw + x) * 4
            if A <= 0.5:
                continue
            for c in range(3):
                # 预乘后再插值
                v = sum(rgba[i + c] * (rgba[i + 3] / 255.0) * wt
                        for i, wt in zip(idx, wts))
                out[di + c] = int(max(0, min(255, v / (A / 255.0))))
            out[di + 3] = int(max(0, min(255, A)))
    return out, nw, nh


def write_png(path, rgb, w, h):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def make(size, bg_top, bg_bot):
    """两行：上行浅底，下行深底；每行 左=裸缩放 右=缩放+alpha补偿。"""
    n = 2
    cw = n * (CELL + PAD * 2) + GAP * (n + 1)
    rh = CELL + PAD * 2
    ch = rh * 2 + GAP * 3
    img = bytearray(cw * ch * 3)

    with open(SRC, "rb") as f:
        sw, sh, big = py_icon.decode_png(f.read())
    py_icon.flood_transparent_bg(big, sw, sh, tol=14)

    variants = [py_icon.resize(big, sw, sh, size, size),
                py_icon.resize_for_icon(big, sw, sh, size)]

    for row, bg in enumerate((bg_top, bg_bot)):
        oy0 = GAP + row * (rh + GAP)
        for i in range(cw * rh):
            p = ((oy0 + i // cw) * cw + i % cw) * 3
            img[p] = img[p + 1] = img[p + 2] = bg
        for vi, rgba in enumerate(variants):
            up, uw, uh = upscale_bilinear(rgba, size, size, CELL, CELL)
            ox = GAP + vi * (CELL + PAD * 2) + PAD
            oy = oy0 + PAD
            for y in range(uh):
                for x in range(uw):
                    si = (y * uw + x) * 4
                    a = up[si + 3] / 255.0
                    di = ((oy + y) * cw + ox + x) * 3
                    for c in range(3):
                        img[di + c] = int(up[si + c] * a +
                                          img[di + c] * (1 - a))

    p = os.path.join(HERE, f"ab_{size}.png")
    write_png(p, img, cw, ch)
    print(f"-> ab_{size}.png  {cw}x{ch}  上行浅底/下行深底"
          f"  左=裸缩放 右=+alpha")


def main():
    for s in (16, 20, 24, 32, 48):
        make(s, 236, 26)
    return 0


if __name__ == "__main__":
    sys.exit(main())
