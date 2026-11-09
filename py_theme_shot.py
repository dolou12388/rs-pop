# -*- coding: utf-8 -*-
"""把窗口真实外观截下来存成 PNG，用于人工与视觉模型核对。

为什么要单独干这件事：
  之前用 `winfo_rooty()` 推坐标，结果取到的"标题栏"与"内容区"亮度完全
  相同（26.3 / 26.3），说明取样点根本落在客户区里 —— 测了个寂寞。
  `winfo_rooty` 在 Tk 里返回的是**客户区**顶端，不含标题栏与边框。
  正确做法是问 Windows 要 GetWindowRect，那是含装饰的真窗口矩形。
"""
import ctypes
import os
import sys
import tkinter as tk
from ctypes import wintypes as wt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import py_dpi
import py_theme

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def grab(x, y, w, h):
    """抓屏，返回 [[(r,g,b), ...], ...]（行优先）。"""
    hdc_s = user32.GetDC(None)
    hdc_m = gdi32.CreateCompatibleDC(hdc_s)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_s, w, h)
    gdi32.SelectObject(hdc_m, hbmp)
    gdi32.BitBlt(hdc_m, 0, 0, w, h, hdc_s, x, y, 0x00CC0020)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_m, hbmp, 0, h, buf, ctypes.byref(bi), 0)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_m)
    user32.ReleaseDC(None, hdc_s)

    raw = buf.raw
    rows = []
    for yy in range(h):
        off = yy * w * 4
        rows.append([(raw[off + i * 4 + 2], raw[off + i * 4 + 1],
                      raw[off + i * 4]) for i in range(w)])
    return rows


def save_png(rows, path, scale=1):
    """不依赖 Pillow，手写 PNG。zlib 是标准库。"""
    import struct
    import zlib
    h = len(rows)
    w = len(rows[0]) if h else 0
    if scale > 1:
        big = []
        for row in rows:
            r2 = []
            for px in row:
                r2.extend([px] * scale)
            for _ in range(scale):
                big.append(r2)
        rows = big
        h *= scale
        w *= scale

    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r, g, b in row:
            raw.extend((r, g, b))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return w, h


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def window_rect(hwnd):
    """取含标题栏与边框的真窗口矩形（物理像素）。"""
    r = RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def main():
    py_dpi.enable()
    root = tk.Tk()
    root.withdraw()
    py_dpi.set_tk_scaling(root)

    W, H = 480, 300
    win = tk.Toplevel(root)
    win.title("划词助手 · 主题验证")
    win.configure(bg=py_theme.BG)
    win.geometry(f"{W}x{H}+200+200")
    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)
    win.update()
    win.update_idletasks()

    hwnd = user32.GetAncestor(win.winfo_id(), 2)
    print(f"HWND={hwnd}")
    print(f"winfo: 客户区 origin=({win.winfo_rootx()},{win.winfo_rooty()}) "
          f"size={win.winfo_width()}x{win.winfo_height()}")

    # 先抓一张"改之前"的
    x, y, w, h = window_rect(hwnd)
    print(f"GetWindowRect: ({x},{y}) {w}x{h}")
    before = grab(x - 4, y - 4, w + 8, h + 8)
    save_png(before, "shot_before.png", scale=1)

    print(f"apply_dark_window -> {py_theme.apply_dark_window(win)}")
    win.update()
    import time
    time.sleep(0.35)          # 给 DWM 一点时间重绘
    win.update()

    x, y, w, h = window_rect(hwnd)
    after = grab(x - 4, y - 4, w + 8, h + 8)
    save_png(after, "shot_after.png", scale=1)
    print(f"已保存 shot_before.png / shot_after.png  尺寸 {w + 8}x{h + 8}")

    # 量化：取第 6 行（标题栏中部）与底部内容区的平均亮度
    def row_lum(rows, idx):
        if idx < 0 or idx >= len(rows):
            return None
        r = rows[idx]
        return sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in r) / len(r)

    print("\n逐行平均亮度（前 40 行，用来看标题栏在哪、是不是深色）:")
    for i in range(0, min(40, len(after))):
        bl = row_lum(before, i)
        al = row_lum(after, i)
        print(f"  y={i:3d}  before={bl:6.1f}  after={al:6.1f}")

    # 左边缘逐行，用来看圆角：第 0 列在前几行应该不是窗口色
    print("\n左边缘前 12 行首像素（圆角处应是桌面/圆角外的颜色）:")
    for i in range(0, min(12, len(after))):
        print(f"  y={i:3d}  x=4 -> {after[i][4]}   x=20 -> {after[i][20]}")

    win.destroy()
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
