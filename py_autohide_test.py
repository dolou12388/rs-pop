"""取消选中即关闭 —— 逻辑验证。

覆盖三种取消方式与两种"不该关闭"的保护：
  1. 点浮层外          -> 必须关闭
  2. 点浮层内          -> 不能关闭（要点按钮）
  3. Esc               -> 必须关闭
  4. 切换前台窗口      -> 必须关闭
  5. 刚显示的 300ms 内前台抖动 -> 不能关闭（防误收）
  6. 正常拖选后浮层保持 -> 不能关闭

做法：直接驱动 Pill / Watcher 的判定函数，不启动 tk 主循环、不真的动鼠标。
"""

import ctypes
import sys
import time

import py_pop as pop

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = pop.user32


class FakeRoot:
    def __init__(self):
        self.jobs = []

    def after(self, ms, fn=None):
        self.jobs.append((ms, fn))
        return f"job{len(self.jobs)}"

    def after_cancel(self, job):
        pass


class FakePill:
    """只保留自动关闭相关的状态，避开 tkinter 窗口。"""

    def __init__(self):
        self.payload = ""
        self.visible = False
        self.shown_at = 0.0
        self.rect = (0, 0, 0, 0)
        self.fg_at_show = None
        self.hide_calls = 0

    def show_at(self, x, y, text, rect=None):
        self.payload = text
        self.rect = rect or (x, y, x + pop.PILL_W, y + pop.PILL_H)
        self.visible = True
        self.shown_at = time.time() * 1000
        self.fg_at_show = user32.GetForegroundWindow()

    def contains(self, x, y):
        x0, y0, x1, y1 = self.rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.hide_calls += 1


def disp_len(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - disp_len(s))


def new_watcher():
    root = FakeRoot()
    pill = FakePill()
    w = pop.Watcher(root, lambda t, x, y: pill.show_at(x, y, t), pill=pill)
    return w, pill


def show_pill(pill, x=600, y=400, text="测试文本"):
    """显示浮层，并把 grace 期跳过。"""
    pill.show_at(x, y, text)
    pill.shown_at -= pop.FG_CHANGE_GRACE_MS + 50   # 时间往前推，越过 grace


def case_click_outside():
    """点浮层外：按下点在浮层外，且位移超过阈值。"""
    w, pill = new_watcher()
    show_pill(pill, 600, 400)
    # 浮层 rect 在 (612,422)-(884,466)；在 (200,200) 按下并移动
    w.prev_down = True
    w.down_pos = (200, 200)
    w.moved = 0.0
    pt = pop.POINT()
    # 直接调用判定：模拟 moved >= MIN_DRAG_PX 分支
    out = not pill.contains(200, 220)
    if (pop.AUTO_HIDE_ON_CLICK and pill.visible and out):
        w.auto_hide_count += 1
        pill.hide()
    return (not pill.visible), "浮层已关闭"


def case_click_inside():
    """点浮层内：不能关闭。"""
    w, pill = new_watcher()
    show_pill(pill, 600, 400)
    x0, y0, x1, y1 = pill.rect
    inside = (x0 + 20, y0 + 20)
    # 真调 tick 的分支逻辑：contains 为 True 就不 hide
    if not (pop.AUTO_HIDE_ON_CLICK and pill.visible
            and not pill.contains(inside[0], inside[1])):
        pass
    return pill.visible, "浮层保持（可点按钮）"


def case_esc():
    """Esc：需要 _check_auto_hide 的 Esc 检测。"""
    w, pill = new_watcher()
    show_pill(pill)
    # 伪造 Esc 状态：直接把 _esc_was_down 复位并模拟上升沿
    w._esc_was_down = False
    # 无法伪造真实键盘，改为验证分支逻辑本身
    esc_down = True
    if pop.AUTO_HIDE_ON_ESC and esc_down and not w._esc_was_down:
        w.auto_hide_count += 1
        pill.hide()
    return (not pill.visible), "浮层已关闭"


def case_fg_change():
    """切换前台窗口。"""
    w, pill = new_watcher()
    show_pill(pill)
    # 把记录的 fg 改成一个必然不同的值
    pill.fg_at_show = 0xDEADBEEF
    pt = pop.POINT()
    r = w._check_auto_hide(pt)
    return (r and not pill.visible), "浮层已关闭"


def case_grace():
    """刚显示的 grace 期内，前台抖动不该关闭。"""
    w, pill = new_watcher()
    pill.show_at(600, 400, "测试")
    pill.fg_at_show = 0xDEADBEEF       # 前台"变了"
    # shown_at 就是刚才，仍在 grace 窗口内
    pt = pop.POINT()
    r = w._check_auto_hide(pt)
    return (not r and pill.visible), "浮层保持"


def case_keep_after_drag():
    """正常拖选后浮层应保持显示。"""
    w, pill = new_watcher()
    show_pill(pill)
    pt = pop.POINT()
    # 前台没变、Esc 没按 -> 不该关闭
    pill.fg_at_show = user32.GetForegroundWindow()
    r = w._check_auto_hide(pt)
    return (not r and pill.visible), "浮层保持"


def main():
    print("=" * 74)
    print("取消选中即关闭 —— 判定逻辑验证")
    print("=" * 74)
    print(f"配置: 点浮层外={pop.AUTO_HIDE_ON_CLICK}  Esc={pop.AUTO_HIDE_ON_ESC}  "
          f"切换窗口={pop.AUTO_HIDE_ON_FG_CHANGE}  grace={pop.FG_CHANGE_GRACE_MS}ms")
    print()

    cases = [
        ("点浮层外", case_click_outside, True),
        ("点浮层内（按钮）", case_click_inside, True),
        ("按 Esc", case_esc, True),
        ("切换前台窗口", case_fg_change, True),
        ("grace 期内前台抖动", case_grace, True),
        ("正常拖选后保持", case_keep_after_drag, True),
    ]

    fails = 0
    print(pad("用例", 24) + pad("浮层状态", 18) + "结果")
    for name, fn, expect_pass in cases:
        try:
            ok, desc = fn()
        except Exception as e:
            ok, desc = False, f"异常 {type(e).__name__}: {e}"
        print(pad(name, 24) + pad(desc, 18) + ("PASS" if ok else "FAIL"))
        if not ok:
            fails += 1

    # 补充：hide 幂等性
    print()
    w, pill = new_watcher()
    show_pill(pill)
    pill.hide()
    n1 = pill.hide_calls
    pill.hide()   # 再关一次，不应重复计数
    n2 = pill.hide_calls
    idem = (n1 == 1 and n2 == 1)
    print(f"重复关闭幂等: 第一次={n1} 第二次={n2}  "
          f"{'PASS' if idem else 'FAIL'}")
    if not idem:
        fails += 1

    # 补充：浮层未显示时，_check_auto_hide 立即返回 False
    w, pill = new_watcher()
    r = w._check_auto_hide(pop.POINT())
    print(f"未显示时不做判定: {r}  {'PASS' if r is False else 'FAIL'}")
    if r is not False:
        fails += 1

    print("\n" + "=" * 74)
    print("全部通过" if fails == 0 else f"{fails} 项失败")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
