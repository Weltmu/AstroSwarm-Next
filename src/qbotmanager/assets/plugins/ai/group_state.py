# -*- coding: utf-8 -*-
"""QQ 官方通道的"收消息模式"状态：全量 / 仅 @ / 还没收到过。

为什么要记这个：机器人开了"接收所有消息"后，群里每条消息都走
`GROUP_MESSAGE_CREATE`（全量事件）；没开就只会收到 `GROUP_AT_MESSAGE_CREATE`
（群里 @ 它才有）。两者在界面上看起来一模一样 —— 用户（和排查的人）根本不知道
到底是哪一种，"群里 @ 没反应"就变得没法解释。这里把真实收到的事件类型记下来，
客户端 QQ 页的状态卡直接显示，一句话说清。

不依赖 nonebot，方便构建环境的测试直接按路径加载。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STATE_NAME = "qq_group_state.json"

EVENT_FULL = "GROUP_MESSAGE_CREATE"        # 全量：群里每条消息都推过来
EVENT_AT = "GROUP_AT_MESSAGE_CREATE"       # 仅 @：只有 @ 机器人才推过来

MODE_FULL = "full"
MODE_AT_ONLY = "at_only"
MODE_NONE = "none"


def empty_state() -> dict:
    return {"events": {}, "last_ts": 0.0, "last_at_me": False, "mode": MODE_NONE}


def normalize_event_type(raw) -> str:
    """把事件类型规范化成 "GROUP_MESSAGE_CREATE" 这种纯名字。

    坑：适配器里是 `class EventType(str, Enum)`，直接 str() 会得到
    "EventType.GROUP_MESSAGE_CREATE"（Python 3.12 的 str-Enum 也这样），
    客户端拿它跟常量比对就永远对不上 —— 所以这里统一取 value 再去掉前缀。
    """
    value = getattr(raw, "value", raw)
    text = str(value or "")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


MAX_GROUPS = 50      # 只留最近活跃的若干个群，防止这个文件无限长大


def record(state: dict, event_type: str, at_me: bool = False, now: float | None = None,
           group_openid: str = "") -> dict:
    """把一条群消息记进状态（纯函数，返回新 dict）。

    group_openid 非空时同时更新"见过消息的群"名单，客户端拿它做
    "允许回话的群"勾选列表 —— 用户不用手抄 openid。
    """
    data = dict(state or {})
    events = dict(data.get("events") or {})
    kind = normalize_event_type(event_type)
    if kind:
        events[kind] = int(events.get(kind, 0)) + 1
    data["events"] = events
    ts = float(now if now is not None else time.time())
    data["last_ts"] = ts
    data["last_at_me"] = bool(at_me)
    gid = str(group_openid or "").strip()
    if gid:
        groups = dict(data.get("groups") or {})
        info = dict(groups.get(gid) or {})
        info["last_ts"] = ts
        info["count"] = int(info.get("count", 0)) + 1
        groups[gid] = info
        if len(groups) > MAX_GROUPS:
            keep = sorted(groups.items(), key=lambda kv: float((kv[1] or {}).get("last_ts") or 0),
                          reverse=True)[:MAX_GROUPS]
            groups = dict(keep)
        data["groups"] = groups
    data["mode"] = mode_of(data)
    return data


def mode_of(state: dict) -> str:
    """按收到过的事件类型判定模式（全量优先）。"""
    events = (state or {}).get("events") or {}
    if int(events.get(EVENT_FULL, 0)) > 0:
        return MODE_FULL
    if int(events.get(EVENT_AT, 0)) > 0:
        return MODE_AT_ONLY
    return MODE_NONE


def load(path) -> dict:
    """读状态文件；没有/坏了都当空状态。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else empty_state()
    except (ValueError, OSError):
        return empty_state()


def save(path, state: dict) -> bool:
    """写状态文件（失败静默：状态记录不该影响收消息）。"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state or {}, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False
