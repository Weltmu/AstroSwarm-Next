# -*- coding: utf-8 -*-
"""把官方消息里的"非纯文本"部分翻译成 AI 看得懂的文字。

老板 2026-10-01 提的，2026-10-03 追加：群里发图片 / 引用 / **卡片（B站、小程序、音乐分享）**
/ 视频 / 文件时，AI 以前只看到一句纯文本，其余全被丢掉 —— 答起来自然驴唇不对马嘴。

这里只做**纯函数**（不依赖 nonebot），输入官方事件的 content / msg_elements / attachments，
输出一段给 AI 看的上下文；适配器负责把事件喂进来。

关于卡片：nonebot-adapter-qq 1.7.3 的 QQMessage 里**没有 ark_data 字段**，但 `content`
本身就是服务端给的原始 JSON 字符串 —— 所以卡片不靠适配器的字段，靠我们自己解析
content（ark_data / embed / prompt / kv / jump_url …）。解析不出来就如实说"这是个卡片"，
绝不编内容。
"""
from __future__ import annotations

import json

# 官方 message_type：102 = 聊天记录，103 = 引用（见 bot.q.qq.com 文档）
TYPE_CHAT_RECORD = 102
TYPE_QUOTE = 103

ATTACH_LABEL = {
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "file": "文件",
}

# 卡片 JSON 里值得抄出来的键（值必须是字符串，别把结构体当文字）
_CARD_TEXT_KEYS = (
    "title", "desc", "description", "prompt", "tag", "name", "nickname",
    "jump_url", "url", "music_url", "source", "app", "app_name", "subtitle",
    "summary", "content", "text", "value",
)
# 出现这些键就认为"这是一张卡片/分享"（而不是普通文本消息）
_CARD_MARKERS = ("ark_data", "ark", "embed", "prompt", "news", "music",
                 "template", "card", "mini_app", "share", "jump_url")
_CARD_SOURCES = {
    "bili": "B站", "bilibili": "B站", "douyin": "抖音", "kuaishou": "快手",
    "y.qq.com": "QQ音乐", "qqmusic": "QQ音乐", "music.163": "网易云音乐",
    "netease": "网易云音乐", "weishi": "微视", "xiaohongshu": "小红书",
    "zhihu": "知乎", "weibo": "微博", "github": "GitHub", "mp.weixin": "微信公众号",
    "taobao": "淘宝", "jd.com": "京东", "pinduoduo": "拼多多", "meituan": "美团",
}
_MAX_PART = 160

# ark 卡片的井号标记 → 我们统一的槽位
_MARK_SLOTS = {
    "TITLE": "title", "TITLE_NAME": "title", "PROMPT": "title", "MUSIC_TITLE": "title",
    "DESC": "desc", "DESCRIPTION": "desc", "SUMMARY": "desc", "META": "desc",
    "SINGER": "desc", "ALBUM": "desc",
    "JUMP_URL": "jump_url", "URL": "jump_url", "MUSIC_URL": "jump_url",
    "FROMURL": "jump_url", "LINK": "jump_url",
    "APP": "source", "SOURCE": "source", "FROM": "source", "TAG": "source",
    "COVER": "cover", "IMAGE": "cover", "PREVIEW": "cover",
}


def _author_name(author) -> str:
    for attr in ("username", "nickname", "user_openid", "member_openid", "id"):
        value = getattr(author, attr, None)
        if value:
            return str(value)[:24]
    return ""


def _clip(text: str, limit: int = _MAX_PART) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _collect(node, found: dict, depth: int = 0) -> None:
    """深度遍历卡片 JSON，把「看着像人话」的字段收进 found（先到先得，不覆盖）。

    官方 ark 卡片的正文长这样：``{"kv": [{"key": "#TITLE#", "value": "..."}]}`` ——
    字段名在 **key 的井号标记**里，不在 JSON 的键上，所以这里两种都要认。
    """
    if depth > 6 or node is None:
        return
    if isinstance(node, dict):
        # ① ark 的 kv 写法：#TITLE# / #DESC# / #META# / #JUMP_URL# …
        mark = str(node.get("key") or "").strip().strip("#").upper()
        value = node.get("value")
        if mark and isinstance(value, str) and value.strip():
            slot = _MARK_SLOTS.get(mark)
            if slot and slot not in found:
                found[slot] = _clip(value)
            elif slot and found.get(slot) != _clip(value):
                # 同一个槽位已经有人了（比如 #DESC# 和 #META# 都算说明）：
                # 别丢，塞进"其他"里，AI 自己判断
                found.setdefault(f"kv:{mark.lower()}", _clip(value))
            elif not slot and len(found) < 12:
                found.setdefault(f"kv:{mark.lower()}", _clip(value))
        for key, item in node.items():
            low = str(key).lower()
            if isinstance(item, str) and item.strip() and low in _CARD_TEXT_KEYS:
                found.setdefault(low, _clip(item))
            else:
                _collect(item, found, depth + 1)
    elif isinstance(node, (list, tuple)):
        for item in node[:12]:
            _collect(item, found, depth + 1)


def _guess_source(found: dict) -> str:
    blob = " ".join(str(v).lower() for v in found.values())
    for key, name in _CARD_SOURCES.items():
        if key in blob:
            return name
    return ""


def describe_card(content) -> str:
    """原始 content（JSON 字符串或 dict）→ 卡片描述；不是卡片就返回空串。"""
    data = content
    if isinstance(data, str):
        raw = data.strip()
        if not raw.startswith(("{", "[")):
            return ""                      # 普通文本，交给正文处理
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return ""
    if not isinstance(data, dict):
        return ""
    keys = {str(k).lower() for k in data.keys()}
    if not any(marker in keys for marker in _CARD_MARKERS):
        return ""

    found: dict = {}
    _collect(data, found)
    title = found.get("title") or found.get("name") or ""
    desc = found.get("desc") or found.get("description") or found.get("summary") or ""
    link = found.get("jump_url") or found.get("url") or found.get("music_url") or ""
    source = _guess_source(found)

    bits = []
    head = "对方分享了一张卡片"
    if source:
        head += f"（{source}）"
    bits.append(head)
    if title:
        bits.append(f"标题：{title}")
    if desc and desc != title:
        bits.append(f"说明：{desc}")
    if link:
        bits.append(f"链接：{link}")
    # ark 里还有别的 kv（时长、UP 主之类）：一并抄给 AI，别丢信息
    extra = [f"{k.split(':', 1)[1]}={v}" for k, v in found.items() if k.startswith("kv:")]
    if extra:
        bits.append("其他：" + "，".join(extra[:4]))
    if len(bits) == 1:
        # 卡片结构认出来了但一个字都抄不到：如实说，别编
        bits.append("卡片里的文字没解析出来；可以请对方把重点打一句，"
                    "或者我试着用 fetch_text 抓链接正文")
    return "[" + " · ".join(bits) + "]"


def describe_elements(elements) -> str:
    """引用 / 聊天记录 → 给 AI 看的一行文字（没有就返回空串）。"""
    lines = []
    for element in (elements or []):
        content = str(getattr(element, "content", "") or "").strip()
        card = describe_card(content)
        if card:
            lines.append(card)
            continue
        who = _author_name(getattr(element, "author", None))
        head = "对方引用了这条消息" if not who else f"对方引用了 {who} 的消息"
        if not content:
            # 2026-10-15：被引用那条如果是**图片/语音/视频/文件**，正文是空的，
            # 信息全在 `element.attachments` 里 —— 以前 content 一空就 continue，
            # 于是"引用一张图 + 问这是什么"机器人**完全看不到那张图**，只能瞎猜或
            # 说看不到。这里补一句占位描述（平台不给我们被引用图的可用地址，
            # 所以只能说明"有一张图"，绝不编内容）。
            quoted_att = describe_attachments(getattr(element, "attachments", None))
            if quoted_att:
                lines.append(f"[{head}（媒体内容）] {quoted_att}")
            continue
        kind = getattr(element, "message_type", None)
        if kind == TYPE_QUOTE:
            lines.append(f"[{head}] {content}")
        elif kind == TYPE_CHAT_RECORD:
            lines.append(f"[聊天记录] {content}")
        else:
            lines.append(f"[引用的消息] {content}")
    return "\n".join(lines)


def _describe_one_attachment(item) -> str:
    raw = str(getattr(item, "content_type", "") or getattr(item, "type", "") or "").lower()
    label = ""
    for key, name in ATTACH_LABEL.items():
        if key in raw:
            label = name
            break
    url = str(getattr(item, "url", "") or "")
    if not label:
        label = "图片" if url.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) else "附件"
    extra = []
    name = str(getattr(item, "filename", "") or "").strip()
    if name:
        extra.append(name[:40])
    size = getattr(item, "size", None)
    try:
        if size:
            kb = float(size) / 1024
            extra.append(f"{kb / 1024:.1f}MB" if kb >= 1024 else f"{int(kb)}KB")
    except (TypeError, ValueError):
        pass
    width = getattr(item, "width", None)
    height = getattr(item, "height", None)
    if width and height:
        extra.append(f"{width}×{height}")
    suffix = f"（{'，'.join(extra)}）" if extra else ""
    # 视频/文件看不懂内容 —— 如实说，别让 AI 假装看过
    if label == "视频":
        return f"{label}{suffix}[看不了画面，需要对方说一句重点]"
    if label == "文件":
        return f"{label}{suffix}[正文需要对方发文字或让我抓链接]"
    if label == "语音":
        return f"{label}{suffix}[听不了语音，需要对方打字]"
    return f"{label}{suffix}"


def describe_attachments(attachments) -> str:
    """附件（图片/语音/视频/文件）→ 带文件名、体积、尺寸的标记；图片文字识别由调用方另补。"""
    parts = []
    for item in (attachments or []):
        parts.append(_describe_one_attachment(item))
    if not parts:
        return ""
    counted = {}
    for part in parts:
        counted[part] = counted.get(part, 0) + 1
    body = "、".join(f"{n} 个{p}" if n > 1 else p for p, n in counted.items())
    return "[收到" + body + "]"


def rich_context(event) -> str:
    """官方事件 → 给 AI 的补充上下文（卡片/引用/聊天记录/附件），没有就返回空串。"""
    parts = [
        describe_card(getattr(event, "content", None)),
        describe_elements(getattr(event, "msg_elements", None)),
        describe_attachments(getattr(event, "attachments", None)),
    ]
    return "\n".join(p for p in parts if p)
