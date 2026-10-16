# -*- coding: utf-8 -*-
"""入群欢迎（QQ 官方 P2）：被拉进群要按人设打招呼，且不许写死话术。

规矩：
- 欢迎语一律 AI 生成；AI 不可用/内容为空 → 安静跳过，绝不发模板腔；
- 开关默认开，能在机器人侧配置里关掉；
- 走 GROUP_ADD_ROBOT 的 event_id 被动回复（不占主动消息额度）。
"""
import ast
import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_welcome_pkg"


def _stub_nonebot():
    """补一个最小的 nonebot 替身：缺什么补什么（pytest 里多个测试文件会共用 sys.modules）。"""
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    for name, value in (("get_bots", lambda: {}), ("get_driver", lambda: types.SimpleNamespace()),
                        ("on_notice", lambda **k: types.SimpleNamespace(handle=lambda f: f)),
                        ("on_message", lambda **k: types.SimpleNamespace(handle=lambda f: f)),
                        ("require", lambda *a, **k: None),
                        ("get_plugin_config", lambda *a, **k: None)):
        if not hasattr(nb, name):
            setattr(nb, name, value)
    if not hasattr(nb, "FinishedException"):
        nb.FinishedException = type("FinishedException", (Exception,), {})
    store = types.ModuleType("nonebot_plugin_localstore")
    store.get_plugin_config_file = lambda n: Path(tempfile.gettempdir()) / ("qbm_w_" + n)
    sys.modules["nonebot_plugin_localstore"] = store


def _ensure_pkg():
    """把插件目录注册成一个包，这样 group_welcome.py 里的相对导入能解析。

    platform 用真身（无重依赖）；manager 用替身（真身要 pydantic-settings，构建环境没有）。
    """
    if PKG in sys.modules:
        return
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(f"{PKG}.platform", AI_DIR / "platform.py")
    plat = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.platform"] = plat
    spec.loader.exec_module(plat)

    class _Mgr:
        enabled = True

        def is_group_welcome_enabled(self):
            return self.enabled

    mgr_mod = types.ModuleType(f"{PKG}.manager")
    mgr_mod.chat_manager = _Mgr()
    mgr_mod.ChatManager = type("ChatManager", (), {})
    sys.modules[f"{PKG}.manager"] = mgr_mod


def _load(name: str, filename: str):
    _stub_nonebot()
    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG}.{filename[:-3]}", AI_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send(self, event, message, **kwargs):
        self.sent.append((getattr(event, "group_openid", ""), message))


class _FakeEvent:
    group_openid = "G-OPENID-1"


def test_welcome_prompt_is_ai_driven():
    gw = _load("ai_welcome_t1", "group_welcome.py")
    assert "系统提示" in gw.WELCOME_PROMPT
    assert "人设" in gw.WELCOME_PROMPT
    # 提示词里不许出现写死的欢迎语（模板腔）
    for banned in ("欢迎加入", "大家好，我是", "很高兴认识"):
        assert banned not in gw.WELCOME_PROMPT, banned


def test_welcome_sends_ai_text():
    gw = _load("ai_welcome_t2", "group_welcome.py")
    import asyncio

    bot = _FakeBot()
    event = _FakeEvent()

    async def fake_reply():
        return "诶？新群啊，我是李清菡，会聊天也会管群。"

    async def run():
        msg, ctx = await gw.build_welcome_context(None, bot, event)
        assert msg.extra.get("group_welcome") is True
        assert msg.room_id == "G-OPENID-1"
        ok = await gw.handle_group_add(event, ctx, fake_reply)
        return ok

    ok = asyncio.run(run())
    assert ok is True
    assert bot.sent and bot.sent[0][0] == "G-OPENID-1"
    assert "李清菡" in bot.sent[0][1]


def test_welcome_stays_quiet_when_ai_fails():
    gw = _load("ai_welcome_t3", "group_welcome.py")
    import asyncio

    bot = _FakeBot()

    async def boom():
        raise RuntimeError("模型超时")

    async def empty():
        return "   "

    async def run():
        _, ctx = await gw.build_welcome_context(None, bot, _FakeEvent())
        first = await gw.handle_group_add(_FakeEvent(), ctx, boom)
        second = await gw.handle_group_add(_FakeEvent(), ctx, empty)
        return first, second

    first, second = asyncio.run(run())
    assert (first, second) == (False, False)
    assert bot.sent == [], "AI 失败时绝不许发兜底话术"


def test_welcome_switch_off():
    gw = _load("ai_welcome_t4", "group_welcome.py")
    import asyncio

    calls = []
    mgr = sys.modules[f"{PKG}.manager"].chat_manager   # 延迟导入，所以改这里才生效
    mgr.enabled = False
    bot = _FakeBot()

    async def reply():
        calls.append(1)
        return "hi"

    async def run():
        _, ctx = await gw.build_welcome_context(None, bot, _FakeEvent())
        return await gw.handle_group_add(_FakeEvent(), ctx, reply)

    try:
        assert asyncio.run(run()) is False
        assert calls == [] and bot.sent == [], "关了开关就不该生成也不该发"
    finally:
        mgr.enabled = True


def test_should_welcome_only_for_group_add():
    gw = _load("ai_welcome_t5", "group_welcome.py")

    class _Msg:
        def __init__(self, extra, scene):
            self.extra = extra
            self.scene = scene

    assert gw.should_welcome(_Msg({"group_welcome": True}, "group")) is True
    assert gw.should_welcome(_Msg({}, "group")) is False
    assert gw.should_welcome(_Msg({"group_welcome": True}, "private")) is False


def test_plugin_registers_welcome_matcher():
    """插件入口里必须真的注册了入群欢迎（否则前面的逻辑再对也不会被调用）。"""
    src = (AI_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    text = src
    assert "GroupAddRobotEvent" in text
    assert "on_notice" in text
    assert "handle_group_add" in text
    assert "build_welcome_context" in text
    assert tree is not None


def test_manager_switch_default_on():
    """真身 manager 的开关默认开（用包里的替身会失真，这里直接读源码断言默认值）。"""
    src = (AI_DIR / "manager.py").read_text(encoding="utf-8")
    assert 'self._data.get("group_welcome_enabled", True)' in src
    assert 'self._data["group_welcome_enabled"] = bool(enabled)' in src


if __name__ == "__main__":
    test_welcome_prompt_is_ai_driven()
    test_welcome_sends_ai_text()
    test_welcome_stays_quiet_when_ai_fails()
    test_welcome_switch_off()
    test_should_welcome_only_for_group_add()
    test_plugin_registers_welcome_matcher()
    test_manager_switch_default_on()
    print("OK group welcome")
