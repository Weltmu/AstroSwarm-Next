# -*- coding: utf-8 -*-
"""飞书通道适配器（G4）：事件要能转成平台中立模型，发不出去要如实回报。

注意：这里只测"转换与降级"逻辑，不连真飞书（没有企业应用凭证，联调要老板在
飞书开放平台建好应用后填凭证才能做）。
"""
import asyncio
import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_feishu_pkg"


def _ensure_pkg():
    if PKG in sys.modules:
        return
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")

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

    mgr = types.ModuleType(f"{PKG}.manager")
    mgr.chat_manager = _Mgr()
    sys.modules[f"{PKG}.manager"] = mgr

    img = types.ModuleType(f"{PKG}.image2txt")

    async def _recognize(url):
        return "（图片）"

    img.recognize_image = _recognize
    sys.modules[f"{PKG}.image2txt"] = img

    root = types.ModuleType(f"{PKG}.send2root")

    async def _send(err, event=None):
        return None

    root.send_error_to_super_users = _send
    sys.modules[f"{PKG}.send2root"] = root


def _load_feishu():
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG}.adapters.feishu",
                                                 AI_DIR / "adapters" / "feishu.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Seg:
    def __init__(self, type_, **data):
        self.type = type_
        self.data = data


class _Msg:
    def __init__(self, segs, text=""):
        self._segs = segs
        self._text = text

    def __iter__(self):
        return iter(self._segs)

    def __str__(self):
        return self._text


class _SenderId:
    open_id = "ou_feishu_user_1"


class _Sender:
    sender_id = _SenderId()


class _EventMsg:
    """`event.event.message` —— 飞书真正的会话信息在这儿（chat_id / chat_type）。"""

    def __init__(self, chat_id="", chat_type=""):
        self.chat_id = chat_id
        self.chat_type = chat_type


class _EventInner:
    def __init__(self, chat_id="", chat_type=""):
        self.sender = _Sender()
        self.message = _EventMsg(chat_id, chat_type)


class _FakeEvent:
    """假飞书事件。

    ⚠ 真事件上**没有 `group_id` 属性** —— 会话 id 在 `event.event.message.chat_id`
    （群以 `oc_` 开头，私聊是用户 open_id）。以前适配器读 `group_id`，取到空串
    于是"群里每条消息都当私聊"。group_id 参数保留给老的桩用法。
    """

    def __init__(self, group_id="", text="你好", segs=None, chat_id=None,
                 chat_type=""):
        self.group_id = group_id
        self.event = _EventInner(chat_id if chat_id is not None else group_id,
                                 chat_type)
        self._msg = _Msg(segs or [], text)

    def get_message(self):
        return self._msg

    def get_user_id(self):
        return "ou_feishu_user_1"

    def get_plaintext(self):
        return str(self._msg)


class _FakeBot:
    """假机器人。

    ⚠ `self_info` 是 **open_id** 字段（飞书 BotInfo 就这么叫，没有 `id`）——
    以前适配器只读 `id`，取到空串 → 群里 @ 机器人认不出来（2026-10-15 修）。
    这里照真实字段建，改回去这个测试就会红。
    """

    def __init__(self, open_id="bot"):
        self.sent = []
        self.self_info = types.SimpleNamespace(open_id=open_id)
        self.self_id = open_id

    async def send(self, event, message, **kwargs):
        self.sent.append(str(message))


def test_feishu_group_message_becomes_channel_message():
    fs = _load_feishu()
    bot = _FakeBot()
    msg, ctx = asyncio.run(fs.build_channel_from_feishu(None, bot, _FakeEvent("oc_group_1", "在吗")))
    assert msg.platform == "feishu"
    assert msg.scene == "group"
    assert msg.room_id == "oc_group_1"
    assert msg.user_id == "ou_feishu_user_1"
    assert "在吗" in msg.text
    assert ctx.supports("image"), "飞书要能发图片（先上传拿 image_key）"

    async def run():
        await ctx.send_text("好的")

    asyncio.run(run())
    assert bot.sent == ["好的"]


def test_feishu_group_detected_from_chat_id_only():
    """**只有 chat_id、没有 group_id** 时也必须认成群聊（真事件就长这样）。

    改前：`getattr(event, "group_id", "")` 恒空 → 群里每条消息都被当成私聊 →
    机器人不看 @ 门禁、对每条群消息都回，还按发言成员各拆一份上下文。
    """
    fs = _load_feishu()
    ev = _FakeEvent("", "在吗", chat_id="oc_real_group", chat_type="group")
    assert not hasattr(ev, "group_id") or ev.group_id == ""
    msg, _ctx = asyncio.run(fs.build_channel_from_feishu(None, _FakeBot(), ev))
    assert msg.scene == "group", "有 chat_id（oc_ 开头）就该判成群聊"
    assert msg.room_id == "oc_real_group"

    # 私聊：chat_id 本身就是对方的 open_id（真事件如此），room 用它
    pv = _FakeEvent("", "悄悄问", chat_id="ou_feishu_user_1", chat_type="p2p")
    msg2, _ctx2 = asyncio.run(fs.build_channel_from_feishu(None, _FakeBot(), pv))
    assert msg2.scene == "private" and msg2.room_id == "ou_feishu_user_1"


def test_feishu_private_message_scene_and_at_detection():
    fs = _load_feishu()
    msg, _ = asyncio.run(fs.build_channel_from_feishu(
        None, _FakeBot(), _FakeEvent("", "悄悄问一句")))
    assert msg.scene == "private"
    assert msg.room_id == "ou_feishu_user_1"

    at_event = _FakeEvent("oc_g2", "@机器人 帮我看看", segs=[_Seg("at", user_id="bot")])
    msg2, _ = asyncio.run(fs.build_channel_from_feishu(None, _FakeBot(), at_event))
    assert msg2.is_at_me is True, "@ 了机器人要认出来"


def test_feishu_media_degrades_honestly():
    fs = _load_feishu()
    msg, ctx = asyncio.run(fs.build_channel_from_feishu(None, _FakeBot(), _FakeEvent("g", "x")))
    assert ctx.supports("file") is False and ctx.supports("voice") is False, "没实现就别装作支持"
    # 不存在的本地文件 → 返回 False，不抛
    ok = asyncio.run(ctx.send_media("image", "/no/such/pic.png"))
    assert ok is False


def test_feishu_dispatch_registered_in_adapters():
    """分发入口要认飞书事件（否则消息进来了没人处理）。"""
    src = (AI_DIR / "adapters" / "__init__.py").read_text(encoding="utf-8")
    assert "nonebot.adapters.feishu" in src
    assert 'return "feishu"' in src
    assert "build_channel_from_feishu" in src


def test_feishu_is_parallel_channel_in_bot_py():
    """bot.py：飞书是并行通道，不能挤掉 QQ 的适配器注册。"""
    src = (ROOT / "src" / "qbotmanager" / "core" / "bot.py").read_text(encoding="utf-8")
    assert "def _feishu_ready" in src
    assert "FEISHU_BOTS=" in src
    assert "nonebot.adapters.feishu" in src
    assert "def ensure_feishu_deps" in src
    assert "def feishu_adapter_installed" in src
    assert "ensure_feishu_deps(settings, log=on_line)" in src


def test_feishu_page_has_install_button():
    src = (ROOT / "src" / "qbotmanager" / "ui" / "pages" / "feishu_page.py").read_text(
        encoding="utf-8")
    assert "装适配器" in src
    assert "ensure_feishu_deps" in src
    assert "q.qq.com" not in src  # 别把 QQ 的指引贴到飞书页
    assert "open.feishu.cn" in src


if __name__ == "__main__":
    test_feishu_group_message_becomes_channel_message()
    test_feishu_private_message_scene_and_at_detection()
    test_feishu_media_degrades_honestly()
    test_feishu_dispatch_registered_in_adapters()
    test_feishu_is_parallel_channel_in_bot_py()
    test_feishu_page_has_install_button()
    print("OK feishu channel")
