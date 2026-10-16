# -*- coding: utf-8 -*-
"""把官网 manifest 的 Linux 无头端更新到 0.2.8（含跨平台互通/身份绑定/自学/识图开关）。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.8.tar.gz"
SIZE = 90194110
SHA = "2f7967d464789cb970767a419b7f07652364b78c093ee2e16ee891bb4ad15569"

NOTE = ("发布 Linux 无头端 v0.2.8：跨平台互通内置升级 —— 身份绑定（验证码，微信↔QQ 算同一个人）、"
        "跨平台发消息与发任意文件（图片/语音/视频/文件）、跨平台主动聊天（严格按目标身份发，"
        "绝不误发他人）、AI 遇到不会的事可自己写插件（仅主人可触发）、识图开关、"
        "以及控制台文案全面精简（134 处）。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux028")
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
                "version": "0.2.8",
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
