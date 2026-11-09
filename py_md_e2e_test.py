# -*- coding: utf-8 -*-
"""结果窗 Markdown 渲染端到端测试。

不靠肉眼：真实建窗、真实流式喂数据、真实收尾渲染，然后对 Text 控件
做程序化断言（文本内容 / tag 落位 / 切换按钮状态 / 幂等性）。
"""
import sys
import time
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8")

import py_pop
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


def pump(root, ms=400):
    """让主线程跑一会儿，等队列泵把动作刷完。"""
    end = time.time() + ms / 1000
    while time.time() < end:
        root.update()
        time.sleep(0.01)


# 一份贴近真实的 AI 回答（DeepSeek / GPT 常见输出形态）
SAMPLE = """### 关于「向量检索」的说明

向量检索（**Vector Search**）把文本映射为高维向量，再按*余弦相似度*找近邻。
它的核心优势在于能处理**语义相近但用词不同**的情况。

#### 关键步骤

1. 用 Embedding 模型把文档切块编码成向量
2. 建立索引（常用 `HNSW` 或 `IVF-Flat`）
3. 查询时编码问题向量，做近邻搜索
4. 取回 top-k 结果后重排（`rerank`）

> 注意：ANN 索引是近似搜索，召回率与延迟之间需要调参权衡。

| 索引类型 | 召回率 | 内存占用 | 适用规模 |
|---|---|---|---|
| Flat | 100% | 高 | < 10 万 |
| IVF | 95% | 中 | 百万级 |
| HNSW | 98% | 高 | 千万级 |

```python
import faiss
index = faiss.IndexHNSWFlat(768, 32)
index.add(vectors)
D, I = index.search(query, 5)
```

参考：[Faiss 文档](https://github.com/facebookresearch/faiss)

---

以上是主要区别，需要我展开某一部分吗？
"""


def test_render_in_window():
    print("\n[1] 建窗 + 流式 + 渲染")
    root = tk.Tk()
    root.withdraw()
    try:
        w = py_pop.ResultWindow(root, "AI 搜索", "向量检索是什么")
        pump(root, 200)

        # 模拟流式：分片喂入
        chunk = 24
        for i in range(0, len(SAMPLE), chunk):
            w.append(SAMPLE[i:i + chunk])
            pump(root, 20)
        pump(root, 300)

        w.finish(SAMPLE, elapsed_ms=1234)
        pump(root, 400)

        content = w.body.get("1.0", "end-1c")
        check("正文非空", len(content.strip()) > 100, f"len={len(content)}")
        check("标题已渲染", "关于「向量检索」的说明" in content)
        check("不含 ###", "###" not in content, content[:80])
        check("不含 ** 粗体标记", "**" not in content)
        check("不含单星斜体标记", "*余弦" not in content)
        check("粗体文字保留", "Vector Search" in content)
        check("行内代码去反引号", "HNSW" in content and "`HNSW`" not in content)
        check("有序列表编号", "1." in content and "2." in content)
        check("引用条", "▎" in content)
        check("表格竖线", "│" in content)
        check("表格内容", "百万级" in content)
        check("代码块围栏已去", "```" not in content)
        check("代码块内容", "IndexHNSWFlat" in content)
        check("分隔线", "─" in content)
        check("链接文字", "Faiss 文档" in content)
        check("链接 URL 不显示", "https://github.com/facebookresearch/faiss"
              not in content,
              "URL 不应出现在正文里")

        # tag 检查
        for t in ("h3", "h4", "bold", "italic", "code", "codeblock",
                  "li", "quote"):
            check(f"tag {t} 已应用",
                  bool(w.body.tag_ranges(md.TAG_PREFIX + t)))

        # 状态
        st = w.status_label.cget("text")
        check("状态显示完成", "完成" in st, st)
        check("状态含耗时", "1.2s" in st, st)
        check("状态含字数", f"{len(SAMPLE)} 字" in st, st)

        # 原文/渲染切换按钮
        check("切换按钮已启用", str(w.btn_md.cget("state")) == "normal")
        check("切换按钮初始文案", w.btn_md.cget("text") == "原文")

        # 切到原始 Markdown
        w._toggle_md()
        pump(root, 200)
        raw = w.body.get("1.0", "end-1c")
        check("原文模式含 ###", "###" in raw, raw[:60])
        check("原文模式含 **", "**" in raw)
        check("原文模式按钮变渲染", w.btn_md.cget("text") == "渲染")

        # 切回渲染
        w._toggle_md()
        pump(root, 200)
        again = w.body.get("1.0", "end-1c")
        check("切回渲染与首次一致", again == content)

        # 复制拿到的必须是原始 Markdown（不是渲染后的）
        w._copy()
        pump(root, 150)
        import py_clipguard as cg
        got = cg.read_text()
        check("复制的是原始 Markdown", got == SAMPLE,
              f"len={len(got)} vs {len(SAMPLE)}")

        w.close()
        pump(root, 150)
        check("关闭后 closed 置位", w.closed is True)
    finally:
        root.destroy()


def test_stream_then_render_short():
    print("\n[2] 短回答与无 Markdown 内容")
    root = tk.Tk()
    root.withdraw()
    try:
        # 无任何 Markdown 语法
        w = py_pop.ResultWindow(root, "翻译", "hello")
        pump(root, 150)
        for ch in "你好，世界":
            w.append(ch)
            pump(root, 12)
        w.finish("你好，世界")
        pump(root, 300)
        c = w.body.get("1.0", "end-1c")
        check("纯文本无标记原样显示", "你好，世界" in c, repr(c))
        check("纯文本无多余符号", c.strip() == "你好，世界", repr(c))
        w.close()
        pump(root, 100)
    finally:
        root.destroy()


def test_error_path():
    print("\n[3] 错误路径不被渲染逻辑污染")
    root = tk.Tk()
    root.withdraw()
    try:
        class E:
            kind = "config"
            message = "供应商未填写接口地址"
            detail = "base_url 为空"

        w = py_pop.ResultWindow(root, "AI 搜索", "x")
        pump(root, 150)
        w.show_error(E())
        pump(root, 300)
        c = w.body.get("1.0", "end-1c")
        check("错误信息显示", "供应商未填写接口地址" in c, repr(c))
        check("错误提示补充", "检查供应商配置" in c or "设置" in c, repr(c))
        check("错误态切换按钮仍禁用",
              str(w.btn_md.cget("state")) == "disabled")
        check("错误态状态为失败", "失败" in w.status_label.cget("text"))
        w.close()
        pump(root, 100)
    finally:
        root.destroy()


def test_render_fallback():
    print("\n[4] 渲染异常时回退纯文本")
    root = tk.Tk()
    root.withdraw()
    try:
        w = py_pop.ResultWindow(root, "AI 搜索", "x")
        pump(root, 150)
        # 故意让渲染器抛错
        orig = md.render
        md.render = lambda *a, **k: (_ for _ in ()).throw(ValueError("模拟渲染失败"))
        try:
            w._render_now("**粗体**内容")
        finally:
            md.render = orig
        pump(root, 200)
        c = w.body.get("1.0", "end-1c")
        check("回退后内容未丢失", "粗体" in c, repr(c))
        check("回退显示原始标记", "**" in c, repr(c))
        check("回退后状态提示失败",
              "渲染失败" in w.status_label.cget("text"),
              w.status_label.cget("text"))
        w.close()
        pump(root, 100)
    finally:
        root.destroy()


def test_many_chars():
    print("\n[5] 长回答性能与完整性")
    root = tk.Tk()
    root.withdraw()
    try:
        big = "\n\n".join([f"### 第 {i} 节\n\n这是第 **{i}** 节的内容，含 `code{i}`。"
                           for i in range(1, 41)])
        w = py_pop.ResultWindow(root, "AI 搜索", "长文")
        pump(root, 150)
        t0 = time.perf_counter()
        for i in range(0, len(big), 200):
            w.append(big[i:i + 200])
        pump(root, 200)
        w.finish(big)
        pump(root, 600)
        dt = (time.perf_counter() - t0) * 1000
        c = w.body.get("1.0", "end-1c")
        check("长文全部渲染", all(f"第 {i} 节" in c for i in range(1, 41)),
              f"缺失节次")
        check("长文无残留标记", "###" not in c and "`" not in c)
        check(f"长文耗时可接受（{dt:.0f}ms）", dt < 5000, f"{dt:.0f}ms")
        w.close()
        pump(root, 100)
    finally:
        root.destroy()


def main():
    test_render_in_window()
    test_stream_then_render_short()
    test_error_path()
    test_render_fallback()
    test_many_chars()

    print("\n" + "=" * 72)
    total = PASS + FAIL
    print(f"{total} 项检查，全部通过" if FAIL == 0
          else f"{total} 项检查，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
