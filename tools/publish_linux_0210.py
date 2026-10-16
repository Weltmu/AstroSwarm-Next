# -*- coding: utf-8 -*-
"""发布 Linux 无头端 0.2.10：修掉"引导地址打不开"和服务误报。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.10.tar.gz"
SIZE = 90194529
SHA = "91ec7fcd0e1bf8a1fd930c3a0a5e72f4d6e8d6557d7daa651df1b153f1870604"

NOTE = ("发布 Linux 无头端 v0.2.10：控制台界面与桌面端对齐（导航 4 组/6 项一致、工坊入口不再被小白模式藏掉、微信卡片重复提示去掉） —— "
        "① 不再打印打不开的「http://<服务器IP>:7860/」，改成给两种真能用的方式"
        "（本地 ssh -L 隧道 / --host 0.0.0.0 并放行防火墙）；"
        "② 服务判活改成轮询最多 15 秒，真失败时直接把日志里的原因打出来"
        "（以前 restart 后立刻检查，好好的服务被误报「服务没起来」）；"
        "③ 装机脚本加端口预检：端口被占直接报错并给出换端口命令。")


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, ROOT / f"manifest.json.bak_{stamp}_linux029")
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
                "version": "0.2.10",
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


if __name__ == "__main__":
    main()
