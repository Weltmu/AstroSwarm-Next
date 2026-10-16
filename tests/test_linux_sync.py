# -*- coding: utf-8 -*-
"""Linux 无头端 ↔ 桌面端 同步项：新加的接口与自动装包都要有证据。

对应审计报告 `Win-Linux功能差集-20261001.md` 的 G1（群消息模式）、G2（通道体检）、
G3（群列表）、G5（连续对话能力包自动装）、G6/G7/G8（三个开关）。
"""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroswarm_linux import auth, console_ext, deploy, headless_config, platform_info
from astroswarm_linux.api import app

client = TestClient(app)


def _auth(monkeypatch, tmp_path) -> dict:
    """把配置目录挪到临时目录并塞一个 token，返回带鉴权的 header。"""
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path)
    cfg = headless_config.load()
    cfg.update({"account_token": "T-TEST", "account_email": "t@test.local"})
    headless_config.save(cfg)
    return {"Authorization": "Bearer T-TEST"}


@pytest.fixture
def robot_root(monkeypatch, tmp_path):
    """机器人根也指到临时目录。

    不加这一层的话 deploy.build_settings() 会落回本机默认数据目录
    （Windows 上是 ~/.local/share/astroswarm），测试就会去写真实文件 —— 2026-10-01 踩过。
    """
    from qbotmanager.core.settings import Settings

    s = Settings(tmp_path / "botroot")
    s.ensure_dirs()
    s.save()
    monkeypatch.setattr(console_ext, "_settings", lambda: s)
    return s


def _settings(tmp_path):
    """机器人根指向临时目录，别碰真机配置。"""
    return deploy.build_settings()


def test_ai_settings_endpoint_reads_robot_side_config(monkeypatch, tmp_path, robot_root):
    """G6/G7/G8：三个开关读的是机器人侧 aichat_manager.json，不是控制台 config.json。"""
    headers = _auth(monkeypatch, tmp_path)
    res = client.get("/api/ai/settings", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["group_chat_enabled"] is True          # 默认开
    assert body["rich_parse_enabled"] is True          # 默认开
    assert body["allowed_groups"] == []                # 空 = 不限群
    assert body["interject"]["enabled"] is False       # 搭话默认关
    assert body["session"]["mode"] == "timeout"        # 免@默认超时模式
    assert "known_groups" in body


def test_ai_settings_endpoint_writes_and_round_trips(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    res = client.put("/api/ai/settings", headers=headers, json={
        "group_chat_enabled": False,
        "rich_parse_enabled": False,
        "allowed_groups": ["群A"],
        "interject": {"enabled": True, "probability": 0.5},
    })
    assert res.status_code == 200, res.text
    assert set(res.json()["changed"]) == {"group_chat_enabled", "rich_parse_enabled",
                                         "allowed_groups", "interject"}
    body = client.get("/api/ai/settings", headers=headers).json()
    assert body["group_chat_enabled"] is False
    assert body["rich_parse_enabled"] is False
    assert body["allowed_groups"] == ["群A"]
    assert body["interject"]["enabled"] is True
    assert abs(body["interject"]["probability"] - 0.5) < 1e-6

    # 空数组 = 回到不限群
    client.put("/api/ai/settings", headers=headers, json={"allowed_groups": []})
    assert client.get("/api/ai/settings", headers=headers).json()["allowed_groups"] == []


def test_ai_settings_rejects_wrong_types(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    assert client.put("/api/ai/settings", headers=headers,
                      json={"allowed_groups": "群A"}).status_code == 400
    assert client.put("/api/ai/settings", headers=headers,
                      json={"interject": "开"}).status_code == 400


def test_ai_settings_requires_login(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path)
    assert client.get("/api/ai/settings").status_code == 401


def test_qq_group_state_endpoint(monkeypatch, tmp_path, robot_root):
    """G1：群消息模式（全量 / 仅@ / 没收到）+ 一句人话。"""
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import ai_config

    # 用夹具里那份 Settings：接口读的就是它，写别处会读到旧的
    s = robot_root
    state_file = ai_config.qq_group_state_file(s)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"mode": "none"}), encoding="utf-8")

    body = client.get("/api/qq/group-state", headers=headers).json()
    assert body["mode"] == "none"
    assert "还没收到过" in body["text"]

    state_file.write_text(json.dumps(
        {"mode": "at_only", "last_ts": time.time(),
         "events": {"GROUP_AT_MESSAGE_CREATE": 3}}), encoding="utf-8")
    body = client.get("/api/qq/group-state", headers=headers).json()
    assert body["mode"] == "at_only"
    assert "仅 @ 消息" in body["text"]
    assert "接收所有消息" in body["hint"], body
    assert body["events"]["GROUP_AT_MESSAGE_CREATE"] == 3

    state_file.write_text(json.dumps(
        {"mode": "full", "last_ts": time.time()}), encoding="utf-8")
    body = client.get("/api/qq/group-state", headers=headers).json()
    assert body["mode"] == "full" and "全量模式" in body["text"]


def test_qq_diagnose_endpoint_gives_rows(monkeypatch, tmp_path):
    """G2：体检逐条给结论；机器人没在跑时要报"没在跑"，不能崩。"""
    headers = _auth(monkeypatch, tmp_path)
    # 体检只对官方通道逐条查（第三方协议端只回一行说明，这是桌面端同样的行为），
    # 所以这里把机器人设置切成官方通道 + 填好凭证。
    from qbotmanager.core.settings import Settings

    real = Settings(tmp_path / "botroot")
    real.ensure_dirs()
    real.qq_channel = "official"
    real.qq_official_appid = "102000777"
    real.qq_official_token = "t" * 32
    real.qq_official_secret = "s" * 32
    real.save()
    monkeypatch.setattr(console_ext, "_settings", lambda: real)
    monkeypatch.setattr(deploy, "status", lambda: {"state": "installed", "qq": "stopped"})

    body = client.get("/api/qq/diagnose", headers=headers).json()
    assert body["rows"], body
    names = [row["name"] for row in body["rows"]]
    for expected in ("凭证", "环境", "机器人进程", "连接", "群消息", "AI 总开关", "群聊回话", "AI 模型"):
        assert expected in names, names
    process = next(r for r in body["rows"] if r["name"] == "机器人进程")
    assert process["level"] == "bad" and "没在跑" in process["text"], process
    assert body["summary"]


def test_qq_diagnose_tells_third_party_channel_is_out_of_scope(monkeypatch, tmp_path):
    """第三方协议端：体检只回一行说明，不硬套官方那套结论。"""
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core.settings import Settings

    real = Settings(tmp_path / "botroot2")
    real.ensure_dirs()
    real.qq_channel = "onebot"
    real.save()
    monkeypatch.setattr(console_ext, "_settings", lambda: real)
    body = client.get("/api/qq/diagnose", headers=headers).json()
    assert len(body["rows"]) == 1
    assert "第三方" in body["rows"][0]["text"]


def test_linux_run_state_adapter():
    """适配器：Linux 的 deploy.status() 要能被 qq_doctor 当成桌面端的 manager 用。"""
    state = console_ext._LinuxRunState()
    assert isinstance(state.bot_running(), bool)
    info = state.qq_login_state()
    assert set(info) >= {"connected", "reason"}
    assert isinstance(info["connected"], bool)


def test_scaffold_bot_installs_session_pack(tmp_path, monkeypatch):
    """G5：无头端部署只走 scaffold_bot —— 它必须把「连续对话能力包」也装好。"""
    from qbotmanager.core import bot as bot_mod
    from qbotmanager.core.settings import Settings

    s = Settings(tmp_path)
    s.ensure_dirs()
    monkeypatch.setattr(bot_mod, "ensure_agent_runtime", lambda *a, **k: None)
    monkeypatch.setattr(bot_mod, "ensure_ilink_adapter", lambda *a, **k: None)
    monkeypatch.setattr(bot_mod, "ensure_ai_plugin", lambda *a, **k: None)
    monkeypatch.setattr(bot_mod, "ensure_bridge_client", lambda *a, **k: None)
    monkeypatch.setattr(bot_mod, "ensure_liqinghan_plugin", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(bot_mod, "ensure_session_pack", lambda st: calls.append(st))
    bot_mod.scaffold_bot(s)
    assert calls == [s], "scaffold_bot 必须调用 ensure_session_pack（Linux 部署路径）"
    assert (s.bot_dir / "run.py").exists() and (s.bot_dir / "pyproject.toml").exists()


def test_session_pack_ships_in_bundled_assets():
    """包本体必须在随软件发出的 assets 里，不然 scaffold_bot 拷不出东西。"""
    pack = (Path(__file__).resolve().parents[1] / "src" / "qbotmanager"
            / "assets" / "tool_packs" / "session-chat")
    assert (pack / "manifest.json").exists()
    assert (pack / "behavior.json").exists()


def test_account_profile_shows_nickname_and_avatar(monkeypatch, tmp_path, robot_root):
    """G14：Linux 控制台也能看到账号头像与昵称（桌面端早就有，无头端一直缺）。"""
    import base64

    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import account as account_mod

    # 造一个"已登录 + 有昵称 + 有头像文件"的账号
    monkeypatch.setattr(account_mod, "_appdata_dir", lambda: tmp_path)
    account_mod.save_account({"email": "boss@test.local", "token": "T",
                              "nickname": "清秋"})
    account_mod.avatar_file().write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    body = client.get("/api/account/profile", headers=headers).json()
    assert body["email"] == "boss@test.local"
    assert body["nickname"] == "清秋"
    assert body["display_name"] == "清秋", body
    assert body["avatar"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(body["avatar"].split(",", 1)[1]) == b"\xff\xd8\xff\xe0fake-jpeg"


def test_account_profile_falls_back_to_email_prefix(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import account as account_mod

    monkeypatch.setattr(account_mod, "_appdata_dir", lambda: tmp_path)
    account_mod.save_account({"email": "someone@test.local", "token": "T"})
    body = client.get("/api/account/profile", headers=headers).json()
    assert body["display_name"] == "someone", body


def test_account_profile_save_forwards_to_server(monkeypatch, tmp_path, robot_root):
    """改昵称：转发给账号服务，并把返回写回本地。"""
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import account as account_mod

    monkeypatch.setattr(account_mod, "_appdata_dir", lambda: tmp_path)
    account_mod.save_account({"email": "boss@test.local", "token": "T"})
    seen = {}

    def fake_post(path, payload, token=""):
        seen["path"] = path
        seen["payload"] = payload
        seen["token"] = token
        return {"ok": True, "nickname": payload["nickname"], "signature": payload["nickname"]}

    monkeypatch.setattr(account_mod, "_post", fake_post)
    body = client.post("/api/account/profile", headers=headers,
                       json={"nickname": "  新名字  "}).json()
    assert seen["path"] == "/api/account/profile"
    assert seen["payload"] == {"nickname": "  新名字  "}
    assert seen["token"] == "T"
    assert body["display_name"] == "新名字"           # 服务端会做空白归一
    assert account_mod.load_account()["nickname"] == "新名字"


def test_account_avatar_upload_validates_and_stores(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import account as account_mod

    monkeypatch.setattr(account_mod, "_appdata_dir", lambda: tmp_path)
    account_mod.save_account({"email": "boss@test.local", "token": "T"})
    # 用一张真 JPEG（1×1 像素）当头像，服务端返回它归一化后的样子
    tiny = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQN"
            "DAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA"
            "/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")
    seen = {}

    def fake_upload(token, data_url):
        seen["token"] = token
        seen["data_url"] = data_url
        return {"ok": True, "avatar": data_url}

    monkeypatch.setattr(account_mod, "upload_avatar", fake_upload)
    res = client.post("/api/account/avatar", headers=headers, json={"avatar": tiny})
    assert res.status_code == 200, res.text
    assert seen["token"] == "T" and seen["data_url"] == tiny
    assert account_mod.avatar_file().exists(), "头像要落盘，下次打开才有"
    # 格式不对 / 太大都要拦住
    assert client.post("/api/account/avatar", headers=headers,
                       json={"avatar": "http://x/y.jpg"}).status_code == 400
    assert client.post("/api/account/avatar", headers=headers,
                       json={"avatar": "data:image/jpeg;base64," + "A" * (4 * 1024 * 1024)}
                       ).status_code == 400


def test_account_profile_requires_login_and_auth(monkeypatch, tmp_path, robot_root):
    from qbotmanager.core import account as account_mod

    monkeypatch.setattr(account_mod, "_appdata_dir", lambda: tmp_path)
    # 没带 token → 401（控制台鉴权）
    assert client.get("/api/account/profile").status_code == 401
    # 带了控制台 token 但本机没登录星群账号 → 401
    headers = _auth(monkeypatch, tmp_path)
    assert client.get("/api/account/profile", headers=headers).status_code == 401


def test_ai_settings_covers_new_switches(monkeypatch, tmp_path, robot_root):
    """2026-10-02 新增的两个开关也要能从 Linux 控制台读写（入群欢迎 + 发送白名单）。"""
    headers = _auth(monkeypatch, tmp_path)
    body = client.get("/api/ai/settings", headers=headers).json()
    assert body["group_welcome_enabled"] is True, "入群欢迎默认开"
    assert body["allowed_send_targets"] == [], "白名单默认空 = 一个都不许发"

    res = client.put("/api/ai/settings", headers=headers, json={
        "group_welcome_enabled": False,
        "allowed_send_targets": ["group:123", " user:abc ", ""],
    })
    assert res.status_code == 200, res.text
    assert set(res.json()["changed"]) == {"group_welcome_enabled", "allowed_send_targets"}

    body = client.get("/api/ai/settings", headers=headers).json()
    assert body["group_welcome_enabled"] is False
    assert body["allowed_send_targets"] == ["group:123", "user:abc"], body

    from qbotmanager.core import manager as _mgr  # noqa: F401  仅为确认路径存在
    bot_side = robot_root.bot_dir / "config" / "ai" / "aichat_manager.json"
    assert bot_side.exists(), "要写进机器人侧配置，不是控制台自己的 config.json"
    import json as _json

    saved = _json.loads(bot_side.read_text(encoding="utf-8"))
    assert saved["group_welcome_enabled"] is False
    assert saved["allowed_send_targets"] == ["group:123", "user:abc"]

    assert client.put("/api/ai/settings", headers=headers,
                      json={"allowed_send_targets": "group:1"}).status_code == 400


def test_logs_humanize_and_official_filter(monkeypatch, tmp_path, robot_root):
    """#10 的 Linux 侧：日志事件名翻人话 + 只看官方通道那几行。"""
    from astroswarm_linux import logs as hl_logs

    headers = _auth(monkeypatch, tmp_path)
    log_file = tmp_path / "nonebot.log"
    log_file.write_text(
        "10-02 10:56:45 [INFO] ai | 活跃度管理器初始化完成\n"
        "10-02 10:56:54 [SUCCESS] nonebot | QQ 1905694838 | "
        "[EventType.READY]: {'session_id': 'x'}\n"
        "10-02 10:57:01 [INFO] nonebot | [EventType.GROUP_MESSAGE_CREATE]: 你好\n"
        "10-02 10:57:02 [INFO] deploy | 部署完成\n",
        encoding="utf-8")
    monkeypatch.setattr(hl_logs, "resolve_log", lambda name=None: ("nonebot", log_file))

    body = client.get("/api/logs/tail?n=50", headers=headers).json()
    joined = "\n".join(body["lines"])
    assert "群里收到一条消息（全量模式：没 @ 也收）" in joined, joined
    assert "连接成功，机器人上线了" in joined
    assert "EventType.GROUP_MESSAGE_CREATE" not in joined, "英文名不该原样露出来"

    off = client.get("/api/logs/official?n=50", headers=headers).json()
    assert off["total"] == 2, off
    assert all("部署完成" not in line and "活跃度" not in line for line in off["lines"])


def test_read_json_uses_shared_cache(monkeypatch, tmp_path):
    """#13：控制台读 JSON 走共用缓存（按 mtime+大小 命中），文件改过仍要立刻重读。"""
    from qbotmanager.core import json_cache

    json_cache.clear()
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert console_ext._read_json(path) == {"a": 1}

    # 命中缓存：把 read_text 换掉也不该被调用
    calls = {"n": 0}
    real_read = Path.read_text

    def counting(self, *a, **k):
        calls["n"] += 1
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting)
    assert console_ext._read_json(path) == {"a": 1}
    assert calls["n"] == 0, "命中缓存时不该再读盘"
    monkeypatch.undo()

    # 文件改了要能读到新内容（缓存不能挡）
    path.write_text(json.dumps({"a": 2, "b": 3}), encoding="utf-8")
    assert console_ext._read_json(path) == {"a": 2, "b": 3}
    # 坏文件 / 不存在都返回空 dict，不抛
    path.write_text("{坏掉的", encoding="utf-8")
    assert console_ext._read_json(path) == {}
    assert console_ext._read_json(tmp_path / "nope.json") == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

