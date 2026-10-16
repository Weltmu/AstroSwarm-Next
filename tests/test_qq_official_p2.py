# -*- coding: utf-8 -*-
"""QQ 官方 P2：按钮点击、频道 @、审核事件（2026-10-03）。

老板拍板"全力做 QQ 端"，这三样是 P2 剩下的：
- 用户点按钮 → 当成"他说了这句话"交给 AI 回话（官方只给 button_id/button_data）；
- 频道里 @ 我 → 按群聊处理（room 用频道 id）；
- 平台审核结果 → 通过记日志，**被拦要提醒管理员**（用户那边什么都看不到，
  不提醒就像"机器人突然不说话"）。
"""
import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_qqp2_pkg"


def _ensure_pkg():
    if PKG in sys.modules:
        return
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    # 适配器文件 import 了 nonebot.adapters.qq，得把包结构补齐（不然 "not a package"）
    adapters = sys.modules.get("nonebot.adapters") or types.ModuleType("nonebot.adapters")
    sys.modules["nonebot.adapters"] = adapters
    nb.adapters = adapters
    qq_mod = sys.modules.get("nonebot.adapters.qq") or types.ModuleType("nonebot.adapters.qq")

    class _Seg:
        def __init__(self, type_, data=None):
            self.type = type_
            self.data = data or {}

        def __str__(self):
            return self.type

    qq_mod.MessageSegment = type("MessageSegment", (), {
        "image": staticmethod(lambda url: _Seg("image", {"url": url})),
        "audio": staticmethod(lambda url: _Seg("audio", {"url": url})),
        "file": staticmethod(lambda url: _Seg("file", {"url": url})),
        "file_image": staticmethod(lambda data, file_name="": _Seg("file_image", {"content": data})),
        "file_file": staticmethod(lambda data, file_name="": _Seg("file_file", {"content": data})),
        "file_audio": staticmethod(lambda data, file_name="": _Seg("file_audio", {"content": data})),
    })
    qq_mod.Message = type("Message", (), {})
    sys.modules["nonebot.adapters.qq"] = qq_mod
    adapters.qq = qq_mod
    if not hasattr(nb, "FinishedException"):
        nb.FinishedException = type("FinishedException", (Exception,), {})
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg
    for name in ("platform",):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", AI_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.{name}"] = mod
        spec.loader.exec_module(mod)

    class _Mgr:
        def is_image_recognition_enabled(self):
            return False

        def is_rich_parse_enabled(self):
            return False

        def get_send_targets(self):
            return []

    mgr = types.ModuleType(f"{PKG}.manager")
    mgr.chat_manager = _Mgr()
    sys.modules[f"{PKG}.manager"] = mgr
    for name, attrs in (("image2txt", {"recognize_image": lambda *a, **k: None}),
                        ("contacts", {"remember": lambda *a, **k: None}),
                        ("send2root", {"send_error_to_super_users": lambda *a, **k: None}),
                        ("group_state", {"STATE_NAME": "s.json", "normalize_event_type": lambda x: x,
                                         "load": lambda *a: {}, "save": lambda *a: None,
                                         "record": lambda *a, **k: {}}),
                        ("rich_content", {"rich_context": lambda *a, **k: "",
                                          "describe_elements": lambda *a, **k: "",
                                          "describe_attachments": lambda *a, **k: ""}),
                        # ⚠ 桩要收 bot_id：适配器把"机器人自己的 id"一起传进去做比对
                        # （2026-10-15 起频道分支也走这两个函数）
                        ("group_policy", {"is_at_me": lambda e, **k: True,
                                          "mentions_other": lambda e, **k: False})):
        mod = types.ModuleType(f"{PKG}.{name}")
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[f"{PKG}.{name}"] = mod


def _load_adapter():
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(
        f"{PKG}.adapters.qq_official", AI_DIR / "adapters" / "qq_official.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Btn:
    button_id = "sign_in"
    button_data = "签到"


class _Interaction:
    chat_type = 1
    group_openid = "G-OPENID-9"
    user_openid = "U-OPENID-9"
    data = _Btn()

    def get_user_id(self):
        return "U-OPENID-9"


class _AuditReject:
    __type__ = "MESSAGE_AUDIT_REJECT"
    group_openid = "G-OPENID-9"
    user_openid = ""
    message_id = "m1"


class _AuditPass:
    __type__ = "MESSAGE_AUDIT_PASS"
    group_openid = ""
    user_openid = "U-OPENID-9"
    message_id = "m2"


class _GuildEvent:
    channel_id = "CH-1"
    guild_id = "GUILD-1"
    group_openid = ""           # 频道事件没有 group_openid（分发靠这一点区分）

    def get_user_id(self):
        return "U-GUILD-1"

    def get_plaintext(self):
        return "帮我看看这个"


class _Bot:
    def __init__(self):
        self.sent = []

    async def send(self, event, message, **kw):
        self.sent.append(("send", str(message)))

    async def send_to_group(self, group_openid, message, **kw):
        self.sent.append(("group", group_openid, message))

    async def send_to_c2c(self, openid, message, **kw):
        self.sent.append(("c2c", openid, message))


def test_interaction_becomes_ai_message():
    qq = _load_adapter()
    bot = _Bot()
    msg, ctx = asyncio.run(qq.build_channel_from_interaction(None, bot, _Interaction()))
    assert msg.platform == "qq_official" and msg.scene == "group"
    assert msg.room_id == "G-OPENID-9"
    assert "签到" in msg.text, msg.text
    assert msg.extra.get("interaction") is True
    assert msg.is_at_me is True

    async def run():
        return await ctx.send_text("好的，给你记上了")

    asyncio.run(run())
    # 2026-10-15：交互回复**优先走被动回复**（复用本次交互的 event_id）——
    # 以前一律 send_to_group（主动消息），点一次按钮回一句都算额度，很容易烧穿。
    assert bot.sent == [("send", "好的，给你记上了")], bot.sent


def test_interaction_falls_back_to_active_send():
    """被动回复发不出去时才退回主动发送 —— 别把回复整条丢了。"""
    qq = _load_adapter()
    bot = _Bot()

    async def boom(event, message, **kw):
        raise RuntimeError("被动窗口不可用")

    bot.send = boom
    _msg, ctx = asyncio.run(qq.build_channel_from_interaction(None, bot, _Interaction()))

    async def run():
        return await ctx.send_text("回你一句")

    asyncio.run(run())
    assert bot.sent == [("group", "G-OPENID-9", "回你一句")], bot.sent


# ================================================================ 2026-10-15 按钮修复
def test_button_fields_read_from_resolved():
    """1.7.3 的真实形状：button_id / button_data 挂在 **`event.data.resolved`** 下。

    `ButtonInteractionData` 只有 `resolved` 一个字段（models/common.py:144-155）。
    旧代码直接从 `event.data` 上 getattr → 三个全空 → AI 永远只看到"一个按钮"，
    老板在客户端给按钮配的语义全丢，日志里也查不出点了哪个。
    """
    qq = _load_adapter()

    class _Resolved:
        button_id = "check_in"
        button_data = "每日签到"

    class _Data:
        resolved = _Resolved()

    class _Ev:
        chat_type = 1
        group_openid = "G-1"
        user_openid = ""
        data = _Data()

        def get_user_id(self):
            return "U-1"

    msg, _ctx = asyncio.run(qq.build_channel_from_interaction(None, _Bot(), _Ev()))
    print("\n[resolved 形状] text=%r extra=%r" % (msg.text, msg.extra))
    assert "每日签到" in msg.text, "没从 data.resolved 里读到 button_data"
    assert msg.extra.get("button_id") == "check_in"
    assert msg.extra.get("button_data") == "每日签到"


def test_button_action_type_defaults_to_callback():
    """`action.type` 默认必须是 **1（回调）**，不是 2（指令）。

    官方取值：0=链接、1=回调（产生 INTERACTION_CREATE）、
    2=指令（只把 `@机器人 data` 填进输入框，**不产生任何事件**）。
    旧代码默认写 2、注释还标成"2 = 回传（推荐）"—— 正好写反：用户点下去
    机器人这边收不到任何回调，得自己再按一次发送。
    """
    qq = _load_adapter()
    assert qq._button_action_type({}) == 1
    assert qq._button_action_type({"action_type": None}) == 1
    assert qq._button_action_type({"action_type": 0}) == 0        # 显式链接按钮照旧
    assert qq._button_action_type({"action_type": "2"}) == 2      # 显式指令按钮照旧
    assert qq._button_action_type({"action_type": 99}) == 1       # 非法值回落到回调
    assert qq._button_action_type({"action_type": "x"}) == 1


def test_keyboard_uses_callback_and_all_users_permission():
    """源码锁：键盘的 action.type / permission / 行数上限。"""
    src = (AI_DIR / "adapters" / "qq_official.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "_button_action_type(b)" in code, "action.type 没走统一取值函数"
    assert "Action.Permission(type=2)" in code, \
        "权限不是「所有人」—— 旧代码写 0（指定用户）却没给 specify_user_ids，等于谁都点不了"
    assert "rows[:5]" in code, "缺少 5 行上限（超了会被平台整条拒收，静默降级成文字）"
    assert 'int(b.get("action_type") or 2)' not in code, "旧的写死默认 2 又回来了"


def test_interaction_without_button_data_falls_back():
    qq = _load_adapter()

    class _NoData:
        chat_type = 2
        group_openid = ""
        user_openid = "U-1"
        data = type("D", (), {"button_id": "", "button_data": ""})()

        def get_user_id(self):
            return "U-1"

    msg, ctx = asyncio.run(qq.build_channel_from_interaction(None, _Bot(), _NoData()))
    assert "一个按钮" in msg.text, msg.text
    assert msg.scene == "private"


def test_guild_at_message_is_group_scene():
    qq = _load_adapter()
    bot = _Bot()
    msg, ctx = asyncio.run(qq.build_channel_from_guild(None, bot, _GuildEvent()))
    assert msg.scene == "group"
    assert msg.room_id == "CH-1", msg.room_id
    assert msg.is_at_me is True, "频道 @-专用事件（to_me=True）要认成 @ 我"
    assert "帮我看看这个" in msg.text
    assert msg.extra.get("guild") is True
    assert msg.extra.get("at_others") is False

    async def run():
        return await ctx.send_text("收到")

    asyncio.run(run())
    assert bot.sent == [("send", "收到")], bot.sent


def test_guild_branch_uses_group_policy_not_hardcoded_at_me():
    """源码锁：频道分支不许再"无差别当 @ 我"。

    以前 scene=group、is_at_me=True 写死 —— 机器人一接收频道全量消息，
    成员之间正常互聊也会被它抢话；频道私信还被当成群聊。
    """
    src = (AI_DIR / "adapters" / "qq_official.py").read_text(encoding="utf-8")
    block = src.split("async def build_channel_from_guild", 1)[1].split("def audit_line", 1)[0]
    code = "\n".join(ln.split("#", 1)[0] for ln in block.splitlines())
    assert "is_at_me(event, bot_id=bot_ids)" in code, "要用 group_policy 判 @"
    assert "mentions_other(event, bot_id=bot_ids)" in code, "要算出 at_others"
    assert "SCENE_PRIVATE if is_dm" in code, "频道私信要按私聊处理"
    assert "is_at_me=True," not in code, "别再写死 is_at_me=True"


def test_audit_reject_is_warning_with_plain_words():
    qq = _load_adapter()
    level, text = qq.audit_line(_AuditReject())
    assert level == "warn"
    assert "拦下" in text and "看不到" in text, text
    level2, text2 = qq.audit_line(_AuditPass())
    assert level2 == "info" and "通过" in text2, text2


def test_plugin_registers_buttons_and_audit():
    src = (AI_DIR / "__init__.py").read_text(encoding="utf-8")
    for needle in ("InteractionCreateEvent", "MessageAuditRejectEvent", "MessageAuditPassEvent",
                   "build_channel(buttons", "audit_line"):
        assert needle in src, needle


def test_dispatch_routes_notice_and_guild():
    src = (AI_DIR / "adapters" / "__init__.py").read_text(encoding="utf-8")
    assert "NoticeEvent" in src
    assert "build_channel_from_interaction" in src
    assert "build_channel_from_guild" in src
    assert "InteractionCreate" in src


if __name__ == "__main__":
    test_interaction_becomes_ai_message()
    test_interaction_without_button_data_falls_back()
    test_guild_at_message_is_group_scene()
    test_audit_reject_is_warning_with_plain_words()
    test_plugin_registers_buttons_and_audit()
    test_dispatch_routes_notice_and_guild()
    print("OK qq official p2")
