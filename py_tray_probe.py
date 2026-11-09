"""纯 ctypes 托盘可行性验证。

目标：不引入 pystray / Pillow 的前提下，用 Shell_NotifyIconW 做出
可以右键弹菜单、菜单项能点、退出时图标能干净移除的托盘图标。

关键风险点（逐个验证）：
  1. 需要一个能收消息的窗口 —— 用 tkinter 的 Tk 窗口还是自建 Win32 窗口？
     这里自建，避免和 tkinter 主循环抢消息。
  2. 任务栏右键菜单要求窗口是前台窗口，否则点别处菜单不消失。
     老办法是 SetForegroundWindow 前置一个技巧。
  3. 图标资源：没有 .ico 文件时可以用系统图标（LoadIconW 的 IDI_APPLICATION），
     或运行时用 CreateIcon 画一个。
"""

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
shell32 = ctypes.WinDLL("shell32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)

# ---- 常量 ----
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NCCREATE = 0x0081
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_TRAYICON = 0x0400 + 20          # WM_APP + 20

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x01

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_SHARED = 0x8000
CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001
WS_OVERLAPPED = 0x00000000

ID_MENU_SETTINGS = 1001
ID_MENU_RELOAD = 1002
ID_MENU_EXIT = 1003

user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wt.HWND
user32.DestroyWindow.argtypes = [wt.HWND]
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wt.HICON
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wt.HWND, ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.GetMessageW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.c_void_p]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)

_shell_ntify = shell32.Shell_NotifyIconW
_shell_ntify.argtypes = [wt.DWORD, ctypes.c_void_p]
_shell_ntify.restype = wt.BOOL

MSG = wt.MSG

log = []


def note(s):
    print(s)
    log.append(s)


def main():
    note("=" * 66)
    note("纯 ctypes 托盘可行性验证")
    note("=" * 66)

    hinst = kernel32.GetModuleHandleW(None)
    note(f"HINSTANCE = 0x{hinst:X}")

    results = {}

    def on_menu(cmd):
        note(f"  >> 菜单被点击: id={cmd}")
        results["clicked"] = cmd

    def wndproc(hwnd, msg, wp, lp):
        if msg == WM_TRAYICON:
            event = lp & 0xFFFF
            if event in (WM_RBUTTONUP, WM_LBUTTONUP):
                note(f"  收到托盘消息 event=0x{event:04X}")
                pt = wt.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                hmenu = user32.CreatePopupMenu()
                user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_SETTINGS, "设置")
                user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_RELOAD, "重载配置")
                user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(hmenu, MF_STRING, ID_MENU_EXIT, "退出")
                # 关键技巧：先把隐藏窗口设为前台，否则点空白菜单不消失
                user32.SetForegroundWindow(hwnd)
                cmd = user32.TrackPopupMenu(
                    hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                    pt.x, pt.y, 0, hwnd, None,
                )
                user32.DestroyMenu(hmenu)
                if cmd:
                    on_menu(cmd)
                user32.PostMessageW(hwnd, 0x0000, 0, 0)   # WM_NULL
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    proc = WNDPROC(wndproc)

    wc = WNDCLASSW()
    wc.style = CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.hIcon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
    wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
    wc.hbrBackground = None
    wc.lpszMenuName = None
    wc.lpszClassName = "PopTrayProbeWnd"

    atom = user32.RegisterClassW(ctypes.byref(wc))
    note(f"RegisterClassW atom = {atom}  err={ctypes.get_last_error()}")
    results["register"] = bool(atom)

    hwnd = user32.CreateWindowExW(
        0, "PopTrayProbeWnd", "PopTray", WS_OVERLAPPED,
        0, 0, 0, 0, None, None, hinst, None,
    )
    note(f"隐藏窗口 hwnd = 0x{hwnd:X}  err={ctypes.get_last_error()}")
    results["window"] = bool(hwnd)
    if not hwnd:
        return 1

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAYICON
    nid.hIcon = wc.hIcon
    nid.szTip = "划词助手 · 探针"

    ok_add = _shell_ntify(NIM_ADD, ctypes.byref(nid))
    note(f"Shell_NotifyIcon NIM_ADD = {ok_add}  err={ctypes.get_last_error()}")
    results["add"] = bool(ok_add)

    if ok_add:
        note("\n托盘图标已添加。请在 3 秒内到右下角查看，")
        note("并尝试右键点击它 —— 菜单应弹出并可点「退出」。")
        note("（本探针窗口无可见界面，托盘图标在系统托盘区）\n")

        # 自检：弹出气泡提示，顺便验证 NIF_INFO 可用
        nid.uFlags = NIF_INFO
        nid.szInfo = "如果你看到这个气泡，说明托盘通道正常。"
        nid.szInfoTitle = "划词助手探针"
        nid.dwInfoFlags = NIIF_INFO
        _shell_ntify(NIM_MODIFY, ctypes.byref(nid))
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

        # 自己给自己发一次托盘消息，验证回调能收到并弹菜单
        # （不依赖人工点击，保证自动化可验）
        note("自动触发一次右键菜单 ...")
        user32.PostMessageW(hwnd, WM_TRAYICON, 1, WM_RBUTTONUP)

        # 消息循环
        msg = MSG()
        import time
        deadline = time.time() + 6
        while time.time() < deadline:
            # 用 PeekMessage 非阻塞轮询，便于超时退出
            r = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001)
            if r:
                if msg.message == 0x0012:   # WM_QUIT
                    note("收到 WM_QUIT")
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.02)
            if results.get("clicked") is not None:
                break

        note(f"\n回调收到: {'是' if results.get('clicked') else '否'}")
        results["callback"] = results.get("clicked") is not None

        # 清理
        ok_del = _shell_ntify(NIM_DELETE, ctypes.byref(nid))
        note(f"Shell_NotifyIcon NIM_DELETE = {ok_del}")
        results["delete"] = bool(ok_del)

    user32.DestroyWindow(hwnd)

    note("\n" + "-" * 66)
    for k in ("register", "window", "add"):
        note(f"  {k:10} {'PASS' if results.get(k) else 'FAIL'}")
    if results.get("add"):
        note(f"  {'callback':10} {'PASS' if results.get('callback') else '未验证（需人工点菜单）'}")
        note(f"  {'delete':10} {'PASS' if results.get('delete') else 'FAIL'}")
    note("-" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
