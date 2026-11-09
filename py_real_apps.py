"""在真实 Chromium 系应用中验证剪贴板取词 + 还原（端到端）。

覆盖目标（按当前本机实际存在）：
  · 飞书（Feishu, Chrome_WidgetWin_1）—— 聊天记录划选
  · ChatGPT 桌面版（Chrome_WidgetWin_1）
  · Edge（Chrome_WidgetWin_1）—— 网页正文
  · 新版记事本（RichEditD2DPT）—— 验证第 1 级是否真能命中

测试方法：
  对每个目标窗口，用 SetForegroundWindow 激活后：
    1. 尝试第 1 级 Win32 消息级（GetGUIThreadInfo -> EM_GETSEL）
    2. 尝试第 3 级剪贴板（Ctrl+C + 还原）
  并检查剪贴板是否回到基线。

限制说明：本脚本无法在目标应用内自动建立选区（那是 UI 交互），
所以对 Chromium 目标，剪贴板路径的成功与否取决于该窗口当前是否
真的有选中内容。因此额外做一项「Ctrl+A 全选后取词」的受控测试：
  · 先 Ctrl+A 建立确定选区
  · 再取词
  · 再还原剪贴板
这条可以给出确定性结论。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg
import py_pop as pop

user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]

VK_CONTROL = 0x11
VK_A = 0x41
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002

MARKER = "RECON_BASELINE_\u57fa\u7ebf_2026"


def pid2name():
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk",
            errors="replace", timeout=20,
        ).stdout
    except Exception:
        return {}
    m = {}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            try:
                m[int(parts[1])] = parts[0]
            except ValueError:
                pass
    return m


def find_targets():
    """返回 [(pid, hwnd, class, title, procname)]，只保留有标题的可见顶层窗口。"""
    p2n = pid2name()
    rows = []

    E = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        n = user32.GetWindowTextLengthW(h)
        if n == 0:
            return True
        tb = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, tb, n + 1)
        cb2 = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cb2, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        rows.append((pid.value, h, cb2.value, tb.value, p2n.get(pid.value, "")))
        return True

    user32.EnumWindows(E(cb), 0)
    return rows


def send_key(vk, ctrl=False, shift=False):
    if ctrl:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.015)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    if ctrl:
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def activate(h):
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)  # SW_RESTORE
        time.sleep(0.4)
    user32.SetForegroundWindow(h)
    time.sleep(0.8)
    return user32.GetForegroundWindow() == h


def probe(h, label):
    """对一个已激活窗口做两级探测，返回结果 dict。"""
    res = {"label": label}

    # 剪贴板基线
    cg.write_text(MARKER)
    time.sleep(0.1)
    base = cg.read_text()
    res["baseline"] = base

    # --- 第 1 级 ---
    fg, focus = pop.get_focus()
    res["focus_class"] = pop.class_of(focus)
    t1, d1 = pop.try_winmsg(focus)
    res["lvl1"] = t1
    res["lvl1_reason"] = d1.get("reason")

    # --- 建立确定选区：Ctrl+A ---
    send_key(VK_A, ctrl=True)
    time.sleep(0.55)

    # --- 第 3 级 ---
    t3, d3 = pop.try_clipboard()
    res["lvl3"] = t3
    res["lvl3_meta"] = d3

    # --- 剪贴板还原检查 ---
    time.sleep(0.15)
    after = cg.read_text()
    res["after"] = after
    res["clip_clean"] = (after == base)

    # 撤销全选，避免影响用户
    send_key(VK_ESCAPE)
    time.sleep(0.2)

    return res


def main():
    print("=" * 78)
    print("真实应用取词验证（Chromium 系 + 新版记事本）")
    print("=" * 78)
    print("注意：会短暂激活目标窗口并发送 Ctrl+A / Ctrl+C / Esc")
    print("      剪贴板会被备份还原，不会留下残留")
    print()

    targets = find_targets()
    wanted = {
        "Feishu.exe": "飞书",
        "ChatGPT.exe": "ChatGPT 桌面版",
        "msedge.exe": "Edge 浏览器",
        "Notepad.exe": "新版记事本",
        "WorkBuddy.exe": "WorkBuddy",
        "Doubao.exe": "豆包",
        "Weixin.exe": "微信",
    }

    picks = []
    seen = set()
    for pid, h, cls, ttl, nm in targets:
        if nm in wanted and nm not in seen:
            if cls in ("Chrome_WidgetWin_1", "Notepad", "WeChatMainWndForPC",
                       "TxGuiFoundation"):
                picks.append((nm, h, cls, ttl))
                seen.add(nm)

    if not picks:
        print("未找到可测目标窗口。")
        print("当前候选：")
        for pid, h, cls, ttl, nm in targets[:20]:
            print(f"  {nm} class={cls!r} title={ttl[:50]!r}")
        return 1

    print("检测到可测目标：")
    for nm, h, cls, ttl in picks:
        print(f"  {wanted.get(nm, nm):<16} class={cls:<22} title={ttl[:44]!r}")
    print()

    results = []
    for nm, h, cls, ttl in picks:
        label = wanted.get(nm, nm)
        print("-" * 78)
        print(f"[{label}] hwnd=0x{h:X}")
        if not activate(h):
            print("  激活失败，跳过")
            continue
        r = probe(h, label)
        r["proc"] = nm
        r["class"] = cls
        results.append(r)

        print(f"  焦点控件类名 : {r['focus_class']!r}")
        print(f"  第1级 Win32  : {'命中 ' + repr(r['lvl1'][:40]) if r['lvl1'] else '未命中 (' + str(r['lvl1_reason']) + ')'}")
        m = r["lvl3_meta"]
        print(f"  第3级 剪贴板 : {'命中 ' + str(len(r['lvl3'])) + ' 字符' if r['lvl3'] else '未命中 (' + str(m.get('reason')) + ')'}")
        print(f"      meta     : changed={m.get('changed')} 等待={m.get('waited_ms')}ms 尝试={m.get('attempts')}")
        print(f"      还原     : {m.get('restore')}")
        if r["lvl3"]:
            preview = r["lvl3"][:60].replace("\n", "\\n")
            print(f"      内容预览 : {preview!r}")
        print(f"  剪贴板洁净   : {'PASS' if r['clip_clean'] else 'FAIL 污染为 ' + repr(r['after'])[:60]}")

    # 汇总
    print("\n" + "=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"{'目标':<16}{'类名':<20}{'第1级':<10}{'第3级':<12}{'剪贴板':<8}")
    for r in results:
        l1 = "命中" if r["lvl1"] else "—"
        l3 = "命中" if r["lvl3"] else "—"
        cc = "洁净" if r["clip_clean"] else "污染"
        print(f"{r['label']:<16}{r['class'][:18]:<20}{l1:<10}{l3:<12}{cc:<8}")

    clean = all(r["clip_clean"] for r in results)
    print(f"\n剪贴板整体: {'全部洁净 PASS' if clean else '存在污染 FAIL'}")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
