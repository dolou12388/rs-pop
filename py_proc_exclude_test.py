# -*- coding: utf-8 -*-
"""进程级屏蔽回归测试：截图工具等「拖框 ≠ 划词」的程序必须被排除。

为什么需要：
  Win+Shift+S 的拖框位移轻松超过 MIN_DRAG_PX(4)，会被当成一次有效拖选。
  一旦放行，我们会（1）合成 Ctrl+C 打断截图工具，（2）在 1.3 秒的事务
  窗口内和截图工具抢剪贴板。用户看到的就是「截图截不出来 / 截图丢了」。

所以必须在 handle() 之前、按**按下瞬间**的前台进程判掉。

运行：py -3 py_proc_exclude_test.py
"""
import inspect
import os
import sys
import time

import py_pop

fails = 0


def check(label, ok, detail=""):
    global fails
    print(("[PASS] " if ok else "[FAIL] ") + label + ("  " + detail if detail else ""))
    if not ok:
        fails += 1


def run():
    print("=" * 70)
    print("1. 进程名解析基本行为")
    print("=" * 70)
    me = py_pop.process_name_of_pid(os.getpid())
    check("能取到自己的进程名", me.startswith("python"), f"实际={me!r}")
    check("进程名统一小写", me == me.lower())
    check("pid=0 返回空串且不抛异常", py_pop.process_name_of_pid(0) == "")
    check("不存在的 pid 返回空串且不抛异常",
          py_pop.process_name_of_pid(99999999) == "")
    check("空 hwnd 返回空串", py_pop.process_name_of_hwnd(0) == "")

    fg = py_pop.user32.GetForegroundWindow()
    fg_name = py_pop.process_name_of_hwnd(fg)
    print(f"  当前前台 hwnd={fg} -> {fg_name!r}")

    print()
    print("=" * 70)
    print("2. 主流截图工具都在排除名单内")
    print("=" * 70)
    must = ["snippingtool.exe", "screenclippinghost.exe", "screensketch.exe",
            "snipaste.exe", "pixpin.exe", "sharex.exe", "greenshot.exe",
            "lightshot.exe", "pickpick.exe", "faststone capture.exe"]
    missing = [m for m in must if m not in py_pop.EXCLUDED_PROCESSES]
    check("截图类工具覆盖完整", not missing, f"缺失={missing}")

    print()
    print("=" * 70)
    print("3. 文本类程序不被误伤")
    print("=" * 70)
    text_apps = ["notepad.exe", "chrome.exe", "msedge.exe", "firefox.exe",
                 "winword.exe", "excel.exe", "code.exe", "acrobat.exe",
                 "typora.exe", "obsidian.exe", "wechat.exe", "notepad++.exe",
                 "sublime_text.exe", "notion.exe", "slack.exe"]
    hurt = [a for a in text_apps if a in py_pop.EXCLUDED_PROCESSES]
    check("文本类程序均未被排除", not hurt, f"误伤={hurt}")

    print()
    print("=" * 70)
    print("4. 判定顺序：按下记录 -> 松开判定 -> 取词")
    print("=" * 70)
    src = inspect.getsource(py_pop.Watcher.tick)
    i_press = src.find("press_proc = process_name_of_hwnd")
    i_chk = src.find("self.press_proc in EXCLUDED_PROCESSES")
    i_handle = src.find("self.handle()")
    print(f"  记录位置={i_press}  判定位置={i_chk}  取词位置={i_handle}")
    check("三个步骤都存在", min(i_press, i_chk, i_handle) >= 0)
    check("记录先于判定", i_press < i_chk)
    check("判定先于取词（否则早已碰过剪贴板）", i_chk < i_handle)

    print()
    print("=" * 70)
    print("5. 缓存正确且开销可忽略")
    print("=" * 70)
    a = py_pop.process_name_of_pid(os.getpid())
    b = py_pop.process_name_of_pid(os.getpid())
    check("同 pid 两次结果一致", a == b, f"{a!r} vs {b!r}")

    py_pop._proc_name_cache.clear()
    t0 = time.perf_counter()
    py_pop.process_name_of_pid(os.getpid())
    cold = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    for _ in range(2000):
        py_pop.process_name_of_pid(os.getpid())
    hot = (time.perf_counter() - t0) * 1000 / 2000
    print(f"  首次 {cold:.3f}ms  缓存命中 {hot*1000:.3f}us")
    check("缓存命中 < 5us（60ms 轮询下可忽略）", hot < 0.005)

    print()
    print("=" * 70)
    print("6. Watcher 有 skip_excluded 计数器")
    print("=" * 70)
    init_src = inspect.getsource(py_pop.Watcher.__init__)
    check("__init__ 声明了 skip_excluded", "skip_excluded" in init_src)
    check("tick 递增 skip_excluded", "self.skip_excluded += 1" in src)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    rc = run()
    print()
    print("=" * 70)
    print("全部通过" if rc == 0 else f"{fails} 项失败")
    print("=" * 70)
    sys.exit(rc)
