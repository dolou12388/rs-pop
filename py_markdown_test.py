# -*- coding: utf-8 -*-
"""Markdown 解析与渲染测试。

分两层：
  A. 解析层（blocks / inline_runs / table_to_text）—— 不依赖 Tk，纯逻辑断言
  B. 渲染层 —— 真实 tkinter Text 控件，断言 tag 落位与文本内容
"""
import sys
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8")

import py_markdown as md

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


def eq(name, got, want):
    check(name, got == want, f"\n        got={got!r}\n        want={want!r}")


# ============ A. 解析层 ============

def test_inline():
    print("\n[A1] 行内语法")
    r = md.inline_runs("普通文本")
    eq("纯文本单段", r, [("普通文本", ())])

    r = md.inline_runs("这是**粗体**结束")
    eq("粗体切三段", [(t, g) for t, g in r],
       [("这是", ()), ("粗体", ("bold",)), ("结束", ())])

    r = md.inline_runs("a `code` b")
    eq("行内代码", [(t, g) for t, g in r],
       [("a ", ()), ("code", ("code",)), (" b", ())])

    r = md.inline_runs("**粗**和*斜*")
    eq("粗+斜", [(t, g) for t, g in r],
       [("粗", ("bold",)), ("和", ()), ("斜", ("italic",))])

    r = md.inline_runs("~~删~~")
    eq("删除线", [(t, g) for t, g in r], [("删", ("strike",))])

    # 行内代码内的 ** 不应被当作粗体
    r = md.inline_runs("`a**b**c`")
    eq("代码内星号不解析", [(t, g) for t, g in r], [("a**b**c", ("code",))])

    # 嵌套：粗体里带斜体
    r = md.inline_runs("**粗*斜*粗**")
    check("粗体内嵌套斜体", any("bold" in g and "italic" in g for _, g in r),
          repr(r))

    # 下划线不误伤英文标识符
    r = md.inline_runs("snake_case_name")
    eq("英文下划线不误判斜体", r, [("snake_case_name", ())])

    r = md.inline_runs("见[文档](https://example.com)")
    check("链接文本带 link 标记",
          any(t == "文档" and "link" in g for t, g in r), repr(r))
    check("链接 URL 被携带",
          any("__linkurl__" in g and "https://example.com" in g for t, g in r),
          repr(r))
    check("链接占位符为不可见字符",
          all(t == "\u200b" for t, g in r if "__linkurl__" in g), repr(r))

    # 未闭合的标记应原样保留，不能吞字符
    r = md.inline_runs("未闭合 **粗体")
    joined = "".join(t for t, _ in r)
    eq("未闭合标记不丢字符", joined, "未闭合 **粗体")


def test_blocks():
    print("\n[A2] 块级语法")
    b = md.blocks("# 标题一\n正文")
    eq("标题层级", b[0]["kind"], "h1")
    eq("标题内容", b[0]["runs"], [("标题一", ())])
    eq("后续段落", b[1]["kind"], "p")

    b = md.blocks("### 三级")
    eq("h3", b[0]["kind"], "h3")

    b = md.blocks("```python\nprint(1)\nprint(2)\n```")
    eq("代码块类型", b[0]["kind"], "code")
    eq("代码块语言", b[0]["lang"], "python")
    eq("代码块内容", b[0]["text"], "print(1)\nprint(2)")

    b = md.blocks("```\n未闭合")
    eq("未闭合代码块容错", b[0]["kind"], "code")
    eq("未闭合也能取到内容", b[0]["text"], "未闭合")

    b = md.blocks("---")
    eq("分隔线", b[0]["kind"], "hr")

    b = md.blocks("> 引用一\n> 引用二")
    eq("引用类型", b[0]["kind"], "quote")
    eq("多行引用合并", "".join(t for t, _ in b[0]["runs"]), "引用一 引用二")

    b = md.blocks("- 甲\n- 乙\n- 丙")
    eq("列表类型", b[0]["kind"], "list")
    eq("无序列表项数", len(b[0]["items"]), 3)
    eq("列表项内容", b[0]["items"][0], ("", "甲"))

    b = md.blocks("1. 第一\n2. 第二")
    eq("有序列表标记", b[0]["items"][0], ("1.", "第一"))
    eq("有序列表第二项", b[0]["items"][1], ("2.", "第二"))

    b = md.blocks("| 名字 | 值 |\n|---|---|\n| 甲 | 1 |\n| 乙 | 22 |")
    eq("表格类型", b[0]["kind"], "table")
    eq("表头", b[0]["head"], ["名字", "值"])
    eq("行数", len(b[0]["rows"]), 2)
    eq("第二行", b[0]["rows"][1], ["乙", "22"])

    # 段落跨行应合并
    b = md.blocks("第一行\n第二行\n\n新段")
    eq("连续行合并为一段", b[0]["kind"], "p")
    eq("合并内容", "".join(t for t, _ in b[0]["runs"]), "第一行 第二行")
    eq("空行分段", b[1]["kind"], "p")

    # 混合文档不能漏块
    doc = "# T\n\n段落\n\n- a\n- b\n\n```\ncode\n```\n\n> q\n\n| h |\n|---|\n| v |"
    b = md.blocks(doc)
    eq("混合文档块数", len(b), 6)
    eq("混合顺序", [x["kind"] for x in b],
       ["h1", "p", "list", "code", "quote", "table"])


def test_table_width():
    print("\n[A3] 表格对齐（CJK 宽度）")
    eq("ASCII 宽度", md._dwidth("abc"), 3)
    eq("中文宽度", md._dwidth("中文"), 4)
    eq("混排宽度", md._dwidth("a中"), 3)

    txt = md.table_to_text(["名", "值"], [["甲", "1"], ["乙乙", "22"]])
    lines = txt.split("\n")
    eq("表格行数", len(lines), 4)  # 表头 + 分隔 + 2 行
    # 对齐必须按**显示宽度**比较，不能按字符索引：
    # "乙乙" 只占 2 个字符串位置，但在等宽字体下是 4 列宽，
    # 用 index 判断会把正确的输出误判为错位（测试逻辑自身的坑）。
    def bar_display_cols(ln):
        cols, acc = [], 0
        for ch in ln:
            if ch == "│":
                cols.append(acc)
            acc += md._dwidth(ch)
        return cols

    data_lines = [lines[0]] + lines[2:]
    cols = [bar_display_cols(ln) for ln in data_lines]
    check("每行列数一致", len(set(len(c) for c in cols)) == 1, repr(cols))
    check("所有行竖线按显示宽度对齐", all(c == cols[0] for c in cols),
          f"{cols} 原文={data_lines}")
    check("分隔行用 ┼", "┼" in lines[1], repr(lines[1]))

    # 列数不足的行要补齐，不能抛异常
    txt = md.table_to_text(["a", "b", "c"], [["1"]])
    check("缺列补空不报错", txt.count("│") >= 2, repr(txt))


def test_edge():
    print("\n[A4] 边界与容错")
    eq("空字符串", md.blocks(""), [])
    eq("仅空白", md.blocks("\n\n  \n"), [])
    check("仅分隔线", md.blocks("***")[0]["kind"] == "hr")

    # 大量嵌套标记不应崩
    s = "**" * 50 + "x" + "**" * 50
    try:
        md.blocks(s)
        check("极端嵌套不崩", True)
    except Exception as e:
        check("极端嵌套不崩", False, repr(e))

    # CRLF
    b = md.blocks("# T\r\n\r\n正文")
    eq("CRLF 归一", [x["kind"] for x in b], ["h1", "p"])

    # 井号但无空格，不是标题
    b = md.blocks("#不是标题")
    eq("无空格不算标题", b[0]["kind"], "p")

    # 表格分隔行缺列
    b = md.blocks("| a | b |\n|---|---|")
    eq("只有表头无数据行", (b[0]["kind"], len(b[0]["rows"])), ("table", 0))


# ============ B. 渲染层 ============

def test_render():
    print("\n[B1] 渲染到 Text")
    root = tk.Tk()
    root.withdraw()
    tw = tk.Text(root)
    try:
        sample = (
            "# 一级标题\n\n"
            "这是**粗体**和*斜体*，还有`code`。\n\n"
            "- 项目一\n- 项目二\n\n"
            "1. 第一\n2. 第二\n\n"
            "> 一段引用\n\n"
            "```python\nprint('hi')\n```\n\n"
            "| 名 | 值 |\n|---|---|\n| 甲 | 1 |\n\n"
            "---\n"
        )
        md.render(tw, sample)
        content = tw.get("1.0", "end-1c")

        check("标题文本存在", "一级标题" in content, repr(content[:60]))
        check("粗体文本存在", "粗体" in content)
        check("不含粗体标记", "**" not in content, repr(content))
        check("行内代码文本存在", "code" in content)
        check("反引号已去除", "`code`" not in content)
        check("列表符号存在", "•" in content)
        check("有序列表编号存在", "1." in content)
        check("引用条存在", "▎" in content)
        check("代码块内容存在", "print('hi')" in content)
        check("代码块围栏已去除", "```" not in content)
        check("表格内容存在", "甲" in content)
        check("表格竖线存在", "│" in content)
        check("分隔线存在", "─" in content)

        # tag 落位
        check("h1 tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "h1")))
        check("bold tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "bold")))
        check("italic tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "italic")))
        check("code tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "code")))
        check("codeblock tag 已用",
              bool(tw.tag_ranges(md.TAG_PREFIX + "codeblock")))
        check("li tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "li")))
        check("quote tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "quote")))
        check("table tag 已用", bool(tw.tag_ranges(md.TAG_PREFIX + "table")))
        # 表格与代码块必须用不同 tag：两者字体相同但间距要求不同，
        # 早期版本共用 codeblock 导致表格紧贴上一段文字。
        check("表格与代码块 tag 分离",
              tw.tag_ranges(md.TAG_PREFIX + "table")
              != tw.tag_ranges(md.TAG_PREFIX + "codeblock"))

        # 粗体 tag 必须精确覆盖"粗体"两字，不多不少
        target = tw.search("粗体", "1.0", "end")
        hit = False
        for a, b in zip(tw.tag_ranges(md.TAG_PREFIX + "bold")[::2],
                        tw.tag_ranges(md.TAG_PREFIX + "bold")[1::2]):
            if str(a) == target and str(b) == f"{int(str(a).split('.')[0])}.{int(str(a).split('.')[1]) + 2}":
                hit = True
        check("bold tag 落位精确", hit,
              f"target={target} ranges={tw.tag_ranges(md.TAG_PREFIX + 'bold')}")

        # 斜体 tag 必须精确覆盖"斜体"两字
        t2 = tw.search("斜体", "1.0", "end")
        hit2 = False
        for a, b in zip(tw.tag_ranges(md.TAG_PREFIX + "italic")[::2],
                        tw.tag_ranges(md.TAG_PREFIX + "italic")[1::2]):
            if str(a) == t2:
                hit2 = True
        check("italic tag 落位精确", hit2,
              f"target={t2} ranges={tw.tag_ranges(md.TAG_PREFIX + 'italic')}")

        # 重复渲染必须幂等（不追加、不残留）
        md.render(tw, sample)
        content2 = tw.get("1.0", "end-1c")
        eq("重复渲染幂等", content2, content)

        # 渲染空文本
        md.render(tw, "")
        eq("空文本清空", tw.get("1.0", "end-1c"), "")

        # 渲染纯文本
        md.render(tw, "只有一行普通文字")
        eq("纯文本原样输出", tw.get("1.0", "end-1c"), "只有一行普通文字\n")

    finally:
        root.destroy()


def test_render_errors():
    print("\n[B2] 渲染容错")
    root = tk.Tk()
    root.withdraw()
    tw = tk.Text(root)
    try:
        cases = [
            ("", "空"),
            ("\n\n\n", "全空行"),
            ("# " , "空标题"),
            ("```", "只有围栏"),
            ("|", "只有竖线"),
            ("- ", "空列表项"),
            ("> ", "空引用"),
            ("**" * 100, "巨量标记"),
            ("a" * 5000, "超长单行"),
        ]
        for text, label in cases:
            try:
                md.render(tw, text)
                check(f"容错：{label}", True)
            except Exception as e:
                check(f"容错：{label}", False, f"{type(e).__name__}: {e}")
    finally:
        root.destroy()


def main():
    test_inline()
    test_blocks()
    test_table_width()
    test_edge()
    test_render()
    test_render_errors()

    print("\n" + "=" * 72)
    total = PASS + FAIL
    if FAIL == 0:
        print(f"{total} 项检查，全部通过")
    else:
        print(f"{total} 项检查，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
