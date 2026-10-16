# -*- coding: utf-8 -*-
r"""检查脚本的路径解析（两端通用，2026-10-11 加）。

为什么：`tools/checks/` 里的脚本原来把 Windows 路径写死了
（`D:\AstroSwarm 星群\机器人\bot\src\plugins\liqinghan`、`D:\ai\QBotManager`），
交接文档说"两端可复用"，实际在 Linux 上**一跑就 FileNotFoundError**。

解析顺序（谁在前谁优先）：
  ① `--src` 命令行参数
  ② 环境变量 `QBM_PLUGINS_SRC`（整个 plugins 目录，脚本自己拼插件名）
  ③ Windows 安装目录 `D:\AstroSwarm 星群\机器人\bot\src\plugins\<插件>`
  ④ Linux 无头端 `/opt/astroswarm/data/astroswarm/bot/src/plugins/<插件>`
  ⑤ `/opt/astroswarm/app/qbotmanager/assets/plugins/<插件>`（应用内置副本=真源）
  ⑥ 本仓库相对路径 `<repo>/src/qbotmanager/assets/plugins/<插件>`（开发态）

找不到就抛 FileNotFoundError，并把试过的路径全列出来 —— 别让人猜。
"""
import os
import sys
from pathlib import Path

WIN_PLUGINS = Path(r"D:\AstroSwarm 星群\机器人\bot\src\plugins")
LINUX_BOT_PLUGINS = Path("/opt/astroswarm/data/astroswarm/bot/src/plugins")
LINUX_APP_PLUGINS = Path("/opt/astroswarm/app/qbotmanager/assets/plugins")


def _repo_root() -> Path:
    """仓库根：tools/checks/_src.py → 上两级。"""
    return Path(__file__).resolve().parent.parent.parent


def _cli_src() -> str:
    """支持 `python xxx.py --src <目录>`（参数名与用例脚本自己的一致）。"""
    argv = sys.argv[1:]
    for i, item in enumerate(argv):
        if item == "--src" and i + 1 < len(argv):
            return argv[i + 1]
        if item.startswith("--src="):
            return item.split("=", 1)[1]
    return ""


def plugin_dir(name: str = "liqinghan", what: str = "插件") -> Path:
    """按上面的顺序找到插件目录；找不到就报出试过的所有路径。"""
    tried = []
    explicit = _cli_src()
    if explicit:
        p = Path(explicit)
        # 允许直接给 plugins 目录，也允许给插件目录本身
        candidates = [p / name, p] if p.name != name else [p]
        for c in candidates:
            if c.is_dir():
                return c
            tried.append(c)
        raise FileNotFoundError(
            f"--src 指到的位置里没有 {name}：{explicit}\n试过：\n  "
            + "\n  ".join(str(x) for x in tried))

    env = os.environ.get("QBM_PLUGINS_SRC", "").strip()
    bases = ([Path(env)] if env else []) + [
        WIN_PLUGINS, LINUX_BOT_PLUGINS, LINUX_APP_PLUGINS,
        _repo_root() / "src" / "qbotmanager" / "assets" / "plugins",
    ]
    for base in bases:
        cand = base / name
        if cand.is_dir():
            return cand
        tried.append(cand)
    raise FileNotFoundError(
        f"找不到{what}目录 {name}。可以显式指定：\n"
        f"  python {Path(sys.argv[0]).name} --src <{what}目录>\n"
        f"  或 export QBM_PLUGINS_SRC=<plugins 目录>\n"
        "试过的位置：\n  " + "\n  ".join(str(x) for x in tried))


def repo_root() -> Path:
    """仓库根（同样支持 --repo / QBM_REPO 覆盖）。"""
    argv = sys.argv[1:]
    for i, item in enumerate(argv):
        if item == "--repo" and i + 1 < len(argv):
            return Path(argv[i + 1])
        if item.startswith("--repo="):
            return Path(item.split("=", 1)[1])
    env = os.environ.get("QBM_REPO", "").strip()
    if env:
        return Path(env)
    return _repo_root()
