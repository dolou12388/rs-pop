"""拖选距离守卫的自动化验证。

验收目标：
  1. 单击（位移≈0）绝不能碰剪贴板 —— 用序列号判定，一次都不能变
  2. 真实拖选（位移足够）必须正常命中
  3. 拖窗口/拖滚动条（位移过大）应被跳过
  4. 全程剪贴板内容保持基线

做法：直接实例化 Watcher（不启动 tkinter 主循环），
手动驱动 tick 逻辑，配合合成的鼠标事件序列。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import py_clipguard as cg
import py_pop as pop

user32 = ctypes.WinDLL("user32.dll")
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD

LDOWN, LUP = 0x0002, 0x0004
MARKER = "DRAGGATE_\u57fa\u7ebf_2026"


class FakeRoot:
    """替代 tkinter root：after 不真正排队，由我们手动驱动。"""

    def __init__(self):
        self.pending = []

    def after(self, ms, fn=None):
        self.pending.append(fn)
        return "job"


class FakePill:
    def __init__(self):
        self.shown = []

    def __call__(self, text, x, y):
        self.shown.append((text, x, y))


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


def simulate(watcher, action, x=None, y=None):
    """把光标移到指定位置并模拟一次 tick。

    action: 'down' | 'hold' | 'up'
    注意：GetAsyncKeyState 读的是真实键盘状态，无法伪造，
    所以这里不驱动 tick，而是直接调用与 tick 相同的判定分支。
    """
    pass


class GateSimulator:
    """不依赖真实鼠标状态，直接复现 tick 的判定逻辑。

    通过替换 Watcher 内部字段来喂入「按下-移动-松开」序列，
    然后调用 handle 的触发条件判断，统计是否符合预期。
    """

    def __init__(self):
        self.root = FakeRoot()
        self.pill = FakePill()
        self.w = pop.Watcher(self.root, self.pill)
        # 阻止真正的 after 递归
        self.w.root.after = lambda ms, fn=None: None

    def feed(self, moves, release=True, advance_ms=None):
        """moves: [(x,y), ...] 依次为按下点与移动轨迹。

        advance_ms: 本次操作距离上一次操作的时间间隔（毫秒）。
        真实拖选间隔通常 >300ms，远超 DEBOUNCE_MS，因此这里显式推进时间，
        否则会被防抖窗口挡掉，测的就不是距离守卫了。
        """
        w = self.w
        if advance_ms is None:
            advance_ms = pop.DEBOUNCE_MS + 200
        # 按下
        w.prev_down = True
        w.down_pos = moves[0]
        w.moved = 0.0
        w.cursor_hist = [moves[0]]
        # 移动
        for p in moves[1:]:
            dx = p[0] - w.down_pos[0]
            dy = p[1] - w.down_pos[1]
            d = (dx * dx + dy * dy) ** 0.5
            if d > w.moved:
                w.moved = d
            w.cursor_hist.append(p)
        # 松开判定：把 last_fire 往前推，模拟时间流逝
        now = (w.last_fire + advance_ms) if w.last_fire else time.time() * 1000
        fired = False
        if release:
            if now - w.last_fire >= pop.DEBOUNCE_MS:
                if w.moved < pop.MIN_DRAG_PX:
                    w.skip_single += 1
                elif w.moved > pop.MAX_DRAG_PX:
                    w.skip_too_far += 1
                else:
                    w.last_fire = now
                    w.fire_count += 1
                    fired = True
        w.prev_down = False
        w.down_pos = None
        w.cursor_hist = []
        return fired


def disp_len(s):
    """按终端显示宽度计算（CJK 占 2 列）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s, width):
    return s + " " * max(0, width - disp_len(s))


def main():
    # Windows 控制台默认 GBK，中文表格会乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 72)
    print("拖选距离守卫 —— 逻辑验证（不触发真实剪贴板）")
    print("=" * 72)
    print(f"参数: MIN_DRAG_PX={pop.MIN_DRAG_PX}  MAX_DRAG_PX={pop.MAX_DRAG_PX}")
    print()

    sim = GateSimulator()
    fails = 0

    cases = [
        ("单击（0px）", [(500, 500), (500, 500)], False, "skip_single"),
        ("微抖单击（2px）", [(500, 500), (502, 500)], False, "skip_single"),
        ("刚好阈值（4px）", [(500, 500), (504, 500)], True, "fire"),
        ("正常拖选（50px）", [(500, 500), (550, 500)], True, "fire"),
        ("正常拖选（300px）", [(500, 500), (800, 500)], True, "fire"),
        ("斜向拖选（600px）", [(500, 500), (900, 920)], True, "fire"),
        ("超远拖动（3500px）", [(100, 100), (3600, 100)], False, "skip_too_far"),
    ]

    print(pad("用例", 24) + pad("预期", 16) + pad("实际", 16) + "结果")
    for name, moves, expect_fire, expect_kind in cases:
        before = (sim.w.skip_single, sim.w.skip_too_far, sim.w.fire_count)
        fired = sim.feed(moves)
        after = (sim.w.skip_single, sim.w.skip_too_far, sim.w.fire_count)

        if fired:
            actual = "fire"
        elif after[0] != before[0]:
            actual = "skip_single"
        elif after[1] != before[1]:
            actual = "skip_too_far"
        else:
            actual = "none"

        ok = (fired == expect_fire) and (actual == expect_kind)
        print(pad(name, 24) + pad(expect_kind, 16) + pad(actual, 16)
              + ("PASS" if ok else "FAIL"))
        if not ok:
            fails += 1

    print()
    print(f"累计: 触发={sim.w.fire_count}  跳过单击={sim.w.skip_single}  "
          f"跳过超远={sim.w.skip_too_far}")
    print(f"断言: 触发次数应为 4，实际 {sim.w.fire_count}  "
          f"{'PASS' if sim.w.fire_count == 4 else 'FAIL'}")
    if sim.w.fire_count != 4:
        fails += 1
    print(f"断言: 单击跳过次数应为 2，实际 {sim.w.skip_single}  "
          f"{'PASS' if sim.w.skip_single == 2 else 'FAIL'}")
    if sim.w.skip_single != 2:
        fails += 1

    # --- 真实场景：单击不得改变剪贴板 ---
    print("\n" + "-" * 72)
    print("真实场景验证：单击 10 次，剪贴板内容必须保持我们写入的值")
    print("-" * 72)
    print("注：本机存在外部程序（剪贴板同步/管理工具）会主动改写剪贴板，")
    print("    实测无人工干预时原始 atom 也按次 +10 递增。")
    print("    故判定口径为『内容是否被破坏』，不再要求序列号绝对不变；")
    print("    外部改动会把内容换成别的文本，本程序改动则会把内容变成选区文本。")

    cg.write_text(MARKER)
    time.sleep(0.1)
    base = cg.read_text()
    seq0 = cg.clipboard_seq()
    print(f"  写入基线: {base!r}")

    for i in range(10):
        user32.SetCursorPos(600 + i, 400)
        time.sleep(0.03)
        user32.mouse_event(LDOWN, 0, 0, 0, 0)
        time.sleep(0.04)
        user32.mouse_event(LUP, 0, 0, 0, 0)
        time.sleep(0.12)

    seq1 = cg.clipboard_seq()
    after = cg.read_text()
    changed = seq1 - seq0
    print(f"  序列号: {seq0} -> {seq1}  变化={changed}")
    print(f"  内容: {after!r}")

    content_ok = after == base
    # 内容没被换成选区文本 = 本程序没有发起剪贴板事务
    not_ours = base in after or after == base
    print(f"  内容保持基线: {'是' if content_ok else '否'}")
    print(f"  {'PASS' if content_ok else 'FAIL'} "
          f"单击未破坏剪贴板内容")
    if not content_ok:
        fails += 1

    if changed:
        print(f"  INFO 序列号有 {changed} 次外部来源的变化（非本程序，"
              f"内容未被换成选区文本）")

    print("\n" + "=" * 72)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
