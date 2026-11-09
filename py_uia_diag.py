"""诊断：枚举焦点元素支持的所有 UIA Pattern，并逐一尝试取文本。

目的：搞清楚 RichEdit 到底暴露了哪些 Pattern，以及为什么 TextPattern 取不到。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import comtypes
import comtypes.client

UIA = comtypes.client.GetModule("UIAutomationCore.dll")
user32 = ctypes.WinDLL("user32.dll")

# 全部 Pattern ID 及名称
PATTERN_NAMES = {
    10000: "InvokePattern",
    10001: "SelectionPattern",
    10002: "ValuePattern",
    10003: "RangeValuePattern",
    10004: "ScrollPattern",
    10005: "ExpandCollapsePattern",
    10006: "GridPattern",
    10007: "GridItemPattern",
    10008: "MultipleViewPattern",
    10009: "WindowPattern",
    10010: "SelectionItemPattern",
    10011: "DockPattern",
    10012: "TablePattern",
    10013: "TableItemPattern",
    10014: "TextPattern",
    10015: "TogglePattern",
    10016: "TransformPattern",
    10017: "ScrollItemPattern",
    10018: "LegacyIAccessiblePattern",
    10019: "ItemContainerPattern",
    10020: "VirtualizedItemPattern",
    10021: "SynchronizedInputPattern",
    10022: "ObjectModelPattern",
    10023: "AnnotationPattern",
    10024: "TextPattern2",
    10025: "StylesPattern",
    10026: "SpreadsheetPattern",
    10027: "SpreadsheetItemPattern",
    10028: "TextChildPattern",
    10029: "DragPattern",
    10030: "DropTargetPattern",
    10031: "TextEditPattern",
    10032: "CustomNavigationPattern",
    10033: "SelectionPattern2",
}


def find_window_by_title(title):
    hwnds = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return hwnds[0] if hwnds else None


def main():
    print("=" * 72)
    print("诊断：焦点元素的 UIA Pattern 能力")
    print("=" * 72)

    ps = subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "uia_target.ps1"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    hwnd = None
    for _ in range(40):
        time.sleep(0.25)
        hwnd = find_window_by_title("UIATarget")
        if hwnd:
            break
    if not hwnd:
        print("[!] 靶子未出现")
        ps.kill()
        return 1

    time.sleep(1.5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    print(f"[1] 靶子已激活 hwnd=0x{hwnd:X}")

    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elem = uia.GetFocusedElement()
        print(f"[2] 焦点 Class={elem.CurrentClassName!r} "
              f"ControlType={elem.CurrentControlType}")

        # 通过 IsPatternAvailable 判断（属性名按类型库实际定义）
        for probe in (
            "CurrentIsTextPatternAvailable",
            "CurrentIsValuePatternAvailable",
            "CurrentIsLegacyIAccessiblePatternAvailable",
            "CurrentIsSelectionPatternAvailable",
        ):
            try:
                v = getattr(elem, probe)
                print(f"    {probe} = {v}")
            except AttributeError:
                pass

        print("\n[3] 逐一探测 Pattern:")
        available = []
        for pid_, pname in sorted(PATTERN_NAMES.items()):
            try:
                p = elem.GetCurrentPattern(pid_)
                if p:
                    available.append((pid_, pname, p))
                    print(f"    ✓ {pid_:5d} {pname}")
            except Exception:
                pass
        if not available:
            print("    （无任何 Pattern）")

        print("\n[4] 尝试取值:")
        for pid_, pname, p in available:
            if pname in ("TextPattern", "TextPattern2"):
                try:
                    tp = p.QueryInterface(UIA.IUIAutomationTextPattern)
                    sel = tp.GetSelection()
                    print(f"    {pname}: 选区数={sel.Length}")
                    doc = tp.DocumentRange
                    dt = doc.GetText(-1)
                    print(f"    {pname}: DocumentRange 全文 {len(dt)} 字符: {dt[:60]!r}")
                    for i in range(sel.Length):
                        r = sel.GetElement(i)
                        t = r.GetText(-1)
                        print(f"    {pname}: 选区[{i}] = {t!r}")
                except Exception as e:
                    print(f"    {pname} 失败: {type(e).__name__}: {e}")

            elif pname == "ValuePattern":
                try:
                    vp = p.QueryInterface(UIA.IUIAutomationValuePattern)
                    print(f"    ValuePattern.CurrentValue = {vp.CurrentValue!r}")
                except Exception as e:
                    print(f"    ValuePattern 失败: {type(e).__name__}")

            elif pname == "LegacyIAccessiblePattern":
                try:
                    lp = p.QueryInterface(UIA.IUIAutomationLegacyIAccessiblePattern)
                    print(f"    LegacyIAccessible.Value = {lp.CurrentValue!r}")
                    print(f"    LegacyIAccessible.Name  = {lp.CurrentName!r}")
                except Exception as e:
                    print(f"    Legacy 失败: {type(e).__name__}")

            elif pname == "SelectionPattern":
                try:
                    sp = p.QueryInterface(UIA.IUIAutomationSelectionPattern)
                    s = sp.GetCurrentSelection()
                    print(f"    SelectionPattern: 选中项 {s.Length} 个")
                    for i in range(s.Length):
                        it = s.GetElement(i)
                        print(f"      [{i}] Name={it.CurrentName!r}")
                except Exception as e:
                    print(f"    SelectionPattern 失败: {type(e).__name__}")
    finally:
        comtypes.CoUninitialize()

    ps.kill()
    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
