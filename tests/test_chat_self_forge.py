# -*- coding: utf-8 -*-
"""聊天里"自学"（2026-10-03 老板要的）：AI 不会的事，当场给自己写个插件。

规矩（用例锁住，别改跑偏）：
1. **只有主人能触发**（群里谁都能触发 = 谁都能烧你的 token）；
2. 触发只入队、立刻返回（生成要几分钟，不能卡住这条回复）；
3. 装好/失败都要**回一句话**给触发它的那个会话；
4. "缺主程序能力"的事（跨平台发消息等）不该靠它 —— 提示词里必须写清这条边界。
"""
import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_forge_pkg"


def _ensure_pkg():
    if PKG in sys.modules:
        return
    import importlib.util
    import logging

    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg

    class _Mgr:
        enabled = True

        def is_self_forge_enabled(self):
            return self.enabled

    mgr = types.ModuleType(f"{PKG}.manager")
    mgr.chat_manager = _Mgr()
    sys.modules[f"{PKG}.manager"] = mgr


def _load_forge():
    import importlib.util

    _ensure_pkg()
    spec = importlib.util.spec_from_file_location(f"{PKG}.skill_forge",
                                                 AI_DIR / "skill_forge.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Ctx:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)
        return True


def test_only_superuser_can_trigger():
    forge = _load_forge()
    assert forge.can_trigger(True) is True
    assert forge.can_trigger(False) is False, "群里任何人都能触发会烧 token"


def test_enqueue_is_non_blocking_and_returns_hint():
    forge = _load_forge()
    ctx = _Ctx()
    out = forge.enqueue("每天早八播报天气", ctx, sender="u1", scene="private")
    assert out["ok"] is True and out["queued"] is True, out
    assert "回你一句" in out.get("note", ""), out
    assert forge.status()["pending"] == 1, forge.status()
    forge._pending.clear()


def test_empty_need_rejected():
    forge = _load_forge()
    assert forge.enqueue("   ", _Ctx())["error"] == "empty_need"


def test_disabled_switch_blocks():
    forge = _load_forge()
    mgr = sys.modules[f"{PKG}.manager"].chat_manager
    mgr.enabled = False
    try:
        out = forge.enqueue("随便什么", _Ctx())
        assert out["error"] == "self_forge_disabled", out
    finally:
        mgr.enabled = True
        forge._pending.clear()


def test_run_pending_reports_both_success_and_failure():
    """成功要和失败都要回话，且内容是人话（别只说"完成"）。"""
    forge = _load_forge()
    ctx = _Ctx()

    async def fake_learn(need):
        if "学不会" in need:
            return {"ok": False, "why": "方案没通过检查"}
        return {"ok": True, "id": "weather-8am", "name": "早八天气", "summary": "每天八点播报"}

    forge._learn = fake_learn
    forge.enqueue("每天早八播报天气", ctx)
    forge.enqueue("这件学不会的事", ctx)
    done = asyncio.run(forge.run_pending())
    assert done == 2, done
    joined = "\n".join(ctx.sent)
    assert "我去给自己写个插件" in joined, joined
    assert "学会了" in joined and "早八天气" in joined, joined
    assert "做不到" in joined and "方案没通过" in joined, joined


def test_tool_registered_and_restricted_in_description():
    """工具要真的注册进聊天工具表，而且描述里必须写明"缺主程序能力时别调它"。"""
    from qbotmanager.core.agent.runtime import AgentRuntime

    rt = AgentRuntime()
    names = [t["function"]["name"] for t in rt.schemas("qq_official")]
    assert "learn_skill" in names, names
    spec = rt.registry.get("learn_skill")
    assert "跨平台发消息" in spec.description, spec.description
    assert "做不到" in spec.description, spec.description


def test_non_superuser_gets_clear_refusal():
    from qbotmanager.core.agent.runtime import AgentRuntime
    from qbotmanager.core.agent.tool import ToolContext

    rt = AgentRuntime()
    spec = rt.registry.get("learn_skill")
    ctx = ToolContext(cfg={}, store=None, permissions=frozenset(), is_superuser=False)
    out = spec.handler(ctx, {"need": "帮我写个插件"})
    assert "not_allowed" in out, out


if __name__ == "__main__":
    test_only_superuser_can_trigger()
    test_enqueue_is_non_blocking_and_returns_hint()
    test_empty_need_rejected()
    test_disabled_switch_blocks()
    test_run_pending_reports_both_success_and_failure()
    test_tool_registered_and_restricted_in_description()
    test_non_superuser_gets_clear_refusal()
    print("OK chat self-forge")
