# -*- coding: utf-8 -*-
"""浮层按钮图标回归测试。

背景：浮层原来只有四个纯文字按钮，后来改成「线框图标 + 文字」。
这个改动踩过两个坑，都必须由本测试长期看住：

  1. 图标字形画不出来 —— 最初手绘矢量图标时，self._name 被 Tkinter
     内部覆盖成 widget 名（如 !lineicon），导致 getattr(self, "_icon_" +
     self._name) 全部失效，四个图标 items=0，肉眼只看到空白。
     现在改用系统图标字体，字形就是一个字符，不存在这个问题，
     但仍要断言「图标 Label 真的建出来了」。

  2. 按钮布局被挤爆 —— _measure 原来是 4 个等宽 64px 槽位。
     加了图标后单个按钮变宽，固定槽位会把「AI 搜索」裁掉并与右侧
     按钮的图标重叠。现在改为按 winfo_reqwidth() 流式排布，
     需要断言「四个按钮互不重叠，且最后一个没超出浮层右边界」。

另外断言悬停与点击对图标同样生效 —— 事件只绑在文字 Label 上时，
鼠标落在图标上按钮不亮、点了也没反应，这是很容易漏的地方。

运行：py -3 py_pill_icon_test.py
"""

import sys
import tkinter as tk

import py_dpi
import py_theme

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def main():
    py_theme.set_mode("dark")
    root = tk.Tk()
    root.withdraw()

    import py_pill_icon
    import py_pop

    print("=" * 68)
    print("1. 图标字体探测")
    print("=" * 68)
    py_pill_icon.reset_cache()
    fam = py_pill_icon.available_family(root)
    check("探测到系统图标字体", fam is not None, fam or "未找到，将退化为纯文字")
    if fam:
        check("字体名属于已知候选", fam in py_pill_icon._ICON_FAMILIES, fam)
    check("四个动作都有对应码位", set(py_pill_icon.GLYPHS) ==
          {"search", "translate", "explain", "copy"},
          ",".join(sorted(py_pill_icon.GLYPHS)))

    print()
    print("=" * 68)
    print("2. 图标 Label 真的建出来了")
    print("=" * 68)
    hits = []
    pill = py_pop.Pill(root, lambda k: hits.append(k))
    root.update_idletasks()

    n_icon = sum(1 for g in pill._icon_widgets if g is not None)
    if fam:
        check("四个图标 Label 全部创建", n_icon == 4, f"{n_icon}/4")
    else:
        check("字体不可用时全部退化为 None", n_icon == 0, "预期，不算失败")

    if fam:
        texts = [g.cget("text") for g in pill._icon_widgets if g is not None]
        check("字形都是私用区单字符",
              all(len(t) == 1 and 0xE000 <= ord(t) <= 0xF8FF for t in texts),
              " ".join(f"U+{ord(t):04X}" for t in texts))
        # 四个图标不能是同码位，否则全画成一样
        check("四个图标互不相同", len(set(texts)) == 4, f"{len(set(texts))} 种")
        # 字号不能用 cget("font").split()[1] 取 —— "Segoe Fluent Icons 11"
        # 这类字体名本身带空格，第二段是 "Fluent" 不是数字。
        from tkinter import font as tkfont
        sizes = [tkfont.Font(root=root, font=g.cget("font")).cget("size")
                 for g in pill._icon_widgets if g is not None]
        check("图标字号为正", all(s > 0 for s in sizes),
              " ".join(str(s) for s in sizes))

    print()
    print("=" * 68)
    print("3. 布局：四按钮互不重叠，未越界")
    print("=" * 68)
    boxes = []
    for b in pill._buttons:
        boxes.append((b.winfo_x(), b.winfo_x() + b.winfo_reqwidth(),
                      b.winfo_reqwidth()))
    overlap = []
    for i in range(len(boxes) - 1):
        if boxes[i][1] > boxes[i + 1][0]:
            overlap.append(f"#{i}->#{i+1}")
    check("相邻按钮无重叠", not overlap, ",".join(overlap) or "无")
    check("按钮从左侧内边距开始", boxes[0][0] == py_dpi.px(6), f"x={boxes[0][0]}")
    check("最后一个按钮未超出浮层右边界",
          boxes[-1][1] <= pill.pill_w, f"右缘 {boxes[-1][1]} <= 宽 {pill.pill_w}")
    check("浮层高度能容纳按钮",
          pill.pill_h > 0 and pill.pill_h >= py_dpi.px(8) + 10,
          f"{pill.pill_w}x{pill.pill_h}")
    check("每个按钮宽度都大于 0（没有被裁成 0）",
          all(b[2] > 0 for b in boxes), " ".join(str(b[2]) for b in boxes))

    print()
    print("=" * 68)
    print("4. 悬停：容器与图标、文字一起换底色")
    print("=" * 68)
    holder = pill._buttons[0]
    kids = holder.winfo_children()
    pill._hover(holder, True)
    on_bgs = {holder.cget("bg")} | {c.cget("bg") for c in kids}
    check("悬停时容器与子件底色一致（单色块）", len(on_bgs) == 1,
          ",".join(sorted(on_bgs)))
    check("悬停底色等于 BG4", holder.cget("bg") == py_theme.BG4, holder.cget("bg"))
    pill._hover(holder, False)
    off_bgs = {holder.cget("bg")} | {c.cget("bg") for c in kids}
    check("离开后底色恢复一致", len(off_bgs) == 1, ",".join(sorted(off_bgs)))
    check("离开后底色等于 BG2", holder.cget("bg") == py_theme.BG2, holder.cget("bg"))

    print()
    print("=" * 68)
    print("5. 点击：落在图标上也要触发对应动作")
    print("=" * 68)
    # 先做绑定检查（不依赖窗口可见）：每个子件都必须绑了 <Button-1>。
    # 这一层比行为测试更早地暴露「事件只绑在文字 Label 上」的问题。
    unbound = []
    for i, holder in enumerate(pill._buttons):
        for w in (holder,) + tuple(holder.winfo_children()):
            if not w.bind("<Button-1>"):
                unbound.append(f"#{i}:{w.winfo_class()}")
    check("容器与每个子件都绑了点击", not unbound, ",".join(unbound) or "全部已绑")

    # 行为验证：Tk 对 withdraw() 的窗口不派发合成事件（拿不到焦点），
    # 所以必须先把浮层显示出来，否则会得到「0 次触发」的假失败。
    pill.show_at(300, 300, "测试")
    root.update()
    expect = ["search", "translate", "explain", "copy"]
    hits.clear()
    for i, holder in enumerate(pill._buttons):
        # 点图标子件（若有），验证事件没有只绑在文字上
        target = pill._icon_widgets[i] or holder
        target.event_generate("<Button-1>", x=1, y=1)
        root.update()
    got = list(hits)
    check("四个按钮各自触发一次", len(got) == 4, f"{len(got)} 次")
    check("动作顺序正确", got == expect, ",".join(got) or "无")
    pill.win.withdraw()
    root.update()

    print()
    print("=" * 68)
    print("6. 浅色主题：图标颜色跟随翻转")
    print("=" * 68)
    # 切换主题会改模块级常量，所以两套值必须在切换前后各自抓下来再比。
    dark_fg, dark_bg = py_theme.FG, py_theme.BG2
    py_theme.set_mode("light")
    light_fg, light_bg = py_theme.FG, py_theme.BG2
    check("两套主题前景色不同", dark_fg != light_fg, f"{dark_fg} -> {light_fg}")

    def lum(hexcolor):
        h = hexcolor.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return 0.299 * r + 0.587 * g + 0.114 * b

    check("深色主题前景比背景亮", lum(dark_fg) > lum(dark_bg),
          f"fg={dark_fg}({lum(dark_fg):.0f}) > bg={dark_bg}({lum(dark_bg):.0f})")
    check("浅色主题前景比背景暗", lum(light_fg) < lum(light_bg),
          f"fg={light_fg}({lum(light_fg):.0f}) < bg={light_bg}({lum(light_bg):.0f})")
    py_theme.set_mode("dark")

    print()
    print("=" * 68)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 68)
    try:
        pill.win.destroy()
        root.destroy()
    except tk.TclError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
