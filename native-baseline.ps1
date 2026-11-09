$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# 与 rs-pop/src/main.rs 相同的窗口样式：
# WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
$form = New-Object System.Windows.Forms.Form
$form.Text = "NativeBaseline"
$form.FormBorderStyle = "None"
$form.StartPosition = "Manual"
$form.Bounds = New-Object System.Drawing.Rectangle(200, 200, 248, 44)
$form.ShowInTaskbar = $false
$form.TopMost = $true

# 还原出 NOACTIVATE 行为
$form.add_Shown({
    $sig = @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true)]
public static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex);
'@
})

$form.Show()

$pid_ = $PID
$proc = Get-Process -Id $pid_

"=== 原生浮窗基线（.NET WinForms / 单进程） ==="
"PID            : $pid_"
"ProcessCount   : 1"
"WorkingSet(MB) : {0:N1}" -f ($proc.WorkingSet64 / 1MB)
"PrivateMB      : {0:N1}" -f ($proc.PrivateMemorySize64 / 1MB)

# 空闲 CPU 采样：连续 30 次，每次间隔 100ms
Start-Sleep -Milliseconds 500
$c1 = $proc.TotalProcessorTime
Start-Sleep -Seconds 3
$proc.Refresh()
$c2 = $proc.TotalProcessorTime

$deltaMs = ($c2 - $c1).TotalMilliseconds
$cpuPct = $deltaMs / (3000 * [Environment]::ProcessorCount) * 100

"CPUSampleMs    : {0:N0}  (3 秒内)" -f $deltaMs
"CPUPercent     : {0:N3}%" -f $cpuPct
"Note           : 含 PowerShell 脚本引擎本身开销，非纯 Win32"

$proc.Refresh()
"WorkingSetAfter(MB): {0:N1}" -f ($proc.WorkingSet64 / 1MB)
"PrivateAfter(MB)   : {0:N1}" -f ($proc.PrivateMemorySize64 / 1MB)

$form.Close()
