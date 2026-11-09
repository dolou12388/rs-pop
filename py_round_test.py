# -*- coding: utf-8 -*-
"""验证浮层圆角真的切上了。

不采信 `round_region()` 的返回值 —— 只看它最终是否让窗口的区域
变成圆角。判据三条：

  1. GetWindowRgn 返回非 NULLREGION（说明确实设了自定义区域）
  2. 区域覆盖的像素面积 < 整矩形面积（圆角必然少掉四角的像素）
  3. 四角顶点落在区域外、中心落在区域内（形状是圆角而非其他裁剪）
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import py_dpi
import py_theme

ok = fail = 0


def chk(cond, name, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"   {extra}" if extra else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f"   {extra}" if extra else ""))


user32 = py_theme.user32
gdi32 = py_theme.gdi32

gdi32.CreateRectRgn.restype = wt.HANDLE
gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
gdi32.GetRgnBox.restype = ctypes.c_int
gdi32.GetRgnBox.argtypes = [wt.HANDLE, ctypes.POINTER(wt.RECT)]
user32.GetWindowRgn.restype = ctypes.c_int
user32.GetWindowRgn.argtypes = [wt.HWND, wt.HANDLE]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
gdi32.PtInRegion.restype = wt.BOOL
gdi32.PtInRegion.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_int]

NULLREGION, SIMPLEREGION, COMPLEXREGION = 1, 2, 3

print("=" * 68)
print("浮层圆角验证（SetWindowRgn）")
print("=" * 68)

py_dpi.enable()
root = tk.Tk()
root.withdraw()

win = tk.Toplevel(root)
win.overrideredirect(True)
win.attributes("-topmost", True)
win.configure(bg=py_theme.BG2)
win.geometry("220x60+300+300")
win.deiconify()
win.update_idletasks()
win.update()

hwnd = user32.GetAncestor(win.winfo_id(), 2)

rect = wt.RECT()
user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
w = rect.right - rect.left
h = rect.bottom - rect.top
print(f"\n窗口真实矩形 {w}x{h} @ ({rect.left},{rect.top})")

print("\n[1] 切圆角")
radius = py_dpi.px(10)
r = py_theme.round_region(win, radius)
chk(bool(r), "round_region 返回成功", f"半径={radius}px")

print("\n[2] 区域形状读回")
hrgn = gdi32.CreateRectRgn(0, 0, 0, 0)
rtype = user32.GetWindowRgn(wt.HWND(hwnd), hrgn)
chk(rtype in (SIMPLEREGION, COMPLEXREGION),
    "窗口已设置自定义区域", f"type={rtype} (2=简单 3=复杂)")
chk(rtype != NULLREGION, "不是 NULLREGION（区域被接受）")

box = wt.RECT()
gdi32.GetRgnBox(hrgn, ctypes.byref(box))
bw = box.right - box.left
bh = box.bottom - box.top
chk(bw > 0 and bh > 0, "区域外框非空", f"{bw}x{bh}")

print("\n[3] 四角被切掉、中心保留")
corners = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)]
left_out = sum(1 for (cx, cy) in corners
               if not gdi32.PtInRegion(hrgn, cx, cy))
chk(left_out == 4, "四个角顶点都在区域外（确实切圆角）",
    f"{left_out}/4 在外")
chk(bool(gdi32.PtInRegion(hrgn, w // 2, h // 2)),
    "窗口中心在区域内（没切成空心）")

# 直线中点在区域内，说明不是把整块缩小的普通矩形
chk(bool(gdi32.PtInRegion(hrgn, w // 2, 2)),
    "上边中点仍在区域内（只切四角）")
chk(bool(gdi32.PtInRegion(hrgn, 2, h // 2)),
    "左边中点仍在区域内（只切四角）")

print("\n[4] 半径合理性")
# 沿某一行从左往右找第一个落点，反推圆角水平内缩量
row = 2
first_in = None
for x in range(0, min(80, w)):
    if gdi32.PtInRegion(hrgn, x, row):
        first_in = x
        break
chk(first_in is not None, "能扫到区域的左边界", f"x={first_in}")
if first_in is not None:
    chk(0 < first_in <= radius + 3,
        "圆角内缩量与半径匹配",
        f"内缩 {first_in}px / 半径 {radius}px")

gdi32.DeleteObject(hrgn)
win.destroy()
root.destroy()

print("\n" + "=" * 68)
print(f"结果: {ok} PASS / {fail} FAIL")
print("=" * 68)
sys.exit(0 if fail == 0 else 1)
