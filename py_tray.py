"""系统托盘图标：右键菜单（设置 / 重载配置 / 打开配置目录 / 退出）。

为什么自己用 ctypes 写而不装 pystray：
  实测 pystray 与 Pillow 本机都没装，为一张图标引入两个包不划算；
  shell32 的 Shell_NotifyIconW 本身足够，且不增加任何依赖。

线程模型（由 py_tray_arch.py 实测确认）：
  · TrackPopupMenu 是阻塞调用，放在 tkinter 主线程会卡住界面
    => 托盘独占一个线程，跑自己的 Win32 消息循环（GetMessage）
  · 菜单点击回调发生在托盘线程里，不能直接操作 tkinter 控件
    => 只做「往队列里放事件」，由主线程轮询执行

图标来源：
  本机没有 .ico 文件，也不想依赖磁盘资源（程序被复制走就丢图标）。
  用 CreateIconIndirect 运行时画一个：紫色圆角底 + 三条文本行，中间行高亮。
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)
shell32 = ctypes.WinDLL("shell32.dll", use_last_error=True)

# ---- 消息与常量 ----
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_TRAYICON = 0x0400 + 20          # WM_APP + 20

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING = 0x01, 0x02

MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

# LoadImage：从 .ico 文件加载图标
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]

ID_SETTINGS = 1001
ID_RELOAD = 1002
ID_OPEN_DIR = 1003
ID_ABOUT = 1004
ID_EXIT = 1099

CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001

# ---- 函数签名（不声明会导致 64 位句柄截断）----
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t,
                                  ctypes.c_ssize_t]
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
user32.DestroyWindow.restype = wt.BOOL
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t,
                                ctypes.c_ssize_t]
user32.PostMessageW.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.c_void_p]
user32.GetCursorPos.restype = wt.BOOL
user32.DestroyIcon.argtypes = [wt.HICON]
user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
user32.CreateIconIndirect.restype = wt.HICON
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wt.HANDLE

gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
    wt.HANDLE, wt.DWORD,
]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                               ctypes.c_void_p]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wt.BOOL


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             ctypes.c_size_t, ctypes.c_ssize_t)


# ---------- 图标绘制 ----------

def draw_icon(size=32):
    """紫色圆角底 + 三条文本行，中间行代表"被选中"。

    返回 BGRA 字节串，自上而下。
    """
    px = bytearray(size * size * 4)
    radius = 7.0

    def blend(x, y, r, g, b, a):
        if not (0 <= x < size and 0 <= y < size):
            return
        i = (y * size + x) * 4
        sa = a / 255.0
        da = px[i + 3] / 255.0
        oa = sa + da * (1 - sa)
        if oa <= 0:
            return
        px[i] = int((b * sa + px[i] * da * (1 - sa)) / oa)
        px[i + 1] = int((g * sa + px[i + 1] * da * (1 - sa)) / oa)
        px[i + 2] = int((r * sa + px[i + 2] * da * (1 - sa)) / oa)
        px[i + 3] = int(oa * 255)

    for y in range(size):
        for x in range(size):
            cx = min(x + 0.5, size - x - 0.5)
            cy = min(y + 0.5, size - y - 0.5)
            if cx < radius and cy < radius:
                dx, dy = radius - cx, radius - cy
                d = (dx * dx + dy * dy) ** 0.5
                cov = max(0.0, min(1.0, radius - d + 0.5))
            else:
                cov = 1.0
            if cov <= 0:
                continue
            t = y / (size - 1)
            blend(x, y,
                  int(70 + (128 - 70) * t),
                  int(108 + (82 - 108) * t),
                  int(242 + (255 - 242) * t),
                  int(255 * cov))

    rows = [
        (9.5, 22.0, 2.4, (228, 234, 252), 235),
        (14.8, 24.0, 3.2, (255, 255, 255), 255),
        (20.6, 19.0, 2.4, (228, 234, 252), 235),
    ]
    x_start = 8.0
    for ly, xr, thick, color, alpha in rows:
        for y in range(int(ly) - 2, int(ly + thick) + 2):
            cov_y = max(0.0, min(1.0, min(y + 1 - ly, ly + thick - y)))
            if cov_y <= 0:
                continue
            for x in range(int(x_start) - 2, int(xr) + 2):
                cov_x = max(0.0, min(1.0, min(x + 1 - x_start, xr - x)))
                cov = min(cov_x, cov_y)
                if cov <= 0:
                    continue
                blend(x, y, color[0], color[1], color[2], int(alpha * cov))

    return bytes(px), size, size


def load_icon_from_ico(ico_path, size=32):
    """从 .ico 文件直接取图标（交给系统挑最匹配的尺寸）。

    比自己解码 ICO 再转 HICON 更省事，且系统会按 DPI/主题自动
    选择合适档位。失败返回 None，由调用方退回手绘。
    """
    if not ico_path or not os.path.exists(ico_path):
        return None
    try:
        h = user32.LoadImageW(
            None, ico_path, IMAGE_ICON, size, size,
            LR_LOADFROMFILE | LR_DEFAULTSIZE)
        return h or None
    except Exception:
        return None


def make_hicon(bgra, w, h):
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # 负 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0

    ppv = ctypes.c_void_p()
    hbm_color = gdi32.CreateDIBSection(None, ctypes.byref(bi), 0,
                                       ctypes.byref(ppv), None, 0)
    if not hbm_color:
        return None
    ctypes.memmove(ppv, bgra, len(bgra))

    hbm_mask = gdi32.CreateBitmap(w, h, 1, 1, None)
    if not hbm_mask:
        gdi32.DeleteObject(hbm_color)
        return None

    ii = ICONINFO()
    ii.fIcon = True
    ii.hbmMask = hbm_mask
    ii.hbmColor = hbm_color
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.DeleteObject(hbm_color)
    gdi32.DeleteObject(hbm_mask)
    return hicon


# ---------- 托盘 ----------

class Tray:
    """系统托盘。事件通过 on_event 回调交回调用方。

    on_event(name) 里 name 为 'settings' / 'reload' / 'open_dir' /
    'about' / 'exit'。回调发生在托盘线程，调用方需自行转到主线程。
    """

    def __init__(self, on_event, tip="划词助手", class_name=None):
        self.on_event = on_event
        self.tip = tip
        self.class_name = class_name or f"PopTrayWnd_{id(self):X}"
        self.thread = None
        self.hwnd = None
        self.hicon = None
        self._proc = None
        self._ready = threading.Event()
        self._nid = None
        self.ok = False
        self.error = None

    def start(self, wait=True, timeout=3.0):
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="PopTray")
        self.thread.start()
        if wait:
            self._ready.wait(timeout)
        return self.ok

    def _run(self):
        try:
            self._build_window()
            self._add_icon()
            self._ready.set()
            if self.ok:
                self._message_loop()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self._ready.set()
        finally:
            self._cleanup()

    def _build_window(self):
        hinst = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, wp, lp):
            if msg == WM_TRAYICON:
                self._on_tray((lp & 0xFFFF))
                return 0
            if msg == WM_COMMAND:
                self._emit(int(wp & 0xFFFF))
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(wndproc)

        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = self.class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:            # ERROR_CLASS_ALREADY_EXISTS 可接受
                raise OSError(f"RegisterClassW 失败 err={err}")

        self.hwnd = user32.CreateWindowExW(
            0, self.class_name, "PopTray", 0,
            0, 0, 0, 0, None, None, hinst, None,
        )
        if not self.hwnd:
            raise OSError(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")

    def _add_icon(self):
        # 优先用打包好的 popdict.ico（多尺寸，系统按 DPI 自动选档）
        here = os.path.dirname(os.path.abspath(__file__))
        ico = os.path.join(here, "popdict.ico")
        self.hicon = load_icon_from_ico(ico, 32)
        if not self.hicon:
            # 退路一：运行时手绘的紫色图标
            bgra, w, h = draw_icon(32)
            self.hicon = make_hicon(bgra, w, h)
        if not self.hicon:
            # 退路二：系统默认图标，至少不崩
            self.hicon = user32.LoadIconW(None, ctypes.c_void_p(32512))

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tip
        self._nid = nid

        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self.error = f"Shell_NotifyIcon NIM_ADD 失败 err={ctypes.get_last_error()}"
            return
        self.ok = True

    def _on_tray(self, event):
        if event in (WM_RBUTTONUP, WM_LBUTTONUP):
            self._popup_menu()
        elif event == WM_LBUTTONDBLCLK:
            self._emit(ID_SETTINGS)

    def _popup_menu(self):
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            user32.AppendMenuW(hmenu, MF_STRING, ID_SETTINGS, "设置")
            user32.AppendMenuW(hmenu, MF_STRING, ID_RELOAD, "重新载入配置")
            user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN_DIR, "打开配置目录")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, ID_ABOUT, "关于")
            user32.AppendMenuW(hmenu, MF_STRING, ID_EXIT, "退出")

            # 关键：先置前台，否则点菜单外面菜单不消失（Win32 已知问题）
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x, pt.y, 0, self.hwnd, None,
            )
            # 必须补一个 WM_NULL，否则下一次右键偶尔不出菜单
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd:
                self._emit(cmd)
        finally:
            user32.DestroyMenu(hmenu)

    def _emit(self, cmd):
        name = {
            ID_SETTINGS: "settings",
            ID_RELOAD: "reload",
            ID_OPEN_DIR: "open_dir",
            ID_ABOUT: "about",
            ID_EXIT: "exit",
        }.get(cmd)
        if not name or not self.on_event:
            return
        try:
            self.on_event(name)
        except Exception as e:
            print(f"[tray] 回调出错: {type(e).__name__}: {e}", file=sys.stderr)

    def _message_loop(self):
        msg = wt.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:                 # 0 = WM_QUIT，-1 = 错误
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _cleanup(self):
        if self._nid is not None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None
        if self.hicon:
            try:
                user32.DestroyIcon(self.hicon)
            except Exception:
                pass
            self.hicon = None
        self.ok = False

    def notify(self, title, text, warning=False):
        """弹气泡提示（配置错误之类）。"""
        if not self.ok or self._nid is None:
            return False
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_INFO
        nid.szInfo = (text or "")[:255]
        nid.szInfoTitle = (title or "")[:63]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        return bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))

    def stop(self):
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=3)
        self.hwnd = None


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    events = []

    def on_event(name):
        print(f"[事件] {name}")
        events.append(name)
        if name == "exit":
            tray.stop()

    print("托盘图标将在 3 秒后自动退出。请在此期间右键查看菜单。")
    tray = Tray(on_event, tip="划词助手（自检）")
    if not tray.start():
        print(f"启动失败: {tray.error}")
        sys.exit(1)
    print(f"托盘已启动 hwnd=0x{tray.hwnd:X}  图标={'自绘' if tray.hicon else '系统'}")
    tray.notify("划词助手", "托盘自检：如果你看到这个气泡，通道正常。")

    t0 = time.time()
    while time.time() - t0 < 3:
        time.sleep(0.1)
    tray.stop()
    print("已退出，图标应已消失。")
    sys.exit(0)
