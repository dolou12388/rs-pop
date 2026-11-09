# -*- coding: utf-8 -*-
"""读自身进程的内存占用。

## 用途

只做一件事：`snapshot()` 读一次当前进程的 7 个指标。
不做持续采样、不起线程、不落盘 —— 调用方（py_pop 启动/退出、
py_mem_now 外部测量）用一次就够了。

## 采什么

| 指标 | 来源 | 意义 |
|---|---|---|
| 工作集 RSS | `GetProcessMemoryInfo.WorkingSetSize` | 任务管理器"内存"那个数 |
| 私有字节 | `PrivateUsage` | 真实独占量，不被共享 DLL 虚高 |
| 峰值工作集 | `PeakWorkingSetSize` | 历史最高水位 |
| CPU 时间 | `GetProcessTimes` | 累计内核+用户态时间 |
| 句柄数 | `GetProcessHandleCount` | 泄漏的强信号 |
| 线程数 | Toolhelp32 快照 | 线程泄漏 |

## 为什么不用 psutil

psutil 会额外导入约 4MB 模块且拖慢冷启动。这里要的指标用 ctypes
直接读 PSAPI 就够，零依赖、微秒级。
"""

import ctypes
import os
import time
from ctypes import wintypes as wt

kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)

# 关键：这些签名必须在模块加载时一次性声明好。
# 之前只在函数体内设 argtypes，导致 GetCurrentProcess 返回的伪句柄 -1
# 被后续调用的 ctypes 推断成 32 位 int，传给 HANDLE 参数时高位丢失，
# GetProcessHandleCount 因此静默失败（返回 0，句柄数读成 None）。
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcess.argtypes = []

kernel32.GetProcessHandleCount.restype = wt.BOOL
kernel32.GetProcessHandleCount.argtypes = [wt.HANDLE,
                                           ctypes.POINTER(wt.DWORD)]

kernel32.GetProcessTimes.restype = wt.BOOL
kernel32.GetProcessTimes.argtypes = [wt.HANDLE, wt.LPVOID, wt.LPVOID,
                                     wt.LPVOID, wt.LPVOID]

kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]

psapi.GetProcessMemoryInfo.restype = wt.BOOL
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD]



class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
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
        ("PrivateUsage", ctypes.c_size_t),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


def _ft_to_sec(ft):
    v = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return v / 10_000_000.0          # 100ns 单位


def snapshot():
    """采一次当前进程的资源数据。失败字段为 None，不抛异常。"""
    out = {
        "ts": time.time(),
        "rss": None,          # 工作集字节
        "peak_rss": None,
        "private": None,      # 私有字节
        "cpu_sec": None,      # 累计 CPU 秒
        "handles": None,
        "threads": None,
    }

    # 拿自身进程句柄。用伪句柄也行，但 PSAPI 要求真句柄更稳。
    h = kernel32.GetCurrentProcess()
    try:
        mc = PROCESS_MEMORY_COUNTERS_EX()
        mc.cb = ctypes.sizeof(mc)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(mc), mc.cb):
            out["rss"] = int(mc.WorkingSetSize)
            out["peak_rss"] = int(mc.PeakWorkingSetSize)
            out["private"] = int(mc.PrivateUsage)
    except Exception:
        pass

    try:
        c = wt.DWORD()
        if kernel32.GetProcessHandleCount(h, ctypes.byref(c)):
            out["handles"] = int(c.value)
    except Exception:
        pass

    try:
        ct = FILETIME()
        et = FILETIME()
        kt = FILETIME()
        ut = FILETIME()
        if kernel32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                                    ctypes.byref(kt), ctypes.byref(ut)):
            out["cpu_sec"] = _ft_to_sec(kt) + _ft_to_sec(ut)
    except Exception:
        pass

    # 线程数：走 Toolhelp32 快照（比 NtQueryInformationProcess 更通用）
    try:
        TH32CS_SNAPTHREAD = 0x00000004

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ThreadID", wt.DWORD), ("th32OwnerProcessID", wt.DWORD),
                ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                ("dwFlags", wt.DWORD),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snap and snap != wt.HANDLE(-1).value:
            try:
                te = THREADENTRY32()
                te.dwSize = ctypes.sizeof(te)
                pid = os.getpid()
                n = 0
                if kernel32.Thread32First(snap, ctypes.byref(te)):
                    while True:
                        if te.th32OwnerProcessID == pid:
                            n += 1
                        if not kernel32.Thread32Next(snap, ctypes.byref(te)):
                            break
                out["threads"] = n
            finally:
                kernel32.CloseHandle(snap)
    except Exception:
        pass

    return out


# ---------- 格式化 ----------

def fmt_bytes(n):
    if n is None:
        return "—"
    units = ("B", "KB", "MB", "GB")
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f} {u}" if u != "B" else f"{int(v)} B"
        v /= 1024.0
    return f"{v:.1f} GB"
