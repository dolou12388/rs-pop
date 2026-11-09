"""诊断：Ctrl+C 之后剪贴板里到底有哪些格式。

针对 py_real_apps.py 里两类失败：

  · ChatGPT: changed=True 但 CF_UNICODETEXT 读不到  —— 怀疑只写 HTML/RTF
  · 飞书   : changed=False 完全没反应               —— 怀疑焦点不在可复制区

诊断内容：
  1. 记录按下 Ctrl+C 前的格式快照与序列号
  2. 按下 Ctrl+C 后立即枚举所有格式，打印格式名与字节数
  3. 对 CF_UNICODETEXT / "HTML Format" / "Rich Text Format" 分别取内容
  4. 最后还原剪贴板

这样能确定：是"没复制到"，还是"复制到了别的格式"。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT
user32.GetClipboardFormatNameW.argtypes = [wt.UINT, wt.LPWSTR, ctypes.c_int]
user32.RegisterClipboardFormatW.argtypes = [wt.LPWSTR]
user32.RegisterClipboardFormatW.restype = wt.UINT
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]

kernel32.GlobalLock.argtypes = [wt.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HANDLE]
kernel32.GlobalSize.argtypes = [wt.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t

CF_UNICODETEXT = 13
CF_TEXT = 1
VK_CONTROL = 0x11
VK_C = 0x43
VK_A = 0x41
KEYEVENTF_KEYUP = 0x0002

MARKER = "FMT_DIAG_\u57fa\u7ebf_888"


def fmt_name(fid):
    std = {1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 8: "CF_DIB",
           13: "CF_UNICODETEXT", 14: "CF_ENHMETAFILE", 15: "CF_HDROP",
           16: "CF_LOCALE", 17: "CF_DIBV5"}
    if fid in std:
        return std[fid]
    b = ctypes.create_unicode_buffer(256)
    n = user32.GetClipboardFormatNameW(fid, b, 256)
    return b.value if n else f"fmt_{fid}"


def dump_formats(tag):
    """枚举当前剪贴板格式并打印大小。"""
    print(f"  {tag}:")
    if not cg._open_clipboard():
        print("    (无法打开剪贴板)")
        return {}
    info = {}
    try:
        fid = 0
        while True:
            fid = user32.EnumClipboardFormats(fid)
            if fid == 0:
                break
            name = fmt_name(fid)
            h = user32.GetClipboardData(fid)
            size = kernel32.GlobalSize(h) if h else 0
            info[fid] = (name, size)
            print(f"    {fid:6d} {name:<28} {size:>8} bytes")
    finally:
        user32.CloseClipboard()
    return info


def read_format_text(fid):
    """尽力把某格式读成可读文本（CF_TEXT/UNICODETEXT 直接读，其它按字节探测）。"""
    if not cg._open_clipboard():
        return None
    try:
        h = user32.GetClipboardData(fid)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            size = kernel32.GlobalSize(h)
            if fid == CF_UNICODETEXT:
                return ctypes.c_wchar_p(p).value
            if fid == CF_TEXT:
                raw = ctypes.string_at(p, min(size, 4096))
                for enc in ("utf-8", "gbk", "latin-1"):
                    try:
                        return raw.split(b"\0")[0].decode(enc)
                    except Exception:
                        continue
            raw = ctypes.string_at(p, min(size, 4096))
            for enc in ("utf-8", "utf-16-le", "gbk"):
                try:
                    s = raw.decode(enc, errors="strict")
                    if s.isprintable() or "\n" in s:
                        return s[:400]
                except Exception:
                    continue
            return None
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def send(vk, ctrl=False):
    if ctrl:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.015)
    user32.keybd_event(vk, 0, KEYDEF_UP := KEYEVENTF_KEYUP, 0)
    if ctrl:
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def pid2name():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, encoding="gbk",
                             errors="replace", timeout=20).stdout
    except Exception:
        return {}
    m = {}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            try:
                m[int(parts[1])] = parts[0]
            except ValueError:
                pass
    return m


def find(nm_want):
    p2n = pid2name()
    hits = []

    E = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if p2n.get(pid.value) == nm_want:
            tb = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, tb, n + 1)
            hits.append((h, tb.value))
        return True

    user32.EnumWindows(E(cb), 0)
    return hits


def diagnose(nm_want, label, do_select_all=True):
    print("\n" + "=" * 74)
    print(f"[{label}] {nm_want}")
    print("=" * 74)

    wins = find(nm_want)
    if not wins:
        print("  未找到窗口")
        return
    h, ttl = wins[0]
    print(f"  hwnd=0x{h:X} title={ttl[:50]!r}")

    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)
        time.sleep(0.4)
    user32.SetForegroundWindow(h)
    time.sleep(0.9)

    # 基线
    cg.write_text(MARKER)
    time.sleep(0.1)
    seq0 = cg.clipboard_seq()

    guard = cg.ClipGuard()
    guard.__enter__()
    try:
        if do_select_all:
            send(VK_A, ctrl=True)
            time.sleep(0.7)
            print("  已发送 Ctrl+A")

        seq_mid = cg.clipboard_seq()
        print(f"  序列号: 基线={seq0} Ctrl+A后={seq_mid}")

        send(VK_C, ctrl=True)
        time.sleep(0.45)
        seq_after = cg.clipboard_seq()
        print(f"  Ctrl+C 后序列号={seq_after} 变化={seq_after - seq_mid}")

        info = dump_formats("Ctrl+C 后的剪贴板格式")
        if not info:
            print("    -> 剪贴板为空，说明 Ctrl+C 未生效")

        # 尝试从各格式取文本
        print("  尝试从各格式读取文本:")
        got_any = False
        for fid, (name, size) in sorted(info.items(), key=lambda x: x[1][1], reverse=True):
            if size == 0:
                continue
            txt = read_format_text(fid)
            if txt:
                got_any = True
                preview = txt[:100].replace("\n", "\\n").replace("\r", "\\r")
                print(f"    {name}: {preview!r}")
        if not got_any:
            print("    (所有格式都读不出可读文本)")
    finally:
        guard.__exit__(None, None, None)
        print(f"  还原: {guard.restore_result}")

    time.sleep(0.1)
    print(f"  最终剪贴板: {cg.read_text()!r}")


def main():
    print("=" * 74)
    print("剪贴板格式诊断：为什么 ChatGPT / 飞书 取词失败")
    print("=" * 74)
    diagnose("ChatGPT.exe", "ChatGPT 桌面版")
    diagnose("Feishu.exe", "飞书")
    diagnose("msedge.exe", "Edge（对照组，已知可成功）")
    print("\n" + "=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
