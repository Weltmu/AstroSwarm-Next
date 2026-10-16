# -*- coding: utf-8 -*-
"""把官网 downloads/manifest.json 里的 Linux 无头端更新到 0.2.0。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

LINUX = ("astroswarm-linux-0.2.0.tar.gz", 90154767,
         "a712c5d657cc955958163e17a1cfaa356689b3335dc983e549d89f636368570d")
NOTE = ("发布 Linux 无头端 v0.2.0：与桌面端同步这一批修复 —— 插件能发图片/文件/语音/音乐卡片、可主动发到指定目标（带白名单，留空=一个都不许发）、新增展开短链/抓正文/下载小文件/图片压缩四个重活动作、被拉进新群按人设打招呼（不占主动额度）、日志里事件名翻人话且能只看官方通道、控制台新增「新群打招呼」与「插件主动发送白名单」两个开关；其余同 v0.1.9 —— 网页控制台新增「机器人 AI（群聊）」"
        "（群聊回话开关、引用/聊天记录/图片解析、群里主动搭话、允许回话的群勾选、免@连续对话），"
        "QQ 区新增「群消息：全量 / 仅 @」一行与「官方通道体检」；插件商店改成卡片式并加了搜索与分类；"
        "插件全部免费、微信通道不再需要开通验证；机器人侧同步带上「连续对话能力包」与群里 @ 不回话的修复。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux018")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    today = time.strftime("%Y-%m-%d")
    name, size, sha = LINUX
    changed = []
    for product in data.get("products", []):
        if product.get("id") != "astroswarm":
            continue
        for platform in product.get("platforms", []):
            if platform.get("id") != "linux-headless":
                continue
            platform.update({
                "version": "0.2.0",
                "filename": name,
                "url": "/downloads/" + name,
                "size": size,
                "sha256": sha,
                "updated_at": today,
                "desc": "解压后跑 install.sh（内置依赖 wheels，装控制台与机器人都零联网）",
            })
            changed.append((platform["id"], name, size))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    updates = data.setdefault("updates", [])
    updates.insert(0, {"date": today, "product": "星群 Linux 无头端", "text": NOTE})
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("备份后缀:", stamp)
    for item in changed:
        print("已更新:", item)


if __name__ == "__main__":
    main()
