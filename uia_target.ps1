# 测试靶子：WinForms RichTextBox，标准 UIA TextPattern 提供者。
# 建立真实选区后保持窗口存活，由调用方结束进程。

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# 直接写 Unicode 码点，绕开 PowerShell 脚本文件编码在不同宿主下的歧义
$codes = [char[]]@(
    0x91CF, 0x5B50, 0x7EA0, 0x7F20, 0x662F, 0x91CF, 0x5B50, 0x529B, 0x5B66,
    0x4E2D, 0x6700, 0x5F15, 0x4EBA, 0x6CE8, 0x76EE, 0x7684, 0x73B0, 0x8C61,
    0x4E4B, 0x4E00
)
$TEST_TEXT = ($codes -join '')

$form = New-Object System.Windows.Forms.Form
$form.Text = "UIATarget"
$form.Width = 660
$form.Height = 300
$form.StartPosition = "CenterScreen"
$form.TopMost = $true

$rtb = New-Object System.Windows.Forms.RichTextBox
$rtb.Dock = "Fill"
$rtb.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12)
$rtb.Text = $TEST_TEXT
$form.Controls.Add($rtb)

$form.add_Shown({
    $rtb.Focus()
    $rtb.SelectionStart = 0
    $rtb.SelectionLength = $rtb.Text.Length
})

[System.Windows.Forms.Application]::Run($form)
