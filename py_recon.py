"""目标应用技术侦查：枚举窗口、子控件类名，判定取词路径。

判定规则：
  · 出现 Edit / RichEdit / RichEditD2DPT / RICHEDIT50W  -> 第1级 Win32 消息级可试
  · 出现 Chrome_WidgetWin_1 / Chrome_RenderWidgetHostHWND -> Chromium 系，需第3级
  · 出现 WebViewHost / TSI 等 -> 内嵌 WebView，需第3级
  · 全自绘（只有顶层窗口，无子控件）-> 只能第3级

不激活、不干扰用户正在用的窗口。仅读取。
"""

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll")

user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.c_void_p, wt.LPARAM]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.c_void_p]
user32.GetWindowRect.restype = wt.BOOL

# 已知含义的类名
CLASS_MEANING = {
    "Edit": "标准单行/多行编辑框 → 第1级可试",
    "RichEdit": "RichEdit → 第1级可试",
    "RichEdit20W": "RichEdit20W → 第1级可试",
    "RichEdit20A": "RichEdit20A → 第1级可试",
    "RICHEDIT50W": "RichEdit50W → 第1级可试",
    "RichEditD2DPT": "Direct2D RichEdit（新版记事本）→ 第1级多半不可用",
    "Chrome_WidgetWin_1": "Chromium 顶层窗口 → 选区需第3级",
    "Chrome_WidgetWin_0": "Chromium 子窗口 → 选区需第3级",
    "Chrome_RenderWidgetHostHWND": "Chromium 渲染宿主 → 选区需第3级",
    "Intermediate D3D Window": "Chromium D3D 合成层 → 不参与取词",
    "WeChatMainWndForPC": "微信旧版主窗口（自绘）",
    "WeChatAppEx": "微信小程序/内置浏览器",
    "WeChatLoginWndForPC": "微信登录窗",
    "WebViewHost": "内嵌 WebView",
    "TSFWindowClass": "输入法窗口",
    "IME": "输入法",
    "MSCTFIME UI": "输入法",
    "tooltips_class32": "工具提示",
    "SysShadow": "窗口阴影",
    "ApplicationFrameWindow": "UWP 框架窗口",
    "Windows.UI.Core.CoreWindow": "UWP 内容窗口",
    "Notepad": "记事本主窗口",
    "NotepadTextBox": "新版记事本编辑区",
    "RichEditD2DPT": "D2D 富文本编辑",
    "DirectUIHWND": "DirectUI（自绘容器）",
    "SHELLDLL_DefView": "桌面视图",
    "WorkerW": "桌面",
    "CabinetWClass": "资源管理器",
    "MSTaskListWClass": "任务栏",
    "ThunderRT6FormDC": "VB6 窗体",
    "TxGuiFoundation": "腾讯 QQ 系自绘",
    "TXMenuWindow": "腾讯菜单",
}

EDITISH = {"Edit", "RichEdit", "RichEdit20W", "RichEdit20A", "RICHEDIT50W",
           "RichEditD2DPT", "NotepadTextBox"}
CHROMIUM = {"Chrome_WidgetWin_1", "Chrome_WidgetWin_0",
            "Chrome_RenderWidgetHostHWND", "Intermediate D3D Window"}


def class_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title_of(h):
    n = user32.GetWindowTextLengthW(h)
    if n == 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def children(h, depth=0, max_depth=3, out=None):
    """递归枚举子窗口，收集 (depth, hwnd, class, title)。"""
    if out is None:
        out = []
    if depth > max_depth:
        return out

    found = []

    def cb(ch, _):
        found.append(ch)
        return True

    user32.EnumChildWindows(h, ctypes.cast(
        ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), ctypes.c_void_p), 0)

    for ch in found:
        cls = class_of(ch)
        ttl = title_of(ch)
        out.append((depth, ch, cls, ttl))
        if depth + 1 <= max_depth:
            sub = []
            children(ch, depth + 1, max_depth, sub)
            # 避免重复（EnumChildWindows 本身是递归的，这里只取一层）
    return out


def one_level_children(h):
    """只取直接子级，避免 EnumChildWindows 的递归特性导致重复。"""
    found = []

    def cb(ch, _):
        found.append(ch)
        return True

    user32.EnumChildWindows(h, ctypes.cast(
        ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), ctypes.c_void_p), 0)
    return found


def collect_tree(h, max_depth=3):
    """BFS 收集树，返回 [(depth, hwnd, class, title)]。"""
    out = []
    level = [(h, 0)]
    seen = set()
    while level:
        nxt = []
        for node, d in level:
            if node in seen:
                continue
            seen.add(node)
            for ch in one_level_children(node):
                if ch in seen:
                    continue
                seen.add(ch)
                out.append((d + 1, ch, class_of(ch), title_of(ch)))
                if d + 1 < max_depth:
                    nxt.append((ch, d + 1))
        level = nxt
    return out


WANTED = ["Weixin", "Feishu", "Doubao", "WXWork", "WeChatAppEx",
          "notepad", "Notepad", "obsidian", "soffice"]


def main():
    targeted = [w.lower() for w in WANTED]

    tops = []

    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        t = title_of(h)
        if not t:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        # 取进程名
        tops.append((h, t, class_of(h), pid.value))
        return True

    user32.EnumWindows(ctypes.cast(
        ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), ctypes.c_void_p), 0)

    # 用 tasklist 风格的进程名映射
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="replace", timeout=20,
        ).stdout
    except Exception:
        out = ""
    pid2name = {}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            try:
                pid2name[int(parts[1])] = parts[0]
            except ValueError:
                pass

    hits = []
    for h, t, c, pid in tops:
        name = pid2name.get(pid, "")
        if any(w in name.lower() for w in targeted) or \
           any(k in c for k in ("WeChat", "TxGui", "Chrome_WidgetWin")):
            hits.append((name, h, c, t, pid))

    if not hits:
        print("未发现目标应用窗口。请先打开微信/飞书/豆包任一窗口。")
        return 1

    print("=" * 80)
    print("目标应用窗口侦查")
    print("=" * 80)

    for name, h, c, t, pid in hits[:14]:
        print(f"\n进程: {name}  pid={pid}")
        print(f"  顶层 hwnd=0x{h:X}  class={c!r}")
        print(f"  标题: {t[:70]!r}")
        tree = collect_tree(h, max_depth=3)
        if not tree:
            print("  子控件: 无（全自绘）")
            continue
        classes = {}
        for d, ch, cls, ttl in tree:
            classes[cls] = classes.get(cls, 0) + 1
        print(f"  子控件 {len(tree)} 个，类名分布:")
        for cls, n in sorted(classes.items(), key=lambda x: -x[1])[:12]:
            meaning = CLASS_MEANING.get(cls, "")
            tag = ""
            if cls in EDITISH:
                tag = "  <<< 第1级候选"
            elif cls in CHROMIUM:
                tag = "  <<< Chromium 系"
            print(f"    {n:3d} x {cls!r}{tag}")
            if meaning:
                print(f"          {meaning}")
        # 汇总判定
        has_edit = any(c2 in EDITISH for _, _, c2, _ in tree)
        has_chromium = any(c2 in CHROMIUM for _, _, c2, _ in tree)
        print(f"  >>> 判定: ", end="")
        if has_edit and has_chromium:
            print("第1级可能命中编辑区，正文区需第3级")
        elif has_edit:
            print("第1级可试")
        elif has_chromium:
            print("Chromium 系，选区必须第3级")
        else:
            print("全自绘，选区必须第3级")

    print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
