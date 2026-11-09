"""用 comtypes 的 UIAutomation 封装读取当前选中文字（零剪贴板）。

对比 py_uia.py（手工 vtable）——本版用 comtypes 生成的类型库绑定，
接口方法名与参数由类型库保证，不存在索引算错的风险。

取词链路：
  CUIAutomation -> GetFocusedElement -> GetCurrentPattern(TextPattern)
  -> GetSelection -> GetElement(0) -> GetText(-1)
"""

import sys
import time

import comtypes.client

# 类型库需先生成再使用；每次运行都调用一次，cached 时开销可忽略。
UIA = comtypes.client.GetModule("UIAutomationCore.dll")


def get_text_from_element(elem):
    """尝试从单个元素抽出选中文字，返回 (text, reason)。"""
    # 优先 TextPattern（富文本控件、编辑框、浏览器正文都可能支持）
    try:
        pat = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
    except Exception as e:
        return None, f"TextPattern 不可用({type(e).__name__})"

    if pat:
        try:
            tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
            sel = tp.GetSelection()
            n = sel.Length
            if n == 0:
                return "", "选区数组为空"
            chunks = []
            for i in range(n):
                rng = sel.GetElement(i)
                if rng:
                    chunks.append(rng.GetText(-1))
            return "".join(chunks), None
        except Exception as e:
            return None, f"TextPattern 读取异常({type(e).__name__}: {e})"

    # 退化到 ValuePattern（部分单行输入框只有 Value）
    try:
        vpat = elem.GetCurrentPattern(UIA.UIA_ValuePatternId)
        if vpat:
            vp = vpat.QueryInterface(UIA.IUIAutomationValuePattern)
            val = vp.CurrentValue
            if val:
                return val, "ValuePattern 兜底（非精确选区）"
    except Exception:
        pass

    return None, "不支持 TextPattern / ValuePattern"


def describe(elem):
    parts = []
    for name, prop in (
        ("Name", "CurrentName"),
        ("Class", "CurrentClassName"),
        ("ControlType", "CurrentControlType"),
    ):
        try:
            v = getattr(elem, prop)
            if name == "ControlType":
                v = f"{v}"
            parts.append(f"{name}={v!r}")
        except Exception:
            parts.append(f"{name}=?")
    return "  ".join(parts)


def main():
    print("=" * 68)
    print("comtypes 直调 UIAutomation —— 零剪贴板取词验证")
    print("=" * 68)

    comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        UIA.CUIAutomation, interface=UIA.IUIAutomation
    )
    print("[1] CUIAutomation 创建成功")

    found = False
    for attempt in range(1, 4):
        elem = uia.GetFocusedElement()
        if not elem:
            print(f"[{attempt}] 无焦点元素")
            time.sleep(0.6)
            continue

        print(f"[{attempt}] 焦点元素: {describe(elem)}")

        text, reason = get_text_from_element(elem)
        if text:
            print(f"    ✓ 选中文字 ({len(text)} 字符): {text!r}")
            found = True
            break

        print(f"    ✗ {reason}")

        # 焦点元素不支持时，向上找祖先再试一次（部分控件选区在父容器上）
        try:
            walker = uia.ControlViewWalker
            parent = walker.GetParentElement(elem)
            if parent:
                print(f"    尝试父级: {describe(parent)}")
                text, reason = get_text_from_element(parent)
                if text:
                    print(f"    ✓ 父级取到 ({len(text)} 字符): {text!r}")
                    found = True
                    break
                print(f"    ✗ 父级 {reason}")
        except Exception as e:
            print(f"    父级遍历异常: {type(e).__name__}")

        time.sleep(0.6)

    print("-" * 68)
    if found:
        print("结论：UIA 零剪贴板取词在本机可行。")
        return 0

    print("结论：当前焦点元素未提供可读选区。")
    print("建议：在记事本 / Word 中选中一段文字后重跑本脚本，")
    print("      以区分「实现问题」与「目标控件不支持」。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
