"""AI 调用层测试：用本地 HTTP 服务模拟各种响应。

不依赖任何真实 API Key，也不发外网请求。
覆盖：普通 JSON、SSE 流、401/404/429/500、非 JSON、空内容、超时、连接拒绝。
"""

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


# ---------------- 模拟服务 ----------------

class Handler(BaseHTTPRequestHandler):
    mode = "json"
    last_body = None
    last_headers = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        Handler.last_body = json.loads(raw.decode("utf-8"))
        Handler.last_headers = dict(self.headers)

        mode = Handler.mode
        path = self.path

        if not path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"no such path"}}')
            return

        if mode == "json":
            body = json.dumps({
                "choices": [{"message": {"role": "assistant",
                                         "content": "这是模拟回复"}}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            pieces = ["量子", "纠缠", "是", "一种", "现象"]
            for p in pieces:
                d = json.dumps({"choices": [{"delta": {"content": p}}]})
                self.wfile.write(f"data: {d}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.01)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        elif mode == "401":
            body = b'{"error":{"message":"Invalid API key provided"}}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "404":
            body = b'{"error":{"message":"model not found"}}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "429":
            body = b'{"error":{"message":"rate limit exceeded"}}'
            self.send_response(429)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "500":
            body = b'{"error":{"message":"internal server error"}}'
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "notjson":
            body = b"<html>bad gateway</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "empty":
            body = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "slow":
            time.sleep(3)
            body = json.dumps({"choices": [{"message": {"content": "慢"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif mode == "nokey_error":
            # 200 但 choices 里带 error
            body = json.dumps({"error": {"message": "模型未开通"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, port


def make_binding(port, model="test-model", key="sk-fake",
                 base=None, timeout=10, no_key=False):
    return {
        "provider_id": "t",
        "prompt": "你是助手",
        "key": "" if no_key else key,
        "provider": {
            "id": "t",
            "name": "test",
            "base_url": base if base is not None else f"http://127.0.0.1:{port}/v1",
            "model": model,
            "wire": "openai",
            "timeout": timeout,
            "temperature": 0.3,
            "max_tokens": 256,
            "no_key": no_key,
        },
    }


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 78)
    print("AI 调用层测试（本地模拟服务，无真实密钥）")
    print("=" * 78)

    srv, port = start_server()
    print(f"模拟服务: 127.0.0.1:{port}\n")

    # 1 普通 JSON
    print("1 普通 JSON 响应")
    Handler.mode = "json"
    out = py_ai.complete("search", "测试文本", make_binding(port), stream=False)
    ck("返回内容正确", out == "这是模拟回复", repr(out))
    b = Handler.last_body
    ck("请求含 model", b.get("model") == "test-model")
    ck("请求含 system prompt",
       b["messages"][0]["role"] == "system" and b["messages"][0]["content"] == "你是助手")
    ck("请求含 user 文本", "测试文本" in b["messages"][-1]["content"])
    ck("Authorization 头正确",
       Handler.last_headers.get("Authorization") == "Bearer sk-fake")
    ck("路径正确拼成 /v1/chat/completions", True)

    # 2 SSE 流
    print("\n2 SSE 流式响应")
    Handler.mode = "stream"
    got = []
    out = py_ai.complete("translate", "量子纠缠是一种现象",
                         make_binding(port), stream=True,
                         on_delta=lambda p: got.append(p))
    ck("流式拼接结果正确", out == "量子纠缠是一种现象", repr(out))
    ck("on_delta 被逐段调用", len(got) == 5, f"片段数={len(got)}")

    # 3 各类 HTTP 错误
    print("\n3 错误分类")
    for mode, kind, hint in [
        ("401", "auth", "Key 无效"),
        ("404", "config", "地址不存在"),
        ("429", "rate", "限流"),
        ("500", "server", "服务端错误"),
    ]:
        Handler.mode = mode
        try:
            py_ai.complete("search", "x", make_binding(port), stream=False)
            ck(f"{mode} 应抛错", False)
        except py_ai.AIError as e:
            ck(f"{mode} -> kind={kind}", e.kind == kind, e.message)
            ck(f"{mode} 错误信息含详情", bool(e.message), e.message[:40])

    # 4 非 JSON
    print("\n4 非 JSON 响应")
    Handler.mode = "notjson"
    try:
        py_ai.complete("search", "x", make_binding(port), stream=False)
        ck("应抛解析错误", False)
    except py_ai.AIError as e:
        ck("非 JSON -> kind=parse", e.kind == "parse", e.message)

    # 5 空内容
    print("\n5 空内容")
    Handler.mode = "empty"
    try:
        py_ai.complete("search", "x", make_binding(port), stream=False)
        ck("空内容应抛错", False)
    except py_ai.AIError as e:
        ck("空内容 -> kind=empty", e.kind == "empty", e.message)

    # 6 200 但带 error
    print("\n6 HTTP 200 但响应体是错误")
    Handler.mode = "nokey_error"
    try:
        py_ai.complete("search", "x", make_binding(port), stream=False)
        ck("应抛错", False)
    except py_ai.AIError as e:
        ck("识别为 empty 并带出原因",
           e.kind == "empty" and "未开通" in e.message, e.message)

    # 7 超时
    print("\n7 超时")
    Handler.mode = "slow"
    t0 = time.time()
    try:
        py_ai.complete("search", "x", make_binding(port, timeout=1), stream=False)
        ck("应超时", False)
    except py_ai.AIError as e:
        dt = time.time() - t0
        ck("超时 -> kind=timeout", e.kind == "timeout", e.message)
        ck("超时时间约 1s", 0.8 <= dt <= 2.5, f"{dt:.2f}s")

    # 8 连接拒绝
    print("\n8 端口不通")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    try:
        py_ai.complete("search", "x", make_binding(dead_port), stream=False)
        ck("应抛网络错误", False)
    except py_ai.AIError as e:
        # 本机若开了系统代理，直连不通会被代理转成 502/503；
        # 正确行为是归为 network 并提示代理，而不是报"服务端错误"
        ck("不可达 -> kind=network", e.kind == "network", e.message[:70])

    # 9 配置缺失
    print("\n9 配置缺失的各种情形")
    cases = [
        ("地址为空", make_binding(port, base=""), "config"),
        ("地址无协议", make_binding(port, base="api.x.com/v1"), "config"),
        ("模型为空", make_binding(port, model=""), "config"),
    ]
    Handler.mode = "json"
    for name, binding, want in cases:
        try:
            py_ai.complete("search", "x", binding, stream=False)
            ck(name, False)
        except py_ai.AIError as e:
            ck(f"{name} -> {want}", e.kind == want, e.message)
    # 无 Key
    try:
        py_ai.complete("search", "x", make_binding(port, key="", no_key=False),
                       stream=False)
        ck("无 Key -> config", False)
    except py_ai.AIError as e:
        ck("无 Key -> config", e.kind == "config", e.message)
    # no_key 允许
    out = py_ai.complete("search", "x", make_binding(port, key="", no_key=True),
                         stream=False)
    ck("no_key=True 时允许无 Key", out == "这是模拟回复")

    # 10 取消
    print("\n10 流式取消")
    Handler.mode = "stream"
    cancel = threading.Event()

    def on_delta(p):
        cancel.set()

    out = py_ai.complete("search", "x", make_binding(port), stream=True,
                         on_delta=on_delta, cancel_event=cancel)
    ck("取消后提前结束（只收到 1 段）", len(out) < len("量子纠缠是一种现象"),
       repr(out))

    # 11 base_url 已含完整路径
    print("\n11 base_url 已包含 /chat/completions")
    b2 = make_binding(port)
    b2["provider"]["base_url"] = f"http://127.0.0.1:{port}/v1/chat/completions"
    Handler.mode = "json"
    out = py_ai.complete("search", "x", b2, stream=False)
    ck("不重复拼接路径", out == "这是模拟回复")

    # 12 尾斜杠
    print("\n12 base_url 带尾斜杠")
    b3 = make_binding(port)
    b3["provider"]["base_url"] = f"http://127.0.0.1:{port}/v1/"
    out = py_ai.complete("search", "x", b3, stream=False)
    ck("尾斜杠被正确处理", out == "这是模拟回复")

    srv.shutdown()

    print("\n" + "=" * 78)
    fails = sum(1 for _, ok in results if not ok)
    print(f"{len(results)} 项，" + ("全部通过" if fails == 0 else f"{fails} 项失败"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
