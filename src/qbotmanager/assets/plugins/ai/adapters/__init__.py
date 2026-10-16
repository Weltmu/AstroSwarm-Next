# -*- coding: utf-8 -*-
"""平台适配器分发：按事件类型把平台事件转成 ChannelMessage / ChannelContext。"""
from typing import Optional

from nonebot import logger

from .. import gates
from ..platform import ChannelContext, ChannelMessage


def platform_enabled(platform: str) -> bool:
    """AstroSwarm 平台开关：AI_PLATFORM_QQ=0 表示 AI 不处理 QQ 消息。

    **每条消息热读**（2026-10-11）：程序侧把算好的有效开关写进热配置快照，
    这里一次 ``os.stat`` 就读到最新值 —— 开关改完立即生效，不用重启机器人。
    快照不在（老装机）时回退环境变量；环境变量也没有就默认启用，避免行为回归。
    """
    return gates.platform_enabled(platform)


def detect_platform(event) -> str:
    """识别事件属于哪个平台（不依赖脆弱的顶层包导入）。

    优先按事件类所在模块名识别：AstroSwarm 部署的微信适配器
    实际模块名是 src.plugins.nonebot_adapter_ilink.*（顶层名不可导入）；
    QQ 走 OneBot V11 类型判断；最后用 context_token 兜底（微信事件独有字段）。
    """
    mod = type(event).__module__ or ""
    if "nonebot_adapter_ilink" in mod:
        return "wechat"
    try:
        from nonebot.adapters.onebot.v11 import MessageEvent as OneBotEvent

        if isinstance(event, OneBotEvent):
            return "qq"
    except Exception:  # noqa: BLE001
        pass
    try:
        from nonebot.adapters.qq import MessageEvent as QQOfficialEvent

        if isinstance(event, QQOfficialEvent):
            return "qq_official"
        # 按钮点击、审核结果这些是**通知事件**，频道 @ 是另一种消息事件：
        # 都归到官方通道（2026-10-03 加的按钮/频道/审核支持）
        from nonebot.adapters.qq import NoticeEvent as QQNoticeEvent

        if isinstance(event, QQNoticeEvent):
            return "qq_official"
        if str(type(event).__module__ or "").startswith("nonebot.adapters.qq"):
            return "qq_official"
    except Exception:  # noqa: BLE001
        pass
    try:
        from nonebot.adapters.feishu import MessageEvent as FeishuEvent

        if isinstance(event, FeishuEvent):
            return "feishu"
    except Exception:  # noqa: BLE001
        pass
    if hasattr(event, "context_token"):
        return "wechat"
    return ""


def _blocked_log(platform: str) -> None:
    """被平台开关挡掉时**一定**留一条清清楚楚的日志（以前这里是静默 return）。"""
    logger.info("[bridge] " + gates.blocked_reason(platform))


async def build_channel(chat_handler, bot, event) -> Optional[tuple[ChannelMessage, ChannelContext]]:
    """根据事件类型分发到对应平台适配器。"""
    platform = detect_platform(event)
    logger.info(f"[bridge] 事件 {type(event).__module__} -> 平台 {platform or '未知'}")
    if platform == "qq":
        if not platform_enabled("qq"):
            _blocked_log("qq")
            return None
        from .qq import build_channel_from_event

        return await build_channel_from_event(chat_handler, bot, event)

    if platform == "qq_official":
        # 官方 QQ 与 OneBot 共用“QQ”平台开关（AI 大脑的 QQ 总开关）
        # 诊断：这个分支里只有这里会静默 return None，把开关值打出来好定位
        logger.info(
            f"[bridge] 官方 QQ 平台开关 {gates.status_line('qq')}"
            f" 启用={platform_enabled('qq')}")
        if not platform_enabled("qq"):
            _blocked_log("qq")
            return None
        from .qq_official import (
            build_channel_from_guild,
            build_channel_from_interaction,
            build_channel_from_qq_official,
        )

        # 按钮点击（通知事件）与频道 @（另一种消息事件）各走各的入口
        if "InteractionCreate" in type(event).__name__:
            return await build_channel_from_interaction(chat_handler, bot, event)
        if hasattr(event, "channel_id") and not hasattr(event, "group_openid"):
            return await build_channel_from_guild(chat_handler, bot, event)
        if not hasattr(event, "get_plaintext"):
            logger.info(f"[bridge] 官方通道的这类事件先不处理：{type(event).__name__}")
            return None
        return await build_channel_from_qq_official(chat_handler, bot, event)

    if platform == "wechat":
        if not platform_enabled("wechat"):
            _blocked_log("wechat")
            return None
        from .wechat import build_channel_from_wechat

        return await build_channel_from_wechat(chat_handler, bot, event)

    if platform == "feishu":
        if not platform_enabled("feishu"):
            _blocked_log("feishu")
            return None
        from .feishu import build_channel_from_feishu

        return await build_channel_from_feishu(chat_handler, bot, event)
    logger.warning(f"[bridge] 未识别的平台事件: {type(event).__module__}")
    return None
