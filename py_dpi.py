# -*- coding: utf-8 -*-
"""DPI 感知与缩放。

## 为什么必须有这个模块

实测本机真实分辨率 2560×1600 / 144 DPI（150% 缩放），但程序未声明 DPI 感知时
Windows 只会报告 1707×1067 / 96 DPI，并把整个窗口**位图拉伸 1.5 倍** ——
所有文字都变糊，而且是插值放大，不是重新渲染。这是"文字不清晰"的根因，
不是字体选得不好。

## 三个层次的修正

1. **进程级**：启动最早期调用 `SetProcessDpiAwarenessContext(V2)`，
   让 Windows 不再替我们拉伸，改由我们自己按真实像素布局。
   V2 相比 V1 多了非客户区（标题栏、边框）的正确缩放。
2. **坐标级**：声明感知后 `GetCursorPos`、`GetWindowRect` 返回的都是真实物理像素，
   之前按 96 DPI 算的坐标会全部失配，因此浮层定位必须用同一坐标系。
3. **视觉级**：字号、间距、控件尺寸都要按 `scale = dpi / 96` 放大，
   否则 150% 缩放下界面会变得极小。

## 关键约束

`SetProcessDpiAwarenessContext` 必须在**创建任何窗口之前**调用，
一旦创建过窗口再调用就会失败（Windows 限制）。所以本模块要在 `tkinter` 建窗前导入。
"""

import ctypes
import sys
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore.dll", use_last_error=True)
except OSError:
    shcore = None

# SetProcessDpiAwarenessContext 的预定义句柄（负数按指针解释）
DPI_AWARENESS_CONTEXT_UNAWARE = -1
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED = -5

BASE_DPI = 96

# 模块级状态：只初始化一次，全程序共享
_state = {
    "enabled": False,
    "dpi": BASE_DPI,
    "scale": 1.0,
    "level": "unknown",
    "note": "",
}


def enable(verbose=False):
    """声明 Per-Monitor V2 DPI 感知。必须在创建任何窗口前调用。

    返回 (ok, note)。失败不抛异常 —— 拿不到高 DPI 只是字糊，
    不该让整个程序起不来。
    """
    if _state["enabled"]:
        return True, _state["note"]

    note = ""
    ok = False

    # 首选 V2（非客户区也按 DPI 缩放）
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        if user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            ok = True
            note = "Per-Monitor V2"
    except (AttributeError, OSError):
        pass

    # 退回 V1
    if not ok and shcore is not None:
        try:
            shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            shcore.SetProcessDpiAwareness.restype = ctypes.c_long
            # 2 = PROCESS_PER_MONITOR_DPI_AWARE
            if shcore.SetProcessDpiAwareness(2) == 0:
                ok = True
                note = "Per-Monitor V1"
        except (AttributeError, OSError):
            pass

    # 最后退回系统级
    if not ok:
        try:
            user32.SetProcessDPIAware.restype = wt.BOOL
            if user32.SetProcessDPIAware():
                ok = True
                note = "System DPI Aware"
        except (AttributeError, OSError):
            note = "声明失败，将使用系统位图缩放（文字会变糊）"

    _state["enabled"] = ok
    _state["note"] = note
    _refresh_dpi()

    if verbose:
        print(f"[DPI] {note}  dpi={_state['dpi']}  "
              f"scale={_state['scale']:.3f}  level={_state['level']}",
              file=sys.stderr)
    return ok, note


def _refresh_dpi():
    """读取系统 DPI 并更新缩放系数。"""
    dpi = BASE_DPI
    try:
        user32.GetDpiForSystem.restype = wt.UINT
        user32.GetDpiForSystem.argtypes = []
        v = user32.GetDpiForSystem()
        if v:
            dpi = int(v)
    except (AttributeError, OSError):
        # 老系统（Win7/8）退回 GetDeviceCaps(LOGPIXELSX)
        try:
            gdi32 = ctypes.WinDLL("gdi32.dll")
            hdc = user32.GetDC(None)
            gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
            gdi32.GetDeviceCaps.restype = ctypes.c_int
            v = gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            user32.ReleaseDC(None, hdc)
            if v:
                dpi = int(v)
        except Exception:
            pass

    _state["dpi"] = dpi
    _state["scale"] = dpi / float(BASE_DPI)

    # 记录感知级别，便于排查
    try:
        val = ctypes.c_int()
        shcore.GetProcessDpiAwareness.argtypes = [
            wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
        shcore.GetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(val)) == 0:
            _state["level"] = {0: "UNAWARE", 1: "SYSTEM_AWARE",
                               2: "PER_MONITOR_AWARE"}.get(val.value, "?")
    except Exception:
        pass


def dpi():
    return _state["dpi"]


def scale():
    """缩放系数：1.0 = 96 DPI，1.5 = 144 DPI。"""
    return _state["scale"]


def info():
    return dict(_state)


# ---------- 尺寸换算 ----------

def px(v):
    """把 96 DPI 下的设计像素换算成当前 DPI 的物理像素。"""
    return int(round(v * _state["scale"]))


def font_size(pt):
    """字号（磅）在 DPI 感知下需要按比例放大吗？

    不需要 —— Tk 的磅值在声明 DPI 感知后会由 Tk 自己按真实 DPI 换算
    （实测 tk scaling 从 1.33 变为 1.998）。若我们再加一遍会放大过头。
    这个函数保留是为了语义清晰，且便于将来需要时统一改动。
    """
    return pt


def set_tk_scaling(root):
    """让 Tk 的磅→像素换算与真实 DPI 一致。

    未声明感知时 Tk 认为 96 DPI（scaling≈1.33），声明后应跟随真实 DPI。
    实测声明感知后 Tk 建窗时就会自己算对（本机直接是 1.998），
    这里显式设置一次是幂等保险，用于多显示器或 Tk 未及时刷新的情况。
    注意 Tk 内部对 scaling 有定点精度，设 2.0 会读回 1.9980（差 0.1%），
    属正常误差，不要在此基础上再做补偿。
    """
    try:
        root.tk.call("tk", "scaling", _state["dpi"] / 72.0)
    except Exception:
        pass


def refresh():
    """显示器 DPI 变化后重新读取。返回是否发生变化。"""
    old = _state["scale"]
    _refresh_dpi()
    return abs(_state["scale"] - old) > 1e-6


# ---------- 字体族选择 ----------

# 中文界面优先用雅黑（有真粗体、字形清晰），退到黑体，最后用 Tk 默认
UI_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
               "Segoe UI", "SimHei", "TkDefaultFont"]
# 等宽：Consolas 在小字号下最清晰；退到 Cascadia Mono / Courier New
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Courier New"]


def pick_family(root, candidates):
    """从候选里挑第一个系统真实存在的字体族。"""
    try:
        import tkinter.font as tkfont
        available = set(tkfont.families(root))
    except Exception:
        return candidates[-1] if candidates else "TkDefaultFont"
    for name in candidates:
        if name in available:
            return name
    return candidates[-1] if candidates else "TkDefaultFont"
