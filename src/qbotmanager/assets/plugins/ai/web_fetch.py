# -*- coding: utf-8 -*-
"""取网页（共用）：给 AI 的 `read_link` 工具和插件动作共用一套。

为什么要单独一个模块：以前这套逻辑只写在插件动作里（只有工坊插件用得上），
AI 自己在聊天里遇到"B站分享/文章链接"时**没有工具可调**，只能瞎猜标题 ——
2026-10-03 老板问到这块，就把实现提到 core 里，两边共用。

安全规矩（和插件动作完全一致）：
- 只允许公网 http / https；内网地址（127./10./192.168./169.254./localhost/裸主机名）一律拒绝；
- 超时 20 秒、单次最多读 400KB、正文最多 2 万字；
- 只做 GET，不带任何凭证。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "AstroSwarm-Plugin/1.0"
FETCH_TIMEOUT = 20
MAX_READ_BYTES = 400_000
TEXT_LIMIT = 20_000
MAX_REDIRECTS = 5

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _ip_is_private(ip) -> bool:
    """这个 IP 是不是"不许碰"的（本机 / 内网 / 链路本地 / 保留段）。

    2026-10-15：IPv4-mapped 的 IPv6（`::ffff:127.0.0.1`）要**按里面那个 IPv4 判**
    —— 它本身不落在 `is_loopback` 上，会被漏放。
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _resolve_ips(host: str) -> list:
    """把域名解析成 IP 列表；解析不了返回空。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    out = []
    for info in infos:
        try:
            addr = info[4][0]
        except (IndexError, TypeError):
            continue
        if addr not in out:
            out.append(addr)
    return out


def is_private_host(host: str) -> bool:
    """内网 / 本机 / 不明主机名一律算"不能碰"。

    2026-10-15 **重要修复**：以前只做字面量判断（`localhost` / `127.` / `10.` …），
    带点的域名一律放行 —— 于是这些全都能绕过：
      · `127.0.0.1.nip.io`、`foo.127.0.0.1.nip.io`（通配 DNS 服务，解析到 127.0.0.1）
      · `localhost.`（**尾点**，解析到 ::1）
      · `metadata.google.internal` 之类的内网别名
    别人在群里发一个链接，机器人就会替他去读内网服务 / 云元数据。现在带点的域名
    **解析后再逐 IP 判**；解析不出来（不存在的域名）也按"不碰"处理。
    """
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return True
    if name in ("localhost", "localhost.localdomain", "0.0.0.0"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        ip = None
    if ip is not None:
        return _ip_is_private(ip)
    if "." not in name:
        # 裸主机名走内网解析（如 intranet），拒绝
        return True
    ips = _resolve_ips(name)
    if not ips:
        return True
    for addr in ips:
        try:
            if _ip_is_private(ipaddress.ip_address(addr)):
                return True
        except ValueError:
            return True
    return False


def check_url(url: str, allow_private: bool = False) -> tuple[str, str]:
    """返回 (规范化后的 URL, 拒绝原因)；合法时原因为空串。

    `allow_private` 只给 `media_guard` 认过出处的地址用（协议端自己的媒体服务就在
    本机/内网口上，不放开就用不了"把 QQ 上的图转到微信"）。**别的调用方一律别传**。
    """
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "", "bad_url"
    if not allow_private and is_private_host(parsed.hostname):
        return "", "blocked_host"
    return parsed.geturl(), ""


class _NoRedirect(HTTPRedirectHandler):
    """不让 urllib 自动跟随跳转 —— 跳转目标要由我们**逐跳复查**。

    2026-10-15：`urlopen` 默认自动跟随，而跳转目标**不会**再过 `check_url`：
    一个公网地址 302 到 `169.254.169.254`（云元数据）就绕过了全部内网拦截。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _origin_of(url: str) -> str:
    """`scheme://host:port`（比"同源"用；取不到返回空串）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _open_checked(target: str, timeout: int, max_hops: int = MAX_REDIRECTS,
                  allow_private: bool = False, method: str = "GET",
                  data: bytes | None = None, headers: dict | None = None):
    """带「每一跳都校验」的请求。返回最后一个响应对象；跳转超标或跳到内网就抛。

    2026-10-15 扩了 method/data/headers：`_act_http_request`（插件动作里的通用 HTTP）
    以前自己写裸 `urlopen` —— 那个**自动跟随跳转且不复查**，一个公网地址 302 到
    169.254.169.254 就把内网拦截整个绕过去了。现在它和取网页、下载文件共用这一份。

    跳转按 HTTP 惯例处理：301/302/303 丢掉请求体改 GET，307/308 保留方法；
    **跨域跳转丢掉 Authorization**（不把凭证带给别的站）。
    """
    url = target
    cur_method = str(method or "GET").upper()
    cur_data = data
    req_headers = dict(headers or {})
    for _ in range(max_hops + 1):
        send_headers = {"User-Agent": USER_AGENT}
        send_headers.update({str(k): str(v) for k, v in req_headers.items()})
        req = Request(url, data=cur_data, method=cur_method, headers=send_headers)
        try:
            return build_opener(_NoRedirect).open(req, timeout=timeout)
        except HTTPError as exc:
            if exc.code not in _REDIRECT_CODES:
                raise
            loc = exc.headers.get("Location", "") if exc.headers else ""
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            if not loc:
                raise
            new_url = urljoin(url, loc)
            _, why = check_url(new_url, allow_private=allow_private)
            if why:
                raise ValueError("blocked_redirect:%s" % why)
            if _origin_of(new_url) != _origin_of(url):
                req_headers.pop("Authorization", None)
            if exc.code in (301, 302, 303) and cur_method not in ("GET", "HEAD"):
                cur_method, cur_data = "GET", None
            url = new_url
    raise ValueError("too_many_redirects")


def plain_text(html: str) -> str:
    """粗暴去标签取正文（够 AI 读；不追求完美排版）。"""
    text = _SCRIPT_RE.sub(" ", str(html or ""))
    text = _TAG_RE.sub(" ", text)
    for src, dst in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(src, dst)
    return _SPACE_RE.sub(" ", text)


def page_title(html: str) -> str:
    match = _TITLE_RE.search(str(html or ""))
    if not match:
        return ""
    return " ".join(plain_text(match.group(1)).split())[:120]


def open_checked(url: str, timeout: int = FETCH_TIMEOUT, allow_private: bool = False,
                 method: str = "GET", data: bytes | None = None,
                 headers: dict | None = None):
    """校验过主机、且**每一跳跳转都复查**的请求（调用方负责 read / close）。

    给 `fetch` 之外的入口共用 —— 例如插件动作里的「下载小文件」「通用 HTTP 请求」、
    跨平台转发的取字节。2026-10-15 之前那些入口各自写了一套前缀黑名单、还直接
    `urlopen`：既不拦内网别名，也没禁 `file://`，而且跳转不复查。内网拦截只此一份，
    才不会再有第二个入口漏掉。

    `allow_private` 见 `check_url`：只有 `media_guard` 认过出处的地址才配传 True。
    """
    target, why = check_url(url, allow_private=allow_private)
    if why:
        raise ValueError(why)
    return _open_checked(target, timeout, allow_private=allow_private,
                         method=method, data=data, headers=headers)


def fetch(url: str, want: str = "text", limit: int = TEXT_LIMIT,
          timeout: int = FETCH_TIMEOUT) -> dict:
    """取网页。want="link" 只回跟随跳转后的真实地址；want="text" 回标题 + 正文。

    返回里 ok=False 时 error 是 `bad_url` / `blocked_host` / `fetch_failed`，
    调用方（AI 工具或插件动作）照原样告诉用户，别编内容。
    """
    target, why = check_url(url)
    if why:
        return {"ok": False, "error": why}

    def _do():
        to = max(1, min(int(timeout or FETCH_TIMEOUT), 30))
        # 走 _open_checked：**每一跳跳转都复查主机**，不能靠 urlopen 自动跟随
        with _open_checked(target, to) as resp:
            raw = resp.read(MAX_READ_BYTES).decode("utf-8", "replace")
            return int(getattr(resp, "status", 200)), str(resp.geturl()), raw

    # ⚠ 本函数是**同步**的。调用方如果在事件循环里，请用
    #   `await asyncio.to_thread(web_fetch.fetch, ...)`，否则会卡住整个机器人（最长 30 秒）。
    #   2026-10-15：以前这里还有一段"先丢线程池、再同步跑一次"的死代码 ——
    #   分支恒为假（`if False else (None, None, None)`），但 `run_in_executor` 那一次
    #   请求**真的会发出去**，于是同一个地址被请求两遍、事件循环还被同步那次卡住。
    try:
        status, final_url, body = _do()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "fetch_failed", "detail": str(exc)[:200]}

    if want == "link":
        return {"ok": True, "status": status, "url": final_url,
                "changed": final_url != target}
    body_text = plain_text(body)
    return {"ok": True, "status": status, "url": final_url,
            "title": page_title(body), "text": body_text[: max(1, int(limit))],
            "truncated": len(body_text) > int(limit)}
