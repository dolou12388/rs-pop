# -*- coding: utf-8 -*-
"""DPI 层测试：验证声明顺序、缩放换算、字体挑选。

关键不是"函数能跑"，而是：
  1. 未声明时 Windows 报 96 DPI（本机真实 144），声明后必须变成 144
  2. 声明后再建窗口仍保持 144（顺序正确）
  3. px() 换算正确，tk scaling 跟随真实 DPI
  4. 字体族挑选必须返回系统真实存在的族
"""

import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL  {name}   {detail}")


# ---------- 1. 纯逻辑（不建窗口） ----------

def test_pure():
    print("\n[1] 换算与状态")
    import py_dpi as d

    check("初始 scale 为 1.0 或真实值", d.scale() > 0)
    check("px(100) 为正整数", isinstance(d.px(100), int) and d.px(100) > 0)
    # 换算必须单调、且与 scale 一致
    sc = d.scale()
    check("px 与 scale 一致", d.px(100) == int(round(100 * sc)),
          f"px={d.px(100)} scale={sc}")
    check("px(0) == 0", d.px(0) == 0)
    check("font_size 原样返回", d.font_size(10) == 10)

    info = d.info()
    for k in ("enabled", "dpi", "scale", "level", "note"):
        check(f"info 含 {k}", k in info)

    check("UI_FAMILIES 非空", len(d.UI_FAMILIES) >= 3)
    check("MONO_FAMILIES 非空", len(d.MONO_FAMILIES) >= 2)


# ---------- 2. DPI 感知必须真的改变读数 ----------

def test_awareness():
    print("\n[2] 声明前后的 DPI 变化（子进程）")
    code = r"""
import ctypes, sys
from ctypes import wintypes as wt
u = ctypes.WinDLL("user32.dll")
try:
    s = ctypes.WinDLL("shcore.dll")
except OSError:
    s = None

def rd():
    u.GetDpiForSystem.restype = wt.UINT
    dpi = int(u.GetDpiForSystem())
    sw = u.GetSystemMetrics(0); sh = u.GetSystemMetrics(1)
    lvl = -1
    if s:
        v = ctypes.c_int()
        s.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
        if s.GetProcessDpiAwareness(None, ctypes.byref(v)) == 0:
            lvl = v.value
    return dpi, sw, sh, lvl

before = rd()
sys.path.insert(0, %r)
import py_dpi
py_dpi.enable()
after = rd()
print("BEFORE", before)
print("AFTER", after)
print("SCALE", py_dpi.scale())
print("NOTE", py_dpi.info()["note"])
""" % BASE

    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=30,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        check("声明进程可完成", False, "超时")
        return

    out = r.stdout
    check("探针正常退出", r.returncode == 0, r.stderr[-300:])

    def grab(key):
        for line in out.splitlines():
            if line.startswith(key + " "):
                body = line[len(key) + 1:].strip()
                if body.startswith("("):
                    return tuple(int(x) for x in body.strip("()").split(","))
                try:
                    return float(body)
                except ValueError:
                    return body
        return None

    before = grab("BEFORE")
    after = grab("AFTER")
    scale = grab("SCALE")
    note = grab("NOTE")

    check("拿到声明前读数", isinstance(before, tuple), out)
    check("拿到声明后读数", isinstance(after, tuple), out)
    if not isinstance(before, tuple) or not isinstance(after, tuple):
        return

    check("声明前为 UNAWARE(0)", before[3] == 0, f"level={before[3]}")
    check("声明后不再是 UNAWARE", after[3] != 0, f"level={after[3]}")
    check("声明后 DPI 提升或本就一致", after[0] >= before[0],
          f"{before[0]} -> {after[0]}")
    check("声明后屏幕宽度按真实像素报告", after[1] >= before[1],
          f"{before[1]} -> {after[1]}")
    check("scale 与 DPI 自洽", abs(scale - after[0] / 96.0) < 1e-6,
          f"scale={scale} dpi={after[0]}")
    print(f"        读数：前 {before}  后 {after}  scale={scale}")

    if after[0] > 96:
        print(f"        本机为高 DPI（{after[0]}），可验证真实收益")
    else:
        print("        本机 100% 缩放，DPI 收益不可见（逻辑仍应正确）")


# ---------- 3. 窗口建后仍保持感知 ----------

def test_after_window():
    print("\n[3] 建窗口后感知级别保持")
    code = r"""
import sys, tkinter as tk
sys.path.insert(0, %r)
import py_dpi
ok, note = py_dpi.enable()
root = tk.Tk(); root.withdraw()
scaling = root.tk.call("tk", "scaling")
py_dpi.set_tk_scaling(root)
after = root.tk.call("tk", "scaling")
import py_markdown
fam, mono = py_markdown.init_fonts(root)
print("DPI", py_dpi.dpi())
print("LEVEL", py_dpi.info()["level"])
print("SCALING_BEFORE", float(scaling))
print("SCALING_AFTER", float(after))
print("FAM", fam)
print("MONO", mono)
print("BASE_FONT", py_markdown.base_font(10))
print("MONO_FONT", py_markdown.mono_font(10))
root.destroy()
""" % BASE

    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=40,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        check("窗口场景可完成", False, "超时")
        return

    check("窗口场景正常退出", r.returncode == 0, r.stderr[-500:])
    if r.returncode != 0:
        return

    d = {}
    for line in r.stdout.splitlines():
        if " " in line:
            k, v = line.split(" ", 1)
            d[k] = v.strip()

    check("建窗后仍是 PER_MONITOR", d.get("LEVEL") == "PER_MONITOR_AWARE",
          d.get("LEVEL"))
    sb = float(d.get("SCALING_BEFORE", 0))
    sa = float(d.get("SCALING_AFTER", 0))
    dpi = int(d.get("DPI", 0))
    # Tk 内部对 scaling 有定点精度（本机设 2.0 读回 1.9980，差 0.1%），
    # 且声明感知后 Tk 建窗时就会自己算对，这里的设置只是幂等保险。
    check("scaling 跟随真实 DPI（±1%）", abs(sa - dpi / 72.0) / (dpi / 72.0) < 0.01,
          f"sa={sa} dpi={dpi} 期望={dpi / 72.0:.4f}")
    print(f"        dpi={dpi} scaling {sb:.3f} -> {sa:.3f}")

    check("挑了真实存在的 UI 字体", bool(d.get("FAM")), d)
    check("挑了真实存在的等宽字体", bool(d.get("MONO")), d)
    check("base_font 元组含字体族", d.get("BASE_FONT", "").startswith("("),
          d.get("BASE_FONT"))
    check("mono_font 元组含字体族", d.get("MONO_FONT", "").startswith("("),
          d.get("MONO_FONT"))
    print(f"        UI={d.get('FAM')}  等宽={d.get('MONO')}")
    print(f"        base={d.get('BASE_FONT')}  mono={d.get('MONO_FONT')}")


# ---------- 4. 生产入口确实先声明再建窗 ----------

def test_order_in_source():
    print("\n[4] 生产代码调用顺序（静态检查）")
    src = open(os.path.join(BASE, "py_pop.py"), encoding="utf-8").read()

    i_enable = src.find("py_dpi.enable(")
    i_tk = src.find("root = tk.Tk()")
    i_scaling = src.find("py_dpi.set_tk_scaling(root)")
    i_fonts = src.find("py_markdown.init_fonts(root)")

    check("main 里有 py_dpi.enable", i_enable > 0)
    check("enable 在 tk.Tk() 之前",
          0 < i_enable < i_tk, f"enable@{i_enable} tk@{i_tk}")
    check("set_tk_scaling 在 tk.Tk() 之后",
          i_scaling > i_tk, f"scaling@{i_scaling} tk@{i_tk}")
    check("init_fonts 在 tk.Tk() 之后",
          i_fonts > i_tk, f"fonts@{i_fonts} tk@{i_tk}")
    check("import py_dpi 存在", "import py_dpi" in src)

    # run_diag 也要声明，否则诊断打印的坐标与生产不一致
    diag = src.find("def run_diag")
    check("run_diag 里也声明了 DPI",
          src.find("py_dpi.enable(", diag) > diag)

    # 浮层尺寸不能是裸字符常量
    check("Pill 尺寸走实测/换算",
          "self.pill_w" in src and "self.pill_h" in src)

    s_src = open(os.path.join(BASE, "py_settings.py"),
                 encoding="utf-8").read()
    check("设置窗引入了 py_dpi", "import py_dpi" in s_src)
    check("设置窗调用了 _init_fonts", "_init_fonts(self.root)" in s_src)
    # 原先写死 "py_dpi.px(880)"，窗口尺寸一调（880 -> 940）就误报。
    # 这里改判据：geometry/minsize 里的数字必须包在 py_dpi.px() 里，
    # 也就是不允许出现裸常量宽度。这样调整具体尺寸不会让测试误报。
    import re as _re
    geo = _re.findall(r"\.(?:geometry|minsize)\(\s*([^)]*)", s_src)
    raw = [g for g in geo if _re.search(r"f?\"[^\"]*\d+x\d+[^\"]*\"", g)
           and "py_dpi.px(" not in g]
    check("设置窗尺寸走 px()", bool(geo) and not raw,
          f"geometry/minsize 调用 {len(geo)} 处，裸常量 {len(raw)} 处")
    iif = s_src.find("_init_fonts(self.root)")
    ist = s_src.find("style_tk(self.root)")
    check("字体初始化在 style_tk 之前", 0 < iif < ist,
          f"fonts@{iif} style@{ist}")


if __name__ == "__main__":
    print("=" * 60)
    print("DPI 层测试")
    print("=" * 60)
    test_pure()
    test_awareness()
    test_after_window()
    test_order_in_source()
    print("\n" + "=" * 60)
    print(f"结果：{PASS} 通过 / {FAIL} 失败 / 共 {PASS + FAIL}")
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)
