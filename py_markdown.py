# -*- coding: utf-8 -*-
"""Markdown 渲染：把 AI 返回的 Markdown 文本渲染进 tkinter Text 控件。

设计取舍：
  1. **解析与渲染分离**。`blocks()` 只产出结构化块，不碰控件，因此可以脱离
     Tk 做单测（`py_markdown_test.py`）。
  2. **流式期间追加纯文本，收尾时整体重渲染**。流式时逐字插入 Markdown 语法
     字符会闪出一堆 `**` 和 `#`，体验更差；而每来一片就全量重解析又浪费。
     所以流式只追原文，`finish()` 时一次性渲染成富文本。
  3. 只实现 AI 回答里真正会出现的语法，不追求完整 CommonMark：
     标题 / 粗体 / 斜体 / 删除线 / 行内代码 / 围栏代码块 / 引用 /
     有序无序列表 / 分隔线 / 链接 / 表格。
  4. 表格用等宽字体按**显示宽度**对齐（CJK 字符按 2 列算），
     否则中文表格在 Tk 里必然错位。
"""

import re
import unicodedata

import py_dpi
import py_theme

# ---------- 配色 ----------

DARK = {
    "bg": "#26262A",
    "fg": "#D8D8DC",
    "head": "#F2F2F6",
    "muted": "#8E8E96",
    "accent": "#7FA8F0",
    "code_bg": "#33333A",
    "code_fg": "#E2C98C",
    "rule": "#3C3C44",
    "quote_fg": "#A8A8B2",
    # 代码高亮配色：比单一色更容易扫读，也显得更精细
    "kw": "#C792EA",
    "str": "#C3E88D",
    "num": "#F78C6C",
    "com": "#6A737D",
    "fn": "#82AAFF",
}

LIGHT = {
    "bg": "#FFFEFB", "fg": "#202723", "head": "#1D3025",
    "muted": "#67736C", "accent": "#2F7058", "code_bg": "#F0EFE9",
    "code_fg": "#4D493A", "rule": "#D9D8D1",
    "quote_fg": "#52665A", "kw": "#78499B", "str": "#356E36",
    "num": "#A64D31", "com": "#738078", "fn": "#256B86",
}


def active_palette():
    return LIGHT if py_theme.effective_mode() == "light" else DARK

# 字体族在运行时按系统实际情况挑选（见 py_markdown.init_fonts）
BASE_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"
BASE_SIZE = 10
MONO_SIZE = 10

TAG_PREFIX = "md_"

# 是否已通过 init_fonts 校准过字体族
_fonts_ready = False


def init_fonts(root):
    """在系统真实字体里挑可用的族，并按 DPI 校准字号。

    必须显式挑族：Consolas 在部分精简系统上不存在，直接写死会
    让 Tk 静默回退到比例字体，代码块与表格立刻错位。
    """
    global BASE_FAMILY, MONO_FAMILY, _fonts_ready
    BASE_FAMILY = py_dpi.pick_family(root, py_dpi.UI_FAMILIES)
    MONO_FAMILY = py_dpi.pick_family(root, py_dpi.MONO_FAMILIES)
    _fonts_ready = True
    return BASE_FAMILY, MONO_FAMILY


def base_font(size=None, bold=False, italic=False):
    """正文字体元组。

    size 默认取模块级 BASE_SIZE，且是**调用时**才解析 ——
    若写成 size=BASE_SIZE 作默认参数，值会在函数定义时就绑死，
    之后调整 BASE_SIZE（Ctrl+滚轮缩放）对不传参的调用完全无效。
    """
    if size is None:
        size = BASE_SIZE
    style = []
    if bold:
        style.append("bold")
    if italic:
        style.append("italic")
    return (BASE_FAMILY, py_dpi.font_size(size)) + tuple(style)


def mono_font(size=None, bold=False):
    if size is None:
        size = MONO_SIZE
    style = ("bold",) if bold else ()
    return (MONO_FAMILY, py_dpi.font_size(size)) + style


# ---------- 行内语法 ----------

# 用**手写扫描器**而不是一个总正则，原因是实测正则版本会互相抢字符：
#   "**粗**和*斜*" 里粗体的收尾 ** 与斜体的起始 * 相邻，
#   单一正则的交替分支匹配到 "**" 后会把 "*斜*" 整段残留成纯文本。
# 手写扫描按"遇到标记就先找同类收尾"的顺序推进，边界清楚得多。
_MARKERS = ("**", "__", "~~", "`", "*", "_", "[")


def _find_close(s, start, marker):
    """从 start 起找与 marker 配对的收尾位置，找不到返回 -1。"""
    i = start
    n = len(s)
    while i < n:
        if s.startswith(marker, i):
            # 空内容不算（如 ** 紧跟 **）
            if i > start:
                return i
        i += 1
    return -1


def _ascii_word(ch):
    """是否为"词内字符"。

    坑：`'和'.isalnum()` 在 Python 里是 True，中文也算 alnum。
    若直接用 isalnum 判断，中文后面的 *斜体* 会被误认为词内而拒绝解析
    （实测：`'和*斜*'` 解析失败，`'*斜*'` 成功）。
    这里只把 ASCII 字母数字视作词内字符。
    """
    return ch.isascii() and ch.isalnum()


def inline_runs(s, extra=()):
    """把一行文本切成 [(text, tags)]，tags 为元组，可嵌套继承。"""
    runs = []
    buf = []
    i = 0
    n = len(s)

    def flush():
        if buf:
            runs.append(("".join(buf), tuple(extra)))
            buf.clear()

    while i < n:
        ch = s[i]

        # 行内代码最优先：内部一律不再解析
        if ch == "`":
            j = s.find("`", i + 1)
            if j > i + 1:
                flush()
                runs.append((s[i + 1:j], tuple(extra) + ("code",)))
                i = j + 1
                continue

        # 链接 [文本](url)
        if ch == "[":
            rb = s.find("]", i + 1)
            if rb > i and s.startswith("(", rb + 1):
                rp = s.find(")", rb + 2)
                if rp > rb:
                    url = s[rb + 2:rp].strip()
                    flush()
                    inner = inline_runs(s[i + 1:rb], tuple(extra))
                    for it, ig in inner:
                        runs.append((it, ig + ("link",)))
                    runs.append(("\u200b", tuple(extra) + ("__linkurl__", url)))
                    i = rp + 1
                    continue

        # 成对标记
        matched = False
        for mk in ("**", "__", "~~"):
            if s.startswith(mk, i):
                j = _find_close(s, i + len(mk), mk)
                if j != -1:
                    flush()
                    tag = "bold" if mk in ("**", "__") else "strike"
                    runs.extend(inline_runs(s[i + len(mk):j],
                                            tuple(extra) + (tag,)))
                    i = j + len(mk)
                    matched = True
                    break
        if matched:
            continue

        # 单个 * / _ 作斜体，要求两侧不是空白或标识符字符
        if ch in "*_":
            if i + 1 < n and not s[i + 1].isspace():
                prev_ok = (i == 0) or (not _ascii_word(s[i - 1]))
                if prev_ok:
                    j = _find_close(s, i + 1, ch)
                    # 收尾后不能紧跟标识符字符（避免 snake_case 误判）
                    if j != -1 and (j + 1 >= n or not _ascii_word(s[j + 1])):
                        flush()
                        runs.extend(inline_runs(s[i + 1:j],
                                                tuple(extra) + ("italic",)))
                        i = j + 1
                        continue

        buf.append(ch)
        i += 1

    flush()
    return runs


# ---------- 块级语法 ----------

_RE_FENCE = re.compile(r"^\s*```+\s*([\w+#.-]*)\s*$")
_RE_FENCE_END = re.compile(r"^\s*```+\s*$")
_RE_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_HR = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")
_RE_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_RE_ULI = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_RE_OLI = re.compile(r"^(\s*)(\d{1,9})[.)]\s+(.*)$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _is_block_start(line, lines, idx):
    if not line.strip():
        return True
    if _RE_FENCE.match(line) or _RE_HEAD.match(line) or _RE_HR.match(line):
        return True
    if _RE_QUOTE.match(line) or _RE_ULI.match(line) or _RE_OLI.match(line):
        return True
    if "|" in line and idx + 1 < len(lines) and _RE_TABLE_SEP.match(lines[idx + 1]):
        return True
    return False


def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def blocks(text):
    """把 Markdown 文本解析成块列表。每块是 dict，kind 决定渲染方式。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        # 围栏代码块
        m = _RE_FENCE.match(line)
        if m:
            lang = m.group(1)
            i += 1
            body = []
            while i < n and not _RE_FENCE_END.match(lines[i]):
                body.append(lines[i])
                i += 1
            if i < n:
                i += 1  # 吃掉结束围栏；未闭合时也不报错，容错优先
            out.append({"kind": "code", "lang": lang,
                        "text": "\n".join(body)})
            continue

        # 标题
        m = _RE_HEAD.match(line)
        if m:
            out.append({"kind": "h%d" % len(m.group(1)),
                        "runs": inline_runs(m.group(2).strip())})
            i += 1
            continue

        # 分隔线
        if _RE_HR.match(line):
            out.append({"kind": "hr"})
            i += 1
            continue

        # 表格：本行含 | 且下一行是分隔行
        if "|" in line and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
            head = _split_row(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            out.append({"kind": "table", "head": head, "rows": rows})
            continue

        # 引用（连续多行合并）
        if _RE_QUOTE.match(line):
            buf = []
            while i < n and _RE_QUOTE.match(lines[i]):
                buf.append(_RE_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append({"kind": "quote",
                        "runs": inline_runs(" ".join(buf).strip())})
            continue

        # 列表（连续列表项归为一组）
        if _RE_ULI.match(line) or _RE_OLI.match(line):
            items = []
            while i < n:
                mu = _RE_ULI.match(lines[i])
                mo = _RE_OLI.match(lines[i])
                if mu:
                    items.append(("", mu.group(2)))
                elif mo:
                    items.append((mo.group(2) + ".", mo.group(3)))
                else:
                    break
                i += 1
            out.append({"kind": "list", "items": items})
            continue

        # 普通段落：聚合到下一个块起点
        buf = [line]
        i += 1
        while i < n and not _is_block_start(lines[i], lines, i):
            buf.append(lines[i])
            i += 1
        out.append({"kind": "p", "runs": inline_runs(" ".join(
            x.strip() for x in buf).strip())})

    return out


# ---------- 表格对齐 ----------

def _dwidth(s):
    """显示宽度：CJK 全角按 2 列算，否则等宽字体下中文表格必然错位。"""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _pad(s, width):
    return s + " " * max(0, width - _dwidth(s))


def table_to_text(head, rows):
    """把表格转成按显示宽度对齐的等宽文本。"""
    cols = len(head)
    norm = []
    for r in rows:
        r = list(r[:cols]) + [""] * (cols - len(r))
        norm.append(r)
    widths = []
    for c in range(cols):
        w = _dwidth(head[c])
        for r in norm:
            w = max(w, _dwidth(r[c]))
        widths.append(w)

    def render_row(cells):
        # 注意：不能 rstrip()。最后一列若非最长列，rstrip 会吃掉填充空格，
        # 使该行比别的行短，等宽字体下看起来就是列不对齐
        # （实测 ["甲","1"] 与 ["乙乙","22"] 混排时被 rstrip 破坏对齐）。
        return " │ ".join(_pad(c, widths[k]) for k, c in enumerate(cells))

    lines = [render_row(head)]
    lines.append("─┼─".join("─" * w for w in widths))
    for r in norm:
        lines.append(render_row(r))
    return "\n".join(lines)


# ---------- 渲染到 Text ----------

def _tag(tw, name, **kw):
    tw.tag_configure(TAG_PREFIX + name, **kw)


def config_tags(tw, p):
    """按配色方案配置全部 tag。可重复调用（幂等）。

    所有字号走 base_font()/mono_font()，不用硬编码磅值 ——
    磅值本身由 Tk 按 DPI 换算，但字体族必须是我们校准过的。
    """
    s = BASE_SIZE
    _tag(tw, "h1", font=base_font(s + 7, bold=True),
         foreground=p["head"], spacing1=py_dpi.px(12), spacing3=py_dpi.px(7))
    _tag(tw, "h2", font=base_font(s + 4, bold=True),
         foreground=p["head"], spacing1=py_dpi.px(11), spacing3=py_dpi.px(6))
    _tag(tw, "h3", font=base_font(s + 2, bold=True),
         foreground=p["head"], spacing1=py_dpi.px(10), spacing3=py_dpi.px(5))
    _tag(tw, "h4", font=base_font(s + 1, bold=True),
         foreground=p["head"], spacing1=py_dpi.px(8), spacing3=py_dpi.px(4))
    _tag(tw, "h5", font=base_font(s, bold=True),
         foreground=p["head"], spacing1=py_dpi.px(8), spacing3=py_dpi.px(4))
    _tag(tw, "h6", font=base_font(s, bold=True),
         foreground=p["muted"], spacing1=py_dpi.px(8), spacing3=py_dpi.px(4))
    _tag(tw, "bold", font=base_font(s, bold=True), foreground=p["head"])
    _tag(tw, "italic", font=base_font(s, italic=True))
    _tag(tw, "strike", overstrike=True, foreground=p["muted"])
    _tag(tw, "code", font=mono_font(s - 1), background=p["code_bg"],
         foreground=p["code_fg"])
    _tag(tw, "link", foreground=p["accent"], underline=True)
    _tag(tw, "codeblock", font=mono_font(s - 1), background=p["code_bg"],
         foreground=p["code_fg"],
         lmargin1=py_dpi.px(14), lmargin2=py_dpi.px(14),
         rmargin=py_dpi.px(8),
         spacing1=py_dpi.px(3), spacing3=py_dpi.px(3))
    # 表格单独一个 tag：与代码块同字体，但左右留白略大，
    # 并加一点段前后间距，避免和上一段文字黏在一起。
    _tag(tw, "table", font=mono_font(s - 1), background=p["code_bg"],
         foreground=p["code_fg"],
         lmargin1=py_dpi.px(14), lmargin2=py_dpi.px(14),
         rmargin=py_dpi.px(8),
         spacing1=py_dpi.px(10), spacing3=py_dpi.px(10))
    _tag(tw, "quote", foreground=p["quote_fg"],
         lmargin1=py_dpi.px(18), lmargin2=py_dpi.px(18),
         font=base_font(s, italic=True))
    _tag(tw, "li", lmargin1=py_dpi.px(18), lmargin2=py_dpi.px(34),
         spacing1=py_dpi.px(1), spacing3=py_dpi.px(1))
    _tag(tw, "p", spacing1=py_dpi.px(2), spacing3=py_dpi.px(5))
    _tag(tw, "hr", foreground=p["rule"],
         spacing1=py_dpi.px(10), spacing3=py_dpi.px(10))
    # 代码高亮（在 codeblock 之上叠加，只改前景色）
    _tag(tw, "kw", foreground=p["kw"])
    _tag(tw, "str", foreground=p["str"])
    _tag(tw, "num", foreground=p["num"])
    _tag(tw, "com", foreground=p["com"], font=mono_font(s - 1, bold=False))
    _tag(tw, "fn", foreground=p["fn"])


def _ins(tw, text, tags=()):
    start = tw.index("end-1c")
    tw.insert("end", text)
    end = tw.index("end-1c")
    for t in tags:
        if t.startswith(TAG_PREFIX):
            tw.tag_add(t, start, end)
    return start, end


# ---------- 代码高亮 ----------

# 只做"能扫读"级别的着色，不追求语法正确。
# 刻意保持极简：正则分流 + 单遍扫描，避免引入 pygments 这类重依赖
# （它会带来 ~10MB 内存与数十个模块的导入开销）。
_KW = {
    "python": """def class return if elif else for while in not and or import from
        as with try except finally raise pass break continue lambda yield
        global nonlocal assert del None True False self async await""",
    "javascript": """function const let var return if else for while do switch case
        break continue new class extends import export from as default try catch
        finally throw typeof instanceof null undefined true false this async await""",
    "typescript": """function const let var return if else for while do switch case
        break continue new class extends implements interface type import export
        from as default try catch finally throw typeof instanceof null undefined
        true false this async await public private readonly enum""",
    "java": """public private protected class interface extends implements return if
        else for while do switch case break continue new import package static
        final void int long double float boolean char String null true false this""",
    "go": """func package import return if else for range switch case break continue
        var const type struct interface map chan go defer select nil true false""",
    "rust": """fn let mut return if else for while loop match struct enum impl trait
        pub use mod crate self super as const static ref move where unsafe dyn
        Some None Ok Err true false""",
    "sql": """select from where group by having order limit offset insert into values
        update set delete create table index join left right inner outer on as
        and or not null distinct count sum avg min max""",
    "c": """int char float double void long short unsigned signed struct union enum
        return if else for while do switch case break continue sizeof typedef
        static const extern include define NULL""",
}
_KW = {k: set(v.split()) for k, v in _KW.items()}

_LANG_ALIAS = {
    "py": "python", "python3": "python", "python2": "python",
    "js": "javascript", "node": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "golang": "go", "rs": "rust",
    "sh": None, "bash": None, "shell": None, "zsh": None,
    "json": "javascript", "yaml": "yaml", "yml": "yaml",
    "cpp": "c", "c++": "c", "h": "c", "hpp": "c",
    "cs": "java", "csharp": "java", "kotlin": "java", "kt": "java",
    "mysql": "sql", "postgres": "sql", "sqlite": "sql",
}

_RE_TOK = re.compile(
    r"(?P<com>#[^\n]*|//[^\n]*|/\*.*?\*/|--[^\n]*)"
    r"|(?P<str>\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\])*\""
    r"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)"
    r"|(?P<num>\b\d+\.?\d*(?:[eE][+-]?\d+)?\b)"
    r"|(?P<fn>\b[A-Za-z_]\w*(?=\s*\())"
    r"|(?P<word>\b[A-Za-z_]\w*\b)",
    re.S,
)


def highlight_code(lang, code):
    """把代码切成 [(text, tag_or_None)]，供逐段着色。"""
    key = _LANG_ALIAS.get((lang or "").lower(), (lang or "").lower())
    kws = _KW.get(key or "", set())

    out = []
    pos = 0
    for m in _RE_TOK.finditer(code):
        if m.start() > pos:
            out.append((code[pos:m.start()], None))
        g = m.lastgroup
        txt = m.group()
        if g == "com":
            out.append((txt, "com"))
        elif g == "str":
            out.append((txt, "str"))
        elif g == "num":
            out.append((txt, "num"))
        elif g == "fn":
            out.append((txt, "fn"))
        elif g == "word":
            # 语言关键字集合按小写匹配，兼顾 SQL 大小写不敏感
            if kws and (txt.lower() in kws or txt in kws):
                out.append((txt, "kw"))
            else:
                out.append((txt, None))
        else:
            out.append((txt, None))
        pos = m.end()
    if pos < len(code):
        out.append((code[pos:], None))
    return out


def _emit_code(tw, lang, code):
    """渲染代码块：底色统一由 codeblock tag 提供，高亮只叠前景色。"""
    if not code:
        _ins(tw, " \n", (TAG_PREFIX + "codeblock",))
        return
    for seg, tag in highlight_code(lang, code):
        if not seg:
            continue
        tags = (TAG_PREFIX + "codeblock",)
        if tag:
            tags = tags + (TAG_PREFIX + tag,)
        _ins(tw, seg, tags)
    _ins(tw, "\n", (TAG_PREFIX + "codeblock",))


def _emit_runs(tw, runs, base_tags=()):
    """把 runs 依次插入控件。

    行内 tag（bold / code / link …）与块级 tag（h1 / p / quote / li）
    是叠加关系，不是互斥 —— 标题里加粗、引用里的链接都要同时带上
    两边的样式，所以这里把 base_tags 与每个 run 自己的 tag 合并。

    链接用"插入链接文本 + 零宽占位字符携带 URL"的方式处理：
    tkinter 的 tag_bind 回调拿不到自定义参数，只能按 tag 名区分，
    所以每遇到一个链接就建一个独立 tag 名，把 URL 记在闭包里。
    """
    base = tuple(base_tags)
    pending_url = None
    link_start = None

    def close_link(end):
        if pending_url and link_start:
            name = "link_%d" % len(_LINKS)
            _LINKS.append(pending_url)
            tw.tag_configure(
                name, foreground=tw.tag_cget(TAG_PREFIX + "link", "foreground"),
                underline=True)
            tw.tag_add(name, link_start, end)
            tw.tag_bind(name, "<Button-1>", lambda e, u=pending_url: _open(u))
            tw.tag_bind(name, "<Enter>",
                        lambda e, w=tw: w.configure(cursor="hand2"))
            tw.tag_bind(name, "<Leave>",
                        lambda e, w=tw: w.configure(cursor="xterm"))

    for text, tags in runs:
        # 零宽占位：不显示任何东西，只把 URL 传过来
        if "__linkurl__" in tags:
            pending_url = tags[tags.index("__linkurl__") + 1]
            continue

        names = [TAG_PREFIX + t for t in tags
                 if t in ("bold", "italic", "strike", "code")]
        if "link" in tags and link_start is None:
            link_start = tw.index("end-1c")
        start, end = _ins(tw, text, tuple(base) + tuple(dict.fromkeys(names)))
        if "link" in tags:
            pending_url = pending_url or text
            close_link(end)
            link_start = None
            pending_url = None


_LINKS = []


def _open(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _render_blocks(tw, md_text):
    """把 Markdown 的各个块依次写入控件当前位置（不清空）。"""
    for blk in blocks(md_text):
        k = blk["kind"]
        if k == "code":
            _emit_code(tw, blk.get("lang"), blk["text"])
        elif k == "hr":
            _ins(tw, "─" * 40 + "\n", (TAG_PREFIX + "hr",))
        elif k == "table":
            _ins(tw, table_to_text(blk["head"], blk["rows"]) + "\n",
                 (TAG_PREFIX + "table",))
        elif k == "quote":
            _ins(tw, "▎", (TAG_PREFIX + "quote",))
            _emit_runs(tw, blk["runs"], (TAG_PREFIX + "quote",))
            _ins(tw, "\n")
        elif k == "list":
            for mark, body in blk["items"]:
                bullet = (mark + " ") if mark else "• "
                _ins(tw, bullet, (TAG_PREFIX + "li",))
                _emit_runs(tw, inline_runs(body), (TAG_PREFIX + "li",))
                _ins(tw, "\n")
        elif k in ("h1", "h2", "h3", "h4", "h5", "h6"):
            _emit_runs(tw, blk["runs"], (TAG_PREFIX + k,))
            _ins(tw, "\n")
        else:
            _emit_runs(tw, blk["runs"], (TAG_PREFIX + "p",))
            _ins(tw, "\n")


def render(tw, md_text, palette=None, tf=None):
    """渲染 Markdown 到 Text 控件（会先清空）。"""
    p = dict(active_palette())
    if palette:
        p.update(palette)
    config_tags(tw, p)

    was = tw.cget("state")
    tw.configure(state="normal")
    tw.delete("1.0", "end")
    if tf:
        tw.configure(font=tf)
    _render_blocks(tw, md_text)
    tw.configure(state=was)
    return tw


def render_into(tw, md_text, start_mark):
    """把 Markdown 渲染到 start_mark 之后（不清空已有内容）。

    用于多轮追问：第一轮的回答保持在上面，后续回答接着往下写，
    用户能上下翻看完整对话脉络。
    实现上仍然先删掉 start_mark 之后的内容再重写 ——
    因为流式阶段那里存的是纯文本，必须整体替换成富文本。
    """
    p = dict(active_palette())
    config_tags(tw, p)
    was = tw.cget("state")
    tw.configure(state="normal")
    try:
        tw.delete(start_mark, "end-1c")
    except Exception:
        tw.delete("end-1c", "end")
    _render_blocks(tw, md_text)
    tw.configure(state=was)
    return tw
