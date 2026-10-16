# -*- coding: utf-8 -*-
"""热重载（hot reload）回归：设置改了立即生效，不用重启机器人。

老板 2026-10-11 派活的原话："一些功能能不能加上热重载呀，就不要每次重启机器人了"。
这里把**能自动断言的那几条**钉住，剩下的靠服务器上真机验证（有日志为证）：

1. `core/hotconfig.read_json`：带 (mtime,size) 缓存；文件没变不重读；
2. **坏文件回退上次成功的值**，不抛异常，且修好之后自动恢复；
3. `memory.policy.level()`：快照改了就**立即**变（智能 → 基础 → 无），
   没有快照时回退环境变量（老装机行为不变）；
4. 插件侧 `gates.py`：AI 总开关 / 平台开关**每条消息现读**，
   关掉 → 挡掉且有原因，开回来 → 立即恢复；
5. `hotconfig.write_live` 是原子写（写坏了不会留下半截 JSON）。

跑法：
    $env:PYTHONPATH="src"
    .\\.test_venv\\Scripts\\python.exe -m pytest tests/test_hot_reload.py -q
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "src" / "qbotmanager" / "assets" / "plugins"


def _load(name: str, path: Path):
    """把插件里的某个 py 文件当独立模块加载（不触发插件包 __init__，免拉 nonebot）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hotconfig(tmp_path, monkeypatch):
    from qbotmanager.core import hotconfig as hc
    hc.clear()
    monkeypatch.setenv("ASTROSWARM_LIVE_CONFIG", str(tmp_path / "astroswarm_live.json"))
    yield hc
    hc.clear()


@pytest.fixture()
def gates(tmp_path, monkeypatch):
    """插件侧的开关热读模块（独立加载，不吃插件包的 import 期副作用）。"""
    mod = _load("ai_gates_test", PLUGINS / "ai" / "gates.py")
    from qbotmanager.core import hotconfig as hc
    hc.clear()
    monkeypatch.setenv("ASTROSWARM_LIVE_CONFIG", str(tmp_path / "astroswarm_live.json"))
    yield mod
    hc.clear()


# ---------------------------------------------------------------- 缓存与失效检测

def test_read_json_caches_by_mtime_and_size(tmp_path, hotconfig):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert hotconfig.read_json(p) == {"a": 1}
    # 同样的 (mtime,size) 命中缓存：直接改内存里的缓存值，读出来应该是被改过的
    key = str(p)
    mtime, size, data = hotconfig._CACHE[key]
    hotconfig._CACHE[key] = (mtime, size, {"a": "from-cache"})
    assert hotconfig.read_json(p) == {"a": "from-cache"}

    # 文件一变（大小也变）→ 立刻重读
    p.write_text(json.dumps({"a": 2, "b": "x"}), encoding="utf-8")
    os.utime(p, ns=(mtime + 1_000_000, mtime + 1_000_000))
    assert hotconfig.read_json(p) == {"a": 2, "b": "x"}


def test_read_json_missing_file_returns_default(tmp_path, hotconfig):
    assert hotconfig.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}
    assert hotconfig.read_json(None) == {}
    # 返回的是拷贝：调用方改它不会污染缓存
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"k": [1]}), encoding="utf-8")
    got = hotconfig.read_json(p)
    got["k"].append(2)
    assert hotconfig.read_json(p) == {"k": [1]}


# ---------------------------------------------------------------- 坏文件回退

def test_bad_json_falls_back_to_last_good_and_recovers(tmp_path, hotconfig):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"memory_level": "smart"}), encoding="utf-8")
    assert hotconfig.read_json(p) == {"memory_level": "smart"}

    # 写成非法 JSON（模拟"写到一半"）：必须回退到上次成功的值，不抛异常
    p.write_text('{"memory_level": "sma', encoding="utf-8")
    assert hotconfig.read_json(p) == {"memory_level": "smart"}

    # 顶层不是对象也算坏文件 → 同样回退
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert hotconfig.read_json(p) == {"memory_level": "smart"}

    # 修好 → 自动跟上（不需要清缓存、不需要重启）
    p.write_text(json.dumps({"memory_level": "off"}), encoding="utf-8")
    assert hotconfig.read_json(p) == {"memory_level": "off"}


def test_bad_json_before_any_good_read_returns_default(tmp_path, hotconfig):
    """从来没读成功过 + 文件就是坏的：给 default，绝不给 {} 以外的意外值。"""
    p = tmp_path / "cfg.json"
    p.write_text("{ 坏 ]", encoding="utf-8")
    assert hotconfig.read_json(p, {"fallback": True}) == {"fallback": True}


def test_write_live_is_atomic(tmp_path, hotconfig):
    p = tmp_path / "config" / "ai" / hotconfig.LIVE_NAME
    out = hotconfig.write_live({"ai_disabled": False, "platforms": {"qq": True}}, p)
    assert out == p
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["ai_disabled"] is False and data["platforms"]["qq"] is True
    assert "written_at" in data
    # 不留临时文件
    assert [q.name for q in p.parent.iterdir() if q.name.endswith(".tmp")] == []


# ---------------------------------------------------------------- 记忆档位热读

def test_memory_level_follows_live_snapshot_without_restart(hotconfig, monkeypatch):
    """三档切换：智能 → 基础 → 无，**每次都立即生效**（这就是老板要的那件事）。"""
    from qbotmanager.core import memory
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "basic")     # 环境变量是兜底

    hotconfig.write_live({"memory_level": "smart", "memory_enabled": True})
    assert memory.level() == "smart"

    hotconfig.write_live({"memory_level": "basic", "memory_enabled": True})
    assert memory.level() == "basic"

    hotconfig.write_live({"memory_level": "off", "memory_enabled": False})
    assert memory.level() == "off"
    assert memory.enabled() is False

    # 回智能：也是立即的
    hotconfig.write_live({"memory_level": "smart", "memory_enabled": True})
    assert memory.level() == "smart"


def test_memory_level_falls_back_to_env_without_snapshot(hotconfig, monkeypatch):
    """老装机（没有快照文件）：行为与改造前一致 —— 读环境变量，缺省 basic。"""
    from qbotmanager.core import memory
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "smart")
    assert memory.level() == "smart"
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "乱填的")
    assert memory.level() == "basic"
    monkeypatch.delenv("ASTROSWARM_MEMORY_LEVEL", raising=False)
    assert memory.level() == "basic"


def test_memory_level_survives_broken_snapshot(hotconfig, monkeypatch):
    """快照被写坏：回退上一次成功的档位，不崩、不静默变成别的档位。"""
    from qbotmanager.core import memory
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "basic")
    hotconfig.write_live({"memory_level": "smart", "memory_enabled": True})
    assert memory.level() == "smart"

    hotconfig.live_path().write_text('{"memory_level": "sma', encoding="utf-8")
    assert memory.level() == "smart"          # ← 回退上次成功的值

    hotconfig.write_live({"memory_level": "off", "memory_enabled": False})
    assert memory.level() == "off"            # ← 修好即恢复


# ---------------------------------------------------------------- 插件侧开关热读

def test_ai_disabled_hot(gates, hotconfig, monkeypatch):
    monkeypatch.delenv("AI_DISABLED", raising=False)
    assert gates.ai_disabled() is False
    assert gates.blocked_reason() == ""

    hotconfig.write_live({"ai_disabled": True, "platforms": {"qq": True}})
    assert gates.ai_disabled() is True
    reason = gates.blocked_reason()
    assert "总开关" in reason and "不用重启" in reason

    hotconfig.write_live({"ai_disabled": False, "platforms": {"qq": True}})
    assert gates.ai_disabled() is False
    assert gates.blocked_reason() == ""


def test_platform_switch_hot(gates, hotconfig, monkeypatch):
    """QQ 开关置 0 → 挡掉且有明确原因；置回 1 → 立即恢复。"""
    monkeypatch.setenv("AI_PLATFORM_QQ", "1")
    hotconfig.write_live({"ai_disabled": False,
                          "platforms": {"qq": True, "wechat": True}})
    assert gates.platform_enabled("qq") is True
    assert gates.blocked_reason("qq") == ""

    hotconfig.write_live({"ai_disabled": False,
                          "platforms": {"qq": False, "wechat": True}})
    assert gates.platform_enabled("qq") is False
    assert gates.platform_enabled("wechat") is True          # 只关 QQ，别误伤
    reason = gates.blocked_reason("qq")
    assert "QQ" in reason and "不用重启" in reason

    hotconfig.write_live({"ai_disabled": False,
                          "platforms": {"qq": True, "wechat": True}})
    assert gates.platform_enabled("qq") is True


def test_platform_switch_falls_back_to_env_without_snapshot(gates, monkeypatch):
    monkeypatch.setenv("AI_PLATFORM_QQ", "0")
    assert gates.platform_enabled("qq") is False
    monkeypatch.setenv("AI_PLATFORM_QQ", "1")
    assert gates.platform_enabled("qq") is True
    monkeypatch.delenv("AI_PLATFORM_QQ", raising=False)
    assert gates.platform_enabled("qq") is True              # 没有配置 = 默认开
    monkeypatch.setenv("AI_DISABLED", "1")
    assert gates.ai_disabled() is True


def test_gates_survive_broken_snapshot(gates, hotconfig, monkeypatch):
    """快照写坏时：开关回退到**上一次成功的值**，绝不悄悄变成"全开"。"""
    monkeypatch.setenv("AI_PLATFORM_QQ", "1")
    hotconfig.write_live({"ai_disabled": False, "platforms": {"qq": False}})
    assert gates.platform_enabled("qq") is False
    hotconfig.live_path().write_text("{ 坏掉 ]", encoding="utf-8")
    assert gates.platform_enabled("qq") is False             # ← 回退，不是 True


# ---------------------------------------------------------------- 口径一致性

def test_restart_policy_line_and_matrix():
    from qbotmanager.core import restart_policy as rp
    assert rp.HOT_LINE == "设置改了立即生效；代码改了才需要重启，且点一下就能重启。"
    # 本轮做成热读的那几类必须是"不用重启"
    for key in ("memory_level", "ai_platforms", "ai_enabled", "personality",
                "ai_model", "ai_temperature", "ai_max_tokens", "allowed_groups",
                "super_users", "agent_owner", "agent_wake_words", "session",
                "proactive", "interject"):
        assert rp.needs_restart(key) is False, key
    # 明确不做热重载的那几类必须仍然是"要重启"
    for key in ("plugins", "tool_packs", "qq_channel", "mcp"):
        assert rp.needs_restart(key) is True, key
    assert "不做热重载" in rp.NO_HOT_CODE_REASON
    summary = rp.summary()
    assert summary["line"] == rp.HOT_LINE
    assert "memory_level" in summary["hot_keys"]
    assert "plugins" in summary["cold_keys"]
