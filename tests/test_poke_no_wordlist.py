# -*- coding: utf-8 -*-
"""戳一戳必须是「AI 按人设自主回复」，不许再退回词库。

背景：`assets/plugins/ai/chat.py` 里曾有一份 8 句的 `FALLBACK_POKE_REPLIES`
（"干嘛？戳上瘾了是吧？" 这类），AI 一失败就随机挑一句发出去 —— 用户看到的就是固定套路。
这些用例守住三件事：词库没了、失败就不发、poke.py 不会把空串发出去。

（这里用源码级断言而不是 import：测试环境没有 openai / nonebot_plugin_localstore，
 插件模块导入不起来；服务端那份已实测过。）
"""
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
CHAT = PLUGIN / "chat.py"
POKE = PLUGIN / "poke.py"


def test_no_fallback_word_list():
    src = CHAT.read_text(encoding="utf-8")
    assert "FALLBACK_POKE_REPLIES" not in src, "戳一戳的词库兜底又回来了"
    # 常见的几句「套路话」也不该出现
    for line in ("戳上瘾", "暗恋我", "做成表情包", "再戳我要收费"):
        assert line not in src, f"发现固定话术: {line}"


def test_poke_reply_returns_empty_on_failure():
    """取回复的函数里，失败路径必须是 return ""，不能 random.choice。"""
    src = CHAT.read_text(encoding="utf-8")
    start = src.index("async def get_poke_reply")
    body = src[start:start + 3000]
    assert "random.choice" not in body, "戳一戳还在随机挑固定句子"
    assert body.count('return ""') >= 2, "AI 不可用 / 两次都没生成时都应返回空串"
    assert "get_personality" in body, "必须带上人设（get_personality）"


def test_poke_handler_skips_empty_reply():
    """空回复要拦住；而且**非空判断必须在发送之前**。

    2026-10-11：发送方式从 `poke.send(...)`（NoneBot 的 **matcher 级**发送，目标是"这个
    matcher 当前正在处理的事件"，并发时会把戳一戳的回话发到别人的会话上 —— 串人）
    改成按**本事件自己的** group_id / user_id 显式 `bot.call_api(...)`。
    这条用例守的还是原来那件事：**先判非空，再发送**。
    """
    src = POKE.read_text(encoding="utf-8")
    # 只看**代码行**：注释里提到 `poke.send(...)` 是在解释"为什么不用它"，别误伤
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "if not reply" in code, "空回复没有拦住，会把空消息发出去"
    guard_idx = code.index("if not reply")
    send_positions = [code.index('bot.call_api("send_group_msg"'),
                      code.index('bot.call_api("send_private_msg"')]
    assert guard_idx < min(send_positions), "非空判断必须在发送之前"
    assert "poke.send(" not in code, \
        "又用回了 matcher 级发送（并发会串人）—— 请改回按事件显式发送"


def test_poke_session_comes_from_the_event():
    """戳一戳的会话只能从**事件自己**解析；解析不出来就**不发**（宁可不回，也别发错人）。"""
    src = POKE.read_text(encoding="utf-8")
    assert "resolve_notice_session" in src, "没有按事件解析会话"
    assert "session is None" in src, "会话解析失败时没有跳过"
    assert "getattr(event, 'group_id', 0) or 0" not in src, \
        "还在用 '群号:0' 这种兜底拼冷却 key（会话来源不是事件）"


def test_poke_prompt_forbids_canned_lines():
    src = CHAT.read_text(encoding="utf-8")
    body = src[src.index("async def get_poke_reply"):src.index("async def get_interject_reply")]
    assert "固定套话" in body, "提示词要明确禁止固定套话"
    assert re.search(r"不要提「戳一戳」", body), "提示词不该让她把「戳一戳」这个动作念出来"


def test_poke_reply_takes_who_argument():
    """AI 必须知道是谁戳的（昵称/群名片），否则回复像在对空气说话。"""
    src = CHAT.read_text(encoding="utf-8")
    assert "async def get_poke_reply(who: str = \"\")" in src, "get_poke_reply 没有 who 参数"
    body = src[src.index("async def get_poke_reply"):src.index("async def get_interject_reply")]
    assert "who_note" in body and "戳你的人是" in body, "提示词里没有把「谁戳的」告诉 AI"


def test_poke_handler_passes_sender_name():
    """poke.py 要把事件里的群名片/昵称取出来传进去。"""
    src = POKE.read_text(encoding="utf-8")
    assert "_who_poked" in src, "没有取戳人称呼的辅助函数"
    assert "get_poke_reply(_who_poked(event))" in src, "调用时没把称呼传进去"
    assert 'getattr(sender, "card"' in src and 'getattr(sender, "nickname"' in src, \
        "群名片/昵称都要试着取"


def test_poke_module_is_actually_imported():
    """poke.py 必须真的被 __init__.py 导入 —— 否则处理器不注册，戳一戳永远没反应。

    2026-10-12 实测：poke.py 从 10-11 起就躺在插件目录里，但没有任何地方 import 它；
    日志里戳一戳事件到了、没有任何 poke matcher 处理 —— 老板报「戳一戳没有回复」。
    只写模块不接线 = 功能不存在，这条用例守住接线。
    """
    src = (PLUGIN / "__init__.py").read_text(encoding="utf-8")
    assert "from . import poke" in src, "poke 模块没有被导入，戳一戳永远不会注册"
