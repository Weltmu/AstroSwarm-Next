# -*- coding: utf-8 -*-
"""李清菡的共享开关热读（2026-10-15 修「双框架开关不同步」）。

客户端上有两套跟"AI 开不开"有关的开关：

* **AI 总开关**（「AI 大脑」那个总闸）—— 程序侧把算好的结果写进机器人侧的
  **热配置快照** `<机器人目录>/config/ai/astroswarm_live.json`（`ai_disabled`），
  AI 插件每条消息热读它；
* **智能体档案开关**（AI 大脑 → 智能体档案）—— 程序侧写机器人 `.env` 的
  `QBM_AGENT_PROFILE_ENABLED=1`（`core/bot.write_env`）。

李清菡以前**两个都不热读**：它只看自己那份 `.env`，而且是 **import 时看一次**。
后果（老板报的"关了还说话"）：

* 关掉 AI 总开关 → AI 插件当场哑了，李清菡照旧回话、还照旧主动插话 ——
  在客户端上看着就是"关不干净"，日志里也看不出为什么；
* 关掉档案开关 → 插件早就注册好了，不重启就一直说话。

现在：`handle_message` / 主动插话 / 出站动作三处都先问一次这个模块，
两条框架读的是**同一份**开关。

⚠ **只判 `ai_disabled`（总开关），不判平台开关**（`platforms.qq` / `platforms.wechat`）。
档案一启用，程序侧就会把这两个平台开关**故意**置成 false
（`core/bot.py::ai_platform_env`："李清菡接管 QQ 与微信，旧 AI 插件全部停用避免双回复"）。
跟着平台开关判，等于把李清菡自己关掉 —— 那是另一个坑，别踩。
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("liqinghan.gates")

_ENV_KEY = "QBM_AGENT_PROFILE_ENABLED"
_last_logged = ""


def _hotconfig():
    """机器人侧热配置模块（`qbotmanager.core.hotconfig`）；老装机没有就返回 None。"""
    try:
        from qbotmanager.core import hotconfig as hc
        return hc
    except Exception:  # noqa: BLE001 —— 老装机 / 单测环境没有它，属预期
        return None


def env_path() -> Path:
    """机器人 `.env` 的路径（`plugins/liqinghan/gates.py` → parents[3] = 机器人目录）。"""
    return Path(__file__).resolve().parents[3] / ".env"


def ai_disabled() -> bool:
    """AI 总开关是不是关着（读的就是 AI 插件读的那份快照）。"""
    hc = _hotconfig()
    if hc is None:
        return False
    try:
        return bool(hc.ai_disabled())
    except Exception:  # noqa: BLE001 —— 读不动就当没关（绝不因为读配置失败停机）
        return False


def profile_enabled() -> bool:
    """档案开关（热读：进程环境变量 → 机器人 `.env`）。

    三种情况分得很清：
      · 环境变量或 `.env` 里 `QBM_AGENT_PROFILE_ENABLED=1` → 启用；
      · `.env` 读得到、但这个键不是 1（或键已经不在了）→ **程序侧把它关了**，停；
      · `.env` 读不到（文件没了 / 读不动）→ **保持运行**（fail-open）。
        插件既然已经起来了，因为一次读盘失败就静默停机，比"设置不生效"更难排查。
    """
    if str(os.environ.get(_ENV_KEY, "")).strip() == "1":
        return True
    try:
        raw = env_path().read_text(encoding="utf-8")
    except OSError:
        return True
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == _ENV_KEY:
            return value.strip().strip('"').strip("'") == "1"
    return False


def blocked_reason() -> str:
    """该不该拦；该拦返回**人话原因**，不该拦返回空串（每轮都能调，很便宜）。"""
    if ai_disabled():
        return ("AI 总开关是关的（热配置 ai_disabled=true），李清菡不说话也不主动插话；"
                "在客户端把总开关打开即恢复，不用重启")
    if not profile_enabled():
        return ("智能体档案开关是关的（机器人 .env 里 %s 不是 1），李清菡不处理消息；"
                "在「AI 大脑 → 智能体档案」打开即恢复" % _ENV_KEY)
    return ""


def blocked_once() -> str:
    """`blocked_reason()` + 日志限流：同一个原因只打一次，别每条消息刷屏。"""
    global _last_logged
    reason = blocked_reason()
    if reason and reason != _last_logged:
        log.info("[liqinghan] %s", reason)
    _last_logged = reason
    return reason
