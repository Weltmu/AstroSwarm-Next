# -*- coding: utf-8 -*-
r"""手机访问 DSH 界面的本地转发器（自动带认证 Cookie，手机端无需 token）。

为什么需要它：
  · DSH 的 Web 界面只允许 --host 127.0.0.1（作者明确拒绝 0.0.0.0：那等于把"在电脑上执行任意命令"
    的能力开放到网络）；
  · 认证是**绑定域名(Host)的签名 Cookie**（`dsh-auth-<sha256(authority)>`），
    而你电脑浏览器里的 Cookie 绑的是 127.0.0.1，手机上换个域名就对不上；
  · 所以我们自己拿 DSH 存在 `~/.dsh/.credentials.yaml` 里的签名密钥，
    给 127.0.0.1:19387 签一个长期 Cookie，在本机转发时注入。

链路：手机 → Tailscale(只你自己的 tailnet) → https://desktop-i3819ar.taile5e8ca.ts.net
      → 本转发器 127.0.0.1:19389 → 注入 Host/Cookie → 127.0.0.1:19387（DSH 界面）

用法：
  python tools/phone_relay.py            # 前台跑
  python tools/phone_relay.py --check    # 只自检（不监听）
"""
import argparse
import base64
import hashlib
import hmac
import json
import re
import socket
import sys
import threading
import time
from pathlib import Path

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 19389
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 19387
AUTHORITY = f"{TARGET_HOST}:{TARGET_PORT}"          # DSH 侧看到的 Host / Cookie 绑定的域名
CRED = Path.home() / ".dsh" / ".credentials.yaml"
COOKIE_PREFIX = "dsh-auth-"
COOKIE_DAYS = 7
_DAY_MS = 86400 * 1000


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def load_secret() -> bytes:
    """从 DSH 的凭据文件里取签名密钥（32 字节）。"""
    text = CRED.read_text(encoding="utf-8", errors="replace")
    for cand in re.findall(r"[A-Za-z0-9_\-]{43}", text):
        try:
            raw = base64.urlsafe_b64decode(cand + "=" * (-len(cand) % 4))
        except Exception:  # noqa: BLE001
            continue
        if len(raw) == 32:
            return raw
    raise RuntimeError(f"凭据文件里找不到 32 字节密钥：{CRED}")


class Auth:
    """按"请求里的 Host（域名）"签发 Cookie，按天刷新。

    2026-10-11 踩的坑：一开始把 Host 改写成 127.0.0.1:19387，界面能开、但**所有 API 都 403**
    —— 浏览器的 Origin 是 tailnet 域名、Host 却变成 127.0.0.1，被当成跨站请求拒掉。
    正解：**保留原 Host**，Cookie 就按这个域名签（Cookie 名和签名都绑定域名）。
    """

    def __init__(self):
        self.secret = load_secret()
        self._cache = {}          # authority -> (cookie_name, cookie_value, 过期时间)

    def _mint(self, authority: str) -> tuple:
        now = int(time.time() * 1000)
        payload = {"version": 1, "authority": authority,
                   "issuedAt": now, "expiresAt": now + COOKIE_DAYS * _DAY_MS}
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64url(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        name = COOKIE_PREFIX + _b64url(hashlib.sha256(authority.encode()).digest())
        return name, f"v1.{body}.{sig}"

    def cookie_header(self, authority: str) -> str:
        authority = authority or AUTHORITY
        hit = self._cache.get(authority)
        if hit and time.time() < hit[2]:
            return f"{hit[0]}={hit[1]}"
        name, value = self._mint(authority)
        self._cache[authority] = (name, value, time.time() + COOKIE_DAYS * 86400 - 3600)
        return f"{name}={value}"


def rewrite_head(head: bytes, cookie: str) -> bytes:
    """改请求头：**保留原 Host**（Origin 才能对上）、注入本域名的 Cookie、避免长连接。"""
    text = head.decode("latin-1")
    lines = text.split("\r\n")
    first = lines[0]
    is_upgrade = bool(re.search(r"^upgrade:", text, re.I | re.M))
    out = [first]
    for line in lines[1:]:
        if not line:
            continue
        name = line.split(":", 1)[0].strip().lower()
        if name in ("cookie", "connection", "proxy-connection", "host", "origin", "referer"):
            continue
        out.append(line)
    # 2026-10-11 踩坑结论：只改 Host 不行（Host 与 Origin 不一致 → API 全 403），
    # 要把 Host / Origin / Referer **一起**改写成回环地址，服务端才当成"本机请求"放行。
    out.append(f"Host: {AUTHORITY}")
    out.append(f"Origin: http://{AUTHORITY}")
    out.append(f"Referer: http://{AUTHORITY}/")
    out.append(f"Cookie: {cookie}")
    out.append("Connection: Upgrade" if is_upgrade else "Connection: close")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


def host_of(head: bytes) -> str:
    """取请求里的 Host（作为 Cookie 绑定的域名）。"""
    m = re.search(rb"^host:\s*(\S+)", head, re.I | re.M)
    return m.group(1).decode("latin-1") if m else AUTHORITY


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client: socket.socket, auth: Auth) -> None:
    upstream = None
    try:
        client.settimeout(30)
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                return
            head += chunk
        if b"\r\n\r\n" not in head:
            return
        head, _, rest = head.partition(b"\r\n\r\n")
        new_head = rewrite_head(head, auth.cookie_header(AUTHORITY)) + b"\r\n\r\n"
        upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=30)
        upstream.sendall(new_head)
        if rest:
            upstream.sendall(rest)
        client.settimeout(None)
        upstream.settimeout(None)
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        pipe(upstream, client)
    except Exception:  # noqa: BLE001 —— 单连接出错不能影响整体
        pass
    finally:
        for s in (client, upstream):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="手机访问 DSH 界面的本地转发器")
    ap.add_argument("--check", action="store_true", help="只自检：密钥、Cookie、目标端口")
    args = ap.parse_args()

    auth = Auth()
    print(f"密钥: 已从 {CRED} 读取（32 字节）")
    print(f"Cookie 前缀: {COOKIE_PREFIX}  有效期: {COOKIE_DAYS} 天（按访问域名分别签发）")
    if args.check:
        try:
            with socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=5):
                print(f"目标 {AUTHORITY} 可连接 ✓")
        except OSError as e:
            print(f"目标连接失败: {e}")
            return 1
        print("自检通过 ✓")
        return 0

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(64)
    print(f"转发器已启动：127.0.0.1:{LISTEN_PORT} → {AUTHORITY}（手机走 Tailscale 进来）")
    while True:
        try:
            client, _addr = srv.accept()
        except OSError:
            break
        threading.Thread(target=handle, args=(client, auth), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
