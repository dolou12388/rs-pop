# -*- coding: utf-8 -*-
"""浮层视觉结构测试：区分「真实绘制」与「截图产物」。

这个测试存在的原因是一次真实的误判：

  用 PrintWindow 抓浮层截图，看到按钮之间有两道纯黑竖条、四周有黑边，
  一度以为界面画坏了。实际上 Tk 浮层用 -transparentcolor "#FF00FF"
  做透明键，PrintWindow 会把透明区渲染成 (0,0,0) 纯黑 —— 黑边黑缝是
  截图产物，用户看到的是透过来的圆角底板。

本测试用窗口自身的 DC 取色（不经过屏幕合成，也不受别的窗口遮挡，
不需要 DPI 感知，不需要浮层在最前面），直接回答两个问题：

  1. 洋红键色只出现在该透明的位置（按钮区以外的边缘留白）
  2. 按钮条带内有真实内容（背景色 + 文字/图标色）

只要这两条成立，界面就是对的；截图里的黑块一律视为抓图方式的问题。

运行：py -3 py_pill_visual_test.py
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

import tkinter as tk

import py_theme

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)

u.GetWindowDC.argtypes = [wt.HWND]
u.GetWindowDC.restype = wt.HDC
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u.ReleaseDC.restype = ctypes.c_int
u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
u.GetAncestor.restype = wt.HWND
g.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g.GetPixel.restype = wt.DWORD

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def hex2rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def sample_region(hwnd, y, x_step=3):
    """取一行像素，返回 [(x, (r,g,b))]。"""
    r = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(r))
    w = r.right
    dc = u.GetWindowDC(hwnd)
    out = []
    try:
        for x in range(0, w, x_step):
            v = g.GetPixel(dc, x, y)
            out.append((x, (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)))
    finally:
        u.ReleaseDC(hwnd, dc)
    return out


def run_theme(theme):
    print()
    print("=" * 66)
    print(f"主题: {theme}")
    print("=" * 66)
    py_theme.set_mode(theme)
    root = tk.Tk()
    root.withdraw()

    import py_pop
    pill = py_pop.Pill(root, lambda k: None)
    pill.show_at(500, 500, "视觉测试")
    for _ in range(16):
        root.update()
        time.sleep(0.04)

    hwnd = u.GetAncestor(pill.win.winfo_id(), 2)
    r = wt.RECT()
    u.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    key = hex2rgb(py_pop.Pill.TRANSPARENT_KEY)
    bg = hex2rgb(py_theme.BG2)

    print(f"  客户区 {w}x{h}   透明键={py_pop.Pill.TRANSPARENT_KEY}  "
          f"BG2={py_theme.BG2}")

    # --- 1. 顶边与底边应几乎全是透明键 ---
    for name, y in (("顶边", 1), ("底边", h - 2)):
        row = sample_region(hwnd, y)
        n_key = sum(1 for _, c in row if c == key)
        ratio = n_key / len(row)
        check(f"{name}是透明留白（透出圆角底板）", ratio > 0.9,
              f"{n_key}/{len(row)} = {ratio:.0%}")

    # --- 2. 按钮条带应有真实内容 ---
    mid = h // 2
    row = sample_region(hwnd, mid)
    n_key = sum(1 for _, c in row if c == key)
    n_bg = sum(1 for _, c in row if c == bg)
    n_other = len(row) - n_key - n_bg
    print(f"  中线 y={mid}: 透明键 {n_key}  背景色 {n_bg}  其他(文字/图标) {n_other}")
    check("中线不是整行透明（按钮真的画出来了）", n_key < len(row) * 0.9,
          f"透明占比 {n_key/len(row):.0%}")
    check("中线有背景色填充", n_bg > len(row) * 0.3,
          f"背景占比 {n_bg/len(row):.0%}")
    check("中线有文字/图标像素", n_other > 2, f"{n_other} 个采样点")

    # --- 3. 文字颜色方向正确（深色主题用亮字，浅色主题用暗字）---
    def lum(c):
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    others = [c for _, c in row if c != key and c != bg]
    if others:
        # 取离背景最远的那个作为前景代表
        fg_repr = max(others, key=lambda c: abs(lum(c) - lum(bg)))
        want_contrast = theme == "dark"
        got_contrast = lum(fg_repr) > lum(bg)
        check(f"{theme} 主题文字对比方向正确", got_contrast == want_contrast,
              f"前景 {fg_repr} (亮度 {lum(fg_repr):.0f}) vs "
              f"背景 {bg} (亮度 {lum(bg):.0f})")
    else:
        check("找到前景像素", False, "整行只有键色和背景色")

    pill.win.destroy()
    try:
        root.destroy()
    except tk.TclError:
        pass


def main():
    root = tk.Tk()
    root.withdraw()
    v = root.tk.call("tk", "windowingsystem")
    root.destroy()
    print(f"Tk 窗口系统: {v}")

    for theme in ("dark", "light"):
        run_theme(theme)

    print()
    print("=" * 66)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 66)
    print()
    print("说明：本测试只看窗口自己画了什么，不看截图。")
    print("      用 PrintWindow 抓图时，-transparentcolor 的键色区会被")
    print("      渲染成纯黑，出现黑边/黑缝属正常现象，不是界面缺陷。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
