# -*- coding: utf-8 -*-
"""剪贴板零污染回归测试。

覆盖两个线上真实故障，缺任何一条都会让缺陷复发：
  A. 「复制内容会复制不上」—— 用户在我们的事务窗口内自己按了 Ctrl+C，
     我们的还原把用户的新内容换回旧内容。
  B. 「截图在剪贴板里找不到」—— 截图工具的拖框被当成划词触发事务，
     截图工具在窗口内写入的图片被还原 EmptyClipboard 掉。

同时覆盖反向约束：正常的还原路径必须仍然工作，不能"为了安全永不还原"，
否则剪贴板会残留我们复制的中间态，同样违反"零污染"。

运行：py -3 py_clip_race_test.py
"""
import ctypes
import struct
import sys

import py_clipguard as cg

fails = 0


def check(label, ok, detail=""):
    global fails
    print(("[PASS] " if ok else "[FAIL] ") + label + ("  " + detail if detail else ""))
    if not ok:
        fails += 1


def fmt_names():
    out = []
    if not cg._open_clipboard():
        return ["<打不开>"]
    try:
        f = 0
        while True:
            f = cg.user32.EnumClipboardFormats(f)
            if f == 0:
                break
            out.append(cg.format_name(f))
    finally:
        cg.user32.CloseClipboard()
    return out


def put_dib(w=32, h=32):
    """放一张 DIB 进剪贴板，模拟截图工具的写入。"""
    bih = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, w * h * 4,
                      2835, 2835, 0, 0)
    pix = bytes([10, 20, 30, 255]) * (w * h)
    dib = bih + pix
    hmem = cg.kernel32.GlobalAlloc(cg.GMEM_MOVEABLE, len(dib))
    if not hmem:
        return False
    p = cg.kernel32.GlobalLock(hmem)
    if not p:
        cg.kernel32.GlobalFree(hmem)
        return False
    ctypes.memmove(p, dib, len(dib))
    cg.kernel32.GlobalUnlock(hmem)
    if not cg._open_clipboard():
        cg.kernel32.GlobalFree(hmem)
        return False
    try:
        cg.user32.EmptyClipboard()
        return bool(cg.user32.SetClipboardData(cg.CF_DIB, hmem))
    finally:
        cg.user32.CloseClipboard()


def run():
    print("=" * 70)
    print("A. 事务期间用户自己复制 —— 新内容不能被覆盖")
    print("=" * 70)
    cg.write_text("A_旧内容")
    g = cg.ClipGuard()
    g.__enter__()
    # 用户手动复制（我们的取词没读到东西，第三方抢先写入）
    cg.write_text("A_用户手动复制的新内容")
    g.__exit__(None, None, None)
    got = cg.read_text()
    print(f"  还原结论: {g.restore_result}")
    print(f"  最终内容: {got!r}")
    check("用户手动复制的新内容被保留", got == "A_用户手动复制的新内容")

    print()
    print("=" * 70)
    print("B. 事务期间截图工具写入图片 —— 图片不能被抹掉")
    print("=" * 70)
    cg.write_text("B_截图前的旧内容")
    g2 = cg.ClipGuard()
    g2.__enter__()
    g2._copy_attempted = True          # 我们发了 Ctrl+C（截图工具的遮罩没响应）
    put_dib()                          # 截图工具写入图片
    g2.__exit__(None, None, None)
    names = fmt_names()
    print(f"  还原结论: {g2.restore_result}")
    print(f"  最终格式: {names}")
    check("截图仍在剪贴板", any(n in ("CF_DIB", "CF_DIBV5") for n in names),
          f"格式={names}")

    print()
    print("=" * 70)
    print("C. 正常路径 —— 我们自己写的中间态必须被还原（不能永不还原）")
    print("=" * 70)
    cg.write_text("C_原始内容")
    with cg.ClipGuard() as g3:
        cg.write_text("C_我们的中间态")
        g3.note_self_write("C_我们的中间态")
    got3 = cg.read_text()
    print(f"  还原结论: {g3.restore_result}")
    print(f"  最终内容: {got3!r}")
    check("正常事务后内容被还原", got3 == "C_原始内容",
          "若为中间态则说明为了安全放弃了所有还原")
    check("还原结论标记为成功", bool(g3.restore_result and g3.restore_result[0]))

    print()
    print("=" * 70)
    print("D. 我们写到一半、目标程序继续分批写格式 —— 不能误判为第三方")
    print("=" * 70)
    # 模拟：我们登记中间态后，剪贴板序列号又变了一次（目标程序补写 CF_HTML）
    cg.write_text("D_原始内容")
    g4 = cg.ClipGuard()
    g4.__enter__()
    cg.write_text("D_中间态")
    g4.note_self_write("D_中间态")
    # 手动推进序列号，模拟目标程序补写一个附加格式
    cg.write_text("D_中间态")      # 同内容再写一次 => 序列号+1，内容不变
    g4.__exit__(None, None, None)
    got4 = cg.read_text()
    print(f"  还原结论: {g4.restore_result}")
    print(f"  最终内容: {got4!r}")
    check("同内容的重复写入仍被判为我们的中间态并还原",
          got4 == "D_原始内容",
          "若留在中间态说明序列号判据过严，会把正常补写误判成第三方")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    rc = run()
    print()
    print("=" * 70)
    print("全部通过" if rc == 0 else f"{fails} 项失败")
    print("=" * 70)
    sys.exit(rc)
