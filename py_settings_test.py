"""设置界面测试：真实创建 Tk 窗口，程序化操作控件。

不用截图判断，而是直接读控件的值/状态，这才是可断言的。
覆盖：加载、编辑、保存、新增、删除、绑定切换、Key 显示切换、
      未保存提示、测试连接（打本地模拟服务）。
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk

import py_config
import py_ai


def disp_len(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, w):
    return s + " " * max(0, w - disp_len(s))


results = []


def ck(name, cond, note=""):
    results.append((name, bool(cond)))
    print("  " + pad(name, 46) + ("PASS" if cond else "FAIL")
          + (("  " + note) if note else ""))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 78)
    print("设置界面测试（真实 Tk，程序化操作）")
    print("=" * 78)

    tmp = tempfile.mkdtemp(prefix="popui_")
    orig = py_config.config_dir
    py_config.config_dir = lambda: tmp

    # 预置两个供应商
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
        json.dump({
            "providers": [
                {"id": "a", "name": "供应商A",
                 "base_url": "https://api.example.com/v1", "model": "model-a"},
                {"id": "b", "name": "供应商B",
                 "base_url": "http://127.0.0.1:9/v1", "model": "model-b"},
            ],
            "bindings": {
                "search": {"provider_id": "a", "prompt": "搜索提示"},
                "translate": {"provider_id": "a", "prompt": "翻译提示"},
                "explain": {"provider_id": "b", "prompt": "解释提示"},
            },
        }, f, ensure_ascii=False)

    store = py_config.ConfigStore()
    import py_settings

    win = py_settings.SettingsWindow(store)
    # 不调用 show()（会阻塞 mainloop），手工搭壳
    win.root = tk.Tk()
    win.root.withdraw()               # 不真的弹出来干扰
    py_settings.style_tk(win.root)
    win._build()
    win._load_providers()
    win.root.update()

    try:
        # ---------- 1 初始加载 ----------
        print("\n1 初始加载")
        items = win.tree.get_children()
        ck("列表显示两个供应商", len(items) == 2, f"{items}")
        ck("默认选中第一个", win.current_id == "a", win.current_id)
        ck("名称已填入", win.f_name.get() == "供应商A", win.f_name.get())
        ck("地址已填入", win.f_url.get() == "https://api.example.com/v1")
        ck("模型已填入", win.f_model.get() == "model-a")
        ck("搜索绑定指向 A",
           win.bind_vars["search"][0].get() == "供应商A",
           win.bind_vars["search"][0].get())
        ck("解释绑定指向 B",
           win.bind_vars["explain"][0].get() == "供应商B")
        ck("保存按钮初始禁用", str(win.btn_save["state"]) == "disabled")
        ck("提示词加载为搜索的", "搜索提示" in win.prompt_text.get("1.0", "end"))

        # ---------- 2 编辑后变脏 ----------
        print("\n2 编辑")
        win.f_model.delete(0, "end")
        win.f_model.insert(0, "model-a2")
        win._mark_dirty()
        ck("改动后保存按钮可用", str(win.btn_save["state"]) == "normal")
        win._save()
        ck("保存后按钮回到禁用", str(win.btn_save["state"]) == "disabled")
        saved = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
        m = [p for p in saved["providers"] if p["id"] == "a"][0]["model"]
        ck("模型名已持久化", m == "model-a2", m)

        # ---------- 3 Key 保存与显示切换 ----------
        print("\n3 API Key")
        win.f_key.delete(0, "end")
        win.f_key.insert(0, "sk-abc-123")
        win._mark_dirty()
        win._save()
        ck("Key 已写入 store", store.get_key("a") == "sk-abc-123")
        cfg_txt = open(os.path.join(tmp, "config.json"), encoding="utf-8").read()
        ck("Key 没写进 config.json", "sk-abc-123" not in cfg_txt)
        ck("Key 默认掩码", win.f_key.cget("show") == "•")
        win._toggle_key()
        ck("点击后明文显示", win.f_key.cget("show") == "")
        win._toggle_key()
        ck("再点回到掩码", win.f_key.cget("show") == "•")

        # ---------- 4 切换提示词页 ----------
        print("\n4 切换提示词页")
        win.prompt_action.set("translate")
        win._load_prompt()
        ck("切到翻译提示词", "翻译提示" in win.prompt_text.get("1.0", "end"),
           repr(win.prompt_text.get("1.0", "end"))[:40])
        win.prompt_action.set("explain")
        win._load_prompt()
        ck("切到解释提示词", "解释提示" in win.prompt_text.get("1.0", "end"))

        # ---------- 5 改提示词并保存 ----------
        print("\n5 修改提示词")
        win.prompt_action.set("search")
        win._load_prompt()
        win.prompt_text.delete("1.0", "end")
        win.prompt_text.insert("1.0", "新的搜索提示词")
        win._mark_dirty()
        win._save()
        b = store.binding("search")
        ck("提示词已更新", b["prompt"] == "新的搜索提示词", b["prompt"])

        # ---------- 6 新增供应商 ----------
        print("\n6 新增供应商")
        n_before = len(store.providers())
        win._add_provider()
        ck("供应商数量 +1", len(store.providers()) == n_before + 1)
        ck("新增后自动选中", win.current_id not in ("a", "b"), win.current_id)
        ck("新供应商模型为空", win.f_model.get() == "")
        ck("提示新供应商待填写",
           "新增" in win.status.cget("text"), win.status.cget("text"))

        # ---------- 7 绑定切换 ----------
        print("\n7 切换功能绑定")
        win._load_providers(select_id="a")
        var, cb = win.bind_vars["search"]
        var.set("供应商B")
        win._on_bind_change("search")
        win._mark_dirty()
        win._save()
        ck("搜索绑定改为 B", store.binding("search")["provider_id"] == "b",
           store.binding("search")["provider_id"])

        # ---------- 8 预设填充 ----------
        print("\n8 快速填充预设")
        win._load_providers(select_id="b")
        preset_names = [p["name"] for p in py_config.PRESETS]
        win.preset.set("DeepSeek")
        win._apply_preset()
        ck("地址被填成 DeepSeek",
           win.f_url.get() == "https://api.deepseek.com/v1", win.f_url.get())
        ck("模型被填成 deepseek-chat", win.f_model.get() == "deepseek-chat")
        ck("预设后标记为脏", str(win.btn_save["state"]) == "normal")
        win._mark_dirty()
        win._save()

        # ---------- 9 删除供应商 ----------
        print("\n9 删除供应商")
        # 删掉被 search 引用的 b，绑定应回落
        win._load_providers(select_id="b")
        ok, msg = store.remove_provider("b")
        ck("删除被引用的供应商成功", ok, msg)
        ck("搜索绑定已回落", store.binding("search")["provider_id"] != "b",
           store.binding("search")["provider_id"])

        # ---------- 10 不能删空 ----------
        print("\n10 保护：不能删到空")
        while len(store.providers()) > 1:
            store.remove_provider(store.providers()[-1]["id"])
        ok, msg = store.remove_provider(store.providers()[0]["id"])
        ck("拒绝删除最后一个", not ok, msg)

        # ---------- 11 测试连接（失败路径） ----------
        print("\n11 测试连接：不可达")
        win._load_providers()
        pid = store.providers()[0]["id"]
        store.upsert_provider({"id": pid, "name": "坏地址",
                               "base_url": "http://127.0.0.1:9/v1",
                               "model": "m", "timeout": 2})
        store.set_key(pid, "sk-x")
        store.save()
        win._load_providers(select_id=pid)
        win._test()
        for _ in range(80):
            win.root.update()
            if not win._test_running:
                break
            time.sleep(0.08)
        ck("测试结束且恢复按钮", not win._test_running
           and str(win.btn_test["state"]) == "normal")
        txt = win.test_label.cget("text")
        ck("显示出失败原因", txt.startswith("✗"), txt[:60])

        # ---------- 12 测试连接（成功路径） ----------
        print("\n12 测试连接：本地模拟服务")
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.dumps({
                    "choices": [{"message": {"content": "pong"}}]
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        store.upsert_provider({"id": pid, "name": "本地",
                               "base_url": f"http://127.0.0.1:{port}/v1",
                               "model": "m", "timeout": 5})
        store.save()
        win._load_providers(select_id=pid)
        win._test()
        for _ in range(100):
            win.root.update()
            if not win._test_running:
                break
            time.sleep(0.08)
        txt = win.test_label.cget("text")
        ck("成功时显示对勾", txt.startswith("✓"), txt[:60])
        ck("回复内容被带出", "pong" in txt, txt[:60])
        srv.shutdown()

        # ---------- 13 未保存提示 ----------
        print("\n13 未保存提示")
        win._mark_dirty()
        ck("脏状态可检测", win._dirty)
        # 直接验证 _on_close 的分支逻辑（不真弹窗）
        called = {"n": 0}
        orig_ask = py_settings.messagebox.askyesnocancel
        py_settings.messagebox.askyesnocancel = lambda *a, **k: (called.__setitem__("n", called["n"] + 1) or False)
        win._on_close()
        ck("有脏数据时会询问", called["n"] == 1)
        py_settings.messagebox.askyesnocancel = orig_ask

    finally:
        try:
            win.root.destroy()
        except Exception:
            pass
        py_config.config_dir = orig
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    fails = sum(1 for _, ok in results if not ok)
    print(f"{len(results)} 项，" + ("全部通过" if fails == 0 else f"{fails} 项失败"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
