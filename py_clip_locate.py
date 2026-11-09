"""定位剪贴板异常：为什么基线文本会在多次事务后消失。

对比 4 种路径：
  A. write_text 写入 -> 读回               （单纯写入是否可靠）
  B. 写入 -> ClipGuard 备份 -> 不复制 -> 还原  （空事务是否保内容）
  C. 写入 -> ClipGuard 备份 -> 模拟复制 -> 还原（完整事务）
  D. 连续 5 次空事务                         （累积漂移）
每一步都打印序列号变化与格式清单，定位丢在哪一步。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

import py_clipguard as cg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = cg.user32
kernel32 = cg.kernel32

MARKER = "LOCATE_BASELINE_\u57fa\u7ebf_9527"


def dump(tag):
    seq = cg.clipboard_seq()
    fmts = []
    if cg._open_clipboard():
        try:
            f = 0
            while True:
                f = user32.EnumClipboardFormats(f)
                if not f:
                    break
                h = user32.GetClipboardData(f)
                size = kernel32.GlobalSize(h) if h else 0
                fmts.append((f, cg.format_name(f), size))
        finally:
            user32.CloseClipboard()
    txt = cg.read_text()
    desc = ", ".join(f"{n}({s}B)" for _, n, s in fmts) or "(无格式)"
    print(f"  [{tag}] seq={seq} text={txt!r}")
    print(f"         格式: {desc}")
    return seq, txt, len(fmts)


def main():
    print("=" * 74)
    print("剪贴板还原异常定位")
    print("=" * 74)

    # ---------- A ----------
    print("\nA. write_text 单纯写入")
    cg.write_text(MARKER)
    time.sleep(0.12)
    seq_a, txt_a, n_a = dump("A")
    ok_a = txt_a == MARKER
    print(f"  {'PASS' if ok_a else 'FAIL'} 写入可读回\n")

    # ---------- B ----------
    print("B. 空事务（备份后不复制，直接还原）")
    seq_b0, txt_b0, _ = dump("B0")
    g = cg.ClipGuard()
    g.__enter__()
    g.__exit__(None, None, None)
    print(f"  还原结果: {g.restore_result}")
    time.sleep(0.12)
    seq_b1, txt_b1, n_b1 = dump("B1")
    ok_b = txt_b1 == MARKER
    print(f"  {'PASS' if ok_b else 'FAIL'} 空事务后内容不变\n")

    # ---------- C ----------
    print("C. 完整事务（备份 -> 模拟目标写入中间态 -> 还原）")
    cg.write_text(MARKER)
    time.sleep(0.12)
    seq_c0, txt_c0, n_c0 = dump("C0")
    g = cg.ClipGuard()
    g.__enter__()
    # 模拟目标程序把选区写进剪贴板
    cg.write_text("\u88ab\u590d\u5236\u7684\u9009\u533a\u6587\u5b57_SIMULATED")
    time.sleep(0.08)
    mid = cg.read_text()
    print(f"  [C-mid] 中间态 text={mid!r}")
    g.__exit__(None, None, None)
    print(f"  还原结果: {g.restore_result}")
    time.sleep(0.12)
    seq_c1, txt_c1, n_c1 = dump("C1")
    ok_c = txt_c1 == MARKER
    print(f"  {'PASS' if ok_c else 'FAIL'} 完整事务后内容还原")
    print(f"  格式数: {n_c0} -> {n_c1}\n")

    # ---------- D ----------
    print("D. 连续 5 次完整事务，观察累积漂移")
    cg.write_text(MARKER)
    time.sleep(0.12)
    print(f"  起点: {cg.read_text()!r}")
    for i in range(5):
        g = cg.ClipGuard()
        g.__enter__()
        cg.write_text(f"\u4e2d\u95f4\u6001_{i}")
        time.sleep(0.05)
        g.__exit__(None, None, None)
        t = cg.read_text()
        flag = "OK" if t == MARKER else "DRIFT"
        print(f"  第 {i+1} 次: {t!r}  {flag}")
        time.sleep(0.08)

    time.sleep(0.15)
    final = cg.read_text()
    seq_f, _, n_f = dump("D-final")
    ok_d = final == MARKER
    print(f"  {'PASS' if ok_d else 'FAIL'} 5 次事务后仍为基线")

    print("\n" + "=" * 74)
    fails = sum([not ok_a, not ok_b, not ok_c, not ok_d])
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
