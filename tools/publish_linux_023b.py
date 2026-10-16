# -*- coding: utf-8 -*-
"""把官网 downloads/manifest.json 里的 Linux 无头端更新到 0.2.3。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.3.tar.gz"
SIZE = 90164587
SHA = "396bddd29656acdd978b2903ab6c522b671eb8069642a07351e5133cfcd73965"

NOTE = (
    "发布 Linux 无头端 v0.2.3：修一处把关不严 —— 控制台工坊原来把官网下载的能力包也列进"
    "「已装插件」，而且这类包还能投稿上传；现在工坊只列「自己生成的」插件（其余给一句提示"
    "去插件管理页），投稿也只对自研插件开放。桌面端同一处一起修了。"
)


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux023")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    today = time.strftime("%Y-%m-%d")
    updated = []
    for product in data.get("products", []):
        if product.get("id") != "astroswarm":
            continue
        for platform in product.get("platforms", []):
            if platform.get("id") != "linux-headless":
                continue
            platform.update({
                "version": "0.2.3",
                "filename": NAME,
                "url": "/downloads/" + NAME,
                "size": SIZE,
                "sha256": SHA,
                "updated_at": today,
                "desc": "解压后跑 install.sh（内置依赖 wheels，装控制台与机器人都零联网）",
            })
            updated.append((platform["id"], NAME, SIZE))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    data.setdefault("updates", []).insert(
        0, {"date": today, "product": "星群 Linux 无头端", "text": NOTE})
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("备份后缀:", stamp)
    for item in updated:
        print("已更新:", item)
    if not updated:
        print("⚠ 没找到 linux-headless 条目")


if __name__ == "__main__":
    main()
