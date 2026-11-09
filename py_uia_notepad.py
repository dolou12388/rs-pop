"""自动对照测试：在记事本中建立真实选区，再用 UIA 读取。

流程：
  1. 用 Win32 API 启动 notepad.exe
  2. 找到其编辑区子窗口（Edit / RichEditD2DPT）
  3. 通过 SendMessage 写入测试文字
  4. 通过 EM_SETSEL 建立真实选区（全选）
  5. 后台线程用 UIA 读取选区文本，与预期比对

这样避免"人工去选一段文字再重跑"的不确定性，结论可复现。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time

import comtypes.client

UIA = comtypes.client.GetModule("UIAutomationCore.dll")

user32 = ctypes.WinDLL("user32.dll")

TEST_TEXT = "量子纠缠是量子力学中最引人注目的现象之一"

# --- Win32 常量 ---
WM_SETTEXT = 0x000C
EM_SETSEL = 0x00B1
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E


def find_notepad_edit():
    """枚举窗口，找 notepad 的编辑区。"""
    results = []

    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        if cls in ("Notepad", "Edit", "RichEditD2DPT", "RichEdit50W"):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append((hwnd, cls, pid.value))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return results


def find_child_edit(parent):
    """在指定父窗口下找编辑控件。"""
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in ("Edit", "RichEditD2DPT", "RichEdit50W", "Windows.UI.Core.CoreWindow"):
            found.append((hwnd, buf.value))
        return True

    user32.EnumChildWindows(parent, EnumProc(cb), 0)
    return found


def uia_read_focused(tag):
    """在独立线程里读 UIA（comtypes 需要每线程 CoInitialize）。"""
    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elem = uia.GetFocusedElement()
        if not elem:
            print(f"[{tag}] 无焦点元素")
            return None

        info = []
        for n, p in (("Name", "CurrentName"), ("Class", "CurrentClassName")):
            try:
                info.append(f"{n}={getattr(elem, p)!r}")
            except Exception:
                info.append(f"{n}=?")
        try:
            info.append(f"ControlType={elem.CurrentControlType}")
        except Exception:
            pass
        print(f"[{tag}] 焦点: {'  '.join(info)}")

        try:
            pat = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
        except Exception as e:
            print(f"[{tag}] TextPattern 异常 {type(e).__name__}: {e}")
            return None

        if not pat:
            print(f"[{tag}] TextPattern 返回空")
            return None

        tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
        sel = tp.GetSelection()
        print(f"[{tag}] 选区数: {sel.Length}")
        if sel.Length == 0:
            return None

        out = []
        for i in range(sel.Length):
            rng = sel.GetElement(i)
            if rng:
                out.append(rng.GetText(-1))
        text = "".join(out)
        print(f"[{tag}] 读到 {len(text)} 字符: {text[:80]!r}")
        return text
    finally:
        comtypes.CoUninitialize()


def main():
    print("=" * 70)
    print("记事本真实选区 —— UIA 零剪贴板取词对照测试")
    print("=" * 70)

    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1.8)
    print(f"[1] notepad.exe 已启动 pid={proc.pid}")

    # 找记事本主窗口
    wins = [w for w in find_notepad_edit() if w[2] == proc.pid]
    if not wins:
        # 新版记事本可能是 UWP，pid 会变
        wins = [w for w in find_notepad_edit() if w[1] == "Notepad"]
    if not wins:
        print("[!] 未找到记事本窗口")
        proc.kill()
        return 1

    main_hwnd, main_cls, main_pid = wins[0]
    print(f"[2] 主窗口 hwnd=0x{main_hwnd:X} class={main_cls} pid={main_pid}")

    kids = find_child_edit(main_hwnd)
    print(f"[3] 子控件: {[(hex(h), c) for h, c in kids]}")

    # 新版记事本编辑区可能不在直接子级，逐个尝试写入
    edit_hwnd = None
    for h, c in kids:
        user32.SendMessageW(h, WM_SETTEXT, 0, ctypes.c_wchar_p(TEST_TEXT))
        time.sleep(0.2)
        n = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
        if n > 0:
            edit_hwnd = h
            print(f"[4] 写入成功 hwnd=0x{h:X} class={c} 长度={n}")
            break

    if not edit_hwnd:
        print("[!] 无法写入编辑区，改为前台聚焦 + 模拟输入")
        user32.SetForegroundWindow(main_hwnd)
        time.sleep(0.4)
        # 用 SendInput 打字母不可靠（中文），改为整体重试
        proc.kill()
        return 2

    # 建立真实选区（全选）
    user32.SendMessageW(edit_hwnd, EM_SETSEL, 0, -1)
    time.sleep(0.4)
    print(f"[5] 已建立选区 (EM_SETSEL 0,-1)")

    # 把编辑区设成焦点，让 UIA GetFocusedElement 指向它
    user32.SetForegroundWindow(main_hwnd)
    time.sleep(0.3)
    user32.SetFocus(edit_hwnd)
    time.sleep(0.5)

    print("[6] 通过 UIA 读取 ...")
    text = uia_read_focused("UIA")

    print("-" * 70)
    if text is None:
        print("结果：UIA 未取到文本")
        ok = False
    elif TEST_TEXT in text:
        print(f"结果：✓ 成功匹配预期文本")
        ok = True
    else:
        print(f"结果：取到文本但不匹配")
        print(f"  预期: {TEST_TEXT!r}")
        print(f"  实际: {text!r}")
        ok = False

    proc.kill()
    print("=" * 70)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
