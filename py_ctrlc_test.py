# -*- coding: utf-8 -*-
"""回归测试：结果窗 Ctrl+C 必须只复制，不能导致程序退出。

## 背景（这是真实踩过的坑）

`py_pop.py` 里曾有一行 `root.bind_all("<Control-c>", lambda e: root.quit())`。
Tk 的 bindtags 顺序是 widget -> class -> toplevel -> all，`bind_all` 挂在
最后的 all 级。结果窗自己 bind 了 `<Control-c>` 做复制，但没有 return
"break"，于是复制完成后事件继续传播，把整个程序 quit 掉了。

症状极具迷惑性：日志里没有任何 Traceback，退出前统计与内存都正常打印，
用户看到的就是"复制一下，程序自己没了"。

## 本测试守什么

  1. 源码中不得再出现 `bind_all` 的 Ctrl+C 退出绑定
  2. 结果窗的 Ctrl+C 处理器必须 return "break"
  3. 行为验证：Ctrl+C 只触发复制，不触发 quit
"""
import os
import re
import sys
import tkinter as tk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

PASS = 0
FAIL = 0
FAILS = []


def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL  {name}   {detail}")


def read(fn):
    with open(os.path.join(BASE, fn), "r", encoding="utf-8") as f:
        return f.read()


# ---------- 1. 静态检查：源码里不能再有 bind_all 绑 Ctrl+C 到 quit ----------
print("=" * 68)
print("1. 静态检查：危险的全局 Ctrl+C 退出绑定")
print("=" * 68)

src = read("py_pop.py")

# 去掉注释行后再搜，避免注释里的说明文字被误判
code_lines = []
for ln in src.splitlines():
    stripped = ln.lstrip()
    if stripped.startswith("#"):
        continue
    code_lines.append(ln)
code = "\n".join(code_lines)

dangerous = re.findall(r"bind_all\([^)]*Control[^)]*quit", code, re.I)
chk("py_pop.py 中不存在 bind_all + Ctrl + quit 组合",
    not dangerous, f"命中: {dangerous}")

chk("py_pop.py 中完全没有 bind_all 调用",
    "bind_all(" not in code,
    "bind_all 是全局绑定，极易造成跨窗口误触发，本项目不应使用")

# ---------- 2. 静态检查：结果窗的 Ctrl+C 必须 return break ----------
print()
print("=" * 68)
print("2. 静态检查：结果窗 Ctrl+C 处理器必须阻断冒泡")
print("=" * 68)

m = re.search(r"def _on_ctrl_c\(self[^)]*\):(.*?)(?=\n    def )", src, re.S)
chk("存在 _on_ctrl_c 处理器", m is not None)
if m:
    body = m.group(1)
    chk('_on_ctrl_c 内有 return "break"',
        'return "break"' in body,
        f"函数体: {body.strip()[:80]}")

bind_line = re.search(r'self\.win\.bind\("<Control-c>",\s*([^)]+)\)', src)
chk("结果窗 <Control-c> 绑定到 _on_ctrl_c",
    bind_line is not None and "_on_ctrl_c" in bind_line.group(1),
    f"实际绑定: {bind_line.group(1) if bind_line else '未找到'}")

# ---------- 3. 行为验证：真实 Tk 事件传播 ----------
print()
print("=" * 68)
print("3. 行为验证：按 Ctrl+C 只复制，不退出")
print("=" * 68)

root = tk.Tk()
root.geometry("220x80")
root.update()

calls = {"copy": 0, "quit": 0}

entry = tk.Entry(root, width=12)
entry.pack()
entry.focus_force()
root.update()


def on_copy(event=None):
    """与 py_pop 里的实现在语义上一致：复制 + 阻断冒泡。"""
    calls["copy"] += 1
    return "break"


entry.bind("<Control-Key-c>", on_copy)

# 复刻当时那个致命的全局绑定，用于确认"阻断后它不会被触发"
root.bind_all("<Control-Key-c>",
              lambda e: calls.__setitem__("quit", calls["quit"] + 1))

root.update()
entry.event_generate("<Control-Key-c>", keysym="c", state=0x4)
root.update()

chk("Ctrl+C 触发了复制", calls["copy"] == 1, f"copy={calls['copy']}")
chk("Ctrl+C 没有触发退出", calls["quit"] == 0, f"quit={calls['quit']}")

try:
    root.destroy()
except tk.TclError:
    pass

# ---------- 4. 对照：不阻断时确实会漏到 all 级 ----------
print()
print("=" * 68)
print("4. 对照实验：不 return break 时 quit 会被触发（证明测试有效）")
print("=" * 68)

root2 = tk.Tk()
root2.geometry("220x80")
root2.update()

calls2 = {"copy": 0, "quit": 0}
entry2 = tk.Entry(root2, width=12)
entry2.pack()
entry2.focus_force()
root2.update()

entry2.bind("<Control-Key-c>",
            lambda e: calls2.__setitem__("copy", calls2["copy"] + 1))  # 不阻断
root2.bind_all("<Control-Key-c>",
               lambda e: calls2.__setitem__("quit", calls2["quit"] + 1))

root2.update()
entry2.event_generate("<Control-Key-c>", keysym="c", state=0x4)
root2.update()

chk("不阻断时 copy=1", calls2["copy"] == 1, f"copy={calls2['copy']}")
chk("不阻断时 quit 被触发（说明本测试确实能抓到该 bug）",
    calls2["quit"] == 1, f"quit={calls2['quit']}")

try:
    root2.destroy()
except tk.TclError:
    pass

# ---------- 汇总 ----------
print()
print("=" * 68)
print(f"结果: {PASS} PASS / {FAIL} FAIL")
if FAILS:
    for n in FAILS:
        print(f"  FAILED: {n}")
print("=" * 68)

raise SystemExit(0 if FAIL == 0 else 1)
