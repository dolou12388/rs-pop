# -*- coding: utf-8 -*-
"""线程基线探测：区分 Python 线程与本进程内的原生线程来源。"""
import sys, os, ctypes, threading
from ctypes import wintypes as wt

sys.stdout.reconfigure(encoding="utf-8")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

THREAD_QUERY_INFORMATION = 0x0040


def native_thread_count(pid: int) -> int:
    """用 CreateToolhelp32Snapshot 枚举进程原生线程数。"""
    TH32CS_SNAPTHREAD = 0x00000004

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ThreadID", wt.DWORD),
            ("th32OwnerProcessID", wt.DWORD),
            ("tpBasePri", wt.LONG),
            ("tpDeltaPri", wt.LONG),
            ("dwFlags", wt.DWORD),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap in (0, -1):
        return -1
    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(THREADENTRY32)
    n = 0
    ok = kernel32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            n += 1
        ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    kernel32.CloseHandle(snap)
    return n


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else os.getpid()
    print(f"目标 pid = {pid}")
    print(f"  Python threading.active_count() = {threading.active_count()}")
    print(f"  Python 线程名 = {[t.name for t in threading.enumerate()]}")
    print(f"  原生线程总数（含运行时/JIT/GDI 辅助） = {native_thread_count(pid)}")


if __name__ == "__main__":
    main()
