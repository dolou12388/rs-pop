"""深浅色与系统跟随的纯逻辑回归测试。"""

import unittest
from unittest.mock import patch

import py_theme


class ThemeModeTests(unittest.TestCase):
    def tearDown(self):
        py_theme.set_mode("dark")

    def test_explicit_modes_apply_distinct_palettes(self):
        py_theme.set_mode("dark")
        dark = py_theme.colors()
        self.assertEqual(py_theme.effective_mode(), "dark")
        py_theme.set_mode("light")
        light = py_theme.colors()
        self.assertEqual(py_theme.effective_mode(), "light")
        self.assertNotEqual(light["BG"], dark["BG"])
        self.assertNotEqual(light["ACCENT"], dark["ACCENT"])

    def test_system_mode_tracks_os_changes(self):
        with patch.object(py_theme, "system_mode", return_value="light") as system:
            py_theme.set_mode("system")
            self.assertEqual(py_theme.effective_mode(), "light")
            system.return_value = "dark"
            self.assertTrue(py_theme.refresh_system())
            self.assertEqual(py_theme.effective_mode(), "dark")
            self.assertFalse(py_theme.refresh_system())

    def test_explicit_mode_ignores_os_changes(self):
        py_theme.set_mode("light")
        with patch.object(py_theme, "system_mode", return_value="dark"):
            self.assertFalse(py_theme.refresh_system())
        self.assertEqual(py_theme.effective_mode(), "light")


if __name__ == "__main__":
    unittest.main()
