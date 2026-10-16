# -*- coding: utf-8 -*-
"""带缓存的 JSON 小文件读取。

为什么需要它：主界面每 2 秒把所有页面 refresh 一遍（`main_window._on_tick`），
而好几个页面的 refresh 会去读机器人目录下的配置/状态文件。以前每次都真读盘，
机械盘或开着杀软时每次读要几毫秒到几十毫秒、还全压在界面线程上 ——
表现就是"看着像卡了一下"（2026-10-01 用 py-spy 抓到主线程正卡在 read_text）。

缓存键是 (修改时间, 大小)：文件被任何一方改过（界面保存、无头端改、手写）
都会立刻重读，不会读到旧值。调用方写文件后调 `invalidate()` 让缓存失效。
"""
from __future__ import annotations

import json
from pathlib import Path

# path -> (mtime_ns, size, data)
_CACHE: dict[str, tuple[int, int, dict]] = {}


def load_json(path) -> dict:
    """读一个 JSON 对象文件；不存在/读不动/不是对象都返回空 dict，不抛异常。

    返回浅拷贝：调用方随便改都不会污染缓存。
    """
    p = Path(path)
    key = str(p)
    try:
        st = p.stat()
    except OSError:
        _CACHE.pop(key, None)
        return {}
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return dict(cached[2])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    data = data if isinstance(data, dict) else {}
    _CACHE[key] = (st.st_mtime_ns, st.st_size, data)
    return dict(data)


def invalidate(path) -> None:
    """自己刚写过这个文件时调一下，别让下一次读命中旧缓存。"""
    _CACHE.pop(str(Path(path)), None)


def clear() -> None:
    """测试/退出时清干净。"""
    _CACHE.clear()
