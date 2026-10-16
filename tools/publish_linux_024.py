# -*- coding: utf-8 -*-
"""把官网 downloads/manifest.json 里的 Linux 无头端更新到 0.2.4。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.4.tar.gz"
SIZE = 90170321
SHA = "3f5a32f4dd8a0e619623ae1e504ab180df047a99f58a0b957e644fef80c4b6f2"

NOTE = ("发布 Linux 无头端 v0.2.4：控制台工坊补齐四件事 —— 生成改异步（有进度、可取消，"
        "取消不会立刻断、界面照实说）、插件可下载到本地（不用再去服务器上拿）、"
        "投稿记录与撤回、生成模型单独配置；插件侧同步 QQ 官方 P2：按钮可收可发"
        "（发不了按钮的通道自动降级成文字选项）、频道里 @ 也能回话、平台审核被拦会提醒管理员。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux024")
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
                "version": "0.2.4",
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
