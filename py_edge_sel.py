"""全自动验证：Edge 中建立真实 DOM 选区后，UIA TextPattern 能否读到。

无需人工操作。流程：
  1. 用 msedge --app 打开 sel-auto.html（页面 JS 自动建立 30 字符选区）
  2. 等页面完成选区建立（标题变为 SEL:30）
  3. 把 Edge 设为前台，让焦点落在文档上
  4. UIA 读取焦点元素 TextPattern 的 GetSelection
  5. 与预期文本比对

这一步的结论直接决定「浏览器场景能否零剪贴板取词」。
"""

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

import comtypes
import comtypes.client

UIA = comtypes.client.GetModule("UIAutomationCore.dll")
user32 = ctypes.WinDLL("user32.dll")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EXPECTED_PREFIX = "量子纠缠是量子力学中最引人注目的现象之一"


def enum_windows(pred):
    out = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if pred(hwnd, title):
            out.append((hwnd, title))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def read_selection_via_uia():
    """返回 (selected_text, info_dict)。"""
    comtypes.CoInitialize()
    try:
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elem = uia.GetFocusedElement()
        if not elem:
            return None, {"err": "无焦点元素"}

        info = {}
        for k, attr in (
            ("name", "CurrentName"),
            ("class", "CurrentClassName"),
            ("control_type", "CurrentControlType"),
        ):
            try:
                info[k] = getattr(elem, attr)
            except Exception:
                info[k] = "?"

        try:
            pat = elem.GetCurrentPattern(UIA.UIA_TextPatternId)
        except Exception as e:
            info["err"] = f"GetCurrentPattern {type(e).__name__}"
            return None, info

        if not pat:
            info["err"] = "无 TextPattern"
            return None, info

        tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
        try:
            doc = tp.DocumentRange
            info["doc_len"] = len(doc.GetText(-1))
        except Exception:
            info["doc_len"] = -1

        sel = tp.GetSelection()
        info["sel_count"] = sel.Length
        if sel.Length == 0:
            return None, info

        chunks = []
        for i in range(sel.Length):
            r = sel.GetElement(i)
            if r:
                chunks.append(r.GetText(-1))
        return "".join(chunks), info
    finally:
        comtypes.CoUninitialize()


def main():
    print("=" * 76)
    print("Edge 真实 DOM 选区 —— UIA 零剪贴板取词全自动验证")
    print("=" * 76)

    html_path = "sel-auto.html"
    import os
    import tempfile
    import uuid

    # 每次用唯一 profile，避免被上次残留的 Edge 实例锁住而无法新建窗口
    profile = os.path.join(tempfile.gettempdir(), f"uia_edge_{uuid.uuid4().hex[:8]}")
    abs_html = os.path.abspath(html_path).replace("\\", "/")
    args = [
        EDGE,
        f"--app=file:///{abs_html}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeSidebarV2",
        "--window-size=1000,700",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[1] Edge 已启动 pid={proc.pid}")

    # 等窗口出现且标题变成 SEL:30（页面 JS 已建立选区）
    hwnd = None
    title_seen = None
    for i in range(80):
        time.sleep(0.3)
        hits = enum_windows(lambda h, t: t.startswith("SEL:"))
        if hits:
            hwnd, title_seen = hits[0]
            break
    if not hwnd:
        print("[!] 未检测到已建立选区的 Edge 窗口")
        print("    尝试列出所有 Edge 窗口标题用于排查：")
        for h, t in enum_windows(lambda h, t: "Edge" in t or "UIA" in t)[:8]:
            print(f"    hwnd=0x{h:X} title={t[:70]!r}")
        proc.kill()
        return 1

    print(f"[2] 找到窗口 hwnd=0x{hwnd:X} title={title_seen!r}  （等待 {i*0.3:.1f}s）")

    # 激活窗口，使焦点落到文档
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    # 点击网页正文区域中心，确保焦点在文档而不是地址栏
    # 用 SetFocus 对 Chromium 无效，改为发送一次 Tab/点击效果不可靠，
    # 这里依赖 --app 模式启动时文档已持焦点
    fg = user32.GetForegroundWindow()
    print(f"[3] 前台窗口 hwnd=0x{fg:X} {'(匹配)' if fg == hwnd else '(不匹配)'}")
    time.sleep(0.8)

    print("[4] UIA 读取焦点元素选区 ...")
    text, info = read_selection_via_uia()
    print(f"    焦点: class={info.get('class')!r} "
          f"ControlType={info.get('control_type')} "
          f"doc_len={info.get('doc_len')} "
          f"sel_count={info.get('sel_count')}")
    if info.get("err"):
        print(f"    错误: {info['err']}")

    print("-" * 76)
    if text:
        print(f"✓ 读到选区文本 {len(text)} 字符：")
        print(f"  {text!r}")
        matched = EXPECTED_PREFIX in text or text in EXPECTED_PREFIX
        print(f"  与预期比对：{'匹配' if matched else '不匹配'}")
    else:
        matched = False
        print("✗ 未读到选区文本")

    print("\n" + "=" * 76)
    print("结论")
    print("=" * 76)
    if text and matched:
        print("✓ 浏览器正文选区可被 UIA TextPattern 零剪贴板读取。")
        print("  此前『Chromium 不暴露 UIA TextPattern』的判断被推翻，")
        print("  零剪贴板方案可以完整覆盖浏览器场景。")
    elif text:
        print("△ 能读到文本但内容不符，需检查选区范围。")
    else:
        print("✗ 仍未读到。若 doc_len>0 而 sel_count=0，说明")
        print("  TextPattern 存在但 Chromium 不通过 UIA 暴露 DOM 选区。")
    print("=" * 76)

    try:
        proc.kill()
    except Exception:
        pass
    return 0 if (text and matched) else 2


if __name__ == "__main__":
    sys.exit(main())
