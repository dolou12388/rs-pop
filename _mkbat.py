# -*- coding: utf-8 -*-
r"""重写启动划词.bat：去掉硬编码的解释器路径。

## 为什么要脚本化改写

这个 .bat 是 GBK 编码（cmd 默认代码页 936），用文本工具直接改会
破坏中文。这里按 GBK 读、按 GBK 写、换行强制 CRLF，改完再做字节校验。

## 探测顺序（开源版必须是通用的）

  1. `py -3`（Windows Python Launcher，官方安装器默认会装）
  2. PATH 里的 `python`
  3. 常见安装位置的通配扫描
  4. 全部失败 → 给明确指引

原来第一候选写死了一个绝对路径，那是作者本机的安装位置，
在别人机器上必然不存在。开源版必须改成通用探测。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "启动划词.bat")

LINES = [
    "@echo off",
    "setlocal",
    "chcp 936 >nul",
    "title Windows 全局划词",
    "",
    "rem ==========================================================",
    "rem  双击本文件即可启动；关闭此窗口即退出。",
    "rem  本文件会保留一个控制台窗口。想完全无窗口请用【启动划词.vbs】。",
    "rem ==========================================================",
    "",
    'cd /d "%~dp0"',
    "",
    "if not exist launcher.py (",
    "    echo [!] 找不到 launcher.py，请把本文件放在程序目录下。",
    "    pause",
    "    exit /b 1",
    ")",
    "",
    "rem 解释器探测交给 launcher.py：它会逐个验证候选是否带 tkinter。",
    "rem 这里只需随便找个 python 把 launcher 跑起来 —— launcher 内部",
    "rem 用 subprocess 起的才是真正带 tkinter 的那个进程。",
    'set "BOOT="',
    "",
    "rem 1) 官方 Python Launcher（安装器默认会装，最可靠）",
    'if not defined BOOT (',
    "    py -3 -c \"import sys\" >nul 2>&1",
    '    if not errorlevel 1 set "BOOT=py -3"',
    ")",
    "",
    "rem 2) PATH 里的 python",
    'if not defined BOOT (',
    "    python -c \"import sys\" >nul 2>&1",
    '    if not errorlevel 1 set "BOOT=python"',
    ")",
    "",
    "rem 3) 常见安装位置扫描",
    'if not defined BOOT (',
    "    for %%D in (",
    '        "%LOCALAPPDATA%\\Programs\\Python"',
    '        "%ProgramFiles%\\Python"',
    '        "C:\\"',
    "    ) do (",
    '        if not defined BOOT if exist "%%~D" (',
    '            for /d %%V in ("%%~D\\Python3*") do (',
    '                if not defined BOOT if exist "%%~V\\python.exe" (',
    '                    set "BOOT=%%~V\\python.exe"',
    "                )",
    "            )",
    "        )",
    "    )",
    ")",
    "",
    "if not defined BOOT (",
    "    echo.",
    "    echo  [!] 没有找到任何 Python 解释器，无法启动。",
    "    echo.",
    "    echo     解决办法（任选其一）：",
    "    echo        1. 安装官方 Python（勾选 tcl/tk and IDLE）",
    "    echo        2. 安装时勾选 \"Add Python to PATH\"",
    "    echo        3. 设置环境变量 POPDICT_PYTHON 指向 pythonw.exe",
    "    echo.",
    "    pause",
    "    exit /b 1",
    ")",
    "",
    "echo 正在启动 Windows 全局划词 ...",
    "echo 引导解释器: %BOOT%",
    "echo.",
    "",
    "rem launcher.py 会自动挑一个带 tkinter 的解释器并在后台起主程序，",
    "rem 所以本 bat 执行完就退出，程序继续常驻。",
    "chcp 65001 >nul",
    "set PYTHONIOENCODING=utf-8",
    "%BOOT% -u launcher.py",
    "set RC=%ERRORLEVEL%",
    "chcp 936 >nul",
    "",
    "if not \"%RC%\"==\"0\" (",
    "    echo.",
    "    echo 启动失败，退出码 %RC%。",
    "    pause",
    ")",
    "exit /b %RC%",
]


def main():
    # 强制 CRLF + GBK。cmd 对换行敏感，LF-only 会让 rem 注释吃掉下一行。
    body = "\r\n".join(LINES) + "\r\n"
    data = body.encode("gbk")

    # 自检：不该有 BOM，不该有孤立 CR，必须能按 GBK 解回来
    assert not data.startswith(b"\xef\xbb\xbf"), "不能有 UTF-8 BOM"
    assert b"\r\n" in data
    assert data.count(b"\r\n") == len(LINES), "每个逻辑行都要一个 CRLF"
    assert b"\r" not in data.replace(b"\r\n", b""), "不能有孤立 CR"
    decoded = data.decode("gbk")
    assert "全局划词" in decoded, "中文往返失败"

    with open(TARGET, "wb") as f:
        f.write(data)

    print(f"-> {TARGET}")
    print(f"   {len(data)} 字节  {len(LINES)} 行  GBK + CRLF")
    print(f"   中文校验: '全局划词' 存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
