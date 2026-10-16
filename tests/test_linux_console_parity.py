# -*- coding: utf-8 -*-
"""Linux 控制台 ↔ 桌面端：飞书 / 纸飞机 / QQ 官方凭证 / 日志保留（2026-10-04 补齐）。

对应 `tests/test_console_parity_matrix.py` 的 REQUIRED_ENDPOINTS：
    /api/feishu/status、/api/feishu/config、/api/feishu/install-adapter、
    /api/telegram/status、/api/qq/official、/api/logs/retention、/api/logs/cleanup-now

四条硬性验收都覆盖到了：
  ① 飞书配置写进去后 app_id_set 为真、且响应里**不出现密钥明文**；
  ② 日志保留读-改-写回环（临时目录，含越界值拒绝与立刻清理）；
  ③ /api/qq/official 的 secret / token 只回布尔；
  ④ 纸飞机 status 如实回 supported:false。
"""
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astroswarm_linux import console_ext, deploy, headless_config, platform_info
from astroswarm_linux.api import app

client = TestClient(app)

FEISHU_SECRET = "FEISHU-SECRET-9f2c"
FEISHU_TOKEN = "FEISHU-VERIFY-4b7d"
FEISHU_ENCRYPT = "FEISHU-ENCRYPT-1a2b"
QQ_SECRET = "QQ-SECRET-77aa"
QQ_TOKEN = "QQ-TOKEN-88bb"


def _auth(monkeypatch, tmp_path, token="T-PARITY"):
    """配置目录挪到临时目录 + 塞一个控制台 token，返回带鉴权的 header。"""
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "config")
    cfg = headless_config.load()
    cfg.update({"account_token": token, "account_email": "t@test.local"})
    headless_config.save(cfg)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def robot_root(monkeypatch, tmp_path):
    """机器人根 / 数据根 / 日志目录全指到临时目录（不碰真机配置）。"""
    from qbotmanager.core.settings import Settings

    s = Settings(tmp_path / "botroot")
    s.ensure_dirs()
    monkeypatch.setattr(console_ext, "_settings", lambda: s)
    monkeypatch.setattr(platform_info, "data_home", lambda: tmp_path / "data")
    monkeypatch.setattr(platform_info, "log_home", lambda: tmp_path / "logs")
    return s


@pytest.fixture
def no_adapter(monkeypatch):
    """适配器"没装"（真去跑 pip show 会很慢，也会依赖本机环境）。"""
    monkeypatch.setattr(console_ext, "_feishu_adapter_installed",
                        lambda settings, refresh=False: False)


# ---------------------------------------------------------------- ① 飞书
def test_feishu_config_writes_and_never_echoes_secret(monkeypatch, tmp_path, robot_root, no_adapter):
    headers = _auth(monkeypatch, tmp_path)

    res = client.put("/api/feishu/config", headers=headers, json={
        "enabled": True,
        "app_id": "cli_demo_123",
        "app_secret": FEISHU_SECRET,
        "verification_token": FEISHU_TOKEN,
        "encrypt_key": FEISHU_ENCRYPT,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["needs_restart"] is True
    assert set(body["changed"]) == {"enabled", "app_id", "app_secret",
                                   "verification_token", "encrypt_key"}
    # 写操作的响应里也不能有密钥
    for secret in (FEISHU_SECRET, FEISHU_TOKEN, FEISHU_ENCRYPT):
        assert secret not in res.text

    status = client.get("/api/feishu/status", headers=headers)
    assert status.status_code == 200, status.text
    data = status.json()
    assert data["enabled"] is True
    assert data["app_id_set"] is True, "写了 app_id 之后必须显示已填"
    assert data["app_secret_set"] is True
    assert data["verification_token_set"] is True and data["encrypt_key_set"] is True
    # 只回布尔，明文一个都不许出现（前后端都靠这条保证密钥不外泄）
    for secret in (FEISHU_SECRET, FEISHU_TOKEN, FEISHU_ENCRYPT, "cli_demo_123"):
        assert secret not in status.text, status.text
    assert data["app_id_set"] is True and isinstance(data["app_id_set"], bool)
    # 适配器没装 → 还不能算就绪，why 要说清差什么
    assert data["adapter_installed"] is False
    assert data["ready"] is False and "适配器" in data["why"]

    # 配置真的落到无头配置里了（机器人启动读的就是它）
    saved = headless_config.load()
    assert saved["feishu_app_id"] == "cli_demo_123"
    assert saved["feishu_app_secret"] == FEISHU_SECRET


def test_feishu_config_blank_keeps_and_clear_clears(monkeypatch, tmp_path, robot_root, no_adapter):
    """空串 = 不改（前端整表提交时用），__clear__ = 清空。"""
    headers = _auth(monkeypatch, tmp_path)
    client.put("/api/feishu/config", headers=headers, json={
        "app_id": "cli_demo_123", "app_secret": FEISHU_SECRET})

    res = client.put("/api/feishu/config", headers=headers, json={
        "enabled": True, "app_id": "", "app_secret": ""})
    assert res.status_code == 200, res.text
    assert res.json()["changed"] == ["enabled"], res.json()
    assert client.get("/api/feishu/status", headers=headers).json()["app_secret_set"] is True

    res = client.put("/api/feishu/config", headers=headers, json={"app_secret": "__clear__"})
    assert res.json()["changed"] == ["app_secret"]
    assert client.get("/api/feishu/status", headers=headers).json()["app_secret_set"] is False
    assert headless_config.load()["feishu_app_secret"] == ""


def test_feishu_config_rejects_junk(monkeypatch, tmp_path, robot_root, no_adapter):
    headers = _auth(monkeypatch, tmp_path)
    res = client.put("/api/feishu/config", headers=headers,
                     json={"app_secret": "a" * 600})
    assert res.status_code == 400 and "不合法" in res.json()["detail"]
    res = client.put("/api/feishu/config", headers=headers,
                     json={"app_secret": "line1\nline2"})
    assert res.status_code == 400


def test_feishu_ready_when_everything_is_in_place(monkeypatch, tmp_path, robot_root):
    """凭证齐 + 适配器装了 + 开关开了 → ready（与机器人侧 bot._feishu_ready 同一判据）。"""
    headers = _auth(monkeypatch, tmp_path)
    monkeypatch.setattr(console_ext, "_feishu_adapter_installed",
                        lambda settings, refresh=False: True)
    client.put("/api/feishu/config", headers=headers, json={
        "enabled": True, "app_id": "cli_x", "app_secret": FEISHU_SECRET,
        "verification_token": FEISHU_TOKEN})
    data = client.get("/api/feishu/status", headers=headers).json()
    assert data["ready"] is True and data["why"] == "" and data["adapter_installed"] is True

    # 缺 Verification Token：机器人根本不会注册适配器，这里也不许说"就绪"
    client.put("/api/feishu/config", headers=headers, json={"verification_token": "__clear__"})
    data = client.get("/api/feishu/status", headers=headers).json()
    assert data["ready"] is False and "Verification Token" in data["why"]


def test_feishu_credentials_reach_the_bot_startup(monkeypatch, tmp_path, robot_root, no_adapter):
    """写进控制台的飞书凭证必须能被机器人启动路径读到（否则界面"就绪"、机器人不连）。

    deploy.build_settings() 原来不映射这几个键：console_ext 里补了一层（install_settings_bridge）。
    """
    headers = _auth(monkeypatch, tmp_path)
    client.put("/api/feishu/config", headers=headers, json={
        "enabled": True, "app_id": "cli_demo_123", "app_secret": FEISHU_SECRET,
        "verification_token": FEISHU_TOKEN})

    assert getattr(deploy.build_settings, "_console_bridge", False), "设置桥没装上"
    s = deploy.build_settings()
    assert s.feishu_enabled is True
    assert s.feishu_app_id == "cli_demo_123"
    assert s.feishu_app_secret == FEISHU_SECRET

    from qbotmanager.core import bot as bot_mod

    env_text = bot_mod.build_env_text(s)
    assert "FEISHU_BOTS=" in env_text, env_text
    assert FEISHU_SECRET in env_text, "凭证要进机器人 .env —— 密钥只在这里出现，不进 HTTP 响应"


def test_feishu_install_adapter_without_runtime(monkeypatch, tmp_path, robot_root):
    """机器人运行时还没部署（缺 .venv）：ok:false + 中文原因，不是 500。"""
    headers = _auth(monkeypatch, tmp_path)
    monkeypatch.setattr(console_ext, "_bot_python", lambda settings: None)
    res = client.post("/api/feishu/install-adapter", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False and "还没部署" in body["error"]
    assert isinstance(body["log"], list) and body["log"]


def test_feishu_install_adapter_runs_and_reports_log(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    monkeypatch.setattr(console_ext, "_bot_python", lambda settings: Path(sys.executable))
    state = {"installed": False}
    monkeypatch.setattr(console_ext, "_feishu_adapter_installed",
                        lambda settings, refresh=False: state["installed"])
    seen = {}
    bot_mod = __import__("qbotmanager.core.bot", fromlist=["x"])

    def fake_ensure(settings, log=None):
        seen["python_exe"] = str(settings.python_exe)
        state["installed"] = True
        if log:
            log("飞书适配器缺失，正在自动安装（nonebot-adapter-feishu）...")
            log("飞书适配器安装完成")
        return True

    monkeypatch.setattr(bot_mod, "ensure_feishu_deps", fake_ensure)
    res = client.post("/api/feishu/install-adapter", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["adapter_installed"] is True
    assert body["needs_restart"] is True
    assert any("自动安装" in line for line in body["log"]), body["log"]
    # 用的是机器人 venv 的 python（build_settings 给的 python.exe 在 Linux 上不存在）
    assert seen["python_exe"] == str(Path(sys.executable))


def test_feishu_install_adapter_failure_reports_reason(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    monkeypatch.setattr(console_ext, "_bot_python", lambda settings: Path(sys.executable))
    monkeypatch.setattr(console_ext, "_feishu_adapter_installed",
                        lambda settings, refresh=False: False)
    bot_mod = __import__("qbotmanager.core.bot", fromlist=["x"])
    monkeypatch.setattr(bot_mod, "ensure_feishu_deps",
                        lambda settings, log=None: False)
    body = client.post("/api/feishu/install-adapter", headers=headers).json()
    assert body["ok"] is False
    assert "PyPI" in body["error"] or "pip" in body["error"], body


# ---------------------------------------------------------------- ④ 纸飞机
def test_telegram_status_says_planned(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    res = client.get("/api/telegram/status", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["supported"] is False
    assert data["planned"] is True
    assert data["why"] == "纸飞机通道还在规划中"


# ---------------------------------------------------------------- ③ QQ 官方
def test_qq_official_secret_only_returns_booleans(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    res = client.put("/api/qq/official", headers=headers, json={
        "enabled": True, "appid": "102000777", "secret": QQ_SECRET, "token": QQ_TOKEN})
    assert res.status_code == 200, res.text
    assert res.json()["needs_restart"] is True
    assert set(res.json()["changed"]) >= {"enabled", "appid", "secret", "token"}

    res = client.get("/api/qq/official", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["enabled"] is True and data["channel"] == "official"
    assert data["appid_set"] is True and data["secret_set"] is True and data["token_set"] is True
    # 密钥一个都不许出现在响应里（只回布尔）
    for secret in (QQ_SECRET, QQ_TOKEN):
        assert secret not in res.text
    assert isinstance(data["groups"], list) and isinstance(data["allowed_groups"], list)
    assert data["mode"] in ("full", "at_only", "none")

    # 空串不改、__clear__ 清空（与飞书同一套规则）
    assert client.put("/api/qq/official", headers=headers,
                      json={"secret": ""}).json()["changed"] == []
    assert client.get("/api/qq/official", headers=headers).json()["secret_set"] is True
    assert client.put("/api/qq/official", headers=headers,
                      json={"secret": "__clear__"}).json()["changed"] == ["secret"]
    assert client.get("/api/qq/official", headers=headers).json()["secret_set"] is False


def test_qq_official_mode_and_channel_semantics(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    client.put("/api/qq/official", headers=headers, json={"enabled": True})

    # 收消息模式是 q.qq.com 的设置、控制台改不了：不能悄悄当成功，也不能让整表保存失败
    res = client.put("/api/qq/official", headers=headers, json={"mode": "at_only"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode_ignored"] is True and body["changed"] == []
    assert "接收所有消息" in body["note"]
    assert client.get("/api/qq/official", headers=headers).json()["channel"] == "official"

    # mode 也是通道选择：official / onebot（enabled=false 是同一件事的另一种说法）
    assert client.put("/api/qq/official", headers=headers,
                      json={"mode": "onebot"}).json()["changed"] == ["mode"]
    assert client.get("/api/qq/official", headers=headers).json()["enabled"] is False
    body = client.put("/api/qq/official", headers=headers, json={"enabled": True}).json()
    assert body["changed"] == ["enabled"]
    assert client.get("/api/qq/official", headers=headers).json()["channel"] == "official"
    # 完全看不懂的 mode：给人话
    res = client.put("/api/qq/official", headers=headers, json={"mode": "随便写的"})
    assert res.status_code == 400 and "mode 只能是" in res.json()["detail"]


def test_qq_official_allowed_groups_round_trip(monkeypatch, tmp_path, robot_root):
    """群白名单走机器人侧 AI 配置（与 /api/ai/settings 同一份，空数组 = 不限群）。"""
    headers = _auth(monkeypatch, tmp_path)
    res = client.put("/api/qq/official", headers=headers, json={"allowed_groups": ["G1", " G2 "]})
    assert res.status_code == 200, res.text
    assert "allowed_groups" in res.json()["changed"]
    assert client.get("/api/qq/official", headers=headers).json()["allowed_groups"] == ["G1", "G2"]
    assert client.put("/api/qq/official", headers=headers,
                      json={"allowed_groups": "G1"}).status_code == 400


# ---------------------------------------------------------------- ② 日志保留
def test_logs_retention_round_trip_and_cleanup(monkeypatch, tmp_path, robot_root):
    headers = _auth(monkeypatch, tmp_path)
    home = tmp_path / "logs"
    home.mkdir(parents=True)
    (home / "headless.log").write_text("live\n", encoding="utf-8")
    old = home / "nonebot.log.2020-01-01"
    old.write_text("old" * 200, encoding="utf-8")
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))

    body = client.get("/api/logs/retention", headers=headers)
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["keep_days"] == 10, "默认留最近 10 天（老板定的）"
    assert data["usage"]["files"] == 2 and data["usage"]["bytes"] > 0
    assert "nonebot.log.2020-01-01" in data["archives"]
    assert "headless.log" not in data["archives"], "正在写的那份不算归档"

    # 读-改-写回环
    res = client.put("/api/logs/retention", headers=headers, json={"keep_days": 3})
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True and res.json()["keep_days"] == 3
    assert client.get("/api/logs/retention", headers=headers).json()["keep_days"] == 3
    assert headless_config.load()["logs_keep_days"] == 3

    # 脏值 / 越界一律 400，且不悄悄改成别的值
    for bad in (0, 366, -1, "abc", None, True, 10.5, [10]):
        res = client.put("/api/logs/retention", headers=headers, json={"keep_days": bad})
        assert res.status_code == 400, (bad, res.text)
    assert client.put("/api/logs/retention", headers=headers, json={}).status_code == 400
    assert client.get("/api/logs/retention", headers=headers).json()["keep_days"] == 3

    # 立刻清理：40 天前的归档该删，正在写的那份永不删
    res = client.post("/api/logs/cleanup-now", headers=headers)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["ok"] is True, out
    assert out["rotated"] == []
    assert out["deleted"] == ["nonebot.log.2020-01-01"], out
    assert out["bytes_freed"] > 0
    assert not old.exists()
    assert (home / "headless.log").exists(), "正在写的日志永远不删"


def test_logs_retention_survives_missing_dir(monkeypatch, tmp_path, robot_root):
    """日志目录还没有（新装机器）：读要 200、清理要 ok:true 且什么都不做。"""
    headers = _auth(monkeypatch, tmp_path)
    data = client.get("/api/logs/retention", headers=headers).json()
    assert data["keep_days"] == 10 and data["usage"]["bytes"] == 0 and data["archives"] == []
    out = client.post("/api/logs/cleanup-now", headers=headers).json()
    assert out["ok"] is True and out["deleted"] == []


# ---------------------------------------------------------------- 鉴权与回归
def test_new_endpoints_require_login(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    assert client.get("/api/feishu/status").status_code == 401
    assert client.put("/api/feishu/config", json={"enabled": True}).status_code == 401
    assert client.post("/api/feishu/install-adapter").status_code == 401
    assert client.get("/api/telegram/status").status_code == 401
    assert client.get("/api/qq/official").status_code == 401
    assert client.put("/api/qq/official", json={"enabled": True}).status_code == 401
    assert client.get("/api/logs/retention").status_code == 401
    assert client.put("/api/logs/retention", json={"keep_days": 3}).status_code == 401
    assert client.post("/api/logs/cleanup-now").status_code == 401


def test_ai_settings_field_names_unchanged(monkeypatch, tmp_path, robot_root):
    """老板点名的四个字段名不许改（前端已经按这些名字写了）。"""
    headers = _auth(monkeypatch, tmp_path)
    body = client.get("/api/ai/settings", headers=headers).json()
    assert body["image_recognition_enabled"] is True
    assert body["self_forge_enabled"] is True
    assert body["cross_platform_enabled"] is True
    assert body["identity_bindings"] == []


def test_selftest_reports_secret_rule_and_routes():
    out = console_ext.selftest()
    assert out["secret_rule"] == {
        "empty_keeps": "old", "missing_keeps": "old",
        "clear_clears": "", "value_overwrites": "new",
    }
    assert out["settings_bridge"] is True
    for path in ("/api/feishu/status", "/api/feishu/config", "/api/feishu/install-adapter",
                 "/api/telegram/status", "/api/qq/official",
                 "/api/logs/retention", "/api/logs/cleanup-now"):
        assert path in out["routes"], path


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
