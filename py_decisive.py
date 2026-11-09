"""决定性实验：Chromium 中存在确定选区时，Ctrl+C 取词是否可靠。

为什么需要这个实验：
  前两轮测试互相矛盾——
    · 九宫格 + Ctrl+A ：飞书 4/9，ChatGPT 8/9 成功
    · 合成鼠标拖选   ：飞书 0/3，ChatGPT 1/3 成功
  矛盾根源很可能是「合成鼠标拖拽不被 Chromium 识别为选择操作」，
  而不是「剪贴板路径不可靠」。必须把变量剥离开。

实验设计：
  用 JS 的 Range API 在页面里建立**确定性选区**（等价于用户真实选中），
  然后在多个时间点发 Ctrl+C，测量：
    · 选区建立后立即复制   → 是否成功
    · 延迟 100/300/600ms  → 是否成功
    · 连续多次复制         → 是否稳定
  并验证剪贴板每次都还原。

如果全部成功，则证明：只要存在真实选区，剪贴板路径可靠；
则产品上应保证「鼠标释放瞬间立即取词」，而不是拖选后延迟。
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import time
import uuid

import py_clipguard as cg

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002
MARKER = "DECISIVE_\u57fa\u7ebf_2026"

EXPECTED_MARK = "DECISIVE_SELECTION_MARKER"


def class_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title_of(h):
    n = user32.GetWindowTextLengthW(h)
    if n == 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def find_by_title_prefix(prefix):
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


def send_ctrl_c():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.015)
    user32.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.015)
    user32.keybd_event(VK_C, 0, KEYDEF_UP := KEYEVENTF_KEYUP, 0)
    sleep = 0.015
    time.sleep(sleep)
    user32.keybd_event(VK_CONTROL, 0, KEYDEF_UP, 0)


def copy_and_read(timeout=0.35):
    """发 Ctrl+C 并读取文本，返回 (text, seq_delta)。"""
    seq0 = cg.clipboard_seq()
    send_ctrl_c()
    deadline = time.time() + timeout
    text = None
    while time.time() < deadline:
        if cg.clipboard_seq() != seq0:
            time.sleep(0.03)
            text = cg.read_text()
            if text:
                break
        time.sleep(0.012)
    return text, cg.clipboard_seq() - seq0


def main():
    print("=" * 76)
    print("决定性实验：确定选区下 Chromium 的 Ctrl+C 可靠性")
    print("=" * 76)

    profile = os.path.join(tempfile.gettempdir(), f"decisive_{uuid.uuid4().hex[:8]}")
    html = os.path.abspath("decisive_sel.html").replace("\\", "/")
    args = [
        EDGE,
        f"--app=file:///{html}",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--window-size=900,600",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[1] Edge 启动 pid={proc.pid}")

    hwnd = None
    for _ in range(70):
        time.sleep(0.25)
        hits = find_by_title_prefix("SELOK:")
        if hits:
            hwnd, ttl = hits[0]
            break
    if not hwnd:
        print("[!] 未找到已建立选区的窗口")
        print("    现有窗口前缀样本:")
        for h, t in find_by_title_prefix("")[:10]:
            if "Edge" in t or "html" in t:
                print(f"      {t[:60]!r}")
        proc.kill()
        return 1

    print(f"[2] 窗口就绪 hwnd=0x{hwnd:X} title={ttl!r}（页面 JS 已建选区）")
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    print(f"[3] 已激活，焦点在文档")

    fails = 0

    # 阶段 1：立即复制
    print("\n[阶段1] 选区建立后立即 Ctrl+C")
    cg.write_text(MARKER)
    time.sleep(0.1)
    base = cg.read_text()

    guard = cg.ClipGuard()
    guard.__enter__()
    try:
        text, delta = copy_and_read()
        print(f"  结果: {'成功' if text and text != MARKER else '失败'}  "
              f"seq_delta={delta}")
        if text:
            print(f"  内容: {text[:80]!r}")
        ok1 = bool(text and text != MARKER and EXPECTED_MARK in text)
        print(f"  {'PASS' if ok1 else 'FAIL'} 期望包含 {EXPECTED_MARK!r}")
        if not ok1:
            fails += 1
    finally:
        guard.__exit__(None, None, None)
        print(f"  还原: {guard.restore_result}")

    time.sleep(0.15)
    clean1 = cg.read_text() == base
    print(f"  剪贴板: {'洁净' if clean1 else '污染'}")
    if not clean1:
        fails += 1

    # 阶段 2：延迟复制
    print("\n[阶段2] 延迟后 Ctrl+C（验证选区是否易失）")
    for delay in (0.1, 0.3, 0.6, 1.2):
        cg.write_text(MARKER)
        time.sleep(0.1)
        g = cg.ClipGuard()
        g.__enter__()
        try:
            time.sleep(delay)
            text, delta = copy_and_read()
            ok = bool(text and text != MARKER and EXPECTED_MARK in text)
            print(f"  延迟 {delay*1000:4.0f}ms -> {'成功' if ok else '失败'} "
                  f"({len(text) if text else 0} 字符, seq_delta={delta})")
            if not ok:
                fails += 1
        finally:
            g.__exit__(None, None, None)
        time.sleep(0.1)

    # 阶段 3：连续 8 次稳定性
    print("\n[阶段3] 连续 8 次复制稳定性")
    ok_count = 0
    for i in range(8):
        cg.write_text(MARKER)
        time.sleep(0.08)
        g = cg.ClipGuard()
        g.__enter__()
        try:
            text, _ = copy_and_read()
            if text and EXPECTED_MARK in text:
                ok_count += 1
        finally:
            g.__exit__(None, None, None)
        time.sleep(0.08)
    print(f"  成功 {ok_count}/8")
    if ok_count < 8:
        fails += 1

    # 阶段 4：剪贴板最终状态
    time.sleep(0.15)
    final = cg.read_text()
    print(f"\n[阶段4] 最终剪贴板: {final!r}")
    if final != base:
        print(f"  FAIL 偏离基线")
        fails += 1
    else:
        print("  PASS 与基线一致")

    try:
        proc.kill()
    except Exception:
        pass

    print("\n" + "=" * 76)
    if fails == 0:
        print("结论：只要存在真实选区，Chromium 剪贴板取词完全可靠。")
        print("      前一轮拖选失败是「合成鼠标不被识别为选择」，非路径缺陷。")
        print("      产品上必须保证鼠标释放瞬间立即取词，不宜引入多余延迟。")
    else:
        print(f"结论：存在 {fails} 项失败，剪贴板路径在 Chromium 上不可靠。")
    print("=" * 76)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
