# -*- coding: utf-8 -*-
"""「这次改动要不要重启机器人？」——**只有这一份口径**（2026-10-11 老板派活）。

老板的原话："一些功能能不能加上热重载呀，就不要每次重启机器人了"。
结论口径（必须原话一致，别各写各的）：

    设置改了立即生效；代码改了才需要重启，且点一下就能重启。

所以界面上每条改动入口都要标清楚属于哪一类：

* ``hot=True``  → **立即生效**（不用重启）：开关、档位、人设、模型参数、
  群白名单、主人、唤醒词、免@有效期、主动聊天参数……这类全都热读；
* ``hot=False`` → **需要重启一次**：只有"机器人要重新加载 Python 代码/适配器"
  这类改动（装/卸插件、换 QQ 通道、能力包、MCP 服务器、凭证类适配器）。

桌面端（PySide）与无头端控制台（React）共用这里的常量与文案，
免得一边写"改完生效"、一边写"重启生效"，用户只能猜。
"""
from __future__ import annotations

from typing import Dict, List

# ---- 一句话口径（界面、日志、回报都引用它）----
HOT_LINE = "设置改了立即生效；代码改了才需要重启，且点一下就能重启。"
HOT_TAG = "立即生效"
COLD_TAG = "需重启"
HOT_HINT = "改完立即生效，不用重启机器人（热重载）"
COLD_HINT = "这一项要重启一次机器人才生效（点一下就能重启）"

# ---- 改动入口 → 是否需要重启 ----
# key 用界面上的"块名"，与桌面端 / 控制台两边对得上即可。
RESTART_MATRIX: Dict[str, bool] = {
    # 热：设置类（本轮全做）
    "ai_enabled": False,                   # AI 总开关：每条消息热读 → 立即生效
    "ai_platforms": False,                 # 平台开关（QQ/微信/飞书/纸飞机）
    "memory_level": False,                 # 记忆档位（无/基础/智能）
    "memory_enabled": False,               # 全局记忆复选框
    "personality": False,                  # 人格设定
    "ai_model": False,                     # 模型 / 接口地址 / 密钥
    "ai_temperature": False,               # 温度
    "ai_max_tokens": False,                # 最大长度
    "group_chat_enabled": False,           # 群里回话总开关
    "allowed_groups": False,               # 群白名单
    "super_users": False,                  # 主人（超级用户）
    "agent_owner": False,                  # 主人 QQ（智能体档案）
    "agent_wake_words": False,             # 唤醒词 / 称呼
    "session": False,                      # 免@连续对话有效期与模式
    "proactive": False,                    # 主动聊天（间隔/冷却/概率/目标）
    "interject": False,                    # 群里搭话
    "rich_parse_enabled": False,           # 引用/图片解析
    "image_recognition_enabled": False,    # 识图
    "self_forge_enabled": False,           # 聊天里自学
    "cross_platform_enabled": False,       # 跨平台互通
    "group_welcome_enabled": False,        # 入群欢迎
    "allowed_send_targets": False,         # 主动发送白名单
    "mcp": True,                           # MCP 服务器配置：连接要重建 → 需重启
    "plugins": True,                       # 装/卸插件（.py 代码）
    "tool_packs": True,                    # 能力包
    "qq_channel": True,                    # 换 QQ 通道（适配器列表变了）
    "feishu_adapter": True,                # 飞书适配器安装
    "wechat_login": True,                  # 微信重新扫码
    "logs_keep_days": True,                # 日志保留天数（清理任务启动时读）
    "dsh": True,                           # dsh 通道（独立进程）
}

# 明确**不做**热重载的那一类（老板拍板）：插件 Python 代码热重载。
# NoneBot 的 matcher 注册 + Python 模块重载会留旧对象/旧注册，风险大于收益。
NO_HOT_CODE_REASON = (
    "插件代码（.py）不做热重载：NoneBot 的 matcher 注册与 Python 模块重载会留下"
    "旧对象/旧注册，风险大于收益。改代码就重启一次，点一下就好。")


def needs_restart(key: str) -> bool:
    """这个改动入口要不要重启机器人（认不出的按"要重启"保守处理）。"""
    return bool(RESTART_MATRIX.get(str(key or "").strip(), True))


def tag_for(key: str) -> str:
    return COLD_TAG if needs_restart(key) else HOT_TAG


def hint_for(key: str) -> str:
    return COLD_HINT if needs_restart(key) else HOT_HINT


def summary() -> dict:
    """给界面/接口用的一份总表（桌面端与控制台共用）。"""
    hot: List[str] = [k for k, v in RESTART_MATRIX.items() if not v]
    cold: List[str] = [k for k, v in RESTART_MATRIX.items() if v]
    return {
        "line": HOT_LINE,
        "hot_tag": HOT_TAG,
        "cold_tag": COLD_TAG,
        "hot_hint": HOT_HINT,
        "cold_hint": COLD_HINT,
        "hot_keys": sorted(hot),
        "cold_keys": sorted(cold),
        "no_hot_code_reason": NO_HOT_CODE_REASON,
    }
