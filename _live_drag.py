# -*- coding: utf-8 -*-
"""实机验证：真实拖选 + 抓浮层截图。

流程：
  1. 子进程启动 py_pop.py（真实的托盘常驻主程序）
  2. 建一个记事本样子的 Tk 窗口，里面放多行文字
  3. 用 SendInput 发真实的鼠标按下 -> 移动 -> 松开，制造真实拖选
  4. 轮询找出浮层窗口（Tk TopLevel，尺寸在 200~420 x 40~110）
  5. PrintWindow 把浮层客户区抓成 PNG

两个必须踩对的坑：

  (a) 事件注入必须走 SendInput，不能用 event_generate。
      py_pop 的鼠标监听挂在低级钩子（WH_MOUSE_LL）上，只有注入到
      系统输入流的事件才会被它看到；event_generate 只在 Tk 内部
      派发，钩子完全收不到。

  (b) 拖选期间 Tk 主循环必须真的在跑。
      第一版把「发鼠标事件」和「等」写成同一个函数，全程 time.sleep，
      结果 Tk 一个鼠标事件都没处理，Text 里连选区都没产生
      （TclError: text doesn't contain any characters tagged with "sel"），
      看起来像是 py_pop 不弹浮层，实际是自己没造出选区。
      现在改为：子线程负责发事件（带节流 sleep），主线程持续
      root.update() 推进 Tk 事件循环。

运行：py -3 _live_drag.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import subprocess
import sys
import threading
import time
import zlib

import tkinter as tk

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------- SendInput ----------
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def send_mouse(flags, x=0, y=0):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(x, y, 0, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def to_abs(x, y):
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return int(x * 65535 / (sw - 1)), int(y * 65535 / (sh - 1))


def drag_thread(x0, y0, x1, y1, steps=16):
    """在子线程里发真实拖选（主线程要留给 Tk 事件循环）。"""
    ax, ay = to_abs(x0, y0)
    send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)
    time.sleep(0.08)
    send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.05)
    for i in range(1, steps + 1):
        t = i / steps
        ax, ay = to_abs(int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t))
        send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)
        time.sleep(0.014)
    time.sleep(0.06)
    send_mouse(MOUSEEVENTF_LEFTUP)


def pump(root, seconds):
    """在指定时长内持续推进 Tk 事件循环。"""
    end = time.time() + seconds
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.008)


# ---------- 找浮层 ----------
def enum_tk_windows():
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == "TkTopLevel" and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append((hwnd, r.right - r.left, r.bottom - r.top,
                        r.left, r.top))
        return True

    user32.EnumWindows(cb, 0)
    return out


# ---------- PrintWindow 转 PNG ----------
def grab(hwnd, path):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 1 | 2)      # CLIENTONLY | RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        row = buf.raw[y * w * 4:(y + 1) * w * 4]
        for x in range(w):
            b, g, rr = row[x * 4], row[x * 4 + 1], row[x * 4 + 2]
            raw += bytes((rr, g, b))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return w, h


def main():
    out = "live_drag.png"

    # 清空日志，便于确认本次运行发生了什么
    logp = os.path.expandvars(r"%APPDATA%\PopDict\popdict.log")
    try:
        open(logp, "w", encoding="utf-8").close()
    except OSError:
        pass

    env = dict(os.environ)
    env["POP_DEBUG"] = "1"
    proc = subprocess.Popen([sys.executable, "-u", "py_pop.py"], env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    print(f"已启动 py_pop.py  pid={proc.pid}")
    time.sleep(3.5)

    root = tk.Tk()
    root.title("拖选测试")
    root.geometry("640x300+120+120")
    txt = tk.Text(root, font=("Microsoft YaHei", 13), wrap="word",
                  bd=0, padx=18, pady=16, bg="#ffffff", fg="#1a1a1a")
    txt.pack(fill="both", expand=True)
    txt.insert("1.0",
               "这是一段用来测试划词浮层的文字。\n"
               "用鼠标从这一行的左边拖到右边，浮层会带图标弹出来。\n")
    pump(root, 0.6)

    # 必须真的拿到前台焦点，否则注入的鼠标事件不会落到这个窗口
    hwnd_self = user32.GetAncestor(root.winfo_id(), 2)
    user32.SetForegroundWindow(hwnd_self)
    txt.focus_force()
    pump(root, 0.6)

    d = txt.dlineinfo("2.0")
    if not d:
        print("拿不到行信息")
        proc.terminate()
        return 1
    x0 = txt.winfo_rootx() + d[0] + 4
    y0 = txt.winfo_rooty() + d[1] + d[3] // 2
    x1 = x0 + min(d[2] - 12, 300)
    print(f"拖选: ({x0},{y0}) -> ({x1},{y0})")

    th = threading.Thread(target=drag_thread, args=(x0, y0, x1, y0), daemon=True)
    th.start()
    # 主线程持续喂 Tk 事件循环，直到子线程发完
    while th.is_alive():
        pump(root, 0.02)
    pump(root, 1.2)

    # 确认 Tk 里真的产生了选区 —— 没有选区就谈不上「划词」
    try:
        sel = txt.get("sel.first", "sel.last")
        print(f"Tk 选区: {sel!r}  ({len(sel)} 字)")
    except tk.TclError:
        print("Tk 选区: 无（拖选没生效，后续结果不可信）")

    print("等待浮层弹出…")
    pill = None
    for _ in range(24):
        pump(root, 0.15)
        for w in enum_tk_windows():
            hwnd, ww, hh, px, py = w
            if hwnd == hwnd_self:
                continue
            if 200 < ww < 460 and 40 < hh < 110:
                pill = w
                break
        if pill:
            break

    if pill:
        hwnd, ww, hh, px, py = pill
        print(f"浮层: hwnd={hwnd} {ww}x{hh} @({px},{py})")
        pump(root, 0.2)
        print(f"截图: {out}  {grab(hwnd, out)}")
    else:
        print("浮层未弹出")

    proc.terminate()
    pump(root, 0.3)
    try:
        root.destroy()
    except tk.TclError:
        pass

    # 把主程序日志里有信息量的行带出来
    try:
        with open(logp, encoding="utf-8", errors="replace") as f:
            body = f.read()
        hits = [l for l in body.splitlines()
                if any(k in l for k in ("[miss]", "[hit]", "[!]", "Traceback",
                                        "Error", "错误", "失败"))]
        if hits:
            print("\n=== 主程序日志（告警/命中） ===")
            print("\n".join(hits[-12:]))
    except OSError:
        pass

    return 0 if pill else 1


if __name__ == "__main__":
    sys.exit(main())
