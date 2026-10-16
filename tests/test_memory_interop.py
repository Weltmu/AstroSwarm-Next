# -*- coding: utf-8 -*-
"""记忆互通回归（2026-10-13 老板点名："私聊/群聊互通，微信与QQ互通"）。

两个真问题：
1. **写读不对称**：写记忆走"绑定后的统一 key"（canonical），注入却只看当前 raw key
   → 微信↔QQ 绑定后必有一边看不见对方的记忆；绑定前写的旧数据也散在各自 raw key 下。
   修法：注入/读取都看**整组绑定身份**（identity.resolve 的全组）。
2. **两套 key 形态**：李清菡框架写内置库用**裸 QQ 号**，ai 插件用 `qq:数字`
   → 同一个库里同一个人互相看不见。修法：李清菡侧对内置库统一补 `平台:` 前缀
   （老库回退路径不动，旧数据零影响）。
"""
import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qbotmanager.core import memory  # noqa: E402

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
LIQINGHAN_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "liqinghan"
PKG = "ai_interop_pkg"
MOD_TMP = Path(tempfile.mkdtemp(prefix="qbm_interop_"))


def _ensure_pkg():
    """把 ai 插件的 identity / memory_bridge 按路径加载（nonebot 用最小替身）。"""
    if PKG in sys.modules:
        return (sys.modules[PKG + ".identity"], sys.modules[PKG + ".memory_bridge"])
    nb = sys.modules.get("nonebot") or types.ModuleType("nonebot")
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    sys.modules.setdefault("nonebot", nb)
    # ⚠ 必须**无条件**换成我们自己的替身（不能 `if not in sys.modules`）：
    # 全套跑时前面某个测试文件（比如 test_media_actions）已经塞了个指向系统临时目录的
    # 替身，identity 模块会按那个路径读写 —— 于是下面 fixture 里删我们这份文件根本清不掉
    # 真实那份绑定关系，`bind()` 直接返回 False（"已经绑过了"），而单跑这个文件是绿的。
    store = types.ModuleType("nonebot_plugin_localstore")
    target = MOD_TMP / "aichat_identity.json"
    store.get_plugin_config_file = lambda _name: target
    store.get_plugin_data_file = lambda _name: target
    sys.modules["nonebot_plugin_localstore"] = store
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg
    for name in ("identity", "memory_bridge"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", AI_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[PKG + ".identity"], sys.modules[PKG + ".memory_bridge"]


def _load_liqinghan_bridge():
    name = "liqinghan_memory_bridge_t"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, LIQINGHAN_DIR / "memory_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env(tmp_path, monkeypatch):
    ident, mb = _ensure_pkg()
    (MOD_TMP / "aichat_identity.json").unlink(missing_ok=True)
    ident.load_identity()
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "basic")
    db = tmp_path / "astroswarm_memory.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    memory.reset_store(db)
    yield ident, mb
    memory.reset_store(None)


def test_bound_pair_sees_each_other_from_both_sides(env):
    """微信↔QQ 绑定后：一边写的记忆，两边注入都能看到（修前 QQ 侧看不到）。"""
    ident, mb = env
    assert ident.bind("wechat:w1", "qq:q1") is True
    # 模拟 tools_bridge.memory_write：写进"绑定后的统一 key"（canonical = 微信）
    memory.remember(ident.canonical("qq:q1"), "他喜欢喝美式咖啡")

    assert "美式" in mb.inject_block(ident.resolve("qq:q1")), \
        "QQ 侧看不到微信侧写的记忆（写 canonical、读 raw 的老毛病）"
    assert "美式" in mb.inject_block(ident.resolve("wechat:w1"))


def test_pre_binding_rows_survive_binding(env):
    """绑定前写在 raw key 下的老数据，绑定后两边都要看得到。"""
    ident, mb = env
    memory.remember("qq:q1", "他养了一只叫团子的猫")
    assert ident.bind("wechat:w1", "qq:q1") is True
    memory.remember(ident.canonical("wechat:w1"), "他周末去打羽毛球")

    qq_block = mb.inject_block(ident.resolve("qq:q1"))
    wx_block = mb.inject_block(ident.resolve("wechat:w1"))
    assert "团子" in qq_block and "羽毛球" in qq_block
    assert "团子" in wx_block and "羽毛球" in wx_block


def test_liqinghan_writes_land_on_same_key_as_ai(env, monkeypatch):
    """李清菡框架写记忆 → 落到 `qq:数字`，ai 侧立刻能读到（修前是裸数字，互相看不见）。"""
    _ident, mb = env
    lq = _load_liqinghan_bridge()
    assert lq._builtin_key("1000000002") == "qq:1000000002"
    assert lq._builtin_key("wechat:x") == "wechat:x"
    assert lq.remember(None, "1000000002", "他喜欢打星际争霸") is True
    assert "星际" in mb.inject_block(["qq:1000000002"]), \
        "李清菡写的记忆和 ai 插件不在同一套 key 上"


def test_liqinghan_bound_keys_follow_identity(env, monkeypatch):
    """李清菡侧也要认绑定：拿得到 ai 的身份组就用整组 key。"""
    lq = _load_liqinghan_bridge()
    fake = types.ModuleType("src.plugins.ai")

    class _Ident:
        @staticmethod
        def resolve(key):
            return ["wechat:w1", key]

    fake.identity = _Ident
    monkeypatch.setitem(sys.modules, "src.plugins.ai", fake)
    assert lq._bound_keys("qq:q1") == ["wechat:w1", "qq:q1"]
    monkeypatch.delitem(sys.modules, "src.plugins.ai", raising=False)
    assert lq._bound_keys("qq:q1") == ["qq:q1"]      # 拿不到绑定 → 只认自己，不崩


def test_brain_and_tool_inject_whole_group():
    """接线锁：brain 注入、memory_read 工具都要用"整组"而不是单个 key。"""
    brain = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "keys or ([bridge_key] if bridge_key else [])" in brain, \
        "brain 记忆注入没走整组绑定身份"
    bridge = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    assert "block = memory_bridge.inject_block(keys or ([target] if target else []))" \
        in bridge, "memory_read 工具没走整组绑定身份"


# ================================================================ 2026-10-15 通道前缀
def _load_liqinghan_store():
    name = "liqinghan_memory_store_t"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, LIQINGHAN_DIR / "memory.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_liqinghan_official_openid_maps_to_qq_official(tmp_path):
    """李清菡的官方 QQ 事件老 sources = `qq:c2c`/`qq:group`，但 id 是 **openid**。

    两者合起来必须落到 ai 插件那套 `qq_official:<openid>`（2026-10-15 审计的高危项：
    以前落成 `qq:<openid>`，同一人在内置库里是两个人，绑定也串不起来）。
    """
    store_mod = _load_liqinghan_store()
    store = store_mod.Store(str(tmp_path / "lq5.db"))
    store.touch_user("A1B2C3D4E5", "群友", source="qq:group")
    lq = _load_liqinghan_bridge()
    assert lq._builtin_key("A1B2C3D4E5", store=store) == "qq_official:A1B2C3D4E5"
    # 纯数字（万一有人拿李清菡接 OneBot）仍按 QQ 号走
    assert lq._builtin_key("1000000002", store=store) == "qq:1000000002"


def test_official_openid_key_is_not_qq(tmp_path):
    """官方通道给的是 **openid**，不能写成 `qq:<openid>`。

    写成 `qq:` 就和 ai 插件的 `qq_official:<openid>` 不是同一个 key ——
    同一个人的记忆在两个框架里各存一份，绑定身份也串不起来。
    """
    store_mod = _load_liqinghan_store()
    store = store_mod.Store(str(tmp_path / "lq.db"))
    store.touch_user("A1B2C3D4E5", "小李", source="qq_official:c2c")
    lq = _load_liqinghan_bridge()
    assert lq._builtin_key("A1B2C3D4E5", store=store) == "qq_official:A1B2C3D4E5"
    assert lq._builtin_key("A1B2C3D4E5", store=store,
                           source="qq_official:group") == "qq_official:A1B2C3D4E5"


def test_wechat_id_key(tmp_path):
    """微信 id 同理：要落到 `wechat:<id>`（ai 插件用的就是这套）。"""
    store_mod = _load_liqinghan_store()
    store = store_mod.Store(str(tmp_path / "lq2.db"))
    store.touch_user("wxid_abc", "老王", source="wechat:private")
    lq = _load_liqinghan_bridge()
    assert lq._builtin_key("wxid_abc", store=store) == "wechat:wxid_abc"


def test_numeric_qq_and_legacy_rows_unchanged(tmp_path):
    """老规矩不能丢：数字 QQ 号仍是 `qq:数字`；没记过通道的老数据也按 QQ 处理。"""
    store_mod = _load_liqinghan_store()
    store = store_mod.Store(str(tmp_path / "lq3.db"))
    store.touch_user("1000000002", "老板", source="qq:group")
    assert _load_liqinghan_bridge()._builtin_key(
        "1000000002", store=store) == "qq:1000000002"

    store.touch_user("9999", "没记通道的人")     # 老路径：source 空
    assert store.user_source("9999") == ""
    assert _load_liqinghan_bridge()._builtin_key(
        "9999", store=store) == "qq:9999"


def test_store_upgrades_old_db_without_source_column(tmp_path):
    """老库（users 表没有 source 列）要能自动补列，补不了也不能崩。"""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE users(qq TEXT PRIMARY KEY, nickname TEXT DEFAULT '',"
                " first_seen INTEGER, last_seen INTEGER, msg_count INTEGER DEFAULT 0,"
                " relationship INTEGER DEFAULT 0, notes TEXT DEFAULT '')")
    con.execute("INSERT INTO users(qq, nickname) VALUES('1','老数据')")
    con.commit()
    con.close()

    store_mod = _load_liqinghan_store()
    store = store_mod.Store(str(path))
    assert store.user_source("1") == "", "老数据没有通道信息，按空处理"
    store.touch_user("A9", "新来的", source="wechat:private")
    assert store.user_source("A9") == "wechat:private"


def test_memory_write_lands_on_channel_key(tmp_path, env):
    """端到端：官方通道的人写记忆 → 落在 `qq_official:<openid>`，ai 侧按同一 key 读得到。"""
    _ident, mb = env
    store_mod = _load_liqinghan_store()
    store = store_mod.Store(str(tmp_path / "lq4.db"))
    store.touch_user("OPENID-XYZ", "群友", source="qq_official:group")
    lq = _load_liqinghan_bridge()
    assert lq.remember(store, "OPENID-XYZ", "他在准备考研") is True
    assert "考研" in mb.inject_block(["qq_official:OPENID-XYZ"]), \
        "李清菡从官方通道写的记忆，ai 插件按 qq_official 那套 key 读不到"
