# -*- coding: utf-8 -*-
"""资源归因探测：逐个模块加载，量化线程数与内存增量。

用途：确认 34 线程 / 62MB 是运行时固有开销还是我们代码引入的，
      避免把第三方运行时的账算到方案头上，也避免反过来掩盖真实开销。
"""
import sys, os, ctypes, gc
from ctypes import wintypes as wt

sys.stdout.reconfigure(encoding="utf-8")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
        ("th32ThreadID", wt.DWORD), ("th32OwnerProcessID", wt.DWORD),
        ("tpBasePri", wt.LONG), ("tpDeltaPri", wt.LONG), ("dwFlags", wt.DWORD),
    ]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


psapi = ctypes.WinDLL("psapi", use_last_error=True)
# 必须显式声明签名：64 位下不声明时 HANDLE / SIZE_T 会被截断成 32 位，
# GetProcessMemoryInfo 会静默失败，结构体保持全 0（本项目已踩过一次同类坑）。
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcess.argtypes = []
psapi.GetProcessMemoryInfo.argtypes = [
    wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wt.DWORD]
psapi.GetProcessMemoryInfo.restype = wt.BOOL


def native_threads() -> int:
    snap = kernel32.CreateToolhelp32Snapshot(4, 0)
    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(THREADENTRY32)
    n = 0
    ok = kernel32.Thread32First(snap, ctypes.byref(te))
    pid = os.getpid()
    while ok:
        if te.th32OwnerProcessID == pid:
            n += 1
        ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    kernel32.CloseHandle(snap)
    return n


def ws_mb() -> float:
    pmc = PROCESS_MEMORY_COUNTERS()
    pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    ok = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return round(pmc.WorkingSetSize / 1048576, 1)


class Meter:
    def __init__(self, title):
        self.title = title
        self.t0 = native_threads()
        self.m0 = ws_mb()
        self.rows = []

    def mark(self, label):
        gc.collect()
        t, m = native_threads(), ws_mb()
        self.rows.append((label, t - self.t0, round(m - self.m0, 1), t, m))

    def report(self):
        print(f"\n=== {self.title} ===")
        print(f"基线: 线程 {self.t0}  工作集 {self.m0} MB")
        print(f"{'阶段':<34}{'线程Δ':>7}{'内存Δ':>9}{'线程':>7}{'工作集':>10}")
        print("-" * 68)
        for label, dt, dm, t, m in self.rows:
            print(f"{label:<34}{dt:>7}{dm:>9}{t:>7}{m:>10}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    base = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, base)

    if mode == "all":
        mt = Meter("逐模块归因")

        import tkinter as tk                      # noqa: E402
        mt.mark("import tkinter")

        root = tk.Tk()
        root.withdraw()
        root.update()
        mt.mark("tk.Tk() + withdraw + update")

        import py_config                         # noqa: E402
        mt.mark("import py_config")

        store = py_config.ConfigStore()
        store.load()
        mt.mark("ConfigStore().load()")

        import py_ai                             # noqa: E402
        mt.mark("import py_ai")

        import py_clipguard as cg                # noqa: E402
        mt.mark("import py_clipguard")

        import py_tray                           # noqa: E402
        mt.mark("import py_tray")

        hicon = py_tray.draw_icon(32)
        mt.mark("draw_icon(32)")

        events = []
        tray = py_tray.Tray(events.append, "probe")
        tray.start(wait=True)
        mt.mark("Tray.start()")

        # 剪贴板事务：验证 OLE 初始化是否拉起 COM 线程池
        with cg.ClipGuard() as g:
            g.restore()
        mt.mark("一次 ClipGuard 事务")

        root.destroy()
        mt.mark("root.destroy()")

        tray.stop()
        mt.mark("tray.stop()")

        mt.report()

    elif mode == "ole":
        mt = Meter("COM/OLE 线程池归因")
        mt.mark("起点")
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.OleInitialize(None)
        mt.mark("OleInitialize")
        import py_clipguard as cg                # noqa: E402
        for i in range(3):
            with cg.ClipGuard():
                pass
            mt.mark(f"ClipGuard #{i + 1}")
        mt.report()


if __name__ == "__main__":
    main()
