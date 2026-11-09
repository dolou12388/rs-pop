"""剪贴板全格式备份/还原的两条路线对比实验。

路线 A（OLE）：
  OleGetClipboard 拿 IDataObject -> 写入临时内容 -> OleSetClipboard 还原
  预期问题：OleGetClipboard 返回的是代理对象，引用原所有者；原内容被替换后
            延迟渲染的格式失效，OleSetClipboard 报 CLIPBRD_E_CANT_CLOSE。

路线 B（逐格式 HGLOBAL 拷贝）：
  EnumClipboardFormats 枚举 -> 对每个格式 GetClipboardData -> GlobalSize +
  GlobalLock 复制字节到自己的 HGLOBAL -> 还原时 EmptyClipboard 后逐个
  SetClipboardData。这是多数剪贴板管理器采用的做法。
  预期问题：CF_BITMAP 等非 HGLOBAL 格式无法用此法复制，需单独处理。

本脚本对两条路线分别做「写入-还原-比对」，用格式数量与文本内容双指标判定。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.CloseClipboard.restype = wt.BOOL
user32.EmptyClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT
user32.GetClipboardFormatNameW.argtypes = [wt.UINT, wt.LPWSTR, ctypes.c_int]
user32.GetClipboardFormatNameW.restype = ctypes.c_int
user32.RegisterClipboardFormatW.argtypes = [wt.LPWSTR]
user32.RegisterClipboardFormatW.restype = wt.UINT
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
user32.IsClipboardFormatAvailable.restype = wt.BOOL

kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HANDLE
kernel32.GlobalLock.argtypes = [wt.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HANDLE]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalFree.argtypes = [wt.HANDLE]
kernel32.GlobalFree.restype = wt.HANDLE
kernel32.GlobalSize.argtypes = [wt.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t

CF_UNICODETEXT = 13
CF_BITMAP = 2
CF_DIB = 8
GMEM_MOVEABLE = 0x0002

# 无法通过 HGLOBAL 字节拷贝复制的格式
NON_HGLOBAL = {CF_BITMAP}


def open_clip(retries=15):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def fmt_name(f):
    std = {
        1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 8: "CF_DIB",
        13: "CF_UNICODETEXT", 15: "CF_HDROP", 16: "CF_LOCALE",
        17: "CF_DIBV5", 0x0080: "CF_OWNERDISPLAY",
    }
    if f in std:
        return std[f]
    buf = ctypes.create_unicode_buffer(128)
    n = user32.GetClipboardFormatNameW(f, buf, 128)
    return buf.value if n else f"fmt_{f}"


def list_formats():
    out = []
    if not open_clip():
        return out
    try:
        f = 0
        while True:
            f = user32.EnumClipboardFormats(f)
            if f == 0:
                break
            out.append(f)
    finally:
        user32.CloseClipboard()
    return out


def read_text():
    if not open_clip():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.c_wchar_p(p).value
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def write_text(text):
    data = (text + "\0").encode("utf-16-le")
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not open_clip():
        kernel32.GlobalFree(h)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


# ---------- 路线 B：逐格式 HGLOBAL 拷贝 ----------

def backup_formats():
    """返回 [(format_id, hglobal_copy)]，跳过无法复制的格式。"""
    saved = []
    skipped = []
    if not open_clip():
        return saved, skipped, "无法打开剪贴板"
    try:
        f = 0
        while True:
            f = user32.EnumClipboardFormats(f)
            if f == 0:
                break
            if f in NON_HGLOBAL:
                skipped.append((f, fmt_name(f), "非 HGLOBAL"))
                continue
            hsrc = user32.GetClipboardData(f)
            if not hsrc:
                skipped.append((f, fmt_name(f), "GetClipboardData 空"))
                continue
            size = kernel32.GlobalSize(hsrc)
            if size == 0:
                skipped.append((f, fmt_name(f), "GlobalSize=0"))
                continue
            psrc = kernel32.GlobalLock(hsrc)
            if not psrc:
                skipped.append((f, fmt_name(f), "GlobalLock 失败"))
                continue
            try:
                raw = ctypes.string_at(psrc, size)
            finally:
                kernel32.GlobalUnlock(hsrc)

            hcopy = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not hcopy:
                skipped.append((f, fmt_name(f), "GlobalAlloc 失败"))
                continue
            pdst = kernel32.GlobalLock(hcopy)
            if not pdst:
                kernel32.GlobalFree(hcopy)
                skipped.append((f, fmt_name(f), "目标 GlobalLock 失败"))
                continue
            ctypes.memmove(pdst, raw, size)
            kernel32.GlobalUnlock(hcopy)
            saved.append((f, hcopy, size))
    finally:
        user32.CloseClipboard()
    return saved, skipped, None


def restore_formats(saved):
    """按保存顺序写回。返回 (ok, detail)。"""
    if not open_clip():
        return False, "无法打开剪贴板"
    try:
        user32.EmptyClipboard()
        written = 0
        for f, hcopy, size in saved:
            if user32.SetClipboardData(f, hcopy):
                written += 1
            else:
                kernel32.GlobalFree(hcopy)
    finally:
        user32.CloseClipboard()
    return written == len(saved), f"写回 {written}/{len(saved)} 个格式"


# ---------- 实验 ----------

def test_route_b():
    print("\n" + "=" * 70)
    print("路线 B：逐格式 HGLOBAL 拷贝")
    print("=" * 70)

    MARK1 = "ROUTE_B_ORIGINAL_\u539f\u59cb_777"
    MARK2 = "ROUTE_B_TEMP_\u4e34\u65f6_888"

    write_text(MARK1)
    time.sleep(0.1)

    before_fmts = list_formats()
    before_text = read_text()
    print(f"备份前: {len(before_fmts)} 个格式, 文本={before_text!r}")
    print(f"  格式: {[fmt_name(f) for f in before_fmts]}")

    saved, skipped, err = backup_formats()
    print(f"备份: 成功 {len(saved)} 个, 跳过 {len(skipped)} 个")
    for f, n, why in skipped:
        print(f"  跳过 {n}: {why}")
    if err:
        print(f"  错误: {err}")
        return False

    # 模拟目标程序写入临时内容（真实 Ctrl+C 效果）
    write_text(MARK2)
    time.sleep(0.1)
    print(f"中间态: 文本={read_text()!r}")

    # 还原
    ok, detail = restore_formats(saved)
    time.sleep(0.1)

    after_fmts = list_formats()
    after_text = read_text()
    print(f"还原后: {len(after_fmts)} 个格式, 文本={after_text!r}")
    print(f"  还原结果: {detail}")

    text_ok = after_text == MARK1
    fmt_ok = len(after_fmts) >= len(before_fmts)
    print(f"\n文本还原: {'PASS' if text_ok else 'FAIL'}")
    print(f"格式保真: {'PASS' if fmt_ok else 'FAIL'} "
          f"({len(before_fmts)} -> {len(after_fmts)})")
    return text_ok and fmt_ok


def test_route_a():
    print("\n" + "=" * 70)
    print("路线 A：OLE IDataObject")
    print("=" * 70)

    hr = ole32.OleInitialize(None)
    print(f"OleInitialize hr=0x{hr & 0xFFFFFFFF:08X}")

    MARK1 = "ROUTE_A_ORIGINAL_\u539f\u59cb_111"
    write_text(MARK1)
    time.sleep(0.1)

    p = ctypes.c_void_p()
    hr = ole32.OleGetClipboard(ctypes.byref(p))
    print(f"OleGetClipboard hr=0x{hr & 0xFFFFFFFF:08X} ptr={p}")

    write_text("ROUTE_A_TEMP_\u4e34\u65f6_222")
    time.sleep(0.1)

    hr2 = ole32.OleSetClipboard(p)
    print(f"OleSetClipboard hr=0x{hr2 & 0xFFFFFFFF:08X}")
    if hr2 == 0:
        hr3 = ole32.OleFlushClipboard()
        print(f"OleFlushClipboard hr=0x{hr3 & 0xFFFFFFFF:08X}")
        time.sleep(0.1)
        after = read_text()
        print(f"还原后文本: {after!r}")
        print(f"结果: {'PASS' if after == MARK1 else 'FAIL'}")
        return after == MARK1

    print("OleSetClipboard 失败 —— 证明 OLE 代理对象在内容被替换后失效")
    print("  0x800401D4 = CLIPBRD_E_CANT_CLOSE")
    return False


def main():
    print("=" * 70)
    print("剪贴板备份/还原 两条路线对比")
    print("=" * 70)
    b = test_route_b()
    a = test_route_a()
    print("\n" + "=" * 70)
    print("结论")
    print("=" * 70)
    print(f"路线 A (OLE)      : {'可用' if a else '不可用'}")
    print(f"路线 B (逐格式拷贝): {'可用' if b else '不可用'}")
    if b and not a:
        print("=> 采用路线 B 作为剪贴板守卫的实现基础")
    return 0 if b else 1


if __name__ == "__main__":
    sys.exit(main())
