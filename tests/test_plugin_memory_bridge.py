# -*- coding: utf-8 -*-
"""插件侧「特性检测 + 回退」回归：内置记忆模块在 / 不在，都不能崩。

为什么要单独测这一条：老板的要求是「ai 与 liqinghan 都改成调用内置模块，
但要做特性检测 —— 若模块不存在（老装机环境），回退到它们各自原有逻辑，不能崩」。
老装机环境没法现场复现，所以这里用两种办法造：

1. 直接把 `qbotmanager` 从 `sys.modules` 里掐掉（模拟没装这个包）；
2. 直接把两个 bridge 模块的 `builtin()` 打桩成 return None。

跑法：
    $env:PYTHONPATH="D:\\ai\\QBotManager\\src"
    .\\.test_venv\\Scripts\\python.exe -m pytest tests/test_plugin_memory_bridge.py -q
"""
import ast
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "src" / "qbotmanager" / "assets" / "plugins"


def _load(name: str, path: Path):
    """把插件里的某个 py 文件当独立模块加载（不触发插件包的 __init__，免拉 nonebot）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ai_bridge():
    return _load("ai_memory_bridge_test", PLUGINS / "ai" / "memory_bridge.py")


@pytest.fixture(scope="module")
def lq_bridge():
    return _load("lq_memory_bridge_test", PLUGINS / "liqinghan" / "memory_bridge.py")


class FakeStore:
    """插件自带记忆的老逻辑替身（只记调用）。"""

    def __init__(self):
        self.facts = []
        self.calls = []

    def remember_if_new(self, qq, fact):
        self.calls.append(("remember_if_new", qq, fact))
        self.facts.append(fact)
        return True

    def memories_for(self, qq, limit=5):
        self.calls.append(("memories_for", qq, limit))
        return [{"fact": f"老库：{f}", "ts": int(time.time())} for f in self.facts[:limit]]

    def diary_recent(self, limit=2):
        return [{"day": "2026-10-01", "content": "老日记"}]

    def save_diary(self, day, content):
        self.calls.append(("save_diary", day, content))


def _no_builtin(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "builtin", lambda: None)


# ---------------------------------------------------------------- 模块不在 = 回退

def test_ai_bridge_falls_back_when_module_missing(ai_bridge, monkeypatch):
    _no_builtin(ai_bridge, monkeypatch)
    assert ai_bridge.available() is False
    assert ai_bridge.level() == "off"          # ai 侧缺省 off：不动老数据
    assert ai_bridge.enabled() is False
    assert ai_bridge.remember("qq:1", "随便一条") is False
    assert ai_bridge.inject_block(["qq:1"]) == ""
    assert ai_bridge.list_for("qq:1") == []
    assert ai_bridge.forget("qq:1", "x") == []
    assert ai_bridge.prompt_prefix() == "以下是长期记忆（如与当前对话无关可忽略）：\n"
    ai_bridge.touch("qq:1", "某人")             # 不该抛


def test_liqinghan_bridge_falls_back_when_module_missing(lq_bridge, monkeypatch):
    _no_builtin(lq_bridge, monkeypatch)
    store = FakeStore()
    assert lq_bridge.available() is False
    assert lq_bridge.level() == "basic"        # 李清菡侧缺省 basic：保住线上现行为
    assert lq_bridge.legacy_inject_enabled() is True   # 回退 → 老注入照旧跑
    assert lq_bridge.extract_enabled() is True
    assert lq_bridge.remember(store, "10001", "他喜欢喝美式") is True
    assert ("remember_if_new", "10001", "他喜欢喝美式") in store.calls
    rows = lq_bridge.recent(store, "10001", 10)
    assert rows and rows[0]["fact"].startswith("老库：")
    assert lq_bridge.diary_recent(store, 2)[0]["day"] == "2026-10-01"
    lq_bridge.save_diary(store, "2026-10-11", "新日记")
    assert ("save_diary", "2026-10-11", "新日记") in store.calls
    assert lq_bridge.inject_extra(store, object(), "10001") == ""
    assert lq_bridge.merge_after_scan() is None
    assert lq_bridge.migrate_if_needed() is None
    assert "老逻辑" in lq_bridge.level_note()


def test_ai_bridge_import_error_is_swallowed(ai_bridge, monkeypatch):
    """让 import qbotmanager.* 直接抛 ImportError（模拟老装机环境）。

    要挡的是**导入过程**，不是 sys.modules 缓存：把缓存里已有的模块标成 None
    是拦不住的（`from x import y` 命中缓存就成功了），所以这里换成换掉 __import__。
    """
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "qbotmanager" or name.startswith("qbotmanager."):
            raise ImportError("模拟老装机环境：没有 qbotmanager")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert ai_bridge.builtin() is None
    assert ai_bridge.available() is False
    assert ai_bridge.level() == "off"
    assert ai_bridge.inject_block(["qq:1"]) == ""
    assert ai_bridge.remember("qq:1", "写不进去也不该抛") is False


# ---------------------------------------------------------------- 模块在 = 走内置

def test_ai_bridge_uses_builtin_when_present(ai_bridge, tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "smart")
    from qbotmanager.core import memory
    memory.reset_store(db)
    try:
        assert ai_bridge.available() is True
        assert ai_bridge.level() == "smart"
        assert ai_bridge.enabled() is True
        assert ai_bridge.remember("qq:10001", "他妈妈住院了", weight=4) is True
        block = ai_bridge.inject_block(["qq:10001"])
        assert "他妈妈住院了" in block
        assert ai_bridge.prompt_prefix() == ""      # 智能档自带表头
        assert [r["fact"] for r in ai_bridge.list_for("qq:10001")] == ["他妈妈住院了"]
        assert ai_bridge.forget("qq:10001", "他妈妈住院了") == ["他妈妈住院了"]
        assert ai_bridge.list_for("qq:10001") == []
    finally:
        memory.reset_store(None)
        memory.set_level("basic")


def test_ai_bridge_off_never_writes(ai_bridge, tmp_path, monkeypatch):
    """ai 侧缺省档位是 off：没设环境变量时也不该写任何东西。"""
    db = tmp_path / "m.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    monkeypatch.delenv("ASTROSWARM_MEMORY_LEVEL", raising=False)
    from qbotmanager.core import memory
    memory.reset_store(db)
    try:
        # 环境变量真的没有 → level() 回落 basic；显式设成 off 才是 ai 侧的缺省
        monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "off")
        assert ai_bridge.remember("qq:1", "不该写") is False
        assert ai_bridge.inject_block(["qq:1"]) == ""
        # 老注入在 off 档必须停掉（否则「无」档还是会把老记忆念出来）
        assert ai_bridge.prompt_prefix() == ""
        assert not db.exists()
    finally:
        memory.reset_store(None)
        memory.set_level("basic")


def test_liqinghan_bridge_off_stops_legacy_injection(lq_bridge, tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "off")
    from qbotmanager.core import memory
    memory.reset_store(db)
    store = FakeStore()
    try:
        assert lq_bridge.legacy_inject_enabled() is False   # off：老注入停
        assert lq_bridge.extract_enabled() is False         # off：不提炼
        assert lq_bridge.remember(store, "10001", "不该写") is False
        assert store.calls == []                            # 一条都没落到老库
        assert not db.exists()
    finally:
        memory.reset_store(None)
        memory.set_level("basic")


def test_liqinghan_bridge_basic_keeps_legacy_injection(lq_bridge, tmp_path, monkeypatch):
    """基础档 = 线上现行为：老注入照旧跑，内置不额外注入。"""
    db = tmp_path / "m.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "basic")
    from qbotmanager.core import memory
    memory.reset_store(db)
    store = FakeStore()
    try:
        assert lq_bridge.legacy_inject_enabled() is True
        assert lq_bridge.inject_extra(store, object(), "10001") == ""
    finally:
        memory.reset_store(None)


# ---------------------------------------------------------------- 程序侧：档位读写

def _load_ai_config_functions():
    """只取 ai_config.py 里几个纯函数来验档位读写，绕开 nonebot / qt 依赖。"""
    src = (ROOT / "src" / "qbotmanager" / "core" / "ai_config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    want = {"MEMORY_LEVELS", "MEMORY_LEVEL_DEFAULT", "MEMORY_LEVEL_LABELS",
            "MEMORY_LEVEL_HINTS", "MEMORY_LEVEL_NOTE", "normalize_memory_level",
            "read_memory_level", "read_memory_level_or_off", "save_memory_level",
            "memory_level_info", "read_memory_enabled"}
    picked = [n for n in tree.body
              if (isinstance(n, (ast.FunctionDef, ast.Assign))
                  and (getattr(n, "name", None) in want
                       or any(getattr(t, "id", None) in want for t in getattr(n, "targets", []))))]
    mod = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"json": __import__("json"), "Path": Path, "_default_manager": lambda: {
        "memory_enabled": True, "memory_level": "basic"}}
    exec(compile(mod, "ai_config_extract", "exec"), ns)  # noqa: S102 —— 只跑自己仓库的源码
    return ns


class FakeSettings:
    def __init__(self, root: Path):
        self.root = root
        self.bot_dir = root / "bot"


def test_memory_level_config_roundtrip(tmp_path):
    ns = _load_ai_config_functions()
    settings = FakeSettings(tmp_path)
    mgr_file = settings.bot_dir / "config" / "ai" / "aichat_manager.json"
    mgr_file.parent.mkdir(parents=True, exist_ok=True)
    ns["read_manager"] = lambda s: (__import__("json").loads(mgr_file.read_text(encoding="utf-8"))
                                    if mgr_file.exists() else {})
    calls = {}

    def fake_write(s, mgr):
        calls["mgr"] = mgr
        mgr_file.write_text(__import__("json").dumps(mgr, ensure_ascii=False), encoding="utf-8")
        return True

    ns["_write_manager"] = fake_write

    # 老配置（没有 memory_level 键）→ 按 basic，不悄悄升级也不悄悄关掉
    mgr_file.write_text('{"memory_enabled": true}', encoding="utf-8")
    assert ns["read_memory_level"](settings) == "basic"
    assert ns["read_memory_level_or_off"](settings) == "basic"
    # 旧的「启用全局记忆」关掉 = 无
    mgr_file.write_text('{"memory_enabled": false, "memory_level": "smart"}', encoding="utf-8")
    assert ns["read_memory_level_or_off"](settings) == "off"
    # 认中文、认大写、乱填回退
    assert ns["normalize_memory_level"]("智能") == "smart"
    assert ns["normalize_memory_level"]("BASIC") == "basic"
    assert ns["normalize_memory_level"]("off") == "off"
    assert ns["normalize_memory_level"]("") == "basic"
    assert ns["normalize_memory_level"]("什么鬼") == "basic"
    # 保存：写 memory_level 的同时把旧开关一起摆对
    assert ns["save_memory_level"](settings, "无") is True
    saved = __import__("json").loads(mgr_file.read_text(encoding="utf-8"))
    assert saved["memory_level"] == "off" and saved["memory_enabled"] is False
    assert ns["save_memory_level"](settings, "smart") is True
    saved = __import__("json").loads(mgr_file.read_text(encoding="utf-8"))
    assert saved["memory_level"] == "smart" and saved["memory_enabled"] is True
    info = ns["memory_level_info"]()
    assert [o["value"] for o in info["options"]] == ["off", "basic", "smart"]
    assert all(o["label"] and o["hint"] for o in info["options"])
