Option Explicit
' ==========================================================
'  Windows 全局划词 - 后台启动器（无窗口）
'  双击即启动。退出请右键托盘图标 -> 退出。
'
'  解释器探测交给 launcher.py（它会把候选逐个验证 tkinter），
'  本文件只负责找一个 pythonw 把 launcher.py 跑起来 —— 即使
'  那个 pythonw 没有 tkinter 也没关系，launcher 会另选正确的。
' ==========================================================

Dim fso, sh, base, lch, cmd, rc, py

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
lch  = base & "\launcher.py"

If Not fso.FileExists(lch) Then
    MsgBox "找不到 launcher.py，请把本文件放在程序目录下。", 16, "划词助手"
    WScript.Quit 1
End If

' 随便找个 pythonw：本目录优先，其次交给 PATH
py = base & "\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw.exe"

On Error Resume Next
cmd = """" & py & """ """ & lch & """"
rc = sh.Run(cmd, 0, False)

If Err.Number <> 0 Then
    MsgBox "启动失败：" & Err.Description & vbCrLf & vbCrLf & _
           "请改用【启动划词.bat】。", 16, "划词助手"
    WScript.Quit 1
End If
On Error GoTo 0
