# -*- coding: utf-8 -*-
"""线上自检：官网插件市场 + 下载清单，从客户端视角验证。

1. 市场清单签名：必须有 market.json.sig 且能被客户端内置公钥验过（fail closed，签错客户端直接不加载）；
2. 新上的「连续对话能力包」条目在不在、是不是免费、sha256 与本地包一致；
3. 安装包 / 便携版的链接能不能下（HTTP 200）、大小对不对；
4. 包 zip 能不能通过鉴权接口取到（免费包不需要登录）。
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbotmanager.core import plugin_market  # noqa: E402

MARKET = "https://astroswarm.cn/plugins/market.json"
PACK = "https://astroswarm.cn/api/account/plugins/zip?pid=session-chat"
MANIFEST = "https://astroswarm.cn/downloads/manifest.json"
EXPECT_PACK_SHA = "a674c2e9a971b3485067d5e599fe8c5b2ab97981b58463111c633ee25a52a07d"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AstroSwarm-ReleaseCheck/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def main() -> int:
    problems = []
    raw = fetch(MARKET)
    try:
        sig = fetch(MARKET + ".sig").decode()
    except Exception as e:  # noqa: BLE001
        problems.append(f"取签名失败: {e}")
        sig = ""
    ok = plugin_market.verify_manifest(raw, sig) if sig else False
    print("① 市场清单签名:", "通过" if ok else "不通过")
    if not ok:
        problems.append("市场清单签名校验不通过 —— 客户端会拒绝加载整个商店")

    data = json.loads(raw.decode("utf-8"))
    plugins = data["plugins"] if isinstance(data, dict) else data
    entry = next((p for p in plugins if p.get("id") == "session-chat"), None)
    print("② 连续对话能力包条目:", "在" if entry else "不在", f"（共 {len(plugins)} 个插件）")
    if not entry:
        problems.append("市场里没有 session-chat")
    else:
        if entry.get("tier") != "free":
            problems.append(f"tier 不是 free：{entry.get('tier')}")
        if entry.get("sha256") != EXPECT_PACK_SHA:
            problems.append("sha256 与本地包不一致")

    try:
        blob = fetch(PACK)
        digest = hashlib.sha256(blob).hexdigest()
        print(f"③ 包 zip 下载: {len(blob)} 字节 sha256={digest[:12]}… 与本地一致={digest == EXPECT_PACK_SHA}")
        if digest != EXPECT_PACK_SHA:
            problems.append("线上包内容与本地打包不一致（sha256 不符）")
    except Exception as e:  # noqa: BLE001
        problems.append(f"取包失败: {e}")

    manifest = json.loads(fetch(MANIFEST).decode("utf-8"))
    product = next((p for p in manifest["products"] if p.get("id") == "astroswarm"), {})
    print("④ 下载清单版本:", product.get("version"))
    for platform in product.get("platforms", []):
        if platform.get("kind") not in ("installer", "portable"):
            continue
        url = "https://astroswarm.cn" + platform["url"]
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            size = int(resp.headers.get("Content-Length") or 0)
        good = resp.status == 200 and size == platform["size"]
        print(f"   {platform['label']} {platform['version']}: HTTP {resp.status} {size} 字节 与清单一致={good}")
        if not good:
            problems.append(f"{platform['filename']} 线上大小与清单不一致")

    print()
    if problems:
        print("发现问题：")
        for item in problems:
            print(" -", item)
        return 1
    print("线上自检全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
