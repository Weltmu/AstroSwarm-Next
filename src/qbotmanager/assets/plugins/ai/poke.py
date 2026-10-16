"""戳一戳 AI 回复模块（整合原 pokepoke_miss，回复内容由 AI 生成）"""

import time
from typing import Dict

from nonebot import logger, on_notice
from nonebot.adapters.onebot.v11 import PokeNotifyEvent

from . import gates
from . import reply_format
from . import target_guard
from . import tool_marker
from .chat import get_poke_reply
from .config import config
from .manager import chat_manager


def _poke_check(event) -> bool:
    # 只认"戳的是机器人自己"的戳一戳通知。其它 notice（好友添加、正在输入、
    # 群成员变动……）没有 target_id 字段：规则一抛异常 NoneBot 会按 ERROR 打一条
    # "Rule check failed"，每条无关通知都刷屏 —— 先 isinstance 判掉。
    return isinstance(event, PokeNotifyEvent) and event.target_id == event.self_id


poke = on_notice(rule=_poke_check, priority=100)

# 冷却记录：key = 群号:QQ号（私聊戳为 0:QQ号）
_last_poke_time: Dict[str, float] = {}


def _who_poked(event: PokeNotifyEvent) -> str:
    """戳你那个人的称呼：优先群名片，其次昵称；取不到就空串（AI 会当成熟人处理）。"""
    sender = getattr(event, "sender", None)
    if sender is None:
        return ""
    card = str(getattr(sender, "card", "") or "").strip()
    nickname = str(getattr(sender, "nickname", "") or "").strip()
    return card or nickname


@poke.handle()
async def handle_poke(bot, event: PokeNotifyEvent):
    if not config.aichat_poke_enabled:
        return

    # 2026-10-15 补门禁（老板：把某个群 / 整个 QQ 的 AI 关掉之后，戳它照样回话）：
    # 戳一戳以前是一条**完全独立的旁路** —— 消息链路上每一道开关它一道都没走，
    # 只认上面那个插件静态开关。现在按 brain.handle_message 的同口径补齐：
    # AI 总开关 + 平台开关（gates）→ 聊天总开关 → 群聊开关，任一道关掉都不回。
    reason = gates.blocked_reason("qq")
    if reason:
        logger.info("[poke] " + reason)
        return

    # 会话只从**这条事件自己**解析（2026-10-11 串人修复）：
    # 以前用 `poke.send(...)`，那是 matcher 级发送 —— 目标是"这个 matcher 当前正在
    # 处理的事件"，戳一戳与别的消息并发时可能发到**另一条事件**的会话上。
    # 现在按事件自己的 group_id / user_id 显式发送；连会话都解析不出来就**不发**。
    session = target_guard.resolve_notice_session(event)
    if session is None:
        logger.warning("[guard] " + target_guard.REASON_NO_SESSION + "：戳一戳事件没有会话信息")
        return

    # 群聊开关 / 聊天总开关：与 brain 同口径（客户端改完立即生效，不用重启）
    try:
        chat_manager.maybe_reload()
    except Exception:  # noqa: BLE001 —— 热重载失败不能挡住戳一戳
        pass
    if not chat_manager.is_chat_enabled():
        logger.info("[poke] 聊天总开关已关闭，戳一戳不回复")
        return
    if session["scene"] == "group" and not chat_manager.is_group_enabled(session["room_id"]):
        logger.info(f"[poke] 群 {session['room_id']} 未开启群聊回话，戳一戳不回复"
                    "（客户端 AI 大脑页「允许在群里回话」可打开）")
        return

    cooldown = config.aichat_poke_cooldown
    key = f"{session['room_id']}:{event.user_id}"
    now = time.time()
    last = _last_poke_time.get(key, 0.0)
    if cooldown > 0 and now - last < cooldown:
        return

    try:
        # 把「谁戳的」一起交给 AI，回复才知道在对谁说话
        reply = await get_poke_reply(_who_poked(event))
        if not reply or not reply.strip():
            # AI 没生成出内容（或没配 AI）：这一次就不理人，不用任何固定话术顶上
            logger.info("戳一戳：本次没有生成回复，跳过发送")
            return
        # 2026-10-15：戳一戳的回复以前 `reply.strip()` 就直接发，**没过主链路的出站整形**
        # （brain._reply_in_segments 那套 reply_format + tool_marker）。模型偶尔吐
        # `{"reply": [...]}` 壳或 `<tool_call>` 标记时，会原样落进群里 —— 这是踩过的回归。
        # 现在按同一条链过一遍：先整形分段，再做出站清洗，取第一段发送。
        parts = reply_format.reply_segments(reply)
        cleaned, _removed = tool_marker.clean_segments(parts)
        payload = cleaned[0].strip() if cleaned else ""
        if not payload:
            logger.info("戳一戳：出站清洗后没有可发内容（原本是 JSON 壳或纯标记），跳过发送")
            return
        _last_poke_time[key] = now
        if session["scene"] == "group":
            # 群里戳：@ 回戳你的那个人，且只发到**这个群**
            await bot.call_api("send_group_msg", group_id=int(session["room_id"]),
                               message=f"[CQ:at,qq={event.user_id}] {payload}")
        else:
            await bot.call_api("send_private_msg", user_id=int(session["room_id"]),
                               message=payload)
    except Exception as e:
        logger.error(f"戳一戳回复失败: {e}")
