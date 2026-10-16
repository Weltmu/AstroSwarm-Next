# -*- coding: utf-8 -*-
"""端到端验证：控制台入口 HTML 与页签图标能被无头端真的取到。

这条用例补的是"最后一公里"：前面的 parity 用例只证明"index.html 里声明了
`<link rel="icon" href="/logo.png">` 且 `console/public/logo.png` 存在"，
但没证明 FastAPI 的 StaticFiles 真能把它吐出来 —— 也就是服务器日志里那条
`GET /favicon.ico 404` 到底有没有被消掉。

跑法（在 QBotManager 根目录，用 .test_venv）：
    .test_venv\\Scripts\\python.exe -m pytest tests/test_console_favicon_served.py -v

产物目录来源（按顺序）：
  1. 环境变量 ASTROSWARM_CONSOLE_DIST（部署时的正式做法）；
  2. 开发机默认 D:\\ai\\AstroSwarm-public\\console-dist（就是 Vite build.outDir）；
  3. 仓库旁 console-dist（api.py 自己的兜底）。
都没有就 skip —— 本机没铺产物不是这条用例要抓的问题。

⚠ 踩过的坑（2026-10-10）：`api.py` 是在**导入时**读环境变量并挂 StaticFiles 的，
   而 test_linux_console_ext / test_linux_console_parity 也会 `from ...api import app`。
   所以"在模块顶层设 os.environ 再 import"是靠 pytest 的收集顺序吃饭的 ——
   单独跑这份文件能过，和那几个文件一起跑就 404（先导入的 api 已经把 CONSOLE_DIST
   定成了不存在的兜底路径）。正确做法：在 fixture 里设好环境变量再 reload 模块。
"""
import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
DEV_DIST = Path(r"D:\ai\AstroSwarm-public\console-dist")
sys.path.insert(0, str(ROOT / "src"))


def _console_dist() -> Path:
    env = os.environ.get("ASTROSWARM_CONSOLE_DIST", "").strip()
    if env and Path(env).exists():
        return Path(env)
    if DEV_DIST.exists():
        return DEV_DIST
    return ROOT / "console-dist"


CONSOLE_DIST = _console_dist()


@pytest.fixture(scope="module")
def client():
    """把产物目录钉死，然后 reload api，确保 StaticFiles 挂在正确的目录上。"""
    if not (CONSOLE_DIST / "index.html").exists():
        pytest.skip(f"本机没有控制台产物：{CONSOLE_DIST}（先 npm.cmd run build）")
    prev = os.environ.get("ASTROSWARM_CONSOLE_DIST")
    os.environ["ASTROSWARM_CONSOLE_DIST"] = str(CONSOLE_DIST)
    api = importlib.import_module("astroswarm_linux.api")
    api = importlib.reload(api)
    try:
        yield TestClient(api.app)
    finally:
        if prev is None:
            os.environ.pop("ASTROSWARM_CONSOLE_DIST", None)
        else:
            os.environ["ASTROSWARM_CONSOLE_DIST"] = prev
        # 还原成"按环境变量重新解析"的状态，别把后面跑的用例带偏
        importlib.reload(api)


def test_console_dist_is_a_real_directory():
    assert CONSOLE_DIST.exists(), (
        f"控制台产物目录不存在：{CONSOLE_DIST} —— 先 npm.cmd run build（Vite 会写到 console-dist）"
    )
    assert (CONSOLE_DIST / "index.html").exists(), f"产物里没有 index.html：{CONSOLE_DIST}"


def test_entry_html_declares_the_favicon(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<!doctype html" in res.text.lower(), f"入口不是 HTML：{res.text[:120]}"
    assert 'rel="icon"' in res.text.replace("'", '"'), "入口 HTML 里没有 icon 声明"
    assert "/logo.png" in res.text, "入口 HTML 里的 icon 没指向 /logo.png"


def test_logo_png_is_served_with_200(client):
    """/logo.png 必须 200 —— 这就是 favicon 404 被消掉的那一半。

    另一半是 index.html 里的 <link rel="icon">：浏览器找到它就不再盲请求
    /favicon.ico，所以这里不要求 /favicon.ico 存在（也不该为它加文件）。
    """
    res = client.get("/logo.png")
    assert res.status_code == 200, f"/logo.png 取不到：{res.status_code}"
    assert res.headers["content-type"].startswith("image/png"), res.headers["content-type"]
    body = res.content
    assert body.startswith(b"\x89PNG\r\n\x1a\n"), "返回的不是 PNG"
    assert len(body) > 1024, f"返回体太小：{len(body)} bytes"
    # 升级后可能换图标，不能长缓存（ConsoleStaticFiles 的 no-cache 那一档）
    cache = res.headers.get("cache-control", "")
    assert "no-cache" in cache or "must-revalidate" in cache, f"Cache-Control 不对劲：{cache}"
