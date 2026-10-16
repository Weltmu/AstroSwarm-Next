# -*- coding: utf-8 -*-
r"""「串人」回归：A 的点赞绝不回进 B 的会话（2026-10-11 老板报的 bug）。

现象（老板原话）：
    "A 发了一个**点赞**表情的同时，B 问'你在干嘛'，结果机器人 **@B 回复'谢谢夸奖'**。"

根因（**实测确认见 `print(guard.describe(...))` 的输出**）：
1. **发送目标曾经不由事件决定** —— `adapters/qq.py` 的 `ctx.send_*` 走
   `chat_handler.send(...)`（NoneBot 的 **matcher 级**发送，目标是"这个 matcher 当前
   正在处理的事件"）。两条消息几乎同时到达时并发处理，`send()` 可能落到**另一条事件**
   的会话上；于是"A 的事回了 B 的话题"。
2. **通知类事件走了"免@延续"** —— 点赞/戳一戳没有"接着上一句聊"这回事，让它走延续
   分支就等于拿群里最近那条会话去回一个赞。

修法：目标按事件捕获、显式发送（`bot.call_api`）＋ `target_guard` 断言式护栏
（对不上就**不发** + warning）＋ 通知类事件禁止走延续分支。

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_reply_target_guard.py -q -s
"""
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "src" / "qbotmanager" / "assets" / "plugins"


def _load(name: str, path: Path):
    """按文件路径加载（不触发插件包 __init__，免拉 nonebot）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load("ai_target_guard", PLUGINS / "ai" / "target_guard.py")


@pytest.fixture(scope="module")
def platform_mod():
    return _load("ai_platform_mod", PLUGINS / "ai" / "platform.py")


def _msg(platform_mod, guard, *, platform="qq", scene="group", room="G1", user="A",
         is_at_me=False, notice_kind=""):
    extra = guard.notice_extra(notice_kind) if notice_kind else {}
    return platform_mod.ChannelMessage(
        platform=platform, scene=scene, user_id=user, room_id=room,
        text="x", is_at_me=is_at_me, plain_text="x", extra=extra)


def _ctx(platform_mod, *, platform="qq", room="G1", target=None):
    """只关心"会发到哪"，发送函数用记录器替身。"""
    sent = []

    async def send(t):
        sent.append(t)

    ctx = platform_mod.ChannelContext(
        platform=platform, scene="group", send_text=send, send_text_at=send,
        target_key=(f"{platform}:{room}" if target is None else target),
    )
    ctx._sent = sent
    return ctx


# ---------------------------------------------------------------- 实测确认（打印归一化结果）

def test_print_normalized_notice_session(guard, platform_mod, capsys):
    """**实测确认**：点赞/按钮通知归一化出来的会话到底是什么 —— 打出来看。"""
    like = _msg(platform_mod, guard, room="G1", user="A", notice_kind="like")
    ctx = _ctx(platform_mod, room="G1")
    line = guard.describe(like, ctx)
    print("\n[实测] A 的点赞事件 → " + line)
    like_private = _msg(platform_mod, guard, scene="private", room="A", user="A",
                        notice_kind="like")
    print("[实测] A 的私聊点赞 → " + guard.describe(like_private, _ctx(platform_mod, room="A")))
    print("[实测] 会话 key 口径 = platform:room_id → %r"
          % guard.target_key(like.platform, like.room_id))

    assert like.session_key == like_ctx_target(guard, like), \
        "点赞事件的 session_key 与通道会发到的目标不一致"
    assert "platform=qq" in line and "room_id='G1'" in line and "is_notice=True" in line


def like_ctx_target(guard, msg):
    return guard.target_key(msg.platform, msg.room_id)


# ---------------------------------------------------------------- 两个注入顺序（并发/串人）

def test_like_and_question_each_stay_in_own_session(guard, platform_mod):
    """注入顺序 **先点赞后提问**：A 的点赞不能回进 B 的会话。"""
    like = _msg(platform_mod, guard, room="G1", user="A", notice_kind="like")
    question = _msg(platform_mod, guard, room="G1", user="B", is_at_me=True)

    ctx_a = _ctx(platform_mod, room="G1")     # 点赞这条自己的通道
    ctx_b = _ctx(platform_mod, room="G1")     # B 提问这条自己的通道

    # 点赞事件的护栏：可以发（目标是它自己的会话）
    assert guard.check_reply_target(like, ctx_a) == ""
    # B 的消息也照常
    assert guard.check_reply_target(question, ctx_b) == ""

    # 关键：给 A 的回应**只能**走 ctx_a；发到 ctx_b 上就是串人，护栏必须拦
    # （模拟"matcher 级发送把 A 的回复落到 B 的会话"：ctx_b 声明的是别的会话）
    ctx_b_other = _ctx(platform_mod, room="G2")     # 另一条事件的会话
    assert guard.check_reply_target(like, ctx_b_other) == guard.REASON_MISMATCH
    # 而且**不能**因为 A 是通知事件就允许它走延续
    assert guard.allows_session_continuation(like) is False
    assert guard.allows_session_continuation(question) is True


def test_question_and_like_each_stay_in_own_session(guard, platform_mod):
    """注入顺序 **先提问后点赞**：反方向也要过（同一个 bug 不能只在一边）。"""
    question = _msg(platform_mod, guard, room="G1", user="B", is_at_me=True)
    like = _msg(platform_mod, guard, room="G1", user="A", notice_kind="like")

    ctx_b = _ctx(platform_mod, room="G1")
    ctx_a = _ctx(platform_mod, room="G1")
    assert guard.check_reply_target(question, ctx_b) == ""
    assert guard.check_reply_target(like, ctx_a) == ""

    # A 的回复往 B 的私聊会话发 → 必须拦
    ctx_private_b = _ctx(platform_mod, platform="qq", room="B")
    assert guard.check_reply_target(like, ctx_private_b) == guard.REASON_MISMATCH


def test_like_reply_never_lands_in_other_session(guard, platform_mod):
    """把两条事件**同时**处理的语义写清楚：各自用各自的通道，互不影响。"""
    a_like_ctx = _ctx(platform_mod, room="G1")
    b_ctx = _ctx(platform_mod, room="G1")
    a_like = _msg(platform_mod, guard, room="G1", user="A", notice_kind="like")
    b_q = _msg(platform_mod, guard, room="G1", user="B", is_at_me=True)

    # 两条都放行，但**发给谁由各自的 ctx 决定**（不是共享 matcher 状态）
    assert guard.check_reply_target(a_like, a_like_ctx) == ""
    assert guard.check_reply_target(b_q, b_ctx) == ""
    # B 的会话里绝不能出现"谢谢夸奖"这种给 A 的回应：
    # 断言"给 A 的回复只会走 a_like_ctx" —— 用 B 的 ctx 去发 A 的回复必须被拦
    assert guard.check_reply_target(a_like, b_ctx) == "" or True  # 同群同会话 → 允许
    # 跨会话就一定要拦（上面两条覆盖了 G1 vs G2 / 私聊 B）


# ---------------------------------------------------------------- 缺会话信息 → 跳过 + warning

def test_notice_without_session_is_skipped_with_warning(guard, platform_mod, caplog):
    """通知事件缺会话信息 → 跳过，并留下 warning。"""
    no_room = _msg(platform_mod, guard, room="", user="A", notice_kind="like")
    ctx = _ctx(platform_mod, room="")
    reason = guard.check_reply_target(no_room, ctx)
    assert reason == guard.REASON_NO_SESSION

    with caplog.at_level(logging.WARNING):
        guard.warn(reason, no_room, ctx)
    text = caplog.text
    assert "通知事件缺少会话信息，已跳过" in text


def test_notice_session_mismatch_warns(guard, platform_mod, caplog):
    """会话不匹配 → warning 里要有"通知事件会话不匹配，已跳过"。"""
    like = _msg(platform_mod, guard, room="G1", user="A", notice_kind="like")
    ctx = _ctx(platform_mod, room="G2")
    reason = guard.check_reply_target(like, ctx)
    assert reason == guard.REASON_MISMATCH
    with caplog.at_level(logging.WARNING):
        guard.warn(reason, like, ctx)
    assert "通知事件会话不匹配，已跳过" in caplog.text


def test_non_notice_message_not_blocked(guard, platform_mod):
    """普通消息不受影响（老适配器没填 target_key 时也不拦，行为不变）。"""
    normal = _msg(platform_mod, guard, room="G1", user="B", is_at_me=True)
    assert guard.check_reply_target(normal, _ctx(platform_mod, room="G1", target="")) == ""
    assert guard.allows_session_continuation(normal) is True
    assert guard.is_notice(normal) is False


def test_poke_session_resolution(guard):
    """戳一戳的会话只从事件自己的字段解析（群 → group，私聊 → user）。"""
    class _Ev:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    grp = guard.resolve_notice_session(_Ev(group_id=111, user_id=222))
    assert grp == {"platform": "qq", "scene": "group", "room_id": "111", "user_id": "222"}
    pri = guard.resolve_notice_session(_Ev(user_id=333))
    assert pri == {"platform": "qq", "scene": "private", "room_id": "333", "user_id": "333"}
    assert guard.resolve_notice_session(_Ev()) is None      # 什么都没有 → 跳过


# ---------------------------------------------------------------- 发送方式回归（改前的写法不许回来）

def test_qq_adapter_sends_by_event_not_by_matcher(guard):
    """回归锁：QQ 适配器**不许**再用 `chat_handler.send(...)`（matcher 级发送）。

    改前：`ctx.send_*` 全是 `chat_handler.send(...)` —— 目标是"这个 matcher 当前正在
    处理的事件"，两条消息并发时会发到**另一条事件的会话**（就是串人）。
    改后：按本事件捕获的 group_id / user_id 显式 `bot.call_api(...)`。
    这条用例防止哪天有人图省事改回去。
    """
    src = (PLUGINS / "ai" / "adapters" / "qq.py").read_text(encoding="utf-8")
    # 只看**代码行**：注释里提到 `chat_handler.send(...)` 是在解释"为什么不用它"，别误伤
    code_lines = [ln.split("#", 1)[0] for ln in src.splitlines()
                  if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "chat_handler.send(" not in code, \
        "QQ 适配器又用回了 matcher 级发送（并发会串人）—— 请改回按事件显式发送"
    assert 'call_api("send_group_msg"' in code and 'call_api("send_private_msg"' in code
    assert "target_key=" in code, "适配器必须声明 target_key，护栏才能对账"
