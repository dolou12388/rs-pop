"""真实端到端：选中文字 -> 取词 -> 立即再按 Ctrl+C 复制同一段。

为什么必须有这一层（单元测试为什么不够）：

    py_clip_race_test.py 里的场景都用手工赋值构造中间态，因此
    read_text_via_copy 漏写 _intermediate_at 时它们照样全绿 ——
    而真实路径下 elapsed_ms 恒为 0，宽限窗口永远命中，序列号判据
    形同虚设，用户选完词立即复制会被还原掉。

    这个缺陷就是本文件抓出来的。它调用真实的取词函数、真实的按键、
    真实的剪贴板，是唯一能覆盖"登记中间态"这段代码的测试。

为什么不用记事本：Win11 新版记事本是单进程多标签应用，命令行启动的
新"实例"其实是往用户已有的记事本里开标签，测试会碰到用户正在编辑的
文档。这里自己创建窗口和 EDIT 控件 —— 与记事本同样响应 Ctrl+C（照常
写剪贴板），但完全隔离，不影响任何用户数据。

用法：python py_clip_e2e_test.py
需要可交互桌面（会短暂弹出一个小窗口），CI 无头环境下会被跳过。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

import py_clipguard as cg

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_VSCROLL = 0x40000000, 0x10000000, 0x00200000
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
CW_USEDEFAULT = -2147483648
WM_DESTROY, WM_CLOSE = 0x0002, 0x0010
WM_SETFONT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x0030, 0x000D, 0x000E
WM_SETTEXT, EM_SETSEL = 0x000C, 0x00B1

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                             wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
# wparam/lparam 用 c_void_p：既能传整数，也能传指针（WM_SETTEXT / WM_GETTEXT）
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_void_p,
                                ctypes.c_void_p]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetFocus.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wt.HGDIOBJ

state = {"hwnd": None, "edit": None, "ready": threading.Event(),
         "stop": threading.Event(), "wndproc": None}

SEL_TEXT = "E2E立即复制验证文本片段"
OLD_CLIP = "E2E_用户原有剪贴板内容"

fails = 0


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"[PASS] {name}")
    else:
        fails += 1
        print(f"[FAIL] {name}  {detail}")


def send_text_msg(hwnd, msg, text):
    buf = ctypes.c_wchar_p(text)
    p = ctypes.cast(buf, ctypes.c_void_p).value
    return user32.SendMessageW(hwnd, msg, p, p)


def get_text_msg(hwnd, msg, size, buf):
    return user32.SendMessageW(hwnd, msg, size,
                               ctypes.cast(buf, ctypes.c_void_p))


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ui_thread():
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = state["wndproc"]
    wc.hInstance = hinst
    wc.lpszClassName = "PopE2EHost"
    wc.hCursor = user32.LoadCursorW(None, 32512)
    wc.hbrBackground = 6
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(
        0, "PopE2EHost", "PopE2E", WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 620, 260, None, None, hinst, None)
    if not hwnd:
        state["ready"].set()
        return

    edit = user32.CreateWindowExW(
        0, "EDIT", "",
        WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_AUTOVSCROLL,
        10, 10, 580, 180, hwnd, None, hinst, None)
    user32.SendMessageW(edit, WM_SETFONT, gdi32.GetStockObject(17), 1)
    send_text_msg(edit, WM_SETTEXT, SEL_TEXT)

    state["hwnd"], state["edit"] = hwnd, edit
    state["ready"].set()

    msg = wt.MSG()
    while not state["stop"].is_set():
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r <= 0:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def read_edit(hwnd):
    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0) + 1
    buf = ctypes.create_unicode_buffer(n)
    get_text_msg(hwnd, WM_GETTEXT, n, buf)
    return buf.value


def _is_descendant(hwnd, parent):
    h = hwnd
    for _ in range(8):
        if not h:
            return False
        if h == parent:
            return True
        h = user32.GetParent(h)
    return False


def force_foreground(host, edit):
    """把窗口真正带到前台。

    后台进程调用 SetForegroundWindow 通常被 Windows 拦住，焦点不进来，
    Ctrl+C 就打不到 EDIT 上。AttachThreadInput 把自己的输入队列挂到当前
    前台线程上，这样就能合法取得前台权。
    """
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    my_tid = kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != my_tid:
        attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True))
    try:
        user32.ShowWindow(host, 9)      # SW_RESTORE
        user32.BringWindowToTop(host)
        user32.SetForegroundWindow(host)
        user32.SetFocus(host)
        user32.SetFocus(edit)
        user32.SendMessageW(edit, EM_SETSEL, 0, -1)
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.25)
    cur = user32.GetFocus()
    return cur == edit or _is_descendant(cur, host)


def run_once(edit, host, label):
    cg.write_text(OLD_CLIP)
    time.sleep(0.08)

    force_foreground(host, edit)
    time.sleep(0.2)
    sel = read_edit(edit)
    if not sel.strip():
        print(f"  [{label}] 编辑区为空，跳过")
        return None

    with cg.ClipGuard() as g:
        got, _info = g.read_text_via_copy()
        if got is None:
            # 焦点尚未稳定时 Ctrl+C 可能打空，剪贴板压根没被改动，
            # "用户的复制"无从谈起 —— 跳过，不计入结果。
            print(f"  [{label}] 取词未拿到文本，跳过本轮")
            return None
        seq_after_ours = cg.clipboard_seq()
        # 用户随后按 Ctrl+C 复制同一段文字。
        # 真实人工间隔 200ms+，这里取 120ms（比现实更严苛）。
        time.sleep(0.12)
        cg.send_copy_keys()
        time.sleep(0.04)
        seq_after_user = cg.clipboard_seq()

    # 前置判据：用户的 Ctrl+C 必须真的写进了剪贴板，否则本轮测不到东西。
    # 焦点被抢走时按键会打空，此时"保留用户的复制"无从验证 —— 跳过。
    if seq_after_user == seq_after_ours:
        print(f"  [{label}] 用户的 Ctrl+C 未生效（焦点丢失），跳过本轮")
        return None

    final = cg.read_text()
    ok = (final == sel)
    print(f"  [{label}] 选中={sel!r}  取词={got!r}")
    print(f"       还原结论: {g.restore_result}")
    print(f"       最终剪贴板={final!r}")
    return ok


def main():
    state["wndproc"] = WNDPROC(_wndproc)
    t = threading.Thread(target=_ui_thread, daemon=True)
    t.start()
    state["ready"].wait(5)
    host, edit = state["hwnd"], state["edit"]
    if not host or not edit:
        print("窗口创建失败（可能在无交互桌面的环境）")
        return 1

    print("=" * 70)
    print("真实端到端：选中 -> 取词 -> 立即再复制同一段文字")
    print("=" * 70)

    results = []
    try:
        # 预热：首轮常因焦点尚未稳定导致 Ctrl+C 打空，不计结果。
        # 窗口刚创建时 Windows 可能还在处理激活，多发几次焦点请求。
        for _ in range(3):
            force_foreground(host, edit)
            time.sleep(0.35)
        run_once(edit, host, "预热")
        time.sleep(0.4)
        for i in range(3):
            # 每轮重试，直到取词成功（焦点稳定后一次就够）
            for attempt in range(3):
                r = run_once(edit, host, f"第 {i + 1} 轮")
                if r is not None:
                    results.append(r)
                    break
                time.sleep(0.3)
            time.sleep(0.3)
    finally:
        user32.PostMessageW(host, WM_CLOSE, 0, 0)
        state["stop"].set()
        user32.PostQuitMessage(0)
        time.sleep(0.3)

    print()
    for i, ok in enumerate(results, 1):
        check(f"第 {i} 轮的复制结果被保留", ok,
              "变回旧内容即「选完词立即复制，复制不上」")

    if not results:
        print("没有有效轮次（焦点始终未稳定），无法判定")
        return 1

    print()
    print("=" * 70)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    print("=" * 70)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
