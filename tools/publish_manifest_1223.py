# -*- coding: utf-8 -*-
r"""发版 1.2.23：把官网 downloads/manifest.json 里 astroswarm 那条更新到 1.2.23。

只改 astroswarm 产品的版本、notes 与两个 Windows 平台条目，并在 updates 顶部
追加一条发布记录（同文案已存在就不重复加），**其它产品一个字不动**。
用法（在服务器 /opt/qbot_website/downloads 目录下跑）：
    python3 publish_manifest_1223.py
"""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website/downloads")
MANIFEST = ROOT / "manifest.json"
NEW_VERSION = "1.2.23"

SETUP_GLOB = "AstroSwarm_Setup_1.2.23.exe"
PORTABLE_GLOB = "AstroSwarm_v1.2.23_Portable_*.zip"

NOTES = ("修「中文安装路径下无法更新」：更新脚本从批处理换成 PowerShell（全程 Unicode，"
         "UTF-8 带 BOM）——以前安装路径带中文时会把更新装进乱码目录、重启弹「Windows "
         "找不到文件」；同时保留上一版的三层并发防护（更新中不重复触发 / 等所有安装器退出"
         "再拉起程序 / 安装期间启动会被拦下）。已装到乱码目录的，用这版装包重新装回原目录即可。")


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
    backup = ROOT / f"manifest.json.bak_{stamp}_1223"
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
    updates = data.get("updates")
    if not isinstance(updates, list):
        updates = []
        data["updates"] = updates
    if not any(isinstance(u, dict) and u.get("text") == NOTES for u in updates):
        updates.insert(0, {"date": today, "product": "星巢 桌面端", "text": NOTES})
        changed.append(("updates", "追加一条发布记录", 0))
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("备份:", backup)
    for item in changed:
        print("已更新:", item)
    again = json.loads(MANIFEST.read_text(encoding="utf-8"))
    v = next(p["version"] for p in again["products"] if p.get("id") == "astroswarm")
    print("回读版本:", v, "→", "OK" if v == NEW_VERSION else "不一致，检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
