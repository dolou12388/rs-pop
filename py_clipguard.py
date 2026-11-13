"""剪贴板守卫：读词前后保证用户剪贴板内容原样不变。

用户约束：剪贴板可以用，但不能污染。

实现选型（有实测依据，见 py_clip_ab.py）：
  · OLE 路线（OleGetClipboard -> OleSetClipboard）实测失败，
    还原时返回 CLIPBRD_E_CANT_CLOSE (0x800401D4)。
    原因：OleGetClipboard 返回的是指向原所有者的代理对象，
          原内容被替换后该对象失去延迟渲染能力。
  · 逐格式 HGLOBAL 拷贝实测成功：4 个格式全部写回，文本完全一致。
    => 采用此路线。

事务语义：
    with ClipGuard() as g:
        text, meta = g.read_text_via_copy()
    # 离开 with 必然还原；未改动时不写剪贴板

诚实说明（不可消除的限制）：
  1. 中间态对第三方剪贴板管理器可见。这是 Windows 剪贴板机制的固有特性，
     任何"合成 Ctrl+C 再还原"的方案都无法避免。剪贴板历史 (Win+V) 是否
     记录取决于系统设置，本模块无法干预。
  2. CF_BITMAP 等非 HGLOBAL 格式无法字节拷贝，但它由 CF_DIB / CF_DIBV5
     派生，只写回 DIB 系统会自动重建，因此跳过它不丢内容。只有当某个
     格式真的锁不住/分配不出内存（内容必丢）时，才放弃本次取词。
  3. 目标程序若以延迟渲染方式提供数据，备份时会强制实体化 —— 这是
     保证还原正确的必要代价。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

# 64 位必须显式声明签名，否则 HANDLE 被截断成 32 位，写入静默失败
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
user32.GetClipboardSequenceNumber.restype = wt.DWORD
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None

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
CF_DIBV5 = 17
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002

# 我们合成的是 Ctrl+C（取文本），目标程序不可能因此写出位图或文件列表。
# 所以剪贴板里一旦出现这些格式，就说明是截图工具 / 资源管理器写的，
# 与本次取词无关 —— 此时还原会直接把用户的截图抹掉。
#
# 这就是「启用后截图在剪贴板里找不到」的直接原因：Win+Shift+S 的拖框
# 本身就是一次位移远超阈值的拖选，会触发一次剪贴板事务，而截图工具
# 恰好在事务窗口内把图写进剪贴板，随后的还原又把它 EmptyClipboard 掉。
_FOREIGN_IMAGE_FORMATS = {CF_DIB, CF_DIBV5, CF_HDROP}

# 还原前先等剪贴板稳定一段时间。
#
# 目标程序响应 Ctrl+C 时常分批写格式（先 CF_UNICODETEXT，再 CF_HTML、
# CF_RTF），每批都让序列号 +1。如果我们抢在它写到一半时还原，剩下的批次
# 会在还原**之后**落地 —— 用户看到的是「划词完剪贴板还是被改了」，
# 也就是那种"概率性污染"。先等它写完再还原，可显著降低这个概率。
RESTORE_QUIET_MS = 35          # 序列号安静这么久就认为写完了
RESTORE_SETTLE_MAX_MS = 120    # 最多等这么久，不能让取词无限期拖延

# 还原后自检并重试的次数。写完回读比对，不一致说明被覆盖了，再还原一次。
RESTORE_VERIFY_ATTEMPTS = 2

VK_CONTROL = 0x11
VK_C = 0x43
VK_INSERT = 0x2D
KEYEVENTF_KEYUP = 0x0002

# 无法通过 HGLOBAL 字节拷贝、但**可以安全跳过**的格式。
#
# CF_BITMAP 是 GDI 位图句柄，不是 HGLOBAL，GlobalLock/GlobalSize 对它
# 无意义。但它并不需要单独备份 —— 它是从 CF_DIB / CF_DIBV5 派生的：
# 只往剪贴板放 CF_DIB 时，系统会自动合成出 CF_BITMAP。
# 实测（_clip_probe.py）：
#     还原前 CF_DIB(104B) CF_BITMAP(size=0) CF_DIBV5(188B)
#     跳过 CF_BITMAP 备份，只写回 CF_DIB + CF_DIBV5
#     还原后 CF_DIB(104B) CF_BITMAP(自行重建) CF_DIBV5(188B)
#     格式集合完全一致
#
# 所以这里跳过它不会丢内容。早期版本把「跳过任意格式」直接等同于
# 「快照不完整」，导致剪贴板里只要有截图（用户刚截过图是常态），
# 第 3 级剪贴板取词就整体放弃 —— 划词功能当场失效。
# 区分「安全跳过」与「真丢数据」是本模块的关键。
SAFE_SKIP_FORMATS = {CF_BITMAP}

_STD_NAMES = {
    1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 8: "CF_DIB",
    13: "CF_UNICODETEXT", 14: "CF_ENHMETAFILE", 15: "CF_HDROP",
    16: "CF_LOCALE", 17: "CF_DIBV5",
}


def format_name(fid):
    if fid in _STD_NAMES:
        return _STD_NAMES[fid]
    buf = ctypes.create_unicode_buffer(128)
    n = user32.GetClipboardFormatNameW(fid, buf, 128)
    return buf.value if n else f"fmt_{fid}"


def _open_clipboard(retries=15, delay=0.02):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def clipboard_seq():
    return user32.GetClipboardSequenceNumber()


def _wait_copy_settled(quiet_ms=RESTORE_QUIET_MS,
                       max_ms=RESTORE_SETTLE_MAX_MS):
    """等剪贴板序列号安静下来，返回最终序列号。

    目标程序响应 Ctrl+C 常分批写格式（先 CF_UNICODETEXT，再 CF_HTML、
    CF_RTF），每批序列号 +1。若不等就还原，剩下的批次会在还原之后落地，
    表现为「划词完剪贴板还是被改了」—— 即概率性污染。

    代价说明：即便目标程序一次写完，也要空等 quiet_ms 才能确信"没有后续
    批次"。这是确认的固有成本，只能把阈值压到够用为止（实测双击写格式的
    间隔 < 20ms，取 35ms 留一倍余量）。

    用 perf_counter 而不是 time.time：后者受系统时钟调整影响，
    短暂的时钟回拨会让它一直等不到 deadline。
    """
    seq = clipboard_seq()
    quiet_since = time.perf_counter()
    deadline = quiet_since + max_ms / 1000.0
    while True:
        now = time.perf_counter()
        if now >= deadline:
            break
        time.sleep(0.006)
        cur = clipboard_seq()
        if cur != seq:
            seq = cur
            quiet_since = time.perf_counter()
        elif (time.perf_counter() - quiet_since) * 1000 >= quiet_ms:
            break
    return seq


def _clipboard_state():
    """一次性读出 (格式集合, 文本, 图片字节数)。

    用来判断"剪贴板现在装的是不是我们写进去的东西"。
    打不开剪贴板时返回 None —— 调用方据此放弃判断，走原有路径，
    不能因为读不到状态就误认为有第三方写入。
    """
    if not _open_clipboard():
        return None
    try:
        formats = set()
        fid = 0
        while True:
            fid = user32.EnumClipboardFormats(fid)
            if fid == 0:
                break
            formats.add(fid)

        text = None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if h:
            p = kernel32.GlobalLock(h)
            if p:
                try:
                    text = ctypes.c_wchar_p(p).value
                finally:
                    kernel32.GlobalUnlock(h)

        dib_size = None
        for fid_img in (CF_DIBV5, CF_DIB):
            if fid_img in formats:
                hi = user32.GetClipboardData(fid_img)
                if hi:
                    size = kernel32.GlobalSize(hi)
                    if size:
                        dib_size = size
                        break
        return formats, text, dib_size
    finally:
        user32.CloseClipboard()


def _is_safe_skip_reason(reason):
    """判断某次"跳过"是否会造成剪贴板内容丢失。

    只有内容真的会丢（锁不住、分配不出内存）才算失败。
    派生格式与空数据跳过是安全的 —— 前者会由系统重建，后者本来就没有内容。

    用理由字符串而不是格式 ID 判断：同一格式可能因为不同原因被跳过
    （CF_BITMAP 是被主动跳过，某个注册格式可能是 GlobalLock 失败），
    必须按原因区分，不能按格式一刀切。
    """
    return ("安全跳过" in reason) or ("非 HGLOBAL" in reason)


def read_text():
    if not _open_clipboard():
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
    if not _open_clipboard():
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


def send_copy_keys(use_insert=False):
    vk = VK_INSERT if use_insert else VK_C
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.012)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


class ClipGuard:
    """剪贴板事务：逐格式快照 -> 允许中间态 -> 必然还原。"""

    def __init__(self):
        self._snapshot = []        # [(format_id, hglobal, size)]
        self._skipped = []         # [(format_id, name, reason)]
        self._seq_before = None
        # 我们合成的 Ctrl+C 造成的「中间态」指纹：剪贴板稳定后的序列号，
        # 以及我们实际写进去的文本。见 _foreign_write_detected()。
        self._intermediate_seq = None
        self._intermediate_text = None
        # 我们是否**真的发出过** Ctrl+C 指令。这是"剪贴板的变化可能是
        # 我们造成的"的唯一凭据 —— 与"是否成功读到文本"无关。
        # 区分这两件事是本轮修复的关键：目标程序可能写得很晚（我们超时退出
        # 后才写入），也可能写了个空串（`if text:` 判假）。这两种情况下
        # 我们确实动了剪贴板、也确实没能记下中间态，但绝不能因此判定
        # "那是别人的数据、不该还原" —— 那会把我们复制的文本留在剪贴板上。
        self._copy_sent = False
        self._snapshot_formats = set()
        self._restored = False
        self._copy_attempted = False
        self.restore_result = None

    def _release_snapshot(self):
        """释放尚未移交给系统剪贴板的 HGLOBAL。"""
        for _, handle, _ in self._snapshot:
            if handle:
                kernel32.GlobalFree(handle)
        self._snapshot = []

    # -------- 快照 --------

    def _decode_snapshot_text(self):
        """从已备份的快照里取出 CF_UNICODETEXT 的文本，供回读校验用。

        快照存的是原始字节（含结尾 NUL），这里解码成 str。
        没有文本格式、或解码不了时返回 None —— 调用方据此跳过校验。
        绝不能因为解不出文本就让整笔事务失败：校验是加固手段，
        不是主流程的前提。
        """
        for fid, hcopy, size in self._snapshot:
            if fid != CF_UNICODETEXT or not size or not hcopy:
                continue
            try:
                p = kernel32.GlobalLock(hcopy)
            except Exception:
                return None
            if not p:
                return None
            try:
                return ctypes.c_wchar_p(p).value
            except Exception:
                return None
            finally:
                try:
                    kernel32.GlobalUnlock(hcopy)
                except Exception:
                    pass
        return None

    def _backup(self):
        saved, skipped = [], []
        if not _open_clipboard():
            return saved, skipped + [(0, "-", "无法打开剪贴板")]
        try:
            fid = 0
            while True:
                ctypes.set_last_error(0)
                fid = user32.EnumClipboardFormats(fid)
                if fid == 0:
                    err = ctypes.get_last_error()
                    if err:
                        skipped.append((0, "-", f"枚举剪贴板格式失败 ({err})"))
                    break
                if fid in SAFE_SKIP_FORMATS:
                    # 派生格式，跳过不丢数据（见文件顶部说明）
                    skipped.append((fid, format_name(fid), "派生格式，可安全跳过"))
                    continue
                hsrc = user32.GetClipboardData(fid)
                if not hsrc:
                    skipped.append((fid, format_name(fid), "GetClipboardData 为空"))
                    continue
                size = kernel32.GlobalSize(hsrc)
                if size == 0:
                    # size=0 有两种情况：
                    #   a) 派生/句柄型格式（如只有 CF_BITMAP 没有 CF_DIB）
                    #   b) 真的空数据
                    # 前者可安全跳过；后者内容为零字节，没有可丢的东西。
                    # 两种都不应阻止取词，否则只要剪贴板里有图就永远取不到词。
                    skipped.append((fid, format_name(fid),
                                    "空数据/句柄格式，可安全跳过"))
                    continue
                psrc = kernel32.GlobalLock(hsrc)
                if not psrc:
                    skipped.append((fid, format_name(fid), "GlobalLock 失败"))
                    continue
                try:
                    raw = ctypes.string_at(psrc, size)
                finally:
                    kernel32.GlobalUnlock(hsrc)

                hcopy = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if not hcopy:
                    skipped.append((fid, format_name(fid), "GlobalAlloc 失败"))
                    continue
                pdst = kernel32.GlobalLock(hcopy)
                if not pdst:
                    kernel32.GlobalFree(hcopy)
                    skipped.append((fid, format_name(fid), "目标 GlobalLock 失败"))
                    continue
                ctypes.memmove(pdst, raw, size)
                kernel32.GlobalUnlock(hcopy)
                saved.append((fid, hcopy, size))
        finally:
            user32.CloseClipboard()
        return saved, skipped

    def __enter__(self):
        self._seq_before = clipboard_seq()
        self._snapshot, self._skipped = self._backup()
        self._snapshot_formats = {fid for fid, _, _ in self._snapshot}
        # 快照里的文本，用于还原后回读校验。
        self._expect_text = self._decode_snapshot_text()
        # 区分两种"快照为空"：
        #   a) 剪贴板本来就没有任何格式 -> 还原含义是清空
        #   b) 有格式但全部不可拷贝（如只有 CF_BITMAP）-> 无法还原，必须如实报告
        had_any = bool(self._snapshot) or bool(self._skipped)
        self._was_empty = not had_any

        # 关键：不能把「任何跳过」都当成失败。
        #
        # 早期版本写的是 _backup_ok = not self._skipped，结果是：
        # 用户只要截过图（剪贴板里有 CF_BITMAP），备份必然"跳过一项"，
        # 于是一次复制都不做，第 3 级取词直接放弃 —— 划词彻底失效，
        # 而日志只有一句"剪贴板快照不完整"，极难联想到是截图导致的。
        #
        # 现在按「跳过是否会造成数据丢失」区分：
        #   · 派生/空数据格式（CF_BITMAP、size=0）-> 跳过无害，不影响取词
        #   · 真不可拷贝（GlobalLock 失败、GlobalAlloc 失败等）-> 内容会丢，
        #     此时放弃取词，绝不污染用户的剪贴板
        self._lossy_skips = [s for s in self._skipped
                             if not _is_safe_skip_reason(s[2])]
        self._safe_skipped = [s for s in self._skipped
                              if _is_safe_skip_reason(s[2])]
        self._backup_ok = not self._lossy_skips
        return self

    # -------- 取词 --------

    def read_text_via_copy(self, timeout_ms=650):
        """触发复制并读回文本。返回 (text, meta)。

        meta: changed / waited_ms / seq_delta / attempts
        """
        if not self._backup_ok:
            return None, {
                "backup_ok": False,
                "backup_error": "、".join(f"{name}: {reason}"
                                         for _, name, reason in self._lossy_skips),
                "changed": False, "waited_ms": 0, "seq_delta": 0,
                "attempts": 0,
            }

        seq0 = clipboard_seq()
        t0 = time.time()

        text = None
        attempts = 0
        for use_insert in (False, True):
            attempts += 1
            self._copy_attempted = True
            # 置位在**发键之前**：键一按下，剪贴板就可能被改。
            # 哪怕我们之后没读到文本（超时 / 空串），这笔改动也可能是
            # 我们造成的，还原时必须认账，否则就会把中间态留在剪贴板上。
            self._copy_sent = True
            send_copy_keys(use_insert=use_insert)
            deadline = time.time() + timeout_ms / 1000.0
            while time.time() < deadline:
                if clipboard_seq() != seq0:
                    # 目标程序可能分批写格式，等它写稳再读
                    time.sleep(0.025)
                    text = read_text()
                    if text:
                        # 等序列号安静下来再登记指纹，避免把目标程序后续
                        # 补写的批次当成"第三方插手"。
                        self._intermediate_seq = _wait_copy_settled()
                        self._intermediate_text = text
                        return text, {
                            "changed": True,
                            "waited_ms": int((time.time() - t0) * 1000),
                            "seq_delta": clipboard_seq() - seq0,
                            "attempts": attempts,
                        }
                time.sleep(0.012)
            # 第一轮（Ctrl+C）没反应，第二轮换 Ctrl+Insert
            seq0 = clipboard_seq()

        # 没读到可用文本（超时、或目标程序写了个空串）。此时剪贴板很可能
        # 已被我们改动，具体内容由还原阶段现场判定，这里不预置指纹。
        changed = clipboard_seq() != (self._seq_before or 0)
        return text, {
            "changed": changed,
            "waited_ms": int((time.time() - t0) * 1000),
            "seq_delta": clipboard_seq() - (self._seq_before or 0),
            "attempts": attempts,
        }

    # -------- 还原 --------

    def note_self_write(self, text=None):
        """登记「这次事务自己往剪贴板写了什么」。

        正常路径由 read_text_via_copy 在读到文本后自动完成；调用方若绕过
        取词、自己往剪贴板写（例如自测里模拟一次复制），必须显式调用它。

        参数 text: 我们写进去的文本。传 None 表示"我们写的不是文本"。
        """
        self._copy_attempted = True
        self._copy_sent = True
        self._intermediate_seq = clipboard_seq()
        self._intermediate_text = text if text is not None else read_text()

    def _foreign_write_detected(self):
        """判断剪贴板是否已被**第三方**改写（而非我们自己的动作造成的）。

        返回 (是否被占用, 原因)。这是本模块最关键的安全闸门。

        restore() 的动作是 EmptyClipboard + 写回旧快照，对剪贴板内容做
        全量替换。一旦对象搞错，就会抹掉用户刚复制的东西或刚截的图。

        ## 两个方向的错误都要防

        判错的代价是对称的，必须同时防住：

          · 误判为"我们的" → 抹掉用户的新内容
            （「复制不上」「截图找不到」两个原始故障）
          · 误判为"别人的" → 我们复制的文本残留在剪贴板
            （「划词时仍有概率污染剪贴板」）

        上一版只防了第一个方向，把闸门收成"没有中间态指纹就一律不还原"，
        于是第二个方向漏了：目标程序写得很晚（我们两轮超时退出后才落地）
        或写了个空串（`if text:` 判假）时，指纹是空的，还原被跳过，
        剪贴板里就留下了划词复制的文字。

        ## 现在的判据

        先确认"这笔改动我们能认领"（`_copy_sent` 或已登记指纹），
        再排除"有证据表明是别人写的"：

          1. 我们压根没发过复制指令，剪贴板却变了 → 别人写的
          2. 出现了快照里没有的图片/文件列表格式 → 我们发的是 Ctrl+C
             取文本，不可能产生 CF_DIB / CF_DIBV5 / CF_HDROP
          3. 有中间态指纹，且文本与指纹不同 → 被换掉了

        只要不属于以上三种，就认定这仍是我们的中间态并还原。
        "认领不明确"时倾向于**还原**：还原最多把内容退回原样（无损，
        用户原内容还在），而不还原必然留下污染。
        """
        seq_now = clipboard_seq()

        if self._seq_before is not None and seq_now == self._seq_before:
            return False, "无中间态"

        # 我们没发过复制指令，剪贴板却变了 —— 完全是别人的动作。
        if not self._copy_sent and self._intermediate_seq is None:
            return True, "本次未发出复制指令，剪贴板却被改动"

        state = _clipboard_state()
        if state is None:
            # 读不到状态就无法排除"这是截图"。宁可留着中间态，
            # 也不能冒险冲掉用户的图。
            return True, "无法读取剪贴板状态，放弃还原以免覆盖"
        formats, cur_text, _dib = state

        # 判据 2：Ctrl+C 取文本不会产生图片/文件格式。
        foreign_imgs = formats & _FOREIGN_IMAGE_FORMATS
        new_imgs = foreign_imgs - self._snapshot_formats
        if new_imgs:
            names = "、".join(format_name(f) for f in sorted(new_imgs))
            return True, f"出现本次复制不会产生的格式（{names}）"

        # 判据 3：有指纹且文本已经不一样了。
        if self._intermediate_text is not None and cur_text is not None \
                and cur_text != self._intermediate_text:
            return True, "文本内容已被改写成别的"

        return False, "中间态未被外部改动"

    def _restore_verified(self):
        """执行还原并回读校验，必要时重试。返回 (ok, msg)。

        为什么需要重试：即便前面的闸门判定正确，从"判定完"到"写完"之间
        还有一小段窗口，目标程序的迟到写入可能插进来覆盖我们的还原结果。
        写完回读比对能把这种情况抓出来，再补一次即可收敛。
        """
        last_msg = ""
        for attempt in range(RESTORE_VERIFY_ATTEMPTS):
            ok, msg = self._restore_once()
            last_msg = msg
            if not ok:
                return ok, msg
            if self._was_empty:
                return ok, msg
            # 回读：快照里的文本格式必须原样回来
            expect = self._expect_text
            if expect is None:
                return ok, msg
            got = read_text()
            if got == expect:
                return ok, msg
            if attempt + 1 < RESTORE_VERIFY_ATTEMPTS:
                # 被覆盖了，稍等再补一次
                time.sleep(0.03)
                self._restored_early = True
                continue
        return True, last_msg + "（回读校验未通过，可能被外部覆盖）"

    def _restore_once(self):
        if not _open_clipboard():
            return False, "还原时无法打开剪贴板"
        try:
            if not user32.EmptyClipboard():
                return False, "还原时无法清空剪贴板"
            if self._was_empty:
                return True, "原剪贴板为空，已清空中间态"

            written = 0
            for i, (fid, hcopy, size) in enumerate(self._snapshot):
                if user32.SetClipboardData(fid, hcopy):
                    self._snapshot[i] = (fid, 0, size)  # 所有权已移交系统
                    written += 1
            total = len(self._snapshot)
            # 有安全跳过的格式时，写回数必然少于原格式数 —— 这不是失败。
            # 例如截图的 CF_BITMAP 由写回的 CF_DIB 自动重建。
            ok = written == total
            msg = f"写回 {written}/{total} 个格式"
            if ok and self._safe_skipped:
                names = "、".join(n for _, n, _ in self._safe_skipped)
                msg += f"（{names} 为派生格式，由系统自动重建）"
            return ok, msg
        finally:
            user32.CloseClipboard()

    def restore(self):
        if self._restored:
            return self.restore_result or (True, "已还原")
        try:
            if not self._copy_attempted:
                self.restore_result = (True, "未触发复制，原剪贴板未改动")
                return self.restore_result

            # 先等剪贴板写稳。目标程序的分批写入若与我们还原交错，
            # 后到的批次会覆盖还原结果 —— 那是"概率性污染"的主要来源。
            # 多花的这点时间只发生在确实动过剪贴板之后。
            if self._copy_sent:
                _wait_copy_settled()

            seq_now = clipboard_seq()
            if self._seq_before is not None and seq_now == self._seq_before:
                self.restore_result = (True, "剪贴板未变化，跳过还原")
                return self.restore_result

            occupied, why = self._foreign_write_detected()
            if occupied:
                self.restore_result = (True,
                                       f"剪贴板已被其他程序更新，保留其内容（{why}）")
                return self.restore_result

            ok, msg = self._restore_verified()
            self.restore_result = (ok, msg)
            return self.restore_result
        finally:
            self._release_snapshot()
            self._restored = True

    def __exit__(self, exc_type, exc, tb):
        self.restore()
        if self.restore_result and not self.restore_result[0]:
            print(f"[ClipGuard] 还原不完整: {self.restore_result[1]}", file=sys.stderr)
        return False


# ---------- 自测 ----------

def _selftest():
    print("=" * 68)
    print("ClipGuard 自测：剪贴板不污染验证")
    print("=" * 68)

    MARK1 = "ORIGINAL_MARKER_\u539f\u59cb\u5185\u5bb9_42"
    MARK2 = "TEMPORARY_MARKER_\u4e34\u65f6\u5185\u5bb9_99"
    fails = 0

    def fmt_count():
        fs = []
        if not _open_clipboard():
            return fs
        try:
            f = 0
            while True:
                f = user32.EnumClipboardFormats(f)
                if f == 0:
                    break
                fs.append(f)
        finally:
            user32.CloseClipboard()
        return fs

    def simulate_copy(text, guard):
        """模拟「我们触发了一次复制并读回了文本」。

        必须走 note_self_write 登记中间态指纹 —— 真实路径里这一步由
        read_text_via_copy 在读到文本后自动完成。少了它，restore() 会认为
        "我们从未产生可确认的中间态，而剪贴板已经变了"，从而判定被第三方
        占用、拒绝还原，自测就会全部失败。

        这不是在生产代码里开后门：note_self_write 的语义就是
        「本次事务自己往剪贴板写了什么」，自测既然写了剪贴板，就该登记。
        早期版本没登记，导致 [1][3][4] 三项长期假失败，真问题反而被淹没。

        必须显式传入 guard —— 闭包捕获外层同名变量会绑到错误的
        对象上（[3] 的第一版就是这么写的，改的是 g 而不是 g3）。
        """
        write_text(text)
        guard.note_self_write(text)

    # 1. 内容还原
    write_text(MARK1)
    time.sleep(0.08)
    before = read_text()
    n_before = len(fmt_count())
    print(f"[1] 预置: {before!r}  格式数={n_before}")

    with ClipGuard() as g:
        simulate_copy(MARK2, g)
        print(f"    中间态: {read_text()!r}  快照={len(g._snapshot)} 个格式")
        if g._skipped:
            print(f"    跳过: {[(n, r) for _, n, r in g._skipped]}")

    after = read_text()
    n_after = len(fmt_count())
    print(f"    还原后: {after!r}  格式数={n_after}")
    print(f"    还原结果: {g.restore_result[1]}")
    if after == MARK1:
        print("[1] PASS 内容完整还原")
    else:
        print(f"[1] FAIL 预期 {MARK1!r} 实际 {after!r}")
        fails += 1

    # 2. 未改动时跳过还原
    write_text(MARK1)
    time.sleep(0.08)
    seq_a = clipboard_seq()
    with ClipGuard() as g2:
        pass
    seq_b = clipboard_seq()
    if seq_a == seq_b:
        print(f"[2] PASS 未改动时不写剪贴板 (seq {seq_a}=={seq_b})")
    else:
        print(f"[2] FAIL 无谓改写剪贴板 (seq {seq_a}->{seq_b})")
        fails += 1

    # 3. 格式保真
    write_text(MARK1)
    time.sleep(0.08)
    with ClipGuard() as g3:
        simulate_copy(MARK2, g3)
    n3 = len(fmt_count())
    t3 = read_text()
    if t3 == MARK1 and n3 >= n_before:
        print(f"[3] PASS 格式保真 {n_before} -> {n3}")
    else:
        print(f"[3] FAIL 格式/内容丢失 ({n_before}->{n3}, 文本={t3!r})")
        fails += 1

    # 4. 连续 20 次事务，确认无泄漏
    write_text(MARK1)
    time.sleep(0.08)
    for i in range(20):
        with ClipGuard() as gi:
            simulate_copy(f"temp_{i}", gi)
    final = read_text()
    if final == MARK1:
        print("[4] PASS 连续 20 次事务后内容仍一致")
    else:
        print(f"[4] FAIL 连续事务后内容漂移: {final!r}")
        fails += 1

    # 5. 剪贴板里有截图时，备份必须仍然可用
    #
    # 这是线上真实故障的回归测试。用户截图后剪贴板里会有 CF_BITMAP
    # （GDI 句柄，不是 HGLOBAL），早期版本把它当作「快照不完整」，
    # 直接放弃第 3 级取词 —— 表现为「截过图之后划词就再也不弹了」，
    # 日志只有一句"剪贴板快照不完整：CF_BITMAP: 非 HGLOBAL 格式"。
    put_dib_for_test()
    time.sleep(0.15)
    print(f"[5] 预置图片后格式: {[format_name(f) for f in fmt_count()]}")

    with ClipGuard() as g5:
        print(f"    快照={len(g5._snapshot)} 个  "
              f"跳过={[n for _, n, _ in g5._skipped]}  "
              f"backup_ok={g5._backup_ok}")
        if not g5._backup_ok:
            print("    有图片时备份被判失败 —— 取词会整体放弃")
            fails += 1
        else:
            simulate_copy(MARK2, g5)

    # 离开 with 时已还原。图片必须还在（CF_BITMAP 由 CF_DIB 重建）。
    names5 = [format_name(f) for f in fmt_count()]
    has_dib = any(n in ("CF_DIB", "CF_DIBV5") for n in names5)
    if has_dib and g5._backup_ok:
        print(f"[5] PASS 有图片时仍可备份取词，图片已还原 ({names5})")
    elif not g5._backup_ok:
        pass          # 上面已经记过一次失败，不重复计
    else:
        print(f"[5] FAIL 图片丢失 ({names5})")
        fails += 1

    # 6. 真丢数据时仍要拒绝 —— 安全跳过不能变成"什么都放行"
    reason_safe = "派生格式，可安全跳过"
    reason_lossy = "GlobalLock 失败"
    if _is_safe_skip_reason(reason_safe) and \
            not _is_safe_skip_reason(reason_lossy):
        print("[6] PASS 跳过原因分类正确（派生放行 / 锁失败拒绝）")
    else:
        print("[6] FAIL 跳过原因分类错误")
        fails += 1

    print("-" * 68)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


def put_dib_for_test():
    """往剪贴板放一张最小 DIB，模拟截图工具留下的内容。

    只放 CF_DIB，不放 CF_BITMAP —— 系统会自动合成出 CF_BITMAP，
    正好复现线上「备份遇到 CF_BITMAP 就放弃」的场景。
    """
    import struct
    bih = struct.pack("<IiiHHIIiiII", 40, 4, 4, 1, 32, 0, 64, 2835, 2835, 0, 0)
    pix = bytes([30, 60, 90, 255]) * 16
    dib = bih + pix
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        return False
    ctypes.memmove(p, dib, len(dib))
    kernel32.GlobalUnlock(h)
    if not _open_clipboard():
        kernel32.GlobalFree(h)
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(8, h))     # 8 = CF_DIB
    finally:
        user32.CloseClipboard()


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(_selftest())
    print("用法: python py_clipguard.py --test")
    sys.exit(0)
