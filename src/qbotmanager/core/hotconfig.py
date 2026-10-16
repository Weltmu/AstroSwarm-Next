# -*- coding: utf-8 -*-
"""热读配置（hot config）：**统一**「读配置 + 缓存 + 失效检测 + 坏文件回退」。

老板 2026-10-11 的要求：设置类的东西改完要**立即生效**，不要每次重启机器人。
这个模块就是那件事的基础设施，所有"热读"的地方都走它，别再各写各的。

它做三件事：

1. ``read_json(path)``：读一个 JSON 对象文件，带 **(mtime_ns, size)** 缓存。
   文件没变就一次 ``os.stat``，变了才真读盘 —— 每条消息调一次也是微秒级。
   **只读**、纯进程内缓存（不建临时文件、不加锁），多进程各读各的互不影响。

2. **失败回退到"上次成功的值"**：配置被写到一半 / 手写成非法 JSON 时，
   **绝不**把缓存清成空（那样等于把用户的设置悄悄重置）。
   返回上一次读成功的快照，并按路径**限流**打一条 WARNING（不刷屏）。
   文件恢复成合法 JSON 后下一次调用自动跟上（指纹一变就重读）。

3. ``live_path()`` / ``write_live()``：机器人侧那一份"有效开关快照"的读写。
   程序侧（桌面端 / 无头端）把**算好的**开关写进
   ``<机器人目录>/config/ai/astroswarm_live.json``，机器人进程（AI 插件、
   内置记忆）按需热读它 —— 这样"关掉 QQ 开关"不必重启，插件也不必自己复刻
   一遍程序侧的平台/授权/档案判定逻辑。

路径解析（机器人进程侧，按可靠性排序）：
    显式参数 > ``$ASTROSWARM_LIVE_CONFIG`` > ``$ASTROSWARM_BOT_DIR`` >
    从 ``$ASTROSWARM_MEMORY_DB``（``<bot>/data/ai/xxx.db``）反推 > 当前工作目录。

环境变量只做**兜底**：老装机（没有快照文件）时，调用方各自回退读
``AI_DISABLED`` / ``AI_PLATFORM_*`` / ``ASTROSWARM_MEMORY_LEVEL`` —— 行为不变。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("astroswarm.hotconfig")

# 机器人侧有效开关快照（程序侧写、机器人侧读）
LIVE_NAME = "astroswarm_live.json"
ENV_LIVE = "ASTROSWARM_LIVE_CONFIG"
ENV_BOT_DIR = "ASTROSWARM_BOT_DIR"
ENV_MEMORY_DB = "ASTROSWARM_MEMORY_DB"

# 缓存：path -> (mtime_ns, size, data)
_CACHE: Dict[str, tuple] = {}
# 每条路径上一次"读失败"的时间和原因（限流打日志用）
_BAD: Dict[str, tuple] = {}
_BAD_LOG_INTERVAL = 60.0

# 每条路径的"最少重查间隔"（秒）。0 = 每次都 stat（默认，秒级足够快且立即生效）。
# 这里留一个可调项只是为了极端高频场景能再省一点系统调用。
MIN_STAT_INTERVAL = 0.0
_LAST_STAT: Dict[str, float] = {}


def _fingerprint(path: Path):
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def _warn_once(path: Path, reason: str) -> None:
    """同一个文件 60 秒内最多抱怨一次（配置文件被编辑时会连续触发）。"""
    key = str(path)
    now = time.time()
    last = _BAD.get(key)
    if last is not None and now - last[0] < _BAD_LOG_INTERVAL and last[1] == reason:
        return
    _BAD[key] = (now, reason)
    log.warning("热读配置失败（%s）：%s —— 继续用上一次成功的值，修好即自动恢复",
                path, reason)


def read_json(path, default: Optional[dict] = None, *, min_interval: Optional[float] = None
              ) -> dict:
    """带缓存地读一个 JSON 对象文件；坏文件回退到上次成功的值。

    * 文件不存在：返回 ``default``（默认空 dict），并清掉缓存 —— 这不算"坏文件"；
    * JSON 非法 / 读盘出错：返回**上一次成功读到的值**（没有就 ``default``），
      打一条限流 WARNING，绝不抛异常、绝不返回空；
    * 返回**深拷贝**：调用方随便改（哪怕改嵌套的 list/dict）都不会污染缓存。
    """
    if path is None:
        return copy.deepcopy(dict(default or {}))
    p = Path(path)
    key = str(p)
    interval = MIN_STAT_INTERVAL if min_interval is None else float(min_interval)
    now = time.time()
    if interval > 0:
        last = _LAST_STAT.get(key)
        if last is not None and now - last < interval:
            cached = _CACHE.get(key)
            if cached is not None:
                return copy.deepcopy(cached[2])
    _LAST_STAT[key] = now

    try:
        fp = _fingerprint(p)
    except OSError:
        # 文件没了：缓存作废（下次写回来会重新读）。不算坏文件，不打 warning。
        _CACHE.pop(key, None)
        _BAD.pop(key, None)
        return copy.deepcopy(dict(default or {}))

    cached = _CACHE.get(key)
    if cached is not None and cached[0] == fp[0] and cached[1] == fp[1]:
        return copy.deepcopy(cached[2])

    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("顶层不是 JSON 对象")
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        _warn_once(p, "%s: %s" % (type(exc).__name__, exc))
        if cached is not None:
            return copy.deepcopy(cached[2])          # ← 坏文件：回退上次成功的值
        return copy.deepcopy(dict(default or {}))

    _BAD.pop(key, None)
    _CACHE[key] = (fp[0], fp[1], data)
    return copy.deepcopy(data)


def file_key(path):
    """文件指纹 ``(mtime_ns, size)``；读不到返回 None。给"变了才干活"的场景用。"""
    if path is None:
        return None
    try:
        return _fingerprint(Path(path))
    except OSError:
        return None


def invalidate(path) -> None:
    """自己刚写过这个文件时调一下（同进程内立刻可见，且不依赖 mtime 精度）。"""
    key = str(Path(path))
    _CACHE.pop(key, None)
    _LAST_STAT.pop(key, None)
    _BAD.pop(key, None)


def clear() -> None:
    """测试用：清空全部缓存。"""
    _CACHE.clear()
    _BAD.clear()
    _LAST_STAT.clear()


# ---------------------------------------------------------------- 机器人侧有效开关快照

def live_path(bot_dir=None) -> Path:
    """机器人侧有效开关快照的路径（解析不到也返回一个合理的默认路径）。"""
    explicit = os.environ.get(ENV_LIVE)
    if explicit:
        return Path(explicit)
    if bot_dir:
        return Path(bot_dir) / "config" / "ai" / LIVE_NAME
    env_bot = os.environ.get(ENV_BOT_DIR)
    if env_bot:
        return Path(env_bot) / "config" / "ai" / LIVE_NAME
    mem_db = os.environ.get(ENV_MEMORY_DB)
    if mem_db:
        # <bot>/data/ai/astroswarm_memory.db → <bot>/config/ai/astroswarm_live.json
        return Path(mem_db).resolve().parent.parent.parent / "config" / "ai" / LIVE_NAME
    return Path.cwd() / "config" / "ai" / LIVE_NAME


def read_live(path=None, *, min_interval: Optional[float] = None) -> dict:
    """读机器人侧有效开关快照；没有/坏了都返回空 dict（调用方各自回退环境变量）。

    调用方要区分"文件不存在"（返回空 dict）与"文件在但坏了"（返回上次成功的值）时，
    自己 ``live_path().is_file()`` 判一下 —— 这样测试/构建环境（没有快照）
    的行为与老版本完全一致。
    """
    return read_json(path if path is not None else live_path(), {}, min_interval=min_interval)


def write_live(data: dict, path=None, *, bot_dir=None) -> Optional[Path]:
    """原子写机器人侧有效开关快照（唯一临时名 + ``os.replace``）。

    写坏了最多丢这一次更新，绝不会留下半截 JSON 让机器人读到。
    返回写入路径；失败返回 None（调用方不该因为写不了快照就崩）。
    """
    target = Path(path) if path is not None else live_path(bot_dir)
    payload = dict(data or {})
    payload["written_at"] = int(time.time())
    tmp = target.with_name("%s.%d.%s.tmp" % (target.name, os.getpid(), int(time.time() * 1000)))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        log.warning("写热配置快照失败（%s）：%s", target, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    invalidate(target)
    return target


# ---------------------------------------------------------------- 开关判定（机器人侧）

_TRUE = ("1", "true", "yes", "on", "开", "启用")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in _TRUE


def platform_key(platform: str) -> str:
    return "AI_PLATFORM_" + str(platform or "").strip().upper()


def ai_disabled() -> bool:
    """AI 总开关是不是关着（快照优先，环境变量兜底）。"""
    live = read_live()
    if "ai_disabled" in live:
        return bool(live.get("ai_disabled"))
    return _env_flag("AI_DISABLED", False)


def platform_enabled(platform: str) -> bool:
    """某个平台的 AI 开关（快照优先，环境变量兜底，都没有按开）。"""
    live = read_live()
    value = (live.get("platforms") or {}).get(str(platform or "").strip().lower())
    if value is None:
        return _env_flag(platform_key(platform), True)
    return bool(value)


def state_label(platform: str = "") -> str:
    """给日志用的一句话：开关值 + 来源（"凭什么"关掉了）。"""
    live = read_live()
    src = "热配置" if live else "环境变量"
    if platform:
        return "%s=%s（来源 %s）" % (platform_key(platform),
                                    "1" if platform_enabled(platform) else "0", src)
    return "AI_DISABLED=%d（来源 %s）" % (1 if ai_disabled() else 0, src)


def snapshot() -> Dict[str, Any]:
    """当前快照 + 解析到的路径（诊断/验证用，只读）。"""
    p = live_path()
    data = read_live()
    return {"path": str(p), "exists": p.is_file(), "data": data}
