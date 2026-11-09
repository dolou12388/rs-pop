"""严格复验：Chromium 系浏览器正文选区能否被 UIA TextPattern 读取。

此前 sh-poc 的 A/B 测试结论是「Chromium 不暴露 UIA TextPattern」。
但刚发现浏览器窗口的 DocumentRange 可读到 1057 字符全文，
说明 TextPattern 实际可用 —— 之前 GetSelection() 返回 0，
极可能是因为测试时浏览器内没有建立选区，而非能力缺失。

本脚本做严格的双状态对照：
  状态 A：浏览器内无选区  -> GetSelection().Length 应为 0
  状态 B：浏览器内已选中文字 -> GetSelection().Length 应 > 0，且能取到文本

如果状态 B 能取到文本，则「零剪贴板 = 丢浏览器场景」的结论必须推翻，
整个方案可以在浏览器里正常工作。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

import comtypes
import comtypes.client

UIA = comtypes.client.GetModule("UIAutomationCore.dll")
user32 = ctypes.WinDLL("user32.dll")


def list_windows():
    """列出所有可见顶层窗口，便于人工确认目标。"""
    out = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        out.append((hwnd, buf.value, cls.value))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def probe_focused():
    """读当前焦点元素的 TextPattern 选区状态。"""
    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elem = uia.GetFocusedElement()
        if not elem:
            return None
        try:
            cls = elem.CurrentClassName
        except Exception:
            cls = "?"
        try:
            ct = elem.CurrentControlType
        except Exception:
            ct = -1
        try:
            pat = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
        except Exception:
            pat = None

        res = {
            "class": cls,
            "control_type": ct,
            "has_text_pattern": bool(pat),
            "sel_count": -1,
            "selected": None,
            "doc_len": -1,
        }
        if pat:
            tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
            try:
                sel = tp.GetSelection()
                res["sel_count"] = sel.Length
                if sel.Length > 0:
                    chunks = []
                    for i in range(sel.Length):
                        r = sel.GetElement(i)
                        if r:
                            chunks.append(r.GetText(-1))
                    res["selected"] = "".join(chunks)
            except Exception as e:
                res["sel_error"] = f"{type(e).__name__}: {e}"
            try:
                doc = tp.DocumentRange
                res["doc_len"] = len(doc.GetText(-1))
            except Exception:
                pass
        return res
    finally:
        comtypes.CoUninitialize()


def main():
    print("=" * 74)
    print("Chromium UIA TextPattern 选区读取 —— 严格复验")
    print("=" * 74)

    # 列出浏览器窗口，确认目标存在
    wins = [
        (h, t, c) for h, t, c in list_windows()
        if any(k in c for k in ("Chrome_WidgetWin_1", "MozillaWindowClass"))
        or any(k in t for k in ("Chrome", "Edge", "豆包", "127.0.0.1", "Quantum"))
    ]
    print("\n检测到的浏览器/文档窗口：")
    for h, t, c in wins[:12]:
        print(f"  hwnd=0x{h:X}  class={c!r}")
        print(f"           title={t[:70]!r}")
    if not wins:
        print("  （未检测到，请先打开浏览器）")

    print("\n" + "-" * 74)
    print("状态 A：请确保【当前没有任何选中文字】，5 秒后采样 ...")
    for i in range(5, 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 20, end="\r")

    a = probe_focused()
    if a is None:
        print("A: 无焦点元素")
        return 1
    print(f"A: Class={a['class']!r} ControlType={a['control_type']} "
          f"TextPattern={a['has_text_pattern']} 选区数={a['sel_count']} "
          f"文档长度={a['doc_len']}")

    print("\n" + "-" * 74)
    print("状态 B：请现在【用鼠标选中一段文字】，15 秒后采样 ...")
    print("        （用键盘 Ctrl+A 全选也可以）")
    for i in range(15, 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 20, end="\r")

    b = probe_focused()
    if b is None:
        print("B: 无焦点元素")
        return 1
    print(f"B: Class={b['class']!r} ControlType={b['control_type']} "
          f"TextPattern={b['has_text_pattern']} 选区数={b['sel_count']} "
          f"文档长度={b['doc_len']}")

    print("\n" + "=" * 74)
    print("判定")
    print("=" * 74)
    ok = True
    if not b["has_text_pattern"]:
        print("✗ 状态 B 无 TextPattern —— Chromium 确实不暴露，原结论成立")
        ok = False
    elif b["sel_count"] <= 0:
        print(f"△ 有 TextPattern 但选区数={b['sel_count']}，未能读到选区")
        print("  可能原因：选区在 iframe/Shadow DOM 内，或采样时选区已丢失")
        ok = False
    else:
        text = b["selected"] or ""
        print(f"✓ TextPattern 可用且读到选区！选区数={b['sel_count']}")
        print(f"  选中文本 {len(text)} 字符: {text[:120]!r}")
        print()
        print("  结论：此前『Chromium 不暴露 UIA TextPattern』的判断被推翻。")
        print("        零剪贴板方案可覆盖浏览器场景，无需剪贴板兜底。")
    print("=" * 74)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
