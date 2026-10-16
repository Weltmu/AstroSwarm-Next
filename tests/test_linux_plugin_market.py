# -*- coding: utf-8 -*-
"""插件市场接口（控制台 ↔ 桌面端对齐的最后一块）。

桌面端「插件」页能在应用里逛市场、一键装；控制台以前只能传 zip。
这里锁住 `GET /api/plugins/market` 的行为：
- 市场拉不到 / 签名不对 → 回 ok:false + 中文原因（**不能 500**，前端要显示"暂不可用"）；
- 正常 → 回 name/desc/version/category/access，且**不泄漏任何密钥类字段**；
- 需要登录（带 token）。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astroswarm_linux import console_ext, headless_config, platform_info  # noqa: E402
from astroswarm_linux.api import app  # noqa: E402

client = TestClient(app)


def _auth(monkeypatch, tmp_path, token="T-MARKET"):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path / "config")
    cfg = headless_config.load()
    cfg.update({"account_token": token, "account_email": "t@test.local"})
    headless_config.save(cfg)
    return {"Authorization": f"Bearer {token}"}


def test_market_requires_login():
    r = client.get("/api/plugins/market")
    assert r.status_code in (401, 403), r.status_code


def test_market_unreachable_is_not_a_crash(monkeypatch, tmp_path):
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import plugin_market

    def boom(*_a, **_k):
        raise OSError("no network")

    monkeypatch.setattr(plugin_market, "fetch_market", boom)
    r = client.get("/api/plugins/market", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is False and data["entries"] == []
    assert "连不上" in data["why"], data


def test_market_bad_signature_is_refused(monkeypatch, tmp_path):
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import plugin_market

    monkeypatch.setattr(plugin_market, "fetch_market",
                        lambda *a, **k: (b'{"plugins": []}', "bad-sig"))
    monkeypatch.setattr(plugin_market, "verify_manifest", lambda *a, **k: False)
    r = client.get("/api/plugins/market", headers=headers)
    data = r.json()
    assert data["ok"] is False and "签名" in data["why"], data


def test_market_ok_shape_and_no_secrets(monkeypatch, tmp_path):
    headers = _auth(monkeypatch, tmp_path)
    from qbotmanager.core import plugin_market

    # parse_market 要求条目至少有 id + name；url/sha256 是安装时才校验的字段
    raw = ('{"plugins": [{"id": "weather", "name": "天气助手", "desc": "每天播报",'
           ' "version": "1.2.3", "category": "生活", "sha256": "deadbeef",'
           ' "url": "https://astroswarm.cn/x.zip", "api_key": "SHOULD-NOT-LEAK"}]}')
    monkeypatch.setattr(plugin_market, "fetch_market",
                        lambda *a, **k: (raw.encode(), "good-sig"))
    monkeypatch.setattr(plugin_market, "verify_manifest", lambda *a, **k: True)
    monkeypatch.setattr(plugin_market, "plugin_access",
                        lambda entry, ent: {"free": True})
    r = client.get("/api/plugins/market", headers=headers)
    data = r.json()
    assert data["ok"] is True and data["count"] == 1, data
    item = data["entries"][0]
    assert item["name"] == "天气助手" and item["version"] == "1.2.3"
    assert item["category"] == "生活" and item["access"] == {"free": True}
    # 白名单式返回：多余字段（sha256 / api_key 之类）一律不带出去
    assert set(item) == {"id", "name", "desc", "version", "category", "access"}, item
    assert item["id"] == "weather", "id 必须带上：安装按钮靠它"
    assert "SHOULD-NOT-LEAK" not in r.text


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
