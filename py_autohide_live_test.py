"""取消选中即关闭 —— 驱动真实 tick 的验证。

与 py_autohide_test 的区别：那个测试分别调各分支；这个测试
伪造 Win32 的鼠标/键盘/前台窗口状态，真正跑 Watcher.tick()，
能抓到分支之间的时序问题（比如"先隐藏后又被重新弹出"）。

伪造点：GetAsyncKeyState / GetCursorPos / GetForegroundWindow
"""

import ctypes
import sys
import time

import py_pop as pop

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class FakeUser32:
    """只实现 py_pop 用到的几个查询函数，其余转发真实现。"""

    def __init__(self):
        self.real = pop.user32
        self.lbutton = False
        self.esc = False
        self.cursor = (0, 0)
        self.foreground = 0x1001

    def GetAsyncKeyState(self, vk):
        if vk == pop.VK_LBUTTON:
            return -32768 if self.lbutton else 0
        if vk == pop.VK_ESCAPE:
            return -32768 if self.esc else 0
        return 0

    def GetCursorPos(self, ref):
        pt = ref._obj
        pt.x, pt.y = self.cursor
        return 1

    def GetForegroundWindow(self):
        return self.foreground

    def __getattr__(self, name):
        return getattr(self.real, name)


class FakeRoot:
    def __init__(self):
        self.jobs = 0

    def after(self, ms, fn=None):
        self.jobs += 1
        return f"j{self.jobs}"

    def after_cancel(self, job):
        pass


class FakePill:
    def __init__(self):
        self.payload = ""
        self.visible = False
        self.shown_at = 0.0
        self.rect = (0, 0, 0, 0)
        self.fg_at_show = None
        self.hide_calls = 0
        self.show_calls = 0
        self.history = []          # ('show', text) / ('hide', None)

    def show_at(self, x, y, text):
        self.payload = text
        px, py = pop.place_near_cursor(x, y, pop.PILL_W, pop.PILL_H)
        self.rect = (px, py, px + pop.PILL_W, py + pop.PILL_H)
        self.visible = True
        self.shown_at = time.time() * 1000
        self.fg_at_show = FU.foreground
        self.show_calls += 1
        self.history.append(("show", text))

    def contains(self, x, y):
        x0, y0, x1, y1 = self.rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.hide_calls += 1
        self.history.append(("hide", None))


FU = None
WATCHER = None
PILL = None


def setup():
    global FU, WATCHER, PILL
    FU = FakeUser32()
    pop.user32 = FU
    # 取词固定返回一段文本，避免真的碰剪贴板
    pop.acquire_text = lambda: ("仿真选区文本", [{"level": "fake"}])
    PILL = FakePill()
    WATCHER = pop.Watcher(FakeRoot(),
                          lambda t, x, y: PILL.show_at(x, y, t), pill=PILL)
    # Watcher 里 pyd 里的 GetForegroundWindow 也走 FU
    return WATCHER, PILL, FU


def tick():
    """驱动一次 tick，但拦掉 self.root.after 递归。"""
    WATCHER.tick()


def press(x, y):
    FU.cursor = (x, y)
    FU.lbutton = True
    tick()


def release(x, y):
    FU.cursor = (x, y)
    FU.lbutton = False
    tick()


def drag(x0, y0, x1, y1, steps=5):
    press(x0, y0)
    for i in range(1, steps + 1):
        FU.cursor = (x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
        tick()
    release(x1, y1)


def expire_grace():
    PILL.shown_at -= pop.FG_CHANGE_GRACE_MS + 100


def advance_time():
    """把防抖窗口往前推，模拟两次操作之间真实流逝的时间。

    测试里 tick 是瞬发的，两次拖选在真实时钟上只差几微秒，
    会被 DEBOUNCE_MS 拦掉；真实操作间隔远大于 160ms。
    """
    if WATCHER.last_fire:
        WATCHER.last_fire -= pop.DEBOUNCE_MS + 300


def disp_len(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - disp_len(s))


results = []


def check(name, cond, note=""):
    results.append((name, bool(cond), note))
    print(pad(name, 30) + ("PASS" if cond else "FAIL") + ("  " + note if note else ""))


def main():
    print("=" * 78)
    print("取消选中即关闭 —— 真实 tick 驱动验证")
    print("=" * 78)
    print(f"配置: 点浮层外={pop.AUTO_HIDE_ON_CLICK} Esc={pop.AUTO_HIDE_ON_ESC} "
          f"切换窗口={pop.AUTO_HIDE_ON_FG_CHANGE} grace={pop.FG_CHANGE_GRACE_MS}ms")
    print()

    # ---------- 1. 拖选应弹出 ----------
    setup()
    drag(300, 300, 420, 300)
    check("1 拖选弹出浮层", PILL.visible and PILL.payload == "仿真选区文本",
          f"rect={PILL.rect}")
    expire_grace()

    # ---------- 2. 点浮层外（原地单击）应立即关闭 ----------
    show_pill_rect = PILL.rect
    out_x = max(10, show_pill_rect[0] - 200)
    out_y = show_pill_rect[1]
    press(out_x, out_y)
    hid_on_press = not PILL.visible
    release(out_x, out_y)
    check("2 点浮层外(无位移)立即关闭", hid_on_press and not PILL.visible,
          f"按下即关={hid_on_press}")

    # ---------- 3. 点浮层内不应关闭 ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    x0, y0, x1, y1 = PILL.rect
    press(x0 + 15, y0 + 15)
    inside_ok = PILL.visible
    release(x0 + 15, y0 + 15)
    check("3 点浮层内(按钮)不关闭", inside_ok and PILL.visible)

    # ---------- 4. Esc 关闭 ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    FU.esc = True
    tick()
    esc_ok = not PILL.visible
    FU.esc = False
    check("4 按 Esc 关闭", esc_ok)

    # ---------- 5. 切换前台窗口关闭 ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    FU.foreground = 0x2002
    tick()
    check("5 切换前台窗口关闭", not PILL.visible)

    # ---------- 6. grace 期内前台抖动不关闭 ----------
    setup()
    drag(300, 300, 420, 300)
    # 不动 shown_at，仍在 grace 内
    FU.foreground = 0x2002
    tick()
    check("6 grace 期内前台抖动不关闭", PILL.visible)

    # ---------- 7. 拖选到浮层外新文字：应关旧弹新 ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    first_rect = PILL.rect
    advance_time()               # 真实操作间隔 > 防抖窗口
    drag(700, 700, 860, 700)     # 在浮层外重新拖选
    check("7 二次拖选替换浮层", PILL.visible and PILL.rect != first_rect,
          f"show={PILL.show_calls} hide={PILL.hide_calls} rect={PILL.rect}")

    # ---------- 8. 未显示时扰乱输入不应崩溃 ----------
    setup()
    press(500, 500)
    release(500, 500)
    check("8 浮层未显示时单击不崩溃", not PILL.visible and WATCHER.skip_single >= 1)

    # ---------- 9. 单击不触发取词 ----------
    setup()
    press(500, 500)
    release(500, 500)
    check("9 单击不取词", WATCHER.fire_count == 0 and WATCHER.skip_single == 1)

    # ---------- 10. Esc 上升沿只算一次 ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    before = WATCHER.auto_hide_count
    FU.esc = True
    tick()
    tick()
    tick()
    n = WATCHER.auto_hide_count - before
    FU.esc = False
    tick()
    check("10 长按 Esc 只关一次", n == 1, f"计数增量={n}")

    # ---------- 11. 从浮层内部开始拖选（浮层挡住目标文字的情形） ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    advance_time()
    x0, y0, x1, y1 = PILL.rect
    before_show = PILL.show_calls
    drag(x0 + 10, y0 + 10, x1 + 200, y1 + 10)   # 起点在浮层内
    # 起点在浮层内 -> 按下时不算"点浮层外"，浮层保持；松开时是有效拖选，应更新内容
    check("11 浮层内起手拖选仍能更新",
          PILL.visible and PILL.show_calls == before_show + 1,
          f"show {before_show}->{PILL.show_calls}")

    # ---------- 12. 浮层外按下但位移不足（点一下别处） ----------
    setup()
    drag(300, 300, 420, 300)
    expire_grace()
    advance_time()
    x0, y0, _, _ = PILL.rect
    press(x0 - 150, y0 - 100)
    closed = not PILL.visible
    release(x0 - 150, y0 - 100)
    check("12 浮层外单击(无位移)关闭", closed and not PILL.visible)

    print("\n" + "=" * 78)
    fails = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results)} 项检查，" +
          ("全部通过" if fails == 0 else f"{fails} 项失败"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
