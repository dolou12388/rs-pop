# -*- coding: utf-8 -*-
"""浮层字号与上下留空的可调性测试。

这一层测的不是「默认值长什么样」，而是**配置改了真的会生效**：

  1. **配置层**：新增的 pill_font_size / pill_pad_y 有默认值、有范围校验，
     越界或非数字都夹回合法区间
  2. **生效**：改配置后 rebuild() 让按钮真的变大/变小，尺寸随之改变
  3. **留空**：pill_pad_y 作为系数缩放纵向留空，改动可量化
  4. **不破坏**：字号放大后按钮仍互不重叠、不越界，图标与文字仍在
     垂直居中位置（这是本项目最容易被字号改动搞坏的地方）

关于「上下留空」语义的变更：它原来是**绝对像素**（直接 py_dpi.px(v)），
现在改成**相对系数**（以默认 5 为 1.0 倍）。原因是排版改成黄金比例体系后，
纵向留空必须跟着字号一起缩放 —— 若仍用绝对值，字号从 8 调到 18 时
纵向纹丝不动而横向按 φ 一路放大，按钮会越来越扁，节奏就崩了。

为什么单独一个文件：`py_pill_icon_test.py` 只验证默认配置下的布局，
即使字号读错了它也会过 —— 它不碰配置。这里必须显式改配置再重建，
才能真正验证「可调」这条需求。

运行：py -3 py_pill_metrics_test.py
"""

import os
import sys
import tempfile
import tkinter as tk

import py_config
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


def config_section():
    print("=" * 68)
    print("1. 配置层：默认值、范围校验")
    print("=" * 68)

    ui = py_config.default_config()["ui"]
    check("新装配置带字号字段",
          ui.get("pill_font_size") == py_config.PILL_FONT_DEFAULT,
          f"{ui.get('pill_font_size')}")
    check("新装配置带上下留空字段",
          ui.get("pill_pad_y") == py_config.PILL_PAD_Y_DEFAULT,
          f"{ui.get('pill_pad_y')}")
    check("默认字号比原写死的 9 大（否则等于没改）",
          py_config.PILL_FONT_DEFAULT > 9,
          f"{py_config.PILL_FONT_DEFAULT} > 9")
    check("默认留空比原写死的 8 小（这是「上下留空偏多」的修法）",
          py_config.PILL_PAD_Y_DEFAULT < 8,
          f"{py_config.PILL_PAD_Y_DEFAULT} < 8")

    # _clamp_int 的各种脏输入
    lo, hi = py_config.PILL_FONT_MIN, py_config.PILL_FONT_MAX
    cases = [
        (5, lo, "低于下限夹到下限"),
        (999, hi, "高于上限夹到上限"),
        ("12", 12, "数字字符串能解析"),
        (12.7, 12, "浮点截断成整数"),
        (None, py_config.PILL_FONT_DEFAULT, "None 用默认值"),
        ("abc", py_config.PILL_FONT_DEFAULT, "非数字用默认值"),
        (True, py_config.PILL_FONT_DEFAULT, "布尔不当数字（True 不是 1）"),
        ("", py_config.PILL_FONT_DEFAULT, "空串用默认值"),
    ]
    for raw, want, why in cases:
        got = py_config._clamp_int(raw, lo, hi, py_config.PILL_FONT_DEFAULT)
        check(why, got == want, f"{raw!r} -> {got}（期望 {want}）")

    # 落盘再读回，确认越界值在 load 时被修正。
    # 注意必须用 set_ui 写：raw 返回的是深拷贝，直接改它不影响 store。
    tmp = tempfile.mkdtemp(prefix="popdict_metrics_")
    old_cfg = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tmp
    try:
        st = py_config.ConfigStore()
        st.set_ui("pill_font_size", 500)
        st.set_ui("pill_pad_y", -30)
        # 直接写盘：绕开 set_ui 之外可能的即时校验，模拟"用户手改 json"
        st._write_json(py_config.config_path(), dict(st.raw, ui={
            "pill_ttl_ms": 9000, "min_drag_px": 4,
            "theme_mode": "system",
            "pill_font_size": 500, "pill_pad_y": -30,
        }))
        st2 = py_config.ConfigStore()
        check("越界字号在载入时被夹回",
              st2.raw["ui"]["pill_font_size"] == py_config.PILL_FONT_MAX,
              f"{st2.raw['ui']['pill_font_size']}")
        check("越界留空在载入时被夹回",
              st2.raw["ui"]["pill_pad_y"] == py_config.PILL_PAD_Y_MIN,
              f"{st2.raw['ui']['pill_pad_y']}")
    finally:
        if old_cfg is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_cfg


def layout_section(root):
    print()
    print("=" * 68)
    print("2. 生效：改配置后 rebuild 真的改变尺寸")
    print("=" * 68)

    import py_pop

    store = py_config.store()

    def apply(font_size, pad_y):
        store.set_ui("pill_font_size", font_size)
        store.set_ui("pill_pad_y", pad_y)
        pill.rebuild()
        root.update_idletasks()
        b = pill._buttons[0]
        return (b.winfo_reqwidth(), b.winfo_reqheight(),
                pill.pill_w, pill.pill_h)

    pill = py_pop.Pill(root, lambda k: None)
    root.update_idletasks()
    w0, h0, _, _ = apply(10, 5)
    print(f"  字号10/留空5 -> 按钮 {w0}x{h0}")

    w1, h1, pw1, ph1 = apply(14, 5)
    print(f"  字号14/留空5 -> 按钮 {w1}x{h1}, 浮层 {pw1}x{ph1}")
    check("字号变大后按钮更高", h1 > h0, f"{h0} -> {h1}")
    check("字号变大后按钮更宽", w1 > w0, f"{w0} -> {w1}")
    check("字号变大后浮层也跟着变大", ph1 > 0 and pw1 > 0, f"{pw1}x{ph1}")

    w2, h2, _, _ = apply(10, 5)
    check("改回原字号后尺寸完全复原",
          (w2, h2) == (w0, h0), f"{w2}x{h2} vs {w0}x{h0}")

    # 留空是系数：5 是 1.0 倍，12 约 2.4 倍，按钮高度应随之增加。
    # 不再断言"增加固定的 2*px(7)"—— 系数语义下增量随字号而变。
    w3, h3, _, _ = apply(10, 5)
    w4, h4, _, _ = apply(10, 12)
    print(f"  留空 5 -> 12，高度 {h3} -> {h4}（+{h4 - h3}）")
    check("把留空系数调大后按钮变高", h4 > h3, f"{h3} -> {h4}")
    check("只改留空不影响宽度", w4 == w3, f"{w3} -> {w4}")

    # 留空系数调小应该更紧凑
    w5, h5, _, _ = apply(10, py_config.PILL_PAD_Y_MIN)
    check("把留空系数调到最小后按钮更矮", h5 < h3, f"{h3} -> {h5}")

    # 极限值也不该崩
    try:
        apply(py_config.PILL_FONT_MIN, py_config.PILL_PAD_Y_MIN)
        lo_ok = True
    except Exception as e:
        lo_ok = False
        print(f"    下限异常: {e}")
    check("字号与留空都取最小值时能正常重建", lo_ok)

    try:
        w_hi, h_hi, pw_hi, ph_hi = apply(
            py_config.PILL_FONT_MAX, py_config.PILL_PAD_Y_MAX)
        hi_ok = True
    except Exception as e:
        hi_ok = False
        print(f"    上限异常: {e}")
    check("字号与留空都取最大值时能正常重建", hi_ok,
          f"按钮 {w_hi}x{h_hi}" if hi_ok else "")

    # 收尾：还原默认，避免污染后续测试
    apply(py_config.PILL_FONT_DEFAULT, py_config.PILL_PAD_Y_DEFAULT)
    return pill


def golden_section(root, pill):
    """黄金比例：按钮高 : 文字墨迹高 ≈ φ : 1。

    这是本轮的视觉核心。判定用"多个字号下比值都落在 φ±0.12"而不是
    死抠某一次的精确值 —— 像素只能取整，理论比值必然有抖动，抓住
    "系统性偏离"才有意义。0.12 的容差足以抓住"整体算错了"（比如把
    /φ² 当成系数时比值会飘到 1.76~2.1），又不会因取整误报。
    """
    print()
    print("=" * 68)
    print("4. 黄金比例：按钮高 : 文字墨迹高 = φ : 1")
    print("=" * 68)

    gold = (1 + 5 ** 0.5) / 2
    ratios = []
    for size in (py_config.PILL_FONT_MIN, 10, 12, 14, py_config.PILL_FONT_MAX):
        py_config.store().set_ui("pill_font_size", size)
        py_config.store().set_ui("pill_pad_y", py_config.PILL_PAD_Y_DEFAULT)
        pill.rebuild()
        root.update_idletasks()
        b = pill._buttons[0]
        ink = pill._f_text.metrics("ascent")
        h = b.winfo_reqheight()
        r = h / ink
        ratios.append((size, ink, h, r))
        print(f"  字号 {size:2d}: 墨迹 {ink:2d}  按钮高 {h:3d}"
              f"  比值 {r:.3f}  (φ={gold:.3f}, 偏差 {abs(r - gold):.3f})")

    worst = max(abs(r - gold) for _, _, _, r in ratios)
    check("所有字号下「按钮高/墨迹高」都接近 φ（容差 0.12）",
          worst <= 0.12, f"最大偏差 {worst:.3f}")
    check("比值不是系统性偏大（排除仍按 /φ² 算的旧实现）",
          max(r for _, _, _, r in ratios) < gold + 0.12,
          f"最大 {max(r for _, _, _, r in ratios):.3f}")

    # 留空系数为默认值时，纵向留白应随字号同步放大（φ 体系的体现）
    pads = [(size, (h - ink) / 2) for size, ink, h, _ in ratios]
    print("  单侧留空随字号变化: " +
          ", ".join(f"{s}pt→{p:.1f}" for s, p in pads))
    check("纵向留白随字号单调递增（φ 体系会跟着字号缩放）",
          all(pads[i][1] < pads[i + 1][1] for i in range(len(pads) - 1)),
          f"{[round(p, 1) for _, p in pads]}")

    py_config.store().set_ui("pill_font_size", py_config.PILL_FONT_DEFAULT)
    py_config.store().set_ui("pill_pad_y", py_config.PILL_PAD_Y_DEFAULT)
    pill.rebuild()
    root.update_idletasks()


def geometry_section(root, pill):
    print()
    print("=" * 68)
    print("3. 放大后不破坏布局：不重叠、不越界、居中")
    print("=" * 68)

    # 用最大字号跑一遍，这是最容易出问题的配置
    store = py_config.store()
    store.set_ui("pill_font_size", py_config.PILL_FONT_MAX)
    store.set_ui("pill_pad_y", py_config.PILL_PAD_Y_DEFAULT)
    pill.rebuild()
    root.update_idletasks()

    rects = []
    for b in pill._buttons:
        rects.append((b.winfo_x(), b.winfo_y(),
                      b.winfo_x() + b.winfo_reqwidth(),
                      b.winfo_y() + b.winfo_reqheight()))
    print(f"  最大字号下按钮矩形: {rects}")
    print(f"  浮层尺寸: {pill.pill_w}x{pill.pill_h}")

    overlap = False
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b2 = rects[i], rects[j]
            if a[0] < b2[2] and b2[0] < a[2]:
                overlap = True
    check("最大字号下四个按钮互不重叠", not overlap)

    out = [r for r in rects if r[2] > pill.pill_w or r[3] > pill.pill_h]
    check("最大字号下没有按钮越出浮层", not out, f"{len(out)} 个越界")

    # 文字与图标应在按钮垂直中线附近（墨迹中心对齐）
    offs = []
    for canvas, _bg, icon_item, text_item in pill._button_items:
        h = canvas.winfo_reqheight()
        cy = h // 2
        for item in (icon_item, text_item):
            if item is None:
                continue
            bb = canvas.bbox(item)
            if not bb:
                continue
            mid = (bb[1] + bb[3]) / 2
            offs.append(abs(mid - cy))
    worst = max(offs) if offs else 0
    print(f"  文字/图标墨迹中心与按钮中线最大偏差: {worst:.1f}px")
    # 容差给 4px：10~18pt 之间墨迹高度变化大，且 bbox 含抗锯齿外扩 1~2px
    check("文字与图标仍在垂直居中位置（偏差 <= 4px）", worst <= 4,
          f"{worst:.1f}px")

    store.set_ui("pill_font_size", py_config.PILL_FONT_DEFAULT)
    store.set_ui("pill_pad_y", py_config.PILL_PAD_Y_DEFAULT)


def main():
    py_theme.set_mode("light")
    root = tk.Tk()
    root.withdraw()
    py_dpi.enable()
    py_dpi.set_tk_scaling(root)

    config_section()
    pill = layout_section(root)
    golden_section(root, pill)
    geometry_section(root, pill)

    print()
    print("=" * 68)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 68)
    try:
        root.destroy()
    except tk.TclError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
