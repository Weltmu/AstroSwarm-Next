"""
主动聊天模块（魔改新增）

功能：机器人会在随机（不定时）的时间间隔内，主动给配置好的目标用户发送
一条随机内容的消息，像朋友一样主动找用户聊天。

- 优先使用已配置的 AI 大模型生成一句自然的开场白（保持插件人设）
- AI 不可用或调用失败时，自动使用本地随机开场白池
- 同一用户有冷却时间，避免频繁打扰
- 支持设置免打扰时段（默认 00:00 - 08:00 不主动发消息）
"""

import asyncio
import random
from datetime import datetime
from typing import Dict, List, Optional

from nonebot import get_bots, get_driver, logger

from . import gates, reply_format
from .manager import chat_manager

# 本模块只负责在导入时注册后台任务，不向外面导出任何名字
__all__ = []

# 本地随机开场白池（AI 不可用或调用失败时的备用内容，可自行增删）
DEFAULT_OPENERS = [
    "在吗？今天过得怎么样呀",
    "嘿，突然想找你聊聊天，不忙吧？",
    "今天有什么有趣的事发生吗？说来听听",
    "我刚刚想到一个好玩的话题，你要不要猜猜是什么",
    "最近有没有看什么好看的剧或者电影呀",
    "如果现在给你一张任意门门票，你最想去哪里",
    "今天天气好像不错，你那边呢",
    "我有点无聊，你来陪我聊两句呗",
    "你平时周末都做些什么呀",
    "假如明天是假期，你最想干什么",
    "你最近有没有遇到什么开心的小事",
    "我发现自己越来越喜欢跟你聊天了",
    "问你个问题：你觉得晚饭吃什么最幸福",
    "刚看到一条很有意思的新闻，你想听听吗",
    "好久没跟你聊了，最近在忙什么呀",
    "突然想问问你，今天午饭吃的什么呀",
    "如果给你一次重新选择的机会，你想改掉什么事",
    "你相信缘分吗，反正我现在信了",
    "工作学习累不累呀，记得多休息",
    "我刚刚听了一首超好听的歌，下次分享给你",
]

# 记录每个用户上次发送时间（做冷却用）和上次的开场白（避免重复）
_last_send_time: Dict[str, float] = {}
_last_opener: Dict[str, str] = {}
_seq_counters: Dict[str, int] = {}

# 后台任务句柄
_proactive_task: Optional[asyncio.Task] = None

# ===== 魔改新增：群聊搭话 =====
_last_interject_time: Dict[str, float] = {}
_interject_task: Optional[asyncio.Task] = None


def _qq_bot():
    """返回当前连接的 QQ 官方机器人（主动消息只能走官方通道）。"""
    try:
        from nonebot.adapters.qq import Bot as QQBot
        for bot in get_bots().values():
            if isinstance(bot, QQBot):
                return bot
    except Exception:  # noqa: BLE001
        return None
    return None


# ===== 微信（iLink）主动消息 =====
_WX_PROACTIVE_MAX_AGE = 1800      # 秒：用户最近说过话才主动发，超过就不发（token 会失效）
_WX_DIR_CACHE = None


def _wx_dir():
    """适配器约定的目录：<bot>/data/nonebot_adapter_ilink（机器人 cwd 就是 bot 目录）。"""
    global _WX_DIR_CACHE
    if _WX_DIR_CACHE is None:
        from pathlib import Path as _P

        _WX_DIR_CACHE = _P.cwd() / "data" / "nonebot_adapter_ilink"
    return _WX_DIR_CACHE


def _wx_tokens() -> dict:
    """微信用户 -> {context_token, ts}，由 ilink 适配器每收到一条消息就写。"""
    import json as _json

    try:
        data = _json.loads((_wx_dir() / "ilink_tokens.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _wx_state() -> dict:
    import json as _json

    try:
        data = _json.loads((_wx_dir() / "ilink_state.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _wx_available() -> bool:
    """微信通道可用 = 有登录 token + 至少有一个用户会话 token。"""
    st = _wx_state()
    return bool(st.get("token")) and bool(_wx_tokens())


def _wx_client():
    """构造一个独立的 IlinkClient（运行期在 NoneBot 进程里，直接 import 包是安全的）。"""
    mod = None
    for name in ("src.plugins.nonebot_adapter_ilink.client", "nonebot_adapter_ilink.client"):
        try:
            mod = __import__(name, fromlist=["IlinkClient"])
            break
        except Exception:  # noqa: BLE001
            mod = None
    if mod is None:
        return None
    st = _wx_state()
    c = mod.IlinkClient(
        base_url=st.get("baseurl") or "https://ilinkai.weixin.qq.com",
        state_file=str(_wx_dir() / "ilink_state.json"),
        bot_type="3",
    )
    c.token = st.get("token", "")
    c.bot_id = st.get("bot_id", "")
    c.user_id = st.get("user_id", "")
    return c


async def _send_proactive_wechat(user_id: str) -> None:
    """给微信用户发主动消息（必须带缓存的 context_token，且会话不能太旧）。"""
    import time as _t

    info = _wx_tokens().get(user_id) or {}
    token = str(info.get("context_token") or "")
    if not token:
        logger.info(f"微信主动消息跳过：没有 {user_id} 的会话 token（让他先跟机器人说一句话）")
        return
    age = _t.time() - float(info.get("ts") or 0)
    if age > _WX_PROACTIVE_MAX_AGE:
        logger.info(
            f"微信主动消息跳过：{user_id} 已经 {age / 60:.0f} 分钟没说话，会话 token 多半失效了")
        return
    client = _wx_client()
    if client is None:
        logger.error("微信主动消息失败：拿不到 IlinkClient")
        return
    try:
        segments = await _build_opener_messages(user_id)
        for i, seg in enumerate(segments):
            ok = await client.send_text(user_id, token, seg)
            logger.info(f"微信主动消息 -> {user_id}: {'成功' if ok else '失败'} | {seg[:40]}")
            if i < len(segments) - 1:
                await asyncio.sleep(random.uniform(1.5, 3.0))
        _last_send_time[user_id] = datetime.now().timestamp()
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass


# ===== 能力包参数（proactive 包 1.1.0 起）=====
_PACK_DEFAULTS = {"enabled": True, "outreach_chance": 0.5, "min_interval_h": 24, "reply_window_h": 24, "max_unanswered": 3, "greeting_chance": 0.3, "quiet_start": 23, "quiet_end": 8, "rate_per_hour": 20, "platforms": ["qq", "wechat"]}
_PACK_CFG_CACHE = {"at": 0.0, "cfg": None}


def _pack_dir():
    from pathlib import Path as _P

    # bot 的 cwd 就是 bot 目录，data_home 是它的上一级
    return _P.cwd().parent / "tool_packs" / "proactive"


def _pack_cfg() -> dict:
    """读能力包参数：config.json（用户改的）> behavior.json（包默认）> 代码默认。"""
    import time as _t

    now = _t.time()
    if _PACK_CFG_CACHE["cfg"] is not None and now - _PACK_CFG_CACHE["at"] < 30:
        return _PACK_CFG_CACHE["cfg"]
    cfg = dict(_PACK_DEFAULTS)
    d = _pack_dir()
    import json as _json          # 本模块顶层没有 json，统一在函数里按需导入
    for name in ("behavior.json", "config.json"):      # 后者优先级更高
        try:
            raw = (d / name).read_text(encoding="utf-8")
            # 注意用 _json：本模块只在函数里 `import json as _json`（顶层没有 json 这个名字）。
            # 以前这里写的是裸 `json.loads` → NameError 被下面的 except 吞掉，
            # 表现是"能力包里的 proactive 参数永远不生效"（静默失效，查起来很贵）。
            data = _json.loads(raw)
            if isinstance(data, dict):
                # 允许 {"proactive": {...}} 或直接 {...}
                merged = data.get("proactive") if isinstance(data.get("proactive"), dict) else data
                for k, v in (merged or {}).items():
                    if k in cfg:
                        cfg[k] = v
        except Exception:  # noqa: BLE001
            pass
    _PACK_CFG_CACHE.update({"at": now, "cfg": cfg})
    return cfg


def _state_path():
    from pathlib import Path as _P

    try:
        return _pack_dir() / "state.json"
    except Exception:  # noqa: BLE001
        return _P.cwd().parent / "tool_packs" / "proactive" / "state.json"


def _state() -> dict:
    import json as _json

    try:
        return _json.loads(_state_path().read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _state_save(st: dict) -> None:
    import json as _json

    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _unanswered(user_id: str) -> int:
    """连着几条没回（用户在下一次说话后会清零）。"""
    st = _state()
    return int((st.get(user_id) or {}).get("unanswered") or 0)


def _note_sent(user_id: str, replied_since: bool) -> None:
    st = _state()
    rec = st.get(user_id) or {}
    rec["unanswered"] = 0 if replied_since else int(rec.get("unanswered") or 0) + 1
    rec["last_sent"] = datetime.now().timestamp()
    st[user_id] = rec
    _state_save(st)


def _sends_last_hour(user_id: str) -> int:
    st = _state()
    marks = (st.get(user_id) or {}).get("marks") or []
    now = datetime.now().timestamp()
    return len([m for m in marks if now - float(m) < 3600])


def _note_mark(user_id: str) -> None:
    st = _state()
    rec = st.get(user_id) or {}
    now = datetime.now().timestamp()
    marks = [m for m in (rec.get("marks") or []) if now - float(m) < 3600]
    marks.append(now)
    rec["marks"] = marks
    st[user_id] = rec
    _state_save(st)


def _in_quiet(cfg: dict) -> bool:
    import datetime as _dt

    try:
        h = _dt.datetime.now().hour
    except Exception:  # noqa: BLE001
        return False
    qs, qe = int(cfg.get("quiet_start", 23)), int(cfg.get("quiet_end", 8))
    if qs == qe:
        return False
    return (qs <= h or h < qe) if qs > qe else (qs <= h < qe)


def _next_seq(key: str) -> int:
    """QQ 官方主动消息要求 msg_seq 递增，按目标维护计数器。"""
    _seq_counters[key] = _seq_counters.get(key, 0) + 1
    return _seq_counters[key]


def _is_quiet_hour() -> bool:
    """判断当前时间是否处于免打扰时段（支持跨天，如 23-7）"""
    start, end = chat_manager.get_proactive_quiet_hours()
    hour = datetime.now().hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _is_in_cooldown(user_id: str) -> bool:
    """检查某个用户是否还在冷却时间内（刚聊过）"""
    cooldown = chat_manager.get_proactive_cooldown()
    if cooldown <= 0:
        return False
    last_time = _last_send_time.get(user_id)
    if last_time is None:
        return False
    return (datetime.now().timestamp() - last_time) < cooldown * 60


def _pick_local_opener(user_id: str) -> str:
    """从本地开场白池随机挑一句，尽量不与上次重复"""
    pool = [opener for opener in DEFAULT_OPENERS if opener != _last_opener.get(user_id)]
    if not pool:
        pool = DEFAULT_OPENERS
    opener = random.choice(pool)
    _last_opener[user_id] = opener
    return opener


def _opener_context_key(user_id: str, platform: str = "") -> str:
    """主动消息的上下文 key —— 与正常会话同一口径（2026-10-15 审计）。

    以前这里是裸 `str(user_id)`，而用户回话时 brain 按
    `platform.context_key` = `qq_official:private:<openid>` 读上下文 ——
    两条路不在同一个"抽屉"里，开场白永远进不了后续对话。
    """
    plat = str(platform or "").strip().lower() or "qq_official"
    try:
        from .platform import SCENE_PRIVATE, context_key_for

        return context_key_for(plat, SCENE_PRIVATE, str(user_id))
    except Exception:  # noqa: BLE001 —— 兜底：至少别退化成裸 id
        return f"{plat}:private:{user_id}"


async def _build_opener_messages(user_id: str, platform: str = "") -> List[str]:
    """
    生成主动聊天的开场白分段列表

    优先调用 AI 生成（保持人设），失败则用本地随机开场白。
    同时把这条主动消息写入该用户的上下文，这样用户回复时机器人
    能记得这次主动聊天。
    """
    try:
        from .chat import get_chat_reply
        from .context import add_message, get_context

        # 2026-10-15（审计 memory/extra-4）：上下文 key 必须和用户回话时用的
        # **同一个**（platform.context_key_for），否则这次开场白进了"另一个抽屉"，
        # 用户回复时机器人完全不记得自己刚主动说过话。
        key = _opener_context_key(user_id, platform)
        context = get_context(key)
        context = context + [
            {
                "role": "user",
                "content": (
                    "（系统提示：现在轮到你主动找用户聊天了。"
                    "请根据上面的聊天背景，像老朋友一样自然开口，主动开启一个新话题，"
                    "不要提这是系统提示）"
                ),
            }
        ]
        reply = await get_chat_reply(context, is_group=False)

        # 不再假设模型一定按 {"reply": [...]} 回：解析不出就当一段话用
        segments = [_clean_outbound(s) for s in reply_format.reply_segments(reply)]
        segments = [s for s in segments if s]
        if not segments:
            raise ValueError("AI 返回的回复为空")

        # 把 AI 回复写入该用户的上下文（保持和正常聊天一致的存储格式）
        add_message(key, "assistant", reply)
        return segments

    except Exception as e:
        logger.warning(f"AI 生成开场白失败，改用本地随机开场白: {e}")
        from .context import add_message

        text = _pick_local_opener(user_id)
        add_message(_opener_context_key(user_id, platform), "assistant", text)
        return [text]


async def _send_proactive_message(bot, user_id: str) -> None:
    """给指定 openid 发送主动聊天消息（QQ 官方 send_to_c2c，分段发送）。"""
    segments = await _build_opener_messages(user_id)
    for i, segment in enumerate(segments):
        await bot.send_to_c2c(
            openid=user_id, message=segment, msg_seq=_next_seq(user_id))
        if i < len(segments) - 1:
            await asyncio.sleep(random.uniform(1.5, 3.0))

    _last_send_time[user_id] = datetime.now().timestamp()
    logger.info(f"已主动给用户 {user_id} 发送消息: {segments[0][:50]}")


async def _try_send_one() -> None:
    """尝试给一个随机目标用户发送一条主动消息（带开关/免打扰/冷却检查）"""
    # 2026-10-15（审计 F1）：补平台开关与聊天总开关 —— 以前只判 ai_disabled，
    # 客户端关掉「QQ 平台」或聊天总开关后，主动消息照发。
    # 2026-10-16（对抗性复核 F1）：这里只查**总开关** —— 平台门要按"实际发到哪条
    # 平台"查：以前一律拿 "qq" 查，只关 QQ 平台时微信主动消息被误挡，只关微信时
    # 微信那条路又漏查照发。具体平台门在下面各自分支里（QQ / 微信 / 跨平台）。
    if gates.ai_disabled():
        logger.info("[proactive] " + gates.blocked_reason(""))
        return
    if not chat_manager.is_chat_enabled():
        logger.info("[proactive] 主动聊天跳过：聊天总开关是关的")
        return
    if not chat_manager.is_proactive_enabled():
        return

    cfg = _pack_cfg()
    if not cfg.get("enabled", True):
        logger.info("主动聊天：能力包参数里 enabled=false，跳过")
        return

    bot = _qq_bot()
    wx_ok = False if bot is not None else _wx_available()
    if bot is None and not wx_ok:
        logger.info("主动聊天跳过：既没有 QQ 官方机器人，也没有可用的微信会话")
        return

    if _is_quiet_hour() or _in_quiet(cfg):
        logger.info("当前处于免打扰时段（本地 23:00-08:00 或能力包参数），跳过本轮主动聊天")
        return

    targets = chat_manager.get_proactive_targets()
    if not targets:
        logger.info("没有配置主动聊天目标用户，请先使用 ac proactive add <QQ号> 添加")
        return

    eligible = [target for target in targets if not _is_in_cooldown(target)]
    if not eligible:
        logger.info("所有目标用户都在冷却时间内，跳过本轮")
        return

    target = random.choice(eligible)

    # ---- 跨平台主动聊天（2026-10-04 老板要的"整合进软件"）----
    # 目标可以写成 `平台:身份`（如 qq_official:openid / qq:12345 / wechat:wxid），
    # 这时**严格按这条目标自己记录的平台+身份**发，绝不看"最近聊过谁"——
    # 那正是把该发给 QQ 上某人的话从微信发出去的原因。
    if ":" in str(target):
        # 平台门按**目标自己记的平台**查（2026-10-16 复核 F1）
        _plat = str(target).partition(":")[0].strip().lower() or "qq"
        _blocked = gates.blocked_reason(_plat)
        if _blocked:
            logger.info("[proactive] " + _blocked)
            return
        await _send_proactive_cross(str(target))
        return

    if bot is not None and target.isdigit():
        logger.info(
            f"主动聊天目标 {target} 是 QQ 号；QQ 官方主动消息需要 openid，"
            "请先用该账号与机器人对话（程序会自动记录 openid）或直接填写 openid，本轮跳过")
        return
    # ---- 能力包参数：概率 / 最小间隔 / 互动窗口 / 未回上限 / 限速 ----
    import time as _time

    if random.random() > float(cfg.get("outreach_chance", 0.5)):
        logger.info("主动聊天：本轮概率未命中，跳过")
        return
    last_sent = float((_state().get(target) or {}).get("last_sent") or 0)
    need_gap = float(cfg.get("min_interval_h", 24)) * 3600
    if last_sent and _time.time() - last_sent < need_gap:
        logger.info(
            f"主动聊天：距上次只过了 {(_time.time() - last_sent) / 3600:.1f} 小时"
            f"（下限 {cfg.get('min_interval_h')} 小时），跳过")
        return
    rate = int(cfg.get("rate_per_hour", 20))
    if rate > 0 and _sends_last_hour(target) >= rate:
        logger.info(f"主动聊天：{target} 本小时已达上限 {rate} 条，跳过")
        return
    un = _unanswered(target)
    if un >= int(cfg.get("max_unanswered", 3)):
        logger.info(f"主动聊天：{target} 已连续 {un} 条没回（上限 {cfg.get('max_unanswered')}），跳过")
        return
    # 平台门：这条最终发给谁就查谁（2026-10-16 复核 F1）
    _plat = "qq" if bot is not None else "wechat"
    _blocked = gates.blocked_reason(_plat)
    if _blocked:
        logger.info("[proactive] " + _blocked)
        return
    try:
        if bot is not None:
            await _send_proactive_message(bot, target)
        else:
            await _send_proactive_wechat(target)      # 走微信（iLink）通道
    except Exception as e:
        logger.error(f"主动发送消息给 {target} 失败: {e}")
        return
    # 用户上次说话的时间晚于我们上次发送 = 中间回过话 → 未回计数清零
    wx_ts = float((_wx_tokens().get(target) or {}).get("ts") or 0)
    replied_since = bool(wx_ts and wx_ts > last_sent)
    _note_sent(target, replied_since)
    _note_mark(target)


async def _send_proactive_cross(target_key: str) -> None:
    """跨平台主动聊天：把这条消息发到目标自己记录的那个平台身份上。

    target_key 形如 `平台:身份`（qq_official:openid / qq:12345 / wechat:wxid）。
    身份隔离规矩（老板特别强调的那条）：
    - 只发 target_key 这一个身份，**不查绑定关系、不扩散到其它平台**；
    - 不参与"QQ 号 → openid"之类的猜测；
    - 发送失败就如实记日志，不换人重试。
    """
    platform, _, identity = str(target_key).partition(":")
    platform = platform.strip().lower()
    identity = identity.strip()
    if not platform or not identity:
        logger.info(f"主动聊天：跨平台目标格式不对（{target_key}），跳过")
        return
    if _is_quiet_hour():
        logger.info("当前处于免打扰时段，跳过本轮跨平台主动聊天")
        return
    # 上下文 key 用**目标平台那一套**（qq_official:private:<id> / wechat:private:<id>），
    # 与用户在那个平台回话时 brain 用的 key 一致（2026-10-15 审计）
    text = await _build_opener_text(identity, platform=platform)
    if not text:
        logger.info("主动聊天：这一次没生成出内容，跳过")
        return
    from . import cross_platform

    result = await cross_platform._send(target_key, platform, identity, "file", None, text=text)
    if result.get("ok"):
        logger.info(f"跨平台主动消息 -> {target_key}：已发送")
        _note_sent(target_key, False)
        _note_mark(target_key)
    else:
        logger.info(f"跨平台主动消息 -> {target_key} 失败：{result.get('error')}"
                    f"（不换人重试）")


async def _build_opener_text(user_id: str, platform: str = "") -> str:
    """生成一句开场白（复用主动聊天那套提示词，失败返回空串）。

    `platform` 给了就按那条平台的上下文 key 存（与用户回话时读的 key 一致）。
    """
    try:
        segments = await _build_opener_messages(user_id, platform=platform)
        return " ".join(str(s) for s in (segments or []) if s).strip()
    except Exception as e:  # noqa: BLE001
        logger.info(f"主动聊天：生成开场白失败：{e}")
        return ""


async def _wait_effective(interval_picker, *, label: str, step: float = 10.0) -> None:
    """按"当前生效的随机间隔"等一等 —— **等的过程里改配置也立即生效**。

    2026-10-11 热重载修的问题：以前这里是
    ``await asyncio.sleep(random.uniform(min, max) * 60)``，
    也就是"间隔 90 分钟 = 接下来 90 分钟里改配置一律不理会"，
    界面上把主动聊天关掉、机器人还可能过一小时才发一条 —— 看着就像没生效。
    现在：
      * 每 ``step`` 秒醒一次（顺带一次 ``os.stat`` 热重载配置）；
      * 配置一被改过就**按新间隔重新计时**；
      * 间隔被改短时立刻跟上，改长时也不会"睡死"。
    """
    while True:
        try:
            low, high = interval_picker()
            total = max(1.0, random.uniform(low, high) * 60)
        except Exception as e:  # noqa: BLE001 —— 读配置失败按 5 分钟兜底，别空转
            logger.warning("%s：读间隔失败（%s），按 5 分钟重试", label, e)
            total = 300.0
        waited = 0.0
        while waited < total:
            await asyncio.sleep(min(step, total - waited))
            waited += step
            try:
                if chat_manager.maybe_reload():
                    logger.info("%s：配置刚改过，按新参数重新计时", label)
                    break
            except Exception:  # noqa: BLE001
                pass


async def _proactive_loop() -> None:
    """主动聊天主循环：每次按**当前生效的**随机间隔等一等，再试着发一条。"""
    logger.info("主动聊天后台任务已启动（不定时随机找用户聊天；参数热读）")
    while True:
        try:
            await _wait_effective(chat_manager.get_proactive_interval, label="主动聊天")
            await _try_send_one()
        except asyncio.CancelledError:
            logger.info("主动聊天后台任务已停止")
            break
        except Exception as e:
            logger.error(f"主动聊天任务发生异常: {e}")
            await asyncio.sleep(60)


def _is_in_group_cooldown(group_id: str) -> bool:
    """检查某个群是否还在搭话冷却时间内"""
    cooldown = chat_manager.get_proactive_cooldown()
    if cooldown <= 0:
        return False
    last_time = _last_interject_time.get(group_id)
    if last_time is None:
        return False
    return (datetime.now().timestamp() - last_time) < cooldown * 60


def _clean_outbound(text: str) -> str:
    """旁路出站（主动消息 / 群搭话 / 入群欢迎）也要过一遍清洗（2026-10-15 审计 F2）。

    这些路径以前只过 reply_format.reply_segments —— 模型照 2026-10-12 那次事故
    吐 `<||DSML|| invoke …>` 或裸 JSON 调用时，会**原样发到用户微信/群里**。
    主链路（brain）与戳一戳早就补了 tool_marker，这里补齐同一道。
    """
    try:
        from . import tool_marker

        clean, removed = tool_marker.strip_markers(str(text or ""))
        clean, removed_json = tool_marker.strip_json_tool_calls(clean)
        clean = tool_marker.strip_fake_tool_calls(clean)
        if removed or removed_json:
            logger.warning("[proactive] 出站清洗：主动消息里的工具调用标记已剥掉")
        return clean.strip()
    except Exception as e:  # noqa: BLE001 —— 清洗失败不能挡住发消息
        logger.warning(f"[proactive] 出站清洗异常（原样发送）: {e}")
        return str(text or "")


async def _build_interject_content(group_id: str) -> str:
    """生成群聊搭话内容：随机选一种构思（读记录搭话 / 不读记录找话题）"""
    from .chat import get_interject_reply
    from .context import get_context

    mode = "history" if random.random() < 0.5 else "random"
    history = []
    try:
        if mode == "history":
            context = get_context(f"group_{group_id}")
            history = context[-chat_manager.get_interject_history_count():]
            if not history:
                mode = "random"
        text = await get_interject_reply(history, mode)
        return _clean_outbound(text)
    except Exception as e:
        logger.warning(f"AI生成搭话内容失败，改用本地开场白: {e}")
        return _pick_local_opener(group_id)


async def _send_interject(bot, group_id: str) -> None:
    """向指定群发送搭话消息（QQ 官方 send_to_group，group_openid）。"""
    text = await _build_interject_content(group_id)
    if not text:
        logger.info(f"群 {group_id} 搭话内容清洗后为空，跳过")
        return
    await bot.send_to_group(
        group_openid=group_id, message=text, msg_seq=_next_seq(group_id))
    _last_interject_time[group_id] = datetime.now().timestamp()
    logger.info(f"已向群 {group_id} 搭话: {text[:50]}")


async def _try_interject_one() -> None:
    """按概率尝试向配置的群聊搭话"""
    # 2026-10-15（审计 F1）：以前只判 ai_disabled —— 客户端把**聊天总开关**关掉、
    # 或者把某个群从允许群聊里移掉之后，机器人照样往那个群搭话（"关了还说话"）。
    # `gates.blocked_reason("qq")` 同时看总开关与 QQ 平台开关。
    _blocked = gates.blocked_reason("qq")
    if _blocked:
        logger.info("[proactive] " + _blocked)
        return
    if not chat_manager.is_chat_enabled():
        logger.info("[proactive] 群聊搭话跳过：聊天总开关是关的")
        return
    if not chat_manager.is_interject_enabled():
        return

    bot = _qq_bot()
    if bot is None:
        logger.info("群聊搭话跳过：当前没有 QQ 官方机器人连接（iLink 不支持主动推送）")
        return

    if _is_quiet_hour():
        logger.info("当前处于免打扰时段，跳过群聊搭话")
        return

    groups = chat_manager.get_proactive_groups()
    if not groups:
        logger.info("没有配置搭话群聊，请设置 aichat_proactive_groups 或使用 ac proactive group add")
        return

    probability = chat_manager.get_interject_probability()
    for group_id in groups:
        if group_id.isdigit():
            logger.info(
                f"群聊搭话目标 {group_id} 是 QQ 群号；QQ 官方需要 group_openid，"
                "请先在群内 @ 机器人一次（程序会自动记录）或直接填写 openid，跳过")
            continue
        if _is_in_group_cooldown(group_id):
            continue
        # 2026-10-16（对抗性复核 F1）：群白名单也要查 —— 客户端把某个群从
        # 「允许群聊」里移掉之后，机器人以前照样往那个群搭话（"关了还说话"）。
        if not chat_manager.is_group_enabled(group_id):
            logger.info(f"[proactive] 群 {group_id} 未开启群聊回话，跳过搭话"
                        "（客户端 AI 大脑页「允许在群里回话」可打开）")
            continue
        if random.random() >= probability:
            continue
        try:
            await _send_interject(bot, group_id)
        except Exception as e:
            logger.error(f"向群 {group_id} 搭话失败: {e}")


async def _interject_loop() -> None:
    """群聊搭话主循环：按**当前生效的**随机间隔触发，按概率决定是否搭话。"""
    logger.info("群聊搭话后台任务已启动（随机不定时、按概率触发；参数热读）")
    while True:
        try:
            await _wait_effective(chat_manager.get_interject_interval, label="群聊搭话")
            await _try_interject_one()
        except asyncio.CancelledError:
            logger.info("群聊搭话后台任务已停止")
            break
        except Exception as e:
            logger.error(f"群聊搭话任务发生异常: {e}")
            await asyncio.sleep(60)


@get_driver().on_startup
async def _start_proactive_task() -> None:
    """机器人启动时启动主动聊天后台任务。

    **总开关现在不在这里判**（2026-10-11 热重载）：任务照常起，但循环里每次
    干活前都读一次开关 —— 界面上把总开关关掉，它下一次醒来就什么都不做，
    不用重启机器人（以前关掉总开关要么不注册、要么继续发）。
    """
    if gates.ai_disabled():
        logger.info("[proactive] AI 总开关是关的，后台任务先空转（打开立即生效，不用重启）")
    global _proactive_task
    if _proactive_task is None or _proactive_task.done():
        _proactive_task = asyncio.create_task(_proactive_loop())

    global _interject_task
    if _interject_task is None or _interject_task.done():
        _interject_task = asyncio.create_task(_interject_loop())


@get_driver().on_shutdown
async def _stop_proactive_task() -> None:
    """机器人关闭时停止主动聊天后台任务"""
    global _proactive_task
    if _proactive_task:
        _proactive_task.cancel()
        try:
            await _proactive_task
        except (asyncio.CancelledError, Exception):
            pass
        _proactive_task = None

    global _interject_task
    if _interject_task:
        _interject_task.cancel()
        try:
            await _interject_task
        except (asyncio.CancelledError, Exception):
            pass
        _interject_task = None
