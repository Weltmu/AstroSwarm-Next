# -*- coding: utf-8 -*-
"""控制台工坊：异步生成（进度 + 取消）与导出下载（2026-10-03 老板点名要的 4 条之一）。

这里只测后端这一层：任务状态机、取消、导出回 zip 字节。
真跑模型不在测试里做（那要花 token），用替身函数把 `workshop.build` 换掉。
"""
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astroswarm_linux import api as api_mod  # noqa: E402
from astroswarm_linux import headless_config, platform_info  # noqa: E402

client = TestClient(api_mod.app)


@pytest.fixture
def auth(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_info, "config_home", lambda: tmp_path)
    cfg = headless_config.load()
    cfg.update({"account_token": "WS-TOKEN", "account_email": "ws@test.local"})
    headless_config.save(cfg)
    return {"Authorization": "Bearer WS-TOKEN"}


def _fake_build(settings, need, plan=None, channels=None, on_log=None, **kw):
    """假生成：按 need 里的指令决定跑几步、要不要中途报错。"""
    if on_log:
        on_log("正在读需求…")
        on_log("正在生成方案…")
        time.sleep(0.05)
        if "慢" in need:
            for i in range(60):          # 留给取消的时间窗
                on_log(f"生成中 {i}")
                time.sleep(0.05)
        if "崩" in need:
            raise RuntimeError("模型拒绝了这次生成")
        on_log("正在静态检查…")
    return {"ok": True, "plan": {"id": "demo-pack", "name": "示例插件"},
            "files": {"tools/a.py": "print(1)", "manifest.json": "{}"},
            "issues": [], "report": "看起来没问题", "model": "test-model"}


def test_async_build_reports_progress_and_result(auth, monkeypatch):
    monkeypatch.setattr(api_mod.workshop, "build", _fake_build)
    res = client.post("/api/workshop/build-async", headers=auth,
                      json={"need": "给我做个签到插件"})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]

    seen = []
    state = "running"
    for _ in range(60):
        body = client.get(f"/api/workshop/job?job_id={job_id}", headers=auth).json()
        seen += body["logs"]
        state = body["state"]
        if state != "running":
            break
        time.sleep(0.05)
    assert state == "done", (state, seen)
    assert "正在读需求…" in seen and "正在静态检查…" in seen, seen
    assert body["result"]["build_id"], body
    assert body["result"]["files"] == ["manifest.json", "tools/a.py"], body["result"]


def test_async_build_error_is_reported_not_swallowed(auth, monkeypatch):
    monkeypatch.setattr(api_mod.workshop, "build", _fake_build)
    job_id = client.post("/api/workshop/build-async", headers=auth,
                         json={"need": "这个会崩"}).json()["job_id"]
    for _ in range(60):
        body = client.get(f"/api/workshop/job?job_id={job_id}", headers=auth).json()
        if body["state"] != "running":
            break
        time.sleep(0.05)
    assert body["state"] == "error" and "模型拒绝" in body["error"], body


def test_cancel_stops_the_job(auth, monkeypatch):
    monkeypatch.setattr(api_mod.workshop, "build", _fake_build)
    job_id = client.post("/api/workshop/build-async", headers=auth,
                         json={"need": "慢慢来"}).json()["job_id"]
    time.sleep(0.2)
    res = client.post("/api/workshop/cancel", headers=auth, json={"build_id": job_id})
    assert res.status_code == 200 and res.json()["cancelled"] is True, res.text
    assert "会自己结束" in res.json()["note"], res.json()
    for _ in range(60):
        body = client.get(f"/api/workshop/job?job_id={job_id}", headers=auth).json()
        if body["state"] != "running":
            break
        time.sleep(0.05)
    assert body["state"] == "cancelled", body


def test_job_endpoints_need_auth_and_handle_unknown_id(auth):
    assert client.get("/api/workshop/job?job_id=x").status_code == 401
    assert client.post("/api/workshop/build-async", json={"need": "x"}).status_code == 401
    assert client.get("/api/workshop/job?job_id=nope", headers=auth).status_code == 404
    assert client.post("/api/workshop/build-async", headers=auth,
                       json={"need": "   "}).status_code == 400


def test_export_can_return_zip_bytes(auth, monkeypatch, tmp_path):
    """控制台点「下载到本地」时要拿到 zip 字节，而不是一串服务器路径。"""
    archive = tmp_path / "demo-pack.zip"
    archive.write_bytes(b"PK\x03\x04fake-zip")
    monkeypatch.setattr(api_mod.workshop, "export_zip",
                        lambda s, pid: {"ok": True, "id": pid, "archive": str(archive)})

    plain = client.post("/api/workshop/export", headers=auth, json={"id": "demo-pack"})
    assert plain.status_code == 200 and plain.json()["archive"] == str(archive)

    got = client.post("/api/workshop/export?download=1", headers=auth,
                      json={"id": "demo-pack"})
    assert got.status_code == 200, got.text
    assert got.content == b"PK\x03\x04fake-zip"
    assert got.headers["content-type"] == "application/zip"
    assert "demo-pack.zip" in got.headers["content-disposition"]


def test_export_download_reports_missing_file(auth, monkeypatch):
    monkeypatch.setattr(api_mod.workshop, "export_zip",
                        lambda s, pid: {"ok": True, "id": pid, "archive": "/no/such.zip"})
    res = client.post("/api/workshop/export?download=1", headers=auth,
                      json={"id": "demo-pack"})
    assert res.status_code == 404


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
