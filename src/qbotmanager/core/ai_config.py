# -*- coding: utf-8 -*-
"""内置 AI 插件配置桥：服务商预设 + 读写机器人侧 aichat_manager.json。

ai 插件（assets/plugins/ai）使用 nonebot_plugin_localstore 存储配置；
.env 已设置 LOCALSTORE_USE_CWD=true，localstore 落在机器人目录内：
  - 管理配置：<bot>/config/ai/aichat_manager.json
  - 记忆：     <bot>/data/ai/aichat_memory.json
  - 身份绑定： <bot>/config/ai/aichat_identity.json
"""
import re
import json
import shutil
import time
from pathlib import Path

from . import json_cache

MANAGER_NAME = "aichat_manager.json"
MEMORY_NAME = "aichat_memory.json"
IDENTITY_NAME = "aichat_identity.json"
QQ_GROUP_STATE_NAME = "qq_group_state.json"

_DEFAULT_PERSONALITY = "你是星群（AstroSwarm）的 AI 助手，负责跨平台对话与记忆管理。回答保持简洁直接，情绪随心情波动，回答长短看情况，只给关键信息，不啰嗦。"

# 常见大厂 OpenAI 兼容接口预设：切换服务商自动填 base_url + 默认模型
AI_PROVIDERS = [
    {"key": "custom", "name": "自定义接口", "url": "", "model": ""},
    {"key": "deepseek", "name": "DeepSeek", "url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    {"key": "dashscope", "name": "通义千问（百炼）", "url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.7-flash"},
    {"key": "zhipu", "name": "智谱 GLM", "url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.2"},
    {"key": "moonshot", "name": "Kimi（Moonshot）", "url": "https://api.moonshot.cn/v1", "model": "kimi-k3"},
    {"key": "openai", "name": "OpenAI", "url": "https://api.openai.com/v1", "model": "gpt-5.6"},
]

PROVIDER_BY_URL = {}
for _p in AI_PROVIDERS:
    if _p["url"]:
        PROVIDER_BY_URL[_p["url"].rstrip("/")] = _p["key"]


def normalize_api_url(url: str) -> str:
    """规范化 OpenAI 兼容接口地址，兼容用户误贴完整 /chat/completions 的情况。"""
    u = (url or "").strip()
    if not u:
        return ""
    lowered = u.lower()
    if lowered.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    return u.rstrip("/")


def _first_existing(*paths: Path) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _manager_candidates(settings) -> list:
    """管理配置候选路径：localstore use_cwd 规范路径优先。"""
    bot = settings.bot_dir
    return [
        bot / "config" / "ai" / MANAGER_NAME,
        bot / "data" / "config" / "ai" / MANAGER_NAME,
    ]


def manager_file(settings) -> Path:
    """机器人侧 AI 管理配置文件（规范路径优先，旧路径回退）。"""
    found = _first_existing(*_manager_candidates(settings))
    return found if found is not None else _manager_candidates(settings)[0]


def manager_write_file(settings) -> Path:
    """写入用固定规范路径：插件 ai 的 localstore（use_cwd）读 config/ai，旧路径仅迁移。"""
    return settings.bot_dir / "config" / "ai" / MANAGER_NAME


def memory_file(settings) -> Path:
    bot = settings.bot_dir
    return bot / "data" / "ai" / MEMORY_NAME


def identity_file(settings) -> Path:
    bot = settings.bot_dir
    return _first_existing(
        bot / "config" / "ai" / IDENTITY_NAME,
        bot / "data" / "config" / "ai" / IDENTITY_NAME,
    ) or (bot / "config" / "ai" / IDENTITY_NAME)


def read_manager(settings) -> dict:
    """读取 ai 插件管理配置；无文件返回空 dict。

    走带缓存的读（core/json_cache）：AI 大脑页每次心跳都会读它，
    以前每 2 秒真读一次盘，白白占界面线程。
    """
    return json_cache.load_json(manager_file(settings))


def current_config(settings) -> dict:
    """当前使用的 AI 配置（name/api_url/model/api_key），未配置返回空 dict。"""
    mgr = read_manager(settings)
    configs = mgr.get("ai_configs") or []
    idx = int(mgr.get("current_ai_config") or 0)
    if configs and 0 <= idx < len(configs):
        return configs[idx] if isinstance(configs[idx], dict) else {}
    return {}


def _default_manager() -> dict:
    return {
        "super_users": [],
        "enabled_groups": [],
        "group_chat_enabled": True,
        "chat_enabled": True,
        "ai_configs": [],
        "current_ai_config": 0,
        "image_recognition_enabled": False,
        "current_image_recognition_config": 0,
        "enable_search": False,
        "mcp_enabled": False,
        "mcp_servers": {},
        "personality": _DEFAULT_PERSONALITY,
        "proactive_enabled": False,
        "proactive_targets": [],
        "proactive_groups": [],
        "proactive_interval_min": 30,
        "proactive_interval_max": 90,
        "proactive_cooldown": 30,
        "proactive_quiet_start": 0,
        "proactive_quiet_end": 8,
        "memory_enabled": True,
        # 内置记忆档位：off / basic / smart（缺省 basic = 线上现行为）
        "memory_level": "basic",
        # 「空 @」（@ 了但没正文）怎么办：greet = 轻唤一句（默认）/ silent = 不回复
        "empty_at_action": "greet",
        # 上文回溯上限（分钟）：超过这个时间的旧消息不进提示词（除非被显式引用）
        "context_window_minutes": 5,
        # 单条正文上限（字）：别人发来的一条消息最多读多少字，0 = 不限制
        "max_incoming_chars": 4000,
        # 分条发送（老板 2026-10-12）：模式 + 上限 + 节奏
        "multi_message_mode": "auto",
        "max_reply_parts": 3,
        "max_part_chars": 500,
        "part_delay_ms": 80,
        "part_delay_max_ms": 3000,
    }


# ===== 空 @ 行为 + 上文回溯上限（2026-10-11 老板报的"空@答了一小时前的内容"）=====
EMPTY_AT_ACTIONS = ("greet", "silent")
EMPTY_AT_LABELS = {"greet": "轻唤一句", "silent": "不回复"}
EMPTY_AT_HINTS = {
    "greet": "轻唤一句：只回一句人设语气的短句（如「嗯？」「在的」），不带 @、不答上文。",
    "silent": "不回复：只 @ 一下、没写内容时，干脆什么都不说。",
}
CONTEXT_WINDOW_DEFAULT = 5
CONTEXT_WINDOW_MIN = 1
CONTEXT_WINDOW_MAX = 24 * 60
CONTEXT_WINDOW_NOTE = (
    "上文回溯上限：群里旧消息超过这个时间就不进提示词（默认 5 分钟）。"
    "**被显式引用（回复某条）时不受这个限制** —— 引用哪条就答哪条。")


def normalize_empty_at(value) -> str:
    """空 @ 行为规范化（认中文写法，非法值回默认 greet）。"""
    raw = str(value or "").strip().lower()
    if raw in EMPTY_AT_ACTIONS:
        return raw
    if raw in ("不回复", "不回", "静默", "none", "off", "silent"):
        return "silent"
    if raw in ("轻唤", "回应", "greet", "hello", "on"):
        return "greet"
    return "greet"


def read_empty_at(settings) -> str:
    """空 @ 怎么办：greet（默认）/ silent。"""
    mgr = read_manager(settings) or {}
    return normalize_empty_at(mgr.get("empty_at_action"))


def read_context_window_minutes(settings) -> int:
    """上文回溯上限（分钟）。老配置没这个键 → 默认 5。"""
    mgr = read_manager(settings) or {}
    try:
        value = int(mgr.get("context_window_minutes", CONTEXT_WINDOW_DEFAULT))
    except (TypeError, ValueError):
        return CONTEXT_WINDOW_DEFAULT
    return max(CONTEXT_WINDOW_MIN, min(CONTEXT_WINDOW_MAX, value))


def save_empty_at(settings, value) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["empty_at_action"] = normalize_empty_at(value)
    return _write_manager(settings, mgr)


def save_context_window_minutes(settings, value) -> bool:
    mgr = read_manager(settings) or _default_manager()
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = CONTEXT_WINDOW_DEFAULT
    mgr["context_window_minutes"] = max(CONTEXT_WINDOW_MIN,
                                        min(CONTEXT_WINDOW_MAX, minutes))
    return _write_manager(settings, mgr)


def empty_at_info() -> dict:
    """给界面用的一份口径（桌面端与控制台共用）。"""
    return {
        "key": "empty_at_action",
        "label": "只 @ 不写字时",
        "note": "群里只 @ 了它、没写内容：轻唤一句（默认）或干脆不回复。**两种都不会回答上文**。",
        "default": "greet",
        "options": [{"value": k, "label": EMPTY_AT_LABELS[k], "hint": EMPTY_AT_HINTS[k]}
                    for k in EMPTY_AT_ACTIONS],
    }


def _write_manager(settings, mgr: dict) -> bool:
    try:
        p = manager_write_file(settings)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 首次切换到规范路径时，把旧路径数据带过来，避免丢配置
        if not p.exists():
            for old in _manager_candidates(settings):
                if old != p and old.exists():
                    shutil.copy2(old, p)
                    break
        p.write_text(json.dumps(mgr, ensure_ascii=False, indent=2), encoding="utf-8")
        json_cache.invalidate(p)      # 刚写过，别让下一次读命中旧缓存
        return True
    except OSError:
        return False


# ===== 分条发送（2026-10-12 老板要求：像人一样分几条发，但别做死成"所有回复都多条"）=====
# 五个值都写在机器人侧 aichat_manager.json，机器人**每条消息现读**（brain 走
# chat_manager.get_*）→ 控制台改完**立即生效，不用重启机器人**。
MULTI_MESSAGE_MODES = ("off", "auto", "always")
MULTI_MESSAGE_LABELS = {
    "off": "关（永远只发一条）",
    "auto": "自动（默认：由内容决定）",
    "always": "总是（尽量分条）",
}
MULTI_MESSAGE_HINTS = {
    "off": "不管 AI 写了多少段，都拼成一条发出去。",
    "auto": "该分的分（安慰、解释、多步说明、情绪递进），一句话能说完的就一条。",
    "always": "有多少段就发多少段（最多到上限）。**测试或特殊人设才用**："
              "官方机器人通道分多条会把主动消息额度烧掉。",
}
MULTI_MESSAGE_NOTE = (
    "分条：AI 可以像人一样把一条回复拆成几条发出去。默认「自动」= 由内容决定，"
    "简单应答仍然只发一条（不是所有回复都分条）。"
    "QQ 第三方通道（NapCat）可以连发；**QQ 官方机器人、微信通道默认合并成一条**"
    "（官方主动消息有额度，分多条会把额度烧掉）。")
MULTI_MESSAGE_DEFAULT = "auto"

MAX_REPLY_PARTS_DEFAULT = 3
MAX_REPLY_PARTS_MIN = 1
MAX_REPLY_PARTS_MAX = 6
MAX_REPLY_PARTS_NOTE = "一次回复最多分几条（默认 3，1~6）。"

MAX_PART_CHARS_DEFAULT = 500
MAX_PART_CHARS_MIN = 50
MAX_PART_CHARS_MAX = 2000
MAX_PART_CHARS_NOTE = ("每一条最多多少字（默认 500，50~2000）。超了截断，"
                       "**不会**为了塞下内容把一条长回复硬拆成好几条。")

PART_DELAY_MS_DEFAULT = 80
PART_DELAY_MS_MIN = 0
PART_DELAY_MS_MAX = 500
PART_DELAY_MS_NOTE = ("条与条之间的间隔按字数算：每字多少毫秒（默认 80，0~500），"
                      "再加一点随机抖动，避免每条间隔一模一样。")

PART_DELAY_MAX_MS_DEFAULT = 3000
PART_DELAY_MAX_MS_MIN = 300
PART_DELAY_MAX_MS_MAX = 5000
PART_DELAY_MAX_MS_NOTE = ("单条之间最多等多久（毫秒，默认 3000，300~5000）。"
                          "下限 300 保证不会 0 毫秒连发；上限保证不会等 10 秒。")

MAX_INCOMING_CHARS_DEFAULT = 4000
MAX_INCOMING_CHARS_MIN = 200
MAX_INCOMING_CHARS_MAX = 100000
MAX_INCOMING_CHARS_NOTE = ("别人发来的一条消息最多读多少字（默认 4000，200~100000；"
                           "0 = 不限制）。粘超长文进来时先截断，别让它变成一次超大请求。")


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize_multi_message_mode(value) -> str:
    """分条模式规范化：认中文写法；写坏了回默认 auto（不回 off，别让功能悄悄消失）。"""
    raw = str(value or "").strip().lower()
    if raw in MULTI_MESSAGE_MODES:
        return raw
    if raw in ("关", "关闭", "单条", "一条", "single", "none"):
        return "off"
    if raw in ("总是", "总是分条", "强制", "force", "all", "on"):
        return "always"
    return "auto"


def read_multi_message(settings) -> dict:
    """分条发送的五个值（缺键一律回默认值）。"""
    mgr = read_manager(settings) or {}
    return {
        "mode": normalize_multi_message_mode(mgr.get("multi_message_mode")),
        "max_parts": _clamp_int(mgr.get("max_reply_parts"), MAX_REPLY_PARTS_DEFAULT,
                                MAX_REPLY_PARTS_MIN, MAX_REPLY_PARTS_MAX),
        "max_part_chars": _clamp_int(mgr.get("max_part_chars"), MAX_PART_CHARS_DEFAULT,
                                     MAX_PART_CHARS_MIN, MAX_PART_CHARS_MAX),
        "part_delay_ms": _clamp_int(mgr.get("part_delay_ms"), PART_DELAY_MS_DEFAULT,
                                    PART_DELAY_MS_MIN, PART_DELAY_MS_MAX),
        "part_delay_max_ms": _clamp_int(mgr.get("part_delay_max_ms"),
                                        PART_DELAY_MAX_MS_DEFAULT,
                                        PART_DELAY_MAX_MS_MIN, PART_DELAY_MAX_MS_MAX),
    }


def save_multi_message(settings, patch: dict) -> bool:
    """只写传进来的键（一个都不传 = 没改过，返回 False）。"""
    patch = patch if isinstance(patch, dict) else {}
    mgr = read_manager(settings) or _default_manager()
    changed = False
    if "mode" in patch:
        value = normalize_multi_message_mode(patch["mode"])
        if mgr.get("multi_message_mode") != value:
            mgr["multi_message_mode"] = value
            changed = True
    if "max_parts" in patch:
        value = _clamp_int(patch["max_parts"], MAX_REPLY_PARTS_DEFAULT,
                           MAX_REPLY_PARTS_MIN, MAX_REPLY_PARTS_MAX)
        if mgr.get("max_reply_parts") != value:
            mgr["max_reply_parts"] = value
            changed = True
    if "max_part_chars" in patch:
        value = _clamp_int(patch["max_part_chars"], MAX_PART_CHARS_DEFAULT,
                           MAX_PART_CHARS_MIN, MAX_PART_CHARS_MAX)
        if mgr.get("max_part_chars") != value:
            mgr["max_part_chars"] = value
            changed = True
    if "part_delay_ms" in patch:
        value = _clamp_int(patch["part_delay_ms"], PART_DELAY_MS_DEFAULT,
                           PART_DELAY_MS_MIN, PART_DELAY_MS_MAX)
        if mgr.get("part_delay_ms") != value:
            mgr["part_delay_ms"] = value
            changed = True
    if "part_delay_max_ms" in patch:
        value = _clamp_int(patch["part_delay_max_ms"], PART_DELAY_MAX_MS_DEFAULT,
                           PART_DELAY_MAX_MS_MIN, PART_DELAY_MAX_MS_MAX)
        if mgr.get("part_delay_max_ms") != value:
            mgr["part_delay_max_ms"] = value
            changed = True
    if not changed:
        return False
    return _write_manager(settings, mgr)


def multi_message_info() -> dict:
    """给界面用的一份口径（桌面端与控制台共用）。"""
    return {
        "note": MULTI_MESSAGE_NOTE,
        "default_mode": MULTI_MESSAGE_DEFAULT,
        "modes": [{"value": k, "label": MULTI_MESSAGE_LABELS[k],
                   "hint": MULTI_MESSAGE_HINTS[k]} for k in MULTI_MESSAGE_MODES],
        "limits": {
            "max_parts": {"default": MAX_REPLY_PARTS_DEFAULT, "min": MAX_REPLY_PARTS_MIN,
                          "max": MAX_REPLY_PARTS_MAX, "note": MAX_REPLY_PARTS_NOTE},
            "max_part_chars": {"default": MAX_PART_CHARS_DEFAULT,
                               "min": MAX_PART_CHARS_MIN, "max": MAX_PART_CHARS_MAX,
                               "note": MAX_PART_CHARS_NOTE},
            "part_delay_ms": {"default": PART_DELAY_MS_DEFAULT, "min": PART_DELAY_MS_MIN,
                              "max": PART_DELAY_MS_MAX, "note": PART_DELAY_MS_NOTE},
            "part_delay_max_ms": {"default": PART_DELAY_MAX_MS_DEFAULT,
                                  "min": PART_DELAY_MAX_MS_MIN,
                                  "max": PART_DELAY_MAX_MS_MAX,
                                  "note": PART_DELAY_MAX_MS_NOTE},
        },
    }


def read_max_incoming_chars(settings) -> int:
    """别人发来的一条消息最多读多少字（0 = 不限制；非数字回默认 4000）。"""
    mgr = read_manager(settings) or {}
    raw = mgr.get("max_incoming_chars", MAX_INCOMING_CHARS_DEFAULT)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return MAX_INCOMING_CHARS_DEFAULT
    if value <= 0:
        return 0
    return max(MAX_INCOMING_CHARS_MIN, min(MAX_INCOMING_CHARS_MAX, value))


def save_max_incoming_chars(settings, value) -> bool:
    """设置单条正文上限（0 = 不限制，其余夹到 200~100000）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    if number > 0:
        number = max(MAX_INCOMING_CHARS_MIN, min(MAX_INCOMING_CHARS_MAX, number))
    mgr = read_manager(settings) or _default_manager()
    if int(mgr.get("max_incoming_chars", MAX_INCOMING_CHARS_DEFAULT) or 0) == number:
        return False
    mgr["max_incoming_chars"] = number
    return _write_manager(settings, mgr)


def max_incoming_chars_info() -> dict:
    """单条正文上限的一份口径（给界面用）。"""
    return {
        "default": MAX_INCOMING_CHARS_DEFAULT,
        "min": MAX_INCOMING_CHARS_MIN,
        "max": MAX_INCOMING_CHARS_MAX,
        "note": MAX_INCOMING_CHARS_NOTE,
    }


def save_config(settings, api_key: str, api_url: str, model: str, name: str = "main") -> bool:
    """把 API 配置写入机器人侧 aichat_manager.json 并设为当前配置。

    只更新 ai_configs 里同名的项（没有则追加），不动其它设置（MCP/记忆/群聊等）。
    机器人下次启动（或 ac config 命令重载）后生效。
    """
    mgr = read_manager(settings)
    if not mgr:
        mgr = _default_manager()
    configs = mgr.setdefault("ai_configs", [])
    if not isinstance(configs, list):
        configs = []
        mgr["ai_configs"] = configs
    entry = {
        "name": name,
        "api_key": (api_key or "").strip(),
        "api_url": normalize_api_url(api_url),
        "model": (model or "").strip(),
    }
    for i, cfg in enumerate(configs):
        if isinstance(cfg, dict) and cfg.get("name") == name:
            configs[i] = entry
            break
    else:
        configs.append(entry)
    mgr["current_ai_config"] = configs.index(entry)
    return _write_manager(settings, mgr)


def read_personality(settings) -> str:
    """读取机器人侧人设；未配置返回默认人设。"""
    mgr = read_manager(settings)
    return str(mgr.get("personality") or _default_manager()["personality"])


def save_personality(settings, text: str) -> bool:
    """把人设写入机器人侧 aichat_manager.json（重启机器人生效）。"""
    mgr = read_manager(settings) or _default_manager()
    mgr["personality"] = (text or "").strip()
    return _write_manager(settings, mgr)


def read_proactive(settings) -> dict:
    """读取主动聊天配置。"""
    mgr = read_manager(settings)
    return {
        "enabled": bool(mgr.get("proactive_enabled", False)),
        "targets": list(mgr.get("proactive_targets") or []),
        "groups": list(mgr.get("proactive_groups") or []),
        "interval_min": int(mgr.get("proactive_interval_min", 30)),
        "interval_max": int(mgr.get("proactive_interval_max", 90)),
        "cooldown": int(mgr.get("proactive_cooldown", 30)),
        "quiet_start": int(mgr.get("proactive_quiet_start", 0)),
        "quiet_end": int(mgr.get("proactive_quiet_end", 8)),
    }


def save_proactive(
    settings,
    enabled: bool,
    targets: list,
    groups: list,
    interval_min: int = 30,
    interval_max: int = 90,
    cooldown: int = 30,
    quiet_start: int = 0,
    quiet_end: int = 8,
) -> bool:
    """把主动聊天配置写入机器人侧 aichat_manager.json（重启机器人生效）。"""
    mgr = read_manager(settings) or _default_manager()
    mgr["proactive_enabled"] = bool(enabled)
    mgr["proactive_targets"] = [str(x).strip() for x in targets if str(x).strip()]
    mgr["proactive_groups"] = [str(x).strip() for x in groups if str(x).strip()]
    mgr["proactive_interval_min"] = max(1, int(interval_min))
    mgr["proactive_interval_max"] = max(1, int(interval_max))
    if mgr["proactive_interval_max"] < mgr["proactive_interval_min"]:
        mgr["proactive_interval_max"] = mgr["proactive_interval_min"]
    mgr["proactive_cooldown"] = max(1, int(cooldown))
    mgr["proactive_quiet_start"] = max(0, min(23, int(quiet_start)))
    mgr["proactive_quiet_end"] = max(0, min(23, int(quiet_end)))
    return _write_manager(settings, mgr)


def read_memory_enabled(settings) -> bool:
    """全局记忆开关（默认开启）。"""
    mgr = read_manager(settings)
    return bool(mgr.get("memory_enabled", True))


# ===== 内置记忆三档（2026-10-11 老板拍板：无 / 基础 / 智能）=====
# 存的是**机器人侧** aichat_manager.json 的 memory_level 键（桌面端 AI 大脑页与
# 控制台 /api/ai/settings 共用同一份），机器人进程侧由 core.bot.build_bot_env()
# 注入环境变量 ASTROSWARM_MEMORY_LEVEL（.env 不落 os.environ，只能走 env 注入）。
MEMORY_LEVELS = ("off", "basic", "smart")
MEMORY_LEVEL_DEFAULT = "basic"
MEMORY_LEVEL_LABELS = {"off": "无", "basic": "基础", "smart": "智能"}
MEMORY_LEVEL_HINTS = {
    "off": "无：完全不记也不读，等于回到没有记忆的状态。",
    "basic": "基础：只记事实和日记，每次带上最近 3 条记忆——现在线上就是这一档。",
    "smart": "智能：在基础之上加权重/承诺必带、时间线、印象档合并、隐私过滤与可查可删。",
}
MEMORY_LEVEL_NOTE = ("无 = 不记不读；基础 = 记事实和日记、每次带最近 3 条（当前线上行为）；"
                     "智能 = 再加高权重与承诺必带、时间线、印象档合并、隐私过滤、可查可删。")


def normalize_memory_level(value) -> str:
    """把任意输入收成 off/basic/smart（认中文「无/基础/智能」，别的一律回退默认档）。"""
    raw = str(value or "").strip().lower()
    if raw in MEMORY_LEVELS:
        return raw
    for key, label in MEMORY_LEVEL_LABELS.items():
        if raw == label:
            return key
    return MEMORY_LEVEL_DEFAULT


def read_memory_level(settings) -> str:
    """记忆档位。老配置没有这个键 → 按「基础」（保住线上现行为，不悄悄升级）。"""
    mgr = read_manager(settings) or {}
    return normalize_memory_level(mgr.get("memory_level"))


def read_memory_level_or_off(settings) -> str:
    """给机器人的有效档位：旧的「启用全局记忆」关掉时，等于是「无」。"""
    if not read_memory_enabled(settings):
        return "off"
    return read_memory_level(settings)


def save_memory_level(settings, value) -> bool:
    """写档位。选「无」时**同时**把旧的 memory_enabled 置 False —— 桌面端/控制台/
    机器人侧三处读的是同一份配置，两把开关不能各说各话。"""
    level = normalize_memory_level(value)
    mgr = read_manager(settings) or _default_manager()
    mgr["memory_level"] = level
    mgr["memory_enabled"] = level != "off"
    return _write_manager(settings, mgr)


def memory_level_info() -> dict:
    """三档的中文说明（桌面端与控制台共用同一份口径，别各写各的）。"""
    return {
        "key": "memory_level",
        "label": "记忆档位",
        "note": MEMORY_LEVEL_NOTE,
        "default": MEMORY_LEVEL_DEFAULT,
        "options": [{"value": k, "label": MEMORY_LEVEL_LABELS[k], "hint": MEMORY_LEVEL_HINTS[k]}
                    for k in MEMORY_LEVELS],
    }


def read_group_chat_enabled(settings) -> bool:
    """群聊回话总开关（默认开启：群里被 @ 就回，不再要求先加白名单）。"""
    mgr = read_manager(settings)
    return bool(mgr.get("group_chat_enabled", True))


def read_rich_parse_enabled(settings) -> bool:
    """群消息里的引用/聊天记录/图片要不要解析给 AI 看（默认开）。"""
    return bool(read_manager(settings).get("rich_parse_enabled", True))


def save_rich_parse_enabled(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["rich_parse_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


# 群里主动搭话（插件内核 interject_*，界面在 AI 大脑页）
INTERJECT_DEFAULTS = {
    "enabled": False,
    "probability": 0.3,
    "interval_min": 30,
    "interval_max": 90,
    "history_count": 10,
    "auto_participate": False,
}


def read_interject(settings) -> dict:
    """群聊搭话设置：插件里叫 interject_*，老配置没这些键就用默认值。"""
    mgr = read_manager(settings) or {}
    out = dict(INTERJECT_DEFAULTS)
    out["enabled"] = bool(mgr.get("interject_enabled", out["enabled"]))
    try:
        out["probability"] = max(0.0, min(1.0, float(mgr.get("interject_probability",
                                                             out["probability"]))))
    except (TypeError, ValueError):
        pass
    for key, name in (("interval_min", "interject_interval_min"),
                      ("interval_max", "interject_interval_max"),
                      ("history_count", "interject_history_count")):
        try:
            value = int(mgr.get(name, out[key]))
            if value > 0:
                out[key] = value
        except (TypeError, ValueError):
            pass
    out["auto_participate"] = bool(mgr.get("group_auto_participate",
                                           out["auto_participate"]))
    return out


def save_interject(settings, values: dict) -> bool:
    """保存群聊搭话设置（概率按 0~1 存，界面上是百分数）。"""
    mgr = read_manager(settings) or _default_manager()
    data = dict(INTERJECT_DEFAULTS)
    data.update(values or {})
    mgr["interject_enabled"] = bool(data["enabled"])
    try:
        prob = float(data["probability"])
    except (TypeError, ValueError):
        prob = INTERJECT_DEFAULTS["probability"]
    mgr["interject_probability"] = max(0.0, min(1.0, prob))
    for key, name in (("interval_min", "interject_interval_min"),
                      ("interval_max", "interject_interval_max"),
                      ("history_count", "interject_history_count")):
        try:
            mgr[name] = max(1, int(data[key]))
        except (TypeError, ValueError):
            mgr[name] = INTERJECT_DEFAULTS[key]
    if mgr["interject_interval_min"] > mgr["interject_interval_max"]:
        mgr["interject_interval_min"], mgr["interject_interval_max"] = (
            mgr["interject_interval_max"], mgr["interject_interval_min"])
    return _write_manager(settings, mgr)


# 免@连续对话（插件内核 session_*，界面在 AI 大脑页）
SESSION_DEFAULTS = {"mode": "timeout", "expire_seconds": 30}
SESSION_MODES = ("off", "timeout", "api")


def read_session(settings) -> dict:
    """免@连续对话设置：插件里叫 session_mode / session_expire_seconds。"""
    mgr = read_manager(settings) or {}
    out = dict(SESSION_DEFAULTS)
    mode = str(mgr.get("session_mode") or out["mode"]).strip().lower()
    out["mode"] = mode if mode in SESSION_MODES else out["mode"]
    try:
        seconds = int(mgr.get("session_expire_seconds", out["expire_seconds"]))
        out["expire_seconds"] = max(30, min(300, seconds))     # 与插件的夹取范围一致
    except (TypeError, ValueError):
        pass
    return out


def save_session(settings, values: dict) -> bool:
    """保存免@连续对话设置（模式 + 有效期秒数，30~300）。"""
    mgr = read_manager(settings) or _default_manager()
    data = dict(SESSION_DEFAULTS)
    data.update(values or {})
    mode = str(data.get("mode") or "").strip().lower()
    mgr["session_mode"] = mode if mode in SESSION_MODES else SESSION_DEFAULTS["mode"]
    try:
        seconds = int(data.get("expire_seconds"))
    except (TypeError, ValueError):
        seconds = SESSION_DEFAULTS["expire_seconds"]
    mgr["session_expire_seconds"] = max(30, min(300, seconds))
    return _write_manager(settings, mgr)


def known_groups(settings) -> list[dict]:
    """插件记下来的"见过消息的群"（openid + 最近时间 + 条数），按最近活跃排序。

    界面上让用户勾选"允许回话的群"用这个列表 —— 不用他手抄 openid。
    """
    state = read_qq_group_state(settings)
    groups = state.get("groups")
    if not isinstance(groups, dict):
        return []
    rows = []
    for openid, info in groups.items():
        info = info if isinstance(info, dict) else {}
        rows.append({
            "openid": str(openid),
            "last_ts": float(info.get("last_ts") or 0),
            "count": int(info.get("count") or 0),
        })
    rows.sort(key=lambda r: r["last_ts"], reverse=True)
    return rows


def allowed_groups(settings) -> list:
    """当前"只允许这些群"的白名单；空列表 = 不限群（默认）。"""
    return list(read_manager(settings).get("enabled_groups") or [])


def save_allowed_groups(settings, openids) -> bool:
    """保存群白名单：传空列表 = 不限群（回到默认）。"""
    mgr = read_manager(settings) or _default_manager()
    mgr["enabled_groups"] = [str(x).strip() for x in (openids or []) if str(x).strip()]
    return _write_manager(settings, mgr)


def qq_group_state_file(settings) -> Path:
    """机器人侧那份"群消息是哪种事件"的状态文件（插件收到群消息时写）。"""
    return settings.bot_dir / "config" / "ai" / QQ_GROUP_STATE_NAME


def read_qq_group_state(settings) -> dict:
    """读 QQ 官方通道的收消息模式状态；没有文件返回空 dict。"""
    return json_cache.load_json(qq_group_state_file(settings))


def qq_group_state_text(state: dict) -> str:
    """状态卡那一行（短）：全量 / 仅 @ / 还没收到过。"""
    st = state or {}
    mode = str(st.get("mode") or "none")
    when = _short_time(st.get("last_ts"))
    tail = f" · 最近 {when}" if when else ""
    if mode == "full":
        return "全量模式（群里每条都能收到）" + tail
    if mode == "at_only":
        return "仅 @ 消息（只在群里 @ 它时才收得到）" + tail
    return "还没收到过群消息（把它拉进群、在群里 @ 一次）"


def qq_group_state_hint(state: dict) -> str:
    """需要时说清"怎么才能收到全部群消息"；不需要时返回空串。"""
    if str((state or {}).get("mode") or "none") == "at_only":
        return ("当前只收得到 @ 它的消息。想让它看到群里全部聊天（能自己搭话、记上下文）："
                "去 q.qq.com → 你的机器人 → 打开「接收所有消息」，再重启一次机器人。")
    return ""


def short_time(ts) -> str:
    """今天只显示时分，其它日子带上月日；没时间戳返回空串。"""
    try:
        value = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    lt = time.localtime(value)
    now = time.localtime()
    fmt = "%H:%M" if (lt.tm_year, lt.tm_yday) == (now.tm_year, now.tm_yday) else "%m-%d %H:%M"
    return time.strftime(fmt, lt)


_short_time = short_time      # 兼容旧调用


def save_group_chat_enabled(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["group_chat_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


def save_memory_enabled(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["memory_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


# ===== 2026-10-02 新增的两个开关（桌面端与 Linux 控制台共用同一份读写）=====

def read_cross_platform(settings) -> bool:
    """跨平台互通总开关（发消息/发文件到用户自己的另一个平台）。默认**开**。"""
    mgr = read_manager(settings) or {}
    return bool(mgr.get("cross_platform_enabled", True))


def save_cross_platform(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["cross_platform_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


def read_identity_bindings(settings) -> list:
    """读"谁和谁算同一个人"（给界面显示）。

    绑定文件由机器人侧写（nonebot_plugin_localstore），桌面端只读来展示：
    `<机器人根>/bot/data/nonebot_plugin_localstore/aichat_identity.json`
    """
    import json
    from pathlib import Path

    # 2026-10-12：插件实际写在 bot/config/ai/ 下（nonebot_plugin_localstore 配了
    # LOCALSTORE_USE_CWD=true，按工作目录解析）。以前只读老的两处 →
    # 明明绑好了、界面上却一直显示"还没绑定"。
    candidates = [
        Path(getattr(settings, "bot_dir", "")) / "config" / "ai" / "aichat_identity.json",
        Path(getattr(settings, "bot_dir", "")) / "data" / "nonebot_plugin_localstore" / "aichat_identity.json",
        Path(getattr(settings, "bot_dir", "")) / "cache" / "nonebot_plugin_localstore" / "aichat_identity.json",
    ]
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(data, dict):
                    return [list(v) for v in data.values()
                            if isinstance(v, list) and len(v) > 1]
        except (OSError, ValueError):
            continue
    return []


def read_auto_approve_friend(settings) -> bool:
    """要不要自动通过好友申请（卖家场景）。默认**关** —— 开了就是"谁加都通过"。"""
    mgr = read_manager(settings) or {}
    return bool(mgr.get("auto_approve_friend", False))


def save_auto_approve_friend(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["auto_approve_friend"] = bool(enabled)
    return _write_manager(settings, mgr)


def read_agent_mode(settings) -> bool:
    """Agent 模式：先动手把事办了再说话（默认**开**）。

    只管行为；人设依旧只管口吻。机器人侧读的是同一个键 `agent_mode_enabled`。
    """
    mgr = read_manager(settings) or {}
    return bool(mgr.get("agent_mode_enabled", True))


def save_agent_mode(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["agent_mode_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


def read_self_forge(settings) -> bool:
    """AI 能不能在聊天里给自己写插件（"自学"）。默认**开**。

    只有主人/管理员能触发（群里谁都能触发 = 谁都能烧 token，这条在工具里卡死）。
    """
    mgr = read_manager(settings) or {}
    return bool(mgr.get("self_forge_enabled", True))


def save_self_forge(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["self_forge_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


def read_image_recognition(settings) -> bool:
    """要不要让模型看图片（识图）。默认**开** —— 关着时 AI 只会说"我看不到图"。"""
    mgr = read_manager(settings) or {}
    return bool(mgr.get("image_recognition_enabled", True))


def save_image_recognition(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["image_recognition_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


def read_group_welcome(settings) -> bool:
    """被拉进新群时要不要打招呼（QQ 官方，用事件 id 被动回复不占额度）；默认开。"""
    mgr = read_manager(settings) or {}
    return bool(mgr.get("group_welcome_enabled", True))


def save_group_welcome(settings, enabled: bool) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["group_welcome_enabled"] = bool(enabled)
    return _write_manager(settings, mgr)


def read_send_targets(settings) -> list:
    """插件主动发送的白名单（形如 group:123 / user:abc）；**留空 = 一个都不许发**。"""
    mgr = read_manager(settings) or {}
    raw = mgr.get("allowed_send_targets") or []
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def save_send_targets(settings, targets) -> bool:
    mgr = read_manager(settings) or _default_manager()
    clean = [str(x).strip() for x in (targets or []) if str(x).strip()]
    mgr["allowed_send_targets"] = clean
    return _write_manager(settings, mgr)


def read_mcp(settings) -> dict:
    """读取 MCP 总开关与服务器配置。"""
    mgr = read_manager(settings)
    servers = mgr.get("mcp_servers")
    return {
        "enabled": bool(mgr.get("mcp_enabled", False)),
        "servers": servers if isinstance(servers, dict) else {},
    }


def save_mcp(settings, enabled: bool, servers: dict) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["mcp_enabled"] = bool(enabled)
    mgr["mcp_servers"] = servers if isinstance(servers, dict) else {}
    return _write_manager(settings, mgr)


# MCP 服务器允许的传输类型（桌面端页面的 JSON 编辑器与无头端 console_ext.mcp_save 同一套）
MCP_TYPES = ("sse", "stdio", "ws", "http", "streamable_http")


def normalize_mcp_servers(servers) -> dict:
    """校验并清洗 MCP 服务器配置，出错抛 ValueError（消息直接给用户看）。

    以前桌面端只检查「是不是 JSON 对象」就存盘：type 写错、stdio 没填 command、
    名称超长都会原样落盘，机器人重启后 MCP 直接起不来，用户只能自己猜哪儿写错了。
    这里按无头端的规则逐项校验 + 只保留认识的字段。
    """
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise ValueError('MCP 配置必须是 JSON 对象，例如 {"名称": {"type": "sse", "url": "..."}}')
    clean = {}
    for name, spec in servers.items():
        key = str(name).strip()
        if not key or len(key) > 64:
            raise ValueError(f"MCP 名称不合法：{name!r}（1~64 个字符，不能为空）")
        if not isinstance(spec, dict):
            raise ValueError(f"「{key}」的配置必须是对象，例如 {{\"type\": \"sse\", \"url\": \"...\"}}")
        typ = str(spec.get("type") or "sse").strip().lower()
        if typ not in MCP_TYPES:
            raise ValueError(f"「{key}」的 type 不支持：{typ}（只能是 {'/'.join(MCP_TYPES)}）")
        if typ == "stdio":
            if not str(spec.get("command") or "").strip():
                raise ValueError(f"「{key}」是 stdio，必须填 command")
        elif not str(spec.get("url") or "").strip():
            raise ValueError(f"「{key}」必须填 url")
        item = {"type": typ, "enabled": bool(spec.get("enabled", True))}
        for k in ("url", "command", "args", "env", "headers"):
            if spec.get(k) not in (None, "", [], {}):
                item[k] = spec[k]
        clean[key] = item
    return clean


def provider_key_for_url(url: str) -> str:
    return PROVIDER_BY_URL.get((url or "").rstrip("/"), "custom")

# ---------------------------------------------------------------- 本机能力清单
def _plugin_actions() -> list:
    """静态解析 ai 插件里的工具声明（名字/说明/权限）。

    为什么不 import：那个模块要 nonebot，桌面端不该为了"看一眼清单"把机器人依赖拖进来。
    """
    out = []
    f = Path(__file__).resolve().parent.parent / "assets" / "plugins" / "ai" / "tools_bridge.py"
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return out
    for m in re.finditer(r'_tool\(\s*"([a-z_]+)"\s*,\s*"([^"]{0,200})"', text):
        out.append({"name": m.group(1), "desc": m.group(2), "source": "内置 ai 插件"})
    return out


def _runtime_tool_names() -> list:
    """运行时（主程序内置）的工具名。拿不到就返回空，不影响界面。"""
    try:
        import sys as _sys
        from pathlib import Path as _P

        _sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
        from qbotmanager.core.agent.runtime import get_runtime  # noqa: PLC0415

        return sorted(t["function"]["name"] for t in get_runtime().schemas(""))
    except Exception:  # noqa: BLE001
        return []


def read_disabled_tools(settings) -> list:
    """被关掉的能力（开关写入这里；机器人侧读同一个键）。"""
    mgr = read_manager(settings) or {}
    names = mgr.get("disabled_tools")
    return [str(n) for n in names] if isinstance(names, list) else []


def save_disabled_tools(settings, names: list) -> bool:
    mgr = read_manager(settings) or _default_manager()
    mgr["disabled_tools"] = sorted({str(n) for n in (names or [])})
    return _write_manager(settings, mgr)


def set_tool_enabled(settings, name: str, enabled: bool) -> bool:
    cur = set(read_disabled_tools(settings))
    if enabled:
        cur.discard(str(name))
    else:
        cur.add(str(name))
    return save_disabled_tools(settings, sorted(cur))


def has_capability(settings, name: str) -> bool:
    """本机现在有没有这项能力（开关只在有能力时才显示）。

    老板 2026-10-12：「插件未安装情况下 AI 大脑的开关是不是该隐藏？」
    —— 是的：没有能力就不该摆一个按了也没用的开关。
    """
    try:
        return any(str(it.get("name")) == str(name)
                   for it in list_capabilities(settings))
    except Exception:  # noqa: BLE001 —— 查不到就当没有，宁可不显示
        return False


def list_capabilities(settings) -> list:
    """本机能调用的全部能力（给界面用）。每项：name/desc/source/permission/enabled。"""
    disabled = set(read_disabled_tools(settings))
    rows = []

    # 运行时工具（主程序内置）
    for name in _runtime_tool_names():
        rows.append({"name": name, "desc": "主程序内置工具", "source": "主程序",
                     "permission": "", "enabled": name not in disabled})

    # ai 插件里的工具
    perms = {}
    try:
        tb = (Path(__file__).resolve().parent.parent / "assets" / "plugins" / "ai"
              / "tools_bridge.py").read_text(encoding="utf-8")
        perms = dict(re.findall(r'^\s{4}"([a-z_]+)":\s*"([^"]*)",\s*$', tb, re.M))
    except OSError:
        perms = {}
    for item in _plugin_actions():
        rows.append({"name": item["name"], "desc": item["desc"], "source": item["source"],
                     "permission": perms.get(item["name"], ""),
                     "enabled": item["name"] not in disabled})

    # 已装能力包
    try:
        from . import tool_packs  # noqa: PLC0415

        for pack in (tool_packs.list_packs(settings) or []):
            name = pack.get("id") or pack.get("name") or ""
            rows.append({"name": f"包:{name}", "desc": pack.get("desc") or pack.get("title") or "",
                         "source": "能力包", "permission": "",
                         "enabled": True})
    except Exception:  # noqa: BLE001
        pass
    return rows