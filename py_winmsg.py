"""Win32 消息级取词验证：GetGUIThreadInfo + EM_GETSEL + WM_GETTEXT。

思路（完全不碰剪贴板，也不依赖 UIA TextPattern）：
  1. GetForegroundWindow() 拿前台窗口
  2. GetWindowThreadProcessId() 拿该窗口所属线程
  3. GetGUIThreadInfo() 从 GUITHREADINFO.hwndFocus 拿到真正持有焦点的控件句柄
     —— 这是跨进程获取焦点的标准方法
  4. SendMessage(EM_GETSEL=0x00B0) 返回打包的 (start 在 LOWORD, end 在 HIWORD)
     —— 对标准 EDIT / RichEdit 有效，且返回值直接可用，不需要跨进程指针
  5. WM_GETTEXT / WM_GETTEXTLENGTH 取全文（系统对标准控件做跨进程封送）
  6. 用 start/end 切出选中片段

这条路能覆盖：记事本、旧版编辑框、各类 Win32 EDIT/RichEdit 控件。
对 Chromium / Word 无效（它们不是标准 EDIT 类），属于方案的适用边界。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EM_GETSEL = 0x00B0
EM_SETSEL = 0x00B1


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


def get_focus_info():
    """返回 (fg_hwnd, tid, focus_hwnd)。"""
    fg = user32.GetForegroundWindow()
    if not fg:
        return None, None, None
    tid = user32.GetWindowThreadProcessId(fg, None)
    if not tid:
        return fg, None, None

    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
        return fg, tid, None
    return fg, tid, gti.hwndFocus


def class_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def read_selection_by_message(hwnd):
    """用 EM_GETSEL + WM_GETTEXT 读取选中文字。返回 (text, detail)。"""
    cls = class_of(hwnd)
    detail = {"class": cls, "hwnd": hwnd}

    # 1) 取选区位置。EM_GETSEL 的返回值就是打包的 start/end
    #    注意：对 RichEdit 也支持该消息
    res = user32.SendMessageW(hwnd, EM_GETSEL, 0, 0)
    start = res & 0xFFFF
    end = (res >> 16) & 0xFFFF
    detail["sel_start"] = start
    detail["sel_end"] = end

    if start == end:
        detail["reason"] = "start == end，无选区"
        return None, detail

    # 2) 取全文长度
    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    detail["full_len"] = n
    if n <= 0:
        detail["reason"] = "EM_GETSEL 返回了范围，但取不到全文"
        return None, detail

    # 3) 取全文
    buf = ctypes.create_unicode_buffer(n + 2)
    got = user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, ctypes.byref(buf))
    detail["got_len"] = got
    full = buf.value

    if end > len(full):
        detail["reason"] = f"选区 end={end} 超出全文长度 {len(full)}"
        return None, detail

    text = full[start:end]
    detail["reason"] = "ok"
    detail["full_preview"] = full[:80]
    return text, detail


def find_window_by_title(title):
    hits = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if b.value == title:
                hits.append(h)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return hits[0] if hits else None


def main():
    print("=" * 76)
    print("Win32 消息级取词验证（EM_GETSEL + WM_GETTEXT，零剪贴板）")
    print("=" * 76)

    ps = subprocess.Popen(
        [sys.executable, "py_edit_target.py", "40"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    hwnd = None
    for _ in range(40):
        time.sleep(0.25)
        hwnd = find_window_by_title("EditTarget")
        if hwnd:
            break
    if not hwnd:
        print("[!] 靶子未出现")
        out = ps.stdout.read() if ps.stdout else ""
        print(f"    靶子输出: {out[:400]}")
        ps.kill()
        return 1
    print(f"[1] 靶子窗口 hwnd=0x{hwnd:X}")

    time.sleep(1.5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    fg, tid, focus = get_focus_info()
    print(f"[2] 前台 hwnd=0x{fg:X}  tid={tid}")
    if focus:
        print(f"[3] 焦点控件 hwnd=0x{focus:X} class={class_of(focus)!r}")
    else:
        print("[3] GetGUIThreadInfo 未返回 hwndFocus")
        ps.kill()
        return 2

    print("[4] 消息级读取 ...")
    text, detail = read_selection_by_message(focus)
    for k in ("class", "sel_start", "sel_end", "full_len", "got_len", "reason"):
        if k in detail:
            print(f"    {k}: {detail[k]}")
    if "full_preview" in detail:
        print(f"    full_preview: {detail['full_preview']!r}")

    print("-" * 76)
    ok = False
    if text:
        print(f"✓ 读到 {len(text)} 字符")
        # 终端是 GBK 输出，中文会乱码，用 repr 的转义序列 + 长度判定更可靠
        print(f"  repr: {text.encode('unicode_escape').decode('ascii')[:160]}")
        print(f"  原文: {text}")
        ok = len(text) > 0
    else:
        print("✗ 未读到选区")

    print("=" * 76)
    ps.kill()
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
