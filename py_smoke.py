# -*- coding: utf-8 -*-
"""冒烟验证：撤销采样器后，各模块仍可导入、内存读取仍可用。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv = ["py_pop.py"]

import py_dpi
import py_theme
import py_res
import py_log
import py_markdown
import py_pop
import py_settings

print("全部模块 import OK")

m = py_res.snapshot()
print("snapshot 字段:", sorted(m.keys()))
print(f"  RSS  = {py_res.fmt_bytes(m.get('rss'))}")
print(f"  私有 = {py_res.fmt_bytes(m.get('private'))}")
print(f"  句柄 = {m.get('handles')}")
print(f"  线程 = {m.get('threads')}")

# 采样器类已移除，确认它真的不在了
assert not hasattr(py_res, "Sampler"), "Sampler 应已移除"
print("Sampler 已移除 OK")

# 格式化函数仍在
assert py_res.fmt_bytes(1024) == "1.0 KB", py_res.fmt_bytes(1024)
assert py_res.fmt_bytes(None) == "—"
print("fmt_bytes OK")

# 主题配色可用
assert py_theme.BG.startswith("#")
print("py_theme OK")

print("\n全部通过")
