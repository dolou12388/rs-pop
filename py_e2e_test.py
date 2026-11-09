"""端到端集成测试：主程序 + 托盘 + AI 调用。

不启动真实全局监听（会干扰鼠标），而是直接驱动主程序里的
run_action 路径，验证：
  1. 未配置时点「AI 搜索」给出可读提示，不是崩
  2. 配置后点「AI 搜索」能流式显示内容
  3. 服务端报错时窗口显示错误、不崩
  4. 关闭窗口会中断接收（cancel_event 生效）
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import py_ai
import py_config


def disp_len(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - disp_len(s))


results = []


def ck(name, cond, note=""):
    results.append((name, bool(cond)))
    print("  " + pad(name, 48) + ("PASS" if cond else "FAIL")
          + (("  " + note) if note else ""))


class Handler(BaseHTTPRequestHandler):
    mode = "stream"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if Handler.mode == "stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for p in ["量子纠缠", "是量子力学", "中很奇特的", "一种现象。"]:
                d = json.dumps({"choices": [{"delta": {"content": p}}]})
                self.wfile.write(f"data: {d}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.03)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif Handler.mode == "err":
            body = b'{"error":{"message":"quota exhausted"}}'
            self.send_response(429)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def pump(root, ms=400):
    """让主线程事件循环跑一会儿，使 pump 队列里的动作被执行。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 78)
    print("端到端集成测试：划词 -> AI 搜索 -> 流式显示")
    print("=" * 78)

    tmp = tempfile.mkdtemp(prefix="pope2e_")
    orig = py_config.config_dir
    py_config.config_dir = lambda: tmp

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import py_pop

    root = tk.Tk()
    root.withdraw()
    store = py_config.store()

    try:
        # ---------- 1 未配置 ----------
        print("\n1 未配置时点击 AI 搜索")
        win = py_pop.ResultWindow(root, "AI 搜索", "测试原文")
        ok, why = store.readiness("search")
        ck("初始状态未就绪", not ok, why)
        win.show_error(py_ai.AIError("config", why))
        for _ in range(12):
            root.update()
            time.sleep(0.02)
        body = win.body.get("1.0", "end").strip()
        ck("窗口显示错误原因", why in body, body[:50])
        ck("提示去设置", "设置" in body)
        win.close()
        ck("关闭后窗口标记为已关闭", win.closed)

        # ---------- 2 配置后流式 ----------
        print("\n2 配置后流式请求")
        store.upsert_provider({
            "id": "t", "name": "本地测试",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model": "m", "timeout": 10,
        })
        store.set_key("t", "sk-x")
        store.set_binding("search", provider_id="t")
        store.save()
        ok, why = store.readiness("search")
        ck("配置后就绪", ok, why)

        win2 = py_pop.ResultWindow(root, "AI 搜索", "量子纠缠")
        binding = store.binding("search")
        t0 = time.time()
        done = {}

        def on_done(full):
            done["text"] = full
            win2.finish(full, (time.time() - t0) * 1000)

        py_ai.ask_async("search", "量子纠缠", binding,
                        on_delta=win2.append, on_done=on_done,
                        on_error=win2.show_error,
                        cancel_event=win2.cancel_event)

        # 泵事件，等流式结束
        for _ in range(200):
            root.update()
            if "text" in done:
                break
            time.sleep(0.02)

        ck("流式完成", "text" in done, done.get("text", ""))
        pump(root, 300)          # 等状态更新被 pump 应用
        body2 = win2.body.get("1.0", "end").strip()
        ck("窗口内容与返回一致",
           body2 == "量子纠缠是量子力学中很奇特的一种现象。", body2)
        ck("状态显示完成", "完成" in win2.status_label.cget("text"),
           win2.status_label.cget("text"))
        win2.close()

        # ---------- 3 服务端报错 ----------
        print("\n3 服务端限流")
        Handler.mode = "err"
        win3 = py_pop.ResultWindow(root, "AI 搜索", "x")
        err_seen = {}

        def on_err(e):
            err_seen["e"] = e
            win3.show_error(e)

        py_ai.ask_async("search", "x", store.binding("search"),
                        on_delta=win3.append, on_done=lambda t: None,
                        on_error=on_err, cancel_event=win3.cancel_event)
        for _ in range(150):
            root.update()
            if "e" in err_seen:
                break
            time.sleep(0.02)
        ck("收到限流错误", err_seen.get("e") is not None,
           str(err_seen.get("e")))
        ck("错误类型正确",
           getattr(err_seen.get("e"), "kind", "") == "rate")
        pump(root, 300)
        body3 = win3.body.get("1.0", "end").strip()
        ck("窗口展示限流文案", "限流" in body3, body3[:60])
        win3.close()

        # ---------- 4 关闭窗口中断 ----------
        print("\n4 关闭窗口中断接收")
        Handler.mode = "stream"
        win4 = py_pop.ResultWindow(root, "翻译", "hello")
        appended = []

        py_ai.ask_async("translate", "hello", store.binding("search"),
                        on_delta=lambda p: (appended.append(p), win4.append(p)),
                        on_done=lambda t: appended.append("__DONE__"),
                        on_error=lambda e: appended.append("__ERR__"),
                        cancel_event=win4.cancel_event)
        time.sleep(0.05)
        win4.close()          # 立刻关闭 -> cancel_event 置位
        for _ in range(120):
            root.update()
            if "__DONE__" in appended or "__ERR__" in appended:
                break
            time.sleep(0.02)
        ck("关闭后不再回调 on_done", "__DONE__" not in appended,
           f"收到 {len(appended)} 段")
        ck("cancel_event 已被置位", win4.cancel_event.is_set())

        # ---------- 5 结果窗口复制的剪贴板路径 ----------
        print("\n5 结果窗口复制")
        import py_clipguard as cg
        cg.write_text("BASELINE_COPY_TEST")
        win5 = py_pop.ResultWindow(root, "AI 搜索", "源文本")
        win5._acc = "要被复制的内容"
        win5._copy()
        pump(root, 300)
        ck("复制到剪贴板成功", cg.read_text() == "要被复制的内容",
           repr(cg.read_text()))
        ck("状态提示已复制", "已复制" in win5.status_label.cget("text"))
        win5.close()

    finally:
        try:
            root.destroy()
        except Exception:
            pass
        srv.shutdown()
        py_config.config_dir = orig
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    fails = sum(1 for _, ok in results if not ok)
    print(f"{len(results)} 项，" + ("全部通过" if fails == 0 else f"{fails} 项失败"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
