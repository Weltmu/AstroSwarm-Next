# -*- coding: utf-8 -*-
"""把 AI 的原始回复变成"要发出去的几段话"。

背景（2026-10-01 老板报"群聊 @ 它不说话"）：
提示词要求模型严格返回 `{"reply": ["第一段", ...]}`，但模型（尤其快模型）经常
不照格式回。以前只要解析不出 JSON，brain 就把**整条回复扔掉**并写一条错误日志 ——
用户那边看到的就是"机器人一个字都不说"，而日志里明明写着"AI 回复 31 字"。

这里的原则：**能解析就分段，解析不了就把原文当一段发出去**，永远不要因为
格式问题让用户收不到话。纯函数、不依赖 nonebot，方便测试。

**唯一的例外：JSON 外壳绝不能发出去**（2026-10-11 回归）
------------------------------------------------------
上面那条"解析不了就发原文"的兜底，在"原文本身就是 `{"reply": [...]}` 的壳"时
会把**大括号、`"reply"`、方括号原样发进群** —— 群友看到一段机器码，等于刷屏。
触发它的情况有三种，都不罕见：

* JSON 被截断（`{"reply": ["a", "b"`，max_tokens 用尽）；
* `reply` 解析出来了但里面**没有可发的字符串**（`{"reply": []}` / `{"reply": [1,2]}`）；
* 外壳里混了解说词（`好的：{"reply": ["a"] 后面还有半句`）。

所以现在的兜底分两条路：

1. **看得出是回复壳**（顶层是 `{`/`[` 且有正文字段名）→ 只**抠正文**（`_salvage_text`），
   抠不出来就返回空列表 —— **宁可不发，也绝不发壳**（调用方会改说一句人话）；
2. 不是壳（模型直接说了句人话）→ 先按"双空格 / 空行"分一下段：**模型最近爱用
   两个半角空格当分段符**（2026-10-12 老板报"该分几条发的被并成一条"），切出
   ≥2 段就逐段交给分条判定；切不出多段就原样当一段发出，老行为不变。

分段符只认**可靠信号**（连续 ≥2 个半角空格 / 空行 / 带缩进换行），
中文正文里几乎不会自然出现；单空格与单个换行**绝不拆**，防止误伤正常排版。

另外：模型常把**工具调用标记**写进 JSON 字符串里，而且引号往往**没转义**
（`{"reply": ["<tool_call>{"name": "x"}</tool_call>"]}`）—— 不先剥掉的话外层 JSON
必然解析失败、只能走兜底。所以解析**之前**先剥一遍标记（`_strip_tool_markers`）；
主路径上每个分片仍由 `tool_marker.StreamCleaner` 再过一遍（双层护栏）。
"""
from __future__ import annotations

import json
import random
import re


def strip_code_fence(raw: str) -> str:
    """去掉模型爱包的 ```json ... ``` 外壳。"""
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`").strip()
    if text[:4].lower() == "json":
        text = text[4:].strip()
    return text.strip()


def parse_reply_json(raw: str):
    """尽力把回复解析成 JSON；解析不出来返回 None（不抛异常）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    unfenced = strip_code_fence(text)
    try:
        return json.loads(unfenced)
    except Exception:  # noqa: BLE001
        pass
    # 前后带解说词时，抠出最外层的一对括号再试
    for opener, closer in (("{", "}"), ("[", "]")):
        start = unfenced.find(opener)
        end = unfenced.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(unfenced[start:end + 1])
            except Exception:  # noqa: BLE001
                continue
    return None


def _clean_segments(items) -> list:
    out = []
    for item in items or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


# ===== 回复壳的识别 / 正文抢救（2026-10-11 回归：壳不能发出去）=====

# 约定的正文字段名（按优先级）；前两个是提示词要求的，其余是模型跑偏时的常见写法
_TEXT_KEYS = ("reply", "replies", "content", "text", "message", "answer",
              "segments", "messages", "say", "response")
_TEXT_KEYS_LOWER = frozenset(_TEXT_KEYS)
_RE_TEXT_KEY_ALT = "|".join(_TEXT_KEYS)

# 「这是回复壳」：出现 `{` / `[` 紧跟一个正文字段名
_RE_ENVELOPE_KEY = re.compile(
    r"[\[{]\s*[\"']?(?:%s)[\"']?\s*:" % _RE_TEXT_KEY_ALT, re.I)
# 裸键开头（连大括号都省了）：`'reply': ['a']`。要求值以 [ 或 { 开头 ——
# 光一个 `reply:` 开头的人话（"reply: 我不知道"）不算，别误伤。
_RE_BARE_KEY_START = re.compile(
    r"^\s*[\"']?(?:%s)[\"']?\s*:\s*[\[{]" % _RE_TEXT_KEY_ALT, re.I)
# 半截对象的通用形状：以 `{` 开头且出现 `"key":`（2026-10-15：单引号键也认 ——
# 模型照着 JS 写 `{'reply': [...]}` 时，只认双引号会判成"不是壳"→ 原文发进群）
_RE_KEY_PATTERN = re.compile(r"[\"'][^\"'\n]{1,40}[\"']\s*:")
# 工具调用的键名（裸 JSON 的调用形状：{"name": "...", "arguments": ...}）
# 2026-10-16（对抗性复核 A1/A2）：补 query / input / tool —— 只认 arguments 那几个时，
# `{"query": "天气"}` / `{"tool": "web_search"}` 这种"不带 name 的调用形状"照样漏出去。
_RE_TOOL_KEY_PATTERN = re.compile(
    r"[\"'](?:arguments|parameters|tool_calls?|tool_name|tool|function"
    r"|query|input)[\"']\s*:", re.I)
# 带前缀的调用形状：`web_search\n{"query": "..."}` —— 首字符不是括号也得判成壳
_RE_TOOL_CALL_SHAPE = re.compile(
    r"[\"'](?:name|tool)[\"']\s*:\s*[\"'][^\"'\n]{1,40}[\"']\s*,\s*"
    r"[\"'](?:arguments|parameters|args|query|input)[\"']\s*:", re.I)
# JSON 字符串字面量（含转义），抢救正文时用
_RE_JSON_STR = re.compile(r'"((?:[^"\\]|\\.){0,4000})"')
# 单引号版：只在整段抓不到任何双引号字符串时兜底（模型照 JS 写 `'reply': ['a']`）
_RE_JSON_STR_SQ = re.compile(r"'((?:[^'\\]|\\.){0,4000})'")
# 只剩这些字符 = 不是正文（括号 / 标点 / 空壳）
_RE_STRUCTURAL_ONLY = re.compile(
    r"^[\s{}\[\]()<>\"'`,;:.\-|*/\\=+_~!?？。，、；：]+$")

# 工具调用标记（**解析前**先剥一遍）。0.2.39 的 `tool_marker` 是主护栏、管出站；
# 这里这份是"解析前"的第二层，只求把壳修回可解析的形状，不求穷尽所有方言。
# 注意**成对的要整块删**（只删两端 token 的话，中间的参数 JSON 会漏出去 —— 单测抓到过）。
_RE_TOOL_MARK = re.compile(
    r"<\|\s*([A-Za-z0-9_\u2581.\-]{1,32})\s*\|>.*?<\|\s*/\s*\1\s*\|>"
    r"|<\s*(?:tool_calls?|function_calls?|tools?)\b[^>]*>.*?"
    r"<\s*/\s*(?:tool_calls?|function_calls?|tools?)\s*>"
    r"|<\|\s*/?\s*[A-Za-z0-9_/\u2581.\-]{1,64}\s*\|>"
    r"|\u273f[A-Z_]{2,}\u273f"
    r"|\bto=functions\.[A-Za-z0-9_.\-]+"
    r"|\[\s*/?\s*(?:TOOL_CALLS?|AVAILABLE_TOOLS)\s*\]",
    re.S | re.I)


def _strip_tool_markers(text: str) -> str:
    """解析前剥掉工具调用标记（含它们夹带的参数 JSON）。"""
    try:
        return _RE_TOOL_MARK.sub(" ", str(text or ""))
    except Exception:  # noqa: BLE001 —— 剥标记失败不能影响回话
        return str(text or "")


def looks_like_envelope(text: str) -> bool:
    """这段文本是不是"本该是 `{"reply": [...]}` 的回复壳"。

    判据（**只在解析失败时才会被问到**）：
      * 出现 `{`/`[` + 正文字段名（`"reply":` / `"content":` …）；或
      * 顶层是 `{`/`[` 且带着 JSON 的键值/字符串形状（半截 JSON 的典型长相）。

    返回 True ⟹ 调用方**只准抠正文，不准把原文发出去**。
    """
    core = strip_code_fence(str(text or "")).strip()
    if not core:
        return False
    if _RE_ENVELOPE_KEY.search(core):
        return True
    # 2026-10-16（对抗性复核 A1）：没裹大括号的裸键开头（`'reply': ['a']`）也要算壳
    if _RE_BARE_KEY_START.match(core):
        return True
    # 2026-10-15：工具调用形状一律算壳（**不再要求首字符是括号**）——
    # `好的{"name": "send_file", "arguments": {...}}` 以前判"不是壳"，
    # 于是整串（工具名 + 参数 + 本机路径）被当人话发进群。
    if _RE_TOOL_CALL_SHAPE.search(core) or _RE_TOOL_KEY_PATTERN.search(core):
        return True
    if core[0] == "[" and _RE_JSON_STR.search(core):
        return True
    if core[0] == "{" and _RE_KEY_PATTERN.search(core):
        return True
    return False


def _split_plain_segments(text: str) -> list:
    """纯文本回复的保守分段：只认"连续 ≥2 个半角空格 / 空行 / 带缩进换行"。

    场景（2026-10-12）：模型没按 JSON 格式回，直接说人话，却用**两个半角空格**
    当分段符 —— 以前整条当一个段落，用户看到的就是"该分几条的并成一条"。
    这里只切**可靠信号**：中文正文里几乎不会自然出现连续双空格；
    单空格、单个换行一律不拆，防止误伤正常排版与代码块。
    """
    raw = str(text or "").replace("\u3000", " ")   # 全角空格先归一成半角再判
    parts = [p.strip() for p in _RE_PLAIN_SPLIT.split(raw)]
    parts = [p for p in parts if p]
    return parts if len(parts) >= 2 else []


# 纯文本分段符：连续 ≥2 个空格 / 空行 / 换行两侧带空白（带缩进的换行）
_RE_PLAIN_SPLIT = re.compile(r" {2,}|\r?\n[ \t]*(?:\r?\n[ \t]*)+|[ \t]+\r?\n[ \t]*")


def _segments_from_mapping(data: dict) -> list:
    """从解析出来的对象里取正文：先 `reply`，再其它常见的正文字段。"""
    for key in ("reply", "replies"):
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            segments = _clean_segments(value)
            if segments:
                return segments
        elif isinstance(value, str) and value.strip():
            return [value.strip()]
    for key in ("content", "text", "message", "answer", "say", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, list):
            segments = _clean_segments(value)
            if segments:
                return segments
    return []


def _salvage_text(text: str) -> list:
    """从"看得出是回复壳但解析不出来"的文本里，把**能当正文的字符串**抠出来。

    只在这条兜底路径上用。抠不出来就返回空列表 —— **宁可不发，也绝不发壳**。

    2026-10-15 修：工具调用的键名与它的值以前会被**当正文捡回来**
    （`{"tool_calls": [{"function": {"name": "send_to_my_platform",
    "arguments": "{\\"platform\\": \\"qq\\"}"}}]}` → 把 tool_calls / name /
    arguments / 参数串逐段发给用户）。现在：出现工具调用键 → **一个字都不抢救**
    （返回空 = 调用方改说人话兜底）。
    """
    raw_text = str(text or "")
    if _RE_TOOL_KEY_PATTERN.search(raw_text) or _RE_TOOL_CALL_SHAPE.search(raw_text):
        return []
    kept = []
    _cands = [(raw, True) for raw in _RE_JSON_STR.findall(raw_text)]
    if not _cands:
        # 2026-10-16（复核 A1 收尾）：整段只有一个**单引号**壳
        # （`'reply': ['今天周四']`）时，双引号一个都抓不到 —— 退到单引号这一路。
        # 只在双引号颗粒无收时才走，尽量不碰人话里的撇号。
        _cands = [(raw, False) for raw in _RE_JSON_STR_SQ.findall(raw_text)]
    for raw, dq in _cands:
        try:
            value = json.loads('"' + raw + '"') if dq else raw   # 双引号还原 \\n / \\"
        except Exception:  # noqa: BLE001 —— 转义还原失败就用原样
            value = raw
        value = str(value).strip()
        if not value:
            continue
        if value.lower() in _TEXT_KEYS_LOWER:        # 字段名本身不是正文
            continue
        if value.lower() in ("name", "arguments", "parameters", "function",
                             "tool_calls", "tool_call", "tool", "tool_name",
                             "query", "input",
                             "args", "type", "id", "role", "finish_reason"):
            continue                                  # 工具调用的键名/枚举值（2026-10-15）
        if _RE_KEY_PATTERN.search(value):
            continue                                  # 值本身又是一段 JSON（比如调用参数）
        if _RE_STRUCTURAL_ONLY.match(value):         # 只剩括号/标点
            continue
        if _RE_TOOL_MARK.search(value):              # 漏出来的标记残片
            continue
        kept.append(value)
    return kept


def reply_segments(raw: str) -> list:
    """AI 回复 → 要发送的段落列表。

    * 解析得出 `{"reply": [...]}` → 逐条返回（**这就是"解析出 reply 数组再逐条发送"**）；
    * 解析不出、但看得出是回复壳 → 只抠正文（抠不出就返回 `[]`：**绝不放壳出去**）；
    * 解析不出、也不是壳 → 先按"双空格 / 空行"保守分段（切出 ≥2 段才用），
      切不出多段就原文当一段发（老行为不变）。
    """
    text = _strip_tool_markers(str(raw or ""))
    if not text.strip():
        return []
    data = parse_reply_json(text)
    if isinstance(data, dict):
        segments = _segments_from_mapping(data)
        if segments:
            return segments
    elif isinstance(data, list):
        segments = _clean_segments(data)
        if segments:
            return segments
    # ---- 兜底 1：是回复壳 → 只准抠正文，绝不发壳 ----
    if looks_like_envelope(text):
        return _salvage_text(text)
    # ---- 兜底 2：不是壳 → 去掉 ``` 外壳；先按"双空格 / 空行"保守分段，
    #      切出 ≥2 段才用，切不出就原文当一段发 ----
    fallback = strip_code_fence(text)
    if not fallback:
        return []
    split = _split_plain_segments(fallback)
    if split:
        return split
    return [fallback.strip()]


# ============================================================================
# 分条发送：把"模型给的几段"变成"真正要发出去的几条"（2026-10-12 老板要求）
# ============================================================================
# 老板原话：**"这个 ai 目前 QQ 上的回复基本都是回答一句一条消息，没有多条式回复
# （不要做死成所有回复都多条回复）"** —— 要像人一样分几条发，但**由内容决定**，
# 而且可配置、可关掉。
#
# 这一层只做**判定**（纯函数、随机数可注入），发送与 sleep 在 brain 那边。
# 判定的三个闸门，按顺序：
#   ① 模式（关 / 自动 / 总是）；
#   ② 通道（能不能自然连发：OneBot/NapCat 能，QQ 官方/微信 iLink 保守合并）；
#   ③ 内容（全都很短、只有语气词 → 合并；一条 → 就一条，**不许硬拆**）。
#
# 借鉴而非照搬：档案插件（liqinghan）里那套"按本条字数算打字延迟"
# （`0.6 + len(seg) * 0.08` 秒、抖动 ×0.8~1.3、上限 3 秒）思路一样，
# 这里统一成**毫秒 + 硬下限 + 硬上限**，全项目只此一份，别再写第二套。

MULTI_MODE_OFF = "off"          # 关：永远一条
MULTI_MODE_AUTO = "auto"        # 自动（默认）：由内容决定
MULTI_MODE_ALWAYS = "always"    # 总是：尽量分条（测试 / 特殊人设；连受限通道也分）
MULTI_MODES = (MULTI_MODE_OFF, MULTI_MODE_AUTO, MULTI_MODE_ALWAYS)

# 每条都短于这个字数 = "都很短" → 合并成一条（老板给的例子：「嗯」「好」「在」）
SHORT_PART_CHARS = 6
# 打字延迟的下限（毫秒）：再短的间隔也不能是 0，否则像机器人在刷屏
PART_DELAY_FLOOR_MS = 300
# 打字延迟的硬上限（毫秒）：配置再大也不许超过它（别出现"等 10 秒才发第二条"）
PART_DELAY_CEILING_MS = 5000
# 随机抖动：避免每条间隔一模一样（机械感）
PART_DELAY_JITTER = (0.85, 1.15)

# 判定"只有语气词"时先去掉的标点/空白
_PART_TRIM = " \t\r\n，。！？；：、,.!?;:~～…—-—\"'“”‘’()（）[]【】<>《》"
# 语气词/极短应答用字（只用于"整段都是这些字"的判定，不做分词）
_FILLER_CHARS = frozenset(
    "嗯哦啊哈呃诶呀噢喔唉嘿嗨唔咳嘛呢吧么了好在行成中是的对可以收到"
    "哎嗷咦嘞咯啦喽哟嘻嘻呜嘛呐嗯")


def normalize_multi_mode(value) -> str:
    """模式规范化：认中文写法，非法值一律回默认 auto（绝不因为写错值就关掉功能）。"""
    raw = str(value or "").strip().lower()
    if raw in MULTI_MODES:
        return raw
    if raw in ("关", "关闭", "单条", "一条", "single", "none"):
        return MULTI_MODE_OFF
    if raw in ("总是", "总是分条", "强制", "force", "all", "on"):
        return MULTI_MODE_ALWAYS
    return MULTI_MODE_AUTO


def _core_text(text: str) -> str:
    """去掉首尾空白与标点后的"实质内容"。"""
    return str(text or "").strip().strip(_PART_TRIM)


def is_short_part(text: str) -> bool:
    """这一段是不是"很短"（去掉标点后不足 SHORT_PART_CHARS 字）。"""
    return len(_core_text(text)) < SHORT_PART_CHARS


def is_filler_part(text: str) -> bool:
    """这一段是不是"只有语气词"（「嗯」「好的」「在的」「哈哈」这类）。"""
    core = _core_text(text)
    if not core or len(core) > SHORT_PART_CHARS:
        return False
    return all(ch in _FILLER_CHARS for ch in core)


def compute_part_delay_ms(text, per_char_ms: int = 80, max_delay_ms: int = 3000,
                          rng=None) -> int:
    """按**这一条自己的字数**估算"打字时间"（毫秒）+ 随机抖动，再夹到 [下限, 上限]。

    语义：发完上一条之后，人要"把这一条打完"才发得出去 —— 所以按**即将发出的那条**
    的字数算。返回值保证 `PART_DELAY_FLOOR_MS <= v <= min(配置上限, 硬顶)`，
    即**不会 0、也不会等 10 秒**。
    """
    pick = rng if rng is not None else random
    try:
        per_char = max(0, int(per_char_ms))
    except (TypeError, ValueError):
        per_char = 80
    try:
        cap = int(max_delay_ms)
    except (TypeError, ValueError):
        cap = 3000
    cap = max(PART_DELAY_FLOOR_MS, min(PART_DELAY_CEILING_MS, cap))
    raw = len(str(text or "")) * per_char
    value = int(round(raw * pick.uniform(*PART_DELAY_JITTER)))
    return max(PART_DELAY_FLOOR_MS, min(cap, value))


_TAIL_PUNCT = "。！？!?…~～，,、；;：:"      # 上一段已收尾 → 不再补分隔符


def _join_single_parts(parts: list, sep: str = "。") -> str:
    """**所有"并成一条"的场合**共用这个连接：上一段没收尾就补个分隔符。

    直接 "".join 会把「怎么啦这是」+「给你挑了首…」糊成
    「怎么啦这是给你挑了首…」；补个句号读起来才像人话。

    2026-10-15：以前只有"本轮强制单条"那条路用了它，其余合并（模式=关、
    通道受限、都太短/都是语气词、超条数上限）还是 `"".join(parts)` ——
    官方通道 / 微信这些**必须合并**的通道上，模型给 ["在的", "你说吧"]
    就发成「在的你**说吧」，「的你说」看着像错字。现在统一走这里。

    `sep="，"` 用于"只有语气词"那种合并（「嗯」「好」「在」→「嗯，好，在」）：
    逗号是句内停顿，比句号更像人随口应的话。
    """
    out = ""
    for part in parts or ():
        p = str(part or "").strip()
        if not p:
            continue
        if out and out[-1] not in _TAIL_PUNCT:
            out += sep
        out += p
    return out


def plan_parts(segments, *, mode: str = MULTI_MODE_AUTO, max_parts: int = 3,
               max_part_chars: int = 500, channel_allows_multi: bool = True,
               channel_note: str = "", per_char_ms: int = 80,
               max_delay_ms: int = 3000, rng=None,
               force_single: bool = False, force_single_reason: str = ""):
    """模型给的几段 → **真正要发出去的几条**。返回 `(parts, info)`。

    `info` 是判定依据（段数 / 字数 / 模式 / 通道 / 是否合并 / 原因 / 每段间隔毫秒），
    调用方**必须**把它打进一行日志 —— 沿用"判定必须留一行日志"的规矩。
    """
    mode = normalize_multi_mode(mode)
    try:
        cap_parts = max(1, int(max_parts))
    except (TypeError, ValueError):
        cap_parts = 3
    try:
        cap_chars = int(max_part_chars)
    except (TypeError, ValueError):
        cap_chars = 0                       # 非数字 → 当"不限制"

    src = [str(s).strip() for s in (segments or []) if str(s or "").strip()]
    info = {
        "mode": mode,
        "in_parts": len(src),
        "in_lengths": [len(s) for s in src],
        "channel_allows_multi": bool(channel_allows_multi),
        "channel_note": str(channel_note or ""),
        "force_single": bool(force_single),
        "max_parts": cap_parts,
        "max_part_chars": cap_chars,
        "merged": False,
        "merged_reason": "",
        "capped": False,
        "truncated": [],                    # [(原字数, 保留字数)]
        "out_parts": 0,
        "out_lengths": [],
        "delays_ms": [],
    }
    if not src:
        info["merged_reason"] = "模型没给出可发的正文"
        return [], info

    # ---- 闸门⓪（2026-10-13）：本轮**只发一条** ----
    # 用在两种场合：① 空 @ 的轻唤；② 本轮动过工具（卡片/动作已经单独发出去过），
    # 再拆成两三条就变成"每个功能各说一句"的乱（老板实测："答得很多很乱"）。
    forced = bool(force_single) and len(src) > 1
    if forced:
        parts = [_join_single_parts(src)]
        info["merged"] = True
        info["merged_reason"] = "本轮只发一条（%s）" % (force_single_reason or "指定")
    else:
        parts = list(src)
    # ---- 闸门① 模式；闸门② 通道；闸门③ 内容 ----
    if forced:
        pass                       # 已经合并成一条了，别再被后面的闸门改判/覆盖原因
    elif mode == MULTI_MODE_OFF:
        parts = [_join_single_parts(parts)]
        info["merged"] = True
        info["merged_reason"] = "模式=关（永远一条）"
    elif not channel_allows_multi and mode != MULTI_MODE_ALWAYS:
        parts = [_join_single_parts(parts)]
        info["merged"] = True
        info["merged_reason"] = "通道受限（%s）→ 保守合并成一条" % (
            info["channel_note"] or "该通道没标记为可连发")
    elif len(parts) == 1:
        info["merged_reason"] = "模型只给了一条（不许硬拆）"
    elif mode == MULTI_MODE_AUTO and all(is_filler_part(p) for p in parts):
        # 「嗯」「好」「在」这种纯语气词：用逗号连（"嗯，好，在"），别用句号
        parts = [_join_single_parts(parts, sep="，")]
        info["merged"] = True
        info["merged_reason"] = "只有语气词 → 合并成一条"
    elif mode == MULTI_MODE_AUTO and all(is_short_part(p) for p in parts):
        parts = [_join_single_parts(parts)]
        info["merged"] = True
        info["merged_reason"] = "每条都很短（<%d 字）→ 合并成一条" % SHORT_PART_CHARS

    # ---- 条数上限：多出来的并进最后一条（不丢内容，也不刷屏）----
    if len(parts) > cap_parts:
        kept = parts[:cap_parts - 1] + [_join_single_parts(parts[cap_parts - 1:])]
        extra = "超过上限 %d 条 → 尾部合并" % cap_parts
        info["merged_reason"] = ("%s；%s" % (info["merged_reason"], extra)
                                 if info["merged_reason"] else extra)
        info["capped"] = True
        parts = kept

    # ---- 单条字数上限：超了**截断**（绝不硬拆成更多条）----
    if cap_chars > 0:
        fixed = []
        for p in parts:
            if len(p) > cap_chars:
                keep = max(1, cap_chars - 1)
                info["truncated"].append((len(p), keep + 1))
                fixed.append(p[:keep].rstrip() + "…")
            else:
                fixed.append(p)
        parts = fixed

    info["out_parts"] = len(parts)
    info["out_lengths"] = [len(p) for p in parts]
    # 第 i 条（i>=1）之前的间隔 = 按**第 i 条自己**的字数估的打字时间
    info["delays_ms"] = [
        compute_part_delay_ms(parts[i], per_char_ms, max_delay_ms, rng)
        for i in range(1, len(parts))
    ]
    return parts, info


def describe_plan(info: dict) -> str:
    """把判定依据拼成**一行**日志（沿用"判定必须留一行日志"的规矩）。

        grep '分条判定' nonebot.log
    """
    info = info or {}
    merged = bool(info.get("merged"))
    conclusion = ("分 %d 条，每条字数=%s，间隔毫秒=%s"
                  % (info.get("out_parts") or 0, info.get("out_lengths") or [],
                     info.get("delays_ms") or []))
    if not merged and (info.get("out_parts") or 0) <= 1:
        conclusion = "发 1 条，字数=%s" % (info.get("out_lengths") or [])
    line = ("input: 段数=%s 每段字数=%s 通道可连发=%s%s 模式=%s 上限=%s条/%s字"
            "｜结论: %s｜是否合并=%s"
            % (info.get("in_parts"), info.get("in_lengths"),
               "是" if info.get("channel_allows_multi") else "否",
               ("（%s）" % info["channel_note"]) if info.get("channel_note") else "",
               info.get("mode"), info.get("max_parts"), info.get("max_part_chars"),
               conclusion, "是" if merged else "否"))
    if info.get("merged_reason"):
        line += "｜原因=%s" % info["merged_reason"]
    if info.get("truncated"):
        line += "｜截断=%s" % ["原 %d 字 → 保留 %d 字" % (a, b)
                               for a, b in info["truncated"]]
    return line
