# -*- coding: utf-8 -*-
"""链接解析（2026-10-03）：消息里带网址时先抓标题/正文，再交给 AI。

老板问过"B站分享、一堆特殊信息能不能解析"：
- **卡片**（B站/小程序/音乐）→ `rich_content.describe_card` 负责（已做）；
- **纯文本链接** → 这里负责：抓到标题 + 正文摘要塞进消息里，AI 才答得出来。

安全底线沿用 core/web_fetch：只公网 http(s)、内网地址拒绝、超时与长度都有上限。
"""
import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


_PKG = "ai_link_pkg"


def _load_link_preview(name="ai_link_t"):
    """按路径加载 link_preview（它 import 了 nonebot 和同目录的 web_fetch，都补上）。"""
    if "nonebot" not in sys.modules:
        nb = types.ModuleType("nonebot")
        nb.logger = logging.getLogger("nonebot")
        sys.modules["nonebot"] = nb
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(AI_DIR)]
        sys.modules[_PKG] = pkg
        wf_spec = importlib.util.spec_from_file_location(f"{_PKG}.web_fetch",
                                                        AI_DIR / "web_fetch.py")
        wf = importlib.util.module_from_spec(wf_spec)
        sys.modules[f"{_PKG}.web_fetch"] = wf
        wf_spec.loader.exec_module(wf)
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}",
                                                 AI_DIR / "link_preview.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.__dict__.setdefault("web_fetch", sys.modules[f"{_PKG}.web_fetch"])
    return mod


def test_extract_urls_filters_noise():
    lp = _load_link_preview("ai_link_t1")
    text = ("看这个 https://b23.tv/abc123 还有 https://x.com/a.png 和 "
            "https://example.com/post?id=1。另外重复 https://b23.tv/abc123")
    urls = lp.extract_urls(text)
    assert urls == ["https://b23.tv/abc123", "https://example.com/post?id=1"], urls
    assert lp.extract_urls("没有链接") == []


def test_extract_urls_skips_qq_image_cdn():
    """QQ 图片 CDN（无扩展名）不能当网页抓：2026-10-12 群日志里全是这种链接。"""
    lp = _load_link_preview("ai_link_t6")
    text = ("看图 https://multimedia.nt.qq.com.cn/download?appid=1406&fileid=abc&rkey=xx "
            "再看 https://example.com/post")
    assert lp.extract_urls(text) == ["https://example.com/post"]


def test_enrich_appends_title_and_body():
    lp = _load_link_preview("ai_link_t2")
    calls = []

    def fake_fetch(url, want="text", limit=0, timeout=0):
        calls.append((url, want, limit, timeout))
        return {"ok": True, "status": 200, "url": url,
                "title": "【原神】4.2版本前瞻直播", "text": "本期带来新角色与活动预告。",
                "truncated": False}

    lp.web_fetch.fetch = fake_fetch
    out = asyncio.run(lp.enrich("看这个 https://b23.tv/abc123"))
    assert "【原神】4.2版本前瞻直播" in out, out
    assert "本期带来新角色与活动预告" in out, out
    assert calls and calls[0][0] == "https://b23.tv/abc123", calls
    assert calls[0][3] == lp.TIMEOUT, calls


def test_failed_fetch_says_so_instead_of_pretending():
    lp = _load_link_preview("ai_link_t3")
    lp.web_fetch.fetch = lambda *a, **k: {"ok": False, "error": "blocked_host"}
    out = asyncio.run(lp.enrich("https://127.0.0.1/secret"))
    assert "打不开" in out and "blocked_host" in out, out


def test_no_link_returns_text_unchanged():
    lp = _load_link_preview("ai_link_t4")
    assert asyncio.run(lp.enrich("就是一句话")) == "就是一句话"


def test_at_most_two_links_are_fetched():
    lp = _load_link_preview("ai_link_t5")
    seen = []
    lp.web_fetch.fetch = lambda url, **k: (seen.append(url) or
                                           {"ok": True, "title": "t", "text": "b"})
    asyncio.run(lp.enrich("a https://a.com/1 b https://b.com/2 c https://c.com/3"))
    assert len(seen) == lp.MAX_LINKS, seen


def test_web_fetch_blocks_private_hosts_and_schemes():
    """共用的取网页模块：内网与奇怪协议一律拒绝（AI 拿到的链接也走这里）。"""
    web_fetch = _load_link_preview("ai_link_t9").web_fetch

    for bad in ("http://127.0.0.1/x", "http://192.168.1.9/x", "http://10.0.0.5/x",
                "http://169.254.1.1/x", "http://localhost/x", "http://nas/x",
                "file:///etc/passwd", "ftp://x/y"):
        url, why = web_fetch.check_url(bad)
        assert why in ("bad_url", "blocked_host"), (bad, why)
    ok, why = web_fetch.check_url("https://astroswarm.cn/download.html")
    assert ok and not why, (ok, why)


def test_web_fetch_extracts_title_and_text():
    web_fetch = _load_link_preview("ai_link_t9").web_fetch

    html = ("<html><head><title>标题在这</title><script>var x=1</script></head>"
            "<body><p>第一段</p><p>第二段 &amp; 结束</p></body></html>")
    assert web_fetch.page_title(html) == "标题在这"
    text = web_fetch.plain_text(html)
    assert "第一段" in text and "第二段 & 结束" in text, text
    assert "var x=1" not in text, "脚本内容不该混进正文"


if __name__ == "__main__":
    test_extract_urls_filters_noise()
    test_enrich_appends_title_and_body()
    test_failed_fetch_says_so_instead_of_pretending()
    test_no_link_returns_text_unchanged()
    test_at_most_two_links_are_fetched()
    test_web_fetch_blocks_private_hosts_and_schemes()
    test_web_fetch_extracts_title_and_text()
    print("OK link preview")
