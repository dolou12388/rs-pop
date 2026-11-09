# -*- coding: utf-8 -*-
"""实测当前程序的真实内存占用（不含猜测，全部来自 Win32 计数）。

口径说明（这三个数经常被混为一谈）：
  * 工作集 WorkingSetSize   —— 任务管理器"内存"列，含共享 DLL 页。
                                物理 RAM 的实际占用，但不是我们独吞的。
  * 私有字节 PrivateUsage   —— 只有本进程用、不能与他人共享的部分。
                                这是"再加一个实例要多花多少内存"的答案。
  * 提交大小 PagefileUsage  —— 私有 + 可能被换出的部分。

另外单独量一次 Tk 的基线（一个空 Tk 窗口吃多少），用来把框架开销
从我们的代码开销里剥离出来 —— 否则 Tk 那 20+MB 会被算成方案的成本。
"""
import ctypes
import os
import sys
from ctypes import wintypes as wt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)
user32 = ctypes.WinDLL("user32.dll", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PMC_EX(ctypes.Structure):
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
        ("PrivateUsage", ctypes.c_size_t),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260),
    ]


def mem_of(pid):
    """按 PID 读内存。用 OpenProcess 而不是伪句柄，才能看别的进程。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        mc = PMC_EX()
        mc.cb = ctypes.sizeof(mc)
        if not psapi.GetProcessMemoryInfo(h, ctypes.byref(mc), mc.cb):
            return None
        return {
            "rss": int(mc.WorkingSetSize),
            "peak_rss": int(mc.PeakWorkingSetSize),
            "private": int(mc.PrivateUsage),
            "pagefile": int(mc.PagefileUsage),
            "peak_pagefile": int(mc.PeakPagefileUsage),
        }
    finally:
        kernel32.CloseHandle(h)


def open_processes():
    """枚举所有进程，返回 [(pid, exe)]。"""
    TH32CS_SNAPPROCESS = 0x00000002
    out = []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return out
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(pe)
        if kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                exe = pe.szExeFile.decode("mbcs", "replace")
                out.append((pe.th32ProcessID, exe))
                if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return out


def cmdline_of(pid):
    """拿命令行，用于区分不同的 python 脚本。"""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\")"
             f".CommandLine"],
            capture_output=True, text=True, timeout=8,
            creationflags=0x08000000)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def fmt(n):
    if n is None:
        return "—"
    v = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if v < 1024 or u == "GB":
            return f"{v:.1f} {u}"
        v /= 1024.0
    return f"{v:.1f} GB"


def tk_baseline():
    """量一个空 Tk 窗口的基线开销：框架本身的成本。"""
    before = mem_of(os.getpid())
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.update()
    after_tk = mem_of(os.getpid())
    w = tk.Toplevel(root)
    w.geometry("400x300")
    w.update()
    after_win = mem_of(os.getpid())
    w.destroy()
    root.destroy()
    return before, after_tk, after_win


def main():
    print("=" * 70)
    print("划词助手 · 运行内存实测")
    print("=" * 70)

    # ---- 1. 找正在运行的实例 ----
    cands = []
    for pid, exe in open_processes():
        if "python" in exe.lower():
            m = mem_of(pid)
            if m:
                cands.append((pid, exe, m))

    print(f"\n[1] 发现 {len(cands)} 个 python 进程")
    target = None
    for pid, exe, m in cands:
        cl = cmdline_of(pid)
        is_pop = "py_pop.py" in cl
        tag = "  <-- 划词助手" if is_pop else ""
        print(f"  PID {pid:<6} {exe:<14} RSS={fmt(m['rss']):>9} "
              f"私有={fmt(m['private']):>9}{tag}")
        if cl:
            print(f"           命令行: {cl[:100]}")
        if is_pop and target is None:
            target = (pid, m)

    if not target:
        print("\n  [!] 没找到运行中的划词助手实例。")
        print("      当前这个测量进程是自己，下面量本进程基线。")
        me = mem_of(os.getpid())
        print(f"      本进程 RSS={fmt(me['rss'])} 私有={fmt(me['private'])}")
    else:
        pid, m = target
        print(f"\n[2] 划词助手实例 PID {pid} 的内存")
        print(f"  工作集 (任务管理器口径) : {fmt(m['rss']):>10}  "
              f"{m['rss'] / 1048576:.2f} MB")
        print(f"  峰值工作集              : {fmt(m['peak_rss']):>10}  "
              f"{m['peak_rss'] / 1048576:.2f} MB")
        print(f"  私有字节 (真实独占)     : {fmt(m['private']):>10}  "
              f"{m['private'] / 1048576:.2f} MB")
        print(f"  提交大小                : {fmt(m['pagefile']):>10}  "
              f"{m['pagefile'] / 1048576:.2f} MB")
        print(f"  峰值提交                : {fmt(m['peak_pagefile']):>10}  "
              f"{m['peak_pagefile'] / 1048576:.2f} MB")

    # ---- 3. Tk 框架基线（剥离框架成本）----
    print("\n[3] 框架基线：空 Tk 窗口本身吃多少")
    b, a1, a2 = tk_baseline()
    if b and a1 and a2:
        print(f"  仅 import tkinter + Tk() : "
              f"私有 +{fmt(a1['private'] - b['private'])}  "
              f"RSS +{fmt(a1['rss'] - b['rss'])}")
        print(f"  再开一个 Toplevel 窗口   : "
              f"私有 +{fmt(a2['private'] - a1['private'])}  "
              f"RSS +{fmt(a2['rss'] - a1['rss'])}")

    print("\n" + "=" * 70)
    print("口径提示")
    print("  · 任务管理器「内存」= 工作集，含共享的系统 DLL 页")
    print("  · 「私有字节」才是本程序真正独占的物理内存")
    print("  · Tk/Tcl 运行时本身约占 20MB 私有内存，非业务代码开销")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
