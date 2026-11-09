# -*- coding: utf-8 -*-
"""运行日志：无控制台环境下把信息写到文件。

## 为什么需要

程序最终用 pythonw.exe 启动，不弹终端窗口。此时 stdout/stderr 指向
一个不存在的控制台 —— 实测在 Python 3.12 下 `sys.stdout` 不是 None，
`print()` 不抛异常，但**写入会静默丢弃**（因为没有真实设备接收）。
结果是：程序出问题时没有任何痕迹可查，只能靠猜。

所以启动最早期就把 stdout/stderr 接到日志文件上，做到：
  - 无控制台时信息不丢
  - 有控制台时（开发调试）同时输出到屏幕
  - 日志按大小轮转，不会无限增长
"""

import io
import os
import sys
import time

# 单文件上限：2MB。超了就轮转，保留一份 .1 备份。
MAX_BYTES = 2 * 1024 * 1024
LOG_NAME = "popdict.log"

_ready = False
_path = None


def log_dir():
    """日志与配置放同一目录，卸载时一起清掉。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "PopDict")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.dirname(os.path.abspath(sys.argv[0] or "."))
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return d


def log_path():
    return os.path.join(log_dir(), LOG_NAME)


class _Tee:
    """把写入同时送到文件与原始流（若有）。

    注意必须显式处理"原始流是 None"的情况：pythonw 下虽然实测
    sys.stdout 不是 None，但在某些 Python 版本/启动方式下会是 None，
    直接 write 会 AttributeError。
    """

    __slots__ = ("_file", "_orig")

    def __init__(self, fileobj, orig):
        self._file = fileobj
        self._orig = orig

    def write(self, s):
        if self._file is not None:
            try:
                self._file.write(s)
                self._file.flush()
            except (ValueError, OSError):
                pass
        if self._orig is not None:
            try:
                self._orig.write(s)
                self._orig.flush()
            except (ValueError, OSError, AttributeError):
                pass
        return len(s) if s else 0

    def flush(self):
        for f in (self._file, self._orig):
            if f is None:
                continue
            try:
                f.flush()
            except (ValueError, OSError):
                pass

    def isatty(self):
        return False

    def fileno(self):
        # 有控制台时给出真实 fd（某些库会调），否则抛异常让调用方降级
        if self._orig is not None:
            try:
                return self._orig.fileno()
            except (OSError, ValueError, AttributeError):
                pass
        raise io.UnsupportedOperation("fileno")

    @property
    def encoding(self):
        return "utf-8"

    @property
    def errors(self):
        return "replace"

    @property
    def buffer(self):
        return getattr(self._orig, "buffer", None)


def _rotate(path):
    """超过上限就滚一份 .1，只留一代 —— 排查问题不需要很久的历史。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            old = path + ".1"
            try:
                if os.path.exists(old):
                    os.remove(old)
            except OSError:
                pass
            os.replace(path, old)
    except OSError:
        pass


def enable(verbose=False):
    """把 stdout/stderr 接到日志文件。返回日志路径。

    必须在程序最早期调用（在可能抛异常的初始化之前），
    这样连"启动阶段崩了"都能留下痕迹。
    """
    global _ready, _path
    if _ready:
        return _path

    path = log_path()
    _rotate(path)

    # 用行缓冲 + utf-8：中文日志不会乱码，且不丢行
    try:
        f = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        _ready = True
        _path = None
        return None

    # 分隔线：多次启动的日志能分清
    try:
        f.write("\n" + "=" * 62 + "\n")
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  启动\n")
        f.write("=" * 62 + "\n")
    except OSError:
        pass

    if not verbose:
        # 只接管，不往屏幕重复写
        sys.stdout = _Tee(f, None)
        sys.stderr = _Tee(f, None)
    else:
        sys.stdout = _Tee(f, sys.__stdout__)
        sys.stderr = _Tee(f, sys.__stderr__)

    _ready = True
    _path = path
    return path


def tail(n=120):
    """读日志末尾若干行，供设置界面展示。"""
    path = log_path()
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError:
        return ""
