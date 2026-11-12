# -*- coding: utf-8 -*-
"""验证悬停高亮是抗锯齿圆角，不是直角也不再是台阶。

判定分两层：

  1. **形状**：角落像素必须是背景色（圆角外），中心必须是高亮色。
     如果角落也是高亮色，说明还是直角矩形。

  2. **抗锯齿**：角落过渡带必须出现介于背景与高亮之间的中间色。
     只有两种纯色说明是二值边缘（有台阶）。

像素来源：窗口自身 DC 取色（GetWindowDC + 每像素 GetPixel）。
不用屏幕合成：本机 IDE 窗口覆盖全屏，浮层会被遮挡；也不用 PrintWindow：
它会把 -transparentcolor 的键色区渲染成纯黑，干扰判断。

运行：py -3 py_pill_hover_test.py
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

import tkinter as tk

import py_dpi
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


def rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def snap(hwnd, w, h):
    '''把窗口客户区整块读成二维颜色表。'''
    dc = u.GetWindowDC(hwnd)
    rows = []
    try:
        for y in range(h):
            row = []
            for x in range(w):
                v = g.GetPixel(dc, x, y)
                row.append((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF))
            rows.append(row)
    finally:
        u.ReleaseDC(hwnd, dc)
    return rows


def near(c, target, tol=6):
    return all(abs(c[i] - target[i]) <= tol for i in range(3))


def run(theme):
    print()
    print("=" * 66)
    print(f"主题: {theme}")
    print("=" * 66)
    py_theme.set_mode(theme)
    root = tk.Tk()
    root.withdraw()

    import py_pop
    pill = py_pop.Pill(root, lambda k: None)
    pill.show_at(500, 500, "悬停测试")
    for _ in range(14):
        root.update()
        time.sleep(0.04)

    hwnd = u.GetAncestor(pill.win.winfo_id(), 2)
    canvas = pill._buttons[0]
    radius = py_dpi.px(pill.HOVER_RADIUS)
    bg = rgb(py_theme.BG2)
    hi = rgb(py_theme.BG4)

    bx = canvas.winfo_x()
    by = canvas.winfo_y()
    bw = canvas.winfo_reqwidth()
    bh = canvas.winfo_reqheight()
    print(f"  按钮0 区域 ({bx},{by}) {bw}x{bh}   圆角半径 {radius}px")
    print(f"  背景 BG2={py_theme.BG2}  高亮 BG4={py_theme.BG4}")

    # --- 悬停前：按钮区域应全是背景色 ---
    before = snap(hwnd, pill.pill_w, pill.pill_h)
    corner_before = before[by + 1][bx + 1]
    check("悬停前角落是背景色", near(corner_before, bg),
          f"{corner_before} vs BG2 {bg}")

    # --- 触发悬停 ---
    pill._hover(canvas, True)
    for _ in range(8):
        root.update()
        time.sleep(0.04)
    after = snap(hwnd, pill.pill_w, pill.pill_h)

    # --- 1. 形状：角落仍是背景，内部（避开字形）是高亮 ---
    c_corner = after[by + 1][bx + 1]
    # 采样点必须避开文字与图标：中心像素会落在字形上，导致读到字色。
    # 取按钮底部往下 3px 的中间位置 —— 那里在圆角块内、且没有字。
    probe_y = by + bh - py_dpi.px(4)
    probe_x = bx + bw // 2
    c_mid = after[probe_y][probe_x]
    check("悬停后左上角落仍为背景色（说明是圆角，不是直角）",
          near(c_corner, bg), f"{c_corner} ≈ BG2 {bg}")
    check("悬停后按钮内部已变成高亮色", near(c_mid, hi),
          f"({probe_x},{probe_y}) = {c_mid} ≈ BG4 {hi}")

    # 其他三个角也要是背景色
    c_tr = after[by + 1][bx + bw - 2]
    c_bl = after[by + bh - 2][bx + 1]
    c_br = after[by + bh - 2][bx + bw - 2]
    check("右上角为背景色", near(c_tr, bg), f"{c_tr}")
    check("左下角为背景色", near(c_bl, bg), f"{c_bl}")
    check("右下角为背景色", near(c_br, bg), f"{c_br}")

    # --- 2. 抗锯齿：角落过渡带要有中间色 ---
    # 扫描整个左上圆角方块（半径 x 半径），不只是对角几像素。
    # 中间色只要与两端都不完全相等即可，容差给小值，避免把
    # 量化误差当成"没有抗锯齿"。
    seen = {}
    rr = radius + 1
    for y in range(by, by + rr):
        for x in range(bx, bx + rr):
            c = after[y][x]
            seen[c] = seen.get(c, 0) + 1
    mids = [c for c in seen if not near(c, bg, 1) and not near(c, hi, 1)]
    print(f"  左上过渡带颜色种类: {len(seen)}，其中中间色 {len(mids)} 种")
    if mids:
        print("    中间色示例: " + ", ".join(str(c) for c in mids[:6]))
    # 阈值取 2 而不是 3：深色主题下 BG2(33,33,37) 与 BG4(51,51,58) 的差距
    # 只有 18 级，8 位量化后能落出的中间色本就少（实测正好 2 种）。
    # 只要存在既不是背景也不是高亮的色，就证明边缘是混合出来的，
    # 不是二值硬切 —— 这才是有意义的判据，不该按主题定不同标准。
    check("边缘有抗锯齿中间色（不是台阶）", len(mids) >= 2,
          f"{len(mids)} 种中间色")

    # --- 3. 离开后恢复 ---
    pill._hover(canvas, False)
    for _ in range(6):
        root.update()
        time.sleep(0.03)
    restored = snap(hwnd, pill.pill_w, pill.pill_h)
    c_after = restored[probe_y][probe_x]
    check("离开后按钮内部恢复背景色", near(c_after, bg),
          f"{c_after} vs BG2 {bg}")

    pill.win.destroy()
    try:
        root.destroy()
    except tk.TclError:
        pass


def main():
    for theme in ("light", "dark"):
        run(theme)

    print()
    print("=" * 66)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
