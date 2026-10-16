# -*- coding: utf-8 -*-
"""控制台"邮箱 + 验证码"登录（2026-10-06 加）：接口契约与安全。

老板要求控制台登录与官网一致（弹窗里能发码），所以补了：
  POST /api/auth/send-code  {email}          → 透传账号服务 /api/account/send-code
  POST /api/auth/login      {email, code}    → 透传 code 字段（密码仍可用）

这条用例锁住：①发码要先过限流（不能被拿来刷）；②邮箱格式不对直接 400；
③login 会把 code 原样带给账号服务；④两个接口都不回显任何 token/密钥。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astroswarm_linux import auth, headless_config, platform_info  # noqa: E402
from astroswarm_linux.api import app  # noqa: E402

client = TestClient(app)

@pytest.fixture(autouse=True)
def _no_account_write(monkeypatch):
    """用例里别真去写本机账号文件（会落到用户目录）。"""
    monkeypatch.setattr(auth, "_store_account", lambda acc, data: None)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """登录/发码/注册共用按 IP 限流：用例连打会被自己的限流挡住（429），这里关掉它。

    限流本身另有覆盖（登录接口那套），这里的用例只关心请求体形状与校验。
    """
    from astroswarm_linux import api as api_mod

    monkeypatch.setattr(api_mod, "_login_rate_limit", lambda *a, **k: None)


def test_send_code_rejects_bad_email(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    # 故意不桩掉 auth.send_code：邮箱校验就在真实现里，桩掉就测不到
    called = []
    monkeypatch.setattr(auth, "_post",
                        lambda *a, **k: called.append(a) or {"ok": True})
    r = client.post("/api/auth/send-code", json={"email": "不是邮箱"})
    assert r.status_code == 400, r.text
    assert "邮箱" in r.text or "验证码" in r.text
    assert called == [], "格式不对不该真去调账号服务"


def test_send_code_passes_through(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    seen = {}

    def fake(email, client_ip=""):
        seen["email"] = email
        seen["ip"] = client_ip
        return {"ok": True, "sent": True}

    monkeypatch.setattr(auth, "send_code", fake)
    r = client.post("/api/auth/send-code", json={"email": "a@b.com"})
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    assert seen["email"] == "a@b.com"


def test_login_with_code_only_gets_plain_chinese_error(monkeypatch, tmp_path):
    """只有验证码、没密码的登录请求 → 直接给人话。

    账号服务的登录只认"邮箱 + 密码"（验证码那条路是注册）。老板实测踩过：
    旧界面拿 {email, code} 登录，账号服务回一坨 pydantic 报错，看不懂。
    """
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    called = []
    monkeypatch.setattr(auth, "login", lambda *a, **k: called.append(a) or {"ok": True})
    r = client.post("/api/auth/login", json={"email": "a@b.com", "code": "123456"})
    assert r.status_code == 400, r.text
    assert "注册" in r.text and "密码" in r.text, r.text
    assert called == [], "这种请求不该转给账号服务"


def test_login_still_supports_password(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    seen = {}
    monkeypatch.setattr(auth, "login",
                        lambda email, password="", client_ip="", code="":
                        (seen.update({"p": password, "c": code}) or {"ok": True, "token": "T"}))
    r = client.post("/api/auth/login", json={"email": "a@b.com", "password": "pw12345678"})
    assert r.status_code == 200, r.text
    assert seen["p"] == "pw12345678" and seen["c"] == "", seen


def test_auth_module_login_body_shape(monkeypatch):
    """auth.login 只在有 code 时才带 code 字段（账号服务按字段判断登录方式）。"""
    sent = {}
    monkeypatch.setattr(auth, "_post", lambda path, body, token=None, client_ip="":
                        (sent.update({"path": path, "body": body}) or
                         {"ok": True, "email": body.get("email"), "token": "T"}))
    monkeypatch.setattr(headless_config, "load", lambda: {})
    monkeypatch.setattr(headless_config, "save", lambda cfg: None)

    auth.login("a@b.com", "", code="654321")
    assert sent["path"] == "/api/account/login"
    assert sent["body"]["code"] == "654321"

    auth.login("a@b.com", "pw12345678")
    assert "code" not in sent["body"], "没传码时不该塞空 code"


def test_code_login_does_not_send_empty_password(monkeypatch):
    """验证码登录**不能**带空 password：账号服务对它 min_length=1，会回 pydantic 报错。

    老板实测踩到的原文：登录失败：[{'type': 'string_too_short', 'loc': ['body','password']…]
    """
    sent = {}
    monkeypatch.setattr(auth, "_post", lambda path, body, token=None, client_ip="":
                        (sent.update({"body": dict(body)}) or {"ok": True, "token": "T"}))
    monkeypatch.setattr(headless_config, "load", lambda: {})
    monkeypatch.setattr(headless_config, "save", lambda cfg: None)

    auth.login("a@b.com", "", code="654321")
    assert "password" not in sent["body"], f"验证码登录不许带 password：{sent['body']}"
    assert sent["body"]["code"] == "654321"

    auth.login("a@b.com", "pw12345678")
    assert "code" not in sent["body"] and sent["body"]["password"] == "pw12345678"


def test_login_without_password_and_code_fails_locally(monkeypatch):
    """两个都不填：本地就报中文错，不要发出去让人看不懂。"""
    monkeypatch.setattr(auth, "_post", lambda *a, **k: pytest.fail("不该发请求"))
    with pytest.raises(RuntimeError) as err:
        auth.login("a@b.com")
    assert "密码" in str(err.value) and "验证码" in str(err.value)

def test_register_passes_through(monkeypatch, tmp_path):
    """注册走 /api/account/register（账号服务真正的验证码入口），成功后落盘 token。"""
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    seen = {}
    monkeypatch.setattr(auth, "_post", lambda path, body, token=None, client_ip="":
                        (seen.update({"path": path, "body": dict(body)}) or
                         {"ok": True, "email": body["email"], "token": "T2"}))
    r = client.post("/api/auth/register",
                    json={"email": "a@b.com", "code": "123456", "password": "pw12345678"})
    assert r.status_code == 200, r.text
    assert seen["path"] == "/api/account/register"
    assert seen["body"]["code"] == "123456" and seen["body"]["password"] == "pw12345678"


def test_register_validates_locally(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "cfg")
    monkeypatch.setattr(auth, "_post", lambda *a, **k: pytest.fail("不该发请求"))
    for body in ({"email": "bad", "code": "123456", "password": "pw12345678"},
                 {"email": "a@b.com", "code": "12", "password": "pw12345678"},
                 {"email": "a@b.com", "code": "123456", "password": "short"}):
        r = client.post("/api/auth/register", json=body)
        assert r.status_code == 400 and "注册失败" in r.text, (body, r.text)

def test_login_or_register_prefers_login(monkeypatch):
    """密码对就直接登录，不去碰注册（mode=login）。"""
    calls = []
    monkeypatch.setattr(auth, "login", lambda e, p="", client_ip="", code="":
                        (calls.append("login") or {"ok": True, "token": "T"}))
    monkeypatch.setattr(auth, "register", lambda *a, **k: (calls.append("register") or {}))
    out = auth.login_or_register("a@b.com", "pw12345678", code="123456")
    assert out["mode"] == "login" and calls == ["login"], (out, calls)


def test_login_or_register_falls_back_to_register(monkeypatch):
    """登录失败 + 带验证码 → 自动注册（mode=register），密码用用户填的。"""
    seen = {}

    def fake_login(e, p="", client_ip="", code=""):
        raise RuntimeError("邮箱或密码错误")

    def fake_register(e, c, p, client_ip=""):
        seen.update({"email": e, "code": c, "password": p})
        return {"ok": True, "token": "T2"}

    monkeypatch.setattr(auth, "login", fake_login)
    monkeypatch.setattr(auth, "register", fake_register)
    out = auth.login_or_register("a@b.com", "pw12345678", code="654321")
    assert out["mode"] == "register", out
    assert seen == {"email": "a@b.com", "code": "654321", "password": "pw12345678"}, seen


def test_login_or_register_reports_register_error_when_code_given(monkeypatch):
    """带了验证码还注册不上 → 报**注册**那条错（多半是码不对），别拿"密码错误"糊弄人。"""
    monkeypatch.setattr(auth, "login", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("该邮箱未注册，请先注册")))
    monkeypatch.setattr(auth, "register", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("验证码错误或已过期")))
    with pytest.raises(RuntimeError) as err:
        auth.login_or_register("a@b.com", "pw12345678", code="654321")
    assert "验证码" in str(err.value), err.value


def test_login_or_register_without_code_hints_send_code(monkeypatch):
    """没带码又没注册过：提示里要告诉他"发个码再点登录就自动注册"。"""
    monkeypatch.setattr(auth, "login", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("该邮箱未注册，请先注册")))
    with pytest.raises(RuntimeError) as err:
        auth.login_or_register("a@b.com", "pw12345678")
    assert "发送验证码" in str(err.value) and "自动帮你注册" in str(err.value), err.value


def test_login_or_register_does_not_hint_code_on_wrong_password(monkeypatch):
    """密码打错时**不要**劝他发验证码（账号其实已注册，会把人绕晕）。"""
    monkeypatch.setattr(auth, "login", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("邮箱或密码错误")))
    with pytest.raises(RuntimeError) as err:
        auth.login_or_register("a@b.com", "pw12345678")
    assert "发送验证码" not in str(err.value), err.value


def test_login_or_register_without_code_just_reports_login_error(monkeypatch):
    monkeypatch.setattr(auth, "login", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("邮箱或密码错误")))
    with pytest.raises(RuntimeError) as err:
        auth.login_or_register("a@b.com", "pw12345678")
    assert "邮箱或密码错误" in str(err.value)


def test_login_or_register_validates_locally(monkeypatch):
    monkeypatch.setattr(auth, "_post", lambda *a, **k: pytest.fail("不该发请求"))
    with pytest.raises(RuntimeError) as e1:
        auth.login_or_register("bad", "pw12345678")
    assert "邮箱" in str(e1.value)
    with pytest.raises(RuntimeError) as e2:
        auth.login_or_register("a@b.com", "")
    assert "密码" in str(e2.value)

if __name__ == "__main__":
    pytest.main([__file__, "-q"])
