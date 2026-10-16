# -*- coding: utf-8 -*-
"""把官网 downloads/manifest.json 里的 Linux 无头端更新到 0.2.5。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.5.tar.gz"
SIZE = 90172906
SHA = "81eec6b151138b375b85692016089a468c1ac72edac779b0508ce9cd9698672b"

NOTE = ("发布 Linux 无头端 v0.2.5：日志自动清理 —— 默认只保留最近 10 天（跨天自动按日期归档，"
        "超期删除，正在写的那份永不删），控制台读日志时顺手清一次；"
        "同步上一批：控制台工坊可看进度/可取消/可下载到本地/可撤回投稿，"
        "QQ 官方按钮可收可发、频道 @ 能回话、审核被拦会提醒管理员。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux025")
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
                "version": "0.2.5",
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
