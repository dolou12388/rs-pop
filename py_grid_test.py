"""探究：Chromium 应用内取词成功率与点击位置的关系。

前一轮发现矛盾结果：
  · ChatGPT 不点击 -> 成功；点击内容区中心 -> 失败
  · 飞书    不点击 -> 失败；点击内容区中心 -> 成功

假设：Ctrl+C 是否生效取决于窗口内**当前焦点落在哪个区域**，
      而不同区域（消息列表 / 输入框 / 侧边栏 / 空白区）对 Ctrl+C 的响应不同。
      例如焦点在「输入框」时 Ctrl+C 复制输入框内容（或什么都不复制），
      焦点在「消息列表」时才会进入文本选择模式。

本轮做法：在窗口内取多个采样点（9 宫格），每个点点击后试 Ctrl+A / Ctrl+C，
记录是否取到文本以及取到多少字符。这能看出规律性。

安全性：每次操作前备份剪贴板，操作后还原；结束时发送 Esc 取消选择。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
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
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

VK_CONTROL = 0x11
VK_C = 0x43
VK_A = 0x41
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002

MARKER = "GRID_TEST_\u57fa\u7ebf_2026"


def class_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def send_ctrl(vk):
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def send_key(vk):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


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


def grid_points(h):
    """窗口内 9 宫格采样点。"""
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    w = r.right - r.left
    hh = r.bottom - r.top
    pts = []
    for ry, lab_y in ((0.25, "上"), (0.50, "中"), (0.80, "下")):
        for rx, lab_x in ((0.25, "左"), (0.50, "中"), (0.78, "右")):
            pts.append((f"{lab_y}{lab_x}", int(r.left + w * rx), int(r.top + hh * ry)))
    return pts


def probe_point(h, name, x, y):
    """点击某点后尝试 Ctrl+A / Ctrl+C，返回 (成功, 字符数, 序列号变化)。"""
    cg.write_text(MARKER)
    time.sleep(0.08)
    seq0 = cg.clipboard_seq()

    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.35)

    guard = cg.ClipGuard()
    guard.__enter__()
    ok = False
    n = 0
    delta = 0
    try:
        send_ctrl(VK_A)
        time.sleep(0.45)
        send_ctrl(VK_C)
        time.sleep(0.4)
        delta = cg.clipboard_seq() - seq0
        text = cg.read_text()
        if text and text != MARKER:
            ok = True
            n = len(text)
    finally:
        guard.__exit__(None, None, None)

    send_key(VK_ESCAPE)
    time.sleep(0.12)
    return ok, n, delta


def run(nm, label):
    print("\n" + "=" * 74)
    print(f"[{label}] {nm}  9 宫格点击采样")
    print("=" * 74)

    wins = find(nm)
    if not wins:
        print("  未找到窗口")
        return []
    h = wins[0]

    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)
        time.sleep(0.4)
    user32.SetForegroundWindow(h)
    time.sleep(0.8)

    pts = grid_points(h)
    rows = []
    print(f"  {'位置':<8}{'结果':<8}{'字符数':<8}{'序列号变化':<10}")
    for name, x, y in pts:
        ok, n, d = probe_point(h, name, x, y)
        rows.append((name, ok, n, d))
        print(f"  {name:<8}{'成功' if ok else '失败':<8}{n:<8}{d:<10}")
        time.sleep(0.2)

    succ = sum(1 for _, ok, _, _ in rows if ok)
    print(f"  -> 成功 {succ}/{len(rows)}")
    return rows


def main():
    print("=" * 74)
    print("Chromium 应用：取词成功率与点击位置的关系")
    print("=" * 74)
    print("说明：会在窗口内做 9 次点击采样，每次约 1.5 秒。")
    print()

    all_rows = {}
    for nm, label in (("Feishu.exe", "飞书"),
                      ("ChatGPT.exe", "ChatGPT")):
        all_rows[label] = run(nm, label)

    print("\n" + "=" * 74)
    print("规律汇总")
    print("=" * 74)
    for label, rows in all_rows.items():
        if not rows:
            continue
        succ = [r for r in rows if r[1]]
        avg = sum(r[2] for r in succ) / len(succ) if succ else 0
        print(f"\n{label}: 成功 {len(succ)}/{len(rows)}，成功时平均 {avg:.0f} 字符")
        print("  成功位置:", [r[0] for r in succ] or "无")
        print("  失败位置:", [r[0] for r in rows if not r[1]] or "无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
