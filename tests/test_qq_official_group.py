# -*- coding: utf-8 -*-
"""QQ 官方通道 · 群聊能不能回话 + 全量事件里的"@我"识别。

背景（2026-10-01 实测）：
- 官方机器人开了"接收所有消息"后，群里每条消息都走全量事件 GROUP_MESSAGE_CREATE，
  它的 to_me 恒为 False（只有 GROUP_AT_MESSAGE_CREATE 才是 True），
  判断"这条 @ 了我"必须看 mention 列表里哪个是机器人自己；
- 插件原来要求群先加进 enabled_groups 白名单，而默认是空列表、界面又没入口 →
  用户装完在群里怎么 @ 都没反应（私聊正常）。
"""
import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_manager():
    """按路径加载插件里的 manager.py：构建环境没有 nonebot，给它补最小替身。"""
    if "nonebot" not in sys.modules:
        nb = types.ModuleType("nonebot")
        nb.logger = logging.getLogger("nonebot")
        adapters = types.ModuleType("nonebot.adapters")
        onebot = types.ModuleType("nonebot.adapters.onebot.v11")
        onebot.MessageEvent = object
        sys.modules["nonebot"] = nb
        sys.modules["nonebot.adapters"] = adapters
        sys.modules["nonebot.adapters.onebot.v11"] = onebot
        store = types.ModuleType("nonebot_plugin_localstore")
        store.get_plugin_config_file = (
            lambda name: Path(tempfile.gettempdir()) / ("qbm_test_" + name))
        sys.modules["nonebot_plugin_localstore"] = store
    pkg = "ai_plugin_pkg"
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.__path__ = [str(AI_DIR)]
    sys.modules[pkg] = pkg_mod

    class _Cfg:
        """插件会读一堆 config 字段，这里按名字给个合理默认值。"""

        aichat_super_users = []
        aichat_proactive_groups = []

        def __getattr__(self, name):
            numeric = ("interval", "cooldown", "count", "seconds", "limit",
                       "probability", "quiet", "start", "end")
            return 30 if any(k in name for k in numeric) else []

    cfg_mod = types.ModuleType(pkg + ".config")
    cfg_mod.config = _Cfg()
    sys.modules[pkg + ".config"] = cfg_mod
    gp = _load_by_path(pkg + ".group_policy", AI_DIR / "group_policy.py")
    sys.modules[pkg + ".group_policy"] = gp
    return _load_by_path(pkg + ".manager", AI_DIR / "manager.py")


class _Seg:
    def __init__(self, type_, data=None):
        self.type = type_
        self.data = data or {}


class _FakeEvent:
    """最小官方事件替身：只需要 to_me / get_message / mentions。"""

    def __init__(self, to_me=False, segments=None, mentions=None):
        self.to_me = to_me
        self._segments = segments or []
        self.mentions = mentions or []

    def get_message(self):
        return self._segments


def test_group_chat_default_allow():
    """默认允许：群白名单为空 = 不限群（以前是"一个群都不回"）。"""
    mgr_mod = _load_manager()
    mgr = mgr_mod.ChatManager.__new__(mgr_mod.ChatManager)
    mgr._data = mgr_mod.ChatManager._get_default_config(mgr)
    assert mgr.is_group_chat_enabled() is True, "群聊回话总开关必须默认开"
    assert mgr.is_group_enabled("任何群") is True, "白名单为空时应当不限群"


def test_group_chat_switch_off_and_whitelist():
    """总开关关掉 → 哪个群都不回；白名单非空 → 只有名单里的群能回。"""
    mgr_mod = _load_manager()
    mgr = mgr_mod.ChatManager.__new__(mgr_mod.ChatManager)
    mgr._data = mgr_mod.ChatManager._get_default_config(mgr)
    mgr._save_manager_config = lambda: True          # 不落盘

    mgr.set_group_chat_enabled(False)
    assert mgr.is_group_enabled("群A") is False

    mgr.set_group_chat_enabled(True)
    mgr._data["enabled_groups"] = ["群A"]
    assert mgr.is_group_enabled("群A") is True
    assert mgr.is_group_enabled("群B") is False, "填了白名单就只听这些群"


def test_at_me_detection_for_full_mode_events():
    """全量群事件里 @ 机器人也要认出来（这些事件的 to_me 恒为 False）。"""
    policy = _load_by_path("ai_group_policy_t", AI_DIR / "group_policy.py")

    assert policy.is_at_me(_FakeEvent(to_me=True)) is True
    at_seg = _Seg("mention_user", {"user_id": "bot-openid", "is_bot": True})
    assert policy.is_at_me(_FakeEvent(segments=[at_seg])) is True
    other = _Seg("mention_user", {"user_id": "someone", "is_bot": False})
    assert policy.is_at_me(_FakeEvent(segments=[other])) is False
    assert policy.is_at_me(_FakeEvent(segments=[_Seg("text", {"text": "你好"})])) is False
    # mentions 原始列表兜底（bot=True 的只有机器人自己）
    assert policy.is_at_me(_FakeEvent(mentions=[type("M", (), {"bot": True})()])) is True
    # 事件结构跟预期不一样时不许抛，当没 @ 处理
    class _Bad:
        to_me = False

        def get_message(self):
            raise RuntimeError("boom")

    assert policy.is_at_me(_Bad()) is False


def test_group_chat_switch_roundtrip_in_client_config():
    """客户端保存的开关要能读回来（写的就是机器人侧那份 aichat_manager.json）。"""
    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_grpchat_"))
    s = Settings(tmp)
    s.ensure_dirs()
    assert ai_config.read_group_chat_enabled(s) is True, "默认必须允许群聊"
    assert ai_config.save_group_chat_enabled(s, False) is True
    assert ai_config.read_group_chat_enabled(s) is False
    assert ai_config.save_group_chat_enabled(s, True) is True
    assert ai_config.read_group_chat_enabled(s) is True


if __name__ == "__main__":
    test_group_chat_default_allow()
    test_group_chat_switch_off_and_whitelist()
    test_at_me_detection_for_full_mode_events()
    test_group_chat_switch_roundtrip_in_client_config()
    print("OK qq official group chat")
