# -*- coding: utf-8 -*-
"""桌面端能力包测试：安装/卸载、权益拦截、人设包写入、启动环境变量。"""
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("QBM_POINTER_DIR"):
    os.environ["QBM_POINTER_DIR"] = str(Path(tempfile.mkdtemp(prefix="qbm_test_ptr_")))


def _trust_plan(lic_mod):
    """短接权益签名校验：这些用例只测档位/到期逻辑，签名本身另有专项测试

    （见 test_entitlement_signature_is_required）。
    """
    lic_mod.verify_entitlement = lambda *a, **k: True


def _make_manifest_pack(root: Path, pid: str, config: dict | None = None) -> Path:
    """造一个带参数 schema 的能力包目录，返回包目录。"""
    pack = root / pid
    pack.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": pid, "name": f"{pid} 包", "version": "1.0.0", "kind": "tool-pack",
        "description": "测试用", "tools": [{"name": "t", "description": "工具"}],
    }
    if config:
        manifest["config"] = config
    (pack / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return pack


def test_pack_detail_and_config_roundtrip():
    """能力包参数：schema 渲染 → 校验 → 落盘 → 重新读回（与无头端同一套规则）。"""
    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_detail_"))
    s = Settings(tmp)
    s.ensure_dirs()
    packs = tool_packs.packs_dir(s)
    packs.mkdir(parents=True, exist_ok=True)
    _make_manifest_pack(packs, "demo", {"fields": [
        {"key": "enable", "label": "开启", "type": "bool", "default": True},
        {"key": "interval", "label": "间隔", "type": "number",
         "default": 30, "min": 5, "max": 240},
        {"key": "mode", "label": "模式", "type": "enum",
         "default": "soft", "options": ["soft", "hard"]},
    ]})

    detail = tool_packs.pack_detail(s, "demo")
    assert detail["installed"] is True
    assert [f["key"] for f in detail["fields"]] == ["enable", "interval", "mode"]
    assert detail["values"] == {"enable": True, "interval": 30, "mode": "soft"}
    assert detail["features"], "详情页要有「功能」列表"

    res = tool_packs.save_pack_config(
        s, "demo", {"interval": 90, "mode": "hard", "enable": False})
    assert res["changed"] == {"interval": 90, "mode": "hard", "enable": False}
    saved = json.loads(
        tool_packs.pack_config_path(s, "demo").read_text(encoding="utf-8"))
    assert saved == {"interval": 90, "mode": "hard", "enable": False}
    assert tool_packs.pack_detail(s, "demo")["values"]["interval"] == 90

    # 越界 / 类型不对 / 不在 schema 里的键
    for bad in ({"interval": 999}, {"interval": "abc"}, {"mode": "x"}):
        try:
            tool_packs.save_pack_config(s, "demo", bad)
            raise AssertionError(f"{bad} 应该被拒绝")
        except ValueError:
            pass
    assert tool_packs.save_pack_config(s, "demo", {"不存在的键": 1})["changed"] == {}

    # 未安装的包不能改参数；包标识非法要挡住
    try:
        tool_packs.save_pack_config(s, "not-installed", {"a": 1})
        raise AssertionError("未安装的包不该允许改参数")
    except RuntimeError:
        pass
    for bad_id in ("../x", "a/b", ""):
        try:
            tool_packs.pack_detail(s, bad_id)
            raise AssertionError(f"{bad_id!r} 应该被拒绝")
        except ValueError:
            pass


def _make_pack(path, pid, kind="tool-pack", persona=None):
    manifest = {
        "id": pid,
        "name": pid,
        "version": "1.0.0",
        "kind": kind,
        "adapters": ["qq_official"],
        "permissions": [],
        "tools": [],
    }
    if kind == "persona-pack":
        manifest["persona"] = persona or "测试人格"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{pid}/manifest.json", json.dumps(manifest, ensure_ascii=False))
        z.writestr(
            f"{pid}/tools/hello.py",
            "def handle(ctx, args):\n    return 'hi'\n",
        )


def test_install_uninstall_and_env():
    from qbotmanager.core import license as lic

    _trust_plan(lic)
    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_pack_"))
    s = Settings(tmp)
    s.ensure_dirs()
    orig = lic.license_file
    try:
        lic.license_file = lambda: tmp / "license.json"
        lic.save_license({"machine_id": "TEST", "key": "TR1-X"})
        z = tmp / "p.zip"
        _make_pack(z, "group-manager")
        entry = {
            "id": "group-manager", "version": "1.0.0", "tier": "member",
            "url": "http://x/p.zip", "sha256": "",
        }
        # 能力包已全部免费：月费档、没买过也照样能装
        lic.save_plan("monthly", time.time() + 86400)
        res = tool_packs.install_pack(s, entry, zip_path=z)
        assert res["ok"] and res["kind"] == "tool-pack"
        tool_packs.uninstall_pack(s, "group-manager")
        assert tool_packs.installed(s) == []

        # 老清单里带 owned_plugins 的账号：行为不变，仍然能装
        lic.save_plan("monthly", time.time() + 86400, ["group-manager"])
        res = tool_packs.install_pack(s, entry, zip_path=z)
        assert res["ok"] and res["kind"] == "tool-pack"
        assert (tool_packs.packs_dir(s) / "group-manager"
                / "manifest.json").exists()
        assert tool_packs.allowed_pack_ids(s) == ["group-manager"]
        env = tool_packs.pack_env(s)
        assert "group-manager" in env["ASTROSWARM_TOOL_PACKS_ALLOWED"]
        assert str(tool_packs.packs_dir(s)) == env["ASTROSWARM_TOOL_PACKS"]

        tool_packs.uninstall_pack(s, "group-manager")
        assert tool_packs.installed(s) == []
        assert tool_packs.allowed_pack_ids(s) == []
    finally:
        lic.license_file = orig
    print("OK pack install_uninstall_env")


def test_install_needs_no_entitlement():
    """插件全免费：免费版（plan=none）也能装任何包。"""
    from qbotmanager.core import license as lic

    _trust_plan(lic)
    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_pack_perm_"))
    s = Settings(tmp)
    s.ensure_dirs()
    orig = lic.license_file
    try:
        lic.license_file = lambda: tmp / "license.json"
        lic.save_license({"machine_id": "TEST", "key": "TR1-X"})
        lic.save_plan("none", 0, [])
        z = tmp / "p.zip"
        _make_pack(z, "group-manager")
        entry = {
            "id": "group-manager", "version": "1.0.0", "tier": "member",
            "url": "http://x/p.zip", "sha256": "",
        }
        res = tool_packs.install_pack(s, entry, zip_path=z)
        assert res["ok"] is True
        assert tool_packs.allowed_pack_ids(s) == ["group-manager"]
    finally:
        lic.license_file = orig
    print("OK pack no_entitlement_needed")


def test_persona_pack_writes_personality():
    from qbotmanager.core import ai_config
    from qbotmanager.core import license as lic

    _trust_plan(lic)
    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_pack_persona_"))
    s = Settings(tmp)
    s.ensure_dirs()
    orig = lic.license_file
    captured = {}
    orig_save = ai_config.save_personality
    ai_config.save_personality = lambda settings, text: (
        captured.update(text=str(text)) or True)
    try:
        lic.license_file = lambda: tmp / "license.json"
        lic.save_license({"machine_id": "TEST", "key": "TR1-X"})
        lic.save_plan("none", 0, [])
        z = tmp / "p.zip"
        _make_pack(z, "liqinghan", kind="persona-pack", persona="我是李清菡")
        entry = {
            "id": "liqinghan", "version": "1.0.0", "tier": "free",
            "url": "http://x/p.zip", "sha256": "",
        }
        res = tool_packs.install_pack(s, entry, zip_path=z)
        assert res["kind"] == "persona-pack"
        assert captured.get("text") == "我是李清菡"
    finally:
        lic.license_file = orig
        ai_config.save_personality = orig_save
    print("OK pack persona_personality")


def test_pack_env_reply_rhythm():
    from qbotmanager.core import license as lic

    _trust_plan(lic)
    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_pack_rhythm_"))
    s = Settings(tmp)
    s.ensure_dirs()
    root = tool_packs.packs_dir(s)
    (root / "reply-rhythm").mkdir(parents=True, exist_ok=True)
    (root / "reply-rhythm" / "behavior.json").write_text(
        '{"min": 1, "max": 5}', encoding="utf-8")
    env = tool_packs.pack_env(s)
    assert env["ASTROSWARM_REPLY_RHYTHM"].endswith(
        "reply-rhythm" + os.sep + "behavior.json")
    print("OK pack reply_rhythm_env")


def test_store_tools_return_structured_data():
    """能力包工具只返回结构化 JSON 数据，不写死中文回复话术。"""
    import importlib.util
    import json

    tools_root = (Path(__file__).resolve().parents[1]
                  / "src" / "qbotmanager" / "assets" / "tool_packs")

    def load(pack, name):
        path = tools_root / pack / "tools" / f"{name}.py"
        if not path.exists():
            pytest.skip(f"能力包 {pack} 不在当前源码树里")
        spec = importlib.util.spec_from_file_location(f"{pack}_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    class FakeCtx:
        def __init__(self, permissions=()):
            self._perms = set(permissions)
            self.store = None
            self.sent = []

        def can(self, perm):
            return perm in self._perms

        def send(self, *args, **kwargs):
            self.sent.append((args, kwargs))

    # 定时提醒：存储不可用也返回结构化错误，而不是"提醒存储不可用"
    mod = load("timer", "set_reminder")
    out = json.loads(mod.handle(FakeCtx(), {"minutes": 5, "text": "喝水"}))
    assert out == {"ok": False, "error": "store_unavailable"}, out

    # 群管理：权限不足是错误码；成功返回结构化结果
    mod = load("group-manager", "mute_user")
    out = json.loads(mod.handle(
        FakeCtx(), {"user_id": "1", "group_id": "2", "minutes": 5}))
    assert out == {"ok": False, "error": "permission_denied",
                   "permission": "group_admin"}, out
    ctx = FakeCtx(permissions=["group_admin"])
    out = json.loads(mod.handle(
        ctx, {"user_id": "1", "group_id": "2", "minutes": 5}))
    assert out["ok"] is True and out["action"] == "mute_user"
    assert out["minutes"] == 5 and out["user_id"] == "1"

    mod = load("group-manager", "recall_message")
    out = json.loads(mod.handle(
        FakeCtx(permissions=["group_admin"]), {"message_id": "m1"}))
    assert out["ok"] is True and out["message_id"] == "m1", out

    # 记忆：不可用返回错误码
    mod = load("memory", "remember")
    out = json.loads(mod.handle(FakeCtx(), {"fact": "x"}))
    assert out == {"ok": False, "error": "store_unavailable"}, out

    # 主动聊天：返回结构化建议种子
    mod = load("proactive", "get_activity_suggestion")
    out = json.loads(mod.handle(FakeCtx(), {}))
    assert out["ok"] is True and "suggestion" in out, out

    # 产品知识库：命中返回数据，未命中返回错误码（话术交给 agent）
    mod = load("knowledge", "product_faq")
    out = json.loads(mod.handle(FakeCtx(), {"question": "价格是多少"}))
    assert out["ok"] is True and out["matched"] == "价格", out
    out = json.loads(mod.handle(FakeCtx(), {"question": "完全无关的问题xyz"}))
    assert out == {"ok": False, "error": "no_answer"}, out

    # 联网搜索：空查询返回错误码
    mod = load("web-search", "web_search")
    out = json.loads(mod.handle(FakeCtx(), {"query": "  "}))
    assert out == {"ok": False, "error": "empty_query"}, out
    print("OK pack tools_structured_data")


def test_param_labels_translate_english_keys():
    """只有英文 key 的能力包（回复节奏那个）要能翻成中文名 + 说明 + 范围。"""
    from qbotmanager.core import tool_packs

    fields = tool_packs.pack_fields({"behavior": {"reply-rhythm": {
        "busy_long_probability": 0.35,
        "busy_long_seconds": [180, 900],
        "busy_short_seconds": [8, 40],
        "idle_probability": 0.1,
        "idle_seconds": [60, 180],
        "some_unknown_key": 7,
    }}})
    by_key = {f["key"]: f for f in fields}

    # 截图里那 5 个英文名，一个都不许再当标题显示
    for key in ("busy_long_probability", "busy_long_seconds", "busy_short_seconds",
                "idle_probability", "idle_seconds"):
        assert by_key[key]["label"] != key, f"{key} 没有中文名"
        assert by_key[key]["help"], f"{key} 缺一句人话说明"

    assert by_key["busy_long_probability"]["label"] == "「忙很久」的出现概率"
    assert by_key["busy_long_seconds"]["label"] == "「忙很久」的回复间隔"
    assert by_key["busy_long_seconds"]["unit"] == "秒"
    assert tool_packs.range_hint(by_key["busy_long_seconds"]) == "可填范围 0–3600"

    # 默认值是 [最小, 最大] → 区间参数；概率类是普通数字
    assert by_key["busy_long_seconds"]["type"] == "range"
    assert by_key["idle_seconds"]["type"] == "range"
    assert by_key["busy_long_probability"]["type"] == "number"

    # 表里没有的 key 兜底显示原名，不许报错、不许空白
    assert by_key["some_unknown_key"]["label"] == "some_unknown_key"


def test_pack_supplied_chinese_label_wins():
    """包里自己写了中文名就以它为准，映射表只补空缺。"""
    from qbotmanager.core import tool_packs

    fields = tool_packs.pack_fields({"config": {"fields": [
        {"key": "busy_long_seconds", "label": "我自己起的名字", "type": "range",
         "default": [1, 2]},
        {"key": "idle_seconds", "label": "idle_seconds", "default": [60, 180]},
    ]}})
    assert fields[0]["label"] == "我自己起的名字", "包里写好的中文名不许被覆盖"
    assert fields[1]["label"] == "空闲时的回复间隔", "label 还是英文 key 时要翻过来"


def test_range_param_coerce_and_save():
    """区间参数：能存成 [最小, 最大]，反着填 / 超范围要给中文报错。"""
    from qbotmanager.core import tool_packs
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_range_"))
    s = Settings(tmp)
    s.ensure_dirs()
    packs = tool_packs.packs_dir(s)
    packs.mkdir(parents=True, exist_ok=True)
    pack = packs / "rhythm"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps({
        "id": "rhythm", "name": "回复节奏能力包", "version": "1.0.0",
        "kind": "behavior-pack", "behavior": {"reply-rhythm": {
            "busy_long_seconds": [180, 900], "idle_seconds": [60, 180]}},
    }, ensure_ascii=False), encoding="utf-8")

    detail = tool_packs.pack_detail(s, "rhythm")
    field = next(f for f in detail["fields"] if f["key"] == "busy_long_seconds")
    assert field["type"] == "range"
    assert detail["values"]["busy_long_seconds"] == [180, 900]

    # 列表 / 字符串两种写法都认
    assert tool_packs.coerce_field(field, [80, 900]) == [80, 900]
    assert tool_packs.coerce_field(field, "80~900") == [80, 900]
    # 反着填：报错要说中文名（截图那种英文名不该出现在报错里）
    try:
        tool_packs.coerce_field(field, [900, 80])
        raise AssertionError("最小大于最大应当被拒绝")
    except ValueError as exc:
        assert "「忙很久」的回复间隔" in str(exc), str(exc)
        assert "最小不能大于最大" in str(exc)
    # 超范围也要拦
    try:
        tool_packs.coerce_field(field, [10, 99999])
        raise AssertionError("超出上限应当被拒绝")
    except ValueError as exc:
        assert "不能大于 3600" in str(exc), str(exc)

    res = tool_packs.save_pack_config(s, "rhythm", {"busy_long_seconds": [100, 500]})
    assert res["changed"] == {"busy_long_seconds": [100, 500]}, res
    saved = json.loads(tool_packs.pack_config_path(s, "rhythm").read_text(encoding="utf-8"))
    assert saved["busy_long_seconds"] == [100, 500]
    assert tool_packs.pack_detail(s, "rhythm")["values"]["busy_long_seconds"] == [100, 500]


if __name__ == "__main__":
    test_install_uninstall_and_env()
    test_install_needs_no_entitlement()
    test_persona_pack_writes_personality()
    test_pack_env_reply_rhythm()
    test_store_tools_return_structured_data()
    test_param_labels_translate_english_keys()
    test_pack_supplied_chinese_label_wins()
    test_range_param_coerce_and_save()
