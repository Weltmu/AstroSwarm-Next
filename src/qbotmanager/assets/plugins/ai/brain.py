# -*- coding: utf-8 -*-
"""平台中立 AI 大脑：接收 ChannelMessage，输出回复（原 __init__.py 主处理器逻辑迁移）。

QQ 行为保持不回归；后续微信 ClawBot / 飞书 / 纸飞机接入时，
只需新增 adapters/<platform>.py，大脑无需改动。
"""
import asyncio
import json
import random
import re
import time
from typing import Dict

from nonebot import logger

from .chat import get_chat_reply_with_tools, judge_user_continues, should_reply_in_group
from .context import add_message, clear_context, get_context
from . import gates
from . import identity as identity_store
from . import group_policy
from . import memory as memory_store
from . import memory_bridge
from . import reply_format
from . import session_policy
from . import target_guard
from . import tool_marker
from .manager import chat_manager
from .prompt_window import (EMPTY_AT_LOOKBACK_LIMIT, EMPTY_AT_LOOKBACK_MINUTES,
                            GREET_PROMPT, GREET_PROMPT_RECENT,
                            QUOTED_UNRESOLVED_HINT,
                            describe as describe_window,  # noqa: F401
                            is_empty_at, normalize_quoted, quoted_state,
                            render_history, select_history)
from .platform import ChannelContext, ChannelMessage, SCENE_GROUP


# ===== 群聊免@会话状态（key: platform:room_id） =====
group_sessions: Dict[str, Dict[str, Dict[str, float]]] = {}
# key: platform:room_id -> 机器人最后一次回复的用户（原始平台 user_id）
last_reply_user: Dict[str, str] = {}


def _extra_of(msg) -> dict:
    """这条消息的 extra（**非 dict / 缺失一律当空字典**）。

    H-21：改前是 `msg.extra.get(...)` 直接调 —— 适配器传 `extra=None` 或 list 时
    抛 AttributeError，而 try 从很后面才开始，整条消息处理直接失败（用户只看到
    "它不理我"）。判定链路读取 extra 的地方全部走这里。
    """
    extra = getattr(msg, "extra", None)
    return extra if isinstance(extra, dict) else {}


def session_is_active(group_key: str, user_key: str) -> bool:
    """检查用户是否处于免@会话有效期内（距上次机器人回复未超过设定秒数）"""
    session = group_sessions.get(group_key, {}).get(user_key)
    if not session:
        return False
    expire_seconds = chat_manager.get_session_expire_seconds()
    last = session.get("last_bot_reply", 0.0)
    if last <= 0 or (time.time() - last) >= expire_seconds:
        session_clear(group_key, user_key)
        return False
    return True


def session_activate(group_key: str, user_key: str) -> None:
    """激活/续期用户的免@会话，并清零无关消息计数"""
    session = group_sessions.setdefault(group_key, {}).setdefault(
        user_key, {"last_bot_reply": 0.0, "unrelated_count": 0}
    )
    session["last_bot_reply"] = time.time()
    session["unrelated_count"] = 0


def session_register_unrelated(group_key: str, user_key: str) -> bool:
    """记录一条与机器人无关的消息；达到上限结束免@会话。返回 True 表示会话已结束。"""
    session = group_sessions.get(group_key, {}).get(user_key)
    if not session:
        return False
    session["unrelated_count"] = int(session.get("unrelated_count", 0)) + 1
    limit = chat_manager.get_session_unrelated_limit()
    logger.info(f"用户{user_key} 连续无关消息 {session['unrelated_count']}/{limit}")
    if session["unrelated_count"] >= limit:
        session_clear(group_key, user_key)
        logger.info(f"用户{user_key} 连续 {limit} 条消息与机器人无关，免@会话已结束，下次需重新@")
        return True
    return False


def session_clear(group_key: str, user_key: str) -> None:
    """清除用户的免@会话"""
    sessions = group_sessions.get(group_key)
    if sessions and user_key in sessions:
        del sessions[user_key]


# ===== 群聊活跃度管理器（平台中立：key 为 platform:room_id） =====
group_timers: Dict[str, asyncio.Task] = {}
group_probability_states: Dict[str, float] = {}


class GroupProbabilityManager:
    """群聊智能参与管理器"""

    def __init__(self):
        self._shutting_down = False
        logger.info(
            f"活跃度管理器初始化完成，基础活跃度: {chat_manager.get_group_chat_probability()}"
        )

    async def _decay_task(self, group_key: str):
        try:
            while not self._shutting_down:
                await asyncio.sleep(60)
                if self._shutting_down:
                    break
                current_prob = group_probability_states.get(group_key, 0.0)
                new_prob = max(0, round(current_prob - 0.1, 2))
                group_probability_states[group_key] = new_prob
                logger.info(
                    f"群组 {group_key} 活跃度衰减: {current_prob:.2f} → {new_prob:.2f}"
                )
                if new_prob <= 0:
                    logger.info(f"群组 {group_key} 活跃度衰减结束")
                    if group_key in group_timers:
                        del group_timers[group_key]
                    if group_key in group_probability_states:
                        del group_probability_states[group_key]
                    break
        except asyncio.CancelledError:
            logger.info(f"群组 {group_key} 衰减任务被取消")
        except Exception as e:
            logger.error(f"群组 {group_key} 衰减任务异常: {e}")
            if group_key in group_timers:
                del group_timers[group_key]
            if group_key in group_probability_states:
                del group_probability_states[group_key]

    def renew_probability(self, group_key: str):
        if self._shutting_down:
            return False
        try:
            base_prob = chat_manager.get_group_chat_probability()
            group_probability_states[group_key] = round(base_prob, 2)
            if group_key in group_timers:
                task = group_timers[group_key]
                if not task.done():
                    task.cancel()
                del group_timers[group_key]
            task = asyncio.create_task(self._decay_task(group_key))
            group_timers[group_key] = task
            logger.info(f"群组 {group_key} 活跃度续租: {base_prob:.2f}")
            return True
        except Exception as e:
            logger.error(f"群组 {group_key} 续租失败: {e}")
            return False

    def get_probability(self, group_key: str) -> float:
        return group_probability_states.get(group_key, 0.0)

    def has_active_timer(self, group_key: str) -> bool:
        return group_key in group_timers and not group_timers[group_key].done()

    async def shutdown(self):
        self._shutting_down = True
        logger.info("开始关闭活跃度管理器...")
        for group_key, task in list(group_timers.items()):
            if not task.done():
                task.cancel()
        group_timers.clear()
        group_probability_states.clear()
        logger.info("活跃度管理器关闭完成")


group_manager: GroupProbabilityManager = GroupProbabilityManager()


# ===== 回复分段 =====
def _parse_reply_json(raw: str):
    """保留旧名字给外部调用；真正实现在 reply_format（纯函数、可单测）。"""
    return reply_format.parse_reply_json(raw)


def _is_intentional_silence(raw: str) -> bool:
    """这一条是不是模型**故意**选择不回？（合法信封 + reply 显式为空）

    区分三种情况：
      · `{"reply": []}` / `{"reply": [""]}` → **故意不回**（安静，不兜底）
      · 半截 JSON / 别的壳        → 坏壳，照旧兜底一句人话
      · 压根不是 JSON             → 不是这里管的
    """
    import json as _json

    text = str(raw or "").strip().strip("`").strip()
    if not text.startswith("{"):
        return False
    try:
        data = _json.loads(text)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, dict) or "reply" not in data:
        return False
    reply = data.get("reply")
    if not isinstance(reply, list):
        return False
    return not [x for x in reply if str(x).strip()]


async def _reply_in_segments(ctx: ChannelContext, raw: str, at_user: bool,
                             single: bool = False, single_reason: str = ""):
    """分条发送回复；群聊被@时第一段带@。

    以前要求回复必须是 `{"reply": [...]}` 的 JSON，模型没照格式回就**整条丢掉**
    （2026-10-01 老板看到的"群里 @ 它一个字都不说"，日志里却是"AI 回复 31 字"）。
    现在交给 reply_format.reply_segments：**解析出 reply 数组就逐条发**；
    解析不出就走兜底 —— 但**只有"不是 JSON 壳"时才把原文发出去**，
    是壳就一定不发（2026-10-11 回归：壳原样发进群等于刷屏）。

    **出站清洗**（2026-10-11）：模型偶尔把工具调用标记写进正文，一律不发出。
    每个分片都单独过一遍 `tool_marker.StreamCleaner`（标记可能正好被分片切成两半，
    只在最后过滤会漏），剥完一个字都不剩就改发一句人话兜底并打 warning。

    **分条判定（2026-10-12 老板要求：像人一样分几条发，但别做死成"所有回复都多条"）**：
    清洗完的段落先过 `reply_format.plan_parts`（纯函数）——
      * 模式（关 / 自动 / 总是）；
      * 通道（QQ 走 OneBot/NapCat 能连发；官方通道/微信 iLink 默认合并）；
      * 内容（都很短、只有语气词 → 合并；模型只给一条 → 就一条，**不许硬拆**）。
    判定依据**一定**打进一行日志（`grep '分条判定' nonebot.log`）。
    条与条之间的间隔按字数估算 + 随机抖动（首条不延迟、有硬上限）。

    `single=True`（2026-10-13）：**本轮只发一条** —— 空 @ 的轻唤、或本轮动过工具
    （卡片/动作已经单独发过东西）时用，避免"每个功能各说一句、答得又乱又多"。
    """
    segments = reply_format.reply_segments(raw)
    if not segments and _is_intentional_silence(raw):
        # 2026-10-12：模型可以**故意不回**（真人经常不回表情包）。
        # 与"坏壳/半截 JSON"区别开：那种还是要兜底一句人话。
        logger.info("[brain] 模型选择不回（reply 为空）→ 本条不发")
        return
    if not segments:
        # 走到这里有两种情况，处置**不同**：
        #   * 原文本来就是空的 → 不发（老行为）；
        #   * 原文是 JSON 壳但抠不出正文（`{"reply": []}` / 半截 JSON）→ 壳绝不外发，
        #     改说一句人话（否则用户那边一个字都收不到）。
        if str(raw or "").strip():
            logger.warning(
                "[brain] 出站整形：原文取不出正文（JSON 外壳/半截 JSON），"
                "已拦下不发（原文 %d 字）" % len(str(raw or "")))
            if at_user:
                await ctx.send_text_at(tool_marker.FALLBACK_TEXT)
            else:
                await ctx.send_text(tool_marker.FALLBACK_TEXT)
        else:
            logger.info("[brain] AI 回复是空的，不发")
        return
    pending, removed = tool_marker.clean_segments(segments)
    if not pending:
        # 整条只剩标记（或 `{}` 这种被剥剩的参数壳）→ **不发标记**，改说一句人话
        logger.warning("[brain] 出站清洗：正文全是工具调用标记，已拦下不发（原文 %d 字）"
                       % len(str(raw or "")))
        if at_user:
            await ctx.send_text_at(tool_marker.FALLBACK_TEXT)
        else:
            await ctx.send_text(tool_marker.FALLBACK_TEXT)
        return
    if removed:
        logger.warning("[brain] 出站清洗：正文里剥掉了工具调用标记（原文 %d 字 → 实发 %d 段）"
                       % (len(str(raw or "")), len(pending)))
    # ---- 分条判定（I7）：怎么发由内容 + 通道 + 配置共同决定 ----
    parts, plan = reply_format.plan_parts(
        pending,
        mode=chat_manager.get_multi_message_mode(),
        max_parts=chat_manager.get_max_reply_parts(),
        max_part_chars=chat_manager.get_max_part_chars(),
        channel_allows_multi=bool(getattr(ctx, "multi_message", False)),
        channel_note=str(getattr(ctx, "multi_message_note", "") or ""),
        per_char_ms=chat_manager.get_part_delay_ms(),
        max_delay_ms=chat_manager.get_part_delay_max_ms(),
        force_single=bool(single),
        force_single_reason=str(single_reason or ""),
    )
    logger.info("[brain] 分条判定（I7）：" + reply_format.describe_plan(plan))
    if not parts:
        logger.warning("[brain] 分条判定：清洗后没有可发的正文，已拦下不发")
        if at_user:
            await ctx.send_text_at(tool_marker.FALLBACK_TEXT)
        else:
            await ctx.send_text(tool_marker.FALLBACK_TEXT)
        return
    _log_outbound(ctx, raw, parts, at_user)
    await _send_parts(ctx, parts, plan.get("delays_ms") or [], at_user=at_user)


async def _send_parts(ctx: ChannelContext, parts: list, delays_ms: list, *, at_user: bool):
    """逐条发出（第 1 条不延迟），条与条之间按判定好的毫秒数 sleep。

    **失败处理**（老板口径）：第 2 条起发送失败 → 记一条 warning，**就此停手**，
    既不重试轰炸、也不影响已经发出去的那几条；第 1 条就失败仍然照老规矩上抛，
    由外层统一报错（否则用户那边一个字都收不到、日志也没有）。
    """
    for i, part in enumerate(parts):
        if i > 0:
            delay = delays_ms[i - 1] if (i - 1) < len(delays_ms) else 0
            if delay > 0:
                await asyncio.sleep(delay / 1000.0)
        try:
            if i == 0 and at_user:
                await ctx.send_text_at(part)
            else:
                await ctx.send_text(part)
        except Exception as e:  # noqa: BLE001 —— 第 2 条起失败不能连累已发出的部分
            if i == 0:
                raise
            logger.warning(
                "[brain] 分条发送：第 %d/%d 条发送失败 → 停手，**不重试**"
                "（已成功发出 %d 条，不受影响）｜错误=%s"
                % (i + 1, len(parts), i, str(e)[:160]))
            return


def _log_outbound(ctx: ChannelContext, raw: str, pending: list, at_user: bool) -> None:
    """「回复正文」日志：打的是**真正发出去的那几段话**，不是模型原始输出。

    2026-10-11 教训：以前这一行打的是**模型原始回复** —— 它常常是
    `{"reply": ["…"]}` 的 JSON，于是排查的人看到这行就以为"把 JSON 发进群了"，
    其实发出去的是解析后的正文。今天为此白排查了很久。
    现在一律打**实发正文**；原始输出与实发不同时，把原始输出附在同一行
    （`｜原始=…`），JSON 壳有没有被解析掉、一眼就能分辨。

        grep '回复正文' nonebot.log
    """
    try:
        room = str(getattr(ctx, "target_key", "") or "")
        sent = " ｜ ".join(str(x) for x in pending)
        line = "[brain] 回复正文 → %s｜带@=%s｜实发=%s" % (
            room, "是" if at_user else "否",
            _mask_for_log(sent)[:120].replace("\n", " "))
        raw_text = str(raw or "")
        if raw_text.strip() and raw_text.strip() != sent.strip():
            line += "｜原始=%s" % _mask_for_log(raw_text)[:80].replace("\n", " ")
        logger.info(line)
    except Exception as e:  # noqa: BLE001 —— 打日志失败绝不能影响回话
        logger.info(f"[brain] 回复正文日志输出失败：{e}")


# ===== 大脑主入口 =====
_QUOTED_LINE = {
    "none": "引用段=无（本条只有 @ 段或纯正文，不算引用）→ 正常窗口",
    "resolved": "引用段=有（取到正文）→ 用被引用的那条决定话题",
    "unresolved": "引用段=有但取不到正文 → 不带上文（宁可不答，也不猜旧话题）",
}


def _quoted_line(quoted) -> str:
    """「引用段判定」那一行 —— 2026-10-11 老板要求打明确日志的直接落实。

    今天就是因为**看不出上下文为什么被丢**才拖了这么久：以前只有一句
    「引用未解析 → 不带上文」，既不说"哪来的引用"，也不说"到底有没有引用段"。
    """
    return _QUOTED_LINE.get(quoted_state(quoted), "引用段=? → 按没有引用处理")


def _mask_for_log(text: str) -> str:
    """给"回复正文日志"用的脱敏：手机号 / 证件号 / 银行卡 / 详细住址照旧打码。

    复用内置记忆模块那套隐私规则（`qbotmanager.core.memory.policy.sanitize`），
    保证"日志里的打码口径"和"入库时的打码口径"是同一份，不各写各的。
    模块不在（老装机）时退回一个最小正则兜底，绝不因为脱敏把日志搞崩。
    """
    raw = str(text or "")
    try:
        from qbotmanager.core.memory import policy as _policy

        return str(_policy.sanitize(raw)[0])
    except Exception:  # noqa: BLE001 —— 拿不到内置模块就用最小兜底
        pass
    try:
        return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[已隐去手机号]", raw)
    except Exception:  # noqa: BLE001
        return raw


# 按会话串行化（2026-10-15 审计 D）：同一会话的两条消息以前是**并发**进的 ——
# 各开一次大模型请求、各写一遍上下文，于是"后问先答"、上下文写入顺序和展示顺序
# 对不上，工具动作（发送类/撤回类）也可能交叉。这里一个会话一把锁，排队处理。
_SESSION_LOCKS: dict = {}


def _session_lock(key: str):
    lock = _SESSION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[key] = lock
        if len(_SESSION_LOCKS) > 500:          # 会话锁不该无限增长
            for k in [k for k, v in _SESSION_LOCKS.items() if not v.locked()][:200]:
                if k != key:
                    _SESSION_LOCKS.pop(k, None)
    return lock


async def handle_message(msg: ChannelMessage, ctx: ChannelContext) -> None:
    """平台中立的聊天大脑（入口）。**同一会话串行处理**，见 `_session_lock`。"""
    async with _session_lock(msg.context_key):
        await _handle_message_inner(msg, ctx)


async def _handle_message_inner(msg: ChannelMessage, ctx: ChannelContext) -> None:
    """平台中立的聊天大脑：原 __init__.py 主处理器逻辑迁移而来。"""
    logger.info(f"[brain] 收到 {msg.platform} 消息 user={msg.user_id} text={msg.plain_text[:80]}")
    # 断言式护栏（2026-10-11 老板报的"串人"）：先对账"这条回复会发到哪个会话"与
    # "这条事件自己的会话"是否一致，对不上就**不发**（宁可沉默，也不要发错人）。
    try:
        reason = target_guard.check_reply_target(msg, ctx)
        if reason:
            target_guard.warn(reason, msg, ctx)
            logger.info("[brain] " + target_guard.describe(msg, ctx))
            return
    except Exception as e:  # noqa: BLE001 —— 护栏本身出错不能吃掉正常回话
        logger.warning(f"[brain] 回复目标护栏异常（继续按原逻辑处理）：{e}")
    try:
        chat_manager.maybe_reload()      # 客户端刚改过人设/开关就热重载（一次 stat，可忽略）
    except Exception:  # noqa: BLE001 —— 热重载失败不能挡住回话
        pass
    if not chat_manager.is_chat_enabled():
        logger.info("[brain] 聊天总开关已关闭，忽略")
        return

    is_group = msg.scene == SCENE_GROUP
    key = msg.context_key
    room = msg.session_key

    if is_group and not chat_manager.is_group_enabled(msg.room_id):
        # 说清为什么丢：以前这里静默 return，用户群里 @ 半天没反应也查不到原因
        logger.info(
            f"[brain] 群 {msg.room_id} 未开启群聊回话，忽略这条"
            "（客户端 AI 大脑页「允许在群里回话」可打开）")
        return

    user_msg2 = msg.plain_text.strip()
    # 入站识别（2026-10-11）：**整条都是工具调用标记**的消息不当人话喂给模型。
    # 群里另一个机器人也会漏出这种标记；喂进去会污染上下文（模型看见就学着吐标记）。
    # 注意：只是这一条跳过，**不拉黑这个账号** —— 它是群里另一个机器人，不是恶意的。
    if tool_marker.is_all_markers(user_msg2):
        logger.info("[brain] 整条都是工具调用标记，不当人话处理（不入上下文）"
                    " user=%s" % msg.user_id)
        return
    if user_msg2.startswith("ac "):
        # I6：每个判定分支都要有"输入 → 结论 → 原因"
        logger.info("[brain] 判定：正文以 'ac ' 开头 → 本条静默丢弃（原因：控制台/桥接命令前缀，"
                    "不当人话处理）｜input: user=%s text=%r"
                    % (msg.user_id, user_msg2[:60]))
        return
    if user_msg2 in ("清除对话", "重置对话"):
        clear_context(key)
        if is_group:
            session_clear(room, msg.user_key)
        logger.info("[brain] 判定：命中「%s」→ 清空本会话上下文并回一句确认"
                    "（原因：用户显式要求；群聊同时清掉免@会话）｜input: user=%s room=%s"
                    % (user_msg2, msg.user_id, room))
        await ctx.send_text("已清除对话历史")
        return

    # 身份绑定命令（「绑定」/「绑定 123456」/「我的身份」）：先拦下来，别丢给 AI
    try:
        from . import identity_bind

        if await identity_bind.try_handle(msg, ctx):
            logger.info("[brain] 判定：命中身份绑定命令 → 已由 identity_bind 处理并回复"
                        "（原因：绑定类命令不交给 AI）｜input: user=%s text=%r"
                        % (msg.user_id, user_msg2[:60]))
            return
    except Exception as e:  # noqa: BLE001 —— 绑定出问题不能挡住正常聊天
        logger.info(f"[identity] 绑定命令处理失败：{e}")

    # 点歌（2026-10-12）：群友/私聊发「点歌 xxx」→ 网易云音乐卡片 + AI 配一句顺口话。
    # 拦在群免@判定之前：点歌是**显式命令**，没 @ 也应答；@ 了别人的消息由
    # song.try_handle 自己放过。点歌出任何问题只记日志，绝不能连累正常聊天。
    try:
        from . import song as song_mod

        if await song_mod.try_handle(msg, ctx):
            logger.info("[brain] 判定：命中「点歌」→ 已按音乐卡片处理"
                        "｜input: user=%s text=%r" % (msg.user_id, user_msg2[:60]))
            return
    except Exception as e:  # noqa: BLE001 —— 点歌失败不能连累正常聊天
        logger.warning(f"[brain] 点歌处理异常（继续正常对话）：{e}")

    # 2026-10-12：QQ 那条「请求添加你为好友」是**系统通知**，不是用户提问 ——
    # 以前直接丢给 AI，它只能回"我没看到有人申请加我啊"。这里识别出来并走见面问候。
    try:
        from . import friend_requests as _fr

        if _fr.is_add_notice(getattr(msg, "text", "") or getattr(msg, "plain_text", "")):
            # 这里没有 bot 对象（handle_message 的签名里就没有），
            # 直接把当前**通道**交给问候逻辑（它支持 ctx.send_text）。
            await _fr.greet_new_friend(ctx, str(getattr(msg, "user_id", "") or ""),
                                       str(getattr(msg, "nickname", "") or ""))
            logger.info("[friend] 这条是加好友系统通知 → 已按「新朋友问候」处理，不交给 AI")
            return
    except Exception as _e:  # noqa: BLE001
        logger.info(f"[friend] 加好友通知识别失败（照常走 AI）：{_e}")

    user_msg = msg.text
    # ---- 空 @（2026-10-11 老板报的"空@把一小时前的内容当问题答了"）----
    # 2026-10-11 **回归修复**：老板 @ 它问「星群是什么」被判成空 @、只回了「在呢」。
    # 两处改：
    #   ① 只读**一个权威字段** —— 适配器归一化后的正文 `msg.text`。以前写的是
    #      `msg.plain_text or user_msg`，两个字段互为旁路，正是误判的温床；
    #   ② 判定收紧成 **fail-open** —— 去掉 @ 段/表情/标点后只要还剩**一个实义字符**
    #      就必须当正常提问处理（见 prompt_window.is_empty_at）。
    # 群里/私聊走**同一套判定**：`is_at_me` 是消息固有属性，与场景无关（不再用 is_group 卡）。
    empty_at = bool(msg.is_at_me and is_empty_at(user_msg))
    if empty_at:
        if chat_manager.get_empty_at_action() == "silent":
            logger.info("[brain] 空 @（去@后无实义字符）且配置为「不回复」，本条忽略")
            return
        user_msg = GREET_PROMPT
        logger.info("[brain] 空 @（去@后无实义字符）：只轻唤一句，不带任何上文、不带 @")
    elif msg.is_at_me:
        logger.info("[brain] @ 但有实义内容 → 按正常提问处理：%r" % user_msg.strip()[:40])
    # 分享链接（B站/文章/小程序卡片里的 url）：趁着这一步把标题和正文摘要抓下来，
    # 一起交给 AI —— 不这么做它只会看到一串短链，答起来只能猜（2026-10-03）。
    try:
        from . import link_preview

        if chat_manager.is_rich_parse_enabled() and not empty_at:
            user_msg = await link_preview.enrich(user_msg)
    except Exception as e:  # noqa: BLE001 —— 抓链接失败绝不能影响回话
        logger.info(f"[brain] 链接解析跳过：{e}")
    # ---- 单条消息长度上限（H-13：超长消息原来没有任何上限）----
    # 直接粘一篇长文（十万字）进来时：上下文只按**条数**裁到 20 条，字数没有闸门 →
    # 一次超大请求（400 / 超时 / 烧钱）。这里在**进上文与进提示词之前**统一截断，
    # 上限可配（AI 大脑里改，默认 4000 字），截了多少如实写进日志。
    _cap = chat_manager.get_max_incoming_chars()
    if _cap > 0 and len(user_msg) > _cap:
        logger.info("[brain] 单条正文过长 → 截断：原 %d 字 → 保留 %d 字（上限可配，"
                    "AI 大脑里改；input: user=%s）" % (len(user_msg), _cap, msg.user_id))
        user_msg = user_msg[:_cap] + "…（本条过长，已截断）"
    # 2026-10-12（禁言实测）：@ 段在归一化时被剥掉 —— 「禁言@某人 一分钟」
    # 到 AI 手里只剩「禁言 一分钟」，它只能反问"禁谁啊"。把被 @ 的号如实
    # 补一句，AI 才能把"他"对上号（禁言、回答关于他的问题都要这个）。
    if is_group:
        _at_ids = _extra_of(msg).get("at_targets") or []
        if _at_ids:
            user_msg = ("%s（本条还 @ 了别人：%s）"
                        % (user_msg, "、".join(str(x) for x in _at_ids)))
    context = get_context(key)

    if is_group:
        user_info = f"用户{msg.user_id}({msg.nickname or '未知用户'})说："
        # 空 @ 的"轻唤指令"**不落上下文**（2026-10-13）：它以前会被存进上下文、
        # 污染之后几轮的上文窗口；现在它作为本条的用户消息，在下面单独发给模型。
        if not empty_at:
            add_message(key, "user", f"{user_info}: {user_msg}",
                        user_id=str(getattr(msg, "user_id", "") or ""))

        should_reply = False
        # 走哪条分支由 session_policy 决定（纯逻辑，可单测）：被@ → 别人@别人跳过 →
        # api 模式每条都让 AI 判 → timeout 模式只在有效期内判 → 旧版随机参与 → 不回
        #
        # **第一优先级：@ 了别人、但没 @ 我 → 完全不接话**（2026-10-11 晚 老板急报：
        # "我和机器人聊天时 @ 别人，机器人也选择回复"）。
        # 为什么必须**单独提前判一次**（而不是只靠 choose_branch）：
        #   ① 通知类事件那条岔路（下面 `allows_session_continuation`）会**绕过**
        #      `choose_branch`，`@ 别人` 的信息在那条路上根本用不上；
        #   ② `direct_continue` 快捷路径（机器人上一条回复对象就是他 → 免 AI 判断
        #      直接回）在 `choose_branch` **之外** —— 线上 22:09:03 就是它抢的话。
        # 所以：这一判定排在任何"免@延续"之前，命中就 SKIP，谁也绕不过去。
        at_others = bool(_extra_of(msg).get("at_others", False))
        # 「被叫到称呼就回话」（2026-10-12 老板报：群里第一次喊"奈姐"没回复）：
        # 称呼词从热配置读（客户端里改完保存即生效，不用重启）；命中就按被 @ 处理。
        wake_word = session_policy.wake_hit(user_msg2, gates.wake_words())
        if session_policy.says_at_other(is_at_me=bool(msg.is_at_me), at_others=at_others):
            logger.info(
                "本条 @ 了别人（未 @ 我）→ 不接话（不进入免@延续）"
                "｜input: is_at_me=%s at_others=%s 段=%s｜结论: skip（第一优先级）"
                % (bool(msg.is_at_me), at_others,
                   group_policy.describe_segments(msg.raw) if msg.raw is not None else "<无事件>"))
            branch = session_policy.BRANCH_SKIP
        elif not target_guard.allows_session_continuation(msg):
            logger.info(
                f"[brain] 通知类事件（kind={_extra_of(msg).get('notice_kind')}）不走免@延续；"
                "只按事件自己的会话回（is_at_me=%s｜结论: %s）"
                % (bool(msg.is_at_me),
                   "at_me（被 @ 了，回）" if msg.is_at_me else "ignore（没 @ 我，不回）"))
            branch = (session_policy.BRANCH_AT_ME if msg.is_at_me
                      else session_policy.BRANCH_IGNORE)
        else:
            session_active = session_is_active(room, msg.user_key)
            mode = chat_manager.get_session_mode()
            auto_participate = chat_manager.is_group_auto_participate_enabled()
            branch = session_policy.choose_branch(
                is_at_me=bool(msg.is_at_me) or bool(wake_word),
                at_others=at_others,
                mode=mode,
                session_active=session_active,
                auto_participate=auto_participate,
            )
            logger.info(
                "[brain] 群聊判定（I6）：input: is_at_me=%s wake=%s at_others=%s mode=%s "
                "session_active=%s auto_participate=%s｜结论: %s"
                % (bool(msg.is_at_me), wake_word or "无", at_others, mode, session_active,
                   auto_participate, branch))

        if branch == session_policy.BRANCH_AT_ME:
            should_reply = True
            if wake_word and not msg.is_at_me:
                logger.info(f"群聊中被叫到「{wake_word}」，准备回复")
            else:
                logger.info("群聊中被@，准备回复")
            group_manager.renew_probability(room)
            session_activate(room, msg.user_key)
        elif branch == session_policy.BRANCH_SKIP:
            should_reply = False
            logger.info(f"用户{msg.user_id}的消息@了其他人，视为与机器人无关，不回复")
            session_register_unrelated(room, msg.user_key)
        elif branch in (session_policy.BRANCH_JUDGE_API, session_policy.BRANCH_JUDGE_TIMEOUT):
            nickname = msg.nickname or "该用户"
            # timeout 模式下的快捷路径：机器人上一条回复的就是他，且他是接着说的 → 免 AI 判断
            direct_continue = (
                branch == session_policy.BRANCH_JUDGE_TIMEOUT
                and last_reply_user.get(room) == msg.user_id
                and len(get_context(key)) >= 2
                and get_context(key)[-2]["role"] == "assistant"
            )
            if direct_continue:
                should_reply = True
                logger.info(f"用户{msg.user_id}直接延续对话（机器人上一条回复对象就是该用户），免AI判断")
                session_activate(room, msg.user_key)
            else:
                # 判断失败一律当"不回"并写日志，绝不打断收消息（见 session_policy.judge_quietly）
                should_reply = await session_policy.judge_quietly(
                    judge_user_continues,
                    messages=get_context(key),
                    user_id=msg.user_id,
                    nickname=nickname,
                    last_reply_to=last_reply_user.get(room, ""),
                )
                if should_reply:
                    logger.info(
                        f"AI判断用户{msg.user_id}在继续和机器人聊天，回复"
                        + ("（api 模式）" if branch == session_policy.BRANCH_JUDGE_API else ""))
                    session_activate(room, msg.user_key)
                else:
                    logger.info(f"AI判断用户{msg.user_id}的消息与机器人无关，不回复")
                    session_register_unrelated(room, msg.user_key)
        elif branch == session_policy.BRANCH_JUDGE_GROUP:
            dynamic_probability = group_manager.get_probability(room)
            if random.random() < dynamic_probability:
                try:
                    should_reply = await should_reply_in_group(get_context(key))
                except Exception as e:
                    error_msg = f"群聊对话判断异常:\n {str(e)}"
                    await ctx.notify_error(error_msg)
                    should_reply = False
                if should_reply:
                    logger.info("AI判断需要参与群聊讨论")
                    group_manager.renew_probability(room)
                else:
                    logger.info("AI判断不需要参与群聊讨论")
        else:
            should_reply = False

        if not should_reply:
            return
    else:
        add_message(key, "user", user_msg,
                    user_id=str(getattr(msg, "user_id", "") or ""))

    try:
        # 注入长期记忆：全局 + 该用户（含绑定身份）的记忆
        #
        # 上文窗口（2026-10-11 老板口径）：**最近 N 条 且 距现在 ≤ T 分钟**；
        # 超时的旧消息不进提示词，除非本条是「引用某条」。
        # 空 @ 时 above 一条都不带（GREET_PROMPT 已经写明"没有历史"）。
        #
        # **引用判定只认真正的引用段**（2026-10-11 回归：@ 被判成引用）：
        #   * 没有引用段（`None` / `{}`，例如"只有 @ 段 + 正文"）→ **正常窗口**；
        #   * 有引用段且取到正文 → 用被引用的那条；
        #   * 有引用段但取不到正文 → 不带上文（这条规则本身是对的，别改坏）。
        # 状态一律写进下面那行 `[brain] 上文窗口：…` 日志，方便一眼看出为什么丢上下文。
        quoted = normalize_quoted(_extra_of(msg).get("quoted"))
        logger.info("[brain] " + _quoted_line(quoted))
        # 通知类事件（点赞 / 戳一戳 / 按钮 / 审核）**0 条上文**（H-8，规则表第 13/14 行）：
        # 它不是"接着上一句聊"，带上文就等于拿群里最近的会话去回一个赞
        # （0.2.37 的"串人"就是这条路的极端形态）。这里与"空 @"同样处理：窗口清零。
        empty_at_mine = False      # 下面空 @ 分支会改写；通知分支不会（见 greet 那段）
        if target_guard.is_notice(msg):
            logger.info("[brain] 判定：通知类事件（kind=%s）→ 上文 0 条"
                        "（原因：通知没有'接着上一句聊'这回事，规则表第 13/14 行）"
                        % _extra_of(msg).get("notice_kind"))
            window, win_info = [], {"mode": "notice",
                                    "minutes": 0, "total": 0,
                                    "selected": 0, "dropped": 0, "oldest_ts": 0,
                                    "newest_ts": 0.0, "span_minutes": 0.0, "cutoff": 0,
                                    "quoted_state": "none", "quoted_reason": ""}
        elif empty_at:
            # 空 @ 微窗口（2026-10-13 老板实测）：常见用法是"先把请求打出来、再 @ 一下"。
            # 旧口径 0 条上文 → 它只回「在的呢」，请求被晾着、看着像在答别的时空线。
            # 现在只看刚刚 EMPTY_AT_LOOKBACK_MINUTES 分钟、最多 EMPTY_AT_LOOKBACK_LIMIT 条：
            # 接得住"请求 + @ 一下"，又不会把一小时前的旧话题翻出来（2026-10-11 老回归）。
            window, win_info = select_history(
                get_context(key), now=time.time(),
                minutes=EMPTY_AT_LOOKBACK_MINUTES, limit=EMPTY_AT_LOOKBACK_LIMIT)
            win_info["mode"] = "empty-at-recent"
            # 2026-10-15：**优先只收"他自己"说的话**。"先打字、再 @ 一下"是同一个人的
            # 动作；不限发送者的话，别人在这两分钟里说的话会被当成本次请求答出去
            # （A 问的事、B 空 @ 一下 → 机器人拿 A 的问题回 B，群里看着就是答错人）。
            # 他自己窗口内没说过话（或老数据没记 user_id）→ 保留原来的群体窗口，
            # 别把"接住正在聊的话题"这个能力一起砍掉。
            _mine = [m for m in window
                     if str(m.get("user_id") or "") == str(getattr(msg, "user_id", "") or "")]
            win_info["empty_at_same_sender"] = bool(_mine)
            empty_at_mine = bool(_mine)
            if _mine:
                window = _mine
                win_info["selected"] = len(_mine)
        else:
            window, win_info = select_history(
                get_context(key), now=time.time(),
                minutes=chat_manager.get_context_window_minutes(),
                quoted=quoted)
        logger.info("[brain] " + describe_window(win_info))
        ctx_messages = render_history(window, quoted)
        # 2026-10-12：有引用段但正文没取到 → 上文照旧不带（select_history 的规则没变），
        # 但**别再让它瞎猜/装没听见**：注入一句提醒，让它如实说"你引用的那条我看不到"。
        # 只认 win_info 的状态（empty_at / 通知分支重置成 none，不会被误注入）。
        if str(win_info.get("quoted_state") or "") == "unresolved":
            ctx_messages = [{"role": "system",
                             "content": QUOTED_UNRESOLVED_HINT}] + ctx_messages
        mem_text = ""
        if chat_manager.is_memory_enabled():
            # 内置记忆（程序侧，三档开关）优先；模块不在就回退插件自带那份
            keys = identity_store.resolve(msg.user_key)
            bridge_key = memory_bridge.identity_key(
                getattr(msg, "platform", ""), getattr(msg, "user_id", ""))
            if bridge_key:
                memory_bridge.touch(bridge_key, str(getattr(msg, "nickname", "") or ""))
            # 2026-10-13（老板：记忆要互通）：注入**整组绑定身份**，不是只看当前 key ——
            # 写记忆走的是"绑定后的统一 key"（canonical），这里只看 raw key 的话，
            # 微信↔QQ 绑定后必有一边看不到对方的记忆（旧数据还散在各自的 raw key 下）。
            builtin_text = memory_bridge.inject_block(
                keys or ([bridge_key] if bridge_key else []),
                person=str(getattr(msg, "nickname", "") or ""))
            if builtin_text:
                mem_text = builtin_text
            elif memory_bridge.available():
                # 内置模块在、但档位是「无」：就是要没有记忆，别再回退旧文件
                mem_text = ""
            else:
                mem_text = memory_store.format_memory_text(keys)
        ai_messages = ctx_messages
        if mem_text:
            ai_messages = [
                {"role": "system", "content": memory_bridge.prompt_prefix() + mem_text}
            ] + ctx_messages
        if empty_at:
            # 轻唤的"指令"本身以前只是存进上下文，从没真正发给模型（模型等于空着
            # 对话硬编一句）；现在作为本条用户消息发出去。
            # 2026-10-15：只有"窗口里全是他自己刚说的话"才用"接着办"那版 ——
            # 回退到**群体窗口**时（他自己两分钟没说话）不能给"直接把它办了"的指令：
            # A 刚提了句"帮我撤回刚才那条"，B 空 @ 一下，机器人就会以 B 的名义去办
            # A 的事（B 根本没说话，A 也不知道发生了什么）。
            greet_text = GREET_PROMPT_RECENT if empty_at_mine else GREET_PROMPT
            ai_messages = list(ai_messages) + [{"role": "user", "content": greet_text}]
            logger.info("[brain] 空 @ 轻唤：微窗口 %d 条 → %s"
                        % (len(window), "接着办最近的事" if window else "冷不接旧话题"))
        _stats: dict = {}
        reply = await get_chat_reply_with_tools(
            ai_messages, is_group, msg=msg, channel=ctx, stats=_stats)
        logger.info(f"[brain] AI 回复 {len(reply)} 字")
        add_message(key, "assistant", reply)
        if is_group:
            session_activate(room, msg.user_key)
            last_reply_user[room] = msg.user_id
        # 空 @ 只轻唤，**不带 @**（老板口径）；其余沿用原来的 at 行为（可配）
        at_user = bool(is_group and msg.is_at_me and not empty_at)
        if empty_at:
            logger.info("[brain] 空 @ → 回复不带 @（群里一条普通消息就够）")
        # 「回复正文」日志现在由 `_reply_in_segments` 打 —— 打的是**真正发出去的那几段**，
        # 不是这里的模型原始输出（以前打原始输出，看到 JSON 就以为"把 JSON 发进群了"）。
        # 2026-10-13（老板："遇到多功能答应时每个功能都用对应话术回答，答得很多很乱"）：
        # 空 @ 轻唤、以及**本轮动过工具**（卡片/动作已经单独发过东西）→ 只发一条，
        # 不把模型给的两三句再拆成好几个气泡。
        _single = bool(empty_at) or int(_stats.get("tool_calls") or 0) > 0
        _single_reason = ("空 @ 轻唤" if empty_at
                          else "本轮用了工具（卡片/动作已单独发过）")
        await _reply_in_segments(ctx, reply, at_user=at_user,
                                 single=_single, single_reason=_single_reason)
        # 聊天里点了"自学"（learn_skill 工具）的话，回话之后才真去生成插件 ——
        # 生成要几分钟，不能卡住这条回复；装好了会往这个会话主动回一句。
        try:
            from . import cross_platform, skill_forge

            if skill_forge.status().get("pending"):
                asyncio.create_task(skill_forge.run_pending())
            # 跨平台投递（内置基础功能）：也是回合结束后才真发，发完回话
            if cross_platform.status().get("pending"):
                asyncio.create_task(cross_platform.run_pending())
        except Exception as e:  # noqa: BLE001 —— 自学不能影响正常回话
            logger.info(f"[forge] 启动自学任务失败：{e}")
    except Exception as e:
        error_msg = f"处理聊天请求时发生异常:\n {str(e)}"
        logger.error(f"[brain] 聊天异常: {e}")
        # 2026-10-15 改（审计：X1）：
        # ① **不再 clear_context(key)** —— 上游一次超时/网关 5xx 就把整个会话的上下文
        #    删光落盘，用户下次开口机器人突然"失忆"、还得重述一遍。上下文是软状态，
        #    坏一轮就丢一轮，别一刀切清库。真要重置，用户发「清除对话」就行。
        # ② 群里**不再广播**"抱歉，处理消息时出现了问题，已通知管理员"：那是系统话术，
        #    整群都看得见、还与人设无关。管理员照旧收到 notify_error；私聊里才补一句
        #    短的（对方在等回话，什么都不说像死机）。
        try:
            await ctx.notify_error(error_msg)
        except Exception:  # noqa: BLE001 —— 上报失败不能挡住下面那句
            pass
        if not is_group:
            try:
                await ctx.send_text("我这边刚刚卡了一下，你再说一次？")
            except Exception:  # noqa: BLE001
                pass
