"""查看当前剪贴板完整状态，用于确认守卫运行期间无残留污染。

只读，不修改剪贴板（枚举格式不触发序列号变化）。
"""

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.OpenClipboard.argtypes = [wt.HWND]
kernel32.GlobalSize.argtypes = [wt.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t

import py_clipguard as cg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    seq = cg.clipboard_seq()
    print(f"剪贴板序列号: {seq}")

    if not cg._open_clipboard():
        print("无法打开剪贴板")
        return 1
    try:
        fmts = []
        fid = 0
        while True:
            fid = user32.EnumClipboardFormats(fid)
            if not fid:
                break
            h = user32.GetClipboardData(fid)
            size = kernel32.GlobalSize(h) if h else 0
            fmts.append((fid, cg.format_name(fid), size))
        print(f"格式数: {len(fmts)}")
        for fid, name, size in fmts:
            print(f"  {fid:>3}  {name:<28} {size:>8} bytes")
    finally:
        user32.CloseClipboard()

    txt = cg.read_text()
    print(f"\nCF_UNICODETEXT: {len(txt) if txt else 0} 字符")
    if txt:
        print(f"内容: {txt[:300]!r}")
    else:
        print("内容: (空)")

    # 与基线比对，确认没有我们自己的缝合物
    print()
    print("序列号复检:", cg.clipboard_seq(), "（应与上面一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
