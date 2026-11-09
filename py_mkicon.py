# -*- coding: utf-8 -*-
"""建图标：全部尺寸从 1024 原图面积平均缩放 + 小尺寸 alpha 补偿。

## 为什么放弃手绘小尺寸

之前做了两版手绘（8 个独立矩形的括号 / 连续路径描边），判断依据是
"16-24px 缩放后糊成噪点"。实际上那个判断是**错的** —— 被检查脚本
的最近邻放大骗了：16px 放大 6 倍显示时，每个原始像素行被复制成
6 条同色横带，满屏条纹看起来就像糊了。

改用双线性放大复查 ab_16/20/24/32.png，逐尺寸并排对比后结论很明确：
**纯缩放在每个尺寸上都比手绘好**，手绘版蓝框太粗、把"I 形光标 +
三条线"的语义压掉了。缩放版能看出括弧、I 形光标、横线三件套。

所以小尺寸不再手绘，改为缩放 + `py_icon.boost_alpha` 补一道 alpha
对比度拉伸，把 64:1 面积平均稀释掉的细笔画抬回来。

## 尺寸清单

Windows 图标常用档位。16/20/24 是任务栏与标题栏，32/48 是桌面与
资源管理器，64+ 给高分屏和大图预览。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import py_icon

HERE = os.path.dirname(os.path.abspath(__file__))
# 源图放在仓库内的 assets/，保证别人 clone 下来也能重建图标。
SRC = os.path.join(HERE, "assets", "icon_source.png")
PREV = os.path.join(HERE, "icons", "preview")

ALL = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def save_png(rgba, w, h, path):
    import struct
    import zlib
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        off = y * w * 4
        raw.extend(rgba[off:off + w * 4])

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


def main():
    os.makedirs(PREV, exist_ok=True)

    print("=" * 70)
    print("图标构建：全尺寸缩放 + 小尺寸 alpha 补偿")
    print("=" * 70)

    if not os.path.exists(SRC):
        print(f"\n源图不存在: {SRC}")
        return 1

    w, h, big = py_icon.decode_png(open(SRC, "rb").read())
    cleared = py_icon.flood_transparent_bg(big, w, h, tol=16)
    print(f"\n原图 {w}x{h}，抠掉背景 {cleared} 像素")

    sized = []
    for s in ALL:
        rgba = py_icon.resize_for_icon(big, w, h, s)
        sized.append((s, rgba))
        save_png(rgba, s, s, os.path.join(PREV, f"icon_{s}.png"))
        opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i] > 128)
        note = "  +alpha" if s <= 32 else ""
        print(f"  {s:>3}px  不透明 {opaque:>5}/{s * s:<6}"
              f"({opaque * 100.0 / (s * s):5.1f}%){note}")

    ico = py_icon.build_ico(sized)
    out = os.path.join(HERE, "popdict.ico")
    open(out, "wb").write(ico)
    print(f"\n-> {out}  {len(ico)} 字节  {len(sized)} 尺寸")
    return 0


if __name__ == "__main__":
    sys.exit(main())
