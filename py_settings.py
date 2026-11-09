"""设置界面：配置 AI 供应商与搜索/翻译绑定。

线程模型（关键）：
  tkinter 不是线程安全的，一个 Tk 实例只能在创建它的线程里操作。
  主程序已有主线程的 Tk 实例（负责浮层），设置窗口不能复用，
  否则从托盘线程打开会崩。

  做法：设置窗口在同一个线程里 Tk() + mainloop()，自己跑自己的循环。
  这个线程由托盘或主程序按需启动，窗口关闭即线程结束。
  两个 Tk 实例互不干扰（实测可共存，见 py_tray_arch.py 的跨线程验证）。

布局：
  ┌─────────────────────────────────────────────┐
  │ 供应商                     搜索 / 翻译 绑定  │
  │ ┌────────┐                 ┌──────────────┐ │
  │ │ 列表   │                 │ 搜索: [下拉] │ │
  │ │        │                 │ 翻译: [下拉] │ │
  │ └────────┘                 └──────────────┘ │
  │ 名称/地址/模型/Key/超时 ...                  │
  │ [测试连接] [保存] [关闭]                     │
  └─────────────────────────────────────────────┘
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import py_ai
import py_config
import py_dpi
import py_theme

# 当前活着的设置窗口实例。用于主程序退出时统一、安全地收掉，
# 避免在错误线程里析构 Tk 对象导致进程异常退出。
_ALIVE = set()

# 配色统一由 py_theme 提供（单一真源）。
# 之前这里和 py_pop.py 各写一套，同一个"边线色"已经悄悄跑偏成
# #34343A / #3A3A42 两个值。集中后改一处全局生效。
BG = py_theme.BG
BG2 = py_theme.BG2
BG3 = py_theme.BG3
BG4 = py_theme.BG4
FG = py_theme.FG
FG_SOFT = py_theme.FG_SOFT
FG_DIM = py_theme.FG_DIM
FG_FAINT = py_theme.FG_FAINT
LINE = py_theme.LINE
ACCENT = py_theme.ACCENT
ACCENT_HOVER = py_theme.ACCENT_HOVER
ACCENT_DIM = py_theme.ACCENT_DIM
ON_ACCENT = py_theme.ON_ACCENT
OK_COLOR = py_theme.OK_COLOR
ERR_COLOR = py_theme.ERR_COLOR
WARN_COLOR = py_theme.WARN_COLOR

FONT = None          # 延迟到窗口创建后再定（需要先挑字体族）
FONT_B = None
FONT_TITLE = None


def _init_fonts(root):
    """按系统实际字体与 DPI 校准字体元组。

    必须在 Tk 实例建立之后调用 —— 字体族是否可用要问 Tk。
    DPI 感知是进程级的，设置窗在独立线程新建 Tk 时已自动继承，
    但 tk scaling 是解释器级的，必须给这个新实例单独设一次。
    """
    global FONT, FONT_B, FONT_TITLE
    py_dpi.set_tk_scaling(root)
    fam = py_dpi.pick_family(root, py_dpi.UI_FAMILIES)
    FONT = (fam, 9)
    FONT_B = (fam, 9, "bold")
    FONT_TITLE = (fam, 11, "bold")


ACTION_LABELS = {"search": "AI 搜索", "translate": "翻译", "explain": "解释"}


def style_tk(root):
    """统一的深色观感。

    几处刻意的取舍：
      * 主题固定用 clam。Windows 原生 vista 主题会忽略大半配色项
        （按钮、下拉的底色改不动），clan 是唯一能完整着色的内置主题。
      * 圆角不靠 ttk 画（ttk 元素不支持），由 DWM 在窗口级处理，
        见 py_theme.apply_dark_window。控件内部用留白和层级色区分，
        不加边框 —— 深色界面上浅色边框会显得脏。
      * 按钮高度用 padding 撑，而不是 width/height 字符数：
        中文字符宽度与字符单位预期不一致，写死会大小不一。
    """
    if FONT is None:
        _init_fonts(root)
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # ---- 容器 ----
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=BG2)
    style.configure("Bar.TFrame", background=BG2)
    style.configure("Soft.TFrame", background=BG3)

    # ---- 文字 ----
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("Dim.TLabel", background=BG, foreground=FG_DIM, font=FONT)
    style.configure("Card.TLabel", background=BG2, foreground=FG, font=FONT)
    style.configure("CardDim.TLabel", background=BG2, foreground=FG_DIM,
                    font=FONT)
    style.configure("Title.TLabel", background=BG, foreground=FG,
                    font=FONT_TITLE)
    style.configure("Sub.TLabel", background=BG, foreground=FG_DIM, font=FONT)
    # 小标题：全大写感的"分组标签"，比正文小一号、更暗、带一点字距感
    style.configure("Group.TLabel", background=BG, foreground=FG_DIM,
                    font=FONT_B)
    style.configure("Num.TLabel", background=BG2, foreground=FG,
                    font=FONT_B)
    style.configure("Hint.TLabel", background=BG2, foreground=FG_FAINT,
                    font=FONT)

    # ---- 按钮 ----
    # 同样要清掉 clam 的三圈描边，否则深色按钮会带一圈浅灰亮边
    style.configure("TButton", background=BG3, foreground=FG, font=FONT,
                    borderwidth=0, focuscolor=BG3, relief="flat",
                    bordercolor=BG3, lightcolor=BG3, darkcolor=BG3,
                    padding=(py_dpi.px(14), py_dpi.px(7)))
    style.map("TButton",
              background=[("pressed", BG4), ("active", BG4),
                          ("disabled", BG2)],
              foreground=[("disabled", FG_FAINT)],
              bordercolor=[("active", BG4), ("pressed", BG4)],
              lightcolor=[("active", BG4), ("pressed", BG4)],
              darkcolor=[("active", BG4), ("pressed", BG4)],
              relief=[("pressed", "flat"), ("active", "flat")])
    style.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT,
                    font=FONT_B, borderwidth=0, relief="flat",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    padding=(py_dpi.px(16), py_dpi.px(7)))
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_DIM), ("active", ACCENT_HOVER),
                          ("disabled", BG3)],
              foreground=[("disabled", FG_FAINT)],
              bordercolor=[("active", ACCENT_HOVER),
                           ("pressed", ACCENT_DIM)],
              lightcolor=[("active", ACCENT_HOVER),
                          ("pressed", ACCENT_DIM)],
              darkcolor=[("active", ACCENT_HOVER),
                         ("pressed", ACCENT_DIM)])
    # 幽灵按钮：透明底，只靠文字，用于次要操作
    style.configure("Ghost.TButton", background=BG, foreground=FG_DIM,
                    font=FONT, borderwidth=0, relief="flat",
                    bordercolor=BG, lightcolor=BG, darkcolor=BG,
                    padding=(py_dpi.px(12), py_dpi.px(7)))
    style.map("Ghost.TButton",
              background=[("active", BG3)],
              foreground=[("active", FG)],
              bordercolor=[("active", BG3)],
              lightcolor=[("active", BG3)],
              darkcolor=[("active", BG3)])

    # ---- 输入控件 ----
    # clam 主题的 Entry/Combobox/Spinbox 有三圈描边：bordercolor、lightcolor
    # （左上高光）、darkcolor（右下阴影），默认都是浅色。在深色底上它们会
    # 变成刺眼的白框 —— 实测截图里一眼就能看到，是整套界面最脏的地方。
    # 三圈必须一起改成背景色，看起来才是"无边框的深色输入区"。
    style.configure("TEntry", fieldbackground=BG3, foreground=FG,
                    insertcolor=FG, borderwidth=0, relief="flat",
                    bordercolor=BG3, lightcolor=BG3, darkcolor=BG3,
                    padding=(py_dpi.px(9), py_dpi.px(7)))
    style.map("TEntry",
              fieldbackground=[("focus", BG4), ("disabled", BG2)],
              foreground=[("disabled", FG_FAINT)],
              bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT_DIM)])

    style.configure("TCombobox", fieldbackground=BG3, foreground=FG,
                    background=BG3, arrowcolor=FG_DIM, borderwidth=0,
                    relief="flat", bordercolor=BG3, lightcolor=BG3,
                    darkcolor=BG3, padding=(py_dpi.px(9), py_dpi.px(7)))
    style.map("TCombobox",
              fieldbackground=[("readonly", BG3), ("active", BG4),
                               ("focus", BG4)],
              foreground=[("readonly", FG)],
              arrowcolor=[("active", FG)],
              bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", BG3)],
              darkcolor=[("focus", BG3)],
              selectbackground=[("readonly", BG3)],
              selectforeground=[("readonly", FG)])
    # 下拉弹出列表也是 tk Listbox，要单独配色，否则是系统白底
    root.option_add("*TCombobox*Listbox.background", BG3)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure("TSpinbox", fieldbackground=BG3, foreground=FG,
                    background=BG3, arrowcolor=FG_DIM, borderwidth=0,
                    relief="flat", bordercolor=BG3, lightcolor=BG3,
                    darkcolor=BG3, padding=(py_dpi.px(7), py_dpi.px(6)))
    style.map("TSpinbox",
              fieldbackground=[("focus", BG4)],
              arrowcolor=[("active", FG)],
              bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", BG3)],
              darkcolor=[("focus", BG3)])

    # ---- 列表 ----
    style.configure("Treeview", background=BG2, fieldbackground=BG2,
                    foreground=FG, borderwidth=0, font=FONT,
                    rowheight=py_dpi.px(30))
    style.configure("Treeview.Heading", background=BG2, foreground=FG_DIM,
                    font=FONT, borderwidth=0, relief="flat",
                    padding=(py_dpi.px(8), py_dpi.px(8)))
    style.map("Treeview.Heading", background=[("active", BG2)],
              relief=[("active", "flat")])
    style.map("Treeview",
              background=[("selected", ACCENT_DIM)],
              foreground=[("selected", FG)])
    # 去掉 Treeview 那圈突兀的 1px 边框
    style.layout("Treeview", [
        ("Treeview.treearea", {"sticky": "nswe"}),
    ])

    # ---- 滚动条 ----
    style.configure("Vertical.TScrollbar", background=BG3,
                    troughcolor=BG2, borderwidth=0, arrowcolor=FG_DIM,
                    relief="flat", arrowsize=py_dpi.px(10))
    style.map("Vertical.TScrollbar",
              background=[("active", BG4), ("pressed", BG4)])

    # ---- 标注 / 标签页 ----
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
    style.configure("TNotebook.Tab", background=BG, foreground=FG_DIM,
                    font=FONT, padding=(py_dpi.px(14), py_dpi.px(7)),
                    borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", BG2)],
              foreground=[("selected", FG)])

    # ---- 单选/复选 ----
    style.configure("TRadiobutton", background=BG2, foreground=FG, font=FONT,
                    borderwidth=0, focuscolor=BG2,
                    indicatorcolor=BG3)
    style.map("TRadiobutton",
              background=[("active", BG2)],
              indicatorcolor=[("selected", ACCENT), ("active", BG4)])
    style.configure("TCheckbutton", background=BG2, foreground=FG, font=FONT,
                    borderwidth=0, focuscolor=BG2, indicatorcolor=BG3)
    style.map("TCheckbutton",
              background=[("active", BG2)],
              indicatorcolor=[("selected", ACCENT)])

    # ---- 进度条 ----
    style.configure("Thin.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=BG3, borderwidth=0, thickness=py_dpi.px(4))


class SettingsWindow:
    """一次设置会话。每次打开新建实例，关闭即销毁。"""

    def __init__(self, store, on_saved=None):
        self.store = store
        self.on_saved = on_saved
        self.root = None
        self.current_id = None
        self._dirty = False
        self._prompt_drafts = {}
        self._loaded_prompt_action = None
        self._test_running = False
        # 跨线程关闭请求：只置位，由窗口自己的线程执行关闭
        self.close_request = threading.Event()

    # ---------- 生命周期 ----------

    def show(self):
        """在当前线程创建并运行窗口（阻塞直到关闭）。"""
        self.root = tk.Tk()
        self.root.title("划词助手 · 设置")
        # 设置窗口跑在独立线程、独立的 Tk 实例里，不继承主窗口的
        # iconbitmap，必须自己设一次。失败不影响功能。
        try:
            _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "popdict.ico")
            if os.path.exists(_ico):
                self.root.iconbitmap(default=_ico)
        except Exception:
            pass
        # DPI 感知已由主线程在进程级声明，这里补上本实例的 scaling
        # 与字体族挑选，否则 150% 缩放下字会小而糊。
        _init_fonts(self.root)
        # 宽高按内容定。之前写 900x600 看似够，实测在 150% DPI 下
        # 右侧表单 + 底部按钮加起来需要 700 逻辑像素，600 会把底栏
        # 挤出可视区（截图确认过）。这里放宽，同时给底部按钮固定
        # 一块不被压缩的空间（见 _build_footer 的 pack(side="bottom")）。
        self.root.geometry(f"{py_dpi.px(940)}x{py_dpi.px(700)}")
        self.root.minsize(py_dpi.px(860), py_dpi.px(600))
        self.root.configure(bg=BG)
        style_tk(self.root)
        self._build()
        self._load_providers()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda e: self._on_close())
        self._center()
        # DWM 装饰必须在窗口已映射之后调用，否则 HWND 还没真正建立。
        # 放在 _center 之后（此时 geometry 已确定），再 update 一次让它落地。
        try:
            self.root.update_idletasks()
            py_theme.apply_dark_window(self.root, small_corners=False)
        except Exception:
            pass
        # 外部请求关闭的通道。
        # 原因：这个 Tk 实例归本线程所有，别的线程直接调 quit()/destroy()
        # 会在错误线程里动 Tcl 对象，实测会让进程在收尾时崩于
        # "Tcl_AsyncDelete: async handler deleted by the wrong thread"。
        # 所以别的线程只能置标志，由本线程的定时器自己关。
        self._poll_close()
        try:
            self.root.mainloop()
        finally:
            _ALIVE.discard(self)
            # 主动释放 Tk 变量：
            # 这些 StringVar 的 __del__ 会去访问 Tcl 解释器，
            # 若留到解释器收尾阶段才回收，回收线程不是拥有该解释器的线程，
            # 就会抛 "RuntimeError: main thread is not in main loop"。
            # 在本线程主动断引用，让它在这里析构。
            self._release_vars()
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            self.root = None

    def _release_vars(self):
        """在当前线程释放所有 Tk 变量引用。"""
        try:
            for _var, cb in list(getattr(self, "bind_vars", {}).values()):
                try:
                    cb.configure(textvariable="")
                except tk.TclError:
                    pass
            if hasattr(self, "bind_vars"):
                self.bind_vars.clear()
            if getattr(self, "prompt_action", None) is not None:
                try:
                    self.prompt_action.set("")
                except tk.TclError:
                    pass
                self.prompt_action = None
        except Exception:
            pass

    def _poll_close(self):
        if self.root is None:
            return
        if self.close_request.is_set():
            try:
                self.root.quit()
            except tk.TclError:
                pass
            return
        try:
            self.root.after(120, self._poll_close)
        except tk.TclError:
            pass

    def request_close(self):
        """任意线程可调：只置标志，真正的关闭由窗口自己的线程完成。"""
        self.close_request.set()

    def _center(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{max(40, (sh - h) // 2 - 40)}")

    # ---------- 构建界面 ----------

    def _build(self):
        # 顶栏：标题 + 副标题。左右留白比内边距大一号，视觉上"呼吸"更足。
        head = ttk.Frame(self.root)
        head.pack(side="top", fill="x", padx=py_dpi.px(24),
                  pady=(py_dpi.px(18), py_dpi.px(4)))
        ttk.Label(head, text="设置", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="配置划词后「AI 搜索」「翻译」使用的模型",
                  style="Sub.TLabel").pack(side="left",
                                           padx=(py_dpi.px(10), 0),
                                           pady=(py_dpi.px(4), 0))

        # 一条极弱的分割线，把标题区和内容区分开（不加会显得空）
        ttk.Frame(self.root, style="Bar.TFrame",
                  height=1).pack(side="top", fill="x", padx=py_dpi.px(24),
                                 pady=(py_dpi.px(6), 0))

        # 关键顺序：底栏必须在 body **之前** pack。
        # tkinter 的 pack 按调用顺序分配空间，谁先 pack 谁先拿到位置。
        # 之前底栏最后 pack，窗口一缩小（或内容一多），body 把空间吃光，
        # 底栏就被挤出可视区 —— 截图里"保存/关闭"按钮完全看不到。
        # 改成先 pack 底栏（side="bottom"），再让 body 填剩下的空间。
        self._build_footer()

        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True, padx=py_dpi.px(24),
                  pady=(py_dpi.px(14), py_dpi.px(8)))

        self._build_left(body)
        self._build_right(body)

    def _section(self, parent, text, pady=(0, 6)):
        """分组小标题。比正文更暗 + 加粗，作为视觉锚点。"""
        lb = ttk.Label(parent, text=text, style="Group.TLabel")
        lb.pack(anchor="w", pady=pady)
        return lb

    def _build_left(self, parent):
        """左侧：供应商列表。"""
        wrap = ttk.Frame(parent)
        wrap.pack(side="left", fill="both", expand=False,
                  padx=(0, py_dpi.px(20)))
        # 固定左栏宽度。不给宽度时 Treeview 的 stretch 列会把剩余空间
        # 全吃掉，右侧表单被挤窄；而只有 1 个供应商时列表又空出大片
        # 深色空白（截图确认）。这里定死宽度，两侧比例稳定。
        wrap.configure(width=py_dpi.px(340))
        wrap.pack_propagate(False)

        self._section(wrap, "供应商")

        # 快速填充放在列表**上方**。原先放在最底部，此时列表已把剩余
        # 高度全部吃掉，它被挤到窗口下缘、紧贴底栏，看起来像多出来的
        # 一条（截图确认）。上移到列表之前，符合"先选模板再建条目"的
        # 操作顺序，视觉上也把左栏分成 标题 / 工具 / 列表 三段。
        pf = ttk.Frame(wrap)
        pf.pack(fill="x", pady=(0, py_dpi.px(8)))
        self.preset = ttk.Combobox(pf, state="readonly", font=FONT,
                                   values=[p["name"] for p in py_config.PRESETS])
        # readonly Combobox 没有初始值时显示为空白框，看起来像坏了。
        # 给一个明确的占位提示，用户知道这里可以点。
        self.preset.set("选择预设模板…")
        self.preset.pack(fill="x")
        self.preset.bind("<<ComboboxSelected>>", self._apply_preset)

        lb = ttk.Frame(wrap, style="Card.TFrame")
        lb.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(lb, columns=("name", "model"), show="headings",
                                 height=9, selectmode="browse")
        self.tree.heading("name", text="名称")
        self.tree.heading("model", text="模型")
        # 列宽按 DPI 缩放。之前写死 118/112，在 150% DPI 下这两列合计
        # 只有 230 物理像素，而"deepseek-v4-1-flash"这类模型名要宽得多，
        # 末尾直接被裁成 "deepseek-v4-1-flat"（截图确认）。
        # 名称列给 130、模型列给 150 逻辑像素，并把模型列设成可拉伸。
        self.tree.column("name", width=py_dpi.px(130), minwidth=py_dpi.px(80),
                         anchor="w", stretch=False)
        self.tree.column("model", width=py_dpi.px(160), minwidth=py_dpi.px(80),
                         anchor="w", stretch=True)
        self.tree.pack(side="left", fill="both", expand=True,
                       padx=(py_dpi.px(6), 0), pady=py_dpi.px(6))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # 悬停整行高亮，让"可点"这件事更明显
        self.tree.tag_configure("odd", background=BG2)
        self.tree.tag_configure("even", background=BG2)

        sb = ttk.Scrollbar(lb, orient="vertical", command=self.tree.yview,
                           style="Vertical.TScrollbar")
        sb.pack(side="right", fill="y", pady=py_dpi.px(6),
                padx=(0, py_dpi.px(2)))
        self.tree.configure(yscrollcommand=sb.set)

        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(py_dpi.px(10), 0))
        ttk.Button(btns, text="新增", command=self._add_provider).pack(
            side="left")
        ttk.Button(btns, text="复制", command=self._dup_provider).pack(
            side="left", padx=py_dpi.px(6))
        ttk.Button(btns, text="删除", command=self._del_provider).pack(
            side="left")

    def _build_right(self, parent):
        """右侧：详情 + 绑定。"""
        wrap = ttk.Frame(parent)
        wrap.pack(side="left", fill="both", expand=True)

        # --- 绑定区 ---
        self._section(wrap, "功能绑定")
        bind_card = ttk.Frame(wrap, style="Card.TFrame")
        bind_card.pack(fill="x")

        self.bind_vars = {}
        rowi = 0
        for act in ("search", "translate", "explain"):
            row = ttk.Frame(bind_card, style="Card.TFrame")
            row.pack(fill="x", padx=py_dpi.px(12),
                     pady=(py_dpi.px(10) if rowi == 0 else py_dpi.px(6), 0))
            ttk.Label(row, text=ACTION_LABELS[act], style="CardDim.TLabel",
                      width=8).pack(side="left")
            # 必须显式传 master=self.root：
            # 不传时 tkinter 会去问**默认解释器**（即主线程那个 Tk），
            # 而本窗口跑在独立线程的独立解释器里。只要主线程此刻不在
            # mainloop 中（例如正忙于 pump 队列），就会抛
            #   RuntimeError: main thread is not in main loop
            # 表现为设置窗口偶发打不开。实测已复现此异常。
            var = tk.StringVar(master=self.root)
            cb = ttk.Combobox(row, textvariable=var, state="readonly",
                              font=FONT)
            cb.pack(side="left", fill="x", expand=True)
            cb.bind("<<ComboboxSelected>>",
                    lambda e, a=act: self._on_bind_change(a))
            self.bind_vars[act] = (var, cb)
            rowi += 1

        # 提示词
        pr = ttk.Frame(bind_card, style="Card.TFrame")
        pr.pack(fill="x", padx=py_dpi.px(12),
                pady=(py_dpi.px(10), py_dpi.px(12)))
        ttk.Label(pr, text="提示词", style="CardDim.TLabel",
                  width=8).pack(side="left", anchor="n")
        self.prompt_action = tk.StringVar(master=self.root, value="search")
        pcol = ttk.Frame(pr, style="Card.TFrame")
        pcol.pack(side="left", fill="both", expand=True)
        tabs = ttk.Frame(pcol, style="Card.TFrame")
        tabs.pack(fill="x")
        for act in ("search", "translate", "explain"):
            rb = tk.Radiobutton(
                tabs, text=ACTION_LABELS[act], value=act,
                variable=self.prompt_action, command=self._load_prompt,
                bg=BG2, fg=FG_DIM, selectcolor=BG3, activebackground=BG2,
                activeforeground=FG, font=FONT, borderwidth=0,
                highlightthickness=0, cursor="hand2",
            )
            rb.pack(side="left", padx=(0, py_dpi.px(12)))
        self.prompt_text = tk.Text(pcol, height=3, bg=BG3, fg=FG, font=FONT,
                                   relief="flat", insertbackground=FG,
                                   wrap="word", padx=py_dpi.px(10),
                                   pady=py_dpi.px(8),
                                   highlightthickness=1,
                                   highlightbackground=BG3,
                                   highlightcolor=ACCENT,
                                   borderwidth=0)
        self.prompt_text.pack(fill="x", pady=(py_dpi.px(6), 0))
        self.prompt_text.bind("<<Modified>>", self._on_prompt_edit)

        # --- 详情区 ---
        self._section(wrap, "供应商详情", pady=(py_dpi.px(14), py_dpi.px(6)))
        card = ttk.Frame(wrap, style="Card.TFrame")
        card.pack(fill="both", expand=True)
        inner = ttk.Frame(card, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=py_dpi.px(12),
                   pady=py_dpi.px(12))
        inner.columnconfigure(1, weight=1)

        # 标签列固定宽度，让 名称/接口地址/模型名/API Key/参数 左边缘对齐。
        # 不固定的话每行标签各按自己文字宽度排，右边参差不齐。
        self.f_name = self._field(inner, 0, "名称")
        self.f_url = self._field(inner, 1, "接口地址",
                                 hint="形如 https://api.deepseek.com/v1")
        self.f_model = self._field(inner, 2, "模型名",
                                   hint="如 deepseek-chat / gpt-4o-mini")

        # Key 行：带显示/隐藏
        ttk.Label(inner, text="API Key", style="CardDim.TLabel").grid(
            row=3, column=0, sticky="w", pady=(py_dpi.px(8), 0))
        krow = ttk.Frame(inner, style="Card.TFrame")
        krow.grid(row=3, column=1, sticky="ew", pady=(py_dpi.px(8), 0))
        self.f_key = ttk.Entry(krow, show="•", font=FONT)
        self.f_key.pack(side="left", fill="x", expand=True)
        self._key_shown = False
        self.key_toggle = tk.Label(krow, text="显示", bg=BG3, fg=FG_DIM,
                                   font=FONT, cursor="hand2",
                                   padx=py_dpi.px(10), pady=py_dpi.px(4))
        self.key_toggle.pack(side="left", padx=(py_dpi.px(8), 0))
        self.key_toggle.bind("<Button-1>", lambda e: self._toggle_key())
        self.key_toggle.bind(
            "<Enter>", lambda e: self.key_toggle.configure(bg=BG4, fg=FG))
        self.key_toggle.bind(
            "<Leave>", lambda e: self.key_toggle.configure(bg=BG3, fg=FG_DIM))
        self.key_hint = ttk.Label(inner, text="", style="CardDim.TLabel",
                                  foreground=FG_FAINT)
        self.key_hint.grid(row=4, column=1, sticky="w",
                           pady=(py_dpi.px(2), 0))

        # 超时 / 温度
        prow = ttk.Frame(inner, style="Card.TFrame")
        prow.grid(row=5, column=1, sticky="ew", pady=(py_dpi.px(10), 0))
        ttk.Label(inner, text="参数", style="CardDim.TLabel").grid(
            row=5, column=0, sticky="w", pady=(py_dpi.px(10), 0))
        ttk.Label(prow, text="超时(秒)", style="CardDim.TLabel").pack(
            side="left", pady=(py_dpi.px(7), 0))
        self.f_timeout = ttk.Spinbox(prow, from_=5, to=300,
                                     width=5, font=FONT)
        self.f_timeout.pack(side="left", padx=(py_dpi.px(6), py_dpi.px(16)))
        ttk.Label(prow, text="温度", style="CardDim.TLabel").pack(
            side="left", pady=(py_dpi.px(7), 0))
        self.f_temp = ttk.Spinbox(prow, from_=0, to=2, increment=0.1,
                                  width=5, font=FONT)
        self.f_temp.pack(side="left", padx=(py_dpi.px(6), 0))

        # 测试结果
        self.test_label = ttk.Label(inner, text="", style="CardDim.TLabel",
                                    wraplength=py_dpi.px(420),
                                    justify="left")
        self.test_label.grid(row=6, column=0, columnspan=3, sticky="w",
                             pady=(py_dpi.px(12), 0))

    def _field(self, parent, row, label, hint=""):
        ttk.Label(parent, text=label, style="CardDim.TLabel").grid(
            row=row, column=0, sticky="w", pady=(py_dpi.px(8), 0),
            padx=(0, py_dpi.px(12)))
        ent = ttk.Entry(parent, font=FONT)
        ent.grid(row=row, column=1, sticky="ew", pady=(py_dpi.px(8), 0))
        ent.bind("<KeyRelease>", self._mark_dirty)
        if hint:
            # 提示放在输入框下方，而不是右边：右边会和窗口边缘抢空间，
            # 窄窗口时被裁掉；放下方永远够位置，也更像现代表单。
            lb = ttk.Label(parent, text=hint, style="CardDim.TLabel",
                           foreground=FG_FAINT)
            lb.grid(row=row, column=2, sticky="w",
                    padx=(py_dpi.px(10), 0), pady=(py_dpi.px(8), 0))
        return ent

    def _build_footer(self):
        # 底栏用 side="bottom"，先占住自己的位置，body 再填剩余空间。
        # 分割线放在底栏内部顶部，保证它总跟着底栏一起可见。
        foot_outer = ttk.Frame(self.root)
        foot_outer.pack(side="bottom", fill="x")
        ttk.Frame(foot_outer, style="Bar.TFrame",
                  height=1).pack(side="top", fill="x",
                                 padx=py_dpi.px(24))

        foot = ttk.Frame(foot_outer)
        foot.pack(side="top", fill="x", padx=py_dpi.px(24),
                  pady=(py_dpi.px(14), py_dpi.px(16)))
        self.status = ttk.Label(foot, text="", style="Dim.TLabel")
        self.status.pack(side="left", pady=(py_dpi.px(4), 0))

        ttk.Button(foot, text="关闭", style="Ghost.TButton",
                   command=self._on_close).pack(side="right",
                                                padx=(py_dpi.px(6), 0))
        self.btn_save = ttk.Button(foot, text="保存", style="Accent.TButton",
                                   command=self._save)
        self.btn_save.pack(side="right", padx=(py_dpi.px(6), 0))
        self.btn_test = ttk.Button(foot, text="测试连接",
                                   command=self._test)
        self.btn_test.pack(side="right")

    # ---------- 数据 <-> 界面 ----------

    def _load_providers(self, select_id=None):
        provs = self.store.providers()
        self.tree.delete(*self.tree.get_children())
        for p in provs:
            self.tree.insert("", "end", iid=p["id"],
                             values=(p["name"], p["model"] or "(未填)"))
        names = [f"{p['name']}" for p in provs]
        ids = [p["id"] for p in provs]
        self._prov_names = names
        self._prov_ids = ids
        for act, (var, cb) in self.bind_vars.items():
            cb["values"] = names
            b = self.store.binding(act)
            pid = b.get("provider_id") or (ids[0] if ids else "")
            var.set(self._name_of(pid))
            # 记住 id，避免靠名字反查（名字可能重复）
            cb._pid = pid

        target = select_id or (provs[0]["id"] if provs else None)
        if target and target in self.tree.get_children():
            self.tree.selection_set(target)
            self.tree.focus(target)
            self._load_detail(target)

        self._load_prompt()
        self._dirty = bool(self._prompt_drafts)
        self._update_save_state()

    def _name_of(self, pid):
        for p in self.store.providers():
            if p["id"] == pid:
                return p["name"]
        return ""

    def _id_of_name(self, name):
        for p in self.store.providers():
            if p["name"] == name:
                return p["id"]
        return None

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        if self._dirty:
            keep = messagebox.askyesno(
                "未保存的修改",
                "当前供应商有未保存的修改，切换将丢弃。是否继续？",
                parent=self.root,
            )
            if not keep:
                if self.current_id:
                    self.tree.selection_set(self.current_id)
                return
        self._load_detail(sel[0])

    def _load_detail(self, pid):
        p = self.store.provider(pid)
        if not p:
            return
        self.current_id = pid
        for ent, val in ((self.f_name, p.get("name", "")),
                         (self.f_url, p.get("base_url", "")),
                         (self.f_model, p.get("model", ""))):
            ent.delete(0, "end")
            ent.insert(0, val or "")
        self.f_key.delete(0, "end")
        self.f_key.insert(0, self.store.get_key(pid) or "")
        self.f_timeout.delete(0, "end")
        self.f_timeout.insert(0, str(p.get("timeout", 30)))
        self.f_temp.delete(0, "end")
        self.f_temp.insert(0, str(p.get("temperature", 0.3)))

        if self.store.has_key(pid):
            self.key_hint.configure(text="已保存密钥（本地 %APPDATA%\\PopDict）",
                                    foreground=FG_DIM)
        elif p.get("no_key"):
            self.key_hint.configure(text="该供应商无需密钥", foreground=FG_DIM)
        else:
            self.key_hint.configure(text="尚未填写密钥", foreground=WARN_COLOR)

        self.test_label.configure(text="")
        self._dirty = bool(self._prompt_drafts)
        self._update_save_state()

    def _collect(self):
        """把界面内容收集成一个供应商 dict。"""
        if not self.current_id:
            return None
        old = self.store.provider(self.current_id) or {}
        p = dict(old)
        p["id"] = self.current_id
        p["name"] = self.f_name.get().strip() or self.current_id
        p["base_url"] = self.f_url.get().strip()
        p["model"] = self.f_model.get().strip()
        try:
            p["timeout"] = max(1, min(300, int(float(self.f_timeout.get()))))
        except (ValueError, tk.TclError):
            p["timeout"] = 30
        try:
            p["temperature"] = max(0.0, min(2.0, float(self.f_temp.get())))
        except (ValueError, tk.TclError):
            p["temperature"] = 0.3
        return p

    # ---------- 交互 ----------

    def _mark_dirty(self, _e=None):
        self._dirty = True
        self._update_save_state()

    def _update_save_state(self):
        try:
            self.btn_save.configure(state="normal" if self._dirty else "disabled")
        except tk.TclError:
            pass

    def _toggle_key(self):
        self._key_shown = not self._key_shown
        self.f_key.configure(show="" if self._key_shown else "•")
        self.key_toggle.configure(text="隐藏" if self._key_shown else "显示")

    def _on_prompt_edit(self, _e=None):
        if not self.prompt_text.edit_modified():
            return
        self.prompt_text.edit_modified(False)
        self._dirty = True
        self._update_save_state()

    def _remember_prompt(self):
        act = self._loaded_prompt_action
        if not act:
            return
        text = self.prompt_text.get("1.0", "end-1c").rstrip()
        if text == self.store.binding(act).get("prompt", ""):
            self._prompt_drafts.pop(act, None)
        else:
            self._prompt_drafts[act] = text

    def _load_prompt(self):
        self._remember_prompt()
        act = self.prompt_action.get()
        b = self.store.binding(act)
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", self._prompt_drafts.get(
            act, b.get("prompt", "")))
        self.prompt_text.edit_modified(False)
        self._loaded_prompt_action = act

    def _on_bind_change(self, act):
        var, cb = self.bind_vars[act]
        pid = self._id_of_name(var.get())
        if pid:
            cb._pid = pid
            self._dirty = True
            self._update_save_state()

    def _apply_preset(self, _e=None):
        name = self.preset.get()
        for pre in py_config.PRESETS:
            if pre["name"] == name:
                self.f_url.delete(0, "end")
                self.f_url.insert(0, pre["base_url"])
                self.f_model.delete(0, "end")
                self.f_model.insert(0, pre["model"])
                if self.f_name.get().strip() in ("", "默认供应商") or \
                        self.f_name.get().strip() in [p["name"] for p in py_config.PRESETS]:
                    self.f_name.delete(0, "end")
                    self.f_name.insert(0, pre["name"])
                if pre.get("no_key"):
                    self.key_hint.configure(text="该供应商无需密钥（本地服务）",
                                            foreground=FG_DIM)
                self._dirty = True
                self._update_save_state()
                self._set_status(f"已填入 {pre['name']} 的默认参数", FG_DIM)
                break

    def _add_provider(self):
        n = 1
        existing = {p["id"] for p in self.store.providers()}
        while f"p{n}" in existing:
            n += 1
        pid = f"p{n}"
        self.store.upsert_provider({
            "id": pid, "name": f"新供应商 {n}", "base_url": "", "model": "",
            "wire": "openai", "timeout": 30, "temperature": 0.3,
            "max_tokens": 1024,
        })
        self.store.save()
        self._load_providers(select_id=pid)
        self._set_status("已新增供应商，请填写地址与模型", FG_DIM)

    def _dup_provider(self):
        if not self.current_id:
            return
        src = self._collect()
        if not src:
            return
        n = 1
        existing = {p["id"] for p in self.store.providers()}
        while f"p{n}" in existing:
            n += 1
        pid = f"p{n}"
        src["id"] = pid
        src["name"] = f"{src['name']} 副本"
        self.store.upsert_provider(src)
        key = self.store.get_key(self.current_id)
        if key:
            self.store.set_key(pid, key)
        self.store.save()
        self._load_providers(select_id=pid)
        self._set_status("已复制供应商", FG_DIM)

    def _del_provider(self):
        if not self.current_id:
            return
        name = self._name_of(self.current_id)
        if not messagebox.askyesno("删除供应商",
                                   f"确定删除「{name}」？\n引用它的功能会自动切到其他供应商。",
                                   parent=self.root):
            return
        ok, msg = self.store.remove_provider(self.current_id)
        if not ok:
            messagebox.showwarning("无法删除", msg, parent=self.root)
            return
        self.store.save()
        self._load_providers()
        self._set_status(msg, FG_DIM)

    # ---------- 保存 / 测试 ----------

    def _set_status(self, text, color=FG_DIM):
        try:
            self.status.configure(text=text, foreground=color)
        except tk.TclError:
            pass

    def _save(self):
        p = self._collect()
        if not p:
            return
        self.store.upsert_provider(p)
        self.store.set_key(self.current_id, self.f_key.get().strip())

        for act in ("search", "translate", "explain"):
            var, cb = self.bind_vars[act]
            pid = getattr(cb, "_pid", None) or self._id_of_name(var.get())
            if pid:
                self.store.set_binding(act, provider_id=pid)
        self._remember_prompt()
        for act, prompt in self._prompt_drafts.items():
            self.store.set_binding(act, prompt=prompt)

        if not self.store.save():
            messagebox.showerror("保存失败",
                                 "配置文件写入失败，请检查磁盘权限。",
                                 parent=self.root)
            return

        self._prompt_drafts.clear()
        self._loaded_prompt_action = None
        self._dirty = False
        self._update_save_state()
        self._load_providers(select_id=self.current_id)
        self._set_status("已保存", OK_COLOR)
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass

    def _test(self):
        if self._test_running:
            return
        p = self._collect()
        if not p:
            return
        # 用界面上的值临时构造 binding，不必先保存
        binding = {
            "provider_id": p["id"],
            "prompt": self.prompt_text.get("1.0", "end").rstrip(),
            "key": self.f_key.get().strip(),
            "provider": p,
        }
        self._test_running = True
        self.btn_test.configure(state="disabled", text="测试中…")
        self.test_label.configure(text="正在连接 …", foreground=FG_DIM)

        result = {}

        def worker():
            result["r"] = py_ai.test_connection(binding)

        th = threading.Thread(target=worker, daemon=True)
        th.start()

        def poll():
            if th.is_alive():
                self.root.after(120, poll)
                return
            ok, msg = result.get("r", (False, "未知错误"))
            self._test_running = False
            self.btn_test.configure(state="normal", text="测试连接")
            self.test_label.configure(
                text=("✓ " if ok else "✗ ") + msg,
                foreground=OK_COLOR if ok else ERR_COLOR)
            if ok:
                self._set_status("测试通过", OK_COLOR)
            else:
                self._set_status("测试失败", ERR_COLOR)

        self.root.after(120, poll)

    def _on_close(self):
        if self._dirty:
            r = messagebox.askyesnocancel(
                "未保存的修改",
                "有未保存的修改，是否保存后退出？",
                parent=self.root,
            )
            if r is None:
                return
            if r:
                self._save()
                if self._dirty:
                    return
        try:
            self.root.quit()
        except tk.TclError:
            pass


def open_settings(store, on_saved=None, wait=False):
    """打开设置窗口。

    wait=False 时在后台线程里开（供托盘调用，不阻塞托盘消息循环）。
    wait=True 时在当前线程阻塞运行（供主线程直接调用）。
    """
    def run():
        win = SettingsWindow(store, on_saved=on_saved)
        _ALIVE.add(win)
        try:
            win.show()
        except Exception as e:
            _ALIVE.discard(win)
            print(f"[settings] 打开失败: {type(e).__name__}: {e}",
                  file=sys.stderr)

    if wait:
        run()
        return None
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def close_all(timeout=3.0):
    """请求所有已打开的设置窗口自行关闭，并等待它们真的从登记表消失。

    为什么必须这么做：设置窗口的 Tk 实例归它自己的线程所有。
    主程序退出时如果不管它，解释器收尾阶段会在主线程里析构那些 Tcl 对象，
    实测报 "Tcl_AsyncDelete: async handler deleted by the wrong thread"
    并让进程以异常码退出（exit code 1），剪贴板还原也可能来不及跑完。

    注意：只置标志就返回是不够的 —— 调用方紧接着可能再 open_settings()，
    而旧实例此刻仍挂在 _ALIVE 里，窗口会越堆越多（实测 1→2→3），
    堆到解释器收尾时就会在 __del__ 里炸 "main thread is not in main loop"。
    所以这里必须等到登记表真的空了才算关完。
    """
    if not _ALIVE:
        return 0
    targets = list(_ALIVE)
    for w in targets:
        w.request_close()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(w in _ALIVE for w in targets):
            return len(targets)
        time.sleep(0.03)
    # 超时兜底：把残留实例从登记表摘掉，避免调用方看到脏状态
    stuck = [w for w in targets if w in _ALIVE]
    for w in stuck:
        _ALIVE.discard(w)
    return len(targets)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    open_settings(py_config.store(), wait=True)
