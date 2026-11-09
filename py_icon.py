# -*- coding: utf-8 -*-
"""零依赖 PNG 解码 / 缩放 / ICO 打包。

## 为什么不用 Pillow

只为做一张图标就引入 Pillow（约 10MB 安装包、运行时多 3-4MB 内存）
不值当。PNG 解码用标准库 zlib 就够，ICO 是个很简单的容器格式。

## PNG 解码需要处理的

  · 8 位、颜色类型 2(RGB) / 6(RGBA)、非交错 —— 图标素材基本都落在这里
  · 5 种行过滤器（None/Sub/Up/Average/Paeth），必须逐个实现，
    少一个就会解出花屏。这是手写 PNG 解码最容易漏的地方。
  · idat 可能有多块，要拼起来再解压

## 缩放用面积平均而不是最近邻

图标从 1024 缩到 16 是 64 倍缩小，最近邻会丢掉 4095/4096 的像素，
细笔画（比如括号的圆弧）会整段消失或断裂。面积平均（box filter）
对每个目标像素覆盖的源区域求平均，缩小时不会丢结构。

## ICO 容器

每个尺寸一项，项内数据用 BMP DIB（32 位 BGRA + AND 掩码）。
不用 PNG 压缩项 —— 虽然 Vista+ 支持，但 <=48px 用 DIB 兼容性最好，
避免个别老组件（如某些 shell 扩展）读不出来。
"""

import struct
import zlib

# ---------- PNG 解码 ----------

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data):
    """解 PNG，返回 (width, height, bytearray RGBA, 每像素 4 字节)。"""
    if data[:8] != PNG_SIG:
        raise ValueError("不是 PNG 文件")

    off = 8
    w = h = bit_depth = color_type = interlace = None
    idat = bytearray()
    palette = None
    trns = None

    while off < len(data):
        if off + 8 > len(data):
            break
        ln = struct.unpack(">I", data[off:off + 4])[0]
        tag = data[off + 4:off + 8]
        body = data[off + 8:off + 8 + ln]
        off += 12 + ln

        if tag == b"IHDR":
            w, h, bit_depth, color_type, _cm, _fl, interlace = \
                struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat.extend(body)
        elif tag == b"IEND":
            break

    if w is None:
        raise ValueError("缺 IHDR")
    if bit_depth != 8:
        raise ValueError(f"只支持 8 位色深，实际 {bit_depth}")
    if interlace:
        raise ValueError("不支持交错 PNG")

    # 每像素字节数与颜色通道数
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"不支持颜色类型 {color_type}")

    raw = zlib.decompress(bytes(idat))
    stride = w * channels
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    pos = 0

    for y in range(h):
        if pos >= len(raw):
            break
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(line) < stride:
            line.extend(b"\x00" * (stride - len(line)))

        # 反过滤
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            raise ValueError(f"未知行过滤器 {ftype}")

        # 转 RGBA
        base = y * w * 4
        if color_type == 2:
            for x in range(w):
                si = x * 3
                di = base + x * 4
                out[di] = line[si]
                out[di + 1] = line[si + 1]
                out[di + 2] = line[si + 2]
                out[di + 3] = 255
        elif color_type == 6:
            out[base:base + w * 4] = line
        elif color_type == 0:
            for x in range(w):
                v = line[x]
                di = base + x * 4
                out[di] = out[di + 1] = out[di + 2] = v
                out[di + 3] = 255
        elif color_type == 4:
            for x in range(w):
                v = line[x * 2]
                di = base + x * 4
                out[di] = out[di + 1] = out[di + 2] = v
                out[di + 3] = line[x * 2 + 1]
        elif color_type == 3:
            for x in range(w):
                idx = line[x]
                di = base + x * 4
                pi = idx * 3
                if palette and pi + 2 < len(palette):
                    out[di] = palette[pi]
                    out[di + 1] = palette[pi + 1]
                    out[di + 2] = palette[pi + 2]
                a = 255
                if trns and idx < len(trns):
                    a = trns[idx]
                out[di + 3] = a

        prev = line

    return w, h, out


# ---------- 缩放 ----------

def resize(rgba, w, h, nw, nh):
    """面积平均缩放。返回新 RGBA bytearray。

    预先累加 alpha 加权色值，避免透明像素的黑色把边缘染黑
    （直接平均 RGB 会在抗锯齿边缘产生黑边，是很常见的坑）。
    """
    out = bytearray(nw * nh * 4)
    x_ratio = w / nw
    y_ratio = h / nh

    for ty in range(nh):
        y0 = int(ty * y_ratio)
        y1 = max(y0 + 1, int((ty + 1) * y_ratio))
        if y1 > h:
            y1 = h
        for tx in range(nw):
            x0 = int(tx * x_ratio)
            x1 = max(x0 + 1, int((tx + 1) * x_ratio))
            if x1 > w:
                x1 = w
            sa = sr = sg = sb = 0.0
            n = 0
            for sy in range(y0, y1):
                rowi = sy * w * 4
                for sx in range(x0, x1):
                    i = rowi + sx * 4
                    a = rgba[i + 3]
                    sa += a
                    sr += rgba[i] * a
                    sg += rgba[i + 1] * a
                    sb += rgba[i + 2] * a
                    n += 1
            di = (ty * nw + tx) * 4
            if n == 0:
                continue
            if sa > 0:
                out[di] = int(sr / sa)
                out[di + 1] = int(sg / sa)
                out[di + 2] = int(sb / sa)
                out[di + 3] = int(sa / n)
            else:
                out[di + 3] = 0
    return out


# ---------- 小尺寸补偿 ----------

def boost_alpha(rgba, w, h, gamma=2.0, lo=0.28):
    """小尺寸 alpha 锐化：把被面积平均"稀释"的细笔画补回来。

    1024 -> 16 是 64:1 的面积平均，一根 2px 宽的笔画在这里只覆盖了
    零星的采样，alpha 会被平均成很淡的灰。做一次 gamma 式提亮：
    高于 lo 的 alpha 抬向 255，低于 lo 的压向 0 —— 相当于在 alpha
    通道上做对比度拉伸，让笔画"立起来"而不影响已完全不透明的区域。

    只对小尺寸调用（<=32）。大尺寸不需要，反而会把抗锯齿边缘弄硬。
    """
    lut = []
    for v in range(256):
        f = v / 255.0
        if f <= lo:
            nv = 0.0
        else:
            nv = ((f - lo) / (1.0 - lo)) ** (1.0 / gamma)
        lut.append(int(max(0, min(255, nv * 255))))
    for i in range(3, len(rgba), 4):
        a = rgba[i]
        if a:
            rgba[i] = lut[a]


def resize_for_icon(rgba, w, h, size):
    """缩放 + 小尺寸 alpha 补偿，图标小尺寸统一走这里。"""
    out = resize(rgba, w, h, size, size)
    if size <= 32:
        boost_alpha(out, size, size, gamma=2.0,
                    lo=0.30 if size <= 20 else 0.20)
    return out


# ---------- 圆角透明 ----------

def round_corners_transparent(rgba, w, h, radius_ratio=0.22):
    """把方形的四个角抠成透明，做出圆角方块。

    用超采样算覆盖率（每像素 4x4 采样），边缘才有抗锯齿过渡；
    直接按像素中心判断会在圆弧上留下明显阶梯。
    """
    r = radius_ratio * min(w, h)
    sub = 4
    for y in range(h):
        for x in range(w):
            # 判断是否落在四角的圆外
            cx = min(x + 0.5, w - x - 0.5)
            cy = min(y + 0.5, h - y - 0.5)
            if cx >= r or cy >= r:
                continue
            dx, dy = r - cx, r - cy
            if dx * dx + dy * dy <= r * r:
                continue
            # 在圆外，算覆盖率
            hit = 0
            for sy in range(sub):
                for sx in range(sub):
                    px = x + (sx + 0.5) / sub
                    py = y + (sy + 0.5) / sub
                    ccx = min(px, w - px)
                    ccy = min(py, h - py)
                    if ccx >= r or ccy >= r:
                        hit += 1
                        continue
                    ddx, ddy = r - ccx, r - ccy
                    if ddx * ddx + ddy * ddy <= r * r:
                        hit += 1
            cov = hit / (sub * sub)
            i = (y * w + x) * 4
            rgba[i + 3] = int(rgba[i + 3] * cov)


def flood_transparent_bg(rgba, w, h, tol=14):
    """把四周的纯色背景抠成透明（圆角以外的部分）。

    对用户给的图，四角是深色近黑。这里从四条边向内做泛洪，
    只抠掉与边缘颜色相近且连通到边的像素 —— 图标内部的深色
    不会被误抠，这是"按颜色全图替换"做不到的。
    """
    if not rgba:
        return 0
    # 取四角中位色作为背景色
    corners = [0, (w - 1) * 4, (h - 1) * w * 4, ((h - 1) * w + w - 1) * 4]
    ref = [0, 0, 0]
    for c in corners:
        for k in range(3):
            ref[k] += rgba[c + k]
    ref = [v // 4 for v in ref]

    def close(i):
        return (abs(rgba[i] - ref[0]) <= tol
                and abs(rgba[i + 1] - ref[1]) <= tol
                and abs(rgba[i + 2] - ref[2]) <= tol)

    seen = bytearray(w * h)
    stack = []
    for x in range(w):
        stack.append((x, 0))
        stack.append((x, h - 1))
    for y in range(h):
        stack.append((0, y))
        stack.append((w - 1, y))

    cleared = 0
    while stack:
        x, y = stack.pop()
        if not (0 <= x < w and 0 <= y < h):
            continue
        p = y * w + x
        if seen[p]:
            continue
        i = p * 4
        if not close(i):
            continue
        seen[p] = 1
        rgba[i + 3] = 0
        cleared += 1
        stack.append((x + 1, y))
        stack.append((x - 1, y))
        stack.append((x, y + 1))
        stack.append((x, y - 1))
    return cleared


# ---------- ICO 打包 ----------

def _bmp_entry(rgba, w, h):
    """把一个尺寸打成 ICO 里的 BMP DIB 项（BITMAPINFOHEADER + BGRA + 掩码）。"""
    # BITMAPINFOHEADER，高度写两倍（XOR 图 + AND 掩码）
    hdr = struct.pack("<IiiHHIIiiII",
                      40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    # 像素数据：自下而上，BGRA
    pix = bytearray(w * h * 4)
    for y in range(h):
        srow = (h - 1 - y) * w * 4
        drow = y * w * 4
        for x in range(w):
            si = srow + x * 4
            di = drow + x * 4
            pix[di] = rgba[si + 2]      # B
            pix[di + 1] = rgba[si + 1]  # G
            pix[di + 2] = rgba[si]      # R
            pix[di + 3] = rgba[si + 3]  # A

    # AND 掩码：32 位图里它不被使用，但仍须存在且行对齐到 4 字节
    mask_row = ((w + 31) // 32) * 4
    mask = bytearray(mask_row * h)
    for y in range(h):
        srow = (h - 1 - y) * w * 4
        for x in range(w):
            if rgba[srow + x * 4 + 3] == 0:
                mask[y * mask_row + (x // 8)] |= (0x80 >> (x % 8))

    return hdr + bytes(pix) + bytes(mask)


def build_ico(sizes_data):
    """sizes_data: [(size, rgba_bytearray)] -> ICO 字节串。"""
    entries = []
    datas = []
    for size, rgba in sizes_data:
        datas.append(_bmp_entry(rgba, size, size))

    # ICO 头：保留 0，类型 1(图标)，项数
    n = len(datas)
    out = bytearray(struct.pack("<HHH", 0, 1, n))
    offset = 6 + 16 * n
    for (size, _rgba), blob in zip(sizes_data, datas):
        b = size if size < 256 else 0     # 256 在 ICO 里写 0
        out.extend(struct.pack("<BBBBHHII",
                               b, b, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    for blob in datas:
        out.extend(blob)
    return bytes(out)


# ---------- 快速健康检查 ----------

def mean_lum(rgba, w, h):
    tot = 0.0
    n = 0
    for i in range(0, w * h * 4, 4):
        if rgba[i + 3] < 8:
            continue
        tot += 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2]
        n += 1
    return tot / n if n else 0.0
