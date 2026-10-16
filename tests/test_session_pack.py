# -*- coding: utf-8 -*-
"""连续对话能力包：包参数（中文 schema）→ 机器人侧参数，全链路锁一遍。

老板 2026-10-01 的要求：免@连续对话要**做成能力包**，参数在「已装能力包 → 配置」里改，
显示的必须是通俗中文名（不是 busy_long_probability 那种英文）。
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PACK_DIR = ROOT / "src" / "qbotmanager" / "assets" / "tool_packs" / "session-chat"
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_manifest_is_a_valid_behavior_pack():
    from qbotmanager.core import plugin_market

    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "session-chat"
    assert manifest["kind"] == "behavior-pack"
    assert plugin_market.validate_plugin_manifest(
        dict(manifest, adapters=["qq_official"])) is None
    behavior = manifest["behavior"]["session"]
    assert behavior["enabled"] is True
    assert behavior["mode"] == "timeout"
    assert 30 <= behavior["expire_seconds"] <= 300
    # 每个参数都要有中文名 + 一句人话（老板最在意这点）
    fields = manifest["config"]["fields"]
    keys = {f["key"] for f in fields}
    assert keys == {"enabled", "mode", "expire_seconds", "unrelated_limit"}, keys
    for field in fields:
        assert field["label"] and not field["label"].isascii(), field
        assert field.get("help"), field
    mode_field = next(f for f in fields if f["key"] == "mode")
    assert mode_field["type"] == "enum"
    assert {o["value"] for o in mode_field["options"]} == {"timeout", "api"}
    assert all(not o["label"].isascii() for o in mode_field["options"])


def test_behavior_json_matches_manifest_defaults():
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    behavior_file = json.loads((PACK_DIR / "behavior.json").read_text(encoding="utf-8"))
    assert behavior_file["session"] == manifest["behavior"]["session"]


def test_pack_is_listed_in_store_and_env():
    import shutil

    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    assert "session-chat" in tool_packs.FREE_PACK_IDS
    tmp = Path(tempfile.mkdtemp(prefix="qbm_sessionpack_"))
    s = Settings(tmp)
    s.ensure_dirs()
    # 装上（商店"安装"就是把包目录放进 <根>/tool_packs/<id>）
    target = tool_packs.packs_dir(s) / "session-chat"
    shutil.copytree(PACK_DIR, target)
    installed_ids = {p["id"] for p in tool_packs.installed(s)}
    assert "session-chat" in installed_ids, installed_ids
    env = tool_packs.pack_env(s)
    assert env.get("ASTROSWARM_PACK_SESSION_CHAT") == "1", env
    # 参数中文名兜底表里也要有（manifest 万一只剩英文 key）
    assert tool_packs.param_label("expire_seconds")["label"] == "连续对话有效期"
    assert tool_packs.param_label("enabled")["label"] == "开启免 @ 连续对话"


def test_plugin_reads_pack_params_with_config_override():
    """机器人侧：config.json（用户改的）覆盖 behavior.json（包默认）。"""
    pp = _load("ai_pack_params_t", AI_DIR / "pack_params.py")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pack = root / "session-chat"
        pack.mkdir()
        (pack / "behavior.json").write_text(json.dumps({
            "session": {"enabled": True, "mode": "timeout", "expire_seconds": 30,
                        "unrelated_limit": 5}}), encoding="utf-8")
        got = pp.read_pack_params(root, "session-chat", "session")
        assert got["mode"] == "timeout" and got["expire_seconds"] == 30
        (pack / "config.json").write_text(json.dumps({
            "session": {"mode": "api", "expire_seconds": 120}}), encoding="utf-8")
        got = pp.read_pack_params(root, "session-chat", "session")
        assert got["mode"] == "api" and got["expire_seconds"] == 120
        assert got["unrelated_limit"] == 5, "没改的键要保留包默认值"
        # 没装的包：空 dict，功能照旧按插件默认值走
        assert pp.read_pack_params(root, "reply-rhythm", "session") == {}


def test_params_without_section_wrapper_still_work():
    """参数直接平铺（没有 {"session": {...}} 包装）也要认。"""
    pp = _load("ai_pack_params_flat", AI_DIR / "pack_params.py")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pack = root / "session-chat"
        pack.mkdir()
        (pack / "config.json").write_text(
            json.dumps({"enabled": False, "mode": "api"}), encoding="utf-8")
        got = pp.read_pack_params(root, "session-chat", "session")
        assert got == {"enabled": False, "mode": "api"}, got


def test_manager_applies_pack_off_mode():
    """能力包里关掉 → 会话模式变 off（群里只有 @ 才回）。"""
    sp = _load("ai_session_policy_pack", AI_DIR / "session_policy.py")
    assert sp.MODE_OFF == "off"
    assert sp.normalize_mode("off") == "off"
    assert sp.choose_branch(is_at_me=False, at_others=False, mode="off",
                            session_active=True, auto_participate=True) == sp.BRANCH_IGNORE
    assert sp.choose_branch(is_at_me=True, at_others=False, mode="off",
                            session_active=False, auto_participate=False) == sp.BRANCH_AT_ME


def test_app_ships_and_installs_the_pack():
    """随软件自带：bot.ensure_session_pack 把它装到 <机器人根>/tool_packs，且不覆盖用户改过的 config.json。"""
    import shutil

    from qbotmanager.core import bot
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_ensurepack_"))
    s = Settings(tmp)
    s.ensure_dirs()
    # 打包版里 assets 在 qbotmanager/assets 下；测试环境直接用仓库源码目录
    real_assets = ROOT / "src" / "qbotmanager" / "assets"
    orig = bot.__file__
    try:
        import qbotmanager.core.bot as bot_mod
        # 让 Path(__file__).parent.parent / "assets" 指到仓库 assets
        bot_mod.__file__ = str(ROOT / "src" / "qbotmanager" / "core" / "bot.py")
        bot.ensure_session_pack(s)
    finally:
        import qbotmanager.core.bot as bot_mod
        bot_mod.__file__ = orig
    target = Path(s.root) / "tool_packs" / "session-chat"
    assert (target / "manifest.json").exists(), list(target.iterdir())
    assert (target / "behavior.json").exists()

    # 用户改过参数后再同步一次：config.json 必须保住
    (target / "config.json").write_text(
        json.dumps({"session": {"mode": "api"}}), encoding="utf-8")
    bot_mod.__file__ = str(ROOT / "src" / "qbotmanager" / "core" / "bot.py")
    try:
        bot.ensure_session_pack(s)
    finally:
        bot_mod.__file__ = orig
    saved = json.loads((target / "config.json").read_text(encoding="utf-8"))
    assert saved["session"]["mode"] == "api", "用户调过的参数不能被自带包覆盖"

    assert real_assets.is_dir()


if __name__ == "__main__":
    test_manifest_is_a_valid_behavior_pack()
    test_behavior_json_matches_manifest_defaults()
    test_pack_is_listed_in_store_and_env()
    test_plugin_reads_pack_params_with_config_override()
    test_params_without_section_wrapper_still_work()
    test_manager_applies_pack_off_mode()
    test_app_ships_and_installs_the_pack()
    print("OK session pack")
