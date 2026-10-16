# -*- coding: utf-8 -*-
"""文件消息读取（2026-10-13 老板：引用/发来的 pdf、md 能不能读）。

现状改前：文件段（`{"type":"file"}`）在适配器里**没有任何处理** —— 直接发文件她
当没看见（正文为空，甚至会被判成"空 @"），引用文件只会回"你引用的那条我看不到"。

现在：
- md / txt / json / py / csv 这类文本文件：直接解码读（utf-8，坏了退 gbk）；
- PDF：用现成的 `pypdf`（纯 Python，装上就能读；没装就如实说"读不了 PDF"，
  不硬编一个半吊子解析器）；
- 文件从哪拿：优先段里的 `url` / 本地 `path`，否则试协议端接口
  （get_group_file_url / get_private_file_url / get_file）；
- 读出来的正文**截断**后再进提示词（默认 4000 字，防止一个文件把上下文烧光），
  下载大小上限 8MB。

任何一步失败都返回"能说的话"，绝不抛异常拖垮回话。
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.request
from pathlib import Path

try:                                   # 这个模块要能被按路径单独加载（单测/工具脚本），
    from . import media_guard          # 那种场景下没有包上下文，相对导入会直接 ImportError
except ImportError:  # noqa: BLE001
    media_guard = None

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024      # 下载上限 8MB
MAX_PROMPT_CHARS = 4000                   # 进提示词的正文上限
DOWNLOAD_TIMEOUT = 25

TEXT_EXTS = (".md", ".markdown", ".txt", ".text", ".json", ".py", ".js", ".ts",
             ".csv", ".log", ".yaml", ".yml", ".ini", ".toml", ".xml", ".html",
             ".htm", ".sql", ".sh", ".bat", ".cfg", ".conf")

__all__ = ["read_file", "describe", "files_text_from_raw", "extract_text"]


def _log(text: str) -> None:
    try:
        from nonebot import logger

        logger.info(text)
    except Exception:  # noqa: BLE001
        pass


def _ext_of(name: str) -> str:
    return Path(str(name or "")).suffix.lower()


def extract_text(name: str, data: bytes) -> tuple[str, str]:
    """把文件字节变成正文。返回 (正文, 备注)；读不了时正文为空、备注说明原因。"""
    ext = _ext_of(name)
    if ext in TEXT_EXTS or not ext:
        text = ""
        for enc in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text:
            return text, ""
        return "", "这个文件不是能直接读的文本编码"
    if ext == ".pdf":
        reader = None
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader
        except Exception:  # noqa: BLE001 —— 老环境可能只有 PyPDF2
            try:
                from PyPDF2 import PdfReader  # type: ignore

                reader = PdfReader
            except Exception:  # noqa: BLE001
                reader = None
        if reader is None:
            return "", "这台机器人没装 PDF 解析库（pypdf），暂时读不了 PDF 内容"
        try:
            import io

            pdf = reader(io.BytesIO(data))
            pages = []
            for page in pdf.pages[:60]:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001 —— 单页失败不影响别的页
                    continue
            text = "\n".join(p for p in pages if p).strip()
            if not text:
                return "", "这个 PDF 没有可提取的文字（可能是扫描件/图片版）"
            return text, ""
        except Exception as e:  # noqa: BLE001
            return "", f"PDF 解析失败：{str(e)[:80]}"
    return "", "暂不认识这种文件格式（支持 md/txt/pdf 等文本类文件）"


def _candidate_apis(group_id: str):
    """协议端可能提供下载地址的接口（按顺序试，名字对不上就跳过）。"""
    if group_id:
        yield "get_group_file_url", {"group_id": int(group_id)}
    yield "get_private_file_url", {}
    yield "get_file", {}


async def _resolve_url(bot, group_id: str, data: dict) -> tuple[str, str, str]:
    """找文件的下载地址/本地路径。返回 (url 或 path, 来源, 备注)。"""
    url = str(data.get("url") or "").strip()
    if url.startswith(("http://", "https://", "file://")):
        return url, "seg.url", ""
    for key in ("path", "file"):
        cand = str(data.get(key) or "").strip()
        if cand and Path(cand).is_file():
            return cand, "seg.path", ""
    file_id = str(data.get("file_id") or data.get("id") or data.get("file") or "").strip()
    if bot is not None and file_id:
        for api, base in _candidate_apis(group_id):
            params = dict(base)
            params["file_id"] = file_id
            try:
                got = await bot.call_api(api, **params)
            except Exception:  # noqa: BLE001 —— 接口不存在/参数不对，换下一个
                continue
            for key in ("url", "file", "download_url"):
                cand = ""
                if isinstance(got, dict):
                    cand = str(got.get(key) or "")
                elif isinstance(got, str):
                    cand = got
                cand = cand.strip()
                if cand.startswith(("http://", "https://", "file://")):
                    # 2026-10-16（对抗性复核 bridge/C）：这条地址是**协议端自己的接口
                    # 返回的**（不是消息段里用户能构造的 `url` 字段）—— 登记出处，
                    # NapCat 的文件服务就长在本机口上，不登记第一次读取就会吃内网拦截。
                    # seg.url 那条路（上面 113 行）依旧一个字都不登记、严格拦内网。
                    if cand.startswith(("http://", "https://")) and media_guard is not None:
                        media_guard.trust_origin(cand)
                    return cand, api, ""
                if cand and Path(cand).is_file():
                    return cand, api, ""
    return "", "", "拿不到下载地址（协议端没给 url，接口也没返回）"


def _download(url: str) -> tuple[bytes, str]:
    """下载（或读本地文件）。返回 (字节, 备注)。

    2026-10-15 修（安全 + 卡顿）：
      · 以前是**裸 urlopen** —— 不拦内网主机、自动跟随跳转不复查，文件段里的 url
        指向本机/内网（或公网 302 到内网）就会被直接请求，正文还会进提示词；
      · 现在 http(s) 走 `web_fetch.open_checked`（同一套内网防线 + 逐跳复查）。
        协议端自己的文件服务常常长在本机口上，所以先认 `media_guard` 登记过的出处。
      · 本地/file:// 分支先 stat 再读，别把超大文件整个读进内存。
    """
    if url.startswith("file://"):
        path = Path(urllib.request.url2pathname(url[7:]))
        try:
            if path.stat().st_size > MAX_DOWNLOAD_BYTES:
                return b"", "文件超过 8MB，这次不读"
        except OSError as exc:  # noqa: BLE001
            return b"", f"读不了这个文件：{exc}"
        data = path.read_bytes()
        if len(data) > MAX_DOWNLOAD_BYTES:
            return b"", "文件超过 8MB，这次不读"
        return data, ""
    if media_guard is not None:
        try:
            from . import web_fetch

            allow_private = media_guard.is_trusted_origin(url)
            with web_fetch.open_checked(url, DOWNLOAD_TIMEOUT,
                                        allow_private=allow_private) as resp:
                length = int(resp.headers.get("Content-Length") or 0)
                if length and length > MAX_DOWNLOAD_BYTES:
                    return b"", "文件超过 8MB，这次不读"
                data = resp.read(MAX_DOWNLOAD_BYTES + 1)
        except ValueError as exc:
            # open_checked 主动拒收：bad_url / blocked_host / blocked_redirect
            return b"", f"这个地址不允许访问（{str(exc)[:60]}）"
    else:  # pragma: no cover —— 独立加载模块（没有包上下文）时的老路
        req = urllib.request.Request(url, headers={"User-Agent": "AstroSwarm/1.0"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:  # noqa: S310
            length = int(resp.headers.get("Content-Length") or 0)
            if length and length > MAX_DOWNLOAD_BYTES:
                return b"", "文件超过 8MB，这次不读"
            data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        return b"", "文件超过 8MB，这次不读"
    return data, ""


async def read_file(bot, data: dict, group_id: str = "") -> dict:
    """读一个文件段：返回 {name, size, url, text, note, cost_ms}（永不抛异常）。"""
    t0 = time.time()
    name = str((data or {}).get("file_name") or (data or {}).get("name")
               or (data or {}).get("file") or "未命名文件")
    size = int((data or {}).get("file_size") or 0)
    info = {"name": name, "size": size, "url": "", "text": "", "note": "", "cost_ms": 0}
    try:
        source, origin, why = await _resolve_url(bot, str(group_id or ""), data or {})
        if not source:
            info["note"] = why or "拿不到文件内容"
            return info
        info["url"] = source
        if source.startswith(("http://", "https://")):
            # 2026-10-15：DNS + 下载是同步的，**丢线程池**跑 —— 以前直接在协程里
            # 同步调用，最长 25 秒卡住整个机器人（所有通道全停收全停回）。
            data_bytes, why = await asyncio.to_thread(_download, source)
            if not data_bytes:
                info["note"] = why
                return info
        else:
            path = Path(source)
            try:
                if path.stat().st_size > MAX_DOWNLOAD_BYTES:
                    info["note"] = "文件超过 8MB，这次不读"
                    return info
            except OSError as exc:  # noqa: BLE001
                info["note"] = f"读不了这个文件：{exc}"
                return info
            data_bytes = await asyncio.to_thread(path.read_bytes)
            if len(data_bytes) > MAX_DOWNLOAD_BYTES:
                info["note"] = "文件超过 8MB，这次不读"
                return info
        text, note = extract_text(name, data_bytes)
        info["note"] = note
        # 2026-10-16（对抗性复核 bridge/C）：**真下到了才标记可以登记出处**。
        # 以前 describe() 对着任意 http(s) 地址无条件 trust_origin —— 一次被拦的
        # 内网地址（如 169.254.x / 127.0.0.1 的非协议端口）也会被登记成"可信出处"，
        # 同地址第二次就整段放行、正文进提示词：两条消息即两步绕过内网防线。
        info["fetched"] = True
        if len(text) > MAX_PROMPT_CHARS:
            text = text[:MAX_PROMPT_CHARS] + "…（文件太长，已截断）"
        info["text"] = text
        _log(f"[file] 读取文件 {name!r}（来源 {origin}，{len(data_bytes)} 字节 → 正文 {len(info['text'])} 字）"
             + (f"｜备注：{note}" if note else ""))
    except Exception as e:  # noqa: BLE001 —— 读文件失败只影响这一次
        info["note"] = f"读取失败：{str(e)[:100]}"
        _log(f"[file] 读取文件 {name!r} 失败：{e}")
    info["cost_ms"] = int((time.time() - t0) * 1000)
    return info


def describe(info: dict, index: int = 1) -> str:
    """把读取结果拼成给模型看的一段（永远非空 —— 保证"发文件"不会被判成空消息）。"""
    name = info.get("name") or "文件"
    size = info.get("size") or 0
    size_txt = f"（{size / 1024 / 1024:.1f}MB）" if size > 0 else ""
    lines = []
    if info.get("text"):
        lines.append(f"[文件{index}：{name}{size_txt} 的内容]")
        lines.append(info["text"])
        lines.append(f"[文件{index} 内容结束]")
    else:
        lines.append(f"[文件{index}：{name}{size_txt}]（内容读不出来"
                     + (f"：{info['note']}" if info.get("note") else "") + "）")
    url = str(info.get("url") or "")
    if url.startswith(("http://", "https://")):
        # 登记出处（见 media_guard）：这个地址多半是协议端自己的文件服务
        # （本机口/内网口），不登记的话"把文件转到别的平台"会被内网拦截误伤。
        # ⚠ 只有**真读到过内容**（fetched=True）才登记 —— 被防线拦掉的地址
        # 绝不能因为这里"顺手登记"而在下一次放行（2026-10-16 对抗性复核 bridge/C）。
        if media_guard is not None and info.get("fetched"):
            media_guard.trust_origin(url)
        lines.append(f"[文件{index}·转发用] 下载地址：{url}"
                     "（要发到别的平台就调 send_file_to_my_platform"
                     "(platform=目标, url=这个地址, kind=file)）")
    return "\n".join(lines)


_SEG_TYPES_FILE = ("file",)


def _file_segments(raw):
    """从一个 message（list / 单个 dict / CQ 串）里挑出文件段。"""
    out = []
    if isinstance(raw, str):
        for m in re.finditer(r"\[CQ:file,([^\]]*)\]", raw):
            data = {}
            for item in m.group(1).split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    data[k.strip()] = v
            out.append(data)
        return out
    segs = raw if isinstance(raw, list) else [raw]
    for seg in segs:
        if isinstance(seg, dict) and str(seg.get("type") or "") in _SEG_TYPES_FILE:
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            out.append(data)
    return out


async def files_text_from_raw(bot, raw, group_id: str = "") -> str:
    """被引用消息（或任意 raw message）里的文件 → 拼成提示词块；没有文件返回空串。"""
    blocks = []
    for i, data in enumerate(_file_segments(raw), 1):
        info = await read_file(bot, data, group_id=group_id)
        blocks.append(describe(info, i))
    return "\n".join(blocks)
