# -*- coding: utf-8 -*-
"""圆角底色块：给浮层按钮画抗锯齿的悬停高亮。

## 为什么不用 Canvas 的多边形

Tk 的 Canvas **不做抗锯齿**。`create_polygon(smooth=True)` 画出来的
6px 圆角在 150% DPI 下能明显看到台阶，这与项目在 DPI 清晰度上的投入
完全不匹配。

## 采用的方案

自己生成带 alpha 通道的 PNG，交给 `tk.PhotoImage` 贴到 Canvas 上。
实测 Tk 8.6 在 Canvas 上会做真正的 alpha 合成（`_aa_probe.py` 的结论）：
在 BG2 背景上贴 BG4 的圆角块，角落过渡区出现 7 种中间色，而不是只有
纯 BG2 / 纯 BG4 两种 —— 说明边缘是按 alpha 混合出来的，视觉上平滑。

## 缓存策略

每个 (宽, 高, 半径, 颜色) 组合只生成一次，结果同时缓存在内存
（PhotoImage 必须保持引用，否则被 GC 掉图就没了）和磁盘临时文件里。
浮层四个按钮尺寸只有两种，实际只会生成 2~4 张图。
"""

import math
import os
import struct
import tempfile
import zlib

# 每像素在横竖方向各取几个采样点。4x4=16 次判定，150% DPI 下
# 圆角已经看不出台阶；8x8 收益很小但生成耗时翻 4 倍。
_SAMPLES = 4

# (宽, 高, 半径, 颜色) -> PhotoImage
_cache = {}
# 同一 key 的磁盘路径，避免重复写文件
_paths = {}


def _corner_coverage(x, y, w, h, radius, samples):
    """返回像素 (x,y) 被圆角矩形覆盖的比例 0~1。"""
    hit = 0
    total = samples * samples
    for iy in range(samples):
        py = y + (iy + 0.5) / samples
        # 纵向到最近圆角圆心的距离
        if py < radius:
            qy = radius - py
        elif py > h - radius:
            qy = py - (h - radius)
        else:
            qy = 0.0
        for ix in range(samples):
            px = x + (ix + 0.5) / samples
            if qy == 0.0:
                # 纵向在直边段：只要横向也在直边段就完全覆盖
                hit += 1
                continue
            if px < radius:
                qx = radius - px
            elif px > w - radius:
                qx = px - (w - radius)
            else:
                # 纵向在圆角段但横向在直边段：由纵向决定
                # 此处 qy>0 且横向居中，属于上下直边带，必然覆盖
                hit += 1
                continue
            if math.hypot(qx, qy) <= radius:
                hit += 1
    return hit / total


def _rgba_rows(w, h, radius, color, samples=_SAMPLES):
    """生成 RGBA 字节行（非预乘 alpha）。"""
    radius = max(0, min(int(radius), w // 2, h // 2))
    cr, cg, cb = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            if radius == 0:
                a = 255
            else:
                a = round(255 * _corner_coverage(x, y, w, h, radius, samples))
            row += bytes((cr, cg, cb, a))
        rows.append(bytes(row))
    return rows


def _write_png(w, h, rows, path):
    raw = bytearray()
    for row in rows:
        raw.append(0)          # 每行 filter type 0
        raw += row

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    # color type 6 = RGBA
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def rounded_image(w, h, radius, color):
    """返回一张圆角底色块的 PhotoImage（带缓存）。

    PhotoImage 必须由调用方保持引用；本模块内部也留一份，
    避免被 GC 掉导致 Canvas 上的图变空白。
    需要先有 Tk 根窗口。
    """
    key = (int(w), int(h), int(radius), color.lower())
    cached = _cache.get(key)
    if cached is not None:
        return cached

    import tkinter as tk

    path = _paths.get(key)
    if path is None or not os.path.exists(path):
        rows = _rgba_rows(key[0], key[1], key[2], color)
        fd, path = tempfile.mkstemp(prefix="popdict_rr_", suffix=".png")
        os.close(fd)
        _write_png(key[0], key[1], rows, path)
        _paths[key] = path

    img = tk.PhotoImage(file=path)
    _cache[key] = img
    return img


def clear_cache():
    """主题切换或测试用：丢掉内存里的图，下次重新生成。

    磁盘临时文件保留，内容与 key 一一对应，不会读到旧颜色。
    """
    _cache.clear()


def cleanup_files():
    """退出时删掉磁盘上的临时 PNG。"""
    for path in list(_paths.values()):
        try:
            os.unlink(path)
        except OSError:
            pass
    _paths.clear()
    _cache.clear()
