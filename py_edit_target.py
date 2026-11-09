"""靶子进程：用 ctypes 直接创建一个真正的 Win32 EDIT 控件窗口。

为什么不用 PowerShell + WinForms：
  PowerShell 5.1 读取无 BOM 的 UTF-8 脚本时会按 ANSI(GBK) 解码，
  中文测试文本在进入控件前就已损坏，导致"取到的文本乱码"这一
  假失败掩盖真实机制。Python 原生 Unicode，无此问题。

这个靶子等价于记事本 / 各类 Win32 编辑框，是本方案最主要的真实目标。

用法：python py_edit_target.py [存活秒数]
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll")
gdi32 = ctypes.WinDLL("gdi32.dll")

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040
CW_USEDEFAULT = -2147483648
WM_DESTROY = 0x0002
WM_SETFONT = 0x0030
EM_SETSEL = 0x00B1
DEFAULT_GUI_FONT = 17

TEST_TEXT = "量子纠缠是量子力学中最引人注目的现象之一"

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_void_p, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)

# 显式声明签名，避免 LPARAM 负数溢出
user32.DefWindowProcW.argtypes = [
    wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


edit_hwnd = None


def wndproc(hwnd, msg, wp, lp):
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


def main():
    lifetime = int(sys.argv[1]) if len(sys.argv) > 1 else 40

    hinst = kernel32.GetModuleHandleW(None)
    proc = WNDPROC(wndproc)

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = proc
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = hinst
    wc.hIcon = None
    wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
    wc.hbrBackground = ctypes.c_void_p(16)        # COLOR_WINDOW+1
    wc.lpszMenuName = None
    wc.lpszClassName = "EditTargetWnd"

    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        raise OSError(f"RegisterClassW failed {ctypes.get_last_error()}")

    hwnd = user32.CreateWindowExW(
        0, "EditTargetWnd", "EditTarget",
        WS_OVERLAPPEDWINDOW, 300, 200, 700, 320,
        None, None, hinst, None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW failed {ctypes.get_last_error()}")

    global edit_hwnd
    edit_hwnd = user32.CreateWindowExW(
        0, "EDIT", TEST_TEXT,
        WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL,
        10, 10, 660, 260,
        hwnd, None, hinst, None,
    )
    if not edit_hwnd:
        raise OSError(f"create EDIT failed {ctypes.get_last_error()}")

    # 设置字体，让中文正常显示
    hfont = gdi32.GetStockObject(DEFAULT_GUI_FONT)
    user32.SendMessageW(edit_hwnd, WM_SETFONT, hfont, 1)

    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    user32.UpdateWindow(hwnd)

    # 建立真实选区：全选
    user32.SendMessageW(edit_hwnd, EM_SETSEL, 0, -1)
    # 让 EDIT 持有键盘焦点
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(edit_hwnd)

    print(f"TARGET_READY hwnd=0x{hwnd:X} edit=0x{edit_hwnd:X} "
          f"textLen={len(TEST_TEXT)}", flush=True)

    # 消息循环 + 到点退出
    end_at = time.time() + lifetime
    msg = wt.MSG()
    while time.time() < end_at:
        r = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)  # PM_REMOVE
        if r:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)

    user32.DestroyWindow(hwnd)
    print("TARGET_EXIT", flush=True)


if __name__ == "__main__":
    main()
