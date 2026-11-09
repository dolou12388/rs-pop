"""托盘架构关键验证：图标绘制 + 跨线程共存。

两个必须确认的点：

A. 图标怎么来
   本机没有 Pillow，也不想为一张图标引入依赖。
   方案：CreateDIBSection 手工填像素 -> CreateIconIndirect 造 HICON。
   顺便导出 BMP 以便肉眼检查画得对不对。

B. 托盘能不能放在独立线程
   TrackPopupMenu 是阻塞调用（探针已证实），放进 tkinter 主线程会卡住界面。
   所以托盘必须在自己的线程里跑自己的消息循环。
   本探针验证：主线程跑 tkinter，托盘线程跑 Win32 消息循环，两者互不干扰。
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import threading
import time

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32.dll", use_last_error=True)
shell32 = ctypes.WinDLL("shell32.dll", use_last_error=True)

NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
WM_TRAYICON = 0x0400 + 20
WM_DESTROY, WM_CLOSE = 0x0002, 0x0010

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
user32.GetMessageW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DestroyIcon.argtypes = [wt.HICON]

gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
    wt.HANDLE, wt.DWORD,
]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
user32.CreateIconIndirect.restype = wt.HICON
# CreateBitmap 在 gdi32.dll，不在 user32
gdi32.CreateBitmap.argtypes = [
    ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p,
]
gdi32.CreateBitmap.restype = wt.HBITMAP


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
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
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


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t
)


# ---------------- 图标绘制 ----------------

def draw_icon(size=32):
    """画一个「划词」意象图标：紫色圆角底 + 三条文本行，中间一条高亮。

    返回 (bgra_bytes, width, height)，BGRA 顺序、自上而下。
    """
    px = bytearray(size * size * 4)
    radius = 7.0

    def blend(x, y, r, g, b, a):
        """alpha 混合写入一个像素。"""
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

    # 1) 底：竖直渐变 + 圆角 + 边缘抗锯齿
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
            r = int(70 + (128 - 70) * t)
            g = int(108 + (82 - 108) * t)
            b = int(242 + (255 - 242) * t)
            blend(x, y, r, g, b, int(255 * cov))

    # 2) 三条文本行。中间那条代表"被选中"，用纯白且更粗。
    #    (y 起点, 右端 x 终点, 粗细, 颜色, alpha)
    rows = [
        (9.5, 22.0, 2.4, (228, 234, 252), 235),
        (14.8, 24.0, 3.2, (255, 255, 255), 255),
        (20.6, 19.0, 2.4, (228, 234, 252), 235),
    ]
    x_start = 8.0
    for ly, xr, thick, color, alpha in rows:
        for y in range(int(ly) - 2, int(ly + thick) + 2):
            # 纵向覆盖度
            cov_y = max(0.0, min(1.0, min(y + 1 - ly, ly + thick - y)))
            if cov_y <= 0:
                continue
            for x in range(int(x_start) - 2, int(xr) + 2):
                # 纵向覆盖度
                cov_x = max(0.0, min(1.0, min(x + 1 - x_start, xr - x)))
                cov = min(cov_x, cov_y)
                if cov <= 0:
                    continue
                blend(x, y, color[0], color[1], color[2], int(alpha * cov))

    return bytes(px), size, size


def save_bmp(bgra, w, h, path):
    """导出 BMP 以便肉眼检查（32bpp，自下而上）。"""
    row = w * 4
    flipped = b"".join(bgra[y * row:(y + 1) * row] for y in range(h - 1, -1, -1))
    fh = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(flipped), 0, 0, 54)
    ih = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(flipped),
                     2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(fh + ih + flipped)


def make_hicon(bgra, w, h):
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h        # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0    # BI_RGB

    ppv = ctypes.c_void_p()
    hbm_color = gdi32.CreateDIBSection(None, ctypes.byref(bi), 0,
                                       ctypes.byref(ppv), None, 0)
    if not hbm_color:
        return None, "CreateDIBSection 失败"
    ctypes.memmove(ppv, bgra, len(bgra))

    # 掩码全 0：让系统使用 alpha 通道
    hbm_mask = gdi32.CreateBitmap(w, h, 1, 1, None)
    if not hbm_mask:
        gdi32.DeleteObject(hbm_color)
        return None, "CreateBitmap 失败"

    ii = ICONINFO()
    ii.fIcon = True
    ii.hbmMask = hbm_mask
    ii.hbmColor = hbm_color
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))

    gdi32.DeleteObject(hbm_color)
    gdi32.DeleteObject(hbm_mask)
    if not hicon:
        return None, f"CreateIconIndirect 失败 err={ctypes.get_last_error()}"
    return hicon, "ok"


# ---------------- 托盘线程 ----------------

class TrayThread(threading.Thread):
    def __init__(self, hicon, tip="划词助手"):
        super().__init__(daemon=True)
        self.hicon = hicon
        self.tip = tip
        self.hwnd = None
        self.ready = threading.Event()
        self.added = False
        self.deleted = False
        self.error = None
        self._proc_ref = None

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.ready.set()

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, wp, lp):
            if msg == WM_TRAYICON:
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc_ref = WNDPROC(wndproc)

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._proc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = "PopTrayThreadWnd"
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:      # ERROR_CLASS_ALREADY_EXISTS
                self.error = f"RegisterClassW err={err}"
                self.ready.set()
                return

        self.hwnd = user32.CreateWindowExW(
            0, "PopTrayThreadWnd", "PopTray", 0,
            0, 0, 0, 0, None, None, hinst, None,
        )
        if not self.hwnd:
            self.error = f"CreateWindowExW err={ctypes.get_last_error()}"
            self.ready.set()
            return

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tip
        self.nid = nid

        fn = shell32.Shell_NotifyIconW
        fn.argtypes = [wt.DWORD, ctypes.c_void_p]
        fn.restype = wt.BOOL
        self.added = bool(fn(NIM_ADD, ctypes.byref(nid)))
        self.ready.set()

        # 本线程自己的消息循环 —— 与 tkinter 完全隔离
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self.deleted = bool(fn(NIM_DELETE, ctypes.byref(self.nid)))

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self.join(timeout=3)


def main():
    print("=" * 70)
    print("托盘架构验证：图标绘制 + 跨线程共存")
    print("=" * 70)

    fails = 0

    # ---- A. 画图标 ----
    print("\nA. 运行时绘制的图标")
    bgra, w, h = draw_icon(32)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_preview.bmp")
    save_bmp(bgra, w, h, path)
    print(f"  像素数据 {len(bgra)} 字节  预览已导出: {os.path.basename(path)}")

    hicon, msg = make_hicon(bgra, w, h)
    if hicon:
        print(f"  CreateIconIndirect 成功 HICON=0x{hicon:X}")
        ok_icon = True
        print("  PASS 图标资源可运行时生成，无需 .ico 文件")
    else:
        print(f"  FAIL {msg}")
        ok_icon = False
        fails += 1

    # ---- B. 跨线程 ----
    print("\nB. 托盘线程与 tkinter 主线程共存")
    tk_ticks = [0]
    tk_ready = threading.Event()

    def tk_main():
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()

            def tick():
                tk_ticks[0] += 1
                root.after(50, tick)

            root.after(50, tick)
            tk_ready.set()
            root.after(3000, root.quit)
            root.mainloop()
            root.destroy()
        except Exception as e:
            print(f"  tkinter 异常: {e}")
            tk_ready.set()

    th = threading.Thread(target=tk_main, daemon=True)
    th.start()
    tk_ready.wait(3)

    tray = TrayThread(hicon, "划词助手 · 架构探针")
    tray.start()
    tray.ready.wait(3)

    if tray.error:
        print(f"  FAIL 托盘线程错误: {tray.error}")
        fails += 1
    elif not tray.added:
        print("  FAIL 托盘图标添加失败")
        fails += 1
    else:
        print(f"  托盘线程已就绪 hwnd=0x{tray.hwnd:X} 图标=已添加")
        print("  观察右下角 3 秒 ...")
        t0 = tk_ticks[0]
        time.sleep(1.5)
        mid = tk_ticks[0]
        responsive = mid > t0
        print(f"  tkinter 计数: {t0} -> {mid}  （{'仍在运行' if responsive else '被卡住'}）")
        if responsive:
            print("  PASS 托盘线程未阻塞 tkinter 主循环")
        else:
            print("  FAIL tkinter 被托盘阻塞")
            fails += 1

    th.join(timeout=5)
    print(f"  tkinter 总计数 {tk_ticks[0]} 次（约 3 秒，预期 ≈55-60）")

    tray.stop()
    if tray.deleted:
        print("  PASS 退出时托盘图标已移除")
    else:
        print("  WARN 托盘图标移除未确认")

    if hicon:
        user32.DestroyIcon(hicon)

    print("\n" + "-" * 70)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
