# -*- coding: utf-8 -*-
r"""发版 1.2.20：把官网 downloads/manifest.json 里 astroswarm 那条更新到 1.2.20。

只改 astroswarm 产品的版本、notes 与两个 Windows 平台条目，**其它产品一个字不动**。
（沿用 tools/publish_manifest_1219.py 的做法：改前备份、只动该动的字段。）
用法（在服务器 /opt/qbot_website/downloads 目录下跑）：
    python3 publish_manifest_1220.py
"""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"
NEW_VERSION = "1.2.20"

# 文件名 → 由脚本自己算大小与 sha256（不手抄，避免抄错）
SETUP_GLOB = "AstroSwarm_Setup_1.2.20.exe"
PORTABLE_GLOB = "AstroSwarm_v1.2.20_Portable_*.zip"

NOTES = ("换新名字「星巢 Nest」（网站仍是 AstroSwarm 星群）；启动动画换成点阵波浪 —— 点阵铺满卡片、"
         "中间下方「启动中」，文字后面跟着等大的点阵进度条；关闭不再有动画卡片，关窗即退、"
         "后台静默收尾，速度从约 2.5 秒缩到 0.5 秒内；人设设置三块合并成一张卡片（胶囊切换预设 / 自定义）；"
         "账号资料自动同步 —— 官网改昵称 / 换头像，客户端自动跟上；程序图标更新。")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pick(pattern: str) -> Path:
    hits = sorted(ROOT.glob(pattern))
    if not hits:
        print(f"[x] 服务器上找不到 {pattern}")
        sys.exit(2)
    return hits[-1]


def main() -> int:
    setup = pick(SETUP_GLOB)
    portable = pick(PORTABLE_GLOB)
    for f in (setup, portable):
        print(f"  找到 {f.name} {f.stat().st_size} 字节 sha256={sha256(f)[:16]}…")

    stamp = time.strftime("%Y%m%d_%H%M")
    backup = ROOT / f"manifest.json.bak_{stamp}_1220"
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
            pid = platform.get("id")
            if pid == "windows-installer":
                f = setup
            elif pid == "windows-portable":
                f = portable
            else:
                continue
            platform.update({
                "version": NEW_VERSION,
                "filename": f.name,
                "url": "/downloads/" + f.name,
                "size": f.stat().st_size,
                "sha256": sha256(f),
                "updated_at": today,
            })
            changed.append((pid, f.name, f.stat().st_size))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("备份:", backup)
    for item in changed:
        print("已更新:", item)
    # 回读校验
    again = json.loads(MANIFEST.read_text(encoding="utf-8"))
    v = next(p["version"] for p in again["products"] if p.get("id") == "astroswarm")
    print("回读版本:", v, "→", "OK" if v == NEW_VERSION else "不一致，检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
