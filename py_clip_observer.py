# -*- coding: utf-8 -*-
"""剪贴板序列号被动观测：不做任何操作，看序列号是否自己变化。

作用：把"单击改了剪贴板"与"环境里有别的程序在改剪贴板"这两种可能分开。
      如果零交互下序列号就在涨，说明 py_drag_gate_test.py 的
      "序列号必须完全不变化"这条断言实际在测环境，而非测我们的代码。
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import py_clipguard as cg


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("=" * 72)
    print(f"剪贴板序列号被动观测：{seconds} 秒内零交互")
    print("=" * 72)

    seq0 = cg.clipboard_seq()
    text0 = cg.read_text()
    print(f"起点  序列号={seq0}  内容={text0[:40]!r}")

    changes = []
    last = seq0
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(0.15)
        cur = cg.clipboard_seq()
        if cur != last:
            changes.append((round(time.time() - t0, 2), cur - last, cur,
                            cg.read_text()[:40]))
            last = cur

    print(f"终点  序列号={cg.clipboard_seq()}  内容={cg.read_text()[:40]!r}")
    print(f"\n变化次数 = {len(changes)}")
    for t, d, cur, txt in changes:
        print(f"  t={t:>6.2f}s  +{d:<4} -> {cur:<8} {txt!r}")

    if changes:
        print("\n结论：剪贴板在无人操作时被外部程序修改过。")
        print("      因此 '序列号必须完全不变化' 不能用来判定本程序是否碰了剪贴板；")
        print("      应改为判定'内容是否被破坏' + '序列号增量是否为 0'。")
    else:
        print("\n结论：环境干净，观测期内无外部干扰。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
