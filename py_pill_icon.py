# -*- coding: utf-8 -*-
"""浮层按钮图标：用 Windows 自带的图标字体，不手绘、不引图片。

## 为什么用系统图标字体

  1. **风格统一**：参考图的按钮图标就是 Windows 的 Fluent 线性图标，
     直接复用同一套字体，观感与系统原生控件一致。
  2. **矢量清晰**：字体是矢量轮廓，任何 DPI 下都由系统按物理像素栅格化，
     不会被拉伸发虚 —— 这正是本项目花了大力气修掉的 DPI 问题。
  3. **零依赖、零资源文件**：不引 Pillow、不打包 PNG，字体随系统自带。
  4. **可跟随主题变色**：Tk Label 的 fg 直接控制字形颜色。

## 字体选择

优先 `Segoe Fluent Icons`（Win11，线条更细），缺失时回退
`Segoe MDL2 Assets`（Win10 起自带）。两者码位基本一致。

若两者都没有（极简系统），返回 None，调用方退化为纯文字按钮。

## 码位

  E721  搜索（放大镜）
  E8C8  复制（两个叠放矩形）
  E8AC  翻译（双向箭头）
  EA80  解释（灯泡）
"""
import tkinter as tk

# 字体家族，按优先级排列
_ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")

# 按钮 key -> 码位
GLYPHS = {
    "search": "\uE721",
    "translate": "\uE8AC",
    "explain": "\uEA80",
    "copy": "\uE8C8",
}

_resolved = False
_family = None


def available_family(root=None):
    """返回可用的图标字体名，都不可用时返回 None（只探测一次）。"""
    global _resolved, _family
    if _resolved:
        return _family
    _resolved = True

    own_root = False
    if root is None:
        try:
            root = tk._default_root
        except Exception:
            root = None
    if root is None:
        return None

    try:
        from tkinter import font as tkfont
        families = set(tkfont.families(root))
    except Exception:
        return None

    for name in _ICON_FAMILIES:
        if name in families:
            _family = name
            break
    return _family


def reset_cache():
    """测试用：清掉探测缓存。"""
    global _resolved, _family
    _resolved = False
    _family = None


def make_icon_label(parent, key, size_pt=11, color="#ECECEF", bg="#212125",
                    root=None):
    """建一个只含图标字形的 Label；字体不可用时返回 None。"""
    fam = available_family(root or parent)
    glyph = GLYPHS.get(key)
    if not fam or not glyph:
        return None
    return tk.Label(parent, text=glyph, fg=color, bg=bg,
                    font=(fam, size_pt), bd=0, padx=0, pady=0,
                    takefocus=0, cursor="hand2")


NAMES = tuple(GLYPHS)
