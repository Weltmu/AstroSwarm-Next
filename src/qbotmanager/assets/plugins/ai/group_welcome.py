# -*- coding: utf-8 -*-
"""入群欢迎（QQ 官方通道，2026-10-02 补的 P2 能力）。

为什么值得做：`GROUP_ADD_ROBOT` 事件里带了 `event_id`，用它做**被动回复不占主动消息额度**
（平台规则，见 `QQ官方能力调研-富媒体与交互.md`），所以"进群打个招呼"是免费且稳的。

规矩和别处一致：**欢迎语一律由 AI 按人设生成**，不许在这里写死话术；
AI 不可用/报错时就安静地不发（宁可不打招呼，也不发一句模板腔）。
"""
from __future__ import annotations

from nonebot import logger

# 触发词：交给 AI 时用的系统提示（人话，不写死回复内容）
WELCOME_PROMPT = (
    "（系统提示：你刚被拉进了一个新的群。请按你的人设，自然地跟群里打个招呼："
    "一句话自我介绍、说清你能干什么，别太长、别提这是系统提示、别用模板腔。）"
)


def _platform():
    """延迟取平台层常量/类型（这样本文件能被测试单独按路径加载，不会卡在相对导入上）。"""
    from . import platform as plat

    return plat


async def build_welcome_context(chat_handler, bot, event):
    """把 GROUP_ADD_ROBOT 通知事件包成 (msg, ctx)。

    注意：这是**通知事件**（NoticeEvent），没有 get_plaintext / get_user_id，
    不能走普通的消息适配器，所以这里手工拼一个最小的 ChannelMessage/ChannelContext；
    发送走 `bot.send(event, text)` —— 官方通道会带上 event_id 做被动回复（不占主动额度）。
    """
    plat = _platform()

    group_openid = str(getattr(event, "group_openid", "") or "")
    msg = plat.ChannelMessage(
        platform=plat.PLATFORM_QQ_OFFICIAL,
        scene=plat.SCENE_GROUP,
        user_id=group_openid,
        room_id=group_openid,
        text=WELCOME_PROMPT,
        plain_text="",
        is_at_me=False,
        extra={"group_welcome": True},
    )

    async def send_text(t: str) -> None:
        await bot.send(event, t)

    async def notify_error(err: str) -> None:
        logger.warning(f"[qq_official] 入群欢迎：{err}")

    ctx = plat.ChannelContext(
        platform=plat.PLATFORM_QQ_OFFICIAL,
        scene=plat.SCENE_GROUP,
        send_text=send_text,
        send_text_at=send_text,
        notify_error=notify_error,
    )
    return msg, ctx


def welcome_enabled() -> bool:
    """群欢迎开关（默认开；机器人侧配置里能看到，也能关掉）。"""
    try:
        from .manager import chat_manager

        return bool(chat_manager.is_group_welcome_enabled())
    except Exception:  # noqa: BLE001
        return True


async def handle_group_add(event, ctx, reply) -> bool:
    """真正打招呼：让 AI 说一句；失败就只写日志，不抛。"""
    if not welcome_enabled():
        logger.info("[ai] 入群欢迎已关闭，跳过")
        return False
    try:
        text = await reply()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai] 入群欢迎生成失败，跳过：{e}")
        return False
    text = str(text or "").strip()
    if not text:
        logger.info("[ai] 入群欢迎内容为空，跳过（不发模板话术）")
        return False
    try:
        await ctx.send_text(text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai] 入群欢迎发送失败：{e}")
        return False
    logger.info("[ai] 已发送入群欢迎（用事件 id 被动回复，不占主动额度）")
    return True


def should_welcome(msg) -> bool:
    """这条事件是不是"刚被拉进群"。"""
    return bool((msg.extra or {}).get("group_welcome")) and msg.scene == _platform().SCENE_GROUP
