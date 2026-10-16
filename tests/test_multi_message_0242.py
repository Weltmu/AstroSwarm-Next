# -*- coding: utf-8 -*-
r"""分条回复（2026-10-12 老板派活）：**像人一样分几条发，但别做死成"所有回复都多条"**。

老板原话："这个 ai 目前 QQ 上的回复基本都是回答一句一条消息，没有多条式回复
（不要做死成所有回复都多条回复）"。

这份用例覆盖老板点名的 7 条必测（①~⑦），分两层：
  * **纯函数层**（`reply_format.plan_parts` / `compute_part_delay_ms`）：判定快、能精确
    指出"哪一层没拦住"；
  * **端到端层**（真的跑 `brain._reply_in_segments`，假 ctx 记录每次发送与时间间隔）：
    证明判定真的接进发送链路了，而不是只写了个纯函数没人调用。

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_multi_message_0242.py -q -s
"""
import asyncio
import importlib.util
import json
import logging
import os
import random
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"

BOT_ID = "1437808448"
GROUP = "768272849"
USER = "1000000002"
PKG = "ai_multi_e2e"

_STATE = {"sent": [], "history": [], "reply_text": "在的"}


# ---------------------------------------------------------------- 模块加载

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub_nonebot():
    if "nonebot" not in sys.modules:
        nb = types.ModuleType("nonebot")
        nb.logger = logging.getLogger("nonebot")
        sys.modules["nonebot"] = nb
    if "nonebot.adapters" not in sys.modules:
        sys.modules["nonebot.adapters"] = types.ModuleType("nonebot.adapters")
    if "nonebot.adapters.onebot.v11" not in sys.modules:
        onebot = types.ModuleType("nonebot.adapters.onebot.v11")
        onebot.MessageEvent = object
        onebot.Bot = object
        onebot.Message = object
        onebot.MessageSegment = object
        sys.modules["nonebot.adapters.onebot.v11"] = onebot
    if "nonebot_plugin_localstore" not in sys.modules:
        store = types.ModuleType("nonebot_plugin_localstore")

        def _where(name, env_key):
            base = Path(os.environ.get(env_key) or tempfile.gettempdir()) / "qbm_multi"
            base.mkdir(parents=True, exist_ok=True)
            return base / name

        store.get_plugin_config_file = lambda name: _where(name, "ASTROSWARM_DATA_HOME")
        store.get_plugin_data_file = lambda name: _where(name, "ASTROSWARM_DATA_HOME")
        store.get_plugin_cache_file = lambda name: _where(name, "ASTROSWARM_DATA_HOME")
        sys.modules["nonebot_plugin_localstore"] = store


def _reply_format_only():
    """只要判定那一层（不需要 nonebot）。"""
    if "ai_multi_rf" not in sys.modules:
        _load("ai_multi_rf", AI_DIR / "reply_format.py")
    return sys.modules["ai_multi_rf"]


def _load_brain():
    _stub_nonebot()
    if PKG in sys.modules:
        return sys.modules[PKG + ".brain"], sys.modules[PKG + ".platform"]
    pkg_mod = types.ModuleType(PKG)
    pkg_mod.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg_mod

    class _Cfg:
        aichat_super_users = []
        aichat_proactive_groups = []
        aichat_session_expire_seconds = 30
        aichat_session_mode = "timeout"

        def __getattr__(self, name):
            numeric = ("interval", "cooldown", "count", "seconds", "limit",
                       "probability", "quiet", "start", "end")
            return 30 if any(k in name for k in numeric) else []

    cfg_mod = types.ModuleType(PKG + ".config")
    cfg_mod.config = _Cfg()
    cfg_mod.PluginConfig = type("PluginConfig", (), {})
    sys.modules[PKG + ".config"] = cfg_mod
    for name in ("group_policy", "session_policy", "platform", "target_guard", "context",
                 "identity", "memory", "memory_bridge", "reply_format", "tool_marker",
                 "prompt_window"):
        sys.modules[PKG + "." + name] = _load("%s.%s" % (PKG, name), AI_DIR / (name + ".py"))
    manager = _load(PKG + ".manager", AI_DIR / "manager.py")
    sys.modules[PKG + ".manager"] = manager

    chat = types.ModuleType(PKG + ".chat")

    async def fake_reply(messages, is_group, **kw):
        _STATE["history"] = list(messages)
        return _STATE["reply_text"]

    async def fake_judge(messages, user_id, nickname, last_reply_to=""):
        return True

    chat.get_chat_reply_with_tools = fake_reply
    chat.judge_user_continues = fake_judge
    chat.should_reply_in_group = fake_judge
    chat.get_chat_reply = fake_reply
    sys.modules[PKG + ".chat"] = chat

    brain = _load(PKG + ".brain", AI_DIR / "brain.py")
    # ⚠ brain 里是 `from nonebot import logger`。**全套跑**时前面某个测试文件可能已经把
    # 真的 nonebot 导进来了，那时拿到的是 nonebot 自己的 logger（loguru），消息不进
    # stdlib 的 root logger → caplog 永远是空的（单跑这个文件反而是绿的 —— 典型的
    # "靠谁先跑"）。这里把模块的 logger 固定成标准 logging，caplog 才抓得到。
    brain.logger = logging.getLogger("ai_multi_brain")
    return brain, sys.modules[PKG + ".platform"]


# ---------------------------------------------------------------- 假事件 / 假 ctx

class _Seg:
    def __init__(self, type_, **data):
        self.type = type_
        self.data = data


class _Event:
    def __init__(self, segments, self_id=BOT_ID, raw_message="", to_me=False):
        self.to_me = to_me
        self.self_id = self_id
        self.message = segments
        self.raw_message = raw_message or "".join("[%s]" % s.type for s in segments)

    def get_message(self):
        return self.message

    def is_tome(self):
        return self.to_me


def _ctx(platform_mod, *, multi=False, note="", room=GROUP, fail_at=None):
    """假 ctx：记录**每一次发送 + 单调时钟**，并可按序号注入发送失败。

    `fail_at` = 第几条（从 0 数）发送时抛异常 —— 用来测"第 2 条失败"。
    """
    sent = []

    async def _record(kind, text):
        idx = len(sent)
        if fail_at is not None and idx == fail_at:
            raise RuntimeError("模拟发送失败（第 %d 条）" % (idx + 1))
        sent.append({"kind": kind, "text": text, "t": time.monotonic(),
                     "delay_slept": _SLEPT.pop(0) if _SLEPT else None})

    async def send(t):
        await _record("text", t)

    async def send_at(t):
        await _record("at", t)

    async def notify(err):
        sent.append({"kind": "error", "text": err, "t": time.monotonic(),
                     "delay_slept": None})

    ctx = platform_mod.ChannelContext(
        platform="qq", scene="group", send_text=send, send_text_at=send_at,
        notify_error=notify, target_key="qq:%s" % room,
        multi_message=multi, multi_message_note=note)
    ctx._sent = sent
    return ctx


# 记录"每一次 asyncio.sleep 睡了多少秒"，供假 ctx 读取（顺序与发送一一对应）
_SLEPT = []


class _FakeSleep:
    """把 asyncio.sleep 换成"只记账、不真睡"：判定里的毫秒数能被逐条断言。"""

    def __init__(self):
        self.calls = []
        self._real = asyncio.sleep

    async def __call__(self, seconds, *a, **kw):
        self.calls.append(float(seconds))
        await self._real(0)


def _msg(platform_mod, event, *, room=GROUP, user=USER, text="聊聊"):
    return platform_mod.ChannelMessage(
        platform="qq", scene="group", user_id=user, room_id=room, text=text,
        is_at_me=True, nickname="老板", plain_text=text, raw=event,
        extra={"at_others": False, "quoted": None})


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    data_home = tmp_path_factory.mktemp("datahome")
    os.environ["ASTROSWARM_DATA_HOME"] = str(data_home)
    os.environ["XDG_DATA_HOME"] = str(data_home)
    brain, platform_mod = _load_brain()
    brain.chat_manager._data.update({
        "chat_enabled": True, "group_chat_enabled": True, "enabled_groups": [],
        "session_mode": "timeout", "session_expire_seconds": 30,
        "context_window_minutes": 5, "max_incoming_chars": 4000,
        "empty_at_action": "greet", "group_auto_participate": False,
        "multi_message_mode": "auto", "max_reply_parts": 3, "max_part_chars": 500,
        "part_delay_ms": 80, "part_delay_max_ms": 3000,
    })
    return brain, platform_mod


def _reset(brain):
    brain.group_sessions.clear()
    brain.last_reply_user.clear()
    brain.clear_context("group_%s" % GROUP)


async def _send(brain, ctx, raw):
    """直接跑真正的发送函数（不经过 handle_message 的其它判定）。"""
    _SLEPT.clear()
    await brain._reply_in_segments(ctx, raw, at_user=True)


def _run(brain, ctx, raw):
    asyncio.run(_send(brain, ctx, raw))
    return ctx._sent


def _reply(*parts):
    return json.dumps({"reply": list(parts)}, ensure_ascii=False)


# ================================================================ ① 三段叙述 → 真的分 3 条

def test_case1_three_narrative_parts_are_sent_separately(env, caplog):
    """① 模型给 3 条、内容偏叙述 → **真的分 3 条发出**（记录每次发送与时间间隔）。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True, note="OneBot（NapCat）：可以连发多条")
    raw = _reply("先说我查到的", "你那台机器是磁盘满了，日志占了 12G",
                 "清掉旧日志就好了，要我帮你清吗")
    fake = _FakeSleep()
    real_sleep = asyncio.sleep
    asyncio.sleep = fake
    try:
        with caplog.at_level(logging.INFO):
            sent = _run(brain, ctx, raw)
    finally:
        asyncio.sleep = real_sleep
    texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
    assert len(texts) == 3, "必须真的分 3 条发出，实际 %r" % (texts,)
    assert texts[0].endswith("先说我查到的"), "第一条带 @（at_user=True）"
    # 首条不延迟：只有 2 次 sleep（第 2、3 条之前各一次）
    assert len(fake.calls) == 2, "首条不延迟、3 条只需 2 次间隔，实际 %r" % (fake.calls,)
    assert all(0.3 <= s <= 3.0 for s in fake.calls), "间隔必须在上下限内：%r" % (fake.calls,)
    assert "分条判定（I7）" in caplog.text, "判定必须留一行日志"
    assert "结论: 分 3 条" in caplog.text
    line = [ln for ln in caplog.text.splitlines() if "分条判定" in ln][0]
    assert "间隔毫秒=" in line and "每段字数" in line, "日志要写明每条多少字、间隔多少毫秒"
    print("\n[①] 发送=%r" % (texts,))
    print("[①] 间隔(秒)=%r ｜ 日志：%s" % (fake.calls, line[-150:]))


# ================================================================ ② 三条都很短 → 合并成 1 条

def test_case2_all_short_parts_are_merged(env, caplog):
    """② 模型给 3 条但都很短（「嗯」「好」「在」）→ **合并成 1 条**。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True, note="OneBot（NapCat）：可以连发多条")
    fake = _FakeSleep()
    real_sleep = asyncio.sleep
    asyncio.sleep = fake
    try:
        with caplog.at_level(logging.INFO):
            sent = _run(brain, ctx, _reply("嗯", "好", "在"))
    finally:
        asyncio.sleep = real_sleep
    texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
    assert len(texts) == 1, "都很短必须合并成 1 条，实际 %r" % (texts,)
    # 2026-10-15：合并时补逗号（以前是 "".join → "嗯好在"，"好在"看着像错字）
    assert texts[0].endswith("嗯，好，在")
    assert fake.calls == [], "只有一条时不该有任何间隔"
    line = [ln for ln in caplog.text.splitlines() if "分条判定" in ln][0]
    assert "是否合并=是" in line, line
    assert ("语气词" in line or "短" in line), "合并原因要写明：%s" % line
    print("[②] 发送=%r ｜ 日志：%s" % (texts, line[-140:]))


def test_case2b_filler_parts_are_merged(env):
    """② 附：全是语气词（「好的」「哈哈」）也合并 —— 不只看字数。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True)
    sent = _run(brain, ctx, _reply("哈哈", "好的呀"))
    texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
    assert len(texts) == 1, "全是语气词要合并，实际 %r" % (texts,)


# ================================================================ ③ 只给一条 → 就一条

def test_case3_single_part_is_never_split(env, caplog):
    """③ 模型给 1 条 → **就 1 条**（不许拆）；哪怕它很长也不拆。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True)
    long_one = "今天天气不错，" * 12          # 84 字，远超"短"的阈值
    with caplog.at_level(logging.INFO):
        sent = _run(brain, ctx, _reply(long_one))
    texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
    assert len(texts) == 1, "模型只给一条就必须只发一条（不许硬拆），实际 %d 条" % len(texts)
    assert long_one in texts[0]
    line = [ln for ln in caplog.text.splitlines() if "分条判定" in ln][0]
    assert "模型只给了一条（不许硬拆）" in line
    print("[③] 发送 %d 条 ｜ 日志：%s" % (len(texts), line[-140:]))


# ================================================================ ④ 模式=关 → 永远一条

def test_case4_mode_off_always_merges(env, caplog):
    """④ `multi_message_mode=关` → 无论模型给几条都**合成一条**。"""
    brain, platform_mod = env
    _reset(brain)
    brain.chat_manager._data["multi_message_mode"] = "off"
    try:
        ctx = _ctx(platform_mod, multi=True)
        with caplog.at_level(logging.INFO):
            sent = _run(brain, ctx, _reply("第一句挺长的内容", "第二句也不短呢", "第三句同样"))
        texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
        assert len(texts) == 1, "模式=关时必须一条，实际 %r" % (texts,)
        assert "模式=关（永远一条）" in caplog.text
        print("[④] 发送=%r" % (texts,))
    finally:
        brain.chat_manager._data["multi_message_mode"] = "auto"


# ================================================================ ⑤ 受限通道 → 合并 + 写明原因

def test_case5_restricted_channel_merges_with_reason(env, caplog):
    """⑤ **官方通道（或标记为受限的通道）** → 默认合并成一条，且日志写明原因。"""
    brain, platform_mod = env
    _reset(brain)
    note = "QQ 官方通道：主动消息有额度，分多条会把额度烧掉 → 默认合并"
    ctx = _ctx(platform_mod, multi=False, note=note)
    with caplog.at_level(logging.INFO):
        sent = _run(brain, ctx, _reply("先说我查到的", "你那台机器磁盘满了，日志占了 12G",
                                       "清掉旧日志就好"))
    texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
    assert len(texts) == 1, "受限通道默认必须合并成一条，实际 %r" % (texts,)
    line = [ln for ln in caplog.text.splitlines() if "分条判定" in ln][0]
    assert "通道可连发=否" in line
    assert "主动消息有额度" in line, "日志必须写明「为什么合并」：%s" % line
    print("[⑤] 发送=%r\n[⑤] 日志：%s" % (texts, line[-190:]))


def test_case5b_onebot_channel_allows_multi(env):
    """⑤ 附：同为 QQ，OneBot（NapCat）通道**可以**分条 —— 差异只在通道标记。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True, note="OneBot（NapCat）：可以连发多条")
    sent = _run(brain, ctx, _reply("先说我查到的", "你那台机器磁盘满了，日志占了 12G",
                                   "清掉旧日志就好"))
    texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
    assert len(texts) == 3, "OneBot 通道该分 3 条，实际 %r" % (texts,)


def test_case5c_always_mode_overrides_restricted_channel(env, caplog):
    """⑤ 附：模式=总是 是**测试/特殊人设**的逃生口，连受限通道也分条（日志留痕）。"""
    brain, platform_mod = env
    _reset(brain)
    brain.chat_manager._data["multi_message_mode"] = "always"
    try:
        ctx = _ctx(platform_mod, multi=False, note="QQ 官方通道：主动消息有额度")
        with caplog.at_level(logging.INFO):
            sent = _run(brain, ctx, _reply("第一条内容比较长", "第二条内容也比较长", "第三条"))
        texts = [s["text"] for s in sent if s["kind"] in ("text", "at")]
        assert len(texts) == 3, "总是模式该无视通道限制，实际 %r" % (texts,)
        assert "模式=always" in caplog.text
    finally:
        brain.chat_manager._data["multi_message_mode"] = "auto"


# ================================================================ ⑥ 延迟有上下限

def test_case6_delay_has_floor_and_ceiling():
    """⑥ 延迟**有上下限**（不会 0 也不会 10 秒）：纯函数逐个长度扫一遍。"""
    rf = _reply_format_only()
    rng = random.Random(20261012)
    seen = []
    for n in (0, 1, 2, 5, 20, 60, 200, 4000):
        for _ in range(50):
            d = rf.compute_part_delay_ms("字" * n, per_char_ms=80, max_delay_ms=3000, rng=rng)
            seen.append(d)
            assert d >= rf.PART_DELAY_FLOOR_MS, "延迟不能低于下限：n=%d → %d" % (n, d)
            assert d <= 3000, "延迟不能超过配置上限：n=%d → %d" % (n, d)
    assert min(seen) >= 300 > 0, "最坏情况也不会 0 毫秒连发"
    assert max(seen) <= 3000 < 10000, "最坏情况也不会等 10 秒"
    # 配置写大了也有硬顶（PART_DELAY_CEILING_MS），机器人侧还会夹到 5000
    huge = rf.compute_part_delay_ms("字" * 99999, per_char_ms=500, max_delay_ms=999999,
                                    rng=rng)
    assert huge == rf.PART_DELAY_CEILING_MS, "配置再大也不许超过硬顶：%r" % huge
    # 单调：字越多等越久（在到顶之前）
    a = rf.compute_part_delay_ms("字" * 3, 80, 3000, random.Random(1))
    b = rf.compute_part_delay_ms("字" * 30, 80, 3000, random.Random(1))
    assert a < b, "字数多应当等更久：%d vs %d" % (a, b)
    print("[⑥] 延迟范围 = [%d, %d] 毫秒（下限 %d，配置上限 3000，硬顶 %d）"
          % (min(seen), max(seen), rf.PART_DELAY_FLOOR_MS, rf.PART_DELAY_CEILING_MS))


def test_case6b_sent_intervals_match_plan(env):
    """⑥ 附：端到端**真的**按判定好的毫秒数 sleep（不真等，记账断言）。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True)
    fake = _FakeSleep()
    real_sleep = asyncio.sleep
    asyncio.sleep = fake
    try:
        _run(brain, ctx, _reply("第一句内容中等长度", "第二句", "第三句内容稍微长一点点啦"))
    finally:
        asyncio.sleep = real_sleep
    assert len(fake.calls) == 2
    assert all(0.3 <= s <= 3.0 for s in fake.calls), "间隔秒数越界：%r" % (fake.calls,)
    print("[⑥b] 实际 sleep(秒) = %r" % (fake.calls,))


def test_case6c_real_timing_is_not_zero(env, caplog):
    """⑥ 附：**不替换 sleep** 也不打桩计时 —— 两次发送之间真的有间隔（>0）。"""
    brain, platform_mod = env
    _reset(brain)
    brain.chat_manager._data["part_delay_ms"] = 0        # 最小间隔（下限 300ms）
    try:
        ctx = _ctx(platform_mod, multi=True)
        with caplog.at_level(logging.INFO):
            sent = _run(brain, ctx, _reply("第一句稍微长一点点",
                                           "第二句内容也够长了吧",
                                           "第三句内容同样不短呢"))
    finally:
        brain.chat_manager._data["part_delay_ms"] = 80
    stamps = [s["t"] for s in sent if s["kind"] in ("text", "at")]
    assert len(stamps) == 3
    gaps = [round((stamps[i + 1] - stamps[i]) * 1000) for i in range(len(stamps) - 1)]
    assert all(g >= 290 for g in gaps), "真实间隔不能是 0（下限 300ms）：%r" % (gaps,)
    assert all(g <= 3200 for g in gaps), "真实间隔不能到 10 秒：%r" % (gaps,)
    print("[⑥c] 真实间隔(毫秒) = %r" % (gaps,))


# ================================================================ ⑦ 第 2 条失败：不重试、不影响已发出

def test_case7_second_part_failure_warns_and_stops(env, caplog):
    """⑦ 第 2 条发送抛异常 → 第 1 条已成功、日志有 warning、**不重试**。"""
    brain, platform_mod = env
    _reset(brain)
    parts = ["先说我查到的", "你那台机器磁盘满了", "清掉旧日志就好"]
    ctx = _ctx(platform_mod, multi=True, fail_at=1)      # 第 2 条（下标 1）抛异常
    with caplog.at_level(logging.INFO):
        sent = _run(brain, ctx, _reply(*parts))
    real = [s for s in sent if s["kind"] in ("text", "at")]
    assert len(real) == 1, "第 2 条失败后必须停手（不重试、不继续），实际发了 %d 条" % len(real)
    assert real[0]["text"].endswith(parts[0]), "第 1 条必须已经成功发出去"
    warnings = [ln for ln in caplog.text.splitlines() if "第 2/3 条发送失败" in ln]
    assert warnings, "必须有 warning 日志：\n%s" % caplog.text
    assert "不重试" in warnings[0]
    assert len([s for s in sent if s["kind"] == "error"]) == 0, "不该走 notify_error 报错轰炸"
    print("[⑦] 实际发出=%r\n[⑦] warning：%s" % ([s["text"] for s in real], warnings[0][-150:]))


def test_case7b_first_part_failure_still_raises(env):
    """⑦ 附：**第 1 条**失败仍然照老规矩上抛（否则用户一个字都收不到、日志也没有）。"""
    brain, platform_mod = env
    _reset(brain)
    ctx = _ctx(platform_mod, multi=True, fail_at=0)
    with pytest.raises(RuntimeError):
        _run(brain, ctx, _reply("第一句", "第二句内容", "第三句"))


# ================================================================ 回归锁（判定层纯函数）

def test_lock_plan_parts_rules():
    """回归锁：判定规则本身（合并 / 上限 / 截断 / 不硬拆）。"""
    rf = _reply_format_only()
    rng = random.Random(1)

    # 上限：给 5 条、上限 3 → 尾部并进第 3 条，**不丢内容**
    seg = ["甲甲甲甲甲甲甲甲甲甲", "乙乙乙乙乙乙乙乙乙乙", "丙丙丙丙丙丙丙丙丙丙",
           "丁丁丁丁丁丁丁丁丁丁", "戊戊戊戊戊戊戊戊戊戊"]
    parts, info = rf.plan_parts(seg, max_parts=3, rng=rng)
    assert len(parts) == 3 and info["capped"] is True
    # 尾部并进最后一条，**不丢字**；段间补分隔符（2026-10-15：以前直接粘一起，
    # "丙…丁…" 糊成一坨读不出来）
    assert parts[2] == seg[2] + "。" + seg[3] + "。" + seg[4], parts[2]
    assert parts[2].replace("。", "") == seg[2] + seg[3] + seg[4], "一个字都不许丢"

    # 上限：条数=1 等价于"永远一条"
    parts, info = rf.plan_parts(["甲甲甲甲甲甲甲甲", "乙乙乙乙乙乙乙乙"], max_parts=1, rng=rng)
    assert parts == ["甲甲甲甲甲甲甲甲。乙乙乙乙乙乙乙乙"], parts

    # 单条字数上限：超了截断（绝不硬拆），且长度真的不超过上限
    parts, info = rf.plan_parts(["字" * 700], max_part_chars=100, rng=rng)
    assert len(parts) == 1 and len(parts[0]) <= 100
    assert info["truncated"] == [(700, 100)]

    # 模式=总是：很短也照分（测试/特殊人设）
    parts, info = rf.plan_parts(["嗯", "好", "在"], mode="always", rng=rng)
    assert parts == ["嗯", "好", "在"]

    # 模式写坏了回自动（不悄悄关掉功能）
    assert rf.normalize_multi_mode("瞎写") == "auto"
    assert rf.normalize_multi_mode("关") == "off"
    assert rf.normalize_multi_mode("总是") == "always"

    # 日志行必须带齐"几条 / 每条多少字 / 间隔多少毫秒 / 是否合并"
    line = rf.describe_plan(info)
    for token in ("结论", "每段字数", "间隔毫秒", "是否合并"):
        assert token in line, "日志缺 %s：%s" % (token, line)


def test_lock_adapters_declare_channel_capability():
    """回归锁：通道是否可连发必须**显式声明**（QQ 官方/微信默认受限）。

    这一条是老板口径"能自然连发的通道才分条，受限通道默认合并"的守门人 ——
    以后谁把官方通道改成可连发，这里会红。
    """
    qq = (AI_DIR / "adapters" / "qq.py").read_text(encoding="utf-8")
    official = (AI_DIR / "adapters" / "qq_official.py").read_text(encoding="utf-8")
    wechat = (AI_DIR / "adapters" / "wechat.py").read_text(encoding="utf-8")
    assert "multi_message=True" in qq, "OneBot（NapCat）必须显式声明可连发"
    assert "multi_message=True" not in official, "QQ 官方通道不许声明可连发（会烧额度）"
    assert official.count("multi_message=False") >= 2, "官方通道每个入口都要标记受限"
    assert "multi_message=False" in wechat, "微信通道保守合并"
    # 数据类默认值必须是保守的 False（新适配器忘了标记 = 合并，不是分条）
    plat = (AI_DIR / "platform.py").read_text(encoding="utf-8")
    assert "multi_message: bool = False" in plat


def test_lock_manager_settings_are_hot_read(env):
    """回归锁：五个配置项的默认值 / 夹取范围（机器人侧**每条消息现读**）。"""
    brain, _ = env
    mgr = brain.chat_manager
    mgr._data.pop("multi_message_mode", None)
    assert mgr.get_multi_message_mode() == "auto", "默认必须是自动"
    mgr._data["multi_message_mode"] = "乱写"
    assert mgr.get_multi_message_mode() == "auto", "写坏了回自动，不许回 off"
    assert mgr.get_max_reply_parts() == 3
    mgr._data["max_reply_parts"] = 99
    assert mgr.get_max_reply_parts() == 6, "夹到 1~6"
    mgr._data["max_reply_parts"] = "坏了"
    assert mgr.get_max_reply_parts() == 3
    del mgr._data["max_reply_parts"]
    assert mgr.get_max_part_chars() == 500
    mgr._data["max_part_chars"] = 1
    assert mgr.get_max_part_chars() == 50, "夹到 50~2000"
    del mgr._data["max_part_chars"]
    assert mgr.get_part_delay_ms() == 80
    mgr._data["part_delay_ms"] = 99999
    assert mgr.get_part_delay_ms() == 500
    del mgr._data["part_delay_ms"]
    assert mgr.get_part_delay_max_ms() == 3000
    mgr._data["part_delay_max_ms"] = 60000
    assert mgr.get_part_delay_max_ms() == 5000, "上限绝不允许到 10 秒"
    mgr._data["part_delay_max_ms"] = 1
    assert mgr.get_part_delay_max_ms() == 300, "下限 300，保证不会 0 毫秒连发"
    del mgr._data["part_delay_max_ms"]


def test_lock_brain_calls_the_planner():
    """回归锁：发送链路**真的**接了判定（不是写了个纯函数没人用）。"""
    src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "reply_format.plan_parts(" in src
    assert "chat_manager.get_multi_message_mode()" in src
    assert "分条判定（I7）" in src
    assert "不重试" in src
