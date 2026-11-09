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
  2. CF_BITMAP 等非 HGLOBAL 格式无法字节拷贝。遇到无法完整备份的
     剪贴板内容时，跳过本次复制，不改动原内容。
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
GMEM_MOVEABLE = 0x0002

VK_CONTROL = 0x11
VK_C = 0x43
VK_INSERT = 0x2D
KEYEVENTF_KEYUP = 0x0002

# 无法通过 HGLOBAL 字节拷贝复制的格式
NON_HGLOBAL_FORMATS = {CF_BITMAP}

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

    def _backup(self):
        saved, skipped = [], []
        if not _open_clipboard():
            return saved, skipped + [(0, "-", "无法打开剪贴板")]
        try:
            fid = 0
            while True:
                fid = user32.EnumClipboardFormats(fid)
                if fid == 0:
                    break
                if fid in NON_HGLOBAL_FORMATS:
                    skipped.append((fid, format_name(fid), "非 HGLOBAL 格式"))
                    continue
                hsrc = user32.GetClipboardData(fid)
                if not hsrc:
                    skipped.append((fid, format_name(fid), "GetClipboardData 为空"))
                    continue
                size = kernel32.GlobalSize(hsrc)
                if size == 0:
                    skipped.append((fid, format_name(fid), "GlobalSize=0"))
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
        # 区分两种"快照为空"：
        #   a) 剪贴板本来就没有任何格式 -> 还原含义是清空
        #   b) 有格式但全部不可拷贝（如只有 CF_BITMAP）-> 无法还原，必须如实报告
        had_any = bool(self._snapshot) or bool(self._skipped)
        self._was_empty = not had_any
        self._backup_ok = not self._skipped
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
                                         for _, name, reason in self._skipped),
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
            send_copy_keys(use_insert=use_insert)
            deadline = time.time() + timeout_ms / 1000.0
            while time.time() < deadline:
                if clipboard_seq() != seq0:
                    # 目标程序可能分批写格式，等一次稳定
                    time.sleep(0.025)
                    text = read_text()
                    if text:
                        return text, {
                            "changed": True,
                            "waited_ms": int((time.time() - t0) * 1000),
                            "seq_delta": clipboard_seq() - seq0,
                            "attempts": attempts,
                        }
                time.sleep(0.012)
            # 第一轮（Ctrl+C）没反应，第二轮换 Ctrl+Insert
            seq0 = clipboard_seq()

        changed = clipboard_seq() != (self._seq_before or 0)
        return text, {
            "changed": changed,
            "waited_ms": int((time.time() - t0) * 1000),
            "seq_delta": clipboard_seq() - (self._seq_before or 0),
            "attempts": attempts,
        }

    # -------- 还原 --------

    def restore(self):
        if self._restored:
            return self.restore_result or (True, "已还原")
        try:
            if not self._copy_attempted:
                self.restore_result = (True, "未触发复制，原剪贴板未改动")
                return self.restore_result

            seq_now = clipboard_seq()
            if self._seq_before is not None and seq_now == self._seq_before:
                self.restore_result = (True, "剪贴板未变化，跳过还原")
                return self.restore_result

            if not _open_clipboard():
                self.restore_result = (False, "还原时无法打开剪贴板")
                return self.restore_result
            try:
                if not user32.EmptyClipboard():
                    self.restore_result = (False, "还原时无法清空剪贴板")
                    return self.restore_result
                if self._was_empty:
                    self.restore_result = (True, "原剪贴板为空，已清空中间态")
                    return self.restore_result

                written = 0
                for i, (fid, hcopy, size) in enumerate(self._snapshot):
                    if user32.SetClipboardData(fid, hcopy):
                        self._snapshot[i] = (fid, 0, size)  # 所有权已移交系统
                        written += 1
                total = len(self._snapshot)
                self.restore_result = (written == total,
                                       f"写回 {written}/{total} 个格式")
                return self.restore_result
            finally:
                user32.CloseClipboard()
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

    # 1. 内容还原
    write_text(MARK1)
    time.sleep(0.08)
    before = read_text()
    n_before = len(fmt_count())
    print(f"[1] 预置: {before!r}  格式数={n_before}")

    with ClipGuard() as g:
        write_text(MARK2)
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
        write_text(MARK2)
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
            write_text(f"temp_{i}")
    final = read_text()
    if final == MARK1:
        print("[4] PASS 连续 20 次事务后内容仍一致")
    else:
        print(f"[4] FAIL 连续事务后内容漂移: {final!r}")
        fails += 1

    print("-" * 68)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(_selftest())
    print("用法: python py_clipguard.py --test")
    sys.exit(0)
