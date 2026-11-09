# -*- coding: utf-8 -*-
"""把各尺寸图标放大拼成一张图，检查小尺寸清晰度。

托盘真实只有 16-24px，肉眼看不出细节，但放大后能立刻发现：
括号的弧线断没断、光标有没有糊成一团、中间高亮行还在不在。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import py_icon

HERE = os.path.dirname(os.path.abspath(__file__))
PREV = os.path.join(HERE, "icons", "preview")
OUT = os.path.join(HERE, "icon_check.png")

# 每个尺寸放大到 128 显示（用最近邻，保留像素真实形状）
ZOOM = 128
SIZES = [16, 20, 24, 32, 48, 128]


def save_png(rgba, w, h, path):
    import struct
    import zlib
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        off = y * w * 4
        raw.extend(rgba[off:off + w * 4])

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


def main():
    tiles = []
    for s in SIZES:
        p = os.path.join(PREV, f"icon_{s}.png")
        if not os.path.exists(p):
            print(f"缺 {p}")
            continue
        w, h, rgba = py_icon.decode_png(open(p, "rb").read())
        tiles.append((s, w, h, rgba))

    if not tiles:
        return 1

    pad = 8
    cell = ZOOM
    W = len(tiles) * (cell + pad) + pad
    H = cell + pad * 2
    canvas = bytearray(W * H * 4)
    # 底色用浅灰，便于看清透明区域与边缘
    for i in range(0, len(canvas), 4):
        canvas[i] = canvas[i + 1] = canvas[i + 2] = 232
        canvas[i + 3] = 255

    for idx, (s, w, h, rgba) in enumerate(tiles):
        # 最近邻放大：每个源像素铺成 zoom x zoom 的方块
        zoom = max(1, cell // w)
        ox = pad + idx * (cell + pad)
        oy = pad
        for y in range(h * zoom):
            sy = y // zoom
            for x in range(w * zoom):
                sx = x // zoom
                si = (sy * w + sx) * 4
                a = rgba[si + 3] / 255.0
                if a <= 0:
                    continue
                dx, dy = ox + x, oy + y
                if not (0 <= dx < W and 0 <= dy < H):
                    continue
                di = (dy * W + dx) * 4
                for k in range(3):
                    canvas[di + k] = int(rgba[si + k] * a
                                         + canvas[di + k] * (1 - a))

    save_png(canvas, W, H, OUT)
    print(f"已生成 {OUT}  {W}x{H}")
    print("从左到右尺寸:", " ".join(f"{s}px" for s, _w, _h, _r in tiles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
