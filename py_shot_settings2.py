# -*- coding: utf-8 -*-
"""实测当前版本的设置窗口能否真正打开并截图。

## 为什么不复用 py_shot_settings.py

旧脚本用 `EnumWindows` 按标题含"设置"匹配，会抓到编辑器等其它应用
自己的界面（标题里也有"设置"）。这里改为：**在进程内直接拿到 Tk
窗口的 HWND**，完全不靠标题匹配。

## 判据

  1. Tk 窗口创建成功（不抛异常，说明 style_tk 等初始化全部通过）
  2. HWND 有效、窗口可见
  3. 截图像素不是纯色（确实渲染出了内容，不是白屏/黑屏）
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import py_dpi
import py_settings

HERE = os.path.dirname(os.path.abspath(__file__))
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


def grab(hwnd, path):
    """用 PrintWindow 让窗口自己绘制到内存 DC。

    不能用 BitBlt 截屏幕：那只是复制屏幕该区域的像素，任何叠在上面的
    窗口都会被一起拍进来（上一版截到了别的程序，就是这个问题）。
    PrintWindow 是给窗口发渲染请求，z 序无关。
    """
    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    hdc_s = user32.GetDC(None)
    hdc_m = gdi32.CreateCompatibleDC(hdc_s)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_s, w, h)
    gdi32.SelectObject(hdc_m, hbmp)
    # PW_RENDERFULLCONTENT = 0x02，否则部分窗口只画出标题栏
    user32.PrintWindow(wt.HWND(hwnd), hdc_m, 0x02)
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

    # 统计颜色多样性：纯色说明没渲染出内容
    uniq = set()
    for i in range(0, w * h * 4, 4 * 37):
        uniq.add(buf.raw[i:i + 3])
    _write_png(path, buf.raw, w, h)
    return w, h, len(uniq)


def _write_png(path, bgra, w, h):
    import struct
    import zlib
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            i = (y * w + x) * 4
            raw.append(bgra[i + 2])
            raw.append(bgra[i + 1])
            raw.append(bgra[i])

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


result = {}


def run():
    try:
        import py_config
        win = py_settings.SettingsWindow(py_config.store(),
                                         on_saved=lambda *a: None)
        result["win"] = win
        win.show()          # 阻塞到窗口关闭
    except Exception as e:
        import traceback
        result["err"] = traceback.format_exc()


def main():
    py_dpi.enable()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(3.5)

    print("=" * 68)
    print("设置窗口实测")
    print("=" * 68)

    if "err" in result:
        print("\n[FAIL] show() 抛异常：")
        print(result["err"])
        return 1

    win = result.get("win")
    if win is None or not getattr(win, "root", None):
        print("\n[FAIL] 窗口未创建")
        return 1

    root = win.root
    hwnd = user32.GetAncestor(root.winfo_id(), 2)
    print(f"\n  窗口标题: {root.title()}")
    print(f"  HWND    : {hwnd}")
    print(f"  可见    : {bool(root.winfo_viewable())}")
    print(f"  尺寸    : {root.winfo_width()}x{root.winfo_height()}")

    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    print(f"  真矩形  : {rect.right - rect.left}x{rect.bottom - rect.top}"
          f" @ ({rect.left},{rect.top})")

    out = os.path.join(HERE, "shot_settings.png")
    r = grab(hwnd, out)
    if r:
        w, h, uniq = r
        print(f"\n  截图    : {w}x{h}  颜色多样性 {uniq} 种")
        print(f"  -> {out}")
        if uniq < 12:
            print("\n[FAIL] 截图颜色过少，疑似白屏/未渲染")
            return 1
        print("\n[PASS] 设置窗口正常渲染")
    else:
        print("\n[FAIL] 截图失败")
        return 1

    # 关闭窗口，让 show() 返回
    try:
        user32.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)   # WM_CLOSE
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    code = main()
    print("=" * 68)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
