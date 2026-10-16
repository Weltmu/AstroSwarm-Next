# -*- coding: utf-8 -*-
"""控制台密码登录 + 界面偏好上服务器（2026-10-07 老板两条需求）。

老板原话：
  1. "换浏览器会出现先前配置归0，登陆状态出问题"  → 界面偏好要存服务器（ui_* 键）
  2. "再给程序加一个密码登录的功能（防止其他人篡改数据，有 15 天免密码登录验证）"
     → console_session.py + /api/console/*

这份用例盯死三件事：
  · 界面偏好：类型/值域/枚举校验，密钥类字段绝不混进去；
  · 控制台密码：不落明文、设过之后 setup 被拒、限流、会话有效期、作废语义；
  · 保护范围：没会话时受保护接口 401，而 /api/status 与 /api/console/* 仍然可达。
"""
import inspect
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles

from astroswarm_linux import api, console_session, headless_config, platform_info
from astroswarm_linux.api import app

client = TestClient(app)

REMEMBER = console_session.REMEMBER_SECONDS
SHORT = console_session.DEFAULT_SECONDS
PW = "Str0ngPass!word"


def _isolate(monkeypatch, tmp_path):
    """把 config_home 指到临时目录：鉴权文件/会话文件都写到那里去。"""
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path)
    console_session.clear_rate_limit()
    return tmp_path


# ══════════════════════════════════════════════ 1. 界面偏好（ui_*）上服务器

def test_ui_prefs_are_safe_keys_and_have_no_secrets():
    """ui_* 要能被 /api/config 读写，但绝不能是密钥类字段（否则会被掩码/误传）。"""
    api_secrets = {"qq_app_secret", "qq_app_token", "ai_api_key", "vision_api_key"}
    keys = set(headless_config.UI_PREF_KEYS)
    assert keys, "界面偏好白名单不能是空的"
    assert keys <= set(headless_config.SAFE_KEYS), "界面偏好必须进 SAFE_KEYS 才读写得到"
    assert not (keys & api_secrets), "界面偏好里绝不能混进密钥字段"
    assert all(k.startswith("ui_") for k in keys), "统一 ui_ 前缀，避免和桌面端 settings 键撞名"


def test_ui_prefs_type_range_and_enum_validation():
    hc = headless_config
    # 数值越界按夹取处理（滑杆拖到边界是正常操作，不该报错）
    assert hc.ui_pref_value("ui_bg_mask", 999) == 100
    assert hc.ui_pref_value("ui_bg_mask", -3) == 0
    assert hc.ui_pref_value("ui_bg_opacity", 1000) == 95
    assert hc.ui_pref_value("ui_bg_opacity", 1) == 10
    assert hc.ui_pref_value("ui_bg_bright", 500) == 100
    assert hc.ui_pref_value("ui_bg_bright", -500) == -100
    # 类型错 → 明确拒绝，不静默改成默认值
    with pytest.raises(ValueError):
        hc.ui_pref_value("ui_animations", "yes")
    with pytest.raises(ValueError):
        hc.ui_pref_value("ui_bg_mask", "宽")
    with pytest.raises(ValueError):
        hc.ui_pref_value("ui_bg_path", 123)
    # 枚举不认识 → 拒绝
    with pytest.raises(ValueError):
        hc.ui_pref_value("ui_theme", "并不存在的主题")
    # 文案超长 → 拒绝（不是截断，避免前端以为存进去了完整路径）
    with pytest.raises(ValueError):
        hc.ui_pref_value("ui_bg_path", "x" * 2000)
    # 合法值原样通过
    assert hc.ui_pref_value("ui_theme", "深空终端") == "深空终端"
    assert hc.ui_pref_value("ui_animations", False) is False
    assert hc.ui_pref_value("ui_bg_mask", 42) == 42


def test_read_path_repairs_hand_edited_config(tmp_path, monkeypatch):
    """config.json 被手工改坏时，读出来也要是合法值（不能把越界值灌进前端）。"""
    _isolate(monkeypatch, tmp_path)
    cfg = {
        "ui_bg_mask": 500,          # 越界 → 夹到 100
        "ui_theme": "乱写的主题",     # 枚举不认识 → 回落默认
        "ui_animations": "no",       # 类型错 → 回落默认
        "ui_bg_opacity": 70,         # 合法 → 原样
    }
    changed = headless_config.validate_ui_prefs(cfg)
    assert set(changed) == {"ui_bg_mask", "ui_theme", "ui_animations"}
    assert cfg["ui_bg_mask"] == 100
    assert cfg["ui_theme"] == headless_config.UI_PREF_DEFAULTS["ui_theme"]
    assert cfg["ui_animations"] is True
    assert cfg["ui_bg_opacity"] == 70


def test_ui_prefs_roundtrip_through_api(tmp_path, monkeypatch):
    """走真实接口：写进去、读回来一致（证明"换浏览器也在"这条链路是通的）。"""
    _isolate(monkeypatch, tmp_path)
    headless_config.save({**headless_config.load(), "account_token": "t"})
    headers = {"Authorization": "Bearer t"}
    payload = {
        "ui_bg_enabled": True,
        "ui_bg_path": "/data/wall.mp4",
        "ui_bg_mask": 55,
        "ui_bg_opacity": 80,
        "ui_bg_bright": -12,
        "ui_bg_autoplay": True,
        "ui_bg_loop": True,
        "ui_animations": False,
        "ui_theme": "深空终端",
        "ui_accent": "赛博青",
        "ui_glass": "液态玻璃",
    }
    res = client.put("/api/config", headers=headers, json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    for key, want in payload.items():
        assert data[key] == want, (key, data.get(key), want)
    # 落盘了（不是只在响应里）
    saved = headless_config.load()
    for key, want in payload.items():
        assert saved[key] == want, key
    # 越界值走接口也不报错，落库的是夹取后的值
    res2 = client.put("/api/config", headers=headers, json={"ui_bg_mask": 900})
    assert res2.status_code == 200 and res2.json()["ui_bg_mask"] == 100
    # 类型错走接口要 400（不是 500，也不是静默改默认）
    res3 = client.put("/api/config", headers=headers, json={"ui_animations": "yes"})
    assert res3.status_code == 400, res3.text


# ══════════════════════════════════════════════ 2. 控制台密码

def test_password_never_stored_in_plaintext(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert not console_session.is_password_set(), "全新机器应该是 need_setup"
    console_session.set_password(PW)
    auth_file, sess_file = console_session._paths()
    raw = auth_file.read_text(encoding="utf-8")
    assert PW not in raw, "密码明文绝不能落盘"
    rec = json.loads(raw)
    assert rec["password"]["algo"] == "scrypt"
    assert rec["password"]["salt"], "必须是每实例随机 salt"
    assert rec["password"]["hash"] and len(rec["password"]["hash"]) == 64
    # 同一密码两次设置 → 不同 salt → 不同哈希（防彩虹表）
    first = rec["password"]["hash"]
    console_session.set_password("Another!Pass9", require_old=PW)
    second = json.loads(auth_file.read_text(encoding="utf-8"))["password"]
    assert second["hash"] != first


def test_session_token_stored_hashed_and_expiry_matches_remember(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    long_sess = console_session.create_session(remember=True)
    short_sess = console_session.create_session(remember=False)
    _, sess_file = console_session._paths()
    raw = sess_file.read_text(encoding="utf-8")
    assert long_sess["token"] not in raw, "会话 token 只存 sha256，不能存原文"
    # 有效期：服务端签的 expires_at ≈ now + 15 天 / 12 小时（±5 分钟）
    now = time.time()
    assert abs((long_sess["expires_at"] - now) - REMEMBER) < 300, long_sess["expires_at"] - now
    assert abs((short_sess["expires_at"] - now) - SHORT) < 300, short_sess["expires_at"] - now
    assert REMEMBER == 15 * 24 * 3600 and SHORT == 12 * 3600


def test_expired_session_is_rejected(tmp_path, monkeypatch):
    """过期只由服务端判定：把 expires_at 改到过去，同一个 token 立刻失效。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    sess = console_session.create_session(remember=False)
    assert console_session.verify(sess["token"]) is not None
    _, sess_file = console_session._paths()
    data = json.loads(sess_file.read_text(encoding="utf-8"))
    for rec in data["sessions"].values():
        rec["expires_at"] = time.time() - 1
    sess_file.write_text(json.dumps(data), encoding="utf-8")
    assert console_session.verify(sess["token"]) is None, "过期会话必须失效"


def test_weak_passwords_rejected_with_chinese_reason():
    for bad in ("short", "12345678", "11111111", "abcdefgh", "hgfedcba",
                "password", "123456789", "qwertyuiop", "12345678a"[0:8]):
        reason = console_session.password_rules(bad)
        if bad == "12345678a"[0:8]:      # = "12345678"，已在上面覆盖
            continue
        assert reason, f"{bad} 太弱，必须拒绝"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in reason), f"{bad}: 原因要是中文：{reason}"
    assert console_session.password_rules(PW) == ""


def test_ui_prefs_endpoint_needs_only_console_session(tmp_path, monkeypatch):
    """界面偏好只认控制台会话，**不要**星群账号。

    老板报的 bug 正是这个组合：新浏览器打开 → 控制台密码过了、账号那层没登录 →
    如果偏好挂在 /api/config 上就 401 → 界面又回默认值，等于没修。
    所以 /api/ui/prefs 必须只要 X-Console-Token 就能读写。
    """
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    headers = {"X-Console-Token": tok}          # 刻意**不带** Authorization
    # 未登录账号也能读（初始是空的，只有 ok）
    res = client.get("/api/ui/prefs", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}
    # 未登录账号也能写
    payload = {"ui_theme": "深空终端", "ui_accent": "赛博青", "ui_bg_mask": 33,
               "ui_animations": False, "ui_glass": "液态玻璃"}
    w = client.put("/api/ui/prefs", headers=headers, json=payload)
    assert w.status_code == 200, w.text
    for k, v in payload.items():
        assert w.json()[k] == v
    # 读回来一致（换浏览器也读得到同一个值）
    r2 = client.get("/api/ui/prefs", headers=headers).json()
    for k, v in payload.items():
        assert r2[k] == v
    # 没控制台会话 → 401（这是安全边界）
    assert client.get("/api/ui/prefs").status_code == 401
    assert client.put("/api/ui/prefs", json=payload).status_code == 401


def test_ui_prefs_endpoint_validates_and_whitelists(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    h = {"X-Console-Token": tok}
    # 未知键直接 400（不像 /api/config 那样静默忽略）：写错键名要立刻看得见
    bad = client.put("/api/ui/prefs", headers=h, json={"ui_not_a_key": 1})
    assert bad.status_code == 400 and "不认识" in bad.json()["detail"]
    # 类型错 400
    assert client.put("/api/ui/prefs", headers=h,
                      json={"ui_animations": "yes"}).status_code == 400
    # 枚举不认识 400
    assert client.put("/api/ui/prefs", headers=h,
                      json={"ui_theme": "不存在的主题"}).status_code == 400
    # 数值越界按夹取（滑杆拖到边界是正常操作）
    ok = client.put("/api/ui/prefs", headers=h, json={"ui_bg_mask": 900})
    assert ok.status_code == 200 and ok.json()["ui_bg_mask"] == 100
    # 空 body 400
    assert client.put("/api/ui/prefs", headers=h, json={}).status_code == 400
    # 绝不回任何密钥类字段
    body = client.get("/api/ui/prefs", headers=h).json()
    assert not (set(body) & {"qq_app_secret", "qq_app_token", "ai_api_key",
                             "vision_api_key", "account_token"})


def test_ui_prefs_endpoint_round_trips_through_config_file(tmp_path, monkeypatch):
    """/api/ui/prefs 写的就是 config.json 里那批 ui_* 键（和 /api/config 同一份存储）。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    client.put("/api/ui/prefs", headers={"X-Console-Token": tok},
               json={"ui_theme": "瑞士极简"})
    assert headless_config.load()["ui_theme"] == "瑞士极简"
    # 换一个会话（= 换浏览器）读到的还是同一个值
    tok2 = console_session.create_session(remember=True)["token"]
    got = client.get("/api/ui/prefs", headers={"X-Console-Token": tok2}).json()
    assert got["ui_theme"] == "瑞士极简"


# ══════════════════════════════════════════════ 3. 接口行为与保护范围

def test_setup_only_works_once(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    res = client.post("/api/console/setup", json={"password": PW})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["token"], body
    assert body["expires_at"] - time.time() > 14 * 86400, "首次设置就给 15 天"
    # 设过之后再 setup 一律拒绝（否则等于没门槛）
    again = client.post("/api/console/setup", json={"password": "Other!Pass9"})
    assert again.status_code == 400, again.text
    assert "已经设置过" in again.json()["detail"]
    # 而且密码没被改掉：原密码仍然能登录
    ok = client.post("/api/console/login", json={"password": PW, "remember": False})
    assert ok.status_code == 200, ok.text


def test_setup_rejects_weak_password(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    res = client.post("/api/console/setup", json={"password": "12345678"})
    assert res.status_code == 400
    assert "常见" in res.json()["detail"], res.json()["detail"]
    # 纯数字但不成顺子，也要拒（理由不同：别只用数字）
    console_session.clear_rate_limit()
    res2 = client.post("/api/console/setup", json={"password": "48273915"})
    assert res2.status_code == 400
    assert "数字" in res2.json()["detail"], res2.json()["detail"]


def test_login_wrong_password_401_and_rate_limited(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    codes = [
        client.post("/api/console/login", json={"password": "wrong-one"}).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5, codes
    assert codes[5] == 429, codes
    assert "尝试过多" in client.post(
        "/api/console/login", json={"password": "wrong-one"}).json()["detail"]
    console_session.clear_rate_limit()


def test_login_rate_limit_is_per_ip(tmp_path, monkeypatch):
    """一个 IP 打满不能把机主自己堵在门外（沿用账号登录那套口径）。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    attacker = TestClient(app, client=("203.0.113.9", 40000))
    owner = TestClient(app, client=("198.51.100.1", 40000))
    for _ in range(6):
        attacker.post("/api/console/login", json={"password": "nope"})
    assert owner.post("/api/console/login",
                      json={"password": PW, "remember": False}).status_code == 200
    console_session.clear_rate_limit()


def test_logout_invalidates_token_immediately(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    tok = client.post("/api/console/login",
                      json={"password": PW, "remember": True}).json()["token"]
    assert console_session.verify(tok) is not None
    out = client.post("/api/console/logout", headers={"X-Console-Token": tok})
    assert out.status_code == 200 and out.json()["removed"] is True
    assert console_session.verify(tok) is None, "退出后原 token 必须立刻失效"
    # 再拿这个 token 访问受保护接口 → 401
    assert client.get("/api/config", headers={"X-Console-Token": tok}).status_code == 401


def test_logout_does_not_log_out_account(tmp_path, monkeypatch):
    """「锁定」只锁控制台，不该顺手把星群账号也退了（老板要的是锁屏）。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    headless_config.save({**headless_config.load(), "account_token": "acc-tok"})
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    client.post("/api/console/logout", headers={"X-Console-Token": tok})
    assert headless_config.load().get("account_token") == "acc-tok"


def test_change_password_revokes_other_sessions(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    mine = client.post("/api/console/login", json={"password": PW}).json()["token"]
    other = console_session.create_session(remember=True)
    assert console_session.verify(other["token"]) is not None
    res = client.post("/api/console/password",
                      headers={"X-Console-Token": mine},
                      json={"old": PW, "new": "Brand!New9Pass"})
    assert res.status_code == 200, res.text
    assert res.json()["revoked_others"] >= 1
    assert console_session.verify(mine) is not None, "当前会话要留着，别把自己踢下线"
    assert console_session.verify(other["token"]) is None, "其它会话必须失效"
    # 旧密码不能再用，新密码可以
    console_session.clear_rate_limit()
    assert client.post("/api/console/login", json={"password": PW}).status_code == 401
    assert client.post("/api/console/login",
                       json={"password": "Brand!New9Pass"}).status_code == 200


def test_change_password_requires_login_and_old_password(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    # 没会话 → 401
    assert client.post("/api/console/password",
                       json={"old": PW, "new": "Brand!New9Pass"}).status_code == 401
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    # 旧密码不对 → 400，且密码没被改
    bad = client.post("/api/console/password", headers={"X-Console-Token": tok},
                      json={"old": "wrong-old", "new": "Brand!New9Pass"})
    assert bad.status_code == 400
    console_session.clear_rate_limit()
    assert client.post("/api/console/login", json={"password": PW}).status_code == 200


def test_session_endpoint_reports_setup_and_auth_state(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    first = client.get("/api/console/session").json()
    assert first == {"ok": True, "need_setup": True, "authed": False,
                     "expires_at": 0, "remember": False, "device_label": ""}
    console_session.set_password(PW)
    second = client.get("/api/console/session").json()
    assert second["need_setup"] is False and second["authed"] is False
    tok = client.post("/api/console/login",
                      json={"password": PW, "remember": True}).json()["token"]
    third = client.get("/api/console/session",
                       headers={"X-Console-Token": tok}).json()
    assert third["authed"] is True and third["remember"] is True
    assert third["expires_at"] - time.time() > 14 * 86400
    assert third["device_label"], "要给一个可读的设备名"
    # 响应体里不能出现任何 token
    assert tok not in json.dumps(third)


def test_protected_endpoints_require_console_session(tmp_path, monkeypatch):
    """没会话时受保护接口 401；/api/status 与 /api/console/* 仍然可达。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    protected = [
        ("get", "/api/config", None),
        ("get", "/api/auth/status", None),
        ("get", "/api/services/status", None),
        ("put", "/api/config", {"port": 7860}),
        ("get", "/api/plugins/installed", None),
        ("get", "/api/messages/list", None),
    ]
    for method, path, body in protected:
        res = getattr(client, method)(path, **({"json": body} if body else {}))
        assert res.status_code == 401, (method, path, res.status_code)
        assert res.json().get("console_locked") is True, (path, res.text)
    # 例外一：/api/status 不受这一层影响（未登录也回 ok/version）
    assert client.get("/api/status").status_code == 200
    # 例外二：登录流程本身当然要能访问
    assert client.get("/api/console/session").status_code == 200
    assert client.post("/api/console/login",
                       json={"password": PW}).status_code == 200


def test_console_session_unlocks_protected_endpoint(tmp_path, monkeypatch):
    """带上有效会话后，受保护接口照常工作（两层鉴权不打架）。

    注意 /api/config 需要**两层都过**：控制台会话（本模块）+ 星群账号 token（原有）。
    这条正是要证明"加了一层不会把原来那层弄坏"。
    """
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    headless_config.save({**headless_config.load(), "account_token": "acc-tok"})
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    res = client.get("/api/config", headers={"X-Console-Token": tok,
                                             "Authorization": "Bearer acc-tok"})
    assert res.status_code == 200, res.text
    # 只有控制台会话、没有账号 token → 仍然按账号层的口径 401（两层独立）
    only_console = client.get("/api/config", headers={"X-Console-Token": tok})
    assert only_console.status_code == 401
    assert only_console.json()["detail"] == "请先登录"
    assert only_console.json().get("console_locked") is not True
    # 账号那一层仍然独立生效：没有账号 token / 没登录时，auth/status 仍按账号口径回"未登录"
    fresh = TestClient(app, client=("198.51.100.7", 40000))
    anon = fresh.get("/api/auth/status", headers={"X-Console-Token": tok})
    assert anon.status_code == 200 and anon.json()["logged_in"] is False


def test_no_password_set_means_no_lockout(tmp_path, monkeypatch):
    """全新机器还没设密码时不能被关在门外 —— 否则连"设置密码"那一屏都进不去。"""
    _isolate(monkeypatch, tmp_path)
    assert not console_session.is_password_set()
    assert client.get("/api/config").status_code in (200, 401)
    # /api/config 这里仍可能因账号层回 401，但**不能**是 console_locked
    res = client.get("/api/config")
    assert res.json().get("console_locked") is not True


def test_console_token_also_accepted_as_query_param(tmp_path, monkeypatch):
    """浏览器不给带 header 的两个例外（日志流 / 二维码 <img>）走 ?token=。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    assert client.get("/api/status?token=%s" % tok).status_code == 200
    # 受保护接口用 ?token= 也能过这一层（账号层另算）
    res = client.get("/api/config?token=%s" % tok)
    assert res.json().get("console_locked") is not True
    # 假 token 过不去
    bad = client.get("/api/config?token=deadbeef")
    assert bad.status_code == 401 and bad.json().get("console_locked") is True


def test_broken_auth_file_fails_closed(tmp_path, monkeypatch):
    """鉴权文件读坏时**宁可进不去**，也不能当成"没设过密码"把门敞开。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    console_session._paths()[0].write_text("{ 这不是 JSON", encoding="utf-8")
    assert console_session.is_password_set() is True
    res = client.get("/api/config")
    assert res.status_code == 401, "坏文件不能变成敞开的大门"


def test_login_failure_log_has_no_password(tmp_path, monkeypatch, caplog):
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    secret = "Sup3rSecret!pw"
    with caplog.at_level("WARNING"):
        client.post("/api/console/login", json={"password": secret})
    text = caplog.text
    assert secret not in text, "日志里绝不能出现密码"
    assert PW not in text
    console_session.clear_rate_limit()


# ══════════════════════════════════════════════ 4. /api/ui/prefs 的保护范围（2026-10-10 复核）
# 老板 2026-10-10 让查的一条："GET /api/ui/prefs 在没有控制台会话时返回 200（应该 401）"。
# 结论：中间件**覆盖到了**它，200 只可能出现在 need_setup（还没设过密码）的机器上 ——
# 那是首次部署的引导口子，写死在 console_session_guard 里（不设密码就没人能证明自己是机主，
# 锁死会连"设置控制台密码"那一屏都进不去）。下面两条把"两种状态"分别钉死。

def test_ui_prefs_is_401_once_password_is_set(tmp_path, monkeypatch):
    """设过密码之后：/api/ui/prefs 未带会话必须 401（读和写都不漏）。"""
    _isolate(monkeypatch, tmp_path)
    console_session.set_password(PW)
    assert client.get("/api/ui/prefs").status_code == 401
    assert client.put("/api/ui/prefs", json={"ui_theme": "深空终端"}).status_code == 401
    g = client.get("/api/ui/prefs")
    assert g.json().get("console_locked") is True, \
        "401 要带 console_locked，前端才知道该弹密码屏而不是账号弹窗"
    # 假 token 同样过不去
    fake = client.get("/api/ui/prefs", headers={"X-Console-Token": "deadbeef"})
    assert fake.status_code == 401
    # 会话过期后也立刻 401（服务端说了算）
    tok = client.post("/api/console/login", json={"password": PW}).json()["token"]
    assert client.get("/api/ui/prefs",
                      headers={"X-Console-Token": tok}).status_code == 200
    console_session.revoke(tok)
    assert client.get("/api/ui/prefs",
                      headers={"X-Console-Token": tok}).status_code == 401


def test_ui_prefs_open_window_before_setup_leaks_nothing_sensitive(tmp_path, monkeypatch):
    """**还没设过密码**时 /api/ui/prefs 允许匿名读（首次部署的引导口子）。

    这是有意为之，代价是"首设密码前同局域网可读界面偏好"；换来的是开箱即用。
    所以这条用例盯的是**代价的上限**：这个窗口期里读到的也必须只有界面键，
    一个密钥类字段、一个会话 token 都不能有。
    """
    _isolate(monkeypatch, tmp_path)
    assert not console_session.is_password_set()
    # 口子确实开着（200），且不是被账号层放行的 —— 它压根不要账号
    res = client.get("/api/ui/prefs")
    assert res.status_code == 200, res.text
    body = res.json()
    secrets = {"qq_app_secret", "qq_app_token", "ai_api_key", "vision_api_key",
               "account_token", "console_token", "token", "password"}
    assert not (set(body) & secrets), f"引导窗口期泄漏了敏感键：{sorted(set(body) & secrets)}"
    # 回来的每个键都必须是我们已知的界面偏好键
    assert set(body) <= (set(headless_config.UI_PREF_KEYS) | {"ok"}), \
        f"引导窗口期多回了计划外的键：{sorted(set(body) - set(headless_config.UI_PREF_KEYS) - {'ok'})}"
    # 就算 config.json 里塞了密钥字段，这条接口也不会带出来
    cfg = headless_config.load()
    cfg["qq_app_secret"] = "SHOULD-NOT-LEAK"
    cfg["account_token"] = "SHOULD-NOT-LEAK-TOO"
    cfg["ui_theme"] = "瑞士极简"
    headless_config.save(cfg)
    body2 = client.get("/api/ui/prefs").json()
    assert body2["ui_theme"] == "瑞士极简", "界面键要正常读到（不然首屏没样式）"
    assert "SHOULD-NOT-LEAK" not in json.dumps(body2, ensure_ascii=False)
    assert "account_token" not in body2 and "qq_app_secret" not in body2


# ══════════════════════════════════════════════ 5. 静态产物的缓存策略（2026-10-10 老板报的 bug）
# 老板原话："我用以前浏览器打开也没显示登录面啊"。
# 根因：starlette 的 StaticFiles 只发 etag/last-modified，**一个 Cache-Control 都不发**，
# 浏览器于是走启发式缓存，升级后 index.html 还是旧的 → 旧 HTML 指向旧 bundle →
# 新界面（含密码屏）压根没被加载。修法见 api.ConsoleStaticFiles。

def _fake_console_dist(tmp_path):
    """造一份最小 console-dist：入口 HTML + 带哈希的 assets + 一个不带哈希的图标。"""
    dist = tmp_path / "console-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><script type=\"module\" "
        "src=\"/assets/index-BwGvgdAR.js\"></script></body></html>", encoding="utf-8")
    (dist / "assets" / "index-BwGvgdAR.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "assets" / "index-7NLoSNam.css").write_text("body{}", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x01")
    (dist / "logo.png").write_bytes(b"\x89PNG")
    return dist


def _static_client(dist):
    """把 ConsoleStaticFiles 挂到一个干净 app 上（真实 app 在导入时就挂好了，
    测试里没法换目录，所以这里单独搭一个同款挂载）。"""
    fresh = FastAPI()
    fresh.mount("/", api.ConsoleStaticFiles(directory=str(dist), html=True), name="console")
    return TestClient(fresh)


def test_index_html_is_never_heuristically_cached(tmp_path):
    """入口 HTML（/、/index.html）必须 no-cache + must-revalidate。"""
    c = _static_client(_fake_console_dist(tmp_path))
    for path in ("/", "/index.html"):
        res = c.get(path)
        assert res.status_code == 200, f"{path} → {res.status_code}"
        assert "text/html" in res.headers["content-type"]
        cc = res.headers.get("cache-control", "")
        assert "no-cache" in cc and "must-revalidate" in cc, f"{path} 的响应头是 {cc!r}"


def test_html_keeps_etag_so_revalidation_is_a_304(tmp_path):
    """no-cache 不等于"每次重下"：配合 etag，没变就是 304（省流量又不会用旧界面）。"""
    c = _static_client(_fake_console_dist(tmp_path))
    first = c.get("/")
    etag = first.headers.get("etag")
    assert etag, "静态响应要有 etag，否则 no-cache 会退化成每次全量重下"
    again = c.get("/", headers={"If-None-Match": etag})
    assert again.status_code == 304
    # 304 上也要带 no-cache：否则浏览器可能把这次的"没变"结论缓存住
    assert "no-cache" in again.headers.get("cache-control", "")


def test_hashed_assets_stay_long_cached(tmp_path):
    """带内容哈希的 assets/* 仍然长缓存（名字一变就换 URL，永远不会读到旧内容）。"""
    c = _static_client(_fake_console_dist(tmp_path))
    for path in ("/assets/index-BwGvgdAR.js", "/assets/index-7NLoSNam.css"):
        res = c.get(path)
        assert res.status_code == 200, f"{path} → {res.status_code}"
        cc = res.headers.get("cache-control", "")
        assert "max-age=31536000" in cc and "immutable" in cc, f"{path} 的响应头是 {cc!r}"
        assert "no-cache" not in cc, f"{path} 不该被 no-cache 掉（哈希名可长缓存）"


def test_unhashed_static_files_are_not_long_cached(tmp_path):
    """没带哈希的静态文件（favicon / logo 这类）不能长缓存：升级后可能被替换。"""
    c = _static_client(_fake_console_dist(tmp_path))
    for path in ("/favicon.ico", "/logo.png"):
        res = c.get(path)
        assert res.status_code == 200, f"{path} → {res.status_code}"
        cc = res.headers.get("cache-control", "")
        assert "no-cache" in cc, f"{path} 是「可变 URL」，不该长缓存，实际是 {cc!r}"
        assert "immutable" not in cc


def test_api_responses_are_no_store(tmp_path, monkeypatch):
    """/api/** 一律 no-store：接口回的是此刻的服务器状态，缓存住界面就会显示过期值。"""
    _isolate(monkeypatch, tmp_path)
    # 未设密码（引导窗口）与已设密码两条路径都要带上
    for path in ("/api/status", "/api/console/session", "/api/ui/prefs"):
        res = client.get(path)
        assert res.headers.get("cache-control") == "no-store", \
            f"{path} 的 cache-control 是 {res.headers.get('cache-control')!r}"
    console_session.set_password(PW)
    res = client.get("/api/ui/prefs")
    assert res.status_code == 401
    assert res.headers.get("cache-control") == "no-store", \
        "401 也不能被缓存（否则会一直显示密码屏）"
    console_session.clear_rate_limit()


def test_console_mount_uses_the_caching_static_class():
    """接线检查：真实 app 挂的必须是 ConsoleStaticFiles，不是原版 StaticFiles。

    挂载发生在 import 期、目录由 CONSOLE_DIST 决定，测试里换不了目录，
    所以这里查"代码里那行挂载"确实用了新类（真正跑起来的行为由上面的
    _static_client 用例 + 部署机上的 curl -I 复验）。
    """
    src = (Path(__file__).resolve().parents[1] / "src" / "astroswarm_linux"
           / "api.py").read_text(encoding="utf-8")
    assert "class ConsoleStaticFiles(StaticFiles)" in src
    assert "ConsoleStaticFiles(directory=str(CONSOLE_DIST), html=True)" in src
    raw_mount = src.replace("ConsoleStaticFiles(directory=str(CONSOLE_DIST)", "")
    assert "StaticFiles(directory=str(CONSOLE_DIST)" not in raw_mount, \
        "挂载点不能还用原版 StaticFiles（那样又没有 Cache-Control 了）"
    # 类本身的行为真的对：有 async 的 get_response 覆盖（不是只有个空壳名字）。
    # ⚠ 必须 async —— starlette 1.6 的父类方法是协程，同步覆盖会把"未 await 的协程"
    #   当响应返回，静态资源整片崩（这条是实测踩到后补的）。
    assert "async def get_response(self, path: str, scope)" in src
    assert inspect.iscoroutinefunction(api.ConsoleStaticFiles.get_response)
    assert hasattr(api.ConsoleStaticFiles, "get_response")
    assert issubclass(api.ConsoleStaticFiles, StaticFiles)

