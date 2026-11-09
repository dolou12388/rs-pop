"""Python + tkinter 原生浮窗内存/CPU 基线测量。

对齐 rs-pop/src/main.rs 的窗口样式：
  overrideredirect(True)  -> WS_POPUP，无边框
  -toolwindow             -> WS_EX_TOOLWINDOW，不占任务栏
  -topmost                -> WS_EX_TOPMOST
  attributes -alpha       -> 分层透明

用途：在不安装任何 C/C++ 工具链的前提下，测出原生浮窗方案的真实资源占用，
      与 Electron 的 392MB / 5 进程基线做对比。
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import tkinter as tk

# ---------- 进程自身计量 ----------
PROCESS_MEMORY_COUNTERS = 0x0400 | 0x0100


class PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def sample_mem():
    k32 = ctypes.WinDLL("kernel32.dll")
    psapi = ctypes.WinDLL("psapi.dll")
    h = k32.GetCurrentProcess()
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    psapi.GetProcessMemoryInfo(wt.HANDLE(h), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / (1024 * 1024), pmc.PagefileUsage / (1024 * 1024)


def sample_cpu(seconds=3.0):
    k32 = ctypes.WinDLL("kernel32.dll")

    class FT(ctypes.Structure):
        _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

    def proc_time():
        c, e, kern, usr = FT(), FT(), FT(), FT()
        k32.GetProcessTimes(
            k32.GetCurrentProcess(),
            ctypes.byref(c), ctypes.byref(e),
            ctypes.byref(kern), ctypes.byref(usr),
        )
        # FILETIME 单位 100ns
        return ((kern.hi << 32) | kern.lo) + ((usr.hi << 32) | usr.lo)

    t1 = proc_time()
    time.sleep(seconds)
    t2 = proc_time()
    delta_ms = (t2 - t1) / 10000.0
    cores = os.cpu_count() or 1
    return delta_ms, delta_ms / (seconds * 1000 * cores) * 100


def main():
    print("=" * 60)
    print("Python + tkinter 原生浮窗基线")
    print("=" * 60)
    print(f"Python  : {sys.version.split()[0]}")
    print(f"tk      : {tk.TkVersion}")

    ws0, pv0 = sample_mem()
    print(f"\n[导入后]  WorkingSet={ws0:7.1f} MB   Private={pv0:7.1f} MB")

    root = tk.Tk()
    root.overrideredirect(True)          # 无边框 = WS_POPUP
    root.attributes("-topmost", True)    # WS_EX_TOPMOST
    root.attributes("-alpha", 0.96)      # 分层透明
    root.geometry("248x44+200+200")
    root.configure(bg="#202022")

    # 从 tk 顶层窗口的 winfo_id 拿到真正的 HWND 祖先，补 WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOPMOST = 0x00000008

    root.update_idletasks()
    child = root.winfo_id()
    user32 = ctypes.WinDLL("user32.dll")
    # 找到 top-level 祖先
    hwnd = user32.GetAncestor(child, 2)  # GA_ROOT
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new_style = style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
    ok = bool(style & WS_EX_TOOLWINDOW)
    print(f"[样式]    HWND=0x{hwnd:X}  EXSTYLE 0x{style:08X} -> 0x{new_style:08X}")

    # 放四个按钮，模拟 pill.html 的 UI
    for i, (label, color) in enumerate(
        [("AI 搜索", "#E8E8EA"), ("翻译", "#E8E8EA"), ("解释", "#E8E8EA"), ("⋮", "#8A8A90")]
    ):
        b = tk.Label(
            root, text=label, fg=color, bg="#202022",
            font=("Microsoft YaHei UI", 9), padx=10,
        )
        b.place(x=8 + i * 60, y=12)

    root.update()
    time.sleep(1.0)

    ws1, pv1 = sample_mem()
    print(f"[窗口显示后] WorkingSet={ws1:7.1f} MB   Private={pv1:7.1f} MB")

    delta, pct = sample_cpu(3.0)
    print(f"[空闲 CPU]  {delta:6.0f} ms / 3s = {pct:.4f}%  (逻辑核 {os.cpu_count()})")

    ws2, pv2 = sample_mem()
    print(f"[3 秒后]   WorkingSet={ws2:7.1f} MB   Private={pv2:7.1f} MB")

    print("\n" + "=" * 60)
    print("对比基线")
    print("=" * 60)
    print(f"Electron 实测       : 392.0 MB / 5 进程 / CPU≈0%")
    print(f"Python+tkinter 实测 : {ws2:5.1f} MB / 1 进程 / CPU={pct:.4f}%")
    print(f"内存降幅            : {(1 - ws2 / 392) * 100:.1f}%")

    root.destroy()


if __name__ == "__main__":
    main()
