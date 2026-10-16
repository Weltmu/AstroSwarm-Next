# -*- coding: utf-8 -*-
"""插件侧的**开关热读**（AI 总开关 / 平台开关）。

为什么要有这个文件（2026-10-11 热重载，老板派活）：

以前这两个开关是在**插件 import 时**判断的：``AI_DISABLED=1`` 就整个不注册、
``AI_PLATFORM_QQ=0`` 就在建通道时静默 ``return None``。后果是「关掉之后改回来
必须重启机器人」，而且关掉时连插件都不注册，日志里什么都看不到 —— 今天已经
因此踩过坑。

现在改成：

* 插件**永远注册**（`__init__.py` 里不再有 import 期的开关判断）；
* **每条消息进来时**读一次开关（一次 ``os.stat``，微秒级）；
* 开关来源优先级：程序侧写的**有效开关快照**（热配置，改了立即生效）
  → 环境变量（老装机兜底）→ 默认开；
* 被挡掉时**一定有明确日志**（说清是哪个开关、值是多少、来源是哪儿）。
"""
import logging
import os
from typing import Optional

log = logging.getLogger("ai.gates")

_TRUE = ("1", "true", "yes", "on", "开", "启用")


def _hotconfig():
    """统一的热读模块（`qbotmanager.core.hotconfig`）；老装机没有就返回 None。"""
    try:
        from qbotmanager.core import hotconfig as _hc
        return _hc
    except Exception:  # noqa: BLE001 —— 老装机环境没有它，属预期
        return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in _TRUE


def source() -> str:
    """这一轮判定用的是哪份配置（日志里说清楚，免得又查半天）。"""
    hc = _hotconfig()
    if hc is None:
        return "环境变量（热配置模块缺失）"
    try:
        snap = hc.snapshot()
        if snap.get("exists"):
            return "热配置 %s" % snap.get("path")
    except Exception:  # noqa: BLE001
        pass
    return "环境变量（还没有热配置快照）"


def ai_disabled() -> bool:
    """AI 总开关是不是关着（`AI_DISABLED` / 快照 `ai_disabled`）。"""
    hc = _hotconfig()
    if hc is not None:
        try:
            live = hc.read_live()
            if "ai_disabled" in live:
                return bool(live.get("ai_disabled"))
        except Exception:  # noqa: BLE001
            pass
    return _env_flag("AI_DISABLED", False)


def platform_enabled(platform: str) -> bool:
    """某个平台的 AI 开关（快照优先，环境变量兜底，都没有按开）。"""
    key = str(platform or "").strip().lower()
    hc = _hotconfig()
    if hc is not None:
        try:
            live = hc.read_live()
            platforms = live.get("platforms")
            if isinstance(platforms, dict) and key in platforms:
                return bool(platforms[key])
        except Exception:  # noqa: BLE001
            pass
    return _env_flag("AI_PLATFORM_" + key.upper(), True)


def blocked_reason(platform: str = "") -> str:
    """这条消息该不该被开关挡掉；该挡返回**人话原因**，不该挡返回空串。

    调用方拿到非空串就 ``return``，并把这句话打进日志 —— 排查"它怎么不理我"
    的第一现场证据（以前这里是静默 return，什么都看不到）。
    """
    if ai_disabled():
        return ("AI 总开关是关的（AI_DISABLED=1 / 热配置 ai_disabled=true，来源 %s）"
                "，这条消息不处理。去「AI 大脑」或控制台把总开关打开即可，"
                "**不用重启机器人**。" % source())
    if platform and not platform_enabled(platform):
        return ("%s 平台的 AI 开关是关的（AI_PLATFORM_%s=0 / 热配置 platforms.%s=false，"
                "来源 %s），这条消息不处理。打开开关立即生效，**不用重启机器人**。"
                % (platform.upper(), platform.upper(), platform, source()))
    return ""


def status_line(platform: str = "") -> str:
    """给启动/自检日志用的一行现状（含来源），只读。"""
    bits = ["ai_disabled=%d" % (1 if ai_disabled() else 0)]
    if platform:
        bits.append("platform_%s=%d" % (platform, 1 if platform_enabled(platform) else 0))
    bits.append("来源=%s" % source())
    return " ".join(bits)


def probe(platform: str = "") -> Optional[str]:
    """显式探测：给「验证脚本/自检」用，返回人话结论（None = 没被挡）。"""
    return blocked_reason(platform) or None


def wake_words() -> list:
    """「被叫到称呼就回话」的称呼词表（热配置优先，环境变量兜底）。

    客户端把 AI 大脑里的「唤醒词」写进热配置快照（`wake_words`），这里热读 ——
    改完保存立即生效，不用重启机器人。老装机没有快照时回退读
    ``QBM_AI_WAKE_WORDS``（逗号分隔，中英文逗号都认）。
    """
    hc = _hotconfig()
    if hc is not None:
        try:
            live = hc.read_live()
            if "wake_words" in live:
                raw = live.get("wake_words")
                if isinstance(raw, (list, tuple)):
                    return [str(w).strip() for w in raw if str(w).strip()]
                if isinstance(raw, str):
                    return [w.strip() for w in raw.replace("，", ",").split(",")
                            if w.strip()]
        except Exception:  # noqa: BLE001 —— 快照坏了就回退环境变量
            pass
    raw = os.environ.get("QBM_AI_WAKE_WORDS", "") or ""
    return [w.strip() for w in str(raw).replace("，", ",").split(",") if w.strip()]
