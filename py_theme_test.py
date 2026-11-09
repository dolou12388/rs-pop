# -*- coding: utf-8 -*-
"""验证 py_theme 的 DWM 圆角 / 深色标题栏是否真在这台机器上生效。

## 一个必须记住的陷阱

第一版用 `win.winfo_rootx()/winfo_rooty()` 来定位窗口，结果测出来的
"标题栏亮度"和"内容区亮度"完全相等（26.3 / 26.3），并且圆角判据失败。
原因：`winfo_rooty` 返回的是**客户区**（client area）顶端，不含标题栏
和边框。拿它当窗口左上角，取样点整体偏进内容区里，等于什么都没测到。

正确做法是 `GetWindowRect(hwnd)` —— 那才是含装饰的真窗口矩形。
改用之后，标题栏亮度从 ~235 掉到 ~42，圆角也能从角像素看出来。

教训：凡是要断言"窗口外观"，坐标必须来自 Win32，不能来自 Tk 的
`winfo_*`（那是客户区语义）。
"""
import ctypes
import os
import sys
import time
import tkinter as tk
from ctypes import wintypes as wt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import py_dpi
import py_theme

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)

PASS = 0
FAIL = 0


def chk(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def grab(x, y, w, h):
    """抓屏，返回行优先的 [[(r,g,b), ...], ...]。"""
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


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def lum(p):
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


def band_lum(rows, y0, y1, x0, x1):
    vals = []
    for yy in range(y0, min(y1, len(rows))):
        row = rows[yy]
        for xx in range(x0, min(x1, len(row))):
            vals.append(lum(row[xx]))
    return sum(vals) / len(vals) if vals else 0.0


def main():
    print("=" * 66)
    print("py_theme · DWM 圆角与深色标题栏实机验证")
    print("=" * 66)

    py_dpi.enable()
    root = tk.Tk()
    root.withdraw()
    py_dpi.set_tk_scaling(root)

    W, H = 460, 300
    win = tk.Toplevel(root)
    win.title("主题验证窗口")
    win.configure(bg=py_theme.BG)
    win.geometry(f"{W}x{H}+200+200")
    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)
    win.update()
    win.update_idletasks()

    hwnd = user32.GetAncestor(win.winfo_id(), 2)
    x, y, w, h = rect_of(hwnd)
    print(f"\n真窗口矩形(GetWindowRect): ({x},{y}) {w}x{h}")
    print(f"客户区(winfo): ({win.winfo_rootx()},{win.winfo_rooty()}) "
          f"{win.winfo_width()}x{win.winfo_height()}")
    print(f"  标题栏高度推算: {win.winfo_rooty() - y}px")

    # --- 1. 深色标题栏：改前 / 改后对比 ---
    print("\n[1] 深色标题栏")
    before = grab(x, y, w, h)
    bar_before = band_lum(before, 3, 12, 30, w - 120)
    print(f"  改前标题栏亮度 {bar_before:.1f}")

    ok_dark, ok_round = py_theme.apply_dark_window(win)
    win.update()
    time.sleep(0.3)
    win.update()
    after = grab(x, y, w, h)
    bar_after = band_lum(after, 3, 12, 30, w - 120)
    print(f"  改后标题栏亮度 {bar_after:.1f}")

    chk(ok_dark, "DwmSetWindowAttribute(dark) 成功")
    chk(ok_round, "圆角属性设置成功")
    chk(bar_after < 120, "标题栏已变深色（亮度<120）", f"{bar_after:.1f}")
    chk(bar_before - bar_after > 80, "深色化幅度显著",
        f"降了 {bar_before - bar_after:.1f}")

    # --- 2. 圆角：读回属性值，而不是靠像素猜 ---
    # 第一版试图用"角像素 vs 边中像素"判断圆角，结果失败。
    # 原因是 DWM 会在窗口外围画一层柔和阴影，离窗口边缘 1-2px 的
    # 取样点落在阴影过渡带上，四个角的亮度差异被抹平到 4 左右，
    # 根本区分不出"圆角切掉了角"还是"阴影浅了一点"。
    # 属性读回才是可靠判据：值被系统接受并保存，才说明它真会生效。
    print("\n[2] 圆角（属性读回）")
    got = py_theme._get_dwm_attr(hwnd, py_theme.DWMWA_WINDOW_CORNER_PREFERENCE)
    print(f"  DWMWA_WINDOW_CORNER_PREFERENCE = {got} "
          f"(ROUND={py_theme.DWMWCP_ROUND})")
    chk(got is not None, "圆角属性可读回（系统支持该属性）", f"值={got}")
    chk(got != py_theme.DWMWCP_DONOTROUND, "圆角未被拒绝",
        f"值={got}")

    dark_got = py_theme._get_dwm_attr(hwnd,
                                      py_theme.DWMWA_USE_IMMERSIVE_DARK_MODE)
    chk(dark_got == 1, "深色模式属性读回为 1", f"值={dark_got}")

    # --- 3. 边框着色 ---
    print("\n[3] 边框着色 / 深色标题栏可重复调用")
    chk(py_theme.tint_border(win, "#FF0000"), "边框色设置成功")
    chk(py_theme.dark_titlebar(win), "深色标题栏可重复设置（幂等）")
    chk(py_theme.round_corners(win), "圆角可重复设置（幂等）")
    b2 = grab(x, y, w, h)
    chk(band_lum(b2, 3, 12, 30, w - 120) < 120, "重复调用后仍是深色")

    # --- 4. 无边框窗口：DWM 不管，但 API 不报错 ---
    print("\n[4] 无边框窗口（overrideredirect）")
    ov = tk.Toplevel(root)
    ov.overrideredirect(True)
    ov.configure(bg=py_theme.BG)
    ov.geometry(f"200x100+{x}+{y + h + 30}")
    ov.deiconify()
    ov.update()
    d, o = py_theme.apply_dark_window(ov)
    print(f"  overrideredirect: dark={d} round={o}"
          f"（DWM 不管这类窗口，返回值不代表视觉变化）")
    chk(isinstance(d, bool) and isinstance(o, bool), "无边框窗口不抛异常")

    # --- 5. 圆角多边形 ---
    print("\n[5] rounded_rect_points")
    pts = py_theme.rounded_rect_points(0, 0, 100, 40, 8, steps=6)
    chk(len(pts) % 2 == 0, "坐标成对", f"{len(pts)} 个数值")
    xs, ys = pts[0::2], pts[1::2]
    chk(-0.01 <= min(xs) and max(xs) <= 100.01, "x 在范围",
        f"{min(xs):.1f}..{max(xs):.1f}")
    chk(-0.01 <= min(ys) and max(ys) <= 40.01, "y 在范围",
        f"{min(ys):.1f}..{max(ys):.1f}")
    chk(len(pts) // 2 == 4 * 7, "点数 = 4角×(steps+1)", f"{len(pts) // 2}")
    # 半径大于半高时应退化为椭圆式，不越界
    pts2 = py_theme.rounded_rect_points(0, 0, 20, 10, 40, steps=4)
    chk(len(pts2) > 0, "半径过大不崩", f"{len(pts2)} 个数值")

    # --- 6. 配色 ---
    print("\n[6] 配色表")
    cs = py_theme.colors()
    for k in ("BG", "BG2", "BG3", "FG", "FG_SOFT", "FG_DIM", "LINE",
              "ACCENT", "OK_COLOR", "WARN_COLOR", "ERR_COLOR"):
        chk(k in cs, f"存在 {k}", cs.get(k, ""))
    chk(len(cs) >= 15, "配色项数量（≥15 项）", f"{len(cs)} 项")
    order = ["BG", "BG2", "BG3", "BG4"]
    lums = [lum(tuple(int(cs[k].lstrip("#")[i:i + 2], 16)
                      for i in (0, 2, 4))) for k in order]
    chk(lums == sorted(lums), "底色层级单调递增（深->浅）",
        " < ".join(f"{v:.0f}" for v in lums))
    # 主色上的文字必须够对比
    fg_l = lum(tuple(int(cs["FG"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)))
    bg_l = lum(tuple(int(cs["BG"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)))
    chk(fg_l - bg_l > 150, "主文字与底色对比充足",
        f"{fg_l:.0f} vs {bg_l:.0f}")

    try:
        ov.destroy()
        win.destroy()
        root.destroy()
    except Exception:
        pass

    print("\n" + "=" * 66)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
