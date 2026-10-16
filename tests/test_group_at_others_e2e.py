# -*- coding: utf-8 -*-
r"""端到端（假 ctx，绝不发真实群消息）：老板那条场景走**真的** `brain.handle_message`。

为什么还要这个文件：单测是在纯函数层面把 `brain.py` 的决策点"复刻"出来测的，
而这里是**把真函数跑起来**（适配器归一化用真 `group_policy`，决策/日志/上文窗口
全走生产代码），只把"模型"和"发送"换成替身。两者互补：
  * 纯函数单测 → 改前/改后对照（快、能精确指出哪一层漏了）；
  * 本文件 → 真链路不回归（例如 `says_at_other` 真的接进 `handle_message` 了）。

覆盖：①  @别人（未@我）+ 免@有效期内 + direct_continue 条件全成立 → **不发消息**
       ②  @我和别人 → 发
       ③  只@我 → 发
       ④  没@任何人 + 有效期内 → 发；超期 → 不发
       ⑤  私聊 → 发（不受 @ 影响）
       ⑥  通知类事件（按钮）→ 发，但**上文 0 条**
       ⑦  超长正文 → 截断（上限可配）
       ⑧  线上 22:09:03 那条原文（reply + @别人）→ **不发**，且引用段被认出

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_group_at_others_e2e.py -q -s
"""
import asyncio
import importlib.util
import json
import logging
import os
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
OTHER = "6885483"
PKG = "ai_brain_e2e"

_STATE = {"sent": [], "history": [], "judge_calls": 0, "reply_text": "在的，你说"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub_nonebot():
    """构建环境没有 nonebot：补最小替身（与 tests/test_ai_plugin_group_session.py 同法）。"""
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
            base = Path(os.environ.get(env_key) or tempfile.gettempdir()) / "qbm_e2e"
            base.mkdir(parents=True, exist_ok=True)
            return base / name

        store.get_plugin_config_file = lambda name: _where(name, "ASTROSWARM_DATA_HOME")
        store.get_plugin_data_file = lambda name: _where(name, "ASTROSWARM_DATA_HOME")
        store.get_plugin_cache_file = lambda name: _where(name, "ASTROSWARM_DATA_HOME")
        sys.modules["nonebot_plugin_localstore"] = store


def _load_brain():
    """加载**真的** brain.py（连同 group_policy / session_policy / platform / target_guard）。"""
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
    # manager（读配置的那个）—— 用真文件读，但指到临时目录
    manager = _load(PKG + ".manager", AI_DIR / "manager.py")
    sys.modules[PKG + ".manager"] = manager
    # chat / image2txt / 其它依赖：只用到几个函数，替身即可
    chat = types.ModuleType(PKG + ".chat")

    async def fake_reply(messages, is_group, **kw):
        _STATE["history"] = list(messages)
        return _STATE["reply_text"]

    async def fake_judge(messages, user_id, nickname, last_reply_to=""):
        _STATE["judge_calls"] += 1
        return True                      # 最坏情况：AI 判"他在接着聊"

    async def fake_group_judge(messages):
        return True

    chat.get_chat_reply_with_tools = fake_reply
    chat.judge_user_continues = fake_judge
    chat.should_reply_in_group = fake_judge
    chat.get_chat_reply = fake_reply
    sys.modules[PKG + ".chat"] = chat

    brain = _load(PKG + ".brain", AI_DIR / "brain.py")
    return brain, sys.modules[PKG + ".platform"]


# ---------------------------------------------------------------- 假事件 / 假 ctx

class _Seg:
    def __init__(self, type_, **data):
        self.type = type_
        self.data = data


class _Event:
    """OneBot 事件替身：对象段形态（与线上一致）。"""

    def __init__(self, segments, self_id=BOT_ID, raw_message="", to_me=False):
        self.to_me = to_me
        self.self_id = self_id
        self.message = segments
        self.raw_message = raw_message or "".join(
            "[%s]" % s.type for s in segments)

    def get_message(self):
        return self.message

    def is_tome(self):
        return self.to_me


def _ctx(platform_mod, *, scene="group", room=GROUP, target=None):
    sent = []

    async def send(t):
        sent.append(("text", t))

    async def send_at(t):
        sent.append(("at", t))

    async def send_image(t, caption=""):
        sent.append(("image", t))
        return True

    async def notify(err):
        sent.append(("error", err))

    ctx = platform_mod.ChannelContext(
        platform="qq", scene=scene, send_text=send, send_text_at=send_at,
        send_image=send_image, notify_error=notify,
        target_key=(f"qq:{room}" if target is None else target))
    ctx._sent = sent
    return ctx


def _msg(platform_mod, event, *, scene="group", room=GROUP, user=USER, quoted=None,
         extra=None, text="你们先聊", is_at_me=None, notice_kind=""):
    gp = sys.modules[PKG + ".group_policy"]
    if is_at_me is None:
        is_at_me = gp.is_at_me(event, bot_id=BOT_ID)
    at_others = gp.mentions_other(event, bot_id=BOT_ID) if scene == "group" else False
    payload = {"at_others": at_others, "quoted": quoted}
    if notice_kind:
        payload.update({"is_notice": True, "notice_kind": notice_kind})
    payload.update(extra or {})
    return platform_mod.ChannelMessage(
        platform="qq", scene=scene, user_id=user, room_id=room, text=text,
        is_at_me=is_at_me, nickname="老板", plain_text=text, raw=event, extra=payload)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    data_home = tmp_path_factory.mktemp("datahome")
    os.environ["ASTROSWARM_DATA_HOME"] = str(data_home)
    os.environ["XDG_DATA_HOME"] = str(data_home)
    brain, platform_mod = _load_brain()
    # 免@会话有效期 30 秒、timeout 模式、群聊开、聊天开、窗口 5 分钟
    brain.chat_manager._data.update({
        "chat_enabled": True, "group_chat_enabled": True, "enabled_groups": [],
        "session_mode": "timeout", "session_expire_seconds": 30,
        "context_window_minutes": 5, "max_incoming_chars": 4000,
        "empty_at_action": "greet", "group_auto_participate": False,
    })
    return brain, platform_mod


def _reset(brain, *, session_on=True, with_assistant=True):
    """每个用例前重置内存态：免@会话 + 上文（造出 direct_continue 的四个条件）。

    key 口径照生产：`session_key = f"{platform}:{room_id}"`、
    `user_key = f"{platform}:{user_id}"`（brain 那边拿 `msg.user_key` 存的）。
    """
    brain.group_sessions.clear()
    brain.last_reply_user.clear()
    room = f"qq:{GROUP}"
    user_key = f"qq:{USER}"
    if session_on:
        brain.session_activate(room, user_key)
        brain.last_reply_user[room] = USER
    brain.clear_context(_ctx_key(brain, GROUP))
    if with_assistant:
        brain.add_message(_ctx_key(brain, GROUP), "user",
                          "用户%s(老板)说：你好" % USER)
        brain.add_message(_ctx_key(brain, GROUP), "assistant", "你好呀，我在")


def _ctx_key(brain, room):
    """QQ 群会话的记忆 key 沿用旧格式 `group_<room_id>`（见 platform.ChannelMessage.context_key）。"""
    return f"group_{room}"


def _run(brain, msg, ctx):
    _STATE["judge_calls"] = 0
    asyncio.run(brain.handle_message(msg, ctx))
    return ctx._sent


# ---------------------------------------------------------------- ①~④ 老板的判定优先级

def test_e2e_case1_at_other_never_replies(env, caplog):
    """① @ 了别人（未 @ 我）+ 免@会话有效期内 + direct_continue 条件成立 → **不发消息**。"""
    brain, platform_mod = env
    _reset(brain)
    event = _Event([_Seg("at", qq=OTHER), _Seg("text", text="你们先聊")],
                   raw_message="[CQ:at,qq=%s] 你们先聊" % OTHER)
    msg = _msg(platform_mod, event, text="你们先聊")
    assert msg.extra["at_others"] is True and msg.is_at_me is False
    ctx = _ctx(platform_mod)
    with caplog.at_level(logging.INFO):
        sent = _run(brain, msg, ctx)
    assert sent == [], "改后：@ 了别人（未 @ 我）绝不能接话，实际发了 %r" % (sent,)
    assert "本条 @ 了别人（未 @ 我）→ 不接话（不进入免@延续）" in caplog.text, \
        "日志必须写明原因（老板口径）"
    assert _STATE["judge_calls"] == 0, "连 AI 判断都不该走（不能靠模型自觉）"
    print("\n[e2e ①] sent=%r  judge_calls=%d" % (sent, _STATE["judge_calls"]))
    print("[e2e ①] 日志：" + [ln for ln in caplog.text.splitlines()
                             if "不接话" in ln][0][-80:])


def test_e2e_case2_at_me_and_other_replies(env):
    """② 同时 @ 我和别人 → 正常回复。"""
    brain, platform_mod = env
    _reset(brain)
    event = _Event([_Seg("at", qq=BOT_ID), _Seg("at", qq=OTHER), _Seg("text", text="你们先聊")])
    msg = _msg(platform_mod, event, text="你们先聊")
    assert msg.is_at_me is True and msg.extra["at_others"] is True
    sent = _run(brain, msg, _ctx(platform_mod))
    assert sent, "同时 @ 我和别人必须回"
    print("[e2e ②] sent=%r" % (sent,))


def test_e2e_case3_at_me_only_replies(env):
    """③ 只 @ 我 → 正常回复。"""
    brain, platform_mod = env
    _reset(brain)
    event = _Event([_Seg("at", qq=BOT_ID), _Seg("text", text="你好")], to_me=True)
    msg = _msg(platform_mod, event, text="你好")
    assert msg.is_at_me is True and msg.extra["at_others"] is False
    sent = _run(brain, msg, _ctx(platform_mod))
    assert sent, "只 @ 我必须回"
    print("[e2e ③] sent=%r" % (sent,))


def test_e2e_case4_no_at_uses_session_window(env):
    """④ 没 @ 任何人 + 有效期内 → 回（延续）；超出有效期 → 不回。"""
    brain, platform_mod = env
    event = _Event([_Seg("text", text="你刚才说的啥")])
    msg = _msg(platform_mod, event, text="你刚才说的啥")

    _reset(brain, session_on=True)
    sent = _run(brain, msg, _ctx(platform_mod))
    assert sent, "有效期内应当延续"
    print("[e2e ④] 有效期内 sent=%r judge_calls=%d" % (sent, _STATE["judge_calls"]))

    _reset(brain, session_on=False)
    sent2 = _run(brain, msg, _ctx(platform_mod))
    assert sent2 == [], "超出有效期不该回，实际 %r" % (sent2,)
    print("[e2e ④] 超期 sent=%r" % (sent2,))


# ---------------------------------------------------------------- ⑤~⑦ 其它口径

def test_e2e_case5_private_unaffected(env):
    """⑤ 私聊不受影响：私聊没有 @ 概念，有实义就回（且不算空@）。"""
    brain, platform_mod = env
    _reset(brain, session_on=False)
    event = _Event([_Seg("text", text="你在干嘛呢")], to_me=True)
    msg = _msg(platform_mod, event, scene="private", room=USER, user=USER, text="你在干嘛呢",
               is_at_me=False)
    sent = _run(brain, msg, _ctx(platform_mod, scene="private", room=USER))
    assert sent, "私聊必须回"
    print("[e2e ⑤] sent=%r" % (sent,))


def test_e2e_case6_notice_event_zero_history(env):
    """⑥ 通知类事件（按钮）→ 回，但**上文 0 条**（规则表第 13/14 行）。

    按钮交互适配器给的 `is_at_me=True`（它是"用户明确点了它"），所以走
    `BRANCH_AT_ME`；重点是**窗口必须清零** —— 改前会照常带上最近 20 条。
    """
    brain, platform_mod = env
    _reset(brain)
    _STATE["history"] = []
    event = _Event([], raw_message="")
    msg = _msg(platform_mod, event, text="（用户点了按钮：签到）", notice_kind="interaction",
               is_at_me=True)
    sent = _run(brain, msg, _ctx(platform_mod))
    assert sent, "通知类事件该回一句"
    hist = _STATE["history"]
    assert hist == [], "通知类事件必须 0 条上文，实际注入了 %d 条" % len(hist)
    print("[e2e ⑥] sent=%r history=%d 条" % (sent, len(hist)))


def test_e2e_case7_long_message_is_truncated(env, caplog):
    """⑦ 超长正文 → 截断到上限（可配），并写清截了多少。"""
    brain, platform_mod = env
    _reset(brain)
    big = "长" * 12000
    event = _Event([_Seg("at", qq=BOT_ID), _Seg("text", text=big)], to_me=True)
    msg = _msg(platform_mod, event, text=big)
    with caplog.at_level(logging.INFO):
        sent = _run(brain, msg, _ctx(platform_mod))
    assert sent
    joined = "".join(t for _, t in sent)
    assert len(joined) < 12000, "超长正文必须被截断（实际发 %d 字）" % len(joined)
    assert "单条正文过长 → 截断：原 12000 字 → 保留 4000 字" in caplog.text, \
        "日志要写清截了多少"
    # 进上文的也是截断后的
    assert len(_STATE["history"][-1]["content"]) < 12000
    print("[e2e ⑦] 实发 %d 字 ｜ 日志：" % len(joined)
          + [ln for ln in caplog.text.splitlines() if "截断" in ln][0][-70:])


# ---------------------------------------------------------------- ⑧ 线上原文复现

def test_e2e_case8_online_message_2209(env, caplog):
    """⑧ **线上 22:09:03 那条原文**（reply + @别人）→ 不发消息；引用段要认出来。"""
    brain, platform_mod = env
    _reset(brain)
    raw = ("[reply:id=388291130][at:qq=%s] 不是逻辑层面，是代码层面bug" % OTHER)
    event = _Event([_Seg("reply", id="388291130"), _Seg("at", qq=OTHER),
                    _Seg("text", text="不是逻辑层面，是代码层面bug")],
                   raw_message=raw)
    gp = sys.modules[PKG + ".group_policy"]
    quote = gp.find_reply_segment(event)
    assert str(quote["reply_id"]) == "388291130", "线上那条带 reply 段，必须认出来"
    assert quote["from"] == "seg:reply.id"
    msg = _msg(platform_mod, event, text="不是逻辑层面，是代码层面bug",
               quoted={"content": "（被引用那条）", "ts": 0.0, "message_id": "388291130"})
    assert msg.extra["at_others"] is True and msg.is_at_me is False
    ctx = _ctx(platform_mod)
    with caplog.at_level(logging.INFO):
        sent = _run(brain, msg, ctx)
    assert sent == [], "改后：这条必须不接话（22:09:05 那条不能再出现）"
    assert "本条 @ 了别人（未 @ 我）→ 不接话（不进入免@延续）" in caplog.text
    print("\n[e2e ⑧] 段列表=%s" % gp.describe_segments(event))
    print("[e2e ⑧] 引用段=%s（依据 %s）｜sent=%r"
          % (quote["reply_id"], quote["from"], sent))
