"""主程序功能边界回归：确保新增的自动关闭没有破坏取词链路。

针对 Watcher 的完整状态机做一次走查，重点是
"浮层生命周期"与"取词触发"两条线不会互相踩。
"""

import sys
import time

import py_pop as pop


class FakeUser32:
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
    def after(self, ms, fn=None):
        return "j"

    def after_cancel(self, job):
        pass


class FakePill:
    def __init__(self):
        self.payload = ""
        self.visible = False
        self.shown_at = 0.0
        self.rect = (0, 0, 0, 0)
        self.fg_at_show = None

    def show_at(self, x, y, text):
        self.payload = text
        px, py = pop.place_near_cursor(x, y, pop.PILL_W, pop.PILL_H)
        self.rect = (px, py, px + pop.PILL_W, py + pop.PILL_H)
        self.visible = True
        self.shown_at = time.time() * 1000
        self.fg_at_show = FU.foreground

    def contains(self, x, y):
        x0, y0, x1, y1 = self.rect
        return x0 <= x <= x1 and y0 <= y <= y1

    def hide(self):
        self.visible = False


FU = None
W = None
P = None
CALLS = []


def setup(text="仿真文本"):
    global FU, W, P, CALLS
    FU = FakeUser32()
    pop.user32 = FU
    CALLS = []

    def fake_acquire():
        CALLS.append(1)
        return (text, [{"level": "fake"}])

    pop.acquire_text = fake_acquire
    P = FakePill()
    W = pop.Watcher(FakeRoot(), lambda t, x, y: P.show_at(x, y, t), pill=P)
    return W, P


def tick():
    W.tick()


def tap(x, y):
    FU.cursor = (x, y)
    FU.lbutton = True
    tick()
    FU.lbutton = False
    tick()


def drag(x0, y0, x1, y1, steps=6):
    FU.cursor = (x0, y0)
    FU.lbutton = True
    tick()
    for i in range(1, steps + 1):
        FU.cursor = (x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
        tick()
    FU.lbutton = False
    tick()


def advance():
    if W.last_fire:
        W.last_fire -= pop.DEBOUNCE_MS + 300


def expire_grace():
    """越过 FG_CHANGE_GRACE_MS，模拟"浮层已显示一段时间"。"""
    if P.shown_at:
        P.shown_at -= pop.FG_CHANGE_GRACE_MS + 100


res = []


def ck(name, cond, note=""):
    res.append(cond)
    print(("  " + name).ljust(40) + ("PASS" if cond else "FAIL")
          + (("  " + note) if note else ""))


def main():
    print("=" * 76)
    print("主程序行为回归（拖选守卫 + 自动关闭 共存）")
    print("=" * 76)

    # 1
    setup()
    drag(200, 200, 320, 200)
    ck("拖选 -> 弹窗且取词一次", P.visible and len(CALLS) == 1)

    # 2
    setup()
    tap(200, 200)
    ck("单击 -> 不取词", len(CALLS) == 0 and W.skip_single == 1)

    # 3
    setup()
    drag(200, 200, 320, 200)
    advance()
    tap(30, 30)
    ck("弹窗后单击别处 -> 关闭", not P.visible and len(CALLS) == 1)

    # 4
    setup()
    drag(200, 200, 320, 200)
    advance()
    x0, y0, x1, y1 = P.rect
    tap(x0 + 20, y0 + 20)
    ck("弹窗上点按钮 -> 不关闭", P.visible)

    # 5
    setup()
    drag(200, 200, 320, 200)
    advance()
    drag(700, 700, 820, 700)
    ck("连续两次拖选 -> 各取词一次", len(CALLS) == 2 and P.visible)

    # 6
    setup()
    drag(200, 200, 320, 200)
    advance()
    expire_grace()
    FU.foreground = 0x3003
    tick()
    ck("切窗口 -> 关闭", not P.visible)

    # 7
    setup()
    drag(200, 200, 320, 200)
    FU.foreground = 0x3003
    tick()
    ck("grace 内切窗口 -> 保持", P.visible)

    # 8
    setup()
    drag(200, 200, 450, 200)
    advance()
    expire_grace()
    FU.esc = True
    tick()
    FU.esc = False
    tick()
    ck("Esc -> 关闭", not P.visible)

    # 9  超远拖动不取词
    setup()
    drag(100, 100, 3200, 100, steps=8)
    ck("超远拖动 -> 不取词", len(CALLS) == 0 and W.skip_too_far == 1)

    # 10 浮层关闭后再次拖选仍可用
    setup()
    drag(200, 200, 320, 200)
    advance()
    expire_grace()
    FU.foreground = 0x4004
    tick()
    closed = not P.visible
    advance()
    expire_grace()
    drag(500, 500, 620, 500)
    ck("关闭后再次拖选可用", closed and P.visible and len(CALLS) == 2)

    print()
    fails = res.count(False)
    print(f"{len(res)} 项，" + ("全部通过" if fails == 0 else f"{fails} 项失败"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
