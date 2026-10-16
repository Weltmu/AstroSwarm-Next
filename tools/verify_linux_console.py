# -*- coding: utf-8 -*-
"""Linux 控制台端到端联调：真起 FastAPI（含前端静态产物），打真接口。

为什么要这一步：前端是新写的，子智能体只做了渲染冒烟、没连过后端。
这里用临时数据目录起服务（绝不碰真机配置），逐个打新接口 + 取首页静态文件。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(r"D:\ai\QBotManager")
CONSOLE_DIST = Path(r"D:\ai\AstroSwarm-public\console-dist")

home = Path(tempfile.mkdtemp(prefix="astro_e2e_"))
os.environ["XDG_CONFIG_HOME"] = str(home / "config")
os.environ["XDG_DATA_HOME"] = str(home / "data")
os.environ["ASTROSWARM_CONSOLE_DIST"] = str(CONSOLE_DIST)
sys.path.insert(0, str(REPO / "src"))

from astroswarm_linux import headless_config  # noqa: E402
from astroswarm_linux.api import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(("✓ " if ok else "✗ ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    cfg = headless_config.load()
    cfg.update({"account_token": "E2E-TOKEN", "account_email": "e2e@test.local"})
    headless_config.save(cfg)
    headers = {"Authorization": "Bearer E2E-TOKEN"}
    client = TestClient(app)

    # ① 前端静态文件真的挂上了（console-dist 由 --console-dist 提供）
    index = client.get("/")
    check("首页返回 200", index.status_code == 200, f"HTTP {index.status_code}")
    html = index.text
    check("首页是控制台产物", "assets/index-" in html, html[:80].replace("\n", " "))
    asset = html.split('src="')[1].split('"')[0] if 'src="' in html else ""
    if asset:
        js = client.get(asset)
        check("前端 JS 可取到", js.status_code == 200, f"{asset} HTTP {js.status_code}")
        body = js.text
        for needle in ("机器人 AI", "群里主动搭话", "官方通道体检", "群消息",
                       "改昵称", "换头像", "从官网同步"):
            check(f"产物里有「{needle}」", needle in body)
        for api in ("/api/account/profile", "/api/account/avatar", "/api/account/refresh"):
            check(f"产物里调了 {api}", api in body)
        for banned in ("开通会员", "买断", "未解锁", "免费版"):
            check(f"产物里没有「{banned}」", banned not in body)

    # ② 新接口逐个打（真 HTTP 调用，不是读源码）
    res = client.get("/api/ai/settings", headers=headers)
    check("GET /api/ai/settings", res.status_code == 200, f"HTTP {res.status_code}")
    body = res.json() if res.status_code == 200 else {}
    for key in ("group_chat_enabled", "rich_parse_enabled", "allowed_groups",
                "known_groups", "interject", "session",
                "group_welcome_enabled", "allowed_send_targets"):
        check(f"  · 字段 {key}", key in body)
    res = client.put("/api/ai/settings", headers=headers,
                     json={"group_chat_enabled": False, "allowed_groups": ["群X"]})
    check("PUT /api/ai/settings", res.status_code == 200, f"HTTP {res.status_code}")
    body = client.get("/api/ai/settings", headers=headers).json()
    check("  · 写入生效", body["group_chat_enabled"] is False
          and body["allowed_groups"] == ["群X"], str(body)[:100])
    client.put("/api/ai/settings", headers=headers,
               json={"group_chat_enabled": True, "allowed_groups": []})

    res = client.get("/api/qq/group-state", headers=headers)
    check("GET /api/qq/group-state", res.status_code == 200, f"HTTP {res.status_code}")
    body = res.json() if res.status_code == 200 else {}
    check("  · 有模式与人话", body.get("mode") in ("none", "full", "at_only")
          and bool(body.get("text")), str(body)[:100])

    res = client.get("/api/qq/diagnose", headers=headers)
    check("GET /api/qq/diagnose", res.status_code == 200, f"HTTP {res.status_code}")
    body = res.json() if res.status_code == 200 else {}
    check("  · 有结论与条目", bool(body.get("summary")) and isinstance(body.get("rows"), list),
          f"{len(body.get('rows') or [])} 条")

    # 日志：事件名翻人话 + 只看官方通道（桌面端日志页同款能力）
    res = client.get("/api/logs/tail?n=20", headers=headers)
    check("GET /api/logs/tail", res.status_code == 200, f"HTTP {res.status_code}")
    res = client.get("/api/logs/official?n=200", headers=headers)
    check("GET /api/logs/official（只看官方通道）", res.status_code == 200,
          f"HTTP {res.status_code} {str(res.json())[:60]}")

    # ③ 账号头像/昵称接口：本机没登录时 401；已登录则要给出昵称/头像字段
    res = client.get("/api/account/profile", headers=headers)
    if res.status_code == 401:
        check("GET /api/account/profile（本机没登录账号 → 401）", True,
              res.json().get("detail", ""))
    else:
        check("GET /api/account/profile（本机已登录 → 200）", res.status_code == 200,
              f"HTTP {res.status_code}")
        prof = res.json()
        check("  · 有邮箱与显示名", bool(prof.get("email")) and bool(prof.get("display_name")),
              f"{prof.get('email')} / {prof.get('display_name')}")
        check("  · 有头像是 data URL 或空", prof.get("avatar", "").startswith("data:image/")
              or prof.get("avatar", "") == "", str(prof.get("avatar"))[:30])

    # ④ 未登录要 401（安全边界没被新接口破坏）
    for path in ("/api/ai/settings", "/api/qq/group-state", "/api/qq/diagnose",
                 "/api/account/profile"):
        check(f"未登录打 {path} = 401", client.get(path).status_code == 401)

    print()
    if failures:
        print("联调失败：", failures)
        return 1
    print("端到端联调全部通过 ✓（临时目录：%s）" % home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
