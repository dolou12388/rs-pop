"""验证：主程序退出/被强杀时，剪贴板是否会被留在中间态。

这是 py_clip_check 观察到「基线文本消失、只剩 CF_LOCALE」的可疑成因。
如果成立，就是一个必须修的缺陷：用户关一次程序就把剪贴板清空了。

三次实验：
  E1  主程序空转，正常 terminate（模拟关窗）
  E2  主程序空转，taskkill /F 强杀
  E3  主程序正在事务中（人为放慢），强杀

每次实验前都把基线写回，实验后检查内容与格式。
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

import py_clipguard as cg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = cg.user32
kernel32 = cg.kernel32

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
MARKER = "EXITTEST_BASELINE_\u57fa\u7ebf_31415"


def snapshot():
    seq = cg.clipboard_seq()
    fmts = []
    if cg._open_clipboard():
        try:
            f = 0
            while True:
                f = user32.EnumClipboardFormats(f)
                if not f:
                    break
                h = user32.GetClipboardData(f)
                fmts.append((cg.format_name(f), kernel32.GlobalSize(h) if h else 0))
        finally:
            user32.CloseClipboard()
    return seq, cg.read_text(), fmts


def reset_baseline():
    cg.write_text(MARKER)
    time.sleep(0.15)
    return snapshot()


def show(tag, snap):
    seq, txt, fmts = snap
    desc = ", ".join(f"{n}({s}B)" for n, s in fmts) or "(无格式)"
    intact = txt == MARKER
    print(f"  [{tag}] seq={seq} 完整={'是' if intact else '否'} text={txt!r}")
    print(f"         格式: {desc}")
    return intact


def run_case(name, killer, hold_ms=0):
    print(f"\n{name}")
    base = reset_baseline()
    show("基线", base)

    env = dict(os.environ)
    env["POP_DEBUG"] = "1"
    if hold_ms:
        # 让事务里的人为等待变长，制造「事务进行中被杀」的窗口
        env["POP_TEST_HOLD_MS"] = str(hold_ms)

    p = subprocess.Popen(
        [PY, "-u", "py_pop.py"],
        cwd=HERE, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    time.sleep(2.2)   # 等 tkinter 起来、watcher 开始轮询
    print(f"  主程序 pid={p.pid} 已启动，准备终止")
    killer(p)
    time.sleep(1.2)
    if p.poll() is None:
        p.kill()
        time.sleep(0.4)

    after = snapshot()
    ok = show("事后", after)
    return ok


def main():
    print("=" * 74)
    print("主程序退出对剪贴板的影响")
    print("=" * 74)

    fails = 0

    def graceful(p):
        p.terminate()

    def forceful(p):
        p.kill()

    if not run_case("E1. 空转后正常终止 (terminate)", graceful):
        fails += 1

    if not run_case("E2. 空转后强杀 (kill -9)", forceful):
        fails += 1

    print("\n" + "=" * 74)
    print("结论: " + ("退出不影响剪贴板" if fails == 0
                     else f"{fails} 个场景下剪贴板被破坏"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
