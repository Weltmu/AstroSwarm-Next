# -*- coding: utf-8 -*-
"""把官网 downloads/manifest.json 里的 Linux 无头端更新到 0.2.6。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.6.tar.gz"
SIZE = 90181146
SHA = "facea3badaff4e3327aba9de4887624fd6e236dc997ff2370a26e176d79e2b75"

NOTE = ("发布 Linux 无头端 v0.2.6：QQ 官方消息解析补齐 —— 卡片（B站/小程序/QQ音乐等，"
        "从原始 content 里解出标题·说明·链接·来源）、附件（文件名/体积/尺寸，"
        "视频语音看不了就直说）、纯文本链接（自动抓标题与正文摘要再给 AI，"
        "一条消息最多 2 个链接、只走公网）；同步日志默认保留 10 天。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux026")
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
                "version": "0.2.6",
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
