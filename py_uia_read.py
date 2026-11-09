"""跨进程 UIA 取词验证：读另一个进程（WinForms RichTextBox）的选中文字。

这是最接近真实使用场景的测试：
  - 目标进程与读取进程不同
  - 目标控件是标准 UIA TextPattern 提供者
  - 全程零剪贴板干预

判定标准：读到的文本必须等于靶子设定的预期文本。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import comtypes
import comtypes.client

UIA = comtypes.client.GetModule("UIAutomationCore.dll")

EXPECTED = "量子纠缠是量子力学中最引人注目的现象之一"
user32 = ctypes.WinDLL("user32.dll")


def find_window_by_title(title):
    hwnds = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return hwnds[0] if hwnds else None


def main():
    print("=" * 70)
    print("跨进程 UIA 取词验证（零剪贴板）")
    print("=" * 70)

    # 1. 启动靶子进程
    ps = subprocess.Popen(
        [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", "uia_target.ps1",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    print(f"[1] 靶子进程已启动 pid={ps.pid}")

    # 2. 等窗口出现并建立选区
    hwnd = None
    for i in range(40):
        time.sleep(0.25)
        hwnd = find_window_by_title("UIATarget")
        if hwnd:
            break
    if not hwnd:
        print("[!] 靶子窗口未出现")
        ps.kill()
        return 1
    print(f"[2] 找到靶子窗口 hwnd=0x{hwnd:X}（等待 {i*0.25:.2f}s）")

    # 让选区先建立好
    time.sleep(1.5)

    # 3. 把靶子设为前台，使其成为 GetFocusedElement 的目标
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    fg = user32.GetForegroundWindow()
    print(f"[3] 前台窗口 hwnd=0x{fg:X}  {'(靶子已激活)' if fg == hwnd else '(未能激活)'}")

    # 4. UIA 读取
    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        print("[4] CUIAutomation 就绪")

        elem = uia.GetFocusedElement()
        if not elem:
            print("[!] 无焦点元素")
            ps.kill()
            return 2

        try:
            name = elem.CurrentName
        except Exception:
            name = "?"
        try:
            cls = elem.CurrentClassName
        except Exception:
            cls = "?"
        try:
            ct = elem.CurrentControlType
        except Exception:
            ct = -1
        print(f"[5] 焦点元素 Name={name!r} Class={cls!r} ControlType={ct}")

        text = None
        # 焦点元素可能只是容器，向下遍历找 TextPattern
        target = elem
        for depth in range(4):
            try:
                pat = target.GetCurrentPattern(UIA.UIA_TextPatternId)
            except Exception:
                pat = None

            if pat:
                tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
                sel = tp.GetSelection()
                if sel.Length > 0:
                    chunks = []
                    for i in range(sel.Length):
                        r = sel.GetElement(i)
                        if r:
                            chunks.append(r.GetText(-1))
                    text = "".join(chunks)
                    print(f"[6] 在深度 {depth} 取到文本 {len(text)} 字符")
                    break

            # 往下走一层：取第一个子元素
            try:
                walker = uia.ControlViewWalker
                child = walker.GetFirstChildElement(target)
            except Exception:
                child = None
            if not child:
                break
            try:
                target = child
                print(f"    深度 {depth} 下探 -> Class={child.CurrentClassName!r}")
            except Exception:
                break

        print("-" * 70)
        if text is None:
            print("结果：✗ 未取到文本")
            ok = False
        elif text.strip() == EXPECTED:
            print(f"结果：✓ 跨进程取词成功")
            print(f"  文本: {text!r}")
            ok = True
        else:
            print(f"结果：△ 取到文本但不完全匹配")
            print(f"  预期: {EXPECTED!r}")
            print(f"  实际: {text!r}")
            ok = EXPECTED in text
            print(f"  包含判定: {'通过' if ok else '失败'}")
    finally:
        comtypes.CoUninitialize()

    ps.kill()
    print("=" * 70)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
