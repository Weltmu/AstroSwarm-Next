# -*- coding: utf-8 -*-
"""群管理「禁言」三处根因修复的守卫测试（2026-10-12 老板实测报障）。

实机日志证据（本机 nonebot.log）：
- 22:19 「那就全员禁言」→ 运行时工具被判 `不支持当前通道 qq:group`（群管理能力包
  只声明 qq_official，却把本地动作抢了），机器人只能回「这边弄不了」；
- 22:17 「禁言@某人 一分钟」→ AI 看到的正文是「禁言 一分钟」（@ 段被剥掉），
  反问「禁谁啊」；
- 本地 mute_user 的 schema 参数叫 `target`，执行器却只认 `user_id`。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
CHAT = AI_DIR / "chat.py"
BRAIN = AI_DIR / "brain.py"
QQ = AI_DIR / "adapters" / "qq.py"
BRIDGE = AI_DIR / "tools_bridge.py"
POLICY_SRC = AI_DIR / "group_policy.py"


def _load_policy():
    spec = importlib.util.spec_from_file_location("policy_under_test", POLICY_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


POLICY = _load_policy()


class _Evt:
    def __init__(self, segments, self_id="3636775360", mentions=None):
        self.message = segments
        self.self_id = self_id
        if mentions is not None:
            self.mentions = mentions


def test_mention_targets_onebot_segments():
    """OneBot 的 @ 段（type=at, qq=…）：机器人自己不算，重复只算一次。"""
    evt = _Evt([
        {"type": "at", "data": {"qq": "3636775360"}},   # 机器人自己
        {"type": "text", "data": {"text": "禁言"}},
        {"type": "at", "data": {"qq": "1350821813"}},
        {"type": "text", "data": {"text": " 一分钟"}},
        {"type": "at", "data": {"qq": "1350821813"}},   # 重复
    ])
    assert POLICY.mention_targets(evt) == ["1350821813"]


def test_mention_targets_excludes_all_and_bot():
    evt = _Evt([{"type": "at", "data": {"qq": "all"}},
                {"type": "at", "data": {"qq": "3636775360"}}])
    assert POLICY.mention_targets(evt) == []


def test_mention_targets_official_mentions_list():
    class _M:
        def __init__(self, mid, is_you=False):
            self.id = mid
            self.is_you = is_you

    evt = _Evt([], self_id="", mentions=[_M("openid-1"), _M("bot-openid", True)])
    assert POLICY.mention_targets(evt, bot_id="") == ["openid-1"]


def test_mention_targets_accepts_cq_string():
    """事件里塞的是 CQ 串时（协议端形态之一）也能抠出被 @ 的号。"""
    evt = _Evt("[CQ:at,qq=1350821813] 一分钟", self_id="3636775360")
    assert POLICY.mention_targets(evt) == ["1350821813"]


def test_brain_tells_ai_who_was_mentioned():
    src = BRAIN.read_text(encoding="utf-8")
    assert "at_targets" in src, "brain 没把被 @ 的号转给 AI"
    idx = src.index("本条还 @ 了别人")
    assert "user_msg" in src[max(0, idx - 500):idx], "补的这句没拼进给 AI 的正文"


def test_qq_adapter_collects_at_targets():
    src = QQ.read_text(encoding="utf-8")
    assert "at_targets = mention_targets(event" in src
    assert '"at_targets": at_targets' in src, "extra 里没带 at_targets"
    assert "mention_targets" in src.split("from ..group_policy import", 1)[1].split("\n", 3)[0] \
        or "mention_targets," in src, "没有从 group_policy 导入 mention_targets"


def test_chat_falls_back_to_local_action_on_channel_mismatch():
    """运行时工具"通道不支持"时必须改走同名本地动作（两个执行入口都要）。"""
    src = CHAT.read_text(encoding="utf-8")
    assert src.count("except ToolUnavailableError as exc:") == 2, \
        "两个工具执行入口没都接上通道不支持的兜底"
    body = src[src.index("def _local_action_or_error"):
               src.index("async def _execute_one_tool")]
    assert "do_action" in body, "兜底函数没真正派给本地动作"
    # 兜底只对"本地也有的动作"生效；本地没有的仍如实报不支持
    assert "_plugin_action_names()" in body


def test_bridge_accepts_target_alias_for_mute():
    src = BRIDGE.read_text(encoding="utf-8")
    assert 'params.get("user_id") or params.get("target")' in src, \
        "mute_user 执行器不认 schema 里的 target 参数名"


def test_mute_picks_the_message_own_connection():
    """多协议端并存时，群管理动作必须发到"本条消息那条连接"（self_id 对上）。

    2026-10-15：挑连接的逻辑抽成了 `_pick_connection()`（撤回动作也要用同一份，
    少抄一版就少漏一坑）。所以这条锁拆成两半：
      ① 共用函数里必须有「优先挑 self_id 对上的那条」；
      ② `_act_mute` 必须真的用它，而不是自己又抄一份。
    """
    src = BRIDGE.read_text(encoding="utf-8")
    helper = src[src.index("def _pick_connection"):
                 src.index("async def _act_recall_message")]
    assert "want_self" in helper, "没按 self_id 优先挑连接"
    assert 'getattr(b, "self_id", "")' in helper

    mute = src[src.index("async def _act_mute"):src.index("async def _act_send_to")]
    assert "_pick_connection(ctx)" in mute, "_act_mute 没用共用的挑连接函数"
