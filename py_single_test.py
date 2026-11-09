# -*- coding: utf-8 -*-
"""单实例守卫测试。

背景：本程序是常驻托盘程序，没有守卫时重复启动会各注册一个托盘图标，
实测堆积到 4 个图标才被发现。这里验证互斥量真的拦住了第二个实例。

关键点：必须在**子进程**里验证 —— 同进程内重复调用 CreateMutexW 拿的是
同一个句柄，永远测不出"已被占用"。
"""

import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL  {name}   {detail}")


def run_child(code, timeout=20):
    return subprocess.run([sys.executable, "-c", code], cwd=BASE,
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def main():
    print("=" * 62)
    print("单实例守卫测试")
    print("=" * 62)

    print("\n[A] 常量与函数")
    import py_pop
    check("互斥量名已定义", bool(py_pop.SINGLE_INSTANCE_MUTEX),
          py_pop.SINGLE_INSTANCE_MUTEX)
    check("互斥量名带 Local\\ 前缀（仅当前会话）",
          py_pop.SINGLE_INSTANCE_MUTEX.startswith("Local\\"),
          py_pop.SINGLE_INSTANCE_MUTEX)
    check("ERROR_ALREADY_EXISTS 常量为 183",
          py_pop.ERROR_ALREADY_EXISTS == 183,
          py_pop.ERROR_ALREADY_EXISTS)
    check("acquire_single_instance 存在",
          callable(py_pop.acquire_single_instance))

    print("\n[B] 跨进程占用（核心）")
    # 子进程 A 持有互斥量并保持，
    # 子进程 B 在 A 存活期间尝试获取，必须失败。
    holder = r"""
import sys, time, ctypes
from ctypes import wintypes as wt
sys.path.insert(0, %r)
k = ctypes.WinDLL("kernel32.dll", use_last_error=True)
k.CreateMutexW.restype = wt.HANDLE
k.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
h = k.CreateMutexW(None, False, "Local\\PopDict_TestMutex_1")
print("HELD", bool(h), flush=True)
time.sleep(6)
""" % BASE

    try:
        a = subprocess.Popen([sys.executable, "-c", holder], cwd=BASE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
        # 等 A 真的拿到互斥量
        line = a.stdout.readline()
        got = "HELD True" in line
        check("子进程 A 成功持有互斥量", got, line)

        time.sleep(0.4)

        prober = r"""
import sys, ctypes
from ctypes import wintypes as wt
k = ctypes.WinDLL("kernel32.dll", use_last_error=True)
k.CreateMutexW.restype = wt.HANDLE
k.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
h = k.CreateMutexW(None, False, "Local\\PopDict_TestMutex_1")
err = ctypes.get_last_error()
print("ERR", err)
print("BORROWED", bool(h))
"""
        b = run_child(prober, timeout=15)
        check("子进程 B 能拿到句柄（互斥量是打开的）",
              "BORROWED True" in b.stdout, b.stdout + b.stderr)
        check("子进程 B 收到 ERROR_ALREADY_EXISTS(183)",
              "ERR 183" in b.stdout, b.stdout + b.stderr)

        # 关键：py_pop.acquire_single_instance 在同一条件下必须返回 False
        # 但它的互斥量名是生产名，先让 A 也用生产名占一次
        a.terminate()
        a.wait(timeout=5)
    finally:
        try:
            if a.poll() is None:
                a.kill()
        except Exception:
            pass

    print("\n[C] 真实函数行为（用真实互斥量名）")
    holder2 = r"""
import sys, time
sys.path.insert(0, %r)
import py_pop
ok, h = py_pop.acquire_single_instance()
print("FIRST", ok, flush=True)
time.sleep(6)
""" % BASE

    a2 = None
    try:
        a2 = subprocess.Popen([sys.executable, "-c", holder2], cwd=BASE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace")
        line = a2.stdout.readline()
        check("第一个实例获取成功", "FIRST True" in line, line)
        time.sleep(0.4)

        second = r"""
import sys
sys.path.insert(0, %r)
import py_pop
ok, h = py_pop.acquire_single_instance()
print("SECOND", ok)
""" % BASE
        b2 = run_child(second, timeout=15)
        check("第二个实例被拒绝", "SECOND False" in b2.stdout,
              b2.stdout + b2.stderr)
    finally:
        if a2 is not None:
            try:
                a2.terminate()
                a2.wait(timeout=5)
            except Exception:
                pass

    print("\n[D] 释放后可再次获取")
    third = r"""
import sys
sys.path.insert(0, %r)
import py_pop
ok, h = py_pop.acquire_single_instance()
print("AFTER_RELEASE", ok)
""" % BASE
    b3 = run_child(third, timeout=15)
    check("前一实例退出后可重新获取",
          "AFTER_RELEASE True" in b3.stdout, b3.stdout + b3.stderr)

    print("\n[E] 主程序接线（静态检查）")
    src = open(os.path.join(BASE, "py_pop.py"), encoding="utf-8").read()
    i_check = src.find("acquire_single_instance()")
    i_tk = src.find("root = tk.Tk()", i_check if i_check > 0 else 0)
    check("main 里调用了单实例检查", i_check > 0)
    check("检查在建窗之前", 0 < i_check < i_tk,
          f"check@{i_check} tk@{i_tk}")
    check("被拒绝时有提示", "已经在运行" in src)

    print("\n" + "=" * 62)
    print(f"结果：{PASS} 通过 / {FAIL} 失败 / 共 {PASS + FAIL}")
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
