# -*- coding: utf-8 -*-
r"""对账：仓库里的插件文件，和"应用内置 / 机器人目录 / 安装版内置"三处是否一致。

为什么要有它（2026-10-12 老板连踩两次）：
  我改插件时经常**按文件挑着同步**（只 sync brain.py，忘了 reply_format.py），
  结果仓库版 brain.py 调用了一个新函数、机器人那份 reply_format.py 里没有
  → 每次回话都报 `module ... has no attribute 'plan_parts'` → **一个字都发不出去**。
  表现是"表情包发过去没反应"，但根因是同步漏了。

用法：
    python tools/check_plugin_sync.py            # 只查，返回码非 0 表示有漂移
    python tools/check_plugin_sync.py --fix      # 查完顺手全量同步修好
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLED = REPO / "src" / "qbotmanager" / "assets" / "plugins"
INSTALL = Path(r"D:\AstroSwarm 星群")

# 插件名 -> 需要比对的目标目录
TARGETS = {
    "内置插件": [
        ("应用内置", BUNDLED),
        ("机器人目录", INSTALL / "机器人" / "bot" / "src" / "plugins"),
        ("安装版内置", INSTALL / "_internal" / "qbotmanager" / "assets" / "plugins"),
    ],
}


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def check() -> list:
    problems = []
    for label, targets in TARGETS.items():
        for plugin_dir in sorted(p for p in BUNDLED.iterdir() if p.is_dir()):
            for name, base in targets:
                target = base / plugin_dir.name
                if not target.is_dir():
                    problems.append(f"{plugin_dir.name}: 在「{name}」里整个目录缺失")
                    continue
                for src_file in plugin_dir.rglob("*.py"):
                    if "__pycache__" in src_file.parts:
                        continue
                    rel = src_file.relative_to(plugin_dir)
                    tgt_file = target / rel
                    if not tgt_file.is_file():
                        problems.append(f"{plugin_dir.name}/{rel}: 「{name}」缺失")
                    elif _md5(src_file) != _md5(tgt_file):
                        problems.append(f"{plugin_dir.name}/{rel}: 「{name}」内容不一致")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="有漂移就全量同步修好")
    args = ap.parse_args()

    problems = check()
    if not problems:
        print("✓ 插件三处一致，没有漂移")
        return 0

    print(f"✗ 发现 {len(problems)} 处漂移：")
    for p in problems[:40]:
        print("  -", p)
    if len(problems) > 40:
        print(f"  …还有 {len(problems) - 40} 处")

    if args.fix:
        print("\n开始全量同步…")
        tool = REPO / "tools" / "sync_ai_plugin_to_bot.py"
        for plugin_dir in sorted(p for p in BUNDLED.iterdir() if p.is_dir()):
            r = subprocess.run([sys.executable, str(tool), "--plugin", plugin_dir.name],
                               capture_output=True, text=True)
            tail = (r.stdout or r.stderr).strip().splitlines()
            print(f"  {plugin_dir.name}: {tail[-1] if tail else '?'}")
        left = check()
        print("\n同步后：", "✓ 一致了" if not left else f"✗ 还剩 {len(left)} 处")
        return 0 if not left else 1
    print("\n修法：python tools\\check_plugin_sync.py --fix")
    return 1


if __name__ == "__main__":
    sys.exit(main())
