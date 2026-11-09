"""验证：用真实鼠标点击建立焦点后，ChatGPT / 飞书 能否正常复制。

前一次诊断显示：SetForegroundWindow 成功但 Ctrl+A / Ctrl+C 无效果
（剪贴板序列号变化 = 0）。怀疑焦点只落在顶层容器，未进入渲染内容。

本轮改用真实鼠标点击窗口中心区域（模拟用户操作）建立焦点，
再重试 Ctrl+A / Ctrl+C，看序列号是否变化。

对照：Edge 已知可用，作为阳性对照验证测试方法本身正确。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.c_void_p]
user32.GetWindowRect.restype = wt.BOOL
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.GetForegroundWindow.restype = wt.HWND
user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.c_void_p]
user32.GetGUIThreadInfo.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

kernel32.GlobalLock.argtypes = [wt.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HANDLE]

CF_UNICODETEXT = 13
VK_CONTROL = 0x11
VK_C = 0x43
VK_A = 0x41
KEYEVENTF_KEYUP = 0x0002

MARKER = "FOCUS_TEST_\u57fa\u7ebf_2026"


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                ("rcCaret", wt.RECT)]


def class_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def focus_state():
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    user32.GetGUIThreadInfo(tid, ctypes.byref(gti))
    return fg, gti.hwndFocus, gti.hwndActive


def send_ctrl(vk):
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def click_center(h, offset_y=0):
    """在窗口内容区中央做一次真实鼠标点击。"""
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    cx = (r.left + r.right) // 2
    cy = (r.top + r.bottom) // 2 + offset_y
    user32.SetCursorPos(cx, cy)
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    time.sleep(0.5)
    return cx, cy


def pid2name():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, encoding="gbk",
                             errors="replace", timeout=20).stdout
    except Exception:
        return {}
    m = {}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            try:
                m[int(parts[1])] = parts[0]
            except ValueError:
                pass
    return m


def find(nm_want):
    p2n = pid2name()
    hits = []
    E = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        if user32.GetWindowTextLengthW(h) == 0:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if p2n.get(pid.value) == nm_want:
            hits.append(h)
        return True

    user32.EnumWindows(E(cb), 0)
    return hits


def test_one(nm, label, click=True):
    print("\n" + "=" * 74)
    print(f"[{label}] {nm}  方式={'鼠标点击建立焦点' if click else '仅 SetForegroundWindow'}")
    print("=" * 74)

    wins = find(nm)
    if not wins:
        print("  未找到窗口")
        return None
    h = wins[0]

    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)
        time.sleep(0.4)
    user32.SetForegroundWindow(h)
    time.sleep(0.7)

    cg.write_text(MARKER)
    time.sleep(0.12)
    base = cg.read_text()
    seq0 = cg.clipboard_seq()

    guard = cg.ClipGuard()
    guard.__enter__()
    try:
        if click:
            cx, cy = click_center(h)
            print(f"  已点击内容区 ({cx},{cy})")
            fg, foc, act = focus_state()
            print(f"  焦点: fg=0x{fg:X}({class_of(fg)}) focus=0x{foc:X}({class_of(foc)})")

        send_ctrl(VK_A)
        time.sleep(0.65)
        seq_a = cg.clipboard_seq()

        send_ctrl(VK_C)
        time.sleep(0.5)
        seq_c = cg.clipboard_seq()

        text = cg.read_text()
        print(f"  序列号: 基线={seq0}  Ctrl+A后={seq_a}  Ctrl+C后={seq_c}  变化={seq_c - seq_a}")
        if text and text != MARKER:
            print(f"  取到 {len(text)} 字符: {text[:80]!r}")
        elif text == MARKER:
            print("  剪贴板仍是基线 —— Ctrl+C 未生效")
        else:
            print(f"  读不到文本: {text!r}")
        return seq_c != seq_a and text and text != MARKER
    finally:
        guard.__exit__(None, None, None)
        print(f"  还原: {guard.restore_result}")
        time.sleep(0.1)
        print(f"  最终剪贴板: {cg.read_text()!r}")


def main():
    print("=" * 74)
    print("焦点建立方式对 Ctrl+C 有效性的影响")
    print("=" * 74)

    results = {}
    # 阳性对照：Edge 已知可用（不点击也能成功，说明方法本身没问题）
    results["Edge（对照，不点击）"] = test_one("msedge.exe", "Edge", click=False)
    # 实验组：ChatGPT / 飞书，分别测不点击与点击
    results["ChatGPT（不点击）"] = test_one("ChatGPT.exe", "ChatGPT", click=False)
    results["ChatGPT（点击后）"] = test_one("ChatGPT.exe", "ChatGPT", click=True)
    results["飞书（不点击）"] = test_one("Feishu.exe", "飞书", click=False)
    results["飞书（点击后）"] = test_one("Feishu.exe", "飞书", click=True)

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    for k, v in results.items():
        print(f"  {k:<26} {'成功' if v else ('失败' if v is False else '跳过')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
