"""验证修复：原剪贴板为空时，事务结束后必须仍为空。

修复前的缺陷：_snapshot 为空 -> restore() 直接放弃 ->
Ctrl+C 写进去的中间态被永久留下。用户会看到「程序把我剪贴板清空了又塞了奇怪东西」。

三个场景：
  F1  原剪贴板为空 -> 空事务 -> 应仍为空
  F2  原剪贴板为空 -> 完整事务（有中间态）-> 应仍为空
  F3  原剪贴板有内容 -> 完整事务 -> 应还原为原内容（回归检查）
"""

import ctypes
import sys
import time

import py_clipguard as cg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = cg.user32
kernel32 = cg.kernel32

MARKER = "FIXTEST_BASELINE_\u57fa\u7ebf_27182"


def clear_clipboard():
    if cg._open_clipboard():
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()
    time.sleep(0.1)


def state():
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
                fmts.append((cg.format_name(f), kernel32.GlobalSize(h) if h else 0))
        finally:
            user32.CloseClipboard()
    return seq, cg.read_text(), [n for n, _ in fmts]


def main():
    print("=" * 74)
    print("空剪贴板还原修复验证")
    print("=" * 74)
    fails = 0

    # ---------- F1 ----------
    print("\nF1. 原剪贴板为空 + 空事务")
    clear_clipboard()
    seq0, txt0, fmt0 = state()
    print(f"  基线: seq={seq0} text={txt0!r} 格式={fmt0}")
    g = cg.ClipGuard()
    g.__enter__()
    g.__exit__(None, None, None)
    print(f"  还原: {g.restore_result}")
    time.sleep(0.12)
    seq1, txt1, fmt1 = state()
    print(f"  事后: seq={seq1} text={txt1!r} 格式={fmt1}")
    ok1 = txt1 in (None, "") and "CF_UNICODETEXT" not in fmt1
    print(f"  {'PASS' if ok1 else 'FAIL'} 仍为空剪贴板")
    fails += (not ok1)

    # ---------- F2 ----------
    print("\nF2. 原剪贴板为空 + 完整事务（产生中间态）")
    clear_clipboard()
    seq0, txt0, fmt0 = state()
    print(f"  基线: seq={seq0} text={txt0!r} 格式={fmt0}")
    g = cg.ClipGuard()
    g.__enter__()
    cg.write_text("\u4e2d\u95f4\u6001_\u5e94\u88ab\u6e05\u9664_ABCDEF")
    time.sleep(0.06)
    mid = cg.read_text()
    print(f"  中间态: {mid!r}")
    g.__exit__(None, None, None)
    print(f"  还原: {g.restore_result}")
    time.sleep(0.12)
    seq1, txt1, fmt1 = state()
    print(f"  事后: seq={seq1} text={txt1!r} 格式={fmt1}")
    ok2 = txt1 in (None, "") and "CF_UNICODETEXT" not in fmt1
    print(f"  {'PASS' if ok2 else 'FAIL'} 中间态已清除，剪贴板恢复为空")
    fails += (not ok2)

    # ---------- F3 ----------
    print("\nF3. 原剪贴板有内容 + 完整事务（回归）")
    cg.write_text(MARKER)
    time.sleep(0.12)
    seq0, txt0, fmt0 = state()
    print(f"  基线: seq={seq0} text={txt0!r} 格式={fmt0}")
    g = cg.ClipGuard()
    g.__enter__()
    cg.write_text("\u4e2d\u95f4\u6001_\u5e94\u88ab\u8fd8\u539f")
    time.sleep(0.06)
    g.__exit__(None, None, None)
    print(f"  还原: {g.restore_result}")
    time.sleep(0.12)
    seq1, txt1, fmt1 = state()
    print(f"  事后: seq={seq1} text={txt1!r} 格式={fmt1}")
    ok3 = txt1 == MARKER
    print(f"  {'PASS' if ok3 else 'FAIL'} 内容已还原")
    fails += (not ok3)

    print("\n" + "=" * 74)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
