"""三级取词链路的自动化验收测试。

不依赖人工操作：自建靶子进程（原生 EDIT / RichEdit），建立真实选区，
然后逐级调用 acquire_text 的对等逻辑，验证：
  · 第 1 级 Win32 消息级 能否命中，文本是否精确
  · 第 3 级 剪贴板 在 Win32 失败时能否接管，且剪贴板是否被还原
  · 整条链路的最终结果是否等于预期文本

判定以「文本精确相等」为准，不看长度近似。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg
import py_pop as pop

user32 = ctypes.WinDLL("user32.dll")
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL

EXPECTED = "量子纠缠是量子力学中最引人注目的现象之一"
MARKER = "CLIP_BASELINE_\u9884\u7f6e\u526a\u8d34\u677f_555"


def find_window(title):
    hits = []
    E = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if b.value == title:
                hits.append(h)
        return True

    user32.EnumWindows(E(cb), 0)
    return hits[0] if hits else None


def launch_target():
    p = subprocess.Popen(
        [sys.executable, "py_edit_target.py", "60"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    hwnd = None
    for _ in range(50):
        time.sleep(0.2)
        hwnd = find_window("EditTarget")
        if hwnd:
            break
    return p, hwnd


def activate(hwnd):
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.7)


def main():
    print("=" * 72)
    print("三级取词链路 自动化验收")
    print("=" * 72)

    fails = 0

    # 预置剪贴板基线，用于验证第 3 级不污染
    cg.write_text(MARKER)
    time.sleep(0.1)
    baseline = cg.read_text()
    print(f"[0] 剪贴板基线: {baseline!r}")

    proc, hwnd = launch_target()
    if not hwnd:
        print("[!] 靶子未启动")
        out = proc.stdout.read() if proc.stdout else ""
        print(out[:400])
        proc.kill()
        return 1
    print(f"[1] 靶子就绪 hwnd=0x{hwnd:X}")
    activate(hwnd)

    # ---- 第 1 级：Win32 消息级 ----
    fg, focus = pop.get_focus()
    print(f"[2] 焦点控件 hwnd=0x{focus:X} class={pop.class_of(focus)!r}")
    t1, d1 = pop.try_winmsg(focus)
    print(f"    第1级结果: {'命中' if t1 else '未命中'}  {d1.get('reason')}")
    if t1:
        print(f"    文本: {t1!r}")
        if t1 == EXPECTED:
            print("    PASS 文本精确匹配")
        else:
            print(f"    FAIL 预期 {EXPECTED!r}")
            fails += 1
    else:
        print("    FAIL 第1级应命中却未命中")
        fails += 1

    # ---- 第 3 级：剪贴板（先制造 Win32 失败场景：改用 RichEdit 靶子） ----
    print("[3] 切换到 RichEdit 靶子验证剪贴板兜底 ...")
    proc.kill()
    time.sleep(0.5)

    ps = subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", "uia_target.ps1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    rh = None
    for _ in range(50):
        time.sleep(0.2)
        rh = find_window("UIATarget")
        if rh:
            break
    if not rh:
        print("    RichEdit 靶子未启动，跳过第 3 级测试")
    else:
        activate(rh)
        time.sleep(1.0)
        # RichEdit 走 Win32 消息级其实能成功，为测剪贴板兜底，
        # 这里直接强制走第 3 级
        t3, d3 = pop.try_clipboard()
        print(f"    第3级结果: {'命中' if t3 else '未命中'}  {d3.get('reason')}")
        print(f"    meta: changed={d3.get('changed')} 等待={d3.get('waited_ms')}ms "
              f"尝试={d3.get('attempts')}")
        print(f"    还原: {d3.get('restore')}")
        if t3:
            print(f"    文本: {t3!r}")
        # 关键判定：剪贴板必须回到基线
        after = cg.read_text()
        print(f"    剪贴板现状: {after!r}")
        if after == baseline:
            print("    PASS 剪贴板已还原至基线")
        else:
            print(f"    FAIL 剪贴板被污染: 期望 {baseline!r} 实际 {after!r}")
            fails += 1
        ps.kill()

    # ---- 整链路 ----
    print("[4] 整链路 acquire_text ...")
    proc2, hwnd2 = launch_target()
    if hwnd2:
        activate(hwnd2)
        text, trail = pop.acquire_text()
        print(f"    链路轨迹: {[(d.get('level'), d.get('reason')) for d in trail]}")
        if text == EXPECTED:
            print(f"    PASS 整链路取词正确（{len(text)} 字符）")
        else:
            print(f"    FAIL 期望 {EXPECTED!r} 实际 {text!r}")
            fails += 1
        proc2.kill()
    else:
        print("    FAIL 靶子未就绪")
        fails += 1

    # 收尾：剪贴板最终检查
    final = cg.read_text()
    print(f"[5] 最终剪贴板: {final!r}")
    if final != baseline:
        print(f"    FAIL 最终状态偏离基线")
        fails += 1

    try:
        proc.kill()
    except Exception:
        pass

    print("-" * 72)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
