"""AI 调用层：OpenAI 兼容的 /chat/completions。

只依赖标准库 urllib，不引入 requests。
这是刻意的：本机虽有 requests，但为零依赖目标保持一致，
且 urllib 的流式读取足以支撑逐字输出。

支持的返回形态：
  · 普通 JSON（stream=false）
  · SSE 流（stream=true，逐行 data: {...}）

错误分类（决定界面怎么提示）：
  · 未配置      -> 引导去设置
  · 网络不可达  -> 提示网络/地址
  · 401/403     -> 提示 Key 无效
  · 429         -> 提示限流
  · 其他 HTTP   -> 带状态码与响应体片段
  · 超时        -> 提示超时
"""

import json
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request


class AIError(Exception):
    """带可读原因的调用失败。"""

    def __init__(self, kind, message, detail=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or ""

    def __str__(self):
        return self.message


def build_messages(action, text, prompt, history=None):
    """构造请求消息。

    翻译类任务刻意不带额外解释，避免模型加寒暄。

    history: 追问用的多轮历史，形如 [("user", "..."), ("assistant", "...")]。
             注意顺序 —— 必须先有第一轮的 system+user，再插入历史，
             最后才是本次追问。不要把 history 放在最前面，否则模型会
             丢掉 system 提示词的约束。
    """
    if action == "translate":
        user = f"{text}"
    else:
        user = f"待处理文本：\n{text}"
    msgs = []
    if prompt.strip():
        msgs.append({"role": "system", "content": prompt.strip()})
    msgs.append({"role": "user", "content": user})
    for role, content in (history or []):
        if not content:
            continue
        msgs.append({"role": role, "content": content})
    return msgs


def _normalize_base(base_url):
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise AIError("config", "未填写接口地址")
    if not re.match(r"^https?://", b, re.I):
        raise AIError("config", f"接口地址必须以 http:// 或 https:// 开头：{b}")
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


def _headers(key):
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "PopDict/1.0",
    }
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _classify_http_error(code, body, url=""):
    snippet = (body or "")[:300].replace("\n", " ")
    # 502/503/504 经常是「本机代理在目标不可达时回的错误页」，
    # 不是模型服务本身的问题。直连模型服务几乎不会返回 502。
    # 这种情况下提示用户查本地代理，比说"服务端错误"有用得多。
    if code in (502, 503, 504):
        proxy_hint = ""
        if _is_local_or_proxied(url):
            proxy_hint = ("（本地代理返回的 {code}，通常是目标不可达或代理未启动，"
                          "请检查代理设置或关闭代理重试）").format(code=code)
        return AIError("network", f"网关错误（HTTP {code}）{proxy_hint}", snippet)
    if code in (401, 403):
        return AIError("auth", "API Key 无效或无权限", f"HTTP {code} {snippet}")
    if code == 404:
        return AIError("config", "接口地址不存在（404），请检查 base_url",
                       snippet)
    if code == 429:
        return AIError("rate", "请求被限流（429），稍后再试", snippet)
    if 500 <= code < 600:
        return AIError("server", f"服务端错误（HTTP {code}）", snippet)
    return AIError("http", f"请求失败（HTTP {code}）", snippet)


def _is_local_or_proxied(url):
    """判断目标是否本机地址，或环境里配置了代理。"""
    import os
    low = (url or "").lower()
    if "127.0.0.1" in low or "localhost" in low or "[::1]" in low:
        return True
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        if os.environ.get(var):
            return True
    return False


def _extract_error_message(body):
    """尽量从 OpenAI 风格错误体里取出人话。"""
    try:
        data = json.loads(body)
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or ""
        if isinstance(err, str):
            return err
        return data.get("message") or ""
    except (json.JSONDecodeError, AttributeError, TypeError):
        return ""


def complete(action, text, binding, stream=True, on_delta=None,
             cancel_event=None, history=None):
    """调用模型。返回完整文本。

    binding: py_config.ConfigStore.binding() 的返回值
    on_delta: 可选回调，收到增量文本时调用（用于逐字显示）
    cancel_event: 可选 threading.Event，置位则尽力中断
    history: 可选多轮历史，供追问使用
    """
    prov = binding.get("provider") or {}
    prompt = binding.get("prompt") or ""
    key = binding.get("key") or ""

    url = _normalize_base(prov.get("base_url"))
    model = (prov.get("model") or "").strip()
    if not model:
        raise AIError("config", "未填写模型名")
    if not key and not prov.get("no_key"):
        raise AIError("config", "未填写 API Key")

    timeout = prov.get("timeout") or 30
    try:
        timeout = float(timeout)
        if not (1 <= timeout <= 300):
            timeout = 30.0
    except (TypeError, ValueError):
        timeout = 30.0

    payload = {
        "model": model,
        "messages": build_messages(action, text, prompt, history=history),
        "temperature": prov.get("temperature", 0.3),
        "max_tokens": prov.get("max_tokens", 1024),
        "stream": bool(stream),
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(key),
                                 method="POST")

    ctx = None
    if url.lower().startswith("https"):
        ctx = ssl.create_default_context()

    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        msg = _extract_error_message(body)
        err = _classify_http_error(e.code, body, url)
        if msg:
            err.message = f"{err.message}：{msg}"
        raise err from None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.timeout):
            raise AIError("timeout", f"连接超时（{timeout:.0f}s）") from None
        if isinstance(reason, ssl.SSLError):
            raise AIError("tls", f"TLS 握手失败：{reason}") from None
        raise AIError("network", f"无法连接：{reason}") from None
    except socket.timeout:
        raise AIError("timeout", f"连接超时（{timeout:.0f}s）") from None

    try:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if stream and "text/event-stream" in ctype:
            out = _consume_sse(resp, on_delta, cancel_event)
        else:
            out = _consume_json(resp, on_delta)
    finally:
        try:
            resp.close()
        except Exception:
            pass

    if not out.strip():
        raise AIError("empty", "模型返回了空内容",
                      f"耗时 {int((time.time()-t0)*1000)}ms")
    return out


def _consume_json(resp, on_delta):
    raw = resp.read()
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise AIError("parse", "返回内容不是合法 JSON",
                      raw[:300].decode("utf-8", "replace")) from None
    choices = data.get("choices") or []
    if not choices:
        # 有些服务把错误放在 200 响应里
        msg = _extract_error_message(raw.decode("utf-8", "replace"))
        raise AIError("empty", msg or "返回中没有 choices", str(data)[:300])
    c0 = choices[0]
    msg = c0.get("message") or {}
    content = msg.get("content")
    if content is None:
        content = c0.get("text") or ""
    if on_delta and content:
        on_delta(content)
    return content or ""


def _consume_sse(resp, on_delta, cancel_event):
    """逐行读 SSE。兼容 data: / data: 与 [DONE]。"""
    parts = []
    for raw_line in resp:
        if cancel_event is not None and cancel_event.is_set():
            break
        line = raw_line.decode("utf-8", "replace").strip()
        if not line:
            continue
        if line.startswith(":"):
            continue                       # 注释/心跳
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece is None:
            # 有的实现在最后一帧给 message.content
            m = choices[0].get("message") or {}
            piece = m.get("content")
        if piece:
            parts.append(piece)
            if on_delta:
                on_delta(piece)
    return "".join(parts)


def ask_async(action, text, binding, on_delta=None, on_done=None,
              on_error=None, cancel_event=None, history=None):
    """在后台线程里跑一次请求。

    返回 (thread, cancel_event)。界面可置位 cancel_event 停止接收。
    传入 cancel_event 可以让调用方（如窗口关闭）中断接收。
    history 用于追问：把之前的问答按顺序带上。
    """
    if cancel_event is None:
        cancel_event = threading.Event()

    def worker():
        try:
            out = complete(action, text, binding,
                           stream=True, on_delta=on_delta,
                           cancel_event=cancel_event, history=history)
            if on_done and not cancel_event.is_set():
                on_done(out)
        except AIError as e:
            if on_error and not cancel_event.is_set():
                on_error(e)
        except Exception as e:                      # 兜底，避免线程静默死亡
            if on_error and not cancel_event.is_set():
                on_error(AIError("internal", f"内部错误：{type(e).__name__}",
                                 str(e)))

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    return th, cancel_event


def test_connection(binding):
    """连通性测试：发一条极短请求，返回 (ok, 说明)。"""
    try:
        out = complete("translate", "hello", binding, stream=False)
        return True, f"连接正常，回复：{out.strip()[:40]}"
    except AIError as e:
        return False, str(e)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import py_config
    s = py_config.store()
    for act in ("search", "translate", "explain"):
        ok, why = s.readiness(act)
        print(f"{act:10} {'就绪' if ok else why}")
        if ok:
            print("  测试连接 ...")
            ok2, msg = test_connection(s.binding(act))
            print(f"  {'PASS' if ok2 else 'FAIL'} {msg}")
