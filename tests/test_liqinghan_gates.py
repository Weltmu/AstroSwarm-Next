# -*- coding: utf-8 -*-
"""李清菡的共享开关（2026-10-15 修「双框架开关不同步」）。

老板报的现象：客户端里把 AI 总开关关掉，AI 插件当场不回了，**李清菡照旧回话、
照旧往群里搭话**；日志里还看不出任何原因。根因是李清菡只读自己那份 `.env`，
而且是 import 时读一次，压根没读机器人侧的热配置快照。

这个文件锁住三件事：
1. 总开关关着 / 档案开关关着 → `blocked_reason()` 必须给人话原因；
2. 读不到 `.env`（文件没了/读不动）→ **fail-open**（插件既然起来了就别静默停机）；
3. 出站动作那条唯一出口（DispatchChannel.action）必须过闸 —— 它是兜底，
   上面哪条路漏判都不能漏出去。

跑法：
    .\\.test_venv\\Scripts\\python.exe -m pytest tests/test_liqinghan_gates.py -q
"""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ASSETS_PLUGINS = ROOT / "src" / "qbotmanager" / "assets" / "plugins"
LQH = ASSETS_PLUGINS / "liqinghan"

_MOD = {}


def _load(name: str, file_name: str):
    """按文件路径加载插件模块（单测不触发包 __init__，避免 NoneBot 依赖）。"""
    if name in _MOD:
        return _MOD[name]
    spec = importlib.util.spec_from_file_location(name, LQH / file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _MOD[name] = mod
    return mod


def gates():
    return _load("lqh_gates", "gates.py")


def _fake_hotconfig(disabled):
    mod = types.ModuleType("fake_hotconfig")
    mod.ai_disabled = lambda: bool(disabled)
    return mod


def _env(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


# ================================================================ 开关判定
def test_enabled_by_default_when_env_says_one(tmp_path, monkeypatch):
    g = gates()
    monkeypatch.setattr(g, "_hotconfig", lambda: _fake_hotconfig(False))
    monkeypatch.setattr(g, "env_path", lambda: _env(tmp_path, "QBM_AGENT_PROFILE_ENABLED=1\n"))
    monkeypatch.delenv("QBM_AGENT_PROFILE_ENABLED", raising=False)
    assert g.profile_enabled() is True
    assert g.blocked_reason() == "", "开关都开着还拦人，等于把机器人整哑"


def test_ai_master_switch_blocks(tmp_path, monkeypatch):
    """AI 总开关关掉 → 李清菡必须停（以前它压根不读这个开关）。"""
    g = gates()
    monkeypatch.setattr(g, "_hotconfig", lambda: _fake_hotconfig(True))
    monkeypatch.setattr(g, "env_path", lambda: _env(tmp_path, "QBM_AGENT_PROFILE_ENABLED=1\n"))
    monkeypatch.delenv("QBM_AGENT_PROFILE_ENABLED", raising=False)
    reason = g.blocked_reason()
    assert "AI 总开关" in reason, reason


def test_profile_switch_off_blocks(tmp_path, monkeypatch):
    """档案开关关掉（.env 里键没了 / 不是 1）→ 停，不用等重启。"""
    g = gates()
    monkeypatch.setattr(g, "_hotconfig", lambda: _fake_hotconfig(False))
    monkeypatch.delenv("QBM_AGENT_PROFILE_ENABLED", raising=False)
    monkeypatch.setattr(g, "env_path", lambda: _env(tmp_path, "OTHER=1\n"))
    assert "智能体档案" in g.blocked_reason()
    monkeypatch.setattr(g, "env_path",
                        lambda: _env(tmp_path, "QBM_AGENT_PROFILE_ENABLED=0\n"))
    assert g.blocked_reason(), "写了 0 也要停"


def test_process_env_wins_over_file(tmp_path, monkeypatch):
    g = gates()
    monkeypatch.setattr(g, "_hotconfig", lambda: _fake_hotconfig(False))
    monkeypatch.setenv("QBM_AGENT_PROFILE_ENABLED", "1")
    monkeypatch.setattr(g, "env_path", lambda: _env(tmp_path, "QBM_AGENT_PROFILE_ENABLED=0\n"))
    assert g.profile_enabled() is True, "进程环境变量开着就该继续跑"


def test_unreadable_env_fails_open(tmp_path, monkeypatch):
    """`.env` 读不到 → **保持运行**。

    插件都已经起来了：因为一次读盘失败就静默停机，比"设置不生效"更难排查。
    """
    g = gates()
    monkeypatch.setattr(g, "_hotconfig", lambda: _fake_hotconfig(False))
    monkeypatch.delenv("QBM_AGENT_PROFILE_ENABLED", raising=False)
    monkeypatch.setattr(g, "env_path", lambda: tmp_path / "definitely-missing.env")
    assert g.profile_enabled() is True


def test_platform_switch_is_deliberately_not_used(tmp_path, monkeypatch):
    """**故意不判平台开关** —— 档案一启用，程序侧就把 platforms.qq/wechat 置成 false
    （QQ/微信交给档案，旧 AI 插件停用以免双回复）。跟着判等于把李清菡自己关掉。"""
    src = (LQH / "gates.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "platform_enabled" not in code, \
        "gates 里不许出现平台开关判定：那两个开关在档案模式下**故意**是 false"


def test_blocked_once_logs_only_first_time(caplog, tmp_path, monkeypatch):
    g = gates()
    monkeypatch.setattr(g, "_hotconfig", lambda: _fake_hotconfig(True))
    monkeypatch.delenv("QBM_AGENT_PROFILE_ENABLED", raising=False)
    monkeypatch.setattr(g, "_last_logged", "")
    with caplog.at_level("INFO"):
        g.blocked_once()
        g.blocked_once()
        g.blocked_once()
    hits = [r for r in caplog.records if "AI 总开关" in r.getMessage()]
    assert len(hits) == 1, "同一个原因只该打一条日志（别每条消息刷屏）"


# ================================================================ 出站动作兜底
class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send(self, event, msg):
        self.sent.append(msg)
        return {"id": "m1"}

    async def send_to_c2c(self, openid="", message="", msg_seq=0):
        self.sent.append(message)
        return {"id": "m1"}


def test_dispatch_channel_refuses_when_blocked(monkeypatch):
    """总开关关着 → 出站动作一个都不发，且返回明确错误码（不是静默成功）。"""
    qq = _load("lqh_qq_gate", "qq_official.py")
    fake = types.ModuleType("fake_gates")
    fake.blocked_once = lambda: "AI 总开关是关的"
    monkeypatch.setattr(qq, "gates", fake, raising=False)
    ch = qq.DispatchChannel()

    import asyncio
    bot = _FakeBot()
    ch.set_current(bot, None, "qq:c2c")
    res = asyncio.new_event_loop().run_until_complete(
        ch.action("send_private_msg", {"user_id": "o1", "message": "在呢"}))
    assert res.get("error") == "agent_disabled", res
    assert bot.sent == [], "被开关拦住的东西绝不能真发出去"


def test_dispatch_channel_passes_when_open(monkeypatch):
    qq = _load("lqh_qq_gate2", "qq_official.py")
    fake = types.ModuleType("fake_gates2")
    fake.blocked_once = lambda: ""
    monkeypatch.setattr(qq, "gates", fake, raising=False)
    ch = qq.DispatchChannel()
    bot = _FakeBot()
    ch.set_current(bot, None, "qq:c2c")
    import asyncio
    res = asyncio.new_event_loop().run_until_complete(
        ch.action("send_private_msg", {"user_id": "o1", "message": "在呢"}))
    assert res.get("message_id") == "m1" and bot.sent == ["在呢"]


# ================================================================ 源码级锁
def test_source_lock_entry_and_interject_gated():
    """入口（on_event）与主动插话都必须过闸 —— 只拦回复不拦插话，就会出现
    "关了它还在群里搭话"这种最像没关掉的表现。"""
    src = (LQH / "agent.py").read_text(encoding="utf-8")
    assert src.count("gates.blocked_once()") >= 2, \
        "on_event 与 _maybe_interject 都要过闸"
    assert "from . import games, gates, voice" in src


if __name__ == "__main__":
    print("请用 pytest 跑：pytest tests/test_liqinghan_gates.py -q")
