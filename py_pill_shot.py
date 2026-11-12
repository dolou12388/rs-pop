# -*- coding: utf-8 -*-
"""截一张「悬停态」浮层图，用于人工核对圆角观感。

必须真的触发悬停，而不是另画一个仿制品 —— 否则验证不到贴图与
圆角半径的实际效果。

用 PrintWindow 抓窗口自身绘制：本机 IDE 覆盖全屏，屏幕合成会抓到
IDE；PrintWindow 虽然会把 -transparentcolor 的键色区渲染成黑色，
但按钮内部（圆角块所在的区域）画得是真的，足以判断圆角。

运行：py -3 py_pill_shot.py [light|dark]
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time
import tkinter as tk
import zlib

import py_theme

u = ctypes.WinDLL("user32")
g = ctypes.WinDLL("gdi32")


def capture(hwnd, w, h, out):
    u.GetWindowDC.restype = wt.HDC
    u.GetWindowDC.argtypes = [wt.HWND]
    g.CreateCompatibleDC.restype = wt.HDC
    g.CreateCompatibleDC.argtypes = [wt.HDC]
    g.CreateCompatibleBitmap.restype = wt.HBITMAP
    g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype = wt.HGDIOBJ
    g.SelectObject.argtypes = [wt.HDC, wt.HBITMAP]
    u.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    g.DeleteObject.argtypes = [wt.HGDIOBJ]
    g.DeleteDC.argtypes = [wt.HDC]
    u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

    hdc = u.GetWindowDC(hwnd)
    mem = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mem, bmp)
    u.PrintWindow(hwnd, mem, 1 | 2)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        row = buf.raw[y * w * 4:(y + 1) * w * 4]
        for x in range(w):
            b, gg, rr = row[x * 4], row[x * 4 + 1], row[x * 4 + 2]
            raw += bytes((rr, gg, b))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    open(out, "wb").write(png)

    g.DeleteObject(bmp)
    g.DeleteDC(mem)
    u.ReleaseDC(hwnd, hdc)


def main():
    theme = sys.argv[1] if len(sys.argv) > 1 else "light"
    # 必须在建 Pill 之前切主题 —— Pill.__init__ 读的是当前常量
    py_theme.set_mode(theme)

    root = tk.Tk()
    root.withdraw()

    import py_pop
    pill = py_pop.Pill(root, lambda k: None)
    pill.show_at(400, 400, "悬停效果")
    for _ in range(12):
        root.update()
        time.sleep(0.05)

    # 触发第二个按钮悬停，与第一个形成对比（一个高亮一个不高亮）
    pill._hover(pill._buttons[1], True)
    for _ in range(8):
        root.update()
        time.sleep(0.05)

    hwnd = u.GetAncestor(pill.win.winfo_id(), 2)
    out = f"hover_{theme}.png"
    capture(hwnd, pill.pill_w, pill.pill_h, out)
    print(f"{theme}: {out}  {pill.pill_w}x{pill.pill_h}  半径={py_pop.Pill.HOVER_RADIUS}px")

    pill.win.destroy()
    try:
        root.destroy()
    except tk.TclError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
