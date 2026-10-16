# -*- coding: utf-8 -*-
"""发布 Linux 无头端 0.2.9：修掉"引导地址打不开"和服务误报。"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"

NAME = "astroswarm-linux-0.2.9.tar.gz"
SIZE = 90194565
SHA = "393b94eab882bbacd5da32dc89ed9a09537c15ca6ed8e5b0af62d3f24830537d"

NOTE = ("发布 Linux 无头端 v0.2.9：修安装引导与控制台访问说明 —— "
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
                "version": "0.2.9",
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
