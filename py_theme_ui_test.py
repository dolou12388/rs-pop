"""用真实 Tk 控件验证主题切换及配置持久化。"""

import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import py_config
import py_markdown
import py_pop
import py_settings
import py_theme


class ThemeUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(py_config, "config_dir",
                                       return_value=self.tmp.name)
        self.path_patch.start()
        py_theme.set_mode("dark")
        py_settings._sync_theme_colors()
        py_pop._sync_result_colors()
        self.store = py_config.ConfigStore()
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()
        py_theme.set_mode("dark")
        py_settings._sync_theme_colors()
        py_pop._sync_result_colors()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_settings_switch_saves_and_recolors_without_losing_input(self):
        win = py_settings.SettingsWindow(self.store)
        win.root = self.root
        py_settings.style_tk(self.root)
        win._build()
        win._load_providers()
        win.f_model.delete(0, "end")
        win.f_model.insert(0, "draft-model")
        win.theme_var.set("light")
        win._mark_dirty()
        self.assertEqual(str(win.btn_save.cget("state")), "normal")
        win._save()
        self.assertEqual(self.store.raw["ui"]["theme_mode"], "light")
        self.assertEqual(py_config.ConfigStore().raw["ui"]["theme_mode"],
                         "light")
        self.assertEqual(self.root.cget("bg"), py_theme.BG)
        self.assertEqual(win.prompt_text.cget("bg"), py_theme.BG3)
        self.assertEqual(win.f_model.get(), "draft-model")

    def test_open_result_recolors_markdown_and_keeps_text(self):
        py_markdown.init_fonts(self.root)
        result = py_pop.ResultWindow(self.root, "解释", "示例")
        try:
            result.win.update()
            self.assertTrue(result.ask_entry.winfo_ismapped())
            self.assertTrue(result.btn_ask.winfo_ismapped())
            self.assertGreater(result.btn_ask.winfo_width(), 0)
            result.win.withdraw()
            py_markdown.render(result.body, "# 标题\n\n`code`")
            before = result.body.get("1.0", "end-1c")
            old = py_theme.colors()
            py_theme.set_mode("light")
            py_pop._sync_result_colors()
            result.apply_theme(old, py_theme.colors())
            self.assertEqual(result.body.cget("bg"), py_theme.BG2)
            self.assertEqual(result.ask_entry.cget("bg"), py_theme.BG3)
            self.assertEqual(result.body.get("1.0", "end-1c"), before)
            self.assertEqual(result.body.tag_cget("md_code", "background"),
                             py_markdown.LIGHT["code_bg"])
            light = py_theme.colors()
            py_theme.set_mode("dark")
            py_pop._sync_result_colors()
            result.apply_theme(light, py_theme.colors())
            self.assertEqual(result.btn_ask.cget("bg"), py_theme.ACCENT)
            self.assertEqual(result.body.cget("bg"), py_theme.BG2)
            self.assertEqual(result.body.get("1.0", "end-1c"), before)
        finally:
            result.close()


if __name__ == "__main__":
    unittest.main()
