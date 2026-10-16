# -*- coding: utf-8 -*-
"""读能力包（tool_packs）里的参数。

生态里的约定（和 proactive 包一样）：
- `<机器人根>/tool_packs/<包 id>/behavior.json` = 包自带的默认值；
- 同目录 `config.json` = 用户在「已装能力包 → 配置」里改过的值，**优先级更高**；
- 参数可能被包成 `{"session": {...}}` 一层，也可能直接是 `{...}`，两种都认。

纯函数 + 一点点文件读取，不依赖 nonebot，测试可以直接按路径加载。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PACKS_ENV = "ASTROSWARM_TOOL_PACKS"


def packs_root(cwd=None) -> Path | None:
    """能力包根目录：优先环境变量，其次 bot 目录的上一级（bot 的 cwd 就是 bot 目录）。"""
    env = str(os.environ.get(PACKS_ENV) or "").strip()
    if env:
        return Path(env)
    try:
        base = Path(cwd) if cwd is not None else Path.cwd()
    except OSError:
        return None
    return base.parent / "tool_packs"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _unwrap(data: dict, section: str) -> dict:
    """参数可能被包在 {"section": {...}} 里；只在恰好一层包装时摊平。"""
    if section and isinstance(data.get(section), dict):
        return data[section]
    if len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, dict):
            return only
    return data


def read_pack_params(root, pack_id: str, section: str = "") -> dict:
    """读某个能力包的参数（behavior.json → config.json 覆盖）。读不到就返回空 dict。"""
    if not root:
        return {}
    pack_dir = Path(root) / str(pack_id)
    if not pack_dir.is_dir():
        return {}
    out: dict = {}
    for name in ("behavior.json", "config.json"):        # 后者优先级更高
        data = _read_json(pack_dir / name)
        if data:
            out.update(_unwrap(data, section))
    return out
