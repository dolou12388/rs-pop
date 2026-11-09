# -*- coding: utf-8 -*-
r"""通用启动器：挑一个**带 tkinter** 的解释器，然后启动主程序。

## 为什么需要这个文件

`启动划词.vbs` 要保证"双击即用、不弹窗"，但解释器选择不能靠 PATH：
PATH 里排第一的 pythonw 很可能不是你想要的（本机上是一个没有 tkinter
的嵌入式 Python）。而且 VBScript 里写探测逻辑无法在当前环境预检，
是纯粹的赌博。

所以把探测做成这个可测试的 Python 脚本，VBS 只负责用任意一个
pythonw 把它跑起来 —— 即使那个 pythonw 没有 tkinter 也无所谓，
因为本脚本只做 `subprocess` + 文件探测，不用 tkinter。

## 探测顺序

  1. 环境变量 POPDICT_PYTHON（用户显式指定，最高优先级）
  2. `py -3`（官方 Launcher，会指向真正的官方安装）
  3. PATH 里的 python.exe / pythonw.exe 逐个验证 tkinter
  4. 常见安装目录扫描（%LOCALAPPDATA%\Programs\Python、C:\Python*）
  5. 全部失败 → 打印指引并退出非零
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "py_pop.py")

# 验证候选解释器是否带 tkinter。超时给足：慢盘上首次 import 可能 1-2 秒。
PROBE_TIMEOUT = 12


def _has_tkinter(exe):
    """跑一次 import tkinter，返回 True/False。不改环境、不建窗。"""
    try:
        r = subprocess.run(
            [exe, "-c", "import tkinter"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _candidates():
    """按优先级产出候选解释器路径。

    每个候选都会再经 _has_tkinter 验证，所以这里不追求精确，
    宁可多给几个让验证来筛。
    """
    # 1) 用户显式指定
    explicit = os.environ.get("POPDICT_PYTHON", "").strip()
    if explicit:
        yield explicit

    # 2) py 启动器报告的路径（最可靠，直接问它）
    for exe in ("py.exe", "py"):
        if not shutil.which(exe):
            continue
        try:
            r = subprocess.run(
                [exe, "-3", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            )
            if r.returncode == 0 and r.stdout.strip():
                base = os.path.dirname(r.stdout.strip())
                for name in ("pythonw.exe", "python.exe"):
                    p = os.path.join(base, name)
                    if os.path.exists(p):
                        yield p
        except (OSError, subprocess.SubprocessError):
            pass

    # 3) PATH 里的 python / pythonw，全部试一遍
    for name in ("pythonw.exe", "python.exe", "pythonw", "python"):
        p = shutil.which(name)
        if p:
            yield p

    # 4) 常见安装目录
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs", "Python"))
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        v = os.environ.get(var)
        if v:
            roots.append(os.path.join(v, "Python"))
    for drive in ("C:\\", "D:\\"):
        if os.path.isdir(drive):
            try:
                for n in os.listdir(drive):
                    if n.lower().startswith("python3") or \
                            n.lower() == "python":
                        roots.append(os.path.join(drive, n))
            except OSError:
                pass

    for root in roots:
        if not os.path.isdir(root):
            continue
        # root 本身可能就是安装目录
        dirs = [root]
        try:
            dirs += [os.path.join(root, n) for n in os.listdir(root)
                     if n.lower().startswith("python")]
        except OSError:
            pass
        for d in dirs:
            for name in ("pythonw.exe", "python.exe"):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    yield p


def find_python():
    """返回第一个带 tkinter 的解释器路径；找不到返回 None。"""
    seen = set()
    for c in _candidates():
        if not c:
            continue
        key = os.path.normcase(os.path.abspath(c))
        if key in seen:
            continue
        seen.add(key)
        if os.path.exists(c) and _has_tkinter(c):
            return c
    return None


def main():
    if not os.path.exists(MAIN):
        print(f"找不到 {MAIN}", file=sys.stderr)
        return 1

    py = find_python()
    if not py:
        print("没有找到带 tkinter 的 Python。", file=sys.stderr)
        print("", file=sys.stderr)
        print("解决办法（任选其一）：", file=sys.stderr)
        print("  1. 安装官方 Python，勾选 tcl/tk and IDLE",
              file=sys.stderr)
        print("  2. 设置环境变量 POPDICT_PYTHON 指向可用的 pythonw.exe",
              file=sys.stderr)
        return 2

    # 用同一个解释器继续跑主程序，保证 tkinter 可用
    argv = [py, "-u", MAIN] + sys.argv[1:]
    try:
        if os.name == "nt":
            # 不建新控制台，也不把子进程挂到调用者的 job 上
            DETACHED = 0x00000008
            subprocess.Popen(argv, creationflags=DETACHED,
                             cwd=HERE, close_fds=True)
        else:
            subprocess.Popen(argv, cwd=HERE, close_fds=True)
    except OSError as e:
        print(f"启动失败: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
