# -*- coding: utf-8 -*-
"""链接预览：消息里带网址时，先把标题和正文摘要抓下来再交给 AI。

老板 2026-10-03 问的"B站分享能不能解析"——卡片靠 `rich_content`（已做），
**纯文本链接**原来只能让 AI 看到一串短链，它答不出来；这里补上：
- 只在富内容解析开关打开时工作（和引用/图片同一个开关）；
- 一条消息最多抓 2 个链接、每个最多 8 秒、注入正文最多 1200 字；
- 只抓公网 http(s)（内网地址在插件自带的 web_fetch 里就挡掉了）；
- 抓失败就安静跳过（回话优先），但会给 AI 留一句"这个链接我没打开"，
  免得它装作读过。
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from nonebot import logger

from . import web_fetch


URL_RE = re.compile(r"https?://[^\s<>\"'）)】\]，。；：！？、“”‘’「」【】]+")
SKIP_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mp3",
                 ".zip", ".rar", ".7z", ".apk", ".exe", ".pdf")
# QQ 图片/文件 CDN：链接没有扩展名拦不住，抓回来是二进制不是网页（2026-10-12 日志实锤）
SKIP_HOSTS = ("multimedia.nt.qq.com.cn",)
MAX_LINKS = 2
TIMEOUT = 8
BODY_LIMIT = 1200


def _skip_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def extract_urls(text: str) -> list:
    """消息里出现的 http(s) 链接（去掉图片/压缩包这类没必要抓的）。"""
    out = []
    for match in URL_RE.findall(str(text or "")):
        url = match.rstrip(".,;，。；、")
        if any(url.lower().split("?")[0].endswith(s) for s in SKIP_SUFFIXES):
            continue
        if _skip_host(url):
            continue
        if url not in out:
            out.append(url)
    return out


def _fetch_one(url: str) -> dict:
    return web_fetch.fetch(url, want="text", limit=BODY_LIMIT, timeout=TIMEOUT)


async def enrich(text: str) -> str:
    """把消息里的链接抓成"标题 + 正文摘要"附在后面；没有链接就原样返回。"""
    urls = extract_urls(text)
    if not urls:
        return text
    lines = []
    for url in urls[:MAX_LINKS]:
        try:
            info = await asyncio.to_thread(_fetch_one, url)   # 别卡住机器人的事件循环
        except Exception as e:  # noqa: BLE001
            logger.info(f"[link] 抓取失败 {url}: {e}")
            info = {"ok": False, "error": "fetch_failed"}
        if not info.get("ok"):
            lines.append(f"[链接] {url}（我这边打不开：{info.get('error') or '失败'}）")
            continue
        title = str(info.get("title") or "").strip()
        body = " ".join(str(info.get("text") or "").split())
        head = f"[链接内容] {url}"
        if title:
            head += f" 标题：{title}"
        if body:
            head += f"\n正文摘要：{body[:BODY_LIMIT]}"
        if info.get("truncated"):
            head += "…（内容较长，只取了开头）"
        lines.append(head)
    if not lines:
        return text
    return text + "\n" + "\n".join(lines)
