# -*- coding: utf-8 -*-
"""出站媒体的来源校验：把「模型填什么就发什么」改成「只发得出它该拿得到的东西」。

2026-10-15 安全加固。挡的是两个**真实存在**的高危面：

1. 任意本地文件被当"媒体"发出去
   工具链上模型只有一个字符串参数（`url` / `path`），适配器却把它**原样**交给协议端：
   · OneBot 的 send_file → `upload_group_file(file=...)`，NapCat 认本地路径与 `file://`；
   · 跨平台转发 `_read_local()` → 直接 `Path(...).read_bytes()`。
   于是群里任何人只要让模型相信"帮我发个文件"，就能把 `.env`、`data/` 里的密钥配置、
   日志、老板桌面上的私人文件当"文件"发给全群 —— 提示注入的破坏力直接落到本机磁盘上。
   现在本地路径只认机器人自己产出的目录（下载目录 / 插件数据目录）；其余一律拒绝。

2. 拿 http(s) 地址当跳板打内网（SSRF）
   这些地址**不一定由我们取**：OneBot 的 NapCat、QQ 官方开放平台都会替我们取回来。
   内网/回环地址等于把内网服务当"图片源"，最坏能把云元数据渲染成图片发进群。
   现在只放行两种地址：
   · 公网地址（`web_fetch.check_url` 那套判定，逐跳复查跳转）；
   · **机器人自己刚递给模型的地址**（收到图片/文件时登记，见 `trust_origin`）——
     NapCat 给的图片地址常常就是 `127.0.0.1:3000` 这种本机口，一律按内网拦掉会误伤
     "把 QQ 上的图转到微信"这条已经在用的功能。

对外只有两个函数，职责分得很清：
- `precheck_source()` —— 同步入口（模型的工具处理器）用的预筛，不查 DNS：
  本地路径、协议、UNC 这些当场就判；网址原样放行；
- `check_source()`   —— 投递前的权威检查（异步侧丢线程池跑）：解析域名、复查跳转，
  **所有**要出站的东西最终都得过这一关（就地发媒体、跨平台排队、跨平台取字节）。

免得日后又出现"这条路查了、那条路没查"。
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import web_fetch

# 这些协议一律不认（file:// 会被换算成本地路径后按本地规矩判，见下）
_BAD_SCHEMES = ("ftp", "ftps", "data", "gopher", "dict", "smb", "jar", "blob", "ws", "wss")
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_RE = re.compile(r"^(\\\\|//)")

# 机器人自己递出去的地址的"出处"（scheme://host:port），进程生命周期内有效。
# 只登记协议端媒体服务这一个来源，条数很小；给上限是防有人拿它当内存垃圾桶。
_TRUSTED_MAX = 32
_trusted_origins: list = []


def _data_root() -> Path:
    return Path.cwd() / "data"


def allowed_local_roots() -> list:
    """允许当媒体发出去的本地目录。

    只认机器人**自己产出**的两类：
      · `data/downloads`    —— 「下载小文件」「图片压缩」的落点；
      · `data/workshop_data` —— 插件自己画出来的图（排行榜、二维码等）。
    配置、日志、密钥、.env、桌面文件都不算"媒体"，不进这个名单。
    """
    root = _data_root()
    return [root / "downloads", root / "workshop_data"]


def origin_of(url: str) -> str:
    """取出 `scheme://host[:port]`；取不到返回空串。"""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}{port}"


def trust_origin(url: str) -> str:
    """登记一个"这是机器人自己给模型的地址"（协议端媒体服务）。

    调用点：收到图片 / 文件、把原图地址写进提示词的那一刻。登记的是**出处**
    （scheme://host:port）而不是完整地址 —— 模型转述时常常改查询串。
    """
    origin = origin_of(url)
    if not origin:
        return ""
    if origin not in _trusted_origins:
        _trusted_origins.append(origin)
        while len(_trusted_origins) > _TRUSTED_MAX:
            _trusted_origins.pop(0)
    return origin


def is_trusted_origin(url: str) -> bool:
    origin = origin_of(url)
    return bool(origin) and origin in _trusted_origins


def _clear_trusted() -> None:
    """清空登记（单测用；生产不调）。"""
    _trusted_origins[:] = []


def _file_url_to_path(value: str) -> str:
    """`file:///D:/x.jpg` → `D:/x.jpg`（`file://` 是协议端常用的本地文件写法）。"""
    rest = unquote(str(value)[len("file:"):])
    stripped = rest.lstrip("/")
    if _WIN_DRIVE_RE.match(stripped):
        return stripped                    # Windows 盘符路径
    if rest.startswith("/"):
        return "/" + stripped              # POSIX 绝对路径（本机虽在 Windows，别写死）
    return stripped


def _local_check(raw: str, allow_any_local: bool) -> tuple:
    """本地路径校验：返回 (放行后的路径, 错误码, 说明)。"""
    path = Path(raw)
    try:
        if not path.is_file():
            return "", "not_found", "机器人这边找不到这个文件"
        resolved = path.resolve()
    except OSError as exc:  # noqa: BLE001 —— 路径畸形（超长、非法字符）也算不通过
        return "", "bad_path", str(exc)[:120]
    if allow_any_local:
        return str(resolved), "", ""
    for root in allowed_local_roots():
        try:
            resolved.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        return str(resolved), "", ""
    return "", "path_not_allowed", (
        "只允许发机器人自己下载或生成的目录里的文件"
        "（data/downloads、插件数据目录）；本机其它文件不能当媒体发出去")


def _normalize(source) -> tuple:
    """拆掉"看着像网址、其实是别的写法"的壳。返回 (待判地址, 错误码, 说明)。"""
    raw = str(source or "").strip()
    if not raw:
        return "", "missing_source", "没给地址或路径"
    if _UNC_RE.match(raw):
        # `\\主机\共享\文件` 读的是**别人的机器**，不是本机文件
        return "", "bad_path", "不支持网络共享路径"
    if raw.lower().startswith("file:"):
        raw = _file_url_to_path(raw)
        if not raw:
            return "", "bad_url", "file:// 地址看不懂"
    return raw, "", ""


def _scheme_of(raw: str) -> str:
    """协议名；Windows 盘符（`D:\\x`）不算协议。"""
    if _WIN_DRIVE_RE.match(raw):
        return ""
    try:
        return (urlparse(raw).scheme or "").lower()
    except ValueError:
        return ""


def precheck_source(source, allow_any_local: bool = False) -> tuple:
    """同步入口能用的**快速预筛**：只判"不需要 DNS、不依赖登记表"的部分。

    网址一律原样放行（留给投递前的权威检查 `check_source`）；本地路径、协议、
    UNC 这些当场就能判的在这里就判掉 —— 这样"让机器人把本机某个文件转出去"
    这类请求在**排队那一刻**就被拒，模型可以立刻如实回话，而不是排上队再失败。

    为什么不做 DNS：这个函数跑在事件循环里的同步代码上（模型的工具处理器），
    一次卡住的域名解析能把整个机器人拖住好几秒。
    """
    raw, why, hint = _normalize(source)
    if why:
        return "", why, hint
    scheme = _scheme_of(raw)
    if scheme in ("http", "https"):
        return raw, "", ""
    if scheme in _BAD_SCHEMES:
        return "", "bad_scheme", f"不支持 {scheme}:// 这种地址"
    return _local_check(raw, allow_any_local)


def check_source(source, allow_any_local: bool = False) -> tuple:
    """出站媒体的**权威检查**（投递前那道闸）。返回 `(放行后的地址, 错误码, 说明)`。

    错误码非空即拒绝；放行时地址可能与传入的不同（`file://` 会换算成本地路径、
    网址会规范化）。这一步会解析域名 —— 调用方在事件循环里时请丢线程池跑
    （`await asyncio.to_thread(media_guard.check_source, ...)`）。

    `allow_any_local` 只给**确认过是管理员本人**的会话开（微信私聊那条通道）——
    老板让机器人把本机某张表发出来是正常需求，群友借着提示注入提同样要求则不是。
    """
    raw, why, hint = _normalize(source)
    if why:
        return "", why, hint
    scheme = _scheme_of(raw)
    if scheme in ("http", "https"):
        # 机器人自己刚递给模型的地址（协议端媒体服务）先认下来
        if is_trusted_origin(raw):
            return raw, "", ""
        target, why = web_fetch.check_url(raw)
        if why:
            hint = ("不接受这个地址：内网、本机或解析不出来的主机一律不发"
                    if why == "blocked_host" else "只支持公网 http / https 地址")
            return "", why, hint
        return target, "", ""
    if scheme in _BAD_SCHEMES:
        return "", "bad_scheme", f"不支持 {scheme}:// 这种地址"
    return _local_check(raw, allow_any_local)
