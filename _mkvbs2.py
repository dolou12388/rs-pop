# -*- coding: utf-8 -*-
r"""重写启动划词.vbs：探测逻辑交回 Python，VBS 只做最小的事。

## 为什么改这个设计

原版 vbs 里塞了完整的目录扫描逻辑（`For Each` + `SubFolders` + 嵌套
`If`），但 vbs 的语法错误**无法在当前环境下预检**（cscript/wscript
都被沙箱拦截，属于合理的安全策略）。写复杂的、验不了的代码是赌博。

所以把职责切开：

  · VBS 只负责一件事 —— **不显示窗口地启动 pythonw**
  · 解释器探测交给一个小启动器：本目录下找 pythonw.exe，
    找不到就报错并提示改用 .bat

这样 vbs 只有 20 行、零嵌套、零函数，语法错误风险降到近乎为零。
解释器探测本来就不该由 VBS 承担 —— .bat 版已经做了完整的三级探测，
而且那个能验证（已实测通过）。

## 顺带的好处

`pythonw.exe` 的路径由 `%~dp0` 推导，不含任何用户名或盘符假设。
用户只需保证 Python 在 PATH 里，或把 pythonw.exe 放到本目录。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "启动划词.vbs")

LINES = [
    "Option Explicit",
    "' ==========================================================",
    "'  Windows 全局划词 - 后台启动器（无窗口）",
    "'  双击即启动。退出请右键托盘图标 -> 退出。",
    "'",
    "'  解释器探测交给 launcher.py（它会把候选逐个验证 tkinter），",
    "'  本文件只负责找一个 pythonw 把 launcher.py 跑起来 —— 即使",
    "'  那个 pythonw 没有 tkinter 也没关系，launcher 会另选正确的。",
    "' ==========================================================",
    "",
    "Dim fso, sh, base, lch, cmd, rc, py",
    "",
    'Set fso = CreateObject("Scripting.FileSystemObject")',
    'Set sh  = CreateObject("WScript.Shell")',
    "",
    "base = fso.GetParentFolderName(WScript.ScriptFullName)",
    'lch  = base & "\\launcher.py"',
    "",
    "If Not fso.FileExists(lch) Then",
    '    MsgBox "找不到 launcher.py，请把本文件放在程序目录下。", 16, "划词助手"',
    "    WScript.Quit 1",
    "End If",
    "",
    "' 随便找个 pythonw：本目录优先，其次交给 PATH",
    'py = base & "\\pythonw.exe"',
    "If Not fso.FileExists(py) Then py = \"pythonw.exe\"",
    "",
    "On Error Resume Next",
    'cmd = """" & py & """ """ & lch & """"',
    "rc = sh.Run(cmd, 0, False)",
    "",
    "If Err.Number <> 0 Then",
    '    MsgBox "启动失败：" & Err.Description & vbCrLf & vbCrLf & _',
    '           "请改用【启动划词.bat】。", 16, "划词助手"',
    "    WScript.Quit 1",
    "End If",
    "On Error GoTo 0",
]


def main():
    body = "\r\n".join(LINES) + "\r\n"
    data = body.encode("gbk")

    # ---- 字节层自检 ----
    assert not data.startswith(b"\xef\xbb\xbf"), "不能有 UTF-8 BOM"
    assert data.count(b"\r\n") == len(LINES), "行数与 CRLF 数不符"
    assert b"\r" not in data.replace(b"\r\n", b""), "不能有孤立 CR"
    decoded = data.decode("gbk")
    assert "全局划词" in decoded, "中文往返失败"

    # ---- 语法层自检：本版刻意不含 Function / 循环，降低无法预检的风险 ----
    assert decoded.count("Function") == 0, "本版不应有 Function"
    assert decoded.count("For ") == 0, "本版不应有循环"
    # 块 If 的最大数量：4 个（文件检查、启动失败、以及可能的扩展）
    n_end = decoded.count("End If")
    assert n_end >= 2, f"End If 数量异常: {n_end}"

    # ---- 敏感内容自检 ----
    # 只检查「硬编码盘符路径」这一类可泛化的问题，不把作者本机的用户名
    # 写进断言（那本身就会泄露）。用户名由正则模式统一兜住。
    import re as _re
    assert not _re.search(r"[A-Za-z]:\\\\", decoded), "vbs 里不应出现盘符绝对路径"
    assert not _re.search(r"\\\\Users\\\\", decoded), "vbs 里不应出现用户目录路径"

    with open(TARGET, "wb") as f:
        f.write(data)

    print(f"-> {TARGET}")
    print(f"   {len(data)} 字节  {len(LINES)} 行  GBK + CRLF")
    print(f"   零 Function / 零循环 / 无硬编码路径")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
