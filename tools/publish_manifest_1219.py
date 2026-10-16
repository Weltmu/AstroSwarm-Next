# -*- coding: utf-8 -*-
"""把官网 downloads/manifest.json 里的星群版本更新到 1.2.19。

规则（docs/官网改版-交接提示词）：downloads 里的版本号与安装包链接归客户端会话维护；
改之前先跑 .coord/site_fingerprint.sh check，改完追加 handover.md 并 snapshot。
本脚本只改 astroswarm 那条产品的版本与两个 Windows 平台条目，其它产品一个字不动。
"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

SETUP = ("AstroSwarm_Setup_1.2.19.exe", 271338931,
         "9690f3b9a95d06fb97fc87187b23238679df9dbe3d0bba4ccbe4deb650eec703")
PORTABLE = ("AstroSwarm_v1.2.19_Portable_20261001.zip", 354375706,
            "72c150a20422d42cd76902ab6afc6b6cbe8d7733a52bb4f62ed687156393e575")
NEW_VERSION = "1.2.19"
NOTES = ("微信通道已免费开放（扫码即用，不再需要开通验证）；QQ 官方通道修好了"
         "「群里 @ 它不说话」的格式兼容问题；插件商店改成卡片式并加了搜索；"
         "插件参数弹窗文字不再被压扁。需要官方代为部署或长期维护可以联系我们。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    backup = ROOT / f"manifest.json.bak_{stamp}"
    shutil.copy2(MANIFEST, backup)
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    today = time.strftime("%Y-%m-%d")
    changed = []
    for product in data.get("products", []):
        if product.get("id") != "astroswarm":
            continue
        product["version"] = NEW_VERSION
        product["updated_at"] = today
        product["notes"] = NOTES
        for platform in product.get("platforms", []):
            if platform.get("id") == "windows-installer":
                name, size, sha = SETUP
            elif platform.get("id") == "windows-portable":
                name, size, sha = PORTABLE
            else:
                continue
            platform.update({
                "version": NEW_VERSION,
                "filename": name,
                "url": "/downloads/" + name,
                "size": size,
                "sha256": sha,
                "updated_at": today,
            })
            changed.append((platform["id"], name, size))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("backup:", backup)
    for item in changed:
        print("updated:", item)


if __name__ == "__main__":
    main()
