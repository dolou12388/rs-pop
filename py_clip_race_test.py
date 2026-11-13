# -*- coding: utf-8 -*-
"""剪贴板零污染回归测试。

覆盖线上四类真实故障，缺任何一条都会让缺陷复发：

  A. 「复制内容会复制不上」—— 用户在我们的事务窗口内自己按了 Ctrl+C，
     我们的还原把用户的新内容换回旧内容。
  B. 「截图在剪贴板里找不到」—— 截图工具的拖框被当成划词触发事务，
     截图工具在窗口内写入的图片被还原 EmptyClipboard 掉。
  C. 反向：为此把闸门收成"没有指纹就不还原"，会让划词复制的文本
     残留在剪贴板上（「划词时仍有概率污染剪贴板」）。
  D. 目标程序分批写格式、写得很晚、写空串 —— 都不能被误判成第三方。

两个方向的错误代价对称，必须同时防住：
  误判为"我们的" → 抹掉用户的新内容
  误判为"别人的" → 留下我们复制的内容

运行：py -3 -X utf8 py_clip_race_test.py
"""
import ctypes
import struct
import sys
import time

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


def put_same_text_noempty(text):
    """不清空剪贴板，直接再写一次同内容文本 —— 序列号 +1，内容不变。

    这是目标程序分批写入最忠实的模拟：真实程序补写附加格式时不会
    EmptyClipboard，只是在已有基础上 SetClipboardData，序列号因此递增。
    """
    data = (text + "\0").encode("utf-16-le")
    hmem = cg.kernel32.GlobalAlloc(cg.GMEM_MOVEABLE, len(data))
    if not hmem:
        return False
    p = cg.kernel32.GlobalLock(hmem)
    if not p:
        cg.kernel32.GlobalFree(hmem)
        return False
    ctypes.memmove(p, data, len(data))
    cg.kernel32.GlobalUnlock(hmem)
    if not cg._open_clipboard():
        cg.kernel32.GlobalFree(hmem)
        return False
    try:
        return bool(cg.user32.SetClipboardData(cg.CF_UNICODETEXT, hmem))
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
    print("D. 目标程序分批写格式 —— 登记指纹前 settle，不能被误判为第三方")
    print("=" * 70)
    # 真实路径：read_text_via_copy 读到文本后先 _wait_copy_settled() 等序列号
    # 安静，再登记指纹。所以"登记之后程序还在补写"这种情况不会发生 ——
    # 补写都已经在 settle 阶段被吸收掉了。
    #
    # 这条测试要验的正是这个机制：分批写入后 settle 得到的序列号，
    # 应当能代表"我们这次复制的最终状态"，之后的还原不被误判。
    cg.write_text("D_原始内容")
    time.sleep(0.06)
    g4 = cg.ClipGuard()
    g4.__enter__()
    g4._copy_attempted = True
    g4._copy_sent = True
    # 模拟目标程序分批写：先文本
    cg.write_text("D_中间态")
    # 再补一批（序列号 +1，内容不变）
    cg.write_text("D_中间态")
    # 按真实路径：先 settle 再登记
    g4._intermediate_seq = cg._wait_copy_settled()
    g4._intermediate_text = "D_中间态"
    g4._intermediate_at = time.perf_counter()
    g4.__exit__(None, None, None)
    got4 = cg.read_text()
    print(f"  还原结论: {g4.restore_result}")
    print(f"  最终内容: {got4!r}")
    check("分批写入后仍正确还原", got4 == "D_原始内容",
          f"实际={got4!r}（残留说明 settle 后的序列号仍被误判为第三方）")

    print()
    print("=" * 70)
    print("E. 目标程序写得很晚 —— 我们超时退出后才写入，也必须还原")
    print("=" * 70)
    # 真实链路：Ctrl+C 发出去 -> 两轮各 650ms 都没等到 -> 退出循环
    # -> 目标程序这时才把文本写进剪贴板 -> restore 时没有指纹。
    # 早期版本据此判为"外部改写"而跳过还原，剪贴板就残留了选的文字。
    cg.write_text("E_用户原有剪贴板")
    time.sleep(0.06)
    g5 = cg.ClipGuard()
    g5.__enter__()
    g5._copy_attempted = True
    g5._copy_sent = True          # 真实路径由 read_text_via_copy 置位
    cg.write_text("E_用户选中的文字")   # 迟到的写入，从未登记指纹
    g5.__exit__(None, None, None)
    got5 = cg.read_text()
    print(f"  还原结论: {g5.restore_result}")
    print(f"  最终内容: {got5!r}")
    check("迟到的复制结果也被还原", got5 == "E_用户原有剪贴板",
          f"实际={got5!r}（残留即为污染）")

    print()
    print("=" * 70)
    print("F. 目标程序写入空串 —— `if text:` 判假，也必须还原")
    print("=" * 70)
    # 空串让读词循环认为"没取到"，指纹因此为空。若据此放弃还原，
    # 用户剪贴板会被清空 —— 比残留更难察觉。
    cg.write_text("F_用户原有剪贴板")
    time.sleep(0.06)
    g6 = cg.ClipGuard()
    g6.__enter__()
    g6._copy_attempted = True
    g6._copy_sent = True
    cg.write_text("")             # 空串
    g6.__exit__(None, None, None)
    got6 = cg.read_text()
    print(f"  还原结论: {g6.restore_result}")
    print(f"  最终内容: {got6!r}")
    check("空串写入也被还原", got6 == "F_用户原有剪贴板",
          f"实际={got6!r}（清空即为污染）")

    print()
    print("=" * 70)
    print("G. 未发出复制指令时，剪贴板的变化绝不碰（反向约束）")
    print("=" * 70)
    # 上一轮的截图保护必须仍然成立。这里直接构造"我们没发 Ctrl+C，
    # 别人写了图"的最纯粹情形。
    cg.write_text("G_用户原有剪贴板")
    time.sleep(0.06)
    g7 = cg.ClipGuard()
    g7.__enter__()
    # 完全不置 _copy_sent / _copy_attempted
    put_dib()
    g7.__exit__(None, None, None)
    n7 = fmt_names()
    print(f"  还原结论: {g7.restore_result}")
    print(f"  最终格式: {n7}")
    check("未发指令时图片完整保留",
          any(x in ("CF_DIB", "CF_DIBV5") for x in n7), f"格式={n7}")

    print()
    print("=" * 70)
    print("H. 选词后立即按 Ctrl+C 复制同一段文字 —— 必须保留用户的复制")
    print("=" * 70)
    # 线上故障：「选词后立即复制，复制不上」。
    # 用户复制的正是刚选中的那段文字，与我们的中间态**逐字相同**。
    # 任何内容比对都识别不出这是新的一次写入，只有序列号能。
    # 上一版删掉了序列号判据，于是判为"仍是我们的中间态"，
    # 还原把用户的复制换回旧内容。
    cg.write_text("H_复制前的旧内容")
    time.sleep(0.06)
    g8 = cg.ClipGuard()
    g8.__enter__()
    g8._copy_attempted = True
    g8._copy_sent = True
    # 我们的 Ctrl+C 让目标程序写入选中文字
    cg.write_text("H_选中的那段文字")
    g8._intermediate_seq = cg._wait_copy_settled()
    g8._intermediate_text = "H_选中的那段文字"
    g8._intermediate_at = time.perf_counter()
    # 用户紧接着按 Ctrl+C 复制同一段文字 —— 内容完全相同，只有序列号变
    time.sleep(0.08)                      # 超过程序补写的时间量级
    cg.write_text("H_选中的那段文字")
    g8.__exit__(None, None, None)
    got8 = cg.read_text()
    print(f"  还原结论: {g8.restore_result}")
    print(f"  最终内容: {got8!r}")
    check("用户的复制结果被保留", got8 == "H_选中的那段文字",
          f"实际={got8!r}（变回旧内容即「复制不上」）")

    print()
    print("=" * 70)
    print("I. 宽限期内目标程序补写 —— 仍要还原，不能一刀切")
    print("=" * 70)
    # 与 H 相反：序列号虽变，但发生在宽限窗口内（程序补写格式），
    # 内容仍是我们的中间态，必须照常还原，否则剪贴板残留。
    cg.write_text("I_复制前的旧内容")
    time.sleep(0.06)
    g9 = cg.ClipGuard()
    g9.__enter__()
    g9._copy_attempted = True
    g9._copy_sent = True
    cg.write_text("I_中间态")
    g9._intermediate_seq = cg.clipboard_seq()
    g9._intermediate_text = "I_中间态"
    g9._intermediate_at = time.perf_counter()
    # 立刻补一批（模拟程序紧接着写附加格式，宽限期内）
    put_same_text_noempty("I_中间态")
    g9.__exit__(None, None, None)
    got9 = cg.read_text()
    print(f"  还原结论: {g9.restore_result}")
    print(f"  最终内容: {got9!r}")
    check("宽限期内补写未被误判，仍正确还原", got9 == "I_复制前的旧内容",
          f"实际={got9!r}（残留说明宽限窗口太短）")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    rc = run()
    print()
    print("=" * 70)
    print("全部通过" if rc == 0 else f"{fails} 项失败")
    print("=" * 70)
    sys.exit(rc)
