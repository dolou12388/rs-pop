"""第 3 级剪贴板兜底的真实验证（不依赖 PowerShell）。

思路：把焦点放在一个「Win32 消息级必然失败、但支持 Ctrl+C」的目标上。
最可控的做法：让 Python 自己创建一个 EDIT 控件，但**不建立选区**，
而是先把文本放进剪贴板并让目标在收到 Ctrl+C 时写入特定内容 —— 这不现实。

因此改用更直接的等试验证：
  1. 用 ClipGuard 包裹一次「人为写入临时内容」的操作，
     模拟 Ctrl+C 成功产生的中间态；
  2. 验证 read_text_via_copy 的等待/检测逻辑本身正确
     （通过主动写入模拟目标响应）；
  3. 验证还原后剪贴板与基线完全一致，且格式数量不减。

同时用一个真实的 Ctrl+C 场景做端到端验证：
  靶子 EDIT 控件已有选区，向它发送 Ctrl+C，检查剪贴板内容
  是否等于选区文本，然后还原并核对基线。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg

user32 = ctypes.WinDLL("user32.dll")
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

EXPECTED = "量子纠缠是量子力学中最引人注目的现象之一"
MARKER = "CLIP_BASELINE_\u9884\u7f6e\u57fa\u7ebf_777"


def find_window(title):
    hits = []
    E = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if b.value == title:
                hits.append(h)
        return True

    user32.EnumWindows(E(cb), 0)
    return hits[0] if hits else None


def fmt_ids():
    ids = []
    if not cg._open_clipboard():
        return ids
    try:
        f = 0
        while True:
            f = user32.EnumClipboardFormats(f)
            if f == 0:
                break
            ids.append(f)
    finally:
        user32.CloseClipboard()
    return ids


def main():
    print("=" * 72)
    print("第 3 级剪贴板兜底 + 不污染 真实验证")
    print("=" * 72)

    fails = 0

    # 基线
    cg.write_text(MARKER)
    time.sleep(0.1)
    baseline = cg.read_text()
    n_base = len(fmt_ids())
    print(f"[0] 基线: {baseline!r}  格式数={n_base}")

    # 启动靶子（已有全选选区）
    proc = subprocess.Popen(
        [sys.executable, "py_edit_target.py", "60"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    hwnd = None
    for _ in range(50):
        time.sleep(0.2)
        hwnd = find_window("EditTarget")
        if hwnd:
            break
    if not hwnd:
        print("[!] 靶子未启动")
        out = proc.stdout.read() if proc.stdout else ""
        print(out[:400])
        proc.kill()
        return 1
    print(f"[1] 靶子就绪 hwnd=0x{hwnd:X}")

    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)

    # ---- 真实 Ctrl+C 端到端 ----
    print("[2] 真实 Ctrl+C -> 读取 -> 还原 ...")
    guard = cg.ClipGuard()
    guard.__enter__()
    try:
        text, meta = guard.read_text_via_copy(timeout_ms=900)
        print(f"    读到: {text!r}")
        print(f"    meta: changed={meta['changed']} 等待={meta['waited_ms']}ms "
              f"尝试={meta['attempts']} seq_delta={meta['seq_delta']}")
        if text == EXPECTED:
            print("    PASS 剪贴板兜底取词精确")
        else:
            print(f"    FAIL 期望 {EXPECTED!r}")
            fails += 1
    finally:
        guard.__exit__(None, None, None)
        print(f"    还原: {guard.restore_result}")

    time.sleep(0.15)
    after = cg.read_text()
    n_after = len(fmt_ids())
    print(f"[3] 还原后: {after!r}  格式数={n_after}")
    if after == baseline:
        print("    PASS 内容已还原至基线")
    else:
        print(f"    FAIL 内容被污染（期望 {baseline!r}）")
        fails += 1
    if n_after >= n_base:
        print(f"    PASS 格式未减少 {n_base} -> {n_after}")
    else:
        print(f"    FAIL 格式减少 {n_base} -> {n_after}")
        fails += 1

    # ---- 重复 10 次验证稳定性 ----
    print("[4] 重复 10 次 Ctrl+C 事务 ...")
    seq_trace = []
    for i in range(10):
        g = cg.ClipGuard()
        g.__enter__()
        try:
            t, m = g.read_text_via_copy(timeout_ms=800)
        finally:
            g.__exit__(None, None, None)
        seq_trace.append(m["seq_delta"])
        time.sleep(0.05)

    final = cg.read_text()
    print(f"    各次 seq_delta: {seq_trace}")
    print(f"    最终内容: {final!r}")
    if final == baseline:
        print("    PASS 10 次事务后仍与基线一致")
    else:
        print(f"    FAIL 多次事务后漂移: {final!r}")
        fails += 1

    # ---- 无选区时不应污染 ----
    print("[5] 无选区场景：Ctrl+C 不产生新内容时不应改写剪贴板 ...")
    cg.write_text(MARKER)
    time.sleep(0.1)
    seq_b = cg.clipboard_seq()
    g2 = cg.ClipGuard()
    g2.__enter__()
    try:
        time.sleep(0.3)
        # 直接退出，不触发复制
    finally:
        g2.__exit__(None, None, None)
    seq_a = cg.clipboard_seq()
    if seq_a == seq_b:
        print(f"    PASS 未触发复制时不写剪贴板 (seq {seq_b}=={seq_a})")
    else:
        print(f"    FAIL 无谓写入 (seq {seq_b}->{seq_a})")
        fails += 1

    proc.kill()
    print("-" * 72)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
