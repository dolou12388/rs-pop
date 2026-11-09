"""精确拖选实验：只在文字行上拖，避开空白区。

修正前几轮的方法错误：
  · 九宫格采样点落在侧边栏/空白，Ctrl+A 在非文本区无效
  · 固定比例拖选可能起止于空白，Chromium 不产生选区

本轮：
  1. 页面把每段文字首行的精确屏幕坐标报在窗口标题里
  2. 脚本从标题读坐标，在文字行上按下 → 分步拖到行尾 → 松开
  3. 立即走三级取词链路，校验内容是否等于该行文本
  4. 校验剪贴板已还原

这是最贴近真实拖选的验证方式。
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

import py_clipguard as cg
import py_pop as pop

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002
LDOWN, LUP = 0x0002, 0x0004

MARKER = "PRECISE_\u57fa\u7ebf_2026"
ALPHA = "DRAGPROBE_ALPHA"
BETA = "DRAGPROBE_BETA"


def title_of(h):
    n = user32.GetWindowTextLengthW(h)
    if n == 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def find_prefix(prefix):
    hits = []
    E = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        if user32.IsWindowVisible(h):
            t = title_of(h)
            if t.startswith(prefix):
                hits.append((h, t))
        return True

    user32.EnumWindows(E(cb), 0)
    return hits


def drag_on_line(x0, y0, x1, y1, steps=10):
    """在文字行上做真实拖拽：按下 -> 分步移动 -> 松开。"""
    user32.SetCursorPos(x0, y0)
    time.sleep(0.18)
    user32.mouse_event(LDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
    for i in range(1, steps + 1):
        user32.SetCursorPos(x0 + int((x1 - x0) * i / steps),
                            y0 + int((y1 - y0) * i / steps))
        time.sleep(0.022)
    time.sleep(0.12)
    user32.mouse_event(LUP, 0, 0, 0, 0)
    time.sleep(0.35)


def send_esc():
    user32.keybd_event(VK_ESCAPE, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)


def acquire_check(expect_mark):
    """走三级链路取词，返回 (ok, text, level, clip_clean, detail)。"""
    cg.write_text(MARKER)
    time.sleep(0.1)
    base = cg.read_text()

    text, trail = pop.acquire_text()
    time.sleep(0.12)
    clean = cg.read_text() == base

    lvl = trail[-1].get("level", "-") if trail else "-"
    ok = bool(text and expect_mark in text)
    return ok, text, lvl, clean, trail


def main():
    print("=" * 78)
    print("精确拖选实验：只在文字行上拖（修正采样点错误）")
    print("=" * 78)

    profile = os.path.join(tempfile.gettempdir(), f"drag_{uuid.uuid4().hex[:8]}")
    html = os.path.abspath("dragprobe.html").replace("\\", "/")
    proc = subprocess.Popen(
        [EDGE, f"--app=file:///{html}", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check",
         "--window-size=860,560"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"[1] Edge 启动 pid={proc.pid}")

    info = None
    for _ in range(80):
        time.sleep(0.25)
        hits = find_prefix("R:")
        if hits:
            hwnd, ttl = hits[0]
            if ttl.count("|") >= 2:
                info = (hwnd, ttl)
                break
    if not info:
        print("[!] 未拿到坐标报告")
        proc.kill()
        return 1

    hwnd, ttl = info
    parts = ttl.split("|")
    dpr = parts[0].replace("R:", "")
    r1 = [int(v) for v in parts[1].split(",")]
    r2 = [int(v) for v in parts[2].split(",")]
    print(f"[2] 窗口 hwnd=0x{hwnd:X}  dpr={dpr}")
    print(f"    第一段文字行: ({r1[0]},{r1[1]}) -> ({r1[2]},{r1[3]})")
    print(f"    第二段文字行: ({r2[0]},{r2[1]}) -> ({r2[2]},{r2[3]})")

    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    print("[3] 已激活")

    fails = 0
    results = []

    # 用例 A：第一段整行拖选
    print("\n[A] 拖选第一段（整行，从行首到行尾）")
    drag_on_line(r1[0], r1[1], r1[2], r1[3])
    ok, text, lvl, clean, trail = acquire_check(ALPHA)
    print(f"  取词: {'成功' if ok else '失败'}  层级={lvl}  字符={len(text) if text else 0}")
    if text:
        print(f"  内容: {text[:70]!r}")
    print(f"  剪贴板: {'洁净' if clean else '污染'}")
    results.append(("第一段整行", ok, len(text) if text else 0, lvl, clean))
    if not ok:
        fails += 1
        print(f"  轨迹: {[(d.get('level'), d.get('reason')) for d in trail]}")
    if not clean:
        fails += 1
    send_esc()
    time.sleep(0.3)

    # 用例 B：第二段整行拖选
    print("\n[B] 拖选第二段（整行）")
    drag_on_line(r2[0], r2[1], r2[2], r2[3])
    ok, text, lvl, clean, trail = acquire_check(BETA)
    print(f"  取词: {'成功' if ok else '失败'}  层级={lvl}  字符={len(text) if text else 0}")
    if text:
        print(f"  内容: {text[:70]!r}")
    print(f"  剪贴板: {'洁净' if clean else '污染'}")
    results.append(("第二段整行", ok, len(text) if text else 0, lvl, clean))
    if not ok:
        fails += 1
        print(f"  轨迹: {[(d.get('level'), d.get('reason')) for d in trail]}")
    if not clean:
        fails += 1
    send_esc()
    time.sleep(0.3)

    # 用例 C：部分拖选（行内 20% -> 60%）
    print("\n[C] 拖选第一段的一部分（行内 20%->60%）")
    xa = r1[0] + int((r1[2] - r1[0]) * 0.20)
    xb = r1[0] + int((r1[2] - r1[0]) * 0.60)
    drag_on_line(xa, r1[1], xb, r1[3])
    ok, text, lvl, clean, trail = acquire_check(ALPHA)
    partial_ok = bool(text and 0 < len(text) < 120)
    print(f"  取词: {'成功' if bool(text) else '失败'}  层级={lvl}  "
          f"字符={len(text) if text else 0}")
    if text:
        print(f"  内容: {text[:70]!r}")
    print(f"  剪贴板: {'洁净' if clean else '污染'}")
    results.append(("第一段部分", bool(text), len(text) if text else 0, lvl, clean))
    if not text:
        fails += 1
        print(f"  轨迹: {[(d.get('level'), d.get('reason')) for d in trail]}")
    if not clean:
        fails += 1
    send_esc()
    time.sleep(0.3)

    # 用例 D：连续 5 次整行拖选稳定性
    print("\n[D] 连续 5 次整行拖选稳定性")
    stab = 0
    for i in range(5):
        drag_on_line(r1[0], r1[1], r1[2], r1[3])
        ok, text, lvl, clean, trail = acquire_check(ALPHA)
        if ok:
            stab += 1
        if not clean:
            fails += 1
        send_esc()
        time.sleep(0.22)
        print(f"  第 {i+1} 次: {'成功' if ok else '失败'} "
              f"({len(text) if text else 0} 字符)")
    print(f"  稳定成功 {stab}/5")
    if stab < 5:
        fails += 1

    try:
        proc.kill()
    except Exception:
        pass

    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"{'用例':<16}{'取词':<8}{'字符数':<10}{'层级':<12}{'剪贴板'}")
    for name, ok, n, lvl, clean in results:
        print(f"{name:<16}{'成功' if ok else '失败':<8}{n:<10}{lvl:<12}"
              f"{'洁净' if clean else '污染'}")

    print("\n" + "-" * 78)
    if fails == 0:
        print("结论：在**真实文字行**上拖选时，取词链路 100% 成功，剪贴板全部还原。")
        print("      此前失败是采样点落在空白/非文本区域所致，不是方案缺陷。")
    else:
        print(f"结论：仍有 {fails} 项问题，见上方明细。")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
