"""浮层边缘和阴影的像素级回归测试。"""

import ctypes
import unittest
import tkinter as tk
from ctypes import wintypes as wt
from unittest.mock import patch

import py_pill_effect
import py_pop
import py_theme


class PillEffectTests(unittest.TestCase):
    def test_rounded_edge_is_antialiased_and_shadow_is_subtle(self):
        width, height, radius = 300, 56, 14
        pixels, canvas_width, canvas_height, pad = py_pill_effect.backdrop_pixels(
            width, height, radius, "#FFFFFF")

        def alpha(x, y):
            return pixels[(y * canvas_width + x) * 4 + 3]

        self.assertEqual(len(pixels), canvas_width * canvas_height * 4)
        self.assertEqual(alpha(pad + width // 2, pad + height // 2), 255)
        self.assertTrue(any(32 < alpha(x, y) < 255
                            for y in range(pad, pad + radius)
                            for x in range(pad, pad + radius)))
        self.assertLessEqual(alpha(pad + width // 2, pad - 1), 32)
        self.assertGreater(alpha(pad + width // 2, pad - 1), 0)
        self.assertEqual(alpha(0, 0), 0)

    def test_live_pill_backdrop_stays_behind_and_hides(self):
        root = tk.Tk()
        root.withdraw()
        previous = py_theme.preference()
        py_theme.set_mode("dark")
        pill = py_pop.Pill(root, lambda action: None)
        try:
            self.assertIsNotNone(pill._backdrop)
            self.assertEqual(str(pill.win.attributes("-transparentcolor")),
                             pill.TRANSPARENT_KEY)
            pill.show_at(300, 300, "示例")
            self.assertTrue(py_pill_effect.user32.IsWindowVisible(
                pill._backdrop.hwnd))
            user32 = ctypes.WinDLL("user32.dll")
            user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetWindow.restype = ctypes.c_void_p
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            style = user32.GetWindowLongW(pill._backdrop.hwnd, -20)
            self.assertTrue(style & py_pill_effect.WS_EX_TRANSPARENT)
            self.assertTrue(style & py_pill_effect.WS_EX_NOACTIVATE)
            user32.GetWindowRect.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(wt.RECT)]
            before = wt.RECT()
            user32.GetWindowRect(pill._backdrop.hwnd, ctypes.byref(before))
            self.assertEqual(user32.GetWindow(pill.hwnd, 2),
                             pill._backdrop.hwnd)
            old = py_theme.colors()
            py_theme.set_mode("light")
            pill.apply_theme(old, py_theme.colors())
            self.assertEqual(pill._backdrop._key[3], py_theme.BG2)
            after = wt.RECT()
            user32.GetWindowRect(pill._backdrop.hwnd, ctypes.byref(after))
            self.assertEqual((before.left, before.top),
                             (after.left, after.top))
            pill.hide()
            self.assertFalse(py_pill_effect.user32.IsWindowVisible(
                pill._backdrop.hwnd))
        finally:
            pill.close()
            root.destroy()
            py_theme.set_mode(previous)

    def test_native_backdrop_failure_keeps_pill_usable(self):
        root = tk.Tk()
        root.withdraw()
        with patch.object(py_pop.py_pill_effect, "PillBackdrop",
                          side_effect=OSError("unavailable")):
            pill = py_pop.Pill(root, lambda action: None)
        try:
            self.assertIsNone(pill._backdrop)
            pill.show_at(300, 300, "示例")
            self.assertTrue(pill._rounded)
            self.assertEqual(pill.win.cget("bg"), py_theme.BG2)
        finally:
            pill.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
