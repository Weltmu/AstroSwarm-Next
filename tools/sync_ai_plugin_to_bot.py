# -*- coding: utf-8 -*-
r"""把一个 AI 插件文件同步到"应用内置副本 + 机器人目录"（避免手工漏一处）。

为什么要这个工具（2026-10-10 踩的坑）：
  应用每次启动/重启机器人，会把它**自己 _internal 里的 assets/plugins/ai**
  同步进 `机器人\bot\src\plugins\ai`。所以只改源码、或只改机器人目录，都会被覆盖/不生效：
    · 只改机器人目录 → 下次重启被应用的内置副本覆盖掉；
    · 只改源码（仓库）→ 已装应用的内置副本还是旧的，同步过去还是旧代码。
  三处必须同时是新的：
    ① src/qbotmanager/assets/plugins/<插件>/          （打包用）
    ② <安装目录>\_internal\qbotmanager\assets\plugins\<插件>\  （已装应用同步用）
    ③ <安装目录>\机器人\bot\src\plugins\<插件>\        （机器人实际加载的）

  Linux 无头端（2026-10-11 新增，路径不一样，不要照抄 Windows 的）：
    ① 源码仓库同理（打包用）
    ② /opt/astroswarm/app/qbotmanager/assets/plugins/<插件>/   （已装应用内置副本；
       无头端启动部署时用它覆盖机器人目录，所以**它必须一起更新**）
    ③ /opt/astroswarm/data/astroswarm/bot/src/plugins/<插件>/  （机器人实际加载的）

用法：
    python tools/sync_ai_plugin_to_bot.py                 # 同步整个插件目录（Windows 默认路径）
    python tools/sync_ai_plugin_to_bot.py group_policy.py # 只同步某几个文件
    python tools/sync_ai_plugin_to_bot.py --check         # 只比对，不改
    python tools/sync_ai_plugin_to_bot.py --plugin liqinghan
    # Linux 无头端：显式给出两个目标目录（用哪台机器就跑在哪台机器上）
    python3 sync_plugin_linux.py --plugin ai \
        --app-dir /opt/astroswarm/app/qbotmanager/assets/plugins \
        --bot-dir /opt/astroswarm/data/astroswarm/bot/src/plugins [--check]

输出里 sha256 与 md5 都给（服务器上对账习惯用 md5；`--check` 退出码 0=一致）。
"""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PLUGIN = "ai"
DEFAULT_INSTALL = Path(r"D:\AstroSwarm 星群")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else "缺"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "缺"


def source_files(src: Path, files: list) -> list:
    """要同步的相对路径：命令行给了就用它，否则整个目录的 *.py。

    顺手跳过 `.bak*`（历史上 `.bak_p6_20260911` 这类备份文件也在 plugins 里，
    当年确实是靠 `rglob('*.py')` 一起同步的 —— 这里保持原行为，
    只有显式给了文件名时才精确同步）。
    """
    if files:
        return [Path(f) for f in files]
    return [p.relative_to(src) for p in sorted(src.rglob("*.py"))
            if "__pycache__" not in p.parts]


def targets(install: Path, plugin: str) -> list:
    """Windows 安装目录下的两个目标（原有语义，一字未改）。"""
    return [
        (install / "_internal" / "qbotmanager" / "assets" / "plugins" / plugin,
         "应用内置"),
        (install / "机器人" / "bot" / "src" / "plugins" / plugin, "机器人目录"),
    ]


def run(src: Path, pairs: list, rels: list, check: bool, quiet=False) -> int:
    """按 (目标根, 标签) 逐个文件同步/比对。返回不一致条数。"""
    print(f"源目录: {src}")
    missing_roots = [label for root, label in pairs if not root.is_dir()]
    if missing_roots:
        print(f"    [!] 目标目录还不存在（会创建）：{', '.join(missing_roots)}")
    bad, copied = [], 0
    for rel in rels:
        s = src / rel
        if not s.is_file():
            print(f"[x] 源码里没有 {rel}")
            bad.append(str(rel))
            continue
        row = [f"  {rel.as_posix():<34} 源码={sha(s)} md5={md5(s)}"]
        for root, label in pairs:
            dst = root / rel
            if not check:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, dst)
                copied += 1
                pyc = dst.parent / "__pycache__" / (dst.stem + ".cpython-312.pyc")
                if pyc.exists():
                    pyc.unlink()
            row.append(f"{label}={sha(dst)}")
        same = len({sha(s), *[sha(root / rel) for root, _ in pairs]}) == 1
        if not same or not quiet:
            print(" ".join(row) + ("" if same else "  [不一致]"))
        if not same:
            bad.append(str(rel))
    action = "比对" if check else f"写入 {copied} 次"
    print(f"\n（{'--check：没有写任何文件' if check else action}）")
    # 用 ASCII 结果词：Windows 控制台是 GBK，打 ✓/✗ 会 UnicodeEncodeError（实测）
    print("结论:", "各目标一致 OK" if not bad else f"仍有不一致：{bad}")
    return len(bad)


def main() -> int:
    ap = argparse.ArgumentParser(description="把 AI 插件同步到已装应用与机器人目录")
    ap.add_argument("files", nargs="*", help="相对插件目录的路径；不给=整个目录")
    ap.add_argument("--install", default=str(DEFAULT_INSTALL),
                    help="Windows 安装目录（默认 D:\\AstroSwarm 星群）")
    ap.add_argument("--app-dir", default="",
                    help="已装应用的 plugins 目录（Linux: /opt/astroswarm/app/qbotmanager/"
                         "assets/plugins）")
    ap.add_argument("--bot-dir", default="",
                    help="机器人 plugins 目录（Linux: /opt/astroswarm/data/astroswarm/"
                         "bot/src/plugins）")
    ap.add_argument("--check", action="store_true", help="只比对不写")
    ap.add_argument("--quiet", action="store_true", help="只打印不一致的行")
    ap.add_argument("--plugin", default=DEFAULT_PLUGIN,
                    help="插件目录名（默认 ai，另有 liqinghan）")
    args = ap.parse_args()

    src = REPO / "src" / "qbotmanager" / "assets" / "plugins" / args.plugin
    if not src.is_dir():
        print(f"[x] 源码插件目录不存在：{src}")
        return 2

    if args.app_dir or args.bot_dir:
        # Linux / 显式路径模式：两个目录都必须显式给出（免得只同步一半还以为成功）
        if not (args.app_dir and args.bot_dir):
            print("[x] --app-dir 与 --bot-dir 必须一起给（避免只同步一处）")
            return 2
        pairs = [(Path(args.app_dir) / args.plugin, "应用内置"),
                 (Path(args.bot_dir) / args.plugin, "机器人目录")]
    else:
        install = Path(args.install)
        if not (install / "机器人").is_dir():
            print(f"[x] 看起来不是安装目录：{install}"
                  f"（Linux 请改用 --app-dir/--bot-dir）")
            return 2
        pairs = targets(install, args.plugin)

    rels = source_files(src, args.files)
    bad = run(src, pairs, rels, check=args.check, quiet=args.quiet)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
