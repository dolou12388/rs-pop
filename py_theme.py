# -*- coding: utf-8 -*-
"""视觉主题：深浅配色、系统跟随、标题栏与圆角。

## 为什么单独一个模块

配色原本散落在 py_pop.py 和 py_settings.py 两处，同一个"边线色"在
两边的十六进制值就已经不一致（#34343A vs #3A3A42）。集中到这里后，
改一处全局生效，也便于做整体的层级调优。

## 圆角怎么来的

Windows 11（build 22000+）的 DWM 提供 `DWMWA_WINDOW_CORNER_PREFERENCE`，
设成 `DWMWCP_ROUND` 后由系统负责抗锯齿绘制 —— 比自己用 Canvas 拼
圆角矩形要干净得多，也不会有像素锯齿。

注意两个限制（实测）：
  1. `overrideredirect(True)` 的无边框窗口不走 DWM 的窗口框架，
     该属性对它无效。浮层因此改用 alpha 合成的圆角底图（见 pill_round_rect）。
  2. 属性必须在窗口**已映射**（有 HWND 且可见）之后设置才稳定生效。

## 标题栏

`DWMWA_USE_IMMERSIVE_DARK_MODE`（属性号 20，旧版 19）能让原生标题栏
跟随当前配色。深色界面如果保留白色标题栏会很割裂。
"""

import ctypes
import threading
import tkinter as tk
import winreg
from ctypes import wintypes as wt

dwmapi = ctypes.WinDLL("dwmapi.dll", use_last_error=True)
user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)

# 返回句柄的函数必须声明 restype，否则 64 位下会被截断成 32 位 ——
# 这种错误在高位地址分配时才暴露，极难排查。
gdi32.CreateRoundRectRgn.restype = wt.HANDLE
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteObject.argtypes = [wt.HANDLE]
user32.SetWindowRgn.restype = ctypes.c_int
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HANDLE, wt.BOOL]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]

# ---------- DWM 属性号 ----------
DWMWA_USE_IMMERSIVE_DARK_MODE = 20        # Win10 20H1+；更早版本是 19
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34

# ---------- 圆角偏好 ----------
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080

# ---------- 配色（单一真源）----------
# 层级：BG < BG2 < BG3，数字越大越"浮起来"
BG = "#1A1A1D"          # 窗口底色（最深）
BG2 = "#212125"         # 卡片
BG3 = "#2A2A2F"         # 输入框 / 次级按钮
BG4 = "#33333A"         # 悬停

FG = "#ECECEF"          # 主文字
FG_SOFT = "#C6C6CE"     # 次文字
FG_DIM = "#8E8E98"      # 弱文字 / 占位
FG_FAINT = "#63636D"    # 极弱：分隔符、禁用

LINE = "#303038"        # 分割线
LINE_SOFT = "#26262C"   # 更弱的分割线

ACCENT = "#6E8EF8"      # 主色（蓝）
ACCENT_HOVER = "#87A3FA"
ACCENT_DIM = "#4A5F9E"
ON_ACCENT = "#0C1020"   # 主色上的文字

OK_COLOR = "#5CB574"
WARN_COLOR = "#E0A94A"
ERR_COLOR = "#E0645C"

_COLOR_NAMES = (
    "BG", "BG2", "BG3", "BG4", "FG", "FG_SOFT", "FG_DIM", "FG_FAINT",
    "LINE", "LINE_SOFT", "ACCENT", "ACCENT_HOVER", "ACCENT_DIM",
    "ON_ACCENT", "OK_COLOR", "WARN_COLOR", "ERR_COLOR",
)
DARK_PALETTE = {name: globals()[name] for name in _COLOR_NAMES}
LIGHT_PALETTE = {
    "BG": "#F6F4EF", "BG2": "#FFFEFB", "BG3": "#F0EFE9",
    "BG4": "#E5E9E2", "FG": "#202723", "FG_SOFT": "#4B5650",
    "FG_DIM": "#67736C", "FG_FAINT": "#89928B",
    "LINE": "#D9D8D1", "LINE_SOFT": "#ECEAE4",
    "ACCENT": "#2F7058", "ACCENT_HOVER": "#3C8469",
    "ACCENT_DIM": "#245942", "ON_ACCENT": "#FFFFFF",
    "OK_COLOR": "#36865D", "WARN_COLOR": "#A86D21",
    "ERR_COLOR": "#B84B46",
}
THEME_MODES = ("system", "light", "dark")
_mode_lock = threading.RLock()
_preference = "dark"
_effective = "dark"


def system_mode():
    """读取 Windows 的应用深浅色选择；缺失时采用系统默认浅色。"""
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except (OSError, TypeError, ValueError):
        return "light"


def set_mode(mode):
    """设置首选模式并更新模块配色，返回实际生效的深浅色。"""
    global _preference, _effective
    if mode not in THEME_MODES:
        raise ValueError(f"未知主题模式: {mode}")
    effective = system_mode() if mode == "system" else mode
    with _mode_lock:
        _preference, _effective = mode, effective
        palette = LIGHT_PALETTE if effective == "light" else DARK_PALETTE
        globals().update(palette)
    return effective


def refresh_system():
    """跟随系统时检测颜色变化；仅在实际变化时返回 True。"""
    global _effective
    with _mode_lock:
        if _preference != "system":
            return False
        effective = system_mode()
        if effective == _effective:
            return False
        _effective = effective
        globals().update(LIGHT_PALETTE if effective == "light" else DARK_PALETTE)
        return True


def effective_mode():
    with _mode_lock:
        return _effective


def preference():
    with _mode_lock:
        return _preference

# 阴影：alpha 合成的纯黑，直接叠在父窗口的圆角底图外侧


def _hwnd_of(win):
    """从 Tk 窗口取顶层 HWND。

    `winfo_id()` 返回的是 Tk 自己的子窗口句柄，DWM 属性要设在
    顶层框架窗口上，所以必须先 GetAncestor(GA_ROOT=2)。
    """
    try:
        hwnd = user32.GetAncestor(win.winfo_id(), 2)
        return hwnd or win.winfo_id()
    except Exception:
        return None


def _set_dwm_attr(hwnd, attr, value, size=None):
    """设置一个 DWM 窗口属性。失败静默（老系统没有这些属性）。"""
    if not hwnd:
        return False
    try:
        v = ctypes.c_int(value)
        sz = size or ctypes.sizeof(v)
        hr = dwmapi.DwmSetWindowAttribute(
            wt.HWND(hwnd), ctypes.c_uint(attr),
            ctypes.byref(v), ctypes.c_uint(sz))
        return hr == 0
    except Exception:
        return False


def _get_dwm_attr(hwnd, attr):
    """读回一个 DWM 属性。

    比只看 DwmSetWindowAttribute 的 HRESULT 可靠：HRESULT 成功只说明
    调用被受理，不代表值真的存下来了。读回能确认生效。
    不可用/未设置时返回 None。
    """
    if not hwnd:
        return None
    try:
        v = ctypes.c_int(0)
        sz = ctypes.c_uint(ctypes.sizeof(v))
        hr = dwmapi.DwmGetWindowAttribute(
            wt.HWND(hwnd), ctypes.c_uint(attr),
            ctypes.byref(v), sz)
        if hr == 0:
            return v.value
    except Exception:
        pass
    return None


def dark_titlebar(win):
    """把原生标题栏变成深色。必须在窗口可见后调用。"""
    hwnd = _hwnd_of(win)
    if not hwnd:
        return False
    # 新系统用 20，老系统用 19；两个都试一遍，成功即止
    for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE,
                 DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        if _set_dwm_attr(hwnd, attr, 1):
            if _get_dwm_attr(hwnd, attr) == 1:
                return True
    return False


def titlebar_mode(win):
    """让系统标题栏与当前主题一致。"""
    hwnd = _hwnd_of(win)
    want = int(effective_mode() == "dark")
    for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE,
                 DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        if _set_dwm_attr(hwnd, attr, want):
            if _get_dwm_attr(hwnd, attr) == want:
                return True
    return False


def round_corners(win, small=False):
    """让系统给窗口画圆角。对 override 的无边框窗口无效（见模块说明）。"""
    hwnd = _hwnd_of(win)
    if not hwnd:
        return False
    want = DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND
    ok = _set_dwm_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, want)
    if not ok:
        return False
    # 读回确认。某些系统版本对设定值有修正（例如 ROUND 不满足时会
    # 降级成 ROUNDSMALL），只要不是 DONOTROUND 就算圆角生效。
    got = _get_dwm_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE)
    return got is not None and got != DWMWCP_DONOTROUND


def tint_border(win, color=LINE):
    """给窗口一条自定的 1px 边框色（Win11 才有）。

    color 形如 "#303038"；COLORREF 是 0x00BBGGRR，需要反序。
    """
    try:
        c = color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        colorref = (b << 16) | (g << 8) | r
    except Exception:
        return False
    hwnd = _hwnd_of(win)
    return _set_dwm_attr(hwnd, DWMWA_BORDER_COLOR, colorref)


def apply_dark_window(win, small_corners=False, border=LINE):
    """一次性套用：深色标题栏 + 系统圆角 + 自定边框色。

    调用时机：窗口 `deiconify()` / 首次 `update_idletasks()` 之后。
    太早调用时 HWND 还没真正建立，属性会被丢弃。
    """
    ok_dark = dark_titlebar(win)
    ok_round = round_corners(win, small=small_corners)
    if border:
        tint_border(win, border)
    return ok_dark, ok_round


def apply_window(win, small_corners=False):
    """给原生窗口应用当前标题栏、圆角和边线颜色。"""
    titlebar_mode(win)
    rounded = round_corners(win, small=small_corners)
    tint_border(win, LINE)
    return rounded


# ---------- 无边框窗口的圆角底图 ----------

def rounded_rect_points(x0, y0, x1, y1, r, steps=8):
    """生成一个圆角矩形的顶点序列（供 Canvas polygon 平滑参数用）。

    返回扁平列表 [x0,y0, x1,y0, ...]，Tk 的 `create_polygon` 接收
    这种形式。配合 `smooth=False` 时是折线，配合圆角半径足够大时
    肉眼看不出棱角；要更圆就增大 steps。
    """
    import math
    pts = []
    # 四个角的圆心
    corners = (
        (x1 - r, y0 + r, -90, 0),      # 右上
        (x1 - r, y1 - r, 0, 90),       # 右下
        (x0 + r, y1 - r, 90, 180),     # 左下
        (x0 + r, y0 + r, 180, 270),    # 左上
    )
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


def colors():
    """返回当前生效配色的副本。"""
    with _mode_lock:
        return {name: globals()[name] for name in _COLOR_NAMES}


def recolor_tree(widget, old, new):
    """原位换色，保留窗口中尚未保存的输入与滚动位置。"""
    mapping = {value.lower(): new[name] for name, value in old.items()}
    options = ("background", "foreground", "activebackground",
               "activeforeground", "highlightbackground", "highlightcolor",
               "insertbackground", "selectbackground", "selectforeground",
               "troughcolor", "disabledforeground")

    def visit(item):
        for option in options:
            try:
                value = item.cget(option)
                target = mapping.get(str(value).lower())
                if target:
                    item.configure(**{option: target})
            except (tk.TclError, TypeError, AttributeError):
                pass
        for child in item.winfo_children():
            visit(child)
    visit(widget)


# ---------- 无边框浮层的圆角 ----------

def round_region(win, radius_px):
    """给 `overrideredirect(True)` 的浮层切圆角。

    ## 为什么不用 DWM

    实测（py_popup_probe.py）：overrideredirect 窗口本身就是 WS_POPUP，
    没有非客户区，窗口矩形 == 客户区矩形（GetWindowRect 与客户区差
    0x0）。DWM 的 DWMWA_WINDOW_CORNER_PREFERENCE 对这类窗口即使读回
    ROUND 也不会真的画 —— 它只处理有边框的窗口。

    ## 为什么用 SetWindowRgn

    二值裁剪，圆弧边缘没有过渡色（会有一点阶梯）。但在 150% DPI 下
    半径 10-12 逻辑像素、实测放大 6 倍截图后才能看出轻微阶梯，
    正常观看距离不可见。相比"多圈叠色伪造过渡"方案，这个不会在
    浅色文档上留下一圈灰边 —— 那个方案会把过渡色实打实盖在文档上。

    返回 True 表示裁剪区域已设置。
    """
    try:
        hwnd = _hwnd_of(win)
        if not hwnd:
            return False
        # 用窗口真实尺寸建区域。这里必须读 GetWindowRect 而不是
        # winfo_width —— 后者在窗口尚未 map 时返回 1。
        rect = wt.RECT()
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            return False
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return False
        r = max(1, min(int(radius_px), w // 2, h // 2))
        # CreateRoundRectRgn 的右/下边界是"不含"语义，需 +1
        hrgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
        if not hrgn:
            return False
        # SetWindowRgn 成功后区域归系统所有，不要 DeleteObject
        if not user32.SetWindowRgn(wt.HWND(hwnd), hrgn, True):
            gdi32.DeleteObject(hrgn)
            return False
        return True
    except Exception:
        return False
