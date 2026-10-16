# -*- coding: utf-8 -*-
"""发布 Linux 无头端：通用版（读包本身拿版本/大小/sha256，不再每个版本写一个脚本）。

用法：
    python tools/publish_linux.py --package dist_package/astroswarm-linux-0.2.11.tar.gz \
        --note "这一版改了什么（会显示在官网「最新动态」）" [--dry-run]

做的事：算 sha256 → 生成 .sha256（LF）→ 在服务器上校验 → 更新 downloads/manifest.json。
上传由外层命令用 scp 做（见 tools/publish_linux.md 或对话记录）。
"""
import argparse
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

SITE_ROOT = Path("/opt/qbot_website")
DOWNLOADS = SITE_ROOT / "downloads"
MANIFEST = DOWNLOADS / "manifest.json"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_of(name: str) -> str:
    m = re.search(r"astroswarm-linux-([0-9][\w.]*)\.tar\.gz$", name)
    if not m:
        raise SystemExit(f"文件名里读不出版本号：{name}")
    return m.group(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pkg = Path(args.package)
    if not pkg.is_file():
        raise SystemExit(f"找不到包：{pkg}")
    version = version_of(pkg.name)
    size = pkg.stat().st_size
    digest = sha256_of(pkg)
    print(f"包：{pkg.name}\n版本：{version}\n大小：{size}\nsha256：{digest}")

    # .sha256 必须 LF（CRLF 会让 sha256sum -c 失败）
    sha_file = pkg.with_suffix(pkg.suffix + ".sha256")
    sha_file.write_bytes(f"{digest}  {pkg.name}\n".encode())
    print(f"已写校验文件：{sha_file.name}")

    if args.dry_run or not MANIFEST.exists():
        print("（dry-run 或不在服务器上，跳过 manifest 更新）")
        return

    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(MANIFEST, DOWNLOADS / f"manifest.json.bak_{stamp}_linux{version.replace('.', '')}")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    today = time.strftime("%Y-%m-%d")
    hit = False
    for product in data.get("products", []):
        if product.get("id") != "astroswarm":
            continue
        for platform in product.get("platforms", []):
            if platform.get("id") != "linux-headless":
                continue
            platform.update({
                "version": version,
                "filename": pkg.name,
                "url": "/downloads/" + pkg.name,
                "size": size,
                "sha256": digest,
                "updated_at": today,
                "desc": "解压后跑 install.sh（内置依赖 wheels，装控制台与机器人都零联网）",
            })
            hit = True
    if not hit:
        raise SystemExit("manifest 里没有 linux-headless 条目")
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if args.note:
        data.setdefault("updates", []).insert(
            0, {"date": today, "product": "星巢 Linux 无头端",
                "text": f"发布 Linux 无头端 v{version}：{args.note}"})
    # json.dumps 没有 newline 参数（本地 3.14 与线上 3.10 都会 TypeError），
    # 想去掉换行翻译得写在 write_text 上 —— 2026-10-10 修。
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="")
    print(f"manifest 已更新（备份后缀 {stamp}）")


if __name__ == "__main__":
    main()
