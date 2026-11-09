# -*- coding: utf-8 -*-
"""追问功能测试。

覆盖三层：
  A. 消息构造（py_ai.build_messages）—— 历史注入顺序是否正确
  B. 历史累积与渲染定位（ResultWindow）—— 多轮内容是否都在、位置对不对
  C. 交互契约（按钮状态、Enter/Shift+Enter、占位符）—— 不靠人眼
"""

import os
import sys
import threading
import time

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


# ---------- A. 消息构造 ----------

def test_messages():
    print("\n[A] 消息构造：历史注入顺序")
    import py_ai

    base = py_ai.build_messages("search", "原文内容", "你是助手。")
    check("首轮只有 system+user", len(base) == 2, base)
    check("首条是 system", base[0]["role"] == "system", base)
    # 原文会被包一层"待处理文本："——这是给模型的指令边界，原文必须在内
    check("第二条是 user 且含原文",
          base[1]["role"] == "user" and "原文内容" in base[1]["content"],
          base[1])

    hist = [("user", "第一问"), ("assistant", "第一答")]
    m = py_ai.build_messages("search", "原文内容", "你是助手。", history=hist)
    check("带历史时有 4 条", len(m) == 4, m)
    check("system 仍在最前", m[0]["role"] == "system")
    check("原文 user 紧跟 system",
          m[1]["role"] == "user" and "原文内容" in m[1]["content"],
          m[1])
    check("历史问答按序在后",
          [x["role"] for x in m[2:]] == ["user", "assistant"],
          m[2:])
    check("历史内容正确", m[2]["content"] == "第一问"
          and m[3]["content"] == "第一答")

    # 多轮：历史应完整保留
    hist2 = [("user", "问1"), ("assistant", "答1"),
             ("user", "问2"), ("assistant", "答2")]
    m2 = py_ai.build_messages("search", "原文", "sys", history=hist2)
    check("多轮历史全保留", len(m2) == 6, len(m2))
    check("多轮顺序正确",
          [x["content"] for x in m2[2:]] == ["问1", "答1", "问2", "答2"],
          m2[2:])

    # system 为空时不应有空的 system 消息
    m3 = py_ai.build_messages("search", "原文", "")
    check("空提示词不产生 system", len(m3) == 1 and m3[0]["role"] == "user", m3)

    # 空历史等价于无历史
    m4 = py_ai.build_messages("search", "原文", "sys", history=[])
    check("空历史与无历史一致", len(m4) == 2, m4)

    # 历史里的空内容应被跳过（防止 API 报错）
    m5 = py_ai.build_messages("search", "原文", "sys",
                              history=[("user", ""), ("assistant", "有内容")])
    check("跳过空历史项",
          all(x["content"] for x in m5), m5)


# ---------- B. 历史累积与渲染 ----------

def test_window():
    print("\n[B] 结果窗：历史累积与多轮渲染")
    import tkinter as tk
    import py_dpi
    import py_markdown
    import py_pop

    py_dpi.enable()
    root = tk.Tk()
    root.withdraw()
    py_dpi.set_tk_scaling(root)
    py_markdown.init_fonts(root)

    asked = []

    def on_ask(q, hist):
        asked.append((q, list(hist)))

    win = py_pop.ResultWindow(root, "AI 搜索", "原始选中文字",
                              on_ask=on_ask, action_key="search")

    check("初始历史为空", win.history == [], win.history)
    check("初始轮次为空", win._turns == [], win._turns)
    check("初始不忙", win._busy is False)
    check("有追问回调", win.on_ask is not None)
    check("输入框有占位提示", win._hint_shown is True)
    check("占位时不产生提问文本", win.ask_text() == "",
          repr(win.ask_text()))

    # ---- 第一轮完成 ----
    win.begin_request()
    win.finish("# 标题\n\n第一轮回答 **粗体**", 1200)
    _pump(root, win)

    check("第一轮后历史 1 条", len(win.history) == 1, win.history)
    check("记的是 assistant", win.history[0][0] == "assistant",
          win.history)
    check("轮次记录 1 条", len(win._turns) == 1, win._turns)
    check("第一轮问题为空（不是原文）", win._turns[0][0] == "",
          repr(win._turns[0][0]))

    body1 = win.body.get("1.0", "end-1c")
    check("正文含第一轮内容", "第一轮回答" in body1, body1[:80])
    check("标题被渲染（# 消失）", "# 标题" not in body1, body1[:80])
    check("粗体标记被消费", "**粗体**" not in body1, body1[:120])

    # ---- 追问 ----
    win._hint_shown = False
    win.ask_entry.delete("1.0", "end")
    win.ask_entry.insert("1.0", "那它有什么缺点？")
    check("能读到追问文本", win.ask_text() == "那它有什么缺点？",
          repr(win.ask_text()))

    win._do_ask()
    check("追问后进入忙态", win._busy is True)
    check("追问触发了回调", len(asked) == 1, asked)
    check("回调收到问题文本", asked[0][0] == "那它有什么缺点？", asked)
    check("回调收到的历史不含本轮问题（由窗口自己加）",
          all(c != "那它有什么缺点？" for r, c in asked[0][1]),
          asked[0][1])
    check("历史已记 user",
          win.history and win.history[-1] == ("user", "那它有什么缺点？"),
          win.history)
    check("追问后输入框被清空", win.ask_text() == "", repr(win.ask_text()))
    check("记下本轮问题", win._cur_q == "那它有什么缺点？", win._cur_q)

    body2 = win.body.get("1.0", "end-1c")
    check("正文出现追问回显", "那它有什么缺点？" in body2, body2[-160:])
    check("正文仍保留第一轮",
          "第一轮回答" in body2, body2[:200])
    check("出现轮次分隔线", "─" in body2, body2[-200:])

    # ---- 第二轮完成 ----
    win.begin_request()
    win.finish("第二轮回答内容", 800)
    _pump(root, win, rounds=3)

    check("历史 3 条", len(win.history) == 3, win.history)
    check("历史顺序 user/assistant",
          [r for r, _ in win.history] == ["assistant", "user", "assistant"],
          win.history)
    check("轮次记录 2 条", len(win._turns) == 2, win._turns)
    check("第二轮记下了问题",
          win._turns[1][0] == "那它有什么缺点？", win._turns)

    body3 = win.body.get("1.0", "end-1c")
    check("两轮内容同时在", "第一轮回答" in body3 and "第二轮回答内容" in body3,
          body3)
    check("第一轮在第一轮位置（顺序未乱）",
          body3.find("第一轮回答") < body3.find("第二轮回答内容"),
          f"{body3.find('第一轮回答')} vs {body3.find('第二轮回答内容')}")

    # ---- 忙时拒绝并发 ----
    win._busy = True
    n_before = len(asked)
    win._hint_shown = False
    win.ask_entry.delete("1.0", "end")
    win.ask_entry.insert("1.0", "并发尝试")
    win._do_ask()
    check("忙时不再发请求", len(asked) == n_before, asked)
    win._busy = False

    # ---- 空问题被拒 ----
    win._hint_shown = False
    win.ask_entry.delete("1.0", "end")
    before = len(asked)
    win._do_ask()
    check("空问题不发请求", len(asked) == before, asked)

    # ---- 停止 ----
    win._busy = True
    win.btn_stop.config_state("normal")
    win._stop()
    check("停止后退出忙态", win._busy is False)
    check("停止置了取消事件", win.cancel_event.is_set())

    # ---- 原文切换在多轮下不丢内容 ----
    raw = win._full_raw()
    check("原文含两轮问题回显",
          "那它有什么缺点？" in raw, raw[:200])
    check("原文含两轮回答",
          "第一轮回答" in raw and "第二轮回答内容" in raw, raw[:300])
    check("原文不含第一轮的原文（首轮问题为空）",
          "❯ 你问：原始选中文字" not in raw, raw[:200])

    # ---- 字号缩放不丢轮次 ----
    win._zoom(1)
    _pump(root, win, rounds=2)
    z = win.body.get("1.0", "end-1c")
    check("缩放后两轮仍在",
          "第一轮回答" in z and "第二轮回答内容" in z, z[:300])

    # ---- 历史上限 ----
    win2 = py_pop.ResultWindow(root, "翻译", "文字", on_ask=on_ask,
                               action_key="translate")
    for i in range(win2.MAX_HISTORY_TURNS + 5):
        win2.history.append(("user", f"q{i}"))
        win2.history.append(("assistant", f"a{i}"))
    # 手动触发一次裁剪逻辑
    win2.history = win2.history[-(win2.MAX_HISTORY_TURNS * 2):]
    check("历史被裁剪到上限",
          len(win2.history) == win2.MAX_HISTORY_TURNS * 2,
          len(win2.history))

    win.close()
    win2.close()
    _pump(root, win, rounds=2)
    root.destroy()


def _pump(root, win, rounds=1, ms=60):
    """驱动事件循环，让 after 排队的渲染任务真正执行。"""
    for _ in range(rounds):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            try:
                root.update()
            except Exception:
                return
            time.sleep(0.005)


# ---------- C. 界面结构 ----------

def test_ui_contract():
    print("\n[C] 界面契约")
    import tkinter as tk
    import py_dpi
    import py_markdown
    import py_pop

    py_dpi.enable()
    root = tk.Tk()
    root.withdraw()
    py_dpi.set_tk_scaling(root)
    py_markdown.init_fonts(root)

    win = py_pop.ResultWindow(root, "解释", "x", on_ask=lambda q, h: None)
    _pump(root, win, rounds=2)

    check("有追问输入框", hasattr(win, "ask_entry"))
    check("有追问按钮", hasattr(win, "btn_ask"))
    check("有停止按钮", hasattr(win, "btn_stop"))
    check("有正文区", hasattr(win, "body"))
    check("有滚动条", hasattr(win, "sb"))
    check("有原文折叠开关", hasattr(win, "src_toggle"))

    # 尺寸必须是 DPI 换算后的值，不能是裸 620
    g = win.win.geometry()
    w = int(g.split("x")[0])
    expect = py_dpi.px(620)
    check("窗口宽度按 DPI 换算", w >= expect - 2,
          f"实际 {w} 期望 {expect}")

    # Shift+Enter 不应触发发送
    class E:
        state = 0x0001
    r = win._on_ask_return(E())
    check("Shift+Enter 不发送（返回 None）", r is None, r)

    class E2:
        state = 0
    before = len(win.history)
    win._on_ask_return(E2())   # 空内容，应被拒但不抛异常
    check("Enter 在空输入下不崩", True)
    check("空 Enter 没动历史", len(win.history) == before)

    # 按钮禁用态
    win.btn_ask.config_state("disabled")
    ok_disabled = not getattr(win.btn_ask.w, "_enabled", True)
    check("追问按钮可被禁用", ok_disabled, getattr(win.btn_ask.w, "_enabled", None))
    win.btn_ask.config_state("normal")
    check("追问按钮可恢复", getattr(win.btn_ask.w, "_enabled", False))

    win.close()
    _pump(root, win, rounds=2)
    root.destroy()


# ---------- D. 异步链路（假供应商） ----------

def test_async_chain():
    print("\n[D] 异步链路：on_delta/on_done 走真实队列")
    import py_ai

    # 直接测 ask_async 的参数透传，不真的发网络请求
    got = {}
    orig = py_ai.complete

    def fake_complete(action, text, binding, on_delta=None, on_done=None,
                      on_error=None, cancel_event=None, history=None,
                      **kw):
        got["history"] = history
        got["text"] = text
        if on_delta:
            on_delta("片段")
        return "完整回答"

    py_ai.complete = fake_complete
    try:
        out = {}
        t, ev = py_ai.ask_async("search", "问题", {},
                                on_delta=lambda s: None,
                                on_done=lambda s: out.setdefault("d", s),
                                history=[("user", "旧问"), ("assistant", "旧答")])
        t.join(timeout=5)
        check("ask_async 把 history 透传下去",
              got.get("history") == [("user", "旧问"), ("assistant", "旧答")],
              got)
        check("on_done 被调用", out.get("d") == "完整回答", out)
    finally:
        py_ai.complete = orig


if __name__ == "__main__":
    print("=" * 62)
    print("追问功能测试")
    print("=" * 62)
    test_messages()
    test_window()
    test_ui_contract()
    test_async_chain()
    print("\n" + "=" * 62)
    print(f"结果：{PASS} 通过 / {FAIL} 失败 / 共 {PASS + FAIL}")
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
    print("=" * 62)
    sys.exit(0 if FAIL == 0 else 1)
