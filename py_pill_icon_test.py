# -*- coding: utf-8 -*-
"""浮层按钮图标与布局回归测试。

覆盖三层：

  1. **图标字体**：探测到系统图标字体、四个码位齐备且互不相同
  2. **布局**：四个按钮互不重叠、不越界、尺寸合理
  3. **交互**：悬停高亮、点击触发动作、双主题配色方向

按钮实现已从 `Frame + 2×Label` 改为单个 `Canvas`
（图标与文字都是 `create_text` 画的），所以断言方式也跟着变：

  · 原来查 `Label.cget("text")` 拿字形，现在查 `canvas.itemcget(id, "text")`
  · 原来查 `Frame.winfo_children()`，现在查 `canvas.find_all()`
  · 悬停不再是改 bg，而是给底色 item 设 image —— 单独由
    `py_pill_hover_test.py` 用像素级断言验证

历史教训（写在测试里防止再犯）：
  · 手绘矢量图标时 `self._name` 被 Tkinter 覆盖成 widget 名，
    导致 `getattr(self, "_icon_" + self._name)` 全部失效，四个图标
    一个都画不出来。现在字形就是一个字符，不存在属性冲突。
  · 早期 `_measure` 用 4 个等宽 64px 槽位，加图标后按钮变宽，
    固定槽位把「AI 搜索」裁掉并与右侧按钮的图标重叠。

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
    print("2. 图标真的画进 Canvas 了")
    print("=" * 68)
    hits = []
    pill = py_pop.Pill(root, lambda k: hits.append(k))
    root.update_idletasks()

    n_icon = sum(1 for g in pill._icon_widgets if g is not None)
    if fam:
        check("四个图标的 item id 全部存在", n_icon == 4, f"{n_icon}/4")
    else:
        check("字体不可用时全部退化为 None", n_icon == 0, "预期，不算失败")

    if fam:
        glyphs = []
        for canvas, _bg, icon_item, text_item in pill._button_items:
            if icon_item is None:
                continue
            glyphs.append(canvas.itemcget(icon_item, "text"))
        check("字形都是私用区单字符",
              all(len(t) == 1 and 0xE000 <= ord(t) <= 0xF8FF for t in glyphs),
              " ".join(f"U+{ord(t):04X}" for t in glyphs))
        check("四个图标互不相同", len(set(glyphs)) == 4,
              f"{len(set(glyphs))} 种")

    # 每个按钮应该是单个 Canvas（不是 Frame + 多个 Label）
    classes = {b.winfo_class() for b in pill._buttons}
    check("按钮是 Canvas（圆角高亮需要贴图，Frame 做不到）",
          classes == {"Canvas"}, ",".join(sorted(classes)))
    item_counts = [len(b.find_all()) for b in pill._buttons]
    check("每个按钮的 item 数一致（底图 + 图标 + 文字）",
          len(set(item_counts)) == 1, f"{item_counts}")

    print()
    print("=" * 68)
    print("3. 布局：四按钮互不重叠，未越界")
    print("=" * 68)
    boxes = [(b.winfo_x(), b.winfo_x() + b.winfo_reqwidth(),
              b.winfo_reqwidth(), b.winfo_reqheight())
             for b in pill._buttons]
    overlap = [f"#{i}->#{i+1}" for i in range(len(boxes) - 1)
               if boxes[i][1] > boxes[i + 1][0]]
    check("相邻按钮无重叠", not overlap, ",".join(overlap) or "无")
    # 不再断言具体的 px(6)：间距改由黄金比例体系从字号推导后，
    # 具体像素会随字号变化，"等于某个定值"是伪约束。
    # 真正该保证的是左右留白对称、且浮层没有多余空隙。
    left_pad = boxes[0][0]
    right_pad = pill.pill_w - boxes[-1][1]
    check("左右留白对称", abs(left_pad - right_pad) <= 1,
          f"左 {left_pad} vs 右 {right_pad}")
    check("左侧留白是正数且不过大", 0 < left_pad <= py_dpi.px(12),
          f"x={left_pad}")
    check("最后一个按钮未超出浮层右边界",
          boxes[-1][1] <= pill.pill_w, f"右缘 {boxes[-1][1]} <= 宽 {pill.pill_w}")
    check("浮层高度能容纳按钮",
          pill.pill_h > 0 and pill.pill_h >= py_dpi.px(8) + 10,
          f"{pill.pill_w}x{pill.pill_h}")
    check("每个按钮宽高都大于 0（没有被裁成 0）",
          all(b[2] > 0 and b[3] > 0 for b in boxes),
          " ".join(f"{b[2]}x{b[3]}" for b in boxes))
    check("四个按钮高度一致", len({b[3] for b in boxes}) == 1,
          str({b[3] for b in boxes}))

    print()
    print("=" * 68)
    print("4. 点击：落在图标或文字上都触发对应动作")
    print("=" * 68)
    # 绑定检查（不依赖窗口可见）
    unbound = [f"#{i}" for i, b in enumerate(pill._buttons)
               if not b.bind("<Button-1>")]
    check("四个按钮都绑定了点击", not unbound, ",".join(unbound) or "全部已绑")

    # 行为验证：Tk 对 withdraw() 的窗口不派发合成事件，必须先显示
    pill.show_at(300, 300, "测试")
    root.update()
    expect = ["search", "translate", "explain", "copy"]
    hits.clear()
    for b in pill._buttons:
        b.event_generate("<Button-1>", x=2, y=2)
        root.update()
    got = list(hits)
    check("四个按钮各自触发一次", len(got) == 4, f"{len(got)} 次")
    check("动作顺序正确", got == expect, ",".join(got) or "无")
    pill.win.withdraw()
    root.update()

    print()
    print("=" * 68)
    print("5. 主题：颜色常量与切换后的刷新")
    print("=" * 68)
    dark_fg, dark_bg = py_theme.FG, py_theme.BG2
    py_theme.set_mode("light")
    light_fg, light_bg = py_theme.FG, py_theme.BG2
    check("两套主题前景色不同", dark_fg != light_fg, f"{dark_fg} -> {light_fg}")

    def lum(hx):
        h = hx.lstrip("#")
        return (0.299 * int(h[0:2], 16) + 0.587 * int(h[2:4], 16) +
                0.114 * int(h[4:6], 16))

    check("深色主题前景比背景亮", lum(dark_fg) > lum(dark_bg),
          f"{dark_fg}({lum(dark_fg):.0f}) > {dark_bg}({lum(dark_bg):.0f})")
    check("浅色主题前景比背景暗", lum(light_fg) < lum(light_bg),
          f"{light_fg}({lum(light_fg):.0f}) < {light_bg}({lum(light_bg):.0f})")

    # apply_theme 必须把 Canvas 里的文字 fill 也改掉 ——
    # recolor_tree 只看 widget，碰不到 Canvas item
    old = dict(py_theme.colors())
    py_theme.set_mode("dark")
    new = dict(py_theme.colors())
    pill.apply_theme(old, new)
    root.update()
    fills = []
    for canvas, _bg, icon_item, text_item in pill._button_items:
        fills.append(canvas.itemcget(text_item, "fill").lower())
        if icon_item is not None:
            fills.append(canvas.itemcget(icon_item, "fill").lower())
    want = py_theme.FG.lower()
    check("切换主题后 Canvas 内文字与图标 fill 已刷新",
          all(f == want for f in fills), f"期望 {want}，实际 {set(fills)}")

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
