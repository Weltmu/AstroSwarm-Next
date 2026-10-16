# -*- coding: utf-8 -*-
"""把官方通道的英文事件名翻成人话，顺便认出"这条日志是不是官方通道的"。

为什么要单独一个模块：日志页（桌面端）和控制台（Linux）都要用，
而且排查"群里 @ 它不回"时，用户看到的必须是人话 ——
`GROUP_MESSAGE_CREATE` 这种字样对 0 基础用户等于天书。

纯函数，不依赖 PySide6 / nonebot，两端都能用。
"""
from __future__ import annotations

import re

# 官方事件 → 人话（查不到的原样返回，不猜）
EVENT_LABELS = {
    "READY": "连接成功，机器人上线了",
    "C2C_MESSAGE_CREATE": "有人私聊我",
    "GROUP_AT_MESSAGE_CREATE": "群里有人 @ 我",
    "GROUP_MESSAGE_CREATE": "群里收到一条消息（全量模式：没 @ 也收）",
    "GROUP_ADD_ROBOT": "我被拉进了一个群",
    "GROUP_DEL_ROBOT": "我被移出了一个群",
    "INTERACTION_CREATE": "有人点了消息里的按钮",
    "MESSAGE_AUDIT_PASS": "平台审核通过",
    "MESSAGE_AUDIT_REJECT": "平台把这条拦下了（审核不通过）",
    "GUILD_CREATE": "频道相关事件（频道场景）",
    "AT_MESSAGE_CREATE": "频道里有人 @ 我",
    "PUBLIC_MESSAGE_DELETE": "频道里有人撤回消息",
}

# 官方通道的日志里会出现这些字样（用来做「官方通道」这个筛选项）
OFFICIAL_MARKERS = (
    "EventType.",
    "qq_official",
    "官方通道",
    "QQ | Bot",
    "Bot 9",              # 连接成功那行形如 "QQ | Bot 1905694838 connected"
    "webhook",
)

_EVENTTYPE_RE = re.compile(r"EventType\.([A-Z_]+)")
_BARE_RE = re.compile(r"\b(" + "|".join(sorted(EVENT_LABELS)) + r")\b")
# 一次扫描搞定两种写法：先 EventType.XXX，再光秃秃的 XXX（见 humanize_line 的注释）
_COMBO_RE = re.compile(r"EventType\.([A-Z_]+)|\b([A-Z][A-Z_]{3,})\b")


def describe_event(name: str) -> str:
    """单个事件名 → 人话；不认识就原样返回（不编）。"""
    key = str(name or "").strip()
    return EVENT_LABELS.get(key, key)


def humanize_line(line: str) -> str:
    """把一行日志里的英文事件名换成中文（保留原名字，方便对照排查）。

    `[EventType.GROUP_MESSAGE_CREATE]` → `[群里收到一条消息（全量模式：没 @ 也收）]（GROUP_MESSAGE_CREATE）`

    两种写法一次扫完（`EventType.XXX` 与光秃秃的 `XXX`）。**不能分两次 sub** ——
    第一次的产物会被第二次再翻一遍，日志会变成
    `[中文]（中文（GROUP_MESSAGE_CREATE））` 这种套娃（2026-10-02 踩过）。
    """
    text = str(line or "")

    def _sub(match: re.Match) -> str:
        raw = match.group(1) or match.group(2) or ""
        label = EVENT_LABELS.get(raw)
        if not label:
            return match.group(0)          # 不认识的原样留着
        if match.group(1):                 # EventType.XXX 这种写法
            return f"[{label}]（{raw}）"
        return f"{label}（{raw}）"

    return _COMBO_RE.sub(_sub, text)


def is_official_line(line: str) -> bool:
    """这条日志属不属于官方通道（用来给日志页做筛选）。"""
    text = str(line or "")
    return any(marker in text for marker in OFFICIAL_MARKERS)
