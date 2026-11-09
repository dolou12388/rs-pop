"""ClipGuard 的失败路径测试；Win32 接口全部模拟，不触碰系统剪贴板。"""

import types
import unittest
from unittest.mock import patch

import py_clipguard as cg


class ClipGuardSafetyTests(unittest.TestCase):
    def test_incomplete_snapshot_never_sends_copy_and_frees_backup(self):
        copied = []
        freed = []
        with patch.object(cg.ClipGuard, "_backup", return_value=(
                [(cg.CF_UNICODETEXT, 101, 8)],
                [(cg.CF_BITMAP, "CF_BITMAP", "非 HGLOBAL 格式")]
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
        freed = []
        sequence = iter((7, 8))
        with patch.object(cg.ClipGuard, "_backup", return_value=(
                [(cg.CF_UNICODETEXT, 103, 8)], []
        )), patch.object(cg, "clipboard_seq", side_effect=lambda: next(sequence)), \
                patch.object(cg, "_open_clipboard", return_value=False), \
                patch.object(cg, "kernel32", types.SimpleNamespace(GlobalFree=freed.append)):
            with cg.ClipGuard() as guard:
                guard._copy_attempted = True
                pass
            self.assertFalse(guard.restore_result[0])
            self.assertEqual(freed, [103])


if __name__ == "__main__":
    unittest.main()
