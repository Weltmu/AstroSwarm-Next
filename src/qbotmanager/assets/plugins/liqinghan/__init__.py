# -*- coding: utf-8 -*-
"""李清菡智能体 NoneBot 插件（AstroSwarm 内置，可选启用）。

启用条件：程序侧注入 QBM_AGENT_PROFILE_ENABLED=1（AI 大脑页开启李清菡档案）。
QQ 消息由本插件直接消费（官方适配器）；微信消息走现有 qbm_bridge_client
→ 本地 18650 桥 → 同一李清菡 agent（共享大脑与记忆）。
"""
import asyncio
import os
from pathlib import Path

from nonebot import get_driver, logger, on_message
from nonebot.adapters import Bot as BaseBot
from nonebot.plugin import PluginMetadata

def _profile_enabled() -> bool:
    """档案是否启用：进程环境变量优先，其次读机器人 .env。

    2026-10-10 修：应用是把 `QBM_AGENT_PROFILE_ENABLED=1` **写进 .env 文件**的，
    而 NoneBot 读 .env 走 pydantic（不会落进 os.environ）——只查 os.environ 会让
    整个插件静默不启用。后果很隐蔽：应用同时把 QQ 交给档案（`AI_PLATFORM_QQ=0`），
    档案又没起来，于是"群里 @ 它谁都不回"。
    """
    if os.environ.get("QBM_AGENT_PROFILE_ENABLED") == "1":
        return True
    try:
        from .config import _load_env

        env_path = Path(__file__).resolve().parents[3] / ".env"
        raw = _load_env(env_path)
        if str(raw.get("QBM_AGENT_PROFILE_ENABLED", "")).strip() == "1":
            logger_holder = None      # 这里还没 import logger，静默返回即可
            return True
    except Exception:  # noqa: BLE001 —— 判断失败就当没启用，别影响插件加载
        pass
    return False


_ENABLED = _profile_enabled()
_AGENT = None
_CHANNEL = None
_BRIDGE = None
_TASKS = []

if _ENABLED:
    from .agent import Agent, setup_logging
    from .bridge import WechatBridge
    from .config import Config
    from .qq_official import DispatchChannel, to_data

    __plugin_meta__ = PluginMetadata(
        name="liqinghan",
        description="李清菡智能体（AstroSwarm 内置）：真人感 agent + 记忆 + 群管 + 游戏",
        usage="在 AstroSwarm「AI 大脑 → 智能体档案」启用并重启机器人",
        type="application",
        supported_adapters={"~QQ", "~nonebot_adapter_ilink"},
    )

    # 必须把机器人 .env 的路径传进去：API 密钥/模型/视觉配置都是应用写在那里的，
    # 而 Config 只认 os.environ（NoneBot 读 .env 走 pydantic，不落 os.environ）。
    # 2026-10-10 踩过：不传路径 → key 为空 → 回话时 "Bearer " 报 Illegal header value。
    _cfg = Config(Path(__file__).resolve().parents[3] / ".env")
    setup_logging(_cfg)
    _CHANNEL = DispatchChannel()
    _AGENT = Agent(_cfg, _CHANNEL)
    _BRIDGE = WechatBridge(
        _AGENT, _CHANNEL, port=int(os.environ.get("QBM_AGENT_BRIDGE_PORT", "18650")))

    _qq_msg = on_message(priority=20, block=False)

    @_qq_msg.handle()
    async def _handle_qq_message(bot: BaseBot, event):
        if _AGENT is None:
            return
        mod = type(event).__module__ or ""
        if "nonebot.adapters.qq" not in mod:
            return
        try:
            is_group = bool(getattr(event, "group_openid", None))
            # 2026-10-16（审计 platform/E #55）：本插件**只**消费官方 QQ 事件
            # （上面按 `nonebot.adapters.qq` 过滤、拿到的 id 是 openid），所以
            # source 标注统一成 `qq_official:*`。以前标成 `qq:group`/`qq:c2c`
            # （OneBot 的号），插件侧能力矩阵就会按 OneBot 行给官方通道发
            # proactive（set_reminder 等主动消息）—— 与程序侧"官方主动消息按
            # 日限额度、不给 proactive"的口径相反；记忆 key、绑定组也全按
            # openid 走 `qq_official:<openid>`，标注对齐后处处一致。
            source = "qq_official:group" if is_group else "qq_official:c2c"
            data = to_data(event, bot_qq=_cfg.bot_qq, source=source)
            _CHANNEL.set_current(bot, event, source)
            await _AGENT.on_event(data)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[liqinghan] QQ 消息处理失败: {e}")

    _driver = get_driver()

    @_driver.on_startup
    async def _startup():
        global _TASKS
        try:
            await _BRIDGE.start()
        except OSError as e:
            logger.warning(f"[liqinghan] 微信桥启动失败（18650 被占用？）: {e}")
        _TASKS = _AGENT.run_tasks()
        logger.info("[liqinghan] 李清菡智能体已启动，模型=%s", _cfg.ds_model)

    @_driver.on_shutdown
    async def _shutdown():
        await _BRIDGE.stop()
        for t in _TASKS:
            t.cancel()
        if _TASKS:
            await asyncio.gather(*_TASKS, return_exceptions=True)
        _TASKS.clear()
else:
    __plugin_meta__ = PluginMetadata(
        name="liqinghan",
        description="李清菡智能体（未启用，可在 AstroSwarm 设置中开启）",
        usage="在 AstroSwarm「AI 大脑 → 智能体档案」启用并重启机器人",
        type="application",
    )
