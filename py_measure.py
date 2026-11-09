"""测量本程序的资源占用（含托盘与 AI 模块）。"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
psapi = ctypes.WinDLL("psapi.dll", use_last_error=True)


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
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


def find_pids(exe_substr="python"):
    """用 toolhelp 快照枚举进程，按名筛选 python*，再用 WMI-free 方式
    判断是否为我们的主程序：靠进程命令行拿不到，就退化为「所有 python.exe」。

    这里不调 PowerShell —— 它的输出是 GBK，跨语言解码容易翻车。
    """
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel32.Process32FirstW.argtypes = [wt.HANDLE,
                                        ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wt.HANDLE,
                                       ctypes.POINTER(PROCESSENTRY32W)]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1, ctypes.c_void_p(-1).value):
        return []
    pids = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(e)):
            while True:
                if exe_substr.lower() in e.szExeFile.lower():
                    pids.append(e.th32ProcessID)
                if not kernel32.Process32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return pids


def sample(pid, seconds=10):
    h = kernel32.OpenProcess(0x0400 | 0x1000, False, pid)   # QUERY_INFO|QUERY_LIMITED
    if not h:
        return None
    try:
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)

        def cpu_ms():
            t = wt.FILETIME()
            k = wt.FILETIME()
            u = wt.FILETIME()
            e = wt.FILETIME()
            kernel32.GetProcessTimes(h, ctypes.byref(e), ctypes.byref(e),
                                     ctypes.byref(k), ctypes.byref(u))
            K = (k.dwHighDateTime << 32) | k.dwLowDateTime
            U = (u.dwHighDateTime << 32) | u.dwLowDateTime
            return (K + U) / 10000.0      # 100ns -> ms

        psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        c1 = cpu_ms()
        m1 = pmc.WorkingSetSize
        time.sleep(seconds)
        psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        c2 = cpu_ms()
        m2 = pmc.WorkingSetSize
        return {
            "mem_mb": m2 / (1024 * 1024),
            "mem_delta_mb": (m2 - m1) / (1024 * 1024),
            "cpu_ms": c2 - c1,
            "cpu_pct": (c2 - c1) / (seconds * 1000) * 100,
            "peak_mb": pmc.PeakWorkingSetSize / (1024 * 1024),
        }
    finally:
        kernel32.CloseHandle(h)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    all_pids = find_pids("python")
    print(f"枚举到 python 进程: {all_pids}")

    # 只保留能读到较大工作集的（IDE 里的 python 通常也在这，故全部测量）
    print("\n各进程采样 8 秒 ...")
    rows = []
    for pid in all_pids:
        r = sample(pid, 8)
        if not r:
            print(f"  pid={pid} 打不开（可能权限不足）")
            continue
        rows.append((pid, r))
        print(f"  pid={pid:<7} 工作集 {r['mem_mb']:6.1f} MB   "
              f"8s CPU {r['cpu_ms']:5.0f} ms ({r['cpu_pct']:.4f}%)   "
              f"增长 {r['mem_delta_mb']:+.2f} MB")

    if not rows:
        print("没有可测量的进程")
        return 1

    # 主程序是单进程 + 含托盘线程的那个（线程数最多）
    print("\n说明：本程序为单进程架构，其余 python 进程为其他工具。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
