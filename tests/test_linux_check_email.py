# -*- coding: utf-8 -*-
"""控制台「查这个邮箱注册过没有」（2026-10-10 老板要求）。

老板原话：控制台 web 登录**不要**一上来就给「获取验证码」，后端先查这个邮箱
注册过没有 —— 注册过就只让填密码，没注册过才给「获取验证码注册开户」。

这条用例锁住四件事：
  ① 已注册 / 未注册两种结果都从账号服务的 **register 路**问出来（先查库、后验码：
     409「该邮箱已注册」/ 400「验证码错误或已过期」），不发验证码邮件、不试密码；
  ② 探测器用的验证码必须是 6 位**非数字**（账号服务的码恒为 6 位数字 → 必然不相等），
     保证这条路**建不出账号**，也永远走不到"登录"那条会记失败次数的路；
  ③ 问不出来（网络/账号服务报错/契约变了）一律 ok=false + 中文原因，
     **绝不**把"问不到"当成"没注册"（那会让老用户看到注册界面）；
  ④ 邮箱格式不对本地拦掉（不发请求），并且这个接口**单独按 IP 限流**（防批量扫邮箱）。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astroswarm_linux import api as api_mod  # noqa: E402
from astroswarm_linux import auth  # noqa: E402
from astroswarm_linux.api import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_buckets():
    """限流的桶是模块级的：用例之间必须互不影响（否则谁先跑谁把别人挡住）。"""
    api_mod._check_email_hits.clear()
    yield
    api_mod._check_email_hits.clear()


def _stub_probe(monkeypatch, status, payload):
    """把账号服务那一步换成假的，同时记下真实请求（路径 + 请求体）。"""
    seen = []

    def fake(path, body, client_ip=""):
        seen.append({"path": path, "body": dict(body), "ip": client_ip})
        return status, payload

    monkeypatch.setattr(auth, "_post_raw", fake)
    return seen


def test_registered_email_is_reported_as_registered(monkeypatch):
    seen = _stub_probe(monkeypatch, 409, {"detail": "该邮箱已注册"})
    r = client.post("/api/auth/check-email", json={"email": "li@astroswarm.cn"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "registered": True, "email": "li@astroswarm.cn"}, r.text
    assert len(seen) == 1 and seen[0]["path"] == "/api/account/register", seen


def test_unregistered_email_is_reported_as_unregistered(monkeypatch):
    _stub_probe(monkeypatch, 400, {"detail": "验证码错误或已过期"})
    r = client.post("/api/auth/check-email", json={"email": "nobody-9f2a@example.com"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "registered": False, "email": "nobody-9f2a@example.com"}, r.text


def test_probe_never_uses_send_code_or_login(monkeypatch):
    """探测只准走 register：发验证码会真发信，login 会记失败次数（错 5 次锁 15 分钟）。"""
    seen = _stub_probe(monkeypatch, 409, {"detail": "该邮箱已注册"})
    client.post("/api/auth/check-email", json={"email": "li@astroswarm.cn"})
    assert seen[0]["path"] == "/api/account/register"
    assert "send-code" not in seen[0]["path"] and "login" not in seen[0]["path"]


def test_probe_code_cannot_create_an_account(monkeypatch):
    """探测用的验证码必须是 6 位非数字：账号服务的码恒为 6 位数字 → compare_digest 必然不等。

    这是"探测无副作用"的**唯一**依据，所以单独钉一条：谁把它改成数字（或改短），
    这里立刻红 —— 那意味着一次查询有可能真的把账号建出来。
    """
    seen = _stub_probe(monkeypatch, 409, {"detail": "该邮箱已注册"})
    client.post("/api/auth/check-email", json={"email": "li@astroswarm.cn"})
    code = seen[0]["body"]["code"]
    assert len(code) == 6, code
    assert not code.isdigit(), code
    assert len(seen[0]["body"]["password"]) >= 8, seen


def test_failure_is_never_reported_as_unregistered(monkeypatch):
    """问不到 → ok=false + 中文原因；**不许**回 registered（老的/新的用户都不许被误导）。"""
    for status, payload in ((429, {"detail": "今日注册次数已达上限，请明天再试"}),
                            (500, {"detail": "Internal Server Error"}),
                            (404, {"detail": "Not Found"})):
        _stub_probe(monkeypatch, status, payload)
        r = client.post("/api/auth/check-email", json={"email": "li@astroswarm.cn"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False, body
        assert "registered" not in body, body
        assert body["reason"].strip(), body


def test_network_error_is_a_chinese_reason_not_a_guess(monkeypatch):
    """连不上账号服务：ok=false，原因里带得出中文（前端据此说「暂时查不到」）。"""
    def boom(email, client_ip=""):
        raise RuntimeError("连不上星群账号服务：timed out")

    monkeypatch.setattr(auth, "check_email", boom)
    body = client.post("/api/auth/check-email", json={"email": "li@astroswarm.cn"}).json()
    assert body["ok"] is False and "registered" not in body
    assert "连不上" in body["reason"], body


def test_created_account_is_reported_as_an_error(monkeypatch):
    """真回 200 + token（说明账号服务契约变了、而且已经建了号）→ 必须报错，不能装作查到了。"""
    _stub_probe(monkeypatch, 200, {"ok": True, "token": "T", "email": "li@astroswarm.cn"})
    body = client.post("/api/auth/check-email", json={"email": "li@astroswarm.cn"}).json()
    assert body["ok"] is False and "registered" not in body, body


def test_bad_email_is_rejected_locally(monkeypatch):
    """格式不对：本地 400，一个请求都不发（前端也有同样的一道本地校验）。"""
    seen = _stub_probe(monkeypatch, 409, {"detail": "该邮箱已注册"})
    for bad in ("", "li", "不是邮箱", "没有 at 符号"):
        r = client.post("/api/auth/check-email", json={"email": bad})
        assert r.status_code == 400, (bad, r.text)
        assert "邮箱" in r.text, r.text
    assert seen == [], "格式不对不该去调账号服务"


def test_account_service_email_rule_wins_when_it_is_stricter(monkeypatch):
    """本地只看"有没有 @"；账号服务判得更细时，它的中文原因原样回，仍不当成"未注册"。"""
    _stub_probe(monkeypatch, 400, {"detail": "邮箱格式不正确"})
    body = client.post("/api/auth/check-email", json={"email": "li@astroswarm"}).json()
    assert body["ok"] is False and "registered" not in body, body
    assert "邮箱" in body["reason"], body


def test_check_email_is_rate_limited_per_ip(monkeypatch):
    """按 IP 限流（老板要求：别被人拿去批量扫邮箱）：超过额度回 429 中文原因。"""
    seen = _stub_probe(monkeypatch, 400, {"detail": "验证码错误或已过期"})
    for i in range(api_mod._CHECK_EMAIL_MAX):
        r = client.post("/api/auth/check-email", json={"email": f"probe{i}@example.com"})
        assert r.status_code == 200, (i, r.text)
    hit = client.post("/api/auth/check-email", json={"email": "probe-x@example.com"})
    assert hit.status_code == 429, hit.text
    assert "频繁" in hit.text, hit.text
    assert len(seen) == api_mod._CHECK_EMAIL_MAX, "被限流的那次不该再去问账号服务"


def test_rate_limit_does_not_use_the_login_bucket(monkeypatch):
    """两套桶互不顶替：查注册状态不该把「登录」的额度吃掉（反之亦然）。"""
    _stub_probe(monkeypatch, 400, {"detail": "验证码错误或已过期"})
    api_mod._login_hits.clear()
    for i in range(api_mod._CHECK_EMAIL_MAX):
        client.post("/api/auth/check-email", json={"email": f"probe{i}@example.com"})
    assert api_mod._login_hits == {}, api_mod._login_hits
    api_mod._login_hits.clear()
