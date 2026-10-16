# -*- coding: utf-8 -*-
"""提示词里的**上文窗口**：什么时候该看上文、能看多久以前的（老板 2026-10-11 口径）。

事故（老板原话转述）：
    "一个小时前群里聊了一些内容，机器人没有回。然后我**只 @ 了它一下、什么都没说** ——
     本意是叫醒它，结果它**把一小时前的内容当成问题回答了，还 @ 了我**。"

两个毛病：①"空 @"被当成"回答上文"的信号；②上文回溯**没有时间上限**。

定下来的口径（按这个做，别自行发挥）：

1. **空 @（@ 了但正文为空 / 只有标点或表情）** → **绝不回答上文**：
   * 只回一句**轻唤**（人设语气，如「嗯？」「在的」），内容里**不得出现任何上文信息**；
   * **不带 @**（群里一条普通消息就够，不用点人）；
   * 提示词里写成「有人叫了你一声，没说要什么」这种**没有历史**的上下文。
2. **上文只在两种情况下才用**：
   * 本条是**引用/回复**某条消息 → 用**被引用的那条**（+ 其后少量），话题由被引用那条决定；
   * 本条有**实质内容** → 正常处理，可带近期上下文。
3. **上文回溯必须有时间上限**：窗口 = 「最近 N 条」**且**「距现在 ≤ T 分钟」；
   超时的旧消息**不进提示词**，除非被显式引用。默认 **T = 5 分钟**，可配置。
4. **@ 的策略**：群里"被 @ 但没实质内容" → **不加 @**；正常回答时是否 @ 可配置
   （默认沿用现有行为）。

5. **2026-10-13 修订（老板实测）**：空 @ **且刚刚（≤2 分钟）有人说过话**时，
   带一个**微窗口**（最近 2 分钟、最多 4 条）——
   常见用法是"先把请求打出来、再 @ 一下叫她"；旧口径 0 条上文时它只会回
   「在的呢」，请求被晾着、看着还像在答别的时空线（私聊）的话。
   微窗口只认最近 2 分钟：接得住"请求 + @ 一下"，又不会把一小时前的旧话题
   翻出来（那是 2026-10-11 的老回归，规则 1/3 不动）。

这个文件**不依赖 nonebot**（和 group_policy / session_policy / target_guard 一样），
所以测试可以直接按路径加载，纯函数、可断言。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---- 默认值（可被机器人侧配置覆盖，改完立即生效 —— 那些配置本来就是热读的）----
DEFAULT_WINDOW_MINUTES = 5
DEFAULT_HISTORY_LIMIT = 20
MAX_WINDOW_MINUTES = 24 * 60        # 上限一天，防止手滑填个 100000 把窗口关掉

# 空 @ 的两种处理
EMPTY_AT_GREET = "greet"            # 轻唤（默认）：回一句人设语气的短句
EMPTY_AT_SILENT = "silent"          # 不回复
EMPTY_AT_ACTIONS = (EMPTY_AT_GREET, EMPTY_AT_SILENT)

# 空 @ 的**微窗口**（2026-10-13 老板实测："刚发了请求没 @、随后 @ 一下，
# 她只回「在的呢」"）：只看刚刚这几分钟、最多几条。
EMPTY_AT_LOOKBACK_MINUTES = 2
EMPTY_AT_LOOKBACK_LIMIT = 4

# 空 @ 的提示词：**明确没有历史**，别让模型自己去翻上文。
# 2026-10-12 老板实测：只 @ 一下没打字时，它回的是「下午好呀，都在忙什么呢」——
# 像在**跟全群打招呼**，很假。真人被人在群里戳一下，通常就一句「怎么了」「在」
# 或者带点性格的短反应，**不会**对着一群人问好，也不会问"都在忙什么"。
GREET_PROMPT = (
    "（有人只叫了你一声、没说要什么，不要回答任何之前的内容）"
    "就像有人在群里戳了你一下：**回一句很短、很自然的话**就行 —— "
    "例如「怎么了」「在，说」「干嘛呀」，可以带点你的人设脾气。"
    "**千万别**对着一群人打招呼或问好（别说「大家下午好」「都在忙什么呢」这种），"
    "也别长篇大论；一两句、几个字，够了。")

# 空 @ 且微窗口里**真有刚刚的话**时用这版：先看最近那条要不要接，不接就短答。
GREET_PROMPT_RECENT = (
    "（有人只叫了你一声、没说要什么。上面只有刚才一两分钟的几条消息 —— "
    "如果最近的那条明显是在跟你说话、或在等你回应（比如刚提了个请求），"
    "**就直接把它办了**，不要再问「怎么了」；"
    "如果不像是在等你，就只回一句很短的话（「怎么了」「在，说」这种）。"
    "不要翻更早的旧话题，也别对着一群人打招呼。）")

# 引用被解析出来时，插在历史最前面的那一行
QUOTED_HEAD = "（这条是对下面这条的回复）"

# 有引用段但正文取不到时（quoted_state == "unresolved"），由 **brain 层**注入的一句提醒。
# 注意：它不是引用内容 —— 上下文照旧一条都不带（见 select_history），只是让模型别装没听见、
# 如实说一句"看不到"。（2026-10-12 老板：取不到正文时它只会回「在呢」。）
QUOTED_UNRESOLVED_HINT = (
    "（他引用了一条消息，但这边取不到那条的内容 —— "
    "如实说一句「你引用的那条我看不到，能再说一次吗」就行，别猜）")

# 被引用那条之后最多再带几条（"其后少量"）
QUOTED_TAIL_LIMIT = 3


# ---------------------------------------------------------------- 空 @ / 实质内容

# CQ 码、@ 段、表情段、空白、常见标点
_RE_CQ = re.compile(r"\[CQ:[^\]]*\]")
_RE_AT_NAME = re.compile(r"@[^\s,，。！？!?]+")
_RE_SPACE = re.compile(r"[\s\u3000]+")
# 标点 / 符号（中英文 + 常见省略号破折号）
_RE_PUNCT = re.compile(r"[.,;:!?~`'\"“”‘’()\[\]{}<>《》〈〉、，。；：！？…—\-_=+*/\\|^%$#@&]+")
# 表情 / 颜文字 / emoji（含常见面孔与符号块）
_RE_FACE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
    "\u2190-\u21FF\u2B00-\u2BFF]+")
_RE_FACE_WORD = re.compile(r"\[[^\]]{0,12}(?:表情|face|emoji|动画表情|戳一戳)[^\]]{0,12}\]")

# 「实义字符」= 中日韩文字、字母、数字。
# **这是空 @ 判定的唯一依据**：只要还剩一个实义字符，就**必须**当正常提问处理。
# 2026-10-11 回归：老板 @ 它问「星群是什么」被判成空 @、只回了「在呢」。
# 教训：空 @ 的判定要 **fail-open**（拿不准就当有内容），宁可多回一次，
# 也不能把真问题当成"叫一声"。
_RE_MEANINGFUL = re.compile(
    r"[0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff]")


def strip_noise(text: str) -> str:
    """去掉 CQ 码 / @ 名字 / 表情 / 标点 / 空白，只留下"人真正说了什么"。"""
    s = str(text or "")
    s = _RE_CQ.sub(" ", s)
    s = _RE_FACE_WORD.sub(" ", s)
    s = _RE_AT_NAME.sub(" ", s)
    s = _RE_FACE.sub(" ", s)
    s = _RE_PUNCT.sub(" ", s)
    s = _RE_SPACE.sub("", s)
    return s.strip()


def has_meaningful_char(text: str) -> bool:
    """去掉 @ 段（CQ 码）/ 表情 / 空白之后，**还有没有实义字符**。

    只要有一个（中文、英文、数字…）就算"真的有内容"。
    注意：**必须先去掉 CQ 码** —— `[CQ:at,qq=1437808448]` 里本身含 ASCII 字母，
    不去掉的话"纯 @"会被误判成"有内容"（我第一版就是这么错的，测试当场抓到）。
    """
    s = _RE_CQ.sub(" ", str(text or ""))
    s = _RE_FACE_WORD.sub(" ", s)
    s = _RE_FACE.sub(" ", s)
    return bool(_RE_MEANINGFUL.search(s))


def is_empty_at(text: str) -> bool:
    """这条消息是不是"空 @"（@ 了但**没有任何实义内容**）。

    收紧后的定义（fail-open，宁可当有内容）：

    * 先去掉 @ 段（CQ 码）、表情段、空白；
    * 剩下的部分如果没有**任何实义字符**（中日韩文字 / 字母 / 数字），就是空 @；
    * 只要还剩**一个实义字符** → **不是**空 @ → 按正常提问处理；
    * 输入是 None / 空串 / 只有空白 → 空 @。

    这样「@bot 星群是什么」永远是"有内容"，「@bot」/「@bot ？」/「@bot 🙂」才是空 @。
    """
    if not str(text or "").strip():
        return True
    s = _RE_CQ.sub(" ", str(text or ""))
    s = _RE_FACE_WORD.sub(" ", s)
    s = _RE_FACE.sub(" ", s)
    if not s.strip():
        return True
    return not _RE_MEANINGFUL.search(s)


def normalize_empty_at_action(value) -> str:
    """空 @ 行为规范化：认不出的一律回默认的轻唤。"""
    raw = str(value or "").strip().lower()
    if raw in EMPTY_AT_ACTIONS:
        return raw
    # 认几个中文写法，省得界面上写错就静默变了行为
    if raw in ("不回复", "不回", "静默", "silent", "none", "off"):
        return EMPTY_AT_SILENT
    if raw in ("轻唤", "回应", "greet", "hello", "on"):
        return EMPTY_AT_GREET
    return EMPTY_AT_GREET


def clamp_window_minutes(value, default: int = DEFAULT_WINDOW_MINUTES) -> int:
    """上文回溯上限（分钟）：夹到 1~1440；非法值回默认 5。"""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return int(default)
    if minutes <= 0:
        return 1
    return min(MAX_WINDOW_MINUTES, minutes)


# ---------------------------------------------------------------- 上文窗口

# 引用段状态（describe 用同一份口径）
QUOTED_NONE = "none"            # 本条**没有引用段**（@ 段不算引用）→ 正常窗口
QUOTED_RESOLVED = "resolved"    # 有引用段且取到了正文 → 用被引用的那条
QUOTED_UNRESOLVED = "unresolved"  # 有引用段但取不到正文 → 不带上文（这条规则不能改坏）


def normalize_quoted(value) -> Optional[Dict[str, Any]]:
    """把适配器给的 `extra["quoted"]` 归一成"真正的引用段"或 `None`。

    **2026-10-11 回归的正面修复**：适配器对"本条没有 reply 段"的消息返回的是 `{}`，
    而下游按 `is not None` 判 → **只 @ 了一下的正常消息被当成"引用拿不到正文"**，
    20 条上文全被丢掉，机器人于是对所有问题都只能瞎答。

    口径（与老板一致）：
      * `None` / `{}` / 不是 dict          → `None` = **没有引用段** → 正常窗口；
      * 有 `content` 正文                   → 原样返回 = **有引用段，用被引用那条**；
      * 有 dict 但 `content` 为空（有引用段、正文没取到）→ 原样返回 =
        **有引用段但拿不到正文** → 交给 select_history 判"不带上文"（这条规则别改坏）。
    """
    if not isinstance(value, dict):
        return None
    if not value:                       # 空字典 = 适配器说"本条没有引用段"
        return None
    return value


def quoted_state(value) -> str:
    """归一后的引用段状态（写日志用）。"""
    quoted = normalize_quoted(value)
    if quoted is None:
        return QUOTED_NONE
    if str(quoted.get("content") or "").strip():
        return QUOTED_RESOLVED
    return QUOTED_UNRESOLVED


def _ts_of(row: Dict[str, Any]) -> float:
    """消息时间戳；**没有 ts 的老数据按 0 处理**（= 很久以前，不进窗口）。

    为什么要这样定：`aichat_context.json` 是历史文件，升级前存进去的消息没有 ts。
    把它们当成"刚刚"会立刻重现老板报的 bug（把一小时前的当上文），所以按最保守的来：
    没有时间信息 = 不当作近期上文。被显式引用那条不受这个限制。
    """
    try:
        return float(row.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0


def select_history(messages: List[Dict[str, Any]], *, now: float,
                   minutes: int = DEFAULT_WINDOW_MINUTES,
                   limit: int = DEFAULT_HISTORY_LIMIT,
                   quoted: Optional[Dict[str, Any]] = None,
                   quoted_tail: int = QUOTED_TAIL_LIMIT
                   ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """挑出"能进提示词的历史"。返回 (选中的历史, 诊断信息)。

    规则（严格按老板口径）：

    * **显式引用优先**：`quoted` 有正文时 → 只用"被引用那条 + 其后最多 quoted_tail 条"，
      **不受 T 限制**（这是唯一允许翻旧账的情况，证明不是一刀切不看上文）；
    * `quoted` 存在但**拿不到正文** → **不带上文**（宁可不答，也不猜旧话题）；
    * 否则 → 只留 `ts >= now - minutes*60` 的消息，再按 `limit` 取最近 N 条。

    **2026-10-11 回归（关键）**：`quoted` 只在**本条真的有引用段**时才该传进来。
    以前适配器对"没有 reply 段"的消息返回 `{}`（空字典），而这里是按
    `quoted is not None` 判的 —— 于是**只 @ 了一下的正常消息也被当成"引用拿不到正文"**，
    上文被整批丢掉，机器人对所有问题都只能瞎答。
    现在一律按**真值**判：`None` / `{}` = 没有引用段 → 正常窗口。

    诊断信息里给出「实际注入条数 / 时间范围 / 被丢弃条数」—— 验证时要打出来看。
    """
    rows = list(messages or [])
    minutes = clamp_window_minutes(minutes)
    cutoff = float(now) - int(minutes) * 60
    info: Dict[str, Any] = {
        "mode": "", "minutes": minutes, "cutoff": cutoff,
        "total": len(rows), "selected": 0, "dropped": 0,
        "oldest_ts": 0.0, "newest_ts": 0.0, "span_minutes": 0.0,
        # 引用段状态：none=没有引用段 / resolved=有且取到正文 / unresolved=有但取不到
        "quoted_state": "none", "quoted_reason": "",
    }

    if quoted:
        body = str(quoted.get("content") or "").strip()
        if not body:
            info["mode"] = "quoted-unresolved"
            info["quoted_state"] = "unresolved"
            info["quoted_reason"] = str(
                quoted.get("reason") or "有引用段，但拿不到被引用那条的正文")
            info["dropped"] = len(rows)
            return [], info
        q_ts = _ts_of(quoted)
        picked = [dict(quoted)]
        if q_ts:
            tail = [r for r in rows if _ts_of(r) > q_ts]
            tail.sort(key=_ts_of)
            picked.extend(dict(r) for r in tail[:max(0, int(quoted_tail))])
        else:
            # 被引用那条的**时间不知道**（平台只给了 message_id，拿不到时间）：
            # 那就把"窗口内的近期消息"当作它之后的少量 —— 话题仍由被引用那条决定。
            tail = [r for r in rows if _ts_of(r) >= cutoff]
            picked.extend(dict(r) for r in tail[-max(0, int(quoted_tail)):])
        info["mode"] = "quoted"
        info["quoted_state"] = "resolved"
    else:
        picked = [dict(r) for r in rows if _ts_of(r) >= cutoff]
        info["mode"] = "recent"

    if limit and len(picked) > int(limit):
        picked = picked[-int(limit):]
    info["selected"] = len(picked)
    info["dropped"] = max(0, len(rows) - len([r for r in picked if r in rows])) \
        if not quoted else max(0, len(rows) - (len(picked) - 1))
    stamps = [_ts_of(r) for r in picked if _ts_of(r)]
    if stamps:
        info["oldest_ts"] = min(stamps)
        info["newest_ts"] = max(stamps)
        info["span_minutes"] = round((max(stamps) - min(stamps)) / 60.0, 1)
    return picked, info


def render_history(picked: List[Dict[str, Any]],
                   quoted: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """把选中的历史渲染成给模型的消息列表。

    `quoted` 按**真值**判：`None` / `{}` 都算"没有引用段"（见 select_history 的说明）。
    """
    out: List[Dict[str, str]] = []
    has_quote = bool(quoted) and bool(str(quoted.get("content") or "").strip())
    if has_quote:
        out.append({"role": "system",
                    "content": QUOTED_HEAD + "\n" + str(quoted["content"]).strip()})
    for row in picked:
        role = str(row.get("role") or "user")
        content = str(row.get("content") or "")
        if has_quote and _same(row, quoted):
            continue          # 被引用那条已经单独放前面了，别重复
        out.append({"role": role, "content": content})
    return out


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return (str(a.get("content")) == str(b.get("content"))
            and _ts_of(a) == _ts_of(b))


def quoted_state_of(info: Dict[str, Any]) -> str:
    """引用段状态：none（没有引用段）/ resolved（有且取到正文）/ unresolved（有但取不到）。

    2026-10-11 老板要求"把这条判定打上明确的日志"——今天就是因为看不出
    **为什么丢上下文**才拖了这么久。这个函数是那行日志的唯一口径。
    """
    state = str(info.get("quoted_state") or "")
    if state in ("none", "resolved", "unresolved"):
        return state
    mode = str(info.get("mode") or "")
    if mode == "quoted-unresolved":
        return "unresolved"
    return "resolved" if mode == "quoted" else "none"


def describe(info: Dict[str, Any]) -> str:
    """一行诊断（回报里要贴的就是它）：**引用段判定** + 实际注入条数与时间范围。

    三种开头，一眼就能看出"上下文为什么被丢"：
      * `引用段=无 → 正常窗口`      —— 本条没有引用段（@ 段不算引用）
      * `引用段=有 → 用被引用的那条` —— 真的有引用，且取到了正文
      * `引用段=有（取不到正文）→ 不带上文｜原因：…` —— 有引用但拿不到正文
    """
    state = quoted_state_of(info)
    if state == "unresolved":
        return ("上文窗口：引用段=有（取不到正文）→ **不带上文**（丢弃 %d 条）｜原因：%s"
                % (info.get("dropped", 0),
                   info.get("quoted_reason") or "有引用段，但拿不到被引用那条的正文"))
    head = ("上文窗口：引用段=无 → 正常窗口" if state == "none"
            else "上文窗口：引用段=有 → 用被引用的那条")
    return ("%s｜mode=%s 上限 %d 分钟｜原始 %d 条 → 注入 %d 条（丢弃 %d）｜"
            "时间范围 %s ~ %s（跨度 %.1f 分钟）｜cutoff=%s"
            % (head, info.get("mode"), info.get("minutes"), info.get("total"),
               info.get("selected"), info.get("dropped"),
               _fmt(info.get("oldest_ts")), _fmt(info.get("newest_ts")),
               info.get("span_minutes", 0.0), _fmt(info.get("cutoff"))))


def _fmt(ts) -> str:
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    import time as _t
    return _t.strftime("%H:%M:%S", _t.localtime(ts))
