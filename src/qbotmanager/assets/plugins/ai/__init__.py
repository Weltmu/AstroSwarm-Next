# -*- coding: utf-8 -*-
"""AI 对话插件（AstroSwarm 星群内置，不可卸载，可通过设置停用）。

**2026-10-11 热重载改造（重要，别改回去）**：
以前这里在 import 期判断 `AI_DISABLED` / `AI_PLATFORM_*`，结果是「关掉之后
改回来必须重启机器人」，而且关掉时连插件都不注册、日志里一片空白（今天因此
踩了大坑）。现在：

* 插件**永远注册**（无论总开关开着还是关着）；
* 总开关 / 平台开关在**每条消息进来时**读一次（`gates.py`，一次 os.stat）；
* 被挡掉时把**人话原因**打进日志，并说清"打开开关立即生效、不用重启"。

旧版本插件的数据会自动迁移到本插件目录（仅迁移一次）。
"""
import os as _os

from nonebot import get_driver, get_plugin_config, logger, on_message, require
from nonebot.adapters import Bot as BaseBot

require("nonebot_plugin_localstore")

from nonebot.plugin import PluginMetadata

from . import gates
from . import friend_requests  # 好友申请（卖家场景，2026-10-12）
from .config import PluginConfig

from .adapters import build_channel
from .brain import (
    group_manager,
    group_probability_states,
    group_timers,
    handle_message,
)
from .config import *  # noqa: F401,F403
from .context import load_contexts
from .manager import chat_manager
from .platform import ChannelContext, ChannelMessage  # noqa: F401

# 主动聊天能力包已随主仓库开源免费：部署端在装了这个能力包之后
# 会置位 ASTROSWARM_PACK_PROACTIVE=1，这里按它决定要不要导入实现。
if _os.environ.get("ASTROSWARM_PACK_PROACTIVE") == "1":
    from .proactive import *  # noqa: F401,F403  主动聊天模块（导入时自动注册后台任务）
else:
    logger.info(
        "主动聊天能力包没装，跳过加载（在控制台「插件管理」里免费装上 "
        "proactive 能力包，"
        "再重启一次机器人即可生效）"
    )

__plugin_meta__ = PluginMetadata(
    name="ai",
    description="AI 对话插件（AstroSwarm 内置）：大模型对话、主动聊天、人格设定",
    usage="在 AstroSwarm「AI 大脑」页配置人设与主动聊天",
    type="application",
    homepage="https://github.com/Weltmu",
    config=PluginConfig,
    supported_adapters={"~onebot.v11", "~QQ", "~nonebot_adapter_ilink"},
)

# 初始化管理器和上下文
load_contexts()
get_plugin_config(PluginConfig)

# 启动那一刻先把开关现状写进日志：以后"它怎么不理我"先看这行
try:
    logger.info(f"[ai] 插件已注册（总开关/平台开关改为每条消息热读）：{gates.status_line()}")
except Exception as e:  # noqa: BLE001 —— 打日志失败绝不能挡住插件加载
    logger.warning(f"[ai] 开关现状日志输出失败：{e}")

# 创建消息处理器，不限制规则，在 handle 中自行判断
chat = on_message(priority=50, block=False)


@chat.handle()
async def _(bot: BaseBot, event):
    """统一通道入口：按事件类型分发到对应适配器 → 归一化 → AI 大脑。"""
    # 开关在这里判**（不是 import 时）**：改完立即生效，不用重启机器人。
    if gates.ai_disabled():
        logger.info("[ai] " + gates.blocked_reason())
        return
    logger.info(
        f"[ai] 收到事件 {type(event).__module__} "
        f"text={getattr(event, 'get_plaintext', lambda: '')()[:60]}")
    built = await build_channel(chat, bot, event)
    if built is None:
        logger.info("[ai] 平台分发返回 None，跳过")
        return
    msg, ctx = built
    await handle_message(msg, ctx)


# 入群欢迎（QQ 官方 P2，2026-10-02）：GROUP_ADD_ROBOT 是通知事件，
# 用它的 event_id 被动回复**不占主动消息额度**，所以"进群打招呼"是免费且稳的。
# 欢迎语一律交给 AI 按人设生成（不写死话术）；AI 不可用就安静跳过。
async def _generate_welcome(msg):
    from . import reply_format, tool_marker
    from .chat import get_chat_reply

    reply = await get_chat_reply([{"role": "user", "content": msg.text}], is_group=True)
    segments = reply_format.reply_segments(reply)
    if not segments:
        return ""
    # 2026-10-16（审计 F2 收尾）：入群欢迎同属"旁路出站" —— 主链路/戳一戳/主动聊天
    # 都过 tool_marker，这条以前漏了：模型吐工具调用标记或裸 JSON 调用形状时，
    # 会把调用原文直接发进刚进的新群。这里补上同一道清洗（poke.py 同款调用口径）。
    cleaned, _removed = tool_marker.clean_segments(segments)
    return cleaned[0].strip() if cleaned else ""


try:
    from nonebot import on_notice
    from nonebot.adapters.qq.event import GroupAddRobotEvent

    from .group_welcome import build_welcome_context, handle_group_add

    welcome = on_notice(priority=51, block=False)

    @welcome.handle()
    async def _(bot: BaseBot, event):
        if not isinstance(event, GroupAddRobotEvent):
            return
        if gates.ai_disabled() or not gates.platform_enabled("qq"):
            logger.info("[ai] 入群欢迎被开关挡掉：" + gates.blocked_reason("qq"))
            return
        logger.info("[ai] 被拉进新群，准备打招呼")
        msg, ctx = await build_welcome_context(welcome, bot, event)
        await handle_group_add(event, ctx, lambda: _generate_welcome(msg))
except Exception as e:  # noqa: BLE001 —— 注册失败不能连累聊天主流程
    logger.warning(f"[ai] 入群欢迎注册失败（其它功能不受影响）：{e}")

# ---- 好友申请（OneBot V11，2026-10-12 老板要的卖家功能）----
# 收到 request.friend 就记下来（并可选自动通过，开关默认关）。
# 单独 try：这个通道没有 request 事件时不能连累聊天主流程。
try:
    from nonebot import on_request

    friend_req = on_request(priority=49, block=False)

    @friend_req.handle()
    async def _on_friend_request(bot: BaseBot, event):
        mod = type(event).__module__ or ""
        if "onebot.v11" not in mod or "friend" not in type(event).__name__.lower():
            return
        from .friend_requests import handle_friend_request

        await handle_friend_request(bot, event)

    # 好友**已经加上了**的 notice（OneBot V11 的 notice.friend_add）→ 见面问候
    # 2026-10-12：NapCat 自己会先把申请通过掉，我们这边收不到 request.friend，
    # 只剩这条 notice —— 不多挂一个的话，机器人对新朋友一脸茫然。
    from nonebot import on_notice as _on_notice_fa

    friend_added = _on_notice_fa(priority=48, block=False)

    @friend_added.handle()
    async def _on_friend_added(bot: BaseBot, event):
        # 2026-10-12：OneBot V11 的类名是 FriendsAddNoticeEvent/FriendAddNoticeEvent，
        # **没有下划线** —— 以前按 "friend_add" 判类名，永远不成立，于是从不打招呼。
        kind = str(getattr(event, "notice_type", "") or "").lower()
        if not kind:
            kind = type(event).__name__.replace("_", "").lower()
        if "friend" not in kind or "add" not in kind:
            return
        from .friend_requests import greet_new_friend

        await greet_new_friend(bot, str(getattr(event, "user_id", "") or ""))
except Exception as _e:  # noqa: BLE001
    logger.debug(f"好友申请处理未注册: {_e}")

# 按钮点击（QQ 官方 P2，2026-10-03）：把点击当成"用户说了这句话"交给 AI 回话。
# 官方通道的按钮回调只带 button_id/button_data，适配器里拼成人话。
try:
    from nonebot import on_notice as _on_notice2
    from nonebot.adapters.qq.event import (
        InteractionCreateEvent,
        MessageAuditPassEvent,
        MessageAuditRejectEvent,
    )

    buttons = _on_notice2(priority=52, block=False)

    @buttons.handle()
    async def _(bot: BaseBot, event):
        if not isinstance(event, InteractionCreateEvent):
            return
        if gates.ai_disabled() or not gates.platform_enabled("qq"):
            logger.info("[ai] 按钮点击被开关挡掉：" + gates.blocked_reason("qq"))
            return
        built = await build_channel(buttons, bot, event)
        if built is None:
            return
        msg, ctx = built
        logger.info(f"[ai] 有人点了按钮：{msg.extra.get('button_id') or msg.extra.get('button_data')}")
        await handle_message(msg, ctx)

    audit = _on_notice2(priority=53, block=False)

    @audit.handle()
    async def _(bot: BaseBot, event):
        """平台审核结果：通过只记日志，被拦要提醒管理员（用户那边看不到）。"""
        from .adapters.qq_official import audit_line
        from .send2root import send_error_to_super_users

        if not isinstance(event, (MessageAuditPassEvent, MessageAuditRejectEvent)):
            return
        level, text = audit_line(event)
        if level == "warn":
            logger.warning(f"[ai] {text}")
            try:
                await send_error_to_super_users(text, event)
            except Exception as e:  # noqa: BLE001 —— 通知失败不影响别的
                logger.debug(f"[ai] 审核通知发送失败：{e}")
        else:
            logger.info(f"[ai] {text}")
except Exception as e:  # noqa: BLE001
    logger.warning(f"[ai] 按钮/审核事件注册失败（其它功能不受影响）：{e}")

# ---- 戳一戳回复（OneBot V11 的 notice.notify.poke）----
# 2026-10-12 接线：poke.py 一直躺在插件目录里，但**从来没有任何地方 import 它** ——
# 模块不导入就不会注册处理器，表现就是"戳一戳永远没回复"。单独 try：
# 戳一戳注册失败不能连累聊天主流程（官方通道没装 OneBot 时这里只记一条 warning）。
try:
    from . import poke  # noqa: F401
except Exception as e:  # noqa: BLE001
    logger.warning(f"[ai] 戳一戳回复注册失败（其它功能不受影响）：{e}")

driver = get_driver()


@driver.on_shutdown
async def shutdown_hook():
    """Driver 关闭时清理定时任务"""
    if group_manager:
        await group_manager.shutdown()

