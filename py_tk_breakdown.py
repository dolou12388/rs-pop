# -*- coding: utf-8 -*-
"""Tk 内存构成拆解：把 26.6MB 归因到具体调用。

目的：判断"把浮层换成原生 Win32 窗口"能省下多少，
      以及结果窗按需创建时最坏延迟代价是多少。
"""
import sys, os, ctypes, gc, time
from ctypes import wintypes as wt

sys.stdout.reconfigure(encoding="utf-8")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)


class PMC(ctypes.Structure):
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


kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcess.argtypes = []
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
psapi.GetProcessMemoryInfo.restype = wt.BOOL


class TE(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
        ("th32ThreadID", wt.DWORD), ("th32OwnerProcessID", wt.DWORD),
        ("tpBasePri", wt.LONG), ("tpDeltaPri", wt.LONG), ("dwFlags", wt.DWORD),
    ]


def threads():
    s = kernel32.CreateToolhelp32Snapshot(4, 0)
    te = TE(); te.dwSize = ctypes.sizeof(TE)
    pid = os.getpid(); n = 0
    ok = kernel32.Thread32First(s, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            n += 1
        ok = kernel32.Thread32Next(s, ctypes.byref(te))
    kernel32.CloseHandle(s)
    return n


def ws():
    pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
    if not psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return round(pmc.WorkingSetSize / 1048576, 1)


rows = []


def mark(label, t0=None):
    gc.collect()
    rows.append((label, threads(), ws()))
    if t0 is not None:
        rows[-1] = rows[-1] + (round((time.perf_counter() - t0) * 1000, 1),)


def report(title):
    print(f"\n=== {title} ===")
    bm = rows[0][2]
    print(f"{'阶段':<40}{'线程':>6}{'工作集':>9}{'内存Δ':>9}{'耗时ms':>9}")
    print("-" * 74)
    for r in rows:
        label, t, m = r[0], r[1], r[2]
        ms = r[3] if len(r) > 3 else ""
        print(f"{label:<40}{t:>6}{m:>9.1f}{m - bm:>+9.1f}{str(ms):>9}")


def main():
    mark("基线（仅解释器）")

    t = time.perf_counter()
    import tkinter as tk
    mark("import tkinter", t)

    t = time.perf_counter()
    root = tk.Tk()
    mark("tk.Tk()", t)

    t = time.perf_counter()
    root.withdraw()
    mark("root.withdraw()", t)

    t = time.perf_counter()
    root.update()
    mark("root.update()", t)

    t = time.perf_counter()
    w = tk.Toplevel(root)
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg="#202022")
    mark("Toplevel + 无边框 + 置顶", t)

    t = time.perf_counter()
    for lab in ("AI 搜索", "翻译", "解释", "复制"):
        tk.Label(w, text=lab, fg="#E8E8EA", bg="#202022",
                 font=("Microsoft YaHei UI", 9), padx=9, pady=6).place(x=6, y=8)
    mark("4 个 Label（YaHei UI 9pt）", t)

    t = time.perf_counter()
    w.update_idletasks()
    mark("update_idletasks（真正建窗）", t)

    t = time.perf_counter()
    w.deiconify()
    root.update()
    mark("deiconify + 首帧绘制", t)

    report("Tk 内存构成")

    # 结果窗另建一个 Toplevel 的边际成本
    t = time.perf_counter()
    rw = tk.Toplevel(root)
    txt = tk.Text(rw, wrap="word", font=("Microsoft YaHei UI", 10))
    txt.pack(fill="both", expand=True)
    rw.update_idletasks()
    mark("新增一个结果窗（Text 控件）", t)
    report("含结果窗")


if __name__ == "__main__":
    main()
