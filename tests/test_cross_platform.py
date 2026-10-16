# -*- coding: utf-8 -*-
"""跨平台互通（2026-10-04 老板点名要的整包）。

要求原话：
1. "不只是跨平台传输文件（所有类型的文件），要把他整合一个跨平台功能"；
2. "基础的发消息发文件"；
3. "机器人会跨平台主动聊天"；
4. "**注意区分好用户身份**，不要把发给 QQ 上其他人的消息也通过微信发给用户"。

第 4 条是这套东西的命门，所以用例重点锁"身份隔离"：
- 投递目标只能来自**这次对话主体自己绑定的身份**；
- 没绑定 → 如实拒绝（not_bound），绝不猜、绝不退回"上次聊过的人"；
- 平台能力不同要如实说（微信只能发文本，QQ 全类型）。
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
PKG = "ai_cross_pkg"


def _ensure_pkg():
    if PKG in sys.modules:
        return
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")

    store = types.ModuleType("nonebot_plugin_localstore")
    tmp = Path(tempfile.mkdtemp(prefix="qbm_cross_store_"))
    store.get_plugin_config_file = lambda name: tmp / f"{name}.json"
    store.get_plugin_data_file = lambda name: tmp / f"{name}.json"
    sys.modules["nonebot_plugin_localstore"] = store

    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg
    for name in ("platform", "identity", "cross_platform"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", AI_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.{name}"] = mod
        spec.loader.exec_module(mod)


def _cross():
    _ensure_pkg()
    return sys.modules[f"{PKG}.cross_platform"]


def _identity():
    _ensure_pkg()
    return sys.modules[f"{PKG}.identity"]


def _fresh(owner: str, platform: str, target_id: str):
    """清干净后绑一组身份（避免用例之间串味）。"""
    ident = _identity()
    ident._groups.clear()
    ident._index.clear()
    ident.bind(owner, f"{platform}:{target_id}")


def test_resolve_only_uses_bound_identity():
    cross = _cross()
    _fresh("wechat:wx1", "qq_official", "openid1")
    got = cross.resolve_target("wechat:wx1", "qq_official")
    assert got["ok"] and got["target"] == "openid1", got
    # 另一个没绑定的人：必须拒绝（这就是"别把 A 的消息发给 B"）
    miss = cross.resolve_target("wechat:wx404", "qq_official")
    assert miss["ok"] is False and miss["error"] == "not_bound", miss


def test_no_cross_user_leak_even_if_other_user_is_bound():
    """A 在微信、B 在 QQ：A 要发给"我的 QQ"，绝不能落到 B 的 openid 上。"""
    cross = _cross()
    ident = _identity()
    ident._groups.clear()
    ident._index.clear()
    ident.bind("wechat:wxA", "qq_official:openidA")
    ident.bind("wechat:wxB", "qq_official:openidB")

    a = cross.resolve_target("wechat:wxA", "qq_official")
    b = cross.resolve_target("wechat:wxB", "qq_official")
    assert a["target"] == "openidA", a
    assert b["target"] == "openidB", b
    assert a["target"] != b["target"]


def test_platform_capabilities_are_honest():
    cross = _cross()
    assert cross.supports("qq_official", "file") is True
    assert cross.supports("qq", "video") is True
    assert cross.supports("wechat", "file") is False, "微信适配器只有 send_text"
    assert cross.supports("wechat", "image") is False


def test_media_kind_validated_before_sending():
    cross = _cross()
    _fresh("wechat:wx7", "qq_official", "openid7")
    out = asyncio.run(cross.deliver_media("wechat:wx7", "qq_official", "不存在的类型", "/tmp/x"))
    assert out["error"] == "bad_kind", out


def test_wechat_media_refused_with_reason():
    cross = _cross()
    _fresh("qq_official:openid2", "wechat", "wx2")
    out = asyncio.run(cross.deliver_media("qq_official:openid2", "wechat", "file", "/tmp/x"))
    assert out["ok"] is False and out["error"] == "platform_cannot", out


def test_unbound_media_refused():
    cross = _cross()
    _fresh("wechat:wx3", "qq_official", "openid3")
    out = asyncio.run(cross.deliver_media("wechat:wx303", "qq_official", "image", "/tmp/x.png"))
    assert out["ok"] is False and out["error"] == "not_bound", out


def test_offline_platform_reports_clearly():
    """通道没跑时要说"没在跑"，不能假装发成功。"""
    cross = _cross()
    _fresh("wechat:wx4", "qq_official", "openid4")
    out = asyncio.run(cross.deliver_text("wechat:wx4", "qq_official", "你好"))
    assert out["ok"] is False and out["error"] in ("platform_offline", "runtime_unavailable"), out


def test_source_unreadable_is_honest():
    """文件读不到（本地不存在、也不是链接）→ 如实报错，不要静默成功。"""
    cross = _cross()
    _fresh("wechat:wx5", "qq_official", "openid5")
    out = asyncio.run(cross._send("qq_official:openid5", "qq_official", "openid5",
                                  "file", "/definitely/not/here.bin"))
    # 没有可用的 bot → platform_offline；测试环境没有 nonebot 运行时 → runtime_unavailable；
    # 只有真有 bot 时才会走到 source_unreadable —— 三种都算"如实报错，不假装成功"
    assert out["ok"] is False and out["error"] in (
        "platform_offline", "source_unreadable", "runtime_unavailable"), out


def test_describe_capabilities_is_plain_chinese():
    cross = _cross()
    _fresh("wechat:wx6", "qq_official", "openid6")
    assert "qq_official:openid6" in _identity().resolve("wechat:wx6"), "绑定没生效"
    text = cross.describe_capabilities("wechat:wx6")
    assert "可以替他发到" in text and "QQ 官方机器人" in text, text
    unbound = cross.describe_capabilities("wechat:wxZ")
    assert "绑定" in unbound, unbound


def test_builtin_tools_registered():
    """跨平台互通是**内置基础功能**（老板 2026-10-04 明确不做成插件）：
    三个工具必须直接出现在聊天工具表里，不依赖任何能力包。"""
    from qbotmanager.core.agent.runtime import AgentRuntime

    rt = AgentRuntime()
    names = [t["function"]["name"] for t in rt.schemas("qq_official")]
    for tool in ("send_to_my_platform", "send_file_to_my_platform", "my_platforms"):
        assert tool in names, f"{tool} 没进内置工具表：{names}"
    spec = rt.registry.get("send_file_to_my_platform")
    assert "另一个平台" in spec.description, spec.description


def test_builtin_tool_enqueues_and_reports_back():
    """工具只入队（不阻塞回话），发完由 run_pending 回一句人话。"""
    from qbotmanager.core.agent.tool import ToolContext
    from qbotmanager.core.agent.runtime import AgentRuntime

    cross = _cross()
    # 测试环境没有 nonebot：runtime 的三个候选路径（src.plugins / plugins /
    # qbotmanager.assets）都导不进来。把按路径加载好的模块**注册成机器人里
    # 那个模块名**，模拟真实机器人环境（runtime.py 2026-10-12 起优先用
    # "机器人已加载的插件模块"）。
    sys.modules["src.plugins.ai.cross_platform"] = cross
    cross._pending.clear()
    rt = AgentRuntime()
    spec = rt.registry.get("send_to_my_platform")
    ctx = ToolContext(cfg={}, store=None, permissions=frozenset(),
                      platform="wechat", user_id="wxT", is_superuser=False)
    ctx.extra["user_key"] = "wechat:wxT"
    out = spec.handler(ctx, {"platform": "qq_official", "text": "你好"})
    assert '"queued": true' in out or '"queued":true' in out, out
    assert cross.status()["pending"] == 1, cross.status()

    _fresh("wechat:wxT", "qq_official", "openidT")
    sent = []

    async def fake_reply(text):
        sent.append(text)

    asyncio.run(cross.run_pending(send=fake_reply))
    assert sent, "发完必须回一句话"
    assert "没发成功" in sent[0] or "已经发到" in sent[0], sent


def test_action_table_has_cross_platform_actions():
    """三个动作要在动作表里，且发文件/发消息都受 send_message 权限管。"""
    src = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    for action in ("send_to_platform", "send_file_to_platform", "my_platforms"):
        assert f'"{action}"' in src, action
    assert '"send_file_to_platform": "send_message"' in src
    assert "身份绑定" in src or "identity" in src


if __name__ == "__main__":
    test_resolve_only_uses_bound_identity()
    test_no_cross_user_leak_even_if_other_user_is_bound()
    test_platform_capabilities_are_honest()
    test_media_kind_validated_before_sending()
    test_wechat_media_refused_with_reason()
    test_unbound_media_refused()
    test_offline_platform_reports_clearly()
    test_source_unreadable_is_honest()
    test_describe_capabilities_is_plain_chinese()
    test_action_table_has_cross_platform_actions()
    print("OK cross platform")
