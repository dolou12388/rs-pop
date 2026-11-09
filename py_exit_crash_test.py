# -*- coding: utf-8 -*-
"""验证退出路径不再崩于 Tcl_AsyncDelete。

对应真实故障：
  设置窗口在独立线程持有自己的 Tk 实例。若主程序退出时不管它，
  解释器收尾阶段会在主线程里析构那些 Tcl 对象，实测在 stderr 输出
      Tcl_AsyncDelete: async handler deleted by the wrong thread
  并以 exit code 1 结束。

本测试的真实性在于：它**起子进程**跑，检查子进程的退出码与 stderr，
而不是在同进程内 try/except —— 那种崩溃发生在解释器收尾阶段，
同进程内根本捕获不到。
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


SCENARIO = r'''
import sys, os, time, threading, queue
HERE = os.environ["SCEN_DIR"]
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")
import tkinter as tk
import py_config, py_settings

mode = os.environ.get("SCEN", "with_settings")

root = tk.Tk()
root.withdraw()

def job():
    # 后台线程开设置窗口（模拟托盘菜单点"设置"）
    py_settings.open_settings(py_config.store(), wait=False)

def wait_up(root, n=1):
    for _ in range(150):
        root.update()
        time.sleep(0.02)
        if len(py_settings._ALIVE) >= n:
            return True
    return False

if mode in ("with_settings", "settings_and_close"):
    threading.Thread(target=job, daemon=True).start()
    wait_up(root)
    print("SETTINGS_UP", len(py_settings._ALIVE), flush=True)

    if mode == "settings_and_close":
        # 场景：用户先手动关了设置窗，再退出主程序
        py_settings.close_all(timeout=2.0)
        print("SETTINGS_CLOSED", len(py_settings._ALIVE), flush=True)

# 退出主循环，走真实收尾路径
root.after(200, root.quit)
root.mainloop()

# 复刻 py_pop.main() 的 finally 收尾顺序
n = py_settings.close_all(timeout=2.0)
print("EXIT_CLOSED", n, flush=True)
try:
    root.destroy()
except tk.TclError:
    pass
print("DONE", flush=True)
'''

SC3 = SCENARIO.replace(
    'print("SETTINGS_UP", len(py_settings._ALIVE), flush=True)',
    'print("SETTINGS_UP", len(py_settings._ALIVE), flush=True)\n'
    '    for i in range(2):\n'
    '        py_settings.close_all(timeout=2.0)\n'
    '        py_settings.open_settings(py_config.store(), wait=False)\n'
    '        wait_up(root)\n'
    '        print("ROUND", i + 2, len(py_settings._ALIVE), flush=True)')


def _write_and_run(script, scen, tag, timeout=90):
    """把场景脚本落成临时文件再跑。

    不能用 python -c：那种方式下 __file__ 不存在，且相对导入会失效。
    """
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".py", prefix="popscen_",
                                dir=HERE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        env = dict(os.environ)
        env["SCEN"] = scen
        env["SCEN_DIR"] = HERE
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run(
            [PY, path], cwd=HERE, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    print(f"\n  --- {tag} ---")
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if out:
        print("  stdout:", out.replace("\n", " | "))
    if err:
        print("  stderr:", err.replace("\n", " | ")[:400])
    print(f"  exit={p.returncode}")
    return p, out, err


def run_scenario(name, scen):
    return _write_and_run(SCENARIO, scen, name)


def main():
    print("[1] 有设置窗口未关时退出主程序")
    p, out, err = run_scenario("with_settings", "with_settings")
    check("退出码为 0", p.returncode == 0, f"exit={p.returncode}")
    check("stderr 无 Tcl_AsyncDelete",
          "Tcl_AsyncDelete" not in err, err[:200])
    check("stderr 无 Traceback", "Traceback" not in err, err[:200])
    check("确实开过设置窗", "SETTINGS_UP 1" in out, out)
    check("收尾时关闭了设置窗", "EXIT_CLOSED 1" in out, out)
    check("正常走到 DONE", "DONE" in out, out)

    print("\n[2] 先手动关设置窗再退出")
    p, out, err = run_scenario("settings_and_close", "settings_and_close")
    check("退出码为 0", p.returncode == 0, f"exit={p.returncode}")
    check("stderr 无 Tcl_AsyncDelete", "Tcl_AsyncDelete" not in err, err[:200])
    check("手动关闭已清空实例", "SETTINGS_CLOSED 0" in out, out)
    check("收尾时无需再关", "EXIT_CLOSED 0" in out, out)

    print("\n[3] 完全没有设置窗口时退出")
    p, out, err = run_scenario("none", "none")
    check("退出码为 0", p.returncode == 0, f"exit={p.returncode}")
    check("stderr 干净", not err, err[:200])
    check("正常走到 DONE", "DONE" in out, out)

    print("\n[4] 连续开关设置窗 3 次后退出")
    p, out, err = _write_and_run(SC3, "with_settings", "连续开关", timeout=120)
    check("连续开关后退出码为 0", p.returncode == 0, f"exit={p.returncode}")
    check("无 Tcl_AsyncDelete", "Tcl_AsyncDelete" not in err, err[:200])
    check("无实例泄漏", "EXIT_CLOSED 1" in out or "EXIT_CLOSED 0" in out, out)

    print("\n" + "=" * 72)
    total = PASS + FAIL
    print(f"{total} 项检查，全部通过" if FAIL == 0
          else f"{total} 项检查，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
