"""ClipGuard 的失败路径测试；Win32 接口全部模拟，不触碰系统剪贴板。"""

import types
import unittest
from unittest.mock import patch

import py_clipguard as cg


class ClipGuardSafetyTests(unittest.TestCase):
    def test_incomplete_snapshot_never_sends_copy_and_frees_backup(self):
        """唯一不可拷贝的格式真的会丢内容时，必须放弃取词并释放已备份的句柄。

        注意跳过理由要用**真丢数据**的那类（GlobalLock 失败）。早期版本
        这里写的是"非 HGLOBAL 格式"，那是安全的派生格式跳过（CF_BITMAP
        由 CF_DIB 重建），现在按设计不该阻止取词 —— 用它会测错语义。
        """
        copied = []
        freed = []
        with patch.object(cg.ClipGuard, "_backup", return_value=(
                [(cg.CF_UNICODETEXT, 101, 8)],
                [(cg.CF_BITMAP, "CF_BITMAP", "GlobalLock 失败")]
        )), patch.object(cg, "clipboard_seq", return_value=7), \
                patch.object(cg, "send_copy_keys", side_effect=lambda **kw: copied.append(kw)), \
                patch.object(cg, "kernel32", types.SimpleNamespace(GlobalFree=freed.append)):
            with cg.ClipGuard() as guard:
                text, meta = guard.read_text_via_copy()
            self.assertIsNone(text)
            self.assertFalse(meta["backup_ok"])
            self.assertEqual(copied, [])
            self.assertEqual(freed, [101])

    def test_unopened_snapshot_never_sends_copy(self):
        copied = []
        with patch.object(cg.ClipGuard, "_backup", return_value=(
                [], [(0, "-", "无法打开剪贴板")]
        )), patch.object(cg, "clipboard_seq", return_value=7), \
                patch.object(cg, "send_copy_keys", side_effect=lambda **kw: copied.append(kw)):
            with cg.ClipGuard() as guard:
                text, meta = guard.read_text_via_copy()
            self.assertIsNone(text)
            self.assertFalse(meta["backup_ok"])
            self.assertEqual(copied, [])

    def test_format_enumeration_failure_never_sends_copy(self):
        copied = []
        fake_user32 = types.SimpleNamespace(
            OpenClipboard=lambda hwnd: True,
            EnumClipboardFormats=lambda fid: 0,
            CloseClipboard=lambda: True,
        )
        with patch.object(cg, "user32", fake_user32), \
                patch.object(cg, "clipboard_seq", return_value=7), \
                patch.object(cg.ctypes, "get_last_error", return_value=5), \
                patch.object(cg, "send_copy_keys", side_effect=lambda **kw: copied.append(kw)):
            with cg.ClipGuard() as guard:
                text, meta = guard.read_text_via_copy()
            self.assertIsNone(text)
            self.assertFalse(meta["backup_ok"])
            self.assertEqual(copied, [])

    def test_unchanged_clipboard_releases_snapshot_handle(self):
        freed = []
        with patch.object(cg.ClipGuard, "_backup", return_value=(
                [(cg.CF_UNICODETEXT, 102, 8)], []
        )), patch.object(cg, "clipboard_seq", return_value=7), \
                patch.object(cg, "kernel32", types.SimpleNamespace(GlobalFree=freed.append)):
            with cg.ClipGuard() as guard:
                pass
            self.assertEqual(freed, [102])

    def test_failed_restore_releases_snapshot_handle(self):
        """还原时打不开剪贴板 -> 报告失败并释放快照句柄，绝不泄漏内存。

        必须先把"我们的中间态"立起来（_intermediate_seq / _intermediate_text），
        否则 restore() 会在第三方占用闸门处提前返回「保留其内容」——
        那是另一条正确的分支，测不到"打开失败 + 释放句柄"这条。

        用 return_value 而不是严格递增的迭代器：clipboard_seq() 的调用
        次数会随判据演进而变，用 next(sequence) 会在次数变化时报
        StopIteration —— 那测的是"调用次数"而非"行为"，属于伪约束。
        """
        freed = []
        seqs = iter([7])
        with patch.object(cg.ClipGuard, "_backup", return_value=(
                [(cg.CF_UNICODETEXT, 103, 8)], []
        )), patch.object(cg, "clipboard_seq",
                         side_effect=lambda: next(seqs, 8)), \
                patch.object(cg, "_clipboard_state",
                             return_value=({cg.CF_UNICODETEXT}, "中间态", None)), \
                patch.object(cg, "_open_clipboard", return_value=False), \
                patch.object(cg, "kernel32", types.SimpleNamespace(GlobalFree=freed.append)):
            with cg.ClipGuard() as guard:
                guard._copy_attempted = True
                guard._intermediate_seq = 8
                guard._intermediate_text = "中间态"
                pass
            self.assertFalse(guard.restore_result[0])
            self.assertEqual(freed, [103])


if __name__ == "__main__":
    unittest.main()
