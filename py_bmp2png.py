"""把 32bpp BMP 转 PNG，并打印像素网格用于核对。

注意 RGBA 必须逐像素交错（R,G,B,A,R,G,B,A...），
第一版把所有 RGB 连续写、再跟所有 A，导致 PNG 显示成条纹。
"""

import struct
import sys
import zlib


def bmp_to_png(src, dst):
    d = open(src, "rb").read()
    off = struct.unpack_from("<I", d, 10)[0]
    w = struct.unpack_from("<i", d, 18)[0]
    h = struct.unpack_from("<i", d, 22)[0]
    bpp = struct.unpack_from("<H", d, 28)[0]
    if bpp != 32:
        raise ValueError(f"仅支持 32bpp，实际 {bpp}")
    stride = w * 4

    rows = []
    for y in range(h):
        sy = h - 1 - y            # BMP 自下而上
        s = off + sy * stride
        line = d[s:s + stride]
        row = bytearray()
        for x in range(w):
            b, g, r, a = line[x * 4:x * 4 + 4]
            row += bytes((r, g, b, a))     # 逐像素交错
        rows.append(b"\x00" + bytes(row))

    raw = b"".join(rows)

    def chunk(t, data):
        c = t + data
        return (struct.pack(">I", len(data)) + c
                + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    open(dst, "wb").write(png)
    return w, h


def dump_grid(src, step=2):
    d = open(src, "rb").read()
    off = struct.unpack_from("<I", d, 10)[0]
    w = struct.unpack_from("<i", d, 18)[0]
    h = struct.unpack_from("<i", d, 22)[0]
    stride = w * 4

    def px(x, y):
        sy = h - 1 - y
        s = off + sy * stride + x * 4
        b, g, r, a = d[s:s + 4]
        return r, g, b, a

    print(f"尺寸 {w}x{h}")
    print("\n亮度网格（# 亮 / + 中 / . 透明或暗）:")
    for y in range(0, h, step):
        line = ""
        for x in range(0, w, step):
            r, g, b, a = px(x, y)
            if a < 40:
                line += "."
            else:
                lum = (r * 299 + g * 587 + b * 114) // 1000
                line += "#" if lum > 200 else ("+" if lum > 90 else ":")
        print("  " + line)

    print("\n关键点采样:")
    for (x, y, tag) in [(0, 0, "左上角(应透明)"), (16, 16, "中心"),
                        (16, 9, "第1行文字"), (16, 15, "第2行文字"),
                        (4, 16, "左边缘内")]:
        print(f"  {tag:14} ({x:2},{y:2}) = {px(x, y)}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    src = sys.argv[1] if len(sys.argv) > 1 else "icon_preview.bmp"
    dst = src.rsplit(".", 1)[0] + ".png"
    w, h = bmp_to_png(src, dst)
    print(f"已生成 {dst}  {w}x{h}\n")
    dump_grid(src)
