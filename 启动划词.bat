@echo off
setlocal
chcp 936 >nul
title Windows 全局划词

rem ==========================================================
rem  双击本文件即可启动；关闭此窗口即退出。
rem  本文件会保留一个控制台窗口。想完全无窗口请用【启动划词.vbs】。
rem ==========================================================

cd /d "%~dp0"

if not exist launcher.py (
    echo [!] 找不到 launcher.py，请把本文件放在程序目录下。
    pause
    exit /b 1
)

rem 解释器探测交给 launcher.py：它会逐个验证候选是否带 tkinter。
rem 这里只需随便找个 python 把 launcher 跑起来 —— launcher 内部
rem 用 subprocess 起的才是真正带 tkinter 的那个进程。
set "BOOT="

rem 1) 官方 Python Launcher（安装器默认会装，最可靠）
if not defined BOOT (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BOOT=py -3"
)

rem 2) PATH 里的 python
if not defined BOOT (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BOOT=python"
)

rem 3) 常见安装位置扫描
if not defined BOOT (
    for %%D in (
        "%LOCALAPPDATA%\Programs\Python"
        "%ProgramFiles%\Python"
        "C:\"
    ) do (
        if not defined BOOT if exist "%%~D" (
            for /d %%V in ("%%~D\Python3*") do (
                if not defined BOOT if exist "%%~V\python.exe" (
                    set "BOOT=%%~V\python.exe"
                )
            )
        )
    )
)

if not defined BOOT (
    echo.
    echo  [!] 没有找到任何 Python 解释器，无法启动。
    echo.
    echo     解决办法（任选其一）：
    echo        1. 安装官方 Python（勾选 tcl/tk and IDLE）
    echo        2. 安装时勾选 "Add Python to PATH"
    echo        3. 设置环境变量 POPDICT_PYTHON 指向 pythonw.exe
    echo.
    pause
    exit /b 1
)

echo 正在启动 Windows 全局划词 ...
echo 引导解释器: %BOOT%
echo.

rem launcher.py 会自动挑一个带 tkinter 的解释器并在后台起主程序，
rem 所以本 bat 执行完就退出，程序继续常驻。
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
%BOOT% -u launcher.py
set RC=%ERRORLEVEL%
chcp 936 >nul

if not "%RC%"=="0" (
    echo.
    echo 启动失败，退出码 %RC%。
    pause
)
exit /b %RC%
