# -*- coding: utf-8 -*-
"""回复目标的**断言式护栏**：这条回复到底该发给谁。

为什么要有这个文件（2026-10-11 老板报的"串人"）：
    A 发了一个点赞表情，同时 B 问"你在干嘛"，结果机器人 **@B 回复"谢谢夸奖"** ——
    A 的事件被回成了 B 的会话。

根因有两条，都在这里收口：

1. **发送目标曾经不由事件决定**：QQ 适配器的 `ctx.send_*` 走的是 NoneBot 的
   `chat_handler.send(...)`，那是 **matcher 级**发送 —— 目标是"这个 matcher 当前正在
   处理的事件"。两条消息几乎同时到达时 matcher 并发处理，`send()` 可能落到**另一条
   事件**的会话上。现在适配器改为按**本事件捕获的 group_id / user_id** 显式发送，
   并且这里再用 `target_key` 对一遍账。

2. **通知类事件不该走"免@延续"**：点赞 / 戳一戳 / 按钮回调这类 notice 自带
   `user_id`（私聊）或 `group_id`+`user_id`（群聊），它**只能回到这条事件自己的会话**，
   绝不能用"最近活跃会话 / 全局最近用户"来推断目标（那就是串人的来源）。

规矩（老板拍板）：**宁可沉默，也不要发错人**。目标对不上就**不发**、写一条明确的
`logger.warning("通知事件会话不匹配，已跳过")`。

这个文件**不依赖 nonebot**（和 group_policy / session_policy 一样），
所以测试可以直接按路径加载它。
"""
from __future__ import annotations

import logging
from typing import Optional

# 本模块的 stdlib logger：构建环境（没装 nonebot）跑测试时也能被 caplog 抓到，
# 线上再叠一条 nonebot logger（见 warn()）。
_log = logging.getLogger("ai.target_guard")

# 跳过原因（测试与日志共用同一份字面量，别各写各的）
REASON_MISMATCH = "通知事件会话不匹配，已跳过"
REASON_NO_SESSION = "通知事件缺少会话信息，已跳过"
REASON_OK = ""

# 通知类事件（自带会话，且回复只能回到它自己的会话）
NOTICE_KINDS = ("poke", "like", "interaction", "notice", "audit")

WARN_MISMATCH = "[guard] " + REASON_MISMATCH + "：目标={target} 事件={event}"
WARN_NO_SESSION = "[guard] " + REASON_NO_SESSION + "：事件={event}"


def target_key(platform: str, room_id: str) -> str:
    """会话 key：与 `ChannelMessage.session_key` 同一口径（平台+会话）。"""
    return f"{platform}:{room_id}"


def is_notice(msg) -> bool:
    """这条消息是不是通知类事件（点赞 / 戳一戳 / 按钮 / 审核）。"""
    extra = getattr(msg, "extra", None) or {}
    if not isinstance(extra, dict):
        return False
    if extra.get("is_notice") or extra.get("interaction"):
        return True
    kind = str(extra.get("notice_kind") or "").strip().lower()
    return kind in NOTICE_KINDS


def notice_extra(kind: str) -> dict:
    """通知类事件的 extra 片段（适配器与戳一戳处理器共用）。"""
    return {"is_notice": True, "notice_kind": str(kind or "notice").strip().lower()}


def allows_session_continuation(msg) -> bool:
    """这条消息能不能走"免@延续"（上一条还在时效内就接着回）。

    **通知类事件一律不许走**：点赞/戳一戳没有"对话上下文延续"这回事，
    让它走延续分支，就等于拿"群里最近那条会话"去回一个赞 —— 串人。
    """
    return not is_notice(msg)


def check_reply_target(msg, ctx) -> str:
    """发回复之前的最后一道护栏。返回空串 = 可以发；非空 = **跳过**的原因。

    判定（只认事件自己带的会话，不猜）：

    * 通知类事件**必须有**会话信息（`room_id` 非空）→ 否则跳过并 warning；
    * `ctx.target_key`（这个通道**实际会发到**的会话）与 `msg.session_key`
      （事件自己的会话）必须一致 → 不一致就跳过并 warning；
    * 非通知类消息的 `target_key` 留空时不拦（老适配器还没填，行为不变）。
    """
    room = str(getattr(msg, "room_id", "") or "")
    platform = str(getattr(msg, "platform", "") or "")
    want = str(getattr(ctx, "target_key", "") or "")

    if is_notice(msg):
        if not room:
            return REASON_NO_SESSION
        if want and want != target_key(platform, room):
            return REASON_MISMATCH
        if not want:
            # 通知事件连"会发到哪"都说不出来 —— 宁可不发
            return REASON_NO_SESSION
        return REASON_OK

    if want and room and want != target_key(platform, room):
        return REASON_MISMATCH
    return REASON_OK


def warn(reason: str, msg, ctx) -> None:
    """把跳过原因写进日志。

    同时走 **stdlib logging** 与 nonebot 的 logger：
    * stdlib 那条保证构建环境（没装 nonebot）跑测试时也能被 caplog 抓到；
    * nonebot 那条保证线上日志里一定看得见（生产用的是 loguru）。
    """
    platform = str(getattr(msg, "platform", "") or "")
    room = str(getattr(msg, "room_id", "") or "")
    target = str(getattr(ctx, "target_key", "") or "") or "<未声明>"
    line = (WARN_NO_SESSION if reason == REASON_NO_SESSION else WARN_MISMATCH).format(
        target=target, event=target_key(platform, room) or "<空>")
    _log.warning(line)
    try:
        from nonebot import logger

        logger.warning(line)
    except Exception:  # noqa: BLE001 —— 构建环境没有 nonebot，属预期
        pass


def describe(msg, ctx) -> str:
    """一行诊断（实测确认时打出来看"到底归一化成了什么"）。"""
    extra = getattr(msg, "extra", None) or {}
    return ("platform=%s scene=%s room_id=%r user_id=%r session_key=%r context_key=%r "
            "user_key=%r ctx.target_key=%r is_notice=%s kind=%r"
            % (getattr(msg, "platform", ""), getattr(msg, "scene", ""),
               getattr(msg, "room_id", ""), getattr(msg, "user_id", ""),
               getattr(msg, "session_key", ""), getattr(msg, "context_key", ""),
               getattr(msg, "user_key", ""), getattr(ctx, "target_key", ""),
               is_notice(msg),
               (extra.get("notice_kind") if isinstance(extra, dict) else None)))


def resolve_notice_session(event, *, platform: str = "qq") -> Optional[dict]:
    """从**通知事件自己**解析会话；缺会话信息返回 None（调用方应跳过）。

    只读事件字段，绝不看"最近活跃会话"：
    OneBot 的戳一戳/点赞带 `group_id`（群）或只有 `user_id`（私聊）。
    """
    if event is None:
        return None
    group_id = getattr(event, "group_id", None)
    user_id = getattr(event, "user_id", None)
    if group_id:
        return {"platform": platform, "scene": "group", "room_id": str(group_id),
                "user_id": str(user_id or "")}
    if user_id:
        return {"platform": platform, "scene": "private", "room_id": str(user_id),
                "user_id": str(user_id)}
    return None
