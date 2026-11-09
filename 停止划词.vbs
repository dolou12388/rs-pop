Option Explicit
' ==========================================================
'  停止 划词助手
'  结束所有 py_pop.py 进程。双击即可。
' ==========================================================

Dim shell, fso, base, ps, rc

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 用 PowerShell 精确匹配命令行，避免误杀其他 python 程序
ps = "Get-CimInstance Win32_Process -Filter ""Name='pythonw.exe' OR Name='python.exe'"" " & _
     "| Where-Object { $_.CommandLine -like '*py_pop.py*' } " & _
     "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

rc = shell.Run("powershell -NoProfile -WindowStyle Hidden -Command """ & ps & """", 0, True)

MsgBox "划词助手已停止。", 64, "划词助手"
