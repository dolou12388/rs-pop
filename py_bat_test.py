# -*- coding: utf-8 -*-
"""校验 启动划词.bat 的可执行性。

这个测试存在的理由：bat 曾经因为两个纯二进制层面的原因启动失败，
而这两个问题在编辑器里完全看不出来：
  1. 换行是 LF 而不是 CRLF —— cmd 会把多行拼成一条命令，
     报 'tkinter，官方安装包默认包含）' is not recognized 这类诡异错误。
  2. 中文用 UTF-8 存 —— cmd 按系统 ANSI(936) 解码，中文变乱码并撕裂命令行。
所以必须按字节校验，不能只看文本。

关于「不在这里实机启动」：
py_pop.py 是常驻托盘程序，从 bat 拉起来会一直活着。早先尝试在这里
subprocess.run 等它结束，结果测试进程自己被 SIGTERM 掉 —— cmd /c
会把整棵进程树一起挂住。所以这一层改用「把启动程序那一行替换成
exit /b 0」的方式做 cmd 语法预检，只验证解析与探测逻辑。
真正的实机启动交给 py_single_test.py 与手动验收。
"""

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(BASE, "启动划词.bat")

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


def main():
    print("=" * 60)
    print("启动脚本可执行性校验")
    print("=" * 60)

    check("文件存在", os.path.isfile(BAT), BAT)
    if not os.path.isfile(BAT):
        return 1

    data = open(BAT, "rb").read()

    print("\n[A] 字节层面（这两项是曾经启动失败的真因）")
    check("无 UTF-8 BOM", not data.startswith(b"\xef\xbb\xbf"), data[:3])
    lf = data.count(b"\n")
    crlf = data.count(b"\r\n")
    check("换行全为 CRLF", lf == crlf, f"LF={lf} CRLF={crlf}")
    check("无孤立 CR", data.count(b"\r") == crlf,
          f"CR={data.count(bytes([13]))} CRLF={crlf}")

    try:
        text = data.decode("gbk")
        check("GBK 可解码（cmd 的 ANSI 代码页）", True)
    except UnicodeDecodeError as e:
        text = ""
        check("GBK 可解码（cmd 的 ANSI 代码页）", False, str(e))
        return 1

    check("中文内容正确", "正在启动" in text, text[:80])

    print("\n[B] 结构层面")
    lines = text.splitlines()
    check("首行 @echo off", lines[0].strip() == "@echo off", lines[0])
    check("设置了代码页", any("chcp" in l for l in lines))
    check("切到工作目录", any(l.strip().startswith("cd /d") for l in lines))
    check("探测回退到 py 启动器", "py -c" in text)
    check("探测回退到 python", "python -c" in text)
    check("调用了 py_pop.py", "py_pop.py" in text)
    check("探测失败有提示", "没有找到带 tkinter" in text)
    check("用 %~dp0 定位自身目录", "%~dp0" in text)

    cand = None
    for l in lines:
        s = l.strip()
        if s.startswith('set "CAND1='):
            cand = s.split("=", 1)[1].strip().rstrip('"')
            break
    check("找到解释器路径声明", cand is not None, cand)
    if cand:
        check("路径无 //", "//" not in cand, cand)
        check("路径无反斜杠连写", "\\\\" not in cand, cand)
        check("路径以 .exe 结尾", cand.lower().endswith(".exe"), cand)
        print(f"        路径: {cand}")

    print("\n[C] cmd 语法预检（不真正启动程序）")
    body = text.replace('"%PY%" -u py_pop.py', "exit /b 0")
    body = body.replace("pause", "rem pause")
    raw = body.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk")
    tmp = os.path.join(BASE, "_bat_probe.bat")
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        r = subprocess.run(["cmd", "/c", tmp], cwd=BASE,
                           stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=25)
        head = (r.stdout + r.stderr).decode("gbk", errors="replace")
        bad = ("is not recognized" in head
               or "此时不应有" in head
               or "命令语法不正确" in head)
        check("无 cmd 语法错误", not bad, head[:300])
        check("走到启动分支", "正在启动" in head, head[:200])
        check("输出了解释器路径",
              "解释器:" in head or "解释器：" in head, head[:200])
        check("退出码为 0", r.returncode == 0, r.returncode)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    print("\n" + "=" * 60)
    print(f"结果：{PASS} 通过 / {FAIL} 失败 / 共 {PASS + FAIL}")
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print("  -", f)
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
