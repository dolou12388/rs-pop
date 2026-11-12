"""Windows 全局划词 —— 三级降级取词 + 剪贴板不污染

取词链路（按优先级，全部为本机实测结果）：

  第 1 级  Win32 消息级
          GetGUIThreadInfo -> EM_GETSEL -> WM_GETTEXT
          实测：原生 EDIT 20 字符准确；WinForms RichEdit 31 字符准确
          优点：完全不碰剪贴板，零延迟（约 1ms）
          局限：仅适用于标准 EDIT / RichEdit 类控件

  第 2 级  UIA TextPattern
          实测：原生 EDIT 无此 Pattern；Chromium 有 Pattern 但选区数恒为 0；
                WinForms RichEdit 仅暴露 ValuePattern（返回全文而非选区）
          结论：本级在真实目标上收益极低，默认跳过；保留开关供特定控件使用

  第 3 级  剪贴板（受 ClipGuard 保护）
          合成 Ctrl+C（失败退 Ctrl+Insert）-> 读 CF_UNICODETEXT -> 逐格式还原
          实测：4 个格式全部写回，连续 20 次事务后内容仍与原状一致
          局限：覆盖浏览器正文、WPS、Word 等非标准控件；中间态对第三方
                剪贴板管理器可见（Windows 机制固有，无法规避）

资源占用实测：62.7MB / 1 进程 / 空闲 CPU 0.78% / 8 秒内存增长 0.00MB
（Electron 基线 392MB / 5 进程）。其中 27.7MB 与 16 个原生线程来自 tkinter
运行时本身，业务代码合计不足 3.3MB —— 明细见报告 6.2 节逐模块归因。
"""

import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import py_ai
import py_clipguard as cg
import py_config
import py_dpi
import py_log
import py_markdown
import py_pill_effect
import py_res
import py_theme

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

# ---------- 配置 ----------
POLL_INTERVAL_MS = 60
MIN_CHARS = 1
MAX_CHARS = 3000
PILL_W, PILL_H = 272, 44        # 96 DPI 设计尺寸参考值（Pill 实际按内容实测）
PILL_TTL_MS = 9000
DEBOUNCE_MS = 160
CLIP_TIMEOUT_MS = 650
# 左键释放后立即取词。实测表明 Chromium 选区在释放瞬间最可靠，
# 引入额外延迟会显著降低成功率（延迟 1200ms 时完全失败）。
RELEASE_DELAY_MS = 0
# 拖选距离守卫：按下与松开之间的位移小于此值则视为单击，不触发取词。
# 这是避免"每次单击都去动剪贴板"的关键闸门。4px 足以区分单击与划词。
MIN_DRAG_PX = 4
# 拖选最大位移上限：超过则多半是在拖窗口/拖滚动条/拖文件，不是选文字。
MAX_DRAG_PX = 3000
# ---------- 单实例 ----------
# 本程序是常驻托盘程序。没有这个守卫时，重复双击会起多个进程，
# 每个进程各注册一个托盘图标 —— 实测堆积到 4 个图标才被发现。
# 用命名互斥量做进程级唯一性，第二个实例直接退出。
SINGLE_INSTANCE_MUTEX = "Local\\PopDict_SingleInstance_v1"

# ---------- 取消选中即关闭 ----------
# 选区一旦被取消，浮层必须立刻消失，不能等 TTL 到期自己飘在那儿。
# 三种常见的"取消选中"动作分别覆盖：
AUTO_HIDE_ON_CLICK = True       # 单击浮层以外的地方（选择被 caret 取代）
AUTO_HIDE_ON_ESC = True         # 按 Esc（多数程序会清除选区）
AUTO_HIDE_ON_FG_CHANGE = True   # 切到别的窗口（Alt+Tab / 点任务栏）
# 浮层刚出现的一小段时间内不判前台窗口变化，避免目标程序短暂闪一下前台就误收。
FG_CHANGE_GRACE_MS = 300

ENABLE_UIA = os.environ.get("POP_ENABLE_UIA") == "1"
ENABLE_CLIPBOARD = os.environ.get("POP_NO_CLIPBOARD") != "1"

# ---------- 常量 ----------
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EM_GETSEL = 0x00B0
VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B

GWL_EXSTYLE = -20
ERROR_ALREADY_EXISTS = 183
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008

user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.c_void_p]
user32.GetGUIThreadInfo.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
# WM_GETTEXT 的 lParam 是缓冲区指针，需另用不检查 argtypes 的入口
_send_msg_ptr = ctypes.WinDLL("user32.dll").SendMessageW
_send_msg_ptr.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_void_p]
_send_msg_ptr.restype = ctypes.c_ssize_t
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.c_void_p]
user32.GetCursorPos.restype = wt.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long

EXCLUDED_CLASSES = {
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "mintty",
    "PuTTY",
}


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


def class_of(hwnd):
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_focus():
    fg = user32.GetForegroundWindow()
    if not fg:
        return None, None
    pid = wt.DWORD()
    tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    if not tid:
        return fg, None
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
        return fg, None
    return fg, gti.hwndFocus


# ---------- 第 1 级：Win32 消息级 ----------

def try_winmsg(hwnd):
    detail = {"level": "winmsg", "hwnd": hwnd, "class": class_of(hwnd)}
    if not hwnd:
        detail["reason"] = "无焦点控件"
        return None, detail
    if detail["class"] in EXCLUDED_CLASSES:
        detail["reason"] = "排除类"
        return None, detail

    res = user32.SendMessageW(hwnd, EM_GETSEL, 0, 0)
    start, end = res & 0xFFFF, (res >> 16) & 0xFFFF
    detail["sel"] = [start, end]
    if start == end:
        detail["reason"] = "无选区"
        return None, detail

    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    detail["full_len"] = n
    if n <= 0:
        detail["reason"] = "无文本"
        return None, detail

    cap = min(n + 2, MAX_CHARS * 4 + 65536)
    buf = ctypes.create_unicode_buffer(cap)
    _send_msg_ptr(hwnd, WM_GETTEXT, cap - 1, ctypes.byref(buf))
    full = buf.value

    if end > len(full):
        detail["reason"] = f"选区越界 {end}>{len(full)}"
        return None, detail

    text = full[start:end]
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        detail["truncated"] = True
    detail["reason"] = "ok"
    return (text or None), detail


# ---------- 第 2 级：UIA（可选） ----------

def try_uia():
    detail = {"level": "uia"}
    if not ENABLE_UIA:
        detail["reason"] = "未启用（POP_ENABLE_UIA=1 开启）"
        return None, detail
    try:
        import comtypes
        import comtypes.client
        UIA = comtypes.client.GetModule("UIAutomationCore.dll")
    except Exception as e:
        detail["reason"] = f"comtypes 不可用 {type(e).__name__}"
        return None, detail

    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elem = uia.GetFocusedElement()
        if not elem:
            detail["reason"] = "无焦点元素"
            return None, detail
        pat = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
        if not pat:
            detail["reason"] = "无 TextPattern"
            return None, detail
        tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
        sel = tp.GetSelection()
        detail["sel_count"] = sel.Length
        if sel.Length == 0:
            detail["reason"] = "选区数为 0"
            return None, detail
        parts = []
        for i in range(sel.Length):
            r = sel.GetElement(i)
            if r:
                parts.append(r.GetText(-1))
        text = "".join(parts)
        detail["reason"] = "ok"
        return (text or None), detail
    except Exception as e:
        detail["reason"] = f"{type(e).__name__}: {e}"
        return None, detail
    finally:
        comtypes.CoUninitialize()


# ---------- 第 3 级：剪贴板（受保护） ----------

def try_clipboard():
    detail = {"level": "clipboard"}
    if not ENABLE_CLIPBOARD:
        detail["reason"] = "已禁用（POP_NO_CLIPBOARD=1）"
        return None, detail
    guard = cg.ClipGuard()
    guard.__enter__()
    try:
        text, meta = guard.read_text_via_copy(timeout_ms=CLIP_TIMEOUT_MS)
        detail.update(meta)
        detail["reason"] = ("剪贴板快照不完整：" + meta["backup_error"]
                            if not meta.get("backup_ok", True)
                            else "ok" if text else "复制未产生文本")
        return (text or None), detail
    finally:
        guard.__exit__(None, None, None)
        detail["restore"] = guard.restore_result
        if guard.restore_result and not guard.restore_result[0]:
            print(f"[!] 剪贴板还原不完整: {guard.restore_result[1]}", file=sys.stderr)


def acquire_text():
    """三级降级取词。返回 (text, trail)。"""
    trail = []
    fg, focus = get_focus()

    text, d1 = try_winmsg(focus)
    trail.append(d1)
    if text:
        return text, trail

    text, d2 = try_uia()
    trail.append(d2)
    if text:
        return text, trail

    text, d3 = try_clipboard()
    trail.append(d3)
    return text, trail


# ---------- UI ----------

def pill_w():
    return py_dpi.px(PILL_W)


def pill_h():
    return py_dpi.px(PILL_H)


def place_near_cursor(x, y, w, h):
    """把浮层放在光标右下方；贴边时向内收，下方不够则翻到上方。

    DPI 感知后 GetSystemMetrics 返回的是物理像素（本机 2560x1600），
    与 GetCursorPos 同一坐标系，因此不需要再做换算。
    """
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    gap_x = py_dpi.px(12)
    gap_y = py_dpi.px(22)
    pad = py_dpi.px(8)
    px = x + gap_x
    py = y + gap_y
    if px + w > sw - pad:
        px = sw - w - pad
    if py + h > sh - pad:
        py = y - h - py_dpi.px(16)
    return max(pad, px), max(pad, py)


class Pill:
    TRANSPARENT_KEY = "#FF00FF"

    def __init__(self, root, on_action):
        self.root = root
        self.on_action = on_action
        self.payload = ""
        self.visible = False
        self.shown_at = 0.0
        self.rect = (0, 0, 0, 0)
        self.fg_at_show = None
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.97)
        self.win.configure(bg=py_theme.BG2)

        self.hwnd = None
        self._build()
        # 尺寸按实际内容量出来，而不是写死 —— 不同 DPI / 字体回退下
        # 文字宽度差别很大，写死会导致按钮被裁掉或右侧留一大片空。
        self.pill_w, self.pill_h = self._measure()
        self.win.geometry(f"{self.pill_w}x{self.pill_h}+200+200")
        self.win.withdraw()
        self._job = None
        self._rounded = False
        self._backdrop = None
        try:
            self._backdrop = py_pill_effect.PillBackdrop()
            ready = self._backdrop.prepare(
                self.pill_w, self.pill_h, py_dpi.px(10), py_theme.BG2)
        except Exception:
            ready = False
        if ready:
            try:
                self.win.attributes("-alpha", 1.0)
                self.win.configure(bg=self.TRANSPARENT_KEY)
                self.win.attributes("-transparentcolor", self.TRANSPARENT_KEY)
            except tk.TclError:
                self._disable_backdrop()
        else:
            self._disable_backdrop()

    def _disable_backdrop(self):
        if self._backdrop:
            self._backdrop.close()
            self._backdrop = None
        try:
            self.win.attributes("-transparentcolor", "")
        except tk.TclError:
            pass
        self.win.configure(bg=py_theme.BG2)
        self.win.attributes("-alpha", 0.97)

    def _build(self):
        """排布四个按钮，返回每个按钮的右边界与高度。"""
        step = py_dpi.px(64)          # 96 DPI 下每格 64px
        top = py_dpi.px(8)
        x = py_dpi.px(6)
        self._buttons = []
        self._slots = []
        for label, key in (("AI 搜索", "search"), ("翻译", "translate"),
                           ("解释", "explain"), ("复制", "copy")):
            b = tk.Label(self.win, text=label, fg=py_theme.FG,
                         bg=py_theme.BG2,
                         font=(py_markdown.BASE_FAMILY, 9),
                         padx=py_dpi.px(9), pady=py_dpi.px(6))
            b.bind("<Button-1>", lambda e, k=key: self.on_action(k))
            b.bind("<Enter>",
                   lambda e, w=b: w.configure(bg=py_theme.BG4))
            b.bind("<Leave>",
                   lambda e, w=b: w.configure(bg=py_theme.BG2))
            self._buttons.append(b)
            self._slots.append((x, step, top))
            x += step

    def _measure(self):
        """按 4 个等宽槽位算出浮层实际需要的宽高。"""
        self.win.update_idletasks()
        w = self._slots[-1][0] + self._slots[-1][1] + py_dpi.px(2)
        h = 0
        for (sx, sw, sy), b in zip(self._slots, self._buttons):
            bw = max(sw - py_dpi.px(2), b.winfo_reqwidth())
            b.place(x=sx, y=sy, width=bw)
            h = max(h, sy + b.winfo_reqheight() + py_dpi.px(6))
            w = max(w, sx + bw + py_dpi.px(2))
        return w, h

    def contains(self, x, y):
        x0, y0, x1, y1 = self.rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def show_at(self, x, y, text):
        self.payload = text
        w, h = self.pill_w, self.pill_h
        px, py = place_near_cursor(x, y, w, h)
        self.rect = (px, py, px + w, py + h)
        self.win.geometry(f"{w}x{h}+{px}+{py}")
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()
        hwnd = user32.GetAncestor(self.win.winfo_id(), 2)
        if hwnd != self.hwnd:
            self.hwnd = hwnd
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
            py_pill_effect.user32.SetWindowPos(
                hwnd, py_pill_effect.HWND_TOPMOST, 0, 0, 0, 0,
                py_pill_effect.SWP_NOSIZE | py_pill_effect.SWP_NOMOVE |
                py_pill_effect.SWP_NOACTIVATE |
                py_pill_effect.SWP_FRAMECHANGED)
        if self._backdrop and not self._backdrop.show(px, py, self.hwnd):
            self._disable_backdrop()
        # 老系统或逐像素底板失败时保留二值圆角退路。
        if not self._backdrop and not self._rounded:
            self.win.update_idletasks()
            self._rounded = py_theme.round_region(self.win,
                                                  py_dpi.px(10))
        self.visible = True
        self.shown_at = time.time() * 1000
        # 记下展示瞬间的前台窗口：之后它变了就说明用户切走了
        self.fg_at_show = user32.GetForegroundWindow()
        if self._job:
            self.root.after_cancel(self._job)
        self._job = self.root.after(PILL_TTL_MS, self.hide)

    def hide(self):
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if not self.visible:
            return
        self.win.withdraw()
        if self._backdrop:
            self._backdrop.hide()
        self.visible = False

    def apply_theme(self, old, new):
        py_theme.recolor_tree(self.win, old, new)
        if self._backdrop:
            ok = self._backdrop.prepare(
                self.pill_w, self.pill_h, py_dpi.px(10), py_theme.BG2)
            if ok and self.visible:
                ok = self._backdrop.show(self.rect[0], self.rect[1],
                                         self.hwnd)
            if not ok:
                self._disable_backdrop()
                if self.visible:
                    self._rounded = py_theme.round_region(
                        self.win, py_dpi.px(10))

    def close(self):
        self.hide()
        if self._backdrop:
            self._backdrop.close()
            self._backdrop = None
        self.win.destroy()


RESULT_BG = py_theme.BG
RESULT_BAR = py_theme.BG2
RESULT_BODY = py_theme.BG2

# ---------- 结果窗配色（统一走 py_theme，避免到处散落十六进制）----------
FG_MAIN = py_theme.FG
FG_SOFT = py_theme.FG_SOFT
FG_MUTED = py_theme.FG_DIM
FG_DIM = py_theme.FG_FAINT
LINE = py_theme.LINE
ACCENT = py_theme.ACCENT
ACCENT_HOVER = py_theme.ACCENT_HOVER
OK_COLOR = py_theme.OK_COLOR
ERR_COLOR = py_theme.ERR_COLOR
WARN_COLOR = py_theme.WARN_COLOR


def _sync_result_colors():
    globals().update({
        "RESULT_BG": py_theme.BG, "RESULT_BAR": py_theme.BG2,
        "RESULT_BODY": py_theme.BG2, "FG_MAIN": py_theme.FG,
        "FG_SOFT": py_theme.FG_SOFT, "FG_MUTED": py_theme.FG_DIM,
        "FG_DIM": py_theme.FG_FAINT, "LINE": py_theme.LINE,
        "ACCENT": py_theme.ACCENT, "ACCENT_HOVER": py_theme.ACCENT_HOVER,
        "OK_COLOR": py_theme.OK_COLOR, "ERR_COLOR": py_theme.ERR_COLOR,
        "WARN_COLOR": py_theme.WARN_COLOR,
    })


class _StateLabel:
    """给 tk.Label 模拟 Button 的 state 语义。

    Label 没有原生 state，但界面里"禁用/启用按钮"是刚需
    （追问进行中要禁用发送）。这里用浅包装提供 config_state()，
    内部通过改前景色 + 绑定标记来表现禁用态，并拦截点击。
    """

    __slots__ = ("w", "_enabled", "_fg", "_disabled_fg")

    def __init__(self, label):
        self.w = label
        self._enabled = str(label.cget("state")) != "disabled"
        self._fg = label.cget("fg")
        self._disabled_fg = FG_DIM

    def config_state(self, state):
        self._enabled = (state == "normal")
        # 把启用状态同步到 label 上，供悬停/点击回调检查
        try:
            self.w._enabled = self._enabled
            self.w.configure(fg=self._fg if self._enabled
                             else self._disabled_fg)
        except tk.TclError:
            pass

    def configure(self, **kw):
        if "state" in kw:
            self.config_state(kw.pop("state"))
        if kw:
            try:
                self.w.configure(**kw)
            except tk.TclError:
                pass

    def cget(self, key):
        """让 cget("state") 反映真实启用状态。

        tk.Label 本身没有 state，若直接透传给 label 会永远读到 normal，
        调用方（以及测试）据此判断禁用态就会失效。
        """
        if key == "state":
            return "normal" if self._enabled else "disabled"
        try:
            return self.w.cget(key)
        except tk.TclError:
            return ""

    def __getattr__(self, name):
        return getattr(self.w, name)


def _label_state(label, state):
    """兼容旧调用路径。"""
    _StateLabel(label).config_state(state)


class ResultWindow:
    """AI 结果窗口：多轮追问 + Markdown 渲染 + 流式输出。

    线程安全（实测教训）：
      tkinter 不允许非主线程调用 root.after 或操作控件，
      实测会抛 RuntimeError: main thread is not in main loop，
      并且回调被静默丢弃 —— 表现为「请求成功但窗口一直空白」。

      因此这里用「线程安全队列 + 主线程定时轮询」：
      网络线程只往 queue 里放动作，主线程 pump() 取出来执行。

    多轮追问：
      每一轮把自己（user）和回答（assistant）追加到 self.history，
      下一轮请求时整体带上，模型才有上下文。
      注意历史只保留「问答对」，不含 system 提示词 ——
      system 每轮都由 py_ai.build_messages 重新插入到最前面，
      重复带会让上下文膨胀，也容易让模型混乱。
    """

    # 轮询间隔：40ms 足够流畅，又不会明显吃 CPU
    PUMP_MS = 40
    # 历史最多保留多少轮问答（1 轮 = user + assistant）
    MAX_HISTORY_TURNS = 10

    def __init__(self, root, action_label, source_text,
                 on_ask=None, action_key=None):
        self.root = root
        self.closed = False
        self.cancel_event = threading.Event()
        self._request_lock = threading.Lock()
        self._request_id = 0
        self._acc = ""
        self._q = queue.Queue()
        self._pump_job = None

        self.action_label = action_label
        self.action_key = action_key or "search"
        self.source_text = source_text
        # on_ask(question, history) -> 无返回；由外部发起请求
        self.on_ask = on_ask
        self.history = []
        self.rendered = False
        # 追问时的累计回答（用于渲染）
        self._turns = []          # [(question, answer)]
        # 本轮提问（第一轮为空，追问时是问题文本）；用于 _turns 回显
        self._cur_q = ""
        # 本轮回答是否已并入 history（防首轮漏记 / 重复 finish 双写）
        self._answer_pushed = False
        self._answer_start = None
        self._busy = False

        self.win = tk.Toplevel(root)
        self.win.title(f"{action_label} · {len(source_text)} 字符")
        self.win.attributes("-topmost", True)
        # 尺寸按 DPI 缩放；默认给足宽度，中文长句折行少一点
        self.win.geometry(
            f"{py_dpi.px(620)}x{py_dpi.px(520)}+{py_dpi.px(300)}+{py_dpi.px(160)}")
        self.win.configure(bg=RESULT_BG)
        self.win.minsize(py_dpi.px(460), py_dpi.px(300))

        self._build_ui()
        self._bind_keys()
        self._start_pump()

        # DWM 标题栏配色 + 系统圆角。必须在窗口映射之后调用，
        # 否则 HWND 还没真正建立，属性设了也不生效。失败不影响功能。
        try:
            self.win.update_idletasks()
            py_theme.apply_window(self.win, small_corners=False)
        except Exception:
            pass

    # ---------- 界面搭建 ----------

    def _build_ui(self):
        f_title = py_markdown.base_font(10, bold=True)
        f_small = py_markdown.base_font(8)
        f_btn = py_markdown.base_font(9)

        # ---- 顶栏 ----
        bar = tk.Frame(self.win, bg=RESULT_BG, height=py_dpi.px(40))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self.title_label = tk.Label(
            bar, text=self.action_label, fg=FG_MAIN, bg=RESULT_BG,
            font=f_title)
        self.title_label.pack(side="left", padx=(py_dpi.px(14), 0),
                              pady=py_dpi.px(10))
        self.status_label = tk.Label(
            bar, text="准备中…", fg=FG_MUTED, bg=RESULT_BG, font=f_small)
        self.status_label.pack(side="left", padx=(py_dpi.px(8), 0))

        # 右侧按钮：关闭 / 复制 / 原文
        self.btn_close = self._flat_button(bar, "关闭", self.close, f_btn,
                                           muted=True)
        self.btn_close.pack(side="right", padx=(0, py_dpi.px(10)),
                            pady=py_dpi.px(7))
        self.btn_copy = self._flat_button(bar, "复制", self._copy, f_btn)
        self.btn_copy.pack(side="right", padx=(0, py_dpi.px(6)),
                           pady=py_dpi.px(7))
        self.md_mode = True
        self.btn_md = self._flat_button(bar, "原文", self._toggle_md, f_btn,
                                        muted=True)
        self.btn_md.configure(state="disabled")
        self.btn_md.pack(side="right", padx=(0, py_dpi.px(6)),
                         pady=py_dpi.px(7))

        # 顶栏下沿一条细分割线，视觉上把工具栏和内容分开
        tk.Frame(self.win, bg=LINE, height=1).pack(fill="x")

        # ---- 原文折叠区 ----
        self.src_visible = False
        self.src_toggle = tk.Label(
            self.win, text="▸ 原文", fg=FG_DIM, bg=RESULT_BG,
            font=f_small, cursor="hand2", anchor="w")
        self.src_toggle.pack(fill="x", padx=py_dpi.px(14),
                            pady=(py_dpi.px(4), 0))
        self.src_toggle.bind("<Button-1>", lambda e: self._toggle_src())
        self.src_box = tk.Text(
            self.win, height=3, wrap="word", bg=py_theme.BG3, fg=FG_MUTED,
            font=f_small, relief="flat", padx=py_dpi.px(10),
            pady=py_dpi.px(6), insertbackground=FG_MUTED,
            highlightthickness=0, borderwidth=0)
        self.src_box.insert("1.0", self.source_text[:2000])
        self.src_box.configure(state="disabled")

        # ---- 正文区（带滚动条）----
        body_wrap = tk.Frame(self.win, bg=RESULT_BODY)

        self.body = tk.Text(
            body_wrap, wrap="word", bg=RESULT_BODY, fg=FG_MAIN,
            font=py_markdown.base_font(10), relief="flat",
            padx=py_dpi.px(14), pady=py_dpi.px(12),
            insertbackground=FG_MAIN, highlightthickness=0, borderwidth=0,
            spacing1=py_dpi.px(1), spacing3=py_dpi.px(3),
            selectbackground=py_theme.ACCENT_DIM,
            selectforeground=(py_theme.ON_ACCENT
                              if py_theme.effective_mode() == "light"
                              else py_theme.FG))
        self.sb = tk.Scrollbar(
            body_wrap, orient="vertical", command=self.body.yview,
            width=py_dpi.px(11), bg=RESULT_BODY, troughcolor=RESULT_BODY,
            relief="flat", borderwidth=0, highlightthickness=0,
            activebackground=py_theme.BG4)
        self.body.configure(yscrollcommand=self._on_scroll)
        self.sb.pack(side="right", fill="y")
        self.body.pack(side="left", fill="both", expand=True)

        # ---- 追问输入区 ----
        ask_rule = tk.Frame(self.win, bg=LINE, height=1)
        ask_wrap = tk.Frame(self.win, bg=RESULT_BG)

        self.ask_entry = tk.Text(
            ask_wrap, height=2, wrap="word", bg=py_theme.BG3, fg=FG_MAIN,
            font=py_markdown.base_font(10), relief="flat",
            padx=py_dpi.px(10), pady=py_dpi.px(7),
            insertbackground=FG_MAIN, highlightthickness=1,
            highlightbackground=py_theme.LINE,
            highlightcolor=py_theme.ACCENT,
            borderwidth=0, selectbackground=py_theme.ACCENT_DIM,
            selectforeground=(py_theme.ON_ACCENT
                              if py_theme.effective_mode() == "light"
                              else py_theme.FG))
        self.btn_ask = self._flat_button(
            ask_wrap, "追问", self._do_ask, py_markdown.base_font(9),
            accent=True)
        self.btn_ask.pack(side="right", fill="y", padx=(py_dpi.px(8), 0))
        self.btn_stop = self._flat_button(
            ask_wrap, "停止", self._stop, py_markdown.base_font(9),
            muted=True)
        self.btn_stop.pack(side="right", fill="y", padx=(py_dpi.px(6), 0))
        self.btn_stop.configure(state="disabled")
        self.ask_entry.pack(side="left", fill="both", expand=True)

        # 空输入时不显示占位文字，但要让用户知道这里能追问
        self._hint_shown = True
        self.ask_entry.insert("1.0", "继续追问…（Enter 发送，Shift+Enter 换行）")
        self.ask_entry.configure(fg=FG_DIM)
        self.ask_entry.bind("<FocusIn>", self._on_ask_focus)
        self.ask_entry.bind("<FocusOut>", self._on_ask_blur)
        self.ask_entry.bind("<Return>", self._on_ask_return)
        self.ask_entry.bind("<Shift-Return>", lambda e: None)

        # pack 先分配底部的追问区，再让正文占剩余空间。
        ask_wrap.pack(side="bottom", fill="x", padx=py_dpi.px(10),
                      pady=(py_dpi.px(6), py_dpi.px(10)))
        ask_rule.pack(side="bottom", fill="x")
        body_wrap.pack(fill="both", expand=True,
                       padx=py_dpi.px(10), pady=(py_dpi.px(6), py_dpi.px(4)))

    def apply_theme(self, old, new):
        if self.closed:
            return
        py_theme.recolor_tree(self.win, old, new)
        select_fg = (py_theme.ON_ACCENT
                     if py_theme.effective_mode() == "light" else py_theme.FG)
        self.body.configure(selectforeground=select_fg)
        self.ask_entry.configure(selectforeground=select_fg)
        for button in (self.btn_close, self.btn_copy, self.btn_md,
                       self.btn_ask, self.btn_stop):
            role = button.w._role
            if role == "accent":
                button.w._bg = py_theme.ACCENT
                button.w._active = py_theme.ACCENT_HOVER
                button._fg = py_theme.ON_ACCENT
            else:
                button.w._bg = py_theme.BG2
                button.w._active = py_theme.BG4
                button._fg = (py_theme.FG_SOFT if role == "muted"
                              else py_theme.FG)
            button._disabled_fg = py_theme.FG_FAINT
            button.config_state(button.cget("state"))
        py_markdown.config_tags(self.body, py_markdown.active_palette())
        self.body.tag_configure("ask_q_tag", foreground=py_theme.ACCENT)
        py_theme.apply_window(self.win)

    def _flat_button(self, parent, text, cmd, font, muted=False,
                     accent=False):
        """统一风格按钮：无边框、悬停变色、按下变色。

        用 Label 模拟而不是 tk.Button —— 系统 Button 在深色底上
        会带一圈浅色边框和小阴影，与扁平卡片风格冲突。
        """
        if accent:
            bg, fg, active = ACCENT, py_theme.ON_ACCENT, ACCENT_HOVER
        elif muted:
            bg, fg, active = RESULT_BAR, FG_SOFT, py_theme.BG4
        else:
            bg, fg, active = RESULT_BAR, FG_MAIN, py_theme.BG4

        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=font,
                      padx=py_dpi.px(12), pady=py_dpi.px(5),
                      cursor="hand2", takefocus=0)
        lb._active = active
        lb._bg = bg
        lb._role = "accent" if accent else ("muted" if muted else "normal")

        def on_enter(e):
            if str(getattr(lb, "_enabled", True)):
                lb.configure(bg=lb._active)

        def on_leave(e):
            lb.configure(bg=lb._bg)

        def on_click(e):
            if not getattr(lb, "_enabled", True):
                return
            lb.configure(bg=lb._active)
            try:
                cmd()
            finally:
                try:
                    lb.configure(bg=lb._bg)
                except tk.TclError:
                    pass

        lb.bind("<Enter>", on_enter)
        lb.bind("<Leave>", on_leave)
        lb.bind("<Button-1>", on_click)
        lb._enabled = True
        return _StateLabel(lb)

    def _on_scroll(self, first, last):
        """内容不超过一屏时自动隐藏滚动条，减少视觉噪音。"""
        self.sb.set(first, last)
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.sb.pack_forget()
            elif not self.sb.winfo_ismapped():
                self.sb.pack(side="right", fill="y")
        except (ValueError, tk.TclError):
            pass

    def _bind_keys(self):
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        # 必须 return "break"：Tk 的 bindtags 顺序是 widget -> class ->
        # toplevel -> all，而 root.bind_all 注册在最后的 all 级。若不阻断，
        # 复制事件会继续传到全局处理器，把整个程序 quit 掉。
        # 实测见 py_ctrlc_test.py（A 对照组 copy=1 quit=1，修复后 copy=1 quit=0）。
        self.win.bind("<Control-c>", self._on_ctrl_c)

    def _on_ctrl_c(self, event=None):
        self._copy()
        return "break"

    # ---------- 追问输入框 ----------

    HINT = "继续追问…（Enter 发送，Shift+Enter 换行）"

    def _on_ask_focus(self, e=None):
        if self._hint_shown:
            self.ask_entry.delete("1.0", "end")
            self.ask_entry.configure(fg=FG_MAIN)
            self._hint_shown = False

    def _on_ask_blur(self, e=None):
        if not self.ask_entry.get("1.0", "end-1c").strip():
            self.ask_entry.delete("1.0", "end")
            self.ask_entry.configure(fg=FG_DIM)
            self.ask_entry.insert("1.0", self.HINT)
            self._hint_shown = True

    def _on_ask_return(self, e):
        # Shift+Enter 换行；单独 Enter 发送
        if e.state & 0x0001:      # Shift
            return None
        self._do_ask()
        return "break"

    def ask_text(self):
        if self._hint_shown:
            return ""
        return self.ask_entry.get("1.0", "end-1c").strip()

    def _clear_ask(self):
        self.ask_entry.delete("1.0", "end")
        self._hint_shown = False
        self.ask_entry.configure(fg=FG_MAIN)

    def _do_ask(self):
        """发起追问。"""
        if self._busy:
            self._set_status_now("上一轮还在进行中…", WARN_COLOR)
            return
        q = self.ask_text()
        if not q:
            self._set_status_now("请输入追问内容", WARN_COLOR)
            try:
                self.ask_entry.focus_set()
            except tk.TclError:
                pass
            return
        if self.on_ask is None:
            self._set_status_now("当前窗口不支持追问", ERR_COLOR)
            return

        self._clear_ask()
        self._busy = True
        self._cur_q = q          # 记下本轮问题，收尾时写进 _turns
        self.btn_ask.config_state("disabled")
        self.btn_stop.config_state("normal")
        # 在正文里插入一条分隔 + 追问标题，让多轮脉络看得见
        self._begin_turn(q)

        hist = list(self.history)
        self.history.append(("user", q))
        try:
            self.on_ask(q, hist)
        except Exception as e:
            self._drop_unanswered_question()
            self._busy = False
            self.btn_ask.config_state("normal")
            self.btn_stop.config_state("disabled")
            self._set_status_now(f"发起失败：{e}", ERR_COLOR)

    def _begin_turn(self, question):
        """在正文末尾插入追问的分隔与问题回显。"""
        tw = self.body
        tw.configure(state="normal")
        if tw.get("1.0", "end-1c").strip():
            tw.insert("end", "\n")
            start = tw.index("end-1c")
            tw.insert("end", "─" * 46 + "\n")
            tw.tag_add(py_markdown.TAG_PREFIX + "hr", start, "end-1c")
        start = tw.index("end-1c")
        tw.insert("end", "❯ 你问：", "ask_q_tag")
        tw.insert("end", question + "\n\n", "ask_q_tag")
        tw.tag_configure("ask_q_tag", foreground=py_theme.ACCENT,
                         font=py_markdown.base_font(9, italic=True),
                         lmargin1=0, lmargin2=0,
                         spacing1=py_dpi.px(4), spacing3=py_dpi.px(2))
        self._answer_start = tw.index("end-1c")
        tw.see("end")

    def _drop_unanswered_question(self):
        if (self._cur_q and self.history
                and self.history[-1] == ("user", self._cur_q)):
            self.history.pop()

    def _stop(self):
        """中断当前这一轮。"""
        with self._request_lock:
            self.cancel_event.set()
            self._request_id += 1
            if self._busy:
                self._drop_unanswered_question()
            self._busy = False
        self.btn_ask.config_state("normal")
        self.btn_stop.config_state("disabled")
        self._set_status_now("已停止", WARN_COLOR)

    # ---------- 跨线程泵 ----------

    def _start_pump(self):
        try:
            self._pump_job = self.root.after(self.PUMP_MS, self._pump)
        except (tk.TclError, RuntimeError):
            self._pump_job = None

    def _pump(self):
        """主线程执行：把队列里的动作应用到控件上。"""
        if self.closed:
            return
        # 每次最多处理 200 条，避免一次涌入太多卡住界面
        for _ in range(200):
            try:
                request_id, fn, args = self._q.get_nowait()
            except queue.Empty:
                break
            if request_id != self._request_id:
                continue
            try:
                fn(*args)
            except tk.TclError:
                return
        self._start_pump()

    def _post(self, fn, *args):
        """任意线程都可调用：只入队，不碰控件。"""
        if self.closed:
            return
        self._q.put((self._request_id, fn, args))

    # ---------- 状态 ----------

    def set_status(self, text, color=FG_MUTED):
        self._post(self._set_status_now, text, color)

    def _set_status_now(self, text, color=FG_MUTED):
        try:
            self.status_label.configure(text=text, foreground=color)
        except tk.TclError:
            pass

    def append(self, piece, request_id=None):
        """流式追加一段文本（线程安全）。"""
        with self._request_lock:
            if (request_id is not None and
                    (request_id != self._request_id or self.cancel_event.is_set())):
                return
            self._acc += piece
            self._post(self._append_now, piece)

    def _append_now(self, piece):
        try:
            self.body.insert("end", piece)
            self.body.see("end")
        except tk.TclError:
            pass

    def begin_request(self):
        """开始新一轮：清空本轮累积。

        注意不清 _cur_q：追问路径是先 _begin_turn 记下问题、再调
        on_ask，而 on_ask 里才调本方法；第一轮则由构造方在调用前
        _cur_q 保持默认空串。
        """
        with self._request_lock:
            self.cancel_event.set()
            self.cancel_event = threading.Event()
            self._request_id += 1
            self._acc = ""
            self._busy = True
            self._answer_pushed = False
            self._post(self._begin_now)

    def _begin_now(self):
        self.btn_ask.config_state("disabled")
        self.btn_stop.config_state("normal")

    def finish(self, text=None, elapsed_ms=None, request_id=None):
        with self._request_lock:
            if (request_id is not None and
                    (request_id != self._request_id or self.cancel_event.is_set())):
                return
            if text is not None:
                self._acc = text
            # 流式先显示原文，完成后一次性渲染 Markdown。
            self._post(self._render_now, self._acc, True)
            msg = "完成"
            if elapsed_ms is not None:
                msg = f"完成 · {elapsed_ms / 1000:.1f}s · {len(self._acc)} 字"
            self.set_status(msg, OK_COLOR)
            self._post(self._finish_ui_now)

    def _finish_ui_now(self):
        self.btn_ask.config_state("normal")
        self.btn_stop.config_state("disabled")
        # 把本轮回答并入历史。
        # 注意首轮情况：那时 history 还是空的（原文是作为基础 user 消息
        # 传给模型的，不进 history），若照旧用 `history[-1] == user` 判断
        # 就会整轮漏记 —— 用户第一次追问时模型看不见自己刚答过什么。
        # 所以用标志位记录"本轮回答是否已入历史"，顺带防止重复 finish 时
        # 把同一段回答写两遍。
        if not self._answer_pushed:
            self.history.append(("assistant", self._acc))
            self._answer_pushed = True
            max_items = self.MAX_HISTORY_TURNS * 2
            if len(self.history) > max_items:
                self.history = self.history[-max_items:]
        # 本轮问题用请求发起时记下的 _cur_q，不要用 history[-2] 反推 ——
        # 历史裁剪后索引会飘，而且第一轮那里存的是原文，会被误当成"追问"。
        self._turns.append((self._cur_q, self._acc))
        self._busy = False
        try:
            self.ask_entry.focus_set()
        except tk.TclError:
            pass

    def _render_now(self, text, partial=False):
        """主线程执行：把 Markdown 渲染进正文区。

        partial=True 表示这是某一轮的回答（追加到已有内容末尾），
        False 表示整窗重渲染（切换视图时用）。
        """
        target = getattr(self, "_answer_start", None)
        try:
            if partial and target:
                py_markdown.render_into(self.body, text, target)
            else:
                py_markdown.render(self.body, text)
        except Exception as e:
            try:
                self.body.insert("end", text)
            except tk.TclError:
                pass
            self._set_status_now(f"渲染失败，已显示原文：{e}", ERR_COLOR)
            return
        self.rendered = True
        try:
            self.btn_md.config_state("normal")
            self.btn_md.configure(text="原文")
        except tk.TclError:
            pass
        self._bind_body_scroll()
        try:
            self.body.see("end")
        except tk.TclError:
            pass

    def show_error(self, err, request_id=None):
        kind = getattr(err, "kind", "error")
        msg = getattr(err, "message", str(err))
        detail = getattr(err, "detail", "")
        with self._request_lock:
            if (request_id is not None and
                    (request_id != self._request_id or self.cancel_event.is_set())):
                return
            self._post(self._show_error_now, kind, msg, detail)

    def _show_error_now(self, kind, msg, detail):
        self._drop_unanswered_question()
        self._busy = False
        self.btn_ask.config_state("normal")
        self.btn_stop.config_state("disabled")
        try:
            self.body.insert("end", f"\n✗ {msg}")
            if kind == "config":
                self.body.insert(
                    "end", "\n\n请右键托盘图标 → 设置，检查供应商配置。")
            if detail:
                self.body.insert("end", f"\n\n详情：{detail[:400]}")
            start = self.body.index("end-1c linestart")
            self.body.insert("end", "\n")
            self.body.tag_add("err", start, "end-1c")
            self.body.tag_configure("err", foreground=ERR_COLOR,
                                    font=py_markdown.base_font(10))
            self.body.see("end")
        except tk.TclError:
            pass
        self._set_status_now("失败", ERR_COLOR)

    def _toggle_md(self):
        """在 Markdown 渲染视图与原始文本之间切换。"""
        self.md_mode = not self.md_mode
        if self.md_mode:
            self._render_now(self._full_raw())
            self.btn_md.configure(text="原文")
            self._set_status_now("已渲染 Markdown", FG_MUTED)
        else:
            self.body.configure(state="normal")
            self.body.delete("1.0", "end")
            self.body.insert("1.0", self._full_raw())
            self.btn_md.configure(text="渲染")
            self._set_status_now("显示原始 Markdown", FG_MUTED)
        self._bind_body_scroll()

    def _full_raw(self):
        """完整原始文本（含追问回显），用于复制与"原文"视图。

        首轮回答就是模型的完整输出，前后不加任何装饰 —— 之前统一
        加前导 "\\n\\n" 会让首轮复制出的内容凭空多两个换行，粘到别处
        开头就空一行。轮次之间用分隔线断开，分隔线两侧各留一个空行
        即可，不要和 parts 内部的 "\\n\\n" 叠加。
        """
        if not self._turns:
            return self._acc
        parts = []
        for q, a in self._turns:
            if q:
                parts.append(f"❯ 你问：{q}\n\n{a}")
            else:
                parts.append(a)
        if not parts:
            return self._acc
        sep = "\n\n" + "─" * 46 + "\n\n"
        return sep.join(parts)

    def _bind_body_scroll(self):
        """给正文区绑定滚轮，并支持 Ctrl+滚轮调字号。"""
        def on_wheel(e):
            if e.state & 0x0004:      # Ctrl：缩放字号
                self._zoom(1 if e.delta > 0 else -1)
                return "break"
            delta = -1 if e.delta > 0 else 1
            self.body.yview_scroll(delta * 3, "units")
            return "break"

        self.body.bind("<MouseWheel>", on_wheel)
        self.body.bind("<Prior>",
                       lambda e: (self.body.yview_scroll(-1, "pages"), "break")[1])
        self.body.bind("<Next>",
                       lambda e: (self.body.yview_scroll(1, "pages"), "break")[1])

    def _zoom(self, step):
        """Ctrl+滚轮调整字号（重渲染整个正文）。

        多轮追问时必须重渲染**全部轮次**：只渲染 self._acc 会把
        前面几轮从界面上抹掉（内容还在 _turns 里，但用户看不到了）。
        """
        cur = getattr(self, "_zoom_pt", 10)
        cur = max(8, min(18, cur + step))
        if cur == getattr(self, "_zoom_pt", 10):
            return
        self._zoom_pt = cur
        py_markdown.BASE_SIZE = cur
        if self.md_mode:
            self._render_now(self._full_raw())
        else:
            self.body.configure(state="normal")
            self.body.delete("1.0", "end")
            self.body.insert("1.0", self._full_raw())
        self._set_status_now(f"字号 {cur}pt", FG_MUTED)

    def _toggle_src(self):
        self.src_visible = not self.src_visible
        if self.src_visible:
            self.src_toggle.configure(text="▾ 原文")
            self.src_box.pack(fill="x", padx=py_dpi.px(14),
                             pady=(py_dpi.px(2), py_dpi.px(4)),
                             before=self.body.master)
        else:
            self.src_box.pack_forget()
            self.src_toggle.configure(text="▸ 原文")

    def _copy(self):
        """在主线程（按钮回调）执行，直接更新状态即可。

        复制的是原始 Markdown（self._acc），不是渲染后的可见文字 ——
        这样粘到别处仍是完整 Markdown。
        """
        try:
            cg.write_text(self._full_raw())
            self._set_status_now("已复制到剪贴板", OK_COLOR)
        except Exception as e:
            self._set_status_now(f"复制失败：{e}", ERR_COLOR)

    def close(self):
        self.cancel_event.set()
        self.closed = True
        if self._pump_job is not None:
            try:
                self.root.after_cancel(self._pump_job)
            except (tk.TclError, RuntimeError):
                pass
            self._pump_job = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def show_result(root, text, action):
    """兼容旧接口：只展示一段静态文本。"""
    w = ResultWindow(root, action, text)
    w.finish(text)
    return w


def request_ai(win, store, key, question, history, is_first):
    """发起首轮或追问；原文始终是对话中的首条用户消息。"""
    ok, why = store.readiness(key)
    if not ok:
        win.show_error(py_ai.AIError("config", why))
        return

    binding = store.binding(key)
    win.set_status("请求中…", WARN_COLOR)
    t0 = time.time()
    source_text = question if is_first else win.source_text
    turns = None if is_first else list(history) + [("user", question)]
    request_id = win._request_id

    def on_done(full):
        win.finish(full, (time.time() - t0) * 1000, request_id=request_id)

    try:
        py_ai.ask_async(
            key, source_text, binding,
            on_delta=lambda piece: win.append(piece, request_id=request_id),
            on_done=on_done,
            on_error=lambda err: win.show_error(err, request_id=request_id),
            cancel_event=win.cancel_event,
            history=turns,
        )
    except Exception as e:
        win.show_error(py_ai.AIError("internal", f"发起失败：{e}"))


class Watcher:
    def __init__(self, root, on_selection, pill=None):
        self.root = root
        self.on_selection = on_selection
        self.pill = pill
        self.prev_down = False
        self.last_fire = 0.0
        self.fire_count = 0
        self.hit_count = 0
        self.hit_by_level = {}
        # 拖选轨迹
        self.down_pos = None        # 按下时的坐标
        self.moved = 0.0            # 按下期间的最大位移
        self.cursor_hist = []       # 按下期间采样到的坐标序列
        self.skip_single = 0        # 因位移不足被跳过的次数
        self.skip_too_far = 0       # 因位移过大被跳过的次数
        self.auto_hide_count = 0    # 因取消选中自动关闭的次数
        self._esc_was_down = False
        # 事件合成的 Ctrl+C 会短暂改变键盘状态，用它避免我们自己触发自动关闭
        self._suppress_until = 0.0

    def start(self):
        self.root.after(POLL_INTERVAL_MS, self.tick)

    def _check_auto_hide(self, pt):
        """取消选中即关闭。返回 True 表示本次已处理（关闭）。"""
        pill = self.pill
        if not pill or not pill.visible:
            return False
        now = time.time() * 1000
        if now - pill.shown_at < FG_CHANGE_GRACE_MS:
            return False

        # 1) Esc：多数程序按 Esc 清除选区
        if AUTO_HIDE_ON_ESC:
            esc = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            if esc and not self._esc_was_down:
                self._esc_was_down = esc
                self.auto_hide_count += 1
                pill.hide()
                return True
            self._esc_was_down = esc

        # 2) 前台窗口变了：用户切走，选区随失焦通常也消失
        if AUTO_HIDE_ON_FG_CHANGE:
            fg = user32.GetForegroundWindow()
            if fg and pill.fg_at_show and fg != pill.fg_at_show:
                # 浮层自身不会成为前台（WS_EX_NOACTIVATE），所以这里出现差异
                # 一定是用户真的切到了别的窗口
                self.auto_hide_count += 1
                pill.hide()
                return True

        return False

    def tick(self):
        try:
            down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            # 浮层可见时，优先检查"取消选中"
            if self.pill and self.pill.visible:
                if self._check_auto_hide(pt):
                    self.prev_down = down
                    return

            # 按下瞬间：记录起点，重置轨迹
            if down and not self.prev_down:
                self.down_pos = (pt.x, pt.y)
                self.moved = 0.0
                self.cursor_hist = [(pt.x, pt.y)]

                # 在浮层外按下 —— 这是"取消选中"最直接的信号，立即关闭。
                # 不要求位移，因为点一下别处就会让原选区变成 caret。
                # 注意此时不清空 down_pos：用户可能接着拖选新文字。
                if (AUTO_HIDE_ON_CLICK and self.pill and self.pill.visible
                        and not self.pill.contains(pt.x, pt.y)):
                    self.auto_hide_count += 1
                    self.pill.hide()

            # 按下期间：累计位移，保留稀疏采样
            elif down and self.prev_down and self.down_pos:
                dx = pt.x - self.down_pos[0]
                dy = pt.y - self.down_pos[1]
                d = (dx * dx + dy * dy) ** 0.5
                if d > self.moved:
                    self.moved = d
                if len(self.cursor_hist) < 60:
                    self.cursor_hist.append((pt.x, pt.y))

            # 松开：判定是否算一次拖选
            elif self.prev_down and not down:
                now = time.time() * 1000
                if now - self.last_fire >= DEBOUNCE_MS:
                    if self.moved < MIN_DRAG_PX:
                        # 单击 —— 直接跳过，完全不碰剪贴板
                        # 但若浮层正显示着，说明用户在取消选中，要关掉
                        self.skip_single += 1
                        if (AUTO_HIDE_ON_CLICK and self.pill
                                and self.pill.visible
                                and not self.pill.contains(pt.x, pt.y)):
                            self.auto_hide_count += 1
                            self.pill.hide()
                    elif self.moved > MAX_DRAG_PX:
                        # 位移过大，多半是拖窗口/滚动条
                        self.skip_too_far += 1
                        if (AUTO_HIDE_ON_CLICK and self.pill
                                and self.pill.visible):
                            self.auto_hide_count += 1
                            self.pill.hide()
                    else:
                        # 有效拖选：旧浮层先收掉，再由 handle 展示新内容
                        if self.pill and self.pill.visible:
                            self.pill.hide()
                        self.last_fire = now
                        self.fire_count += 1
                        self.handle()
                self.down_pos = None
                self.cursor_hist = []

            self.prev_down = down
        except Exception as e:
            print(f"[watcher] {type(e).__name__}: {e}", file=sys.stderr)
        finally:
            self.root.after(POLL_INTERVAL_MS, self.tick)

    def handle(self):
        text, trail = acquire_text()
        if text:
            self.hit_count += 1
            lvl = trail[-1].get("level", "?")
            self.hit_by_level[lvl] = self.hit_by_level.get(lvl, 0) + 1
            x, y = POINT(), None
            user32.GetCursorPos(ctypes.byref(x))
            self.on_selection(text, x.x, x.y)
        elif os.environ.get("POP_DEBUG"):
            print(f"[miss] {[d.get('reason') for d in trail]}", file=sys.stderr)


def _proc_mem():
    """读一次自身内存/句柄/线程数。失败返回 None。

    只在启动和退出各调一次，不做持续采样、不起线程、不写文件 ——
    零常驻开销。要看详尽的实时数据用 py_mem_now.py（外部测量）。
    """
    try:
        import py_res
        return py_res.snapshot()
    except Exception:
        return None


def run_diag():
    # 诊断会打印坐标，必须用与生产一致的真实坐标系
    py_dpi.enable(verbose=True)
    print("=" * 66)
    print("诊断：当前焦点控件的三级取词能力")
    print("=" * 66)
    print(f"屏幕 {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}  "
          f"DPI {py_dpi.dpi()}  scale {py_dpi.scale():.2f}  "
          f"感知 {py_dpi.info()['level']}")
    print("-" * 66)
    print("请先切到目标程序并选中文字，然后按 Enter ...")
    try:
        input()
    except EOFError:
        pass

    fg, focus = get_focus()
    print(f"前台    hwnd=0x{fg:X} class={class_of(fg)!r}")
    print(f"焦点控件 hwnd=0x{focus:X} class={class_of(focus)!r}")

    text, trail = acquire_text()
    for d in trail:
        print(f"  [{d.get('level')}] {d.get('reason')}  {d}")
    print("-" * 66)
    if text:
        print(f"取到 {len(text)} 字符: {text!r}")
    else:
        print("未取到文本")
    return 0 if text else 2


def acquire_single_instance():
    """尝试成为唯一实例。返回 (ok, handle)。

    用命名互斥量（Local\\ 前缀 = 仅当前登录会话），跨进程有效。
    已存在实例时返回 (False, None)，调用方应直接退出 ——
    否则每多启动一次就多一个托盘图标，实测会堆积。
    """
    try:
        kernel32.CreateMutexW.restype = wt.HANDLE
        kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
        if not handle:
            return True, None        # 互斥量都建不出来，不拦
        err = ctypes.get_last_error()
        if err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False, None
        return True, handle
    except Exception:
        # 单实例是锦上添花，不该让程序起不来
        return True, None


def main():
    # --diag 是命令行诊断工具，结果必须打在屏幕上，不能被日志接管
    if "--diag" in sys.argv:
        return run_diag()

    # 日志必须最先接管。pythonw 启动时没有真实控制台，
    # print 会静默丢弃 —— 启动阶段一旦出错就完全没线索。
    # 用 --console 参数可强制把日志同时打到屏幕（开发调试用）。
    want_console = "--console" in sys.argv
    py_log.enable(verbose=want_console)

    ok, _mutex = acquire_single_instance()
    if not ok:
        msg = ("划词助手已经在运行了。\n\n"
               "请到系统托盘（右下角）右键那个蓝色图标进行操作。\n"
               "如果要重启，先在托盘菜单里选「退出」。")
        print(msg, file=sys.stderr)
        try:
            root0 = tk.Tk()
            root0.withdraw()
            messagebox.showinfo("划词助手", msg)
            root0.destroy()
        except Exception:
            pass
        return 0

    # DPI 感知必须在创建任何窗口之前声明。否则 Windows 只会报告
    # 1707x1067/96DPI，并把整个界面位图拉伸 1.5 倍（本机真实是
    # 2560x1600/144DPI）—— 文字是插值放大出来的，必然发虚。
    py_dpi.enable(verbose=bool(os.environ.get("RSPOP_DEBUG_DPI")))

    store = py_config.store()
    py_theme.set_mode(store.raw["ui"]["theme_mode"])
    _sync_result_colors()

    root = tk.Tk()
    root.withdraw()
    py_dpi.set_tk_scaling(root)
    py_markdown.init_fonts(root)

    # 应用图标。Tk 的 iconbitmap 只接受 .ico，且设在根窗口后会
    # 被同进程的所有 Toplevel 继承 —— 所以只在这里设一次即可。
    # 失败不影响功能（例如 ico 文件缺失），静默忽略。
    try:
        _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "popdict.ico")
        if os.path.exists(_ico):
            root.iconbitmap(default=_ico)
    except Exception as e:
        print(f"[icon] 设置窗口图标失败: {e}")

    pill = Pill(root, on_action=None)

    open_windows = []           # 追踪结果窗口，退出时统一关闭
    applied_palette = py_theme.colors()

    def apply_theme():
        nonlocal applied_palette
        current = py_theme.colors()
        if current == applied_palette:
            return
        old = applied_palette
        _sync_result_colors()
        pill.apply_theme(old, current)
        for result in open_windows:
            result.apply_theme(old, current)
        applied_palette = current

    def run_action(key, text):
        """发起一次 AI 调用，把结果送进结果窗口。"""
        label = {"search": "AI 搜索", "translate": "翻译",
                 "explain": "解释"}.get(key, key)

        def on_ask(question, history):
            """追问回调：由结果窗在用户按下追问时调用。"""
            win.begin_request()
            request_ai(win, store, key, question, history, is_first=False)

        win = ResultWindow(root, label, text,
                           on_ask=on_ask, action_key=key)
        open_windows.append(win)
        open_windows[:] = [w for w in open_windows if not w.closed]

        win.begin_request()
        request_ai(win, store, key, text, [], is_first=True)

    def on_action(key):
        text = pill.payload
        pill.hide()
        if key == "copy":
            cg.write_text(text)
            return
        run_action(key, text)

    pill.on_action = on_action
    watcher = Watcher(root, lambda t, x, y: pill.show_at(x, y, t), pill=pill)
    watcher.start()

    # ---------- 托盘与跨线程事件 ----------
    events = queue.Queue()
    tray = None

    def on_tray_event(name):
        # 此回调在托盘线程运行，绝不能直接操作 tkinter
        events.put(name)

    def open_settings():
        import py_settings
        py_settings.open_settings(store,
                                  on_saved=lambda: events.put("theme"),
                                  wait=False)

    def reload_config():
        store.load()
        py_theme.set_mode(store.raw["ui"]["theme_mode"])
        apply_theme()
        ok, why = store.readiness("search")
        if tray:
            tray.notify("配置已重载",
                        "AI 搜索已就绪" if ok else f"尚未就绪：{why}",
                        warning=not ok)

    def open_config_dir():
        d = py_config.config_dir()
        try:
            os.startfile(d)
        except (OSError, AttributeError):
            print(f"配置目录: {d}")

    def about():
        if tray:
            tray.notify("划词助手",
                        "拖选文字即弹出浮层。右键此图标可配置 AI 供应商。")

    def pump_events():
        try:
            while True:
                name = events.get_nowait()
                if name == "settings":
                    open_settings()
                elif name == "reload":
                    reload_config()
                elif name == "theme":
                    apply_theme()
                elif name == "open_dir":
                    open_config_dir()
                elif name == "about":
                    about()
                elif name == "exit":
                    root.quit()
                    return
        except queue.Empty:
            pass
        root.after(120, pump_events)

    try:
        import py_tray
        tray = py_tray.Tray(on_tray_event, tip="划词助手 · 拖选即弹")
        if not tray.start():
            print(f"[!] 托盘启动失败：{tray.error}", file=sys.stderr)
            tray = None
    except Exception as e:
        print(f"[!] 托盘不可用：{type(e).__name__}: {e}", file=sys.stderr)
        tray = None

    root.after(120, pump_events)

    def poll_system_theme():
        if py_theme.refresh_system():
            apply_theme()
        root.after(1000, poll_system_theme)

    root.after(1000, poll_system_theme)

    print("=" * 66)
    print("Windows 全局划词 —— 三级降级取词 + 剪贴板不污染")
    print("=" * 66)
    print(f"日志文件           : {py_log.log_path()}")
    print(f"轮询 {POLL_INTERVAL_MS}ms | 字符数 {MIN_CHARS}-{MAX_CHARS}")
    print(f"拖选判定: 位移 >= {MIN_DRAG_PX}px 才取词（单击不碰剪贴板）")
    print(f"取消选中: 点浮层外 / Esc / 切换窗口 均自动关闭")
    print(f"第1级 Win32 消息级 : 启用（不碰剪贴板）")
    print(f"第2级 UIA          : {'启用' if ENABLE_UIA else '跳过（实测收益低）'}")
    print(f"第3级 剪贴板       : {'启用（带全格式还原）' if ENABLE_CLIPBOARD else '禁用'}")
    print(f"托盘图标           : {'已启动' if tray else '不可用'}")
    for act, lbl in (("search", "AI 搜索"), ("translate", "翻译")):
        ok, why = store.readiness(act)
        print(f"{lbl:<20}: {'就绪' if ok else why}")
    print(f"配置目录           : {py_config.config_dir()}")
    print("拖选文字即弹出浮层；右键托盘图标可设置或退出")
    print("=" * 66)

    # 启动时打一行内存占用，方便事后从日志核对（不持续采样，零常驻开销）。
    # 用 --mem 可单独查看，见 py_mem_now.py。
    try:
        _m = _proc_mem()
        if _m:
            print(f"[mem] 启动占用 RSS={_m['rss'] / 1048576:.1f}MB "
                  f"私有={_m['private'] / 1048576:.1f}MB "
                  f"句柄={_m['handles']} 线程={_m['threads']}")
    except Exception:
        pass

    # 退出只留一条正路：托盘菜单「退出」。
    #
    # 这里原来还有一行 `root.bind_all("<Control-c>", lambda e: root.quit())`，
    # 想在任意位置按 Ctrl+C 都能退出（开发期图方便）。但它注册在 bindtags
    # 的 all 级 —— 也就是事件传播的最后一站，任何子窗口把自己的
    # <Control-c> 处理完只要没 return "break"，就会落到这里。
    #
    # 后果：在结果窗按 Ctrl+C 复制，复制成功的同时程序整个退出。
    # 日志里表现为"无任何异常、走完整退出流程"，极难与崩溃区分。
    # 已删除，别再为了调试方便加回来。回归测试见 py_ctrlc_test.py。
    try:
        root.mainloop()
    finally:
        # 退出顺序有讲究：
        #   1. 先让设置窗口在自己的线程里关掉（跨线程销毁 Tk 会崩）
        #   2. 再关结果窗、停托盘
        #   3. 最后打印统计
        try:
            import py_settings
            closed = py_settings.close_all(timeout=2.0)
            if closed:
                print(f"[退出] 已关闭 {closed} 个设置窗口", file=sys.stderr)
        except Exception as e:
            print(f"[退出] 关闭设置窗口时出错: {type(e).__name__}: {e}",
                  file=sys.stderr)
        for w in list(open_windows):
            try:
                w.close()
            except Exception:
                pass
        try:
            pill.close()
        except tk.TclError:
            pass
        if tray:
            tray.stop()
        print(f"\n统计: 拖选触发 {watcher.fire_count} 次 / 命中 {watcher.hit_count} 次")
        print(f"      跳过单击 {watcher.skip_single} 次（位移<{MIN_DRAG_PX}px，未碰剪贴板）")
        print(f"      取消选中自动关闭 {watcher.auto_hide_count} 次")
        if watcher.skip_too_far:
            print(f"      跳过超远拖动 {watcher.skip_too_far} 次（位移>{MAX_DRAG_PX}px）")
        if watcher.hit_by_level:
            print(f"      命中分布: {watcher.hit_by_level}")
        # 退出前再看一眼内存，用于判断长时间运行后是否明显上涨
        try:
            _m = _proc_mem()
            if _m:
                print(f"[mem] 退出占用 RSS={_m['rss'] / 1048576:.1f}MB "
                      f"私有={_m['private'] / 1048576:.1f}MB "
                      f"句柄={_m['handles']} 线程={_m['threads']}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
