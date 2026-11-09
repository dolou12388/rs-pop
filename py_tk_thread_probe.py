"""诊断：从非主线程调用 root.after 会发生什么。

如果确认会失败/抛异常，那么 ResultWindow 的跨线程更新机制
必须改成「队列 + 主线程轮询」，这是 tkinter 的硬性要求。
"""

import queue
import sys
import threading
import time
import tkinter as tk


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    root = tk.Tk()
    root.withdraw()

    print("=" * 70)
    print("tkinter 跨线程调用 root.after 的行为")
    print("=" * 70)

    # ---- A. 从后台线程直接调 root.after ----
    print("\nA. 后台线程调用 root.after(0, fn)")
    hits = []

    def from_thread():
        try:
            root.after(0, lambda: hits.append("after-called"))
            print("  root.after 调用未抛异常")
        except Exception as e:
            print(f"  抛异常: {type(e).__name__}: {e}")

    th = threading.Thread(target=from_thread)
    th.start()
    th.join()

    for _ in range(30):
        try:
            root.update()
        except Exception as e:
            print(f"  update 抛异常: {type(e).__name__}: {e}")
        time.sleep(0.01)

    print(f"  回调实际执行次数: {len(hits)}   {'成功' if hits else '失败（回调丢失）'}")

    # ---- B. 队列 + 主线程轮询 ----
    print("\nB. 队列 + 主线程轮询")
    q = queue.Queue()
    consumed = []

    def worker():
        for i in range(10):
            q.put(f"item{i}")
            time.sleep(0.005)

    def drain():
        while True:
            try:
                consumed.append(q.get_nowait())
            except queue.Empty:
                break
        root.after(0, drain) if False else None
        root.after(10, drain)

    root.after(10, drain)
    th2 = threading.Thread(target=worker)
    th2.start()
    th2.join()
    for _ in range(60):
        root.update()
        if len(consumed) >= 10:
            break
        time.sleep(0.01)
    print(f"  消费到 {len(consumed)} 条  {'成功' if len(consumed) == 10 else '失败'}")

    # ---- C. 后台线程直接改控件 ----
    print("\nC. 后台线程直接操作控件（应避免）")
    label = tk.Label(root, text="init")
    label.pack()
    errs = []

    def touch_widget():
        try:
            label.configure(text="from-thread")
        except Exception as e:
            errs.append(f"{type(e).__name__}: {e}")

    th3 = threading.Thread(target=touch_widget)
    th3.start()
    th3.join()
    if errs:
        print(f"  抛异常: {errs[0]}")
    else:
        print("  未抛异常（但仍有竞态风险，不可依赖）")

    root.destroy()
    print("\n结论：跨线程更新必须走「队列 + 主线程轮询」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
