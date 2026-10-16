# -*- coding: utf-8 -*-
r"""备份 / 还原机器人的插件目录（源码版启动/重启机器人时会用它自己的副本覆盖这里）。

用法：
  python tools/backup_bot_plugins.py                 # 备份（自动命名带时间戳）
  python tools/backup_bot_plugins.py --list          # 列出已有备份
  python tools/backup_bot_plugins.py --restore 最新   # 还原（或给备份目录名）
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

BOT_PLUGINS = Path(r"D:\AstroSwarm 星群\机器人\bot\src\plugins")
BACKUP_ROOT = Path(r"D:\ai\QBotManager\backups\bot_plugins")


def do_backup() -> Path:
    assert BOT_PLUGINS.is_dir(), f"找不到机器人插件目录：{BOT_PLUGINS}"
    dst = BACKUP_ROOT / time.strftime("%Y%m%d_%H%M%S")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BOT_PLUGINS, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    n = len([p for p in dst.rglob("*") if p.is_file()])
    print(f"已备份 {n} 个文件 → {dst}")
    return dst


def do_restore(name: str) -> None:
    if name in ("最新", "latest"):
        items = sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()])
        assert items, "还没有任何备份"
        src = items[-1]
    else:
        src = BACKUP_ROOT / name
    assert src.is_dir(), f"找不到备份：{src}"
    # 覆盖式还原：先备份当前，再拷回
    cur = BACKUP_ROOT / ("restore_before_" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copytree(BOT_PLUGINS, cur, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for item in src.iterdir():
        target = BOT_PLUGINS / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    print(f"已从 {src.name} 还原到机器人目录（还原前的状态也存了一份：{cur.name}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="机器人插件目录 备份/还原")
    ap.add_argument("--list", action="store_true", help="列出备份")
    ap.add_argument("--restore", metavar="NAME", help="还原指定备份（或写 最新）")
    args = ap.parse_args()
    if args.list:
        if not BACKUP_ROOT.is_dir():
            print("还没有备份")
            return 0
        for d in sorted(BACKUP_ROOT.iterdir()):
            if d.is_dir():
                n = len([p for p in d.rglob("*") if p.is_file()])
                print(f"  {d.name}  {n} 个文件  {time.strftime('%m-%d %H:%M', time.localtime(d.stat().st_mtime))}")
        return 0
    if args.restore:
        do_restore(args.restore)
        return 0
    do_backup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
