# -*- coding: utf-8 -*-
"""工具调用标记的双向护栏（入站识别 + 出站清洗）。

背景
----
模型偶发把**工具调用标记**写进正文：本该走 API 的 `tool_calls` 字段，结果它把
`<tool_call>{"name": ...}</tool_call>` 当成普通文本吐了出来。以前的后果：

* **出站**：这串标记原样发到群里 —— 群友看到一段乱码，等于机器人在刷屏；
* **入站**：群里另一个机器人也会漏出同样的标记，整条被当"人话"喂进上下文，
  污染对话历史（模型看到就会**学着**也吐标记，越滚越脏）。

这里只做两件事，都是纯函数 / 纯 stdlib，不依赖 nonebot，方便单测：

1. `strip_markers` / `StreamCleaner` —— 出站把标记剥掉，**流式的每个分片都要过**；
2. `is_all_markers` —— 入站判断"整条就是标记"，这种不喂模型。

设计要点
--------
* **流式安全**：标记可能被分片切成两半（`<tool_` + `call>`）。`StreamCleaner`
  会把"可能是标记开头"的尾巴**扣住不发**，等下一片到了再一起判断；
  未闭合的标记会一直吞到闭合标记出现为止，绝不把半个标记漏出去。
* **剥完为空**：调用方（brain）负责"不发 + 人话兜底 + warning"，见 `FALLBACK_TEXT`。
* **宁可多剥**：这些都是模型控制 token，正常人不会打出来。判不准时**不发**比
  "发一段乱码到群里"代价小得多。
"""
from __future__ import annotations

import re

# 剥完标记后没话可说时，替它说的那句人话（要像人，不要像报错）
FALLBACK_TEXT = "抱歉，刚才那句没说出来，你再说一次我重新答。"

# ===== 标记词法 =====

# 成对标签（XML 风：Qwen / Hermes / 一堆开源模型都这么吐）
_PAIR_TAGS = ("tool_call", "tool_calls", "function_call", "function_calls", "tool", "tools")
_TAG_ALT = "|".join(_PAIR_TAGS)

# 完整的一对：<tool_call> ... </tool_call>（非贪婪，中间就是 JSON 参数）
_RE_PAIR = re.compile(r"<\s*(%s)\b[^>]*>.*?</\s*\1\s*>" % _TAG_ALT, re.S | re.I)
# 只有开标签（后面的闭合标记还没到 / 模型就没写）
_RE_OPEN_TAG = re.compile(r"<\s*(%s)\b[^>]*>" % _TAG_ALT, re.I)
# 只有闭标签（开标签被上一条分片吃掉等情况）
_RE_CLOSE_TAG = re.compile(r"</\s*(%s)\s*>" % _TAG_ALT, re.I)

# 特殊 token：<|tool_call|> / <|/tool_call|> / <|python_tag|> / <|im_start|> / <|eot_id|> ...
# 字符集要带 `/`：闭合形态是 `<|/tool_call|>`，少一个斜杠就漏一条（单测当场抓到过）。
_RE_SPECIAL = re.compile(r"<\|[A-Za-z0-9_/\u2581.\-]{1,64}\|>")
# 特殊 token 成对包起来的块：<|tool_call|>{"name": ...}<|/tool_call|>
# **必须整块删**：只删两端 token 的话，中间的 JSON 参数会原样发到群里。
_RE_SPECIAL_BLOCK = re.compile(r"<\|([A-Za-z0-9_\u2581.\-]{1,32})\|>.*?<\|/\1\|>", re.S)

# Qwen 系：✿FUNCTION✿name✿ARGS✿{...}；后面全是参数，一律吞到结尾
_RE_QWEN = re.compile(r"\u273f[A-Z_]{2,}\u273f")
# Mistral 系：[TOOL_CALLS] / [AVAILABLE_TOOLS] ... [/AVAILABLE_TOOLS]
_RE_MISTRAL_OPEN = re.compile(r"\[\s*(?:TOOL_CALLS?|AVAILABLE_TOOLS)\s*\]", re.I)
_RE_MISTRAL_CLOSE = re.compile(r"\[/\s*(?:TOOL_CALLS?|AVAILABLE_TOOLS)\s*\]", re.I)
# gpt-oss 系 commentary 通道：to=functions.xxx
_RE_TO_EQ = re.compile(r"\bto=functions\.[A-Za-z0-9_.\-]+")

# 流式扣留用的"可能的标记开头"（尾巴是其中任意一个的前缀 → 先不发）
_TRIGGERS = (
    "<tool_call", "<tool_calls", "</tool_call", "</tool_calls",
    "<tool>", "</tool>", "<tools>", "</tools>",
    "<function_call", "<function_calls", "</function_call", "</function_calls",
    "<|",
    "[TOOL_CALL", "[TOOL_CALLS", "[/TOOL_CALL", "[/TOOL_CALLS",
    "[AVAILABLE_TOOLS", "[/AVAILABLE_TOOLS",
    "\u273f", "to=functions.",
)
_TRIGGERS_LOWER = tuple(t.lower() for t in _TRIGGERS)
_MAX_TRIGGER = max(len(t) for t in _TRIGGERS_LOWER)

# 判断"剥完还剩不剩人话"：中日韩文字 / 字母 / 数字
_RE_MEANINGFUL = re.compile(
    r"[0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff]")


def has_meaningful_char(text: str) -> bool:
    """还有没有"实义字符"。空 @ 那套（prompt_window）同口径，这里自带一份免依赖。"""
    return bool(_RE_MEANINGFUL.search(str(text or "")))


def _open_after(text: str, opener: str, closer: str) -> int:
    """找第一个"有 opener 但后面再没有 closer"的位置；没有返回 -1。"""
    start = 0
    while True:
        idx = text.find(opener, start)
        if idx == -1:
            return -1
        if text.find(closer, idx + len(opener)) == -1:
            return idx
        start = idx + len(opener)


def open_marker_start(text: str) -> int:
    """找**第一个没闭合**的分隔符 token 的起点；没有返回 -1。

    两族 token 的正文是**变长**的，靠"固定前缀表"扣不住（单测两次抓到漏字）：
      * `<| ... |>`  —— 闭合是 `|>`
      * `✿ ... ✿`    —— 闭合是下一个 `✿`
    所以这里不猜名字、只认结构：有开头、后面找不到闭合 → 就是没闭合，先扣住不发。
    """
    hits = [i for i in (_open_after(text, "<|", "|>"),
                        _open_after(text, "\u273f", "\u273f")) if i != -1]
    return min(hits) if hits else -1


def _earliest(text: str, pos: int):
    """从 pos 起找**最靠前**的一个标记；返回 (kind, match) 或 (None, None)。

    kind：`pair` 完整一对（整段删）／`special` 特殊 token（删 token）／
    `close` 孤立闭标签（删 token）／`open` 未闭合开标签（吞到结尾）／
    `swallow` 开放式标记（吞到结尾）。

    同一位置上取**最长**的那个匹配：`<tool_call>…</tool_call>` 要整体赢过
    「只匹配到开标签」的 `_RE_OPEN_TAG`，否则中间参数会被漏在外面。
    """
    best = None
    for kind, rx in (
        ("pair", _RE_PAIR),
        ("pair", _RE_SPECIAL_BLOCK),
        ("special", _RE_SPECIAL),
        ("close", _RE_CLOSE_TAG),
        ("close", _RE_MISTRAL_CLOSE),
        ("open", _RE_OPEN_TAG),
        ("swallow", _RE_QWEN),
        ("swallow", _RE_MISTRAL_OPEN),
        ("swallow", _RE_TO_EQ),
    ):
        m = rx.search(text, pos)
        if not m:
            continue
        key = (m.start(), -(m.end() - m.start()))
        if best is None or key < best[0]:
            best = (key, kind, m)
    return (best[1], best[2]) if best else (None, None)


def _scan(text: str, final: bool):
    """扫一遍缓冲区；返回 (可发出的文本, 需留在缓冲区的尾巴, 是否剥掉了东西)。"""
    emit = []
    removed = False
    i = 0
    n = len(text)
    while i < n:
        kind, m = _earliest(text, i)
        if kind is None:
            emit.append(text[i:])
            break
        emit.append(text[i:m.start()])
        if kind in ("pair", "special", "close"):
            removed = True
            i = m.end()
            continue
        # open / swallow：后面全是参数，得等闭合标记（swallow 类等不到就整段丢）
        removed = True
        if final:
            i = n                      # 结尾了还没闭合 → 剩下的全丢
            continue
        # 没到结尾：整段扣在缓冲区，等下一片（闭合标记可能就在下一片里）
        emit.append("")                # 占位，保持可读
        return "".join(emit), text[m.start():], removed
    # 尾巴上的半截分隔符 token（`<|tool_call|`、`✿FUNCTIO` 这种断了半截的）
    rest = "".join(emit)
    idx = open_marker_start(rest)
    if idx != -1:
        removed = True
        if final:
            rest = rest[:idx]
        else:
            return rest[:idx], rest[idx:], removed
    return rest, "", removed


def _holdback_len(text: str) -> int:
    """`text` 结尾有多少字符"可能是某个标记的开头"→ 这部分先不发。"""
    low = text.lower()
    for k in range(min(len(low), _MAX_TRIGGER), 0, -1):
        tail = low[-k:]
        for trig in _TRIGGERS_LOWER:
            if trig.startswith(tail):
                return k
    return 0


def strip_markers(text: str):
    """一次性剥掉所有工具调用标记；返回 (干净文本, 是否剥掉过东西)。"""
    clean, _rest, removed = _scan(str(text or ""), final=True)
    return clean, removed


# ---- 裸 JSON 工具调用（模型把调用"写"成 JSON 文本，没有标签壳）----
# 2026-10-15 实测抓到的（本机真模块集成模拟）：
#     好的{"name": "send_file", "arguments": {"path": "D:/secret/.env"}}
# 整串被当人话发进群 —— 工具名、参数、甚至本机路径全广播出去了。
# 上面那套标记词法只认 <tool_call> / ✿FUNCTION✿ 这类**带壳**写法，裸 JSON 一个都不匹配。
# 这里按**结构**认：括号配平的 JSON 对象里出现工具调用特征键 → 整块删掉。
# 特征键取"强键"（arguments/parameters/tool_calls/function/tool_name），
# 或在 name/tool 之外还带 query/input/args/action —— 免得把用户让打印的
# `{"name": "张三", "age": 30}` 这类正常 JSON 也误删。
_STRONG_TOOL_KEYS = frozenset(("arguments", "parameters", "tool_calls", "function",
                               "tool_name", "tool_call"))
_NAME_TOOL_KEYS = frozenset(("name", "tool", "action", "function"))
_ARG_TOOL_KEYS = frozenset(("query", "input", "args", "arguments", "parameters"))
_RE_JSON_KEY = re.compile(r"[\"']([A-Za-z_][A-Za-z0-9_]{1,24})[\"']\s*:")


def _balanced_objects(text: str):
    """找出所有**括号配平**的 `{...}` 片段（字符串里的括号不算）。"""
    spans = []
    depth = 0
    start = -1
    quote = ""
    esc = False
    for i, ch in enumerate(text):
        if quote:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, i + 1))
                start = -1
    return spans


def _looks_like_tool_json(blob: str) -> bool:
    if not blob or len(blob) > 4000:
        return False
    keys = {m.group(1).lower() for m in _RE_JSON_KEY.finditer(blob)}
    if not keys:
        return False
    if keys & _STRONG_TOOL_KEYS:
        return True
    return bool(keys & _NAME_TOOL_KEYS) and bool(keys & _ARG_TOOL_KEYS)


def strip_json_tool_calls(text: str):
    """把"写成裸 JSON 的工具调用"整块删掉；返回 (文本, 是否删过东西)。"""
    raw = str(text or "")
    if "{" not in raw:
        return raw, False
    spans = _balanced_objects(raw)
    if not spans:
        return raw, False
    out = []
    last = 0
    removed = False
    for start, end in spans:
        if not _looks_like_tool_json(raw[start:end]):
            continue
        out.append(raw[last:start])
        last = end
        removed = True
    if not removed:
        return raw, False
    out.append(raw[last:])
    return "".join(out), True


def clean_outbound(text: str):
    """出站单条清洗；返回 (干净文本, 是否剥掉过东西)。空不空由调用方决定。"""
    clean, removed = strip_markers(text)
    clean, removed_json = strip_json_tool_calls(clean)
    return clean.strip(), (removed or removed_json)


# 只剩这些字符 = 没有可发的内容（典型是 `{}` 这种被剥剩下的参数壳）
_RE_STRUCTURAL_ONLY = re.compile(r"^[\s{}\[\]()<>\"'`,;:.\-|*/\\=+_~!?？。，、；：]+$")


def is_nothing_to_send(text: str) -> bool:
    """剥完之后是不是"没话可说"（空串，或只剩 `{}` 这类标点壳）。

    注意**不能**用 `has_meaningful_char` 代替：纯表情（「😂」）是合法回复，
    那是实义内容之外的东西，不该被当成空。这里只认"结构性标点"。
    """
    raw = str(text or "").strip()
    if not raw:
        return True
    return bool(_RE_STRUCTURAL_ONLY.match(raw))


def outbound_text(text: str):
    """出站最终决定：返回 (要发出去的文本, 是否用了兜底, 是否剥过标记)。

    剥完为空 → 交给调用方发 `FALLBACK_TEXT` 并打 warning（这里只做判断，
    不替调用方打日志，保持纯函数好测）。
    """
    clean, removed = clean_outbound(text)
    if is_nothing_to_send(clean):
        return FALLBACK_TEXT, True, removed
    return clean, False, removed


def is_all_markers(text: str) -> bool:
    """整条消息是不是"只有标记、没有人话"（群里另一个机器人漏出来的那种）。

    判据：**剥掉标记后一个实义字符都不剩**。混着人话的返回 False（照常处理）。
    """
    raw = str(text or "")
    if not raw.strip():
        return False
    clean, removed = strip_markers(raw)
    if not removed:
        return False
    return not has_meaningful_char(clean)


class StreamCleaner:
    """流式清洗器：一段一段喂进来，只放行"确定干净"的文本。

    用法::

        c = StreamCleaner()
        for piece in pieces:
            safe = c.feed(piece)      # 可能返回 ""（扣着呢）
            if safe: await send(safe)
        tail = c.flush()              # 收尾，把扣着的尾巴放出来
        if tail: await send(tail)

    关键行为：标记被分片切成两半也不会漏 —— 半截标记会被扣在缓冲区里，
    等下一片拼齐了再整段剥掉（见 `tests/test_tool_marker_outbound.py`）。
    """

    def __init__(self):
        self._buf = ""
        self.removed = False       # 整条里是否剥掉过标记
        self.emitted = False       # 是否真的发出过东西

    def feed(self, fragment: str) -> str:
        self._buf += str(fragment or "")
        safe, rest, removed = _scan(self._buf, final=False)
        self.removed = self.removed or removed
        hold = _holdback_len(safe)
        if hold:
            rest = safe[-hold:] + rest
            safe = safe[:-hold]
        self._buf = rest
        if safe.strip():
            self.emitted = True
        return safe

    def flush(self) -> str:
        safe, _rest, removed = _scan(self._buf, final=True)
        self.removed = self.removed or removed
        self._buf = ""
        if safe.strip():
            self.emitted = True
        return safe


# ---- 假工具调用标记（模型没拿到 tools 时会把调用"念"出来）----
# 2026-10-12 事故：工具路径被 MCP 开关挡住，模型硬编了
#   <||DSML|| calls> <||DSML|| invoke name="send_to_my_platform"> … </||DSML|| invoke> </||DSML|| calls>
# 结果这段标记**原样发到了用户微信上**。这里负责把它连同残渣一起剥掉。
_FAKE_CALL_PATTERNS = [
    re.compile(r"<\|+\s*/?\s*DSML.*?-{0,2}\|+>", re.S),
    re.compile(r"<\|+\s*/?\s*(?:function_calls?|invoke|parameter|calls).*?\|+>", re.S),
    re.compile(r"</?\|+\s*(?:invoke|parameter|calls)[^>]*>", re.S),
    re.compile(r"<function_calls?>.*?</function_calls?>", re.S),
    re.compile(r"</?(?:invoke|parameter)[^>]*>", re.S),
]


def strip_fake_tool_calls(text: str) -> str:
    """把"假工具调用标记"从正文里剥掉（连里面的参数一起，反正它没真的调）。

    2026-10-15 修（老板：让它在群里写段代码，发出来缩进全没了）：
    末尾那两句 `re.sub(r"[ \\t]+", " ", out)` / 空行折叠以前是**无条件**跑的，
    于是任何正文里两位以上的连续空格都被拍成一个 —— Python 缩进、对齐排版、
    表格全部被毁。它们本来只是"剥掉标记之后清理残渣"的手段，
    **一个标记都没剥到时就不该动正文**，所以这里加了"没变化就原样返回"。
    """
    out = str(text or "")
    original = out
    for pat in _FAKE_CALL_PATTERNS:
        out = pat.sub("", out)
    # 兜底：块与块之间的残渣（name="..." 之类）靠上面剥不干净 ——
    # 一旦出现 DSML 签名，就从签名处**整段截掉**（2026-10-12：日志里那串
    # `<||DSML|| calls> <||DSML|| invoke name="accept_friend_request"> …` 就是这么漏出去的）。
    for sig in ("<||DSML", "<|DSML", "<||dsml", "|<|DSML"):
        i = out.find(sig)
        if i >= 0:
            out = out[:i]
    if out == original:
        # 没剥到任何标记 → 正文原样返回（只去首尾空白，绝不动行内缩进）
        return original.strip()
    # 确实剥掉了东西，才清理残渣：残留空标签行与多余空行
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def clean_segments(segments) -> tuple:
    """把"要发出去的几段话"整批过一遍清洗。返回 (可发的段落列表, 是否剥掉过标记)。

    这就是 `brain._reply_in_segments` 出站那一段（**流式的每个分片都要过** ——
    标记可能正好被分片切成两半，只在最后过滤会漏）。抽成函数是为了让单测能
    按"回复整形 → 出站清洗"的真实顺序跑一遍，而不是在测试里复刻一遍循环。
    """
    cleaner = StreamCleaner()
    out = []
    for segment in segments or []:
        safe = cleaner.feed(segment)
        if safe.strip():
            out.append(safe.strip())
    tail = cleaner.flush()
    if tail.strip():
        out.append(tail.strip())
    return out, cleaner.removed


# ---- 出口兜底：任何要发出去的分段都先剥一遍假标记（2026-10-12）----
_orig_clean_segments = clean_segments


def clean_segments(raw_segments):        # type: ignore[override]
    pending, removed = _orig_clean_segments(raw_segments)
    cleaned = []
    for seg in pending:
        s = strip_fake_tool_calls(seg)
        s, removed_json = strip_json_tool_calls(s)      # 裸 JSON 工具调用（2026-10-15）
        removed = removed or removed_json
        if s.strip():
            cleaned.append(s)
    return cleaned, removed or (len(pending) != len(cleaned))
