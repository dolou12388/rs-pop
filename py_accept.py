"""验收主程序：启动 py_pop.py，长时采样内存与 CPU，确认无崩溃/泄漏。

采样策略：
  · T+3s  首次采样（窗口与轮询已就绪）
  · T+15s 二次采样
  · 期间持续触发左键释放事件，验证轮询与取词链路不崩
判定：进程存活、内存增幅 < 5MB、CPU 平均 < 1%
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

kernel32 = ctypes.WinDLL("kernel32.dll")
psapi = ctypes.WinDLL("psapi.dll")

PROCESS_MEMORY_COUNTERS = 0x0400 | 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


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


class FT(ctypes.Structure):
    _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]


def sample(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return None
        c, e, k, u = FT(), FT(), FT(), FT()
        kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                 ctypes.byref(k), ctypes.byref(u))
        cpu = ((k.hi << 32) | k.lo) + ((u.hi << 32) | u.lo)
        return pmc.WorkingSetSize / 1048576, pmc.PagefileUsage / 1048576, cpu
    finally:
        kernel32.CloseHandle(h)


def main():
    print("=" * 70)
    print("主程序验收：长时采样")
    print("=" * 70)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen(
        [sys.executable, "py_pop.py"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    print(f"[1] 启动 pid={p.pid}")

    time.sleep(3.0)
    s1 = sample(p.pid)
    if not s1:
        print("[!] 无法采样，进程可能已退出")
        print(p.stdout.read()[:800] if p.stdout else "")
        p.kill()
        return 1
    ws1, pv1, cpu1 = s1
    print(f"[2] T+3s   WorkingSet={ws1:6.1f}MB  Private={pv1:6.1f}MB")

    if p.poll() is not None:
        print("[!] 进程已退出")
        print(p.stdout.read()[:1200] if p.stdout else "")
        return 1

    # 模拟一段时间的活动：移动鼠标并点击（会触发轮询与链路，取不到词也正常）
    user32 = ctypes.WinDLL("user32.dll")
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]

    print("[3] T+3s..T+15s 期间模拟 40 次点击（不选词，只压测轮询）")
    for i in range(40):
        user32.SetCursorPos(600 + (i % 5) * 8, 400 + (i % 3) * 6)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.02)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(0.23)

    s2 = sample(p.pid)
    if not s2:
        print("[!] 采样失败")
        p.kill()
        return 1
    ws2, pv2, cpu2 = s2
    print(f"[4] T+15s  WorkingSet={ws2:6.1f}MB  Private={pv2:6.1f}MB")

    # CPU 差分
    delta_ms = (cpu2 - cpu1) / 10000.0
    elapsed = 12.0
    cores = os.cpu_count() or 1
    cpu_pct = delta_ms / (elapsed * 1000 * cores) * 100
    print(f"[5] 12 秒内 CPU 时间 {delta_ms:.0f}ms = {cpu_pct:.4f}% (逻辑核 {cores})")

    alive = p.poll() is None
    print(f"[6] 进程存活: {alive}")
    print(f"    内存增幅: {ws2 - ws1:+.1f}MB")

    fails = 0
    if not alive:
        print("FAIL 进程崩溃")
        fails += 1
    if ws2 - ws1 > 5:
        print("FAIL 内存增长超过 5MB，疑似泄漏")
        fails += 1
    if cpu_pct > 1.0:
        print("FAIL CPU 超过 1%")
        fails += 1

    print("\n" + "=" * 70)
    print("对比")
    print("=" * 70)
    print(f"Electron 基线 : 392.0 MB / 5 进程")
    print(f"本方案        : {ws2:5.1f} MB / 1 进程 / CPU {cpu_pct:.4f}%")
    print(f"内存降幅      : {(1 - ws2 / 392) * 100:.1f}%")

    if p.stdout:
        try:
            p.terminate()
            out = p.stdout.read()[:1200]
            if out.strip():
                print("\n--- 程序输出 ---")
                print(out)
        except Exception:
            pass
    try:
        p.kill()
    except Exception:
        pass

    print("-" * 70)
    print("通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
