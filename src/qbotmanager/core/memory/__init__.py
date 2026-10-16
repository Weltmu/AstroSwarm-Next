# -*- coding: utf-8 -*-
"""星群内置记忆模块（程序侧能力，**不是插件**）。

    from qbotmanager.core import memory

    memory.level()                     # "off" | "basic" | "smart"
    memory.remember("123", "他妈妈住院了", kind="event", weight=5)
    memory.forget("123", "他妈妈住院了")
    memory.list_memories("123", 10)
    memory.inject_block("123", session={"last_ts": ..., "visits_today": 1})
    memory.time_line("123", session={...})
    memory.merge_daily()               # 记忆整理（把零散旧事实并成「印象档」）

为什么要内置（而不是留在插件里）：`ai` 与 `liqinghan` 两个插件都要用同一套记忆，
放在程序里就只有一份实现、一份数据；插件侧只做**特性检测后的调用**，
模块不存在（老装机环境）时各自回退到原有逻辑，不会崩。

档位由环境变量 ``ASTROSWARM_MEMORY_LEVEL`` 决定（桌面端/控制台写配置 →
``core.bot.build_bot_env()`` 注入 → 机器人进程读它）。默认 ``basic``，
即**现在线上已有的那套行为**。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 只为类型提示，运行时不导入（机器人 Python 3.10 也安全）
    from datetime import timezone

from . import memory_store, policy
from . import memory_store as store_mod
from .policy import (DEFAULT_LEVEL, ENV_LEVEL, LEVEL_HINTS, LEVEL_LABELS, LEVELS,  # noqa: F401
                     EXTRACT_MAX_FACTS, EXTRACT_MESSAGES, RECALL_LIMIT,
                     classify, default_weight)
from .memory_store import MemoryStore, db_path, legacy_db_path

# 注意：子模块叫 `memory_store`（**不叫 store**），因为 `store()` 是对外 API
# （拿当前库单例）。Python 会把子模块名绑到包命名空间上，同名会把函数盖掉——
# 历史上这类覆盖排查起来很贵，所以这里直接用不会撞的名字。
clean_fact = memory_store.clean_fact

log = logging.getLogger("astroswarm.memory")

__all__ = [
    "level", "set_level", "levels", "level_labels", "level_hints", "enabled", "is_smart",
    "remember", "remember_many", "forget", "forget_all", "list_memories", "recall",
    "recall_text", "inject_block", "prompt_block", "time_line", "merge_daily", "touch",
    "save_diary", "diary_recent", "import_legacy", "migrate_if_needed", "store",
    "memory_store", "clean_fact", "db_path", "legacy_db_path", "reset_store", "stats",
    "level_info", "MemoryStore",
    "EXTRACT_MESSAGES", "EXTRACT_MAX_FACTS", "RECALL_LIMIT",
]

# 迁移只在「第一次以智能档启动」时做一次；做完写 meta 标记，之后不再扫旧库。
MIGRATION_META_KEY = "legacy_import:liqinghan"

_store: Optional[MemoryStore] = None


def store() -> MemoryStore:
    """当前进程的记忆库（单例）。"""
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def reset_store(path: Optional[Path] = None) -> MemoryStore:
    """换一个库文件（测试/迁移用）。"""
    global _store
    if _store is not None:
        _store.close()
    _store = MemoryStore(path)
    return _store


# ---------------------------------------------------------------- 档位

def level() -> str:
    """当前档位：``off`` / ``basic`` / ``smart``（读环境变量，缺省 basic）。"""
    return policy.level()


def set_level(value: str) -> str:
    return policy.set_level(value)


def levels() -> tuple:
    return LEVELS


def level_labels() -> Dict[str, str]:
    return dict(LEVEL_LABELS)


def level_hints() -> Dict[str, str]:
    return dict(LEVEL_HINTS)


def enabled(level_name: Optional[str] = None) -> bool:
    return policy.enabled(level_name)


def is_smart(level_name: Optional[str] = None) -> bool:
    return policy.is_smart(level_name)


def level_info(value: Optional[str] = None) -> Dict[str, Any]:
    """给界面用的一句话说明（桌面端与控制台共用同一份口径）。"""
    lv = value or level()
    return {"level": lv, "label": policy.level_label(lv), "hint": policy.level_hint(lv),
            "options": [{"value": k, "label": LEVEL_LABELS[k], "hint": LEVEL_HINTS[k]}
                        for k in LEVELS]}


# ---------------------------------------------------------------- 写入

def remember(qq: str, fact: str, kind: str = "", weight: int = 0,
             source: str = "plugin", ts: Optional[float] = None) -> int:
    """记一件事。返回新条目 id（0 = 没写：off 档 / 太短 / 重复 / 全被隐私过滤）。

    * ``off`` 档**直接返回 0**，不落任何字节（老板要求：无档位不写不读）；
    * 入库前统一过隐私过滤（手机号 / 证件 / 银行卡 / 详细住址抹掉）；
    * ``kind`` 不传就按内容自动分级；``weight`` 不传就按 kind 给默认权重；
    * ``ts`` 只在导入老库 / 造历史数据时用（默认当前时间）。
    """
    if not enabled():
        return 0
    text = clean_fact(fact)
    if not text or len(text) < 4:
        return 0
    clean, hits = policy.sanitize(text)
    if hits:
        log.info("记忆隐私过滤：命中 %s，已抹掉敏感片段", ",".join(hits))
        clean = clean_fact(clean)
        if len(clean) < 4:
            return 0
    kind = (kind or "").strip() or classify(clean)
    if kind == memory_store.PROMISE_KIND:
        weight = memory_store.PROMISE_WEIGHT
    weight = int(weight or 0) or default_weight(kind)
    try:
        return store().add(qq, clean, kind=kind, weight=weight, source=source, ts=ts)
    except Exception:  # noqa: BLE001 —— 记忆写失败不能拖垮回话
        log.exception("写记忆失败 qq=%s", qq)
        return 0


def remember_many(qq: str, facts: List[str], kind: str = "", source: str = "extract") -> int:
    """一次写多条（事实提炼用）。返回真正写进去的条数。"""
    n = 0
    for fact in list(facts or [])[:EXTRACT_MAX_FACTS]:
        if remember(qq, fact, kind=kind, source=source):
            n += 1
    return n


def touch(qq: str, nickname: str = "") -> None:
    """记一次「他来过」（时间线用）。off 档不写。"""
    if not enabled() or not str(qq or "").strip():
        return
    try:
        store().touch_user(str(qq), nickname)
    except Exception:  # noqa: BLE001
        log.exception("更新用户档案失败 qq=%s", qq)


def save_diary(day: str, content: str) -> None:
    """写日记（off 档不写）。"""
    if not enabled() or not str(content or "").strip():
        return
    try:
        store().save_diary(str(day), str(content))
    except Exception:  # noqa: BLE001
        log.exception("写日记失败 %s", day)


def diary_recent(limit: int = 2) -> List[dict]:
    if not enabled():
        return []
    return store().diary_recent(limit)


# ---------------------------------------------------------------- 读取 / 删除

def list_memories(qq: str, limit: int = 20) -> List[Dict[str, Any]]:
    """「你记得我什么」：列出该用户的记忆（off 档返回空表，不读库）。"""
    if not enabled():
        return []
    return store().list_all(str(qq), limit=limit)


def recall(qq: str, limit: int = RECALL_LIMIT) -> List[Dict[str, Any]]:
    """`recall` 工具翻的那一份：最近 limit 条（与线上「最近 10 条」一致）。"""
    if not enabled():
        return []
    return store().recent(str(qq), limit=limit)


def recall_text(qq: str, limit: int = RECALL_LIMIT) -> str:
    rows = recall(qq, limit)
    if not rows:
        return "（还没有关于你的记忆）"
    return "、".join(f"'{str(r['fact'])[:policy.FACT_CHARS]}'" for r in rows)


def forget(qq: str, fact: str = "") -> List[str]:
    """「忘掉这件事」：**真删**。返回被删掉的内容（空 = 没找到 / off 档）。

    `fact` 为空或写成「全部/所有/一切」时清空该用户的全部记忆。
    """
    if not enabled():
        return []
    text = str(fact or "").strip()
    try:
        if not text or text in ("全部", "所有", "一切", "都忘掉", "全部忘掉"):
            n = store().delete_all(str(qq))
            return [f"（已清空 {n} 条）"] if n else []
        return store().delete_like(str(qq), text)
    except Exception:  # noqa: BLE001
        log.exception("删记忆失败 qq=%s", qq)
        return []


def forget_all(qq: str) -> int:
    if not enabled():
        return 0
    return store().delete_all(str(qq))


# ---------------------------------------------------------------- 注入

def time_line(qq: str, session: Optional[dict] = None, now: Optional[float] = None,
              tz: Optional[timezone] = None, last_topic: str = "") -> str:
    """「距上次说话 3 天 4 小时｜今天第 1 次来｜现在 23:40」。off 档返回空串。"""
    return policy.time_line(qq, session=session, now=now, tz=tz, last_topic=last_topic)


def inject_block(qq: str, session: Optional[dict] = None, now: Optional[float] = None,
                 tz: Optional[timezone] = None, person: str = "",
                 level_name: Optional[str] = None) -> str:
    """要拼进提示词的那一段。档位不同内容不同；``off`` 返回空串。"""
    if not enabled(level_name):
        return ""
    try:
        return policy.injection_block(store(), str(qq), session=session, now=now, tz=tz,
                                      level_name=level_name, person=person)
    except Exception:  # noqa: BLE001
        log.exception("拼注入块失败 qq=%s", qq)
        return ""


def prompt_block(qq: str, session: Optional[dict] = None, now: Optional[float] = None,
                 tz: Optional[timezone] = None, person: str = "") -> str:
    """注入块 + 线上那句前缀（直接塞进 system 消息用）。"""
    return policy.wrap_for_prompt(inject_block(qq, session=session, now=now, tz=tz,
                                               person=person))


def stats(qq: Optional[str] = None) -> Dict[str, Any]:
    """条数统计（桌面端/控制台「现在记住了什么」用）。off 档返回 0。"""
    if not enabled():
        return {"level": level(), "total": 0, "promises": 0, "impressions": 0,
                "expired": 0, "db": str(db_path())}
    s = store()
    rows = s.list_all(qq, 100000, include_expired=True) if qq else None
    if rows is None:
        db = s.connect()
        total = int(db.execute("SELECT COUNT(*) AS n FROM memories WHERE expired=0")
                    .fetchone()["n"])
        promises = int(db.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE expired=0 AND kind='promise'"
        ).fetchone()["n"])
        impressions = int(db.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE expired=0 AND kind='impression'"
        ).fetchone()["n"])
        expired = int(db.execute("SELECT COUNT(*) AS n FROM memories WHERE expired=1")
                      .fetchone()["n"])
        users = int(db.execute("SELECT COUNT(DISTINCT qq) AS n FROM memories").fetchone()["n"])
    else:
        total = sum(1 for r in rows if not r["expired"])
        promises = sum(1 for r in rows if not r["expired"] and r["kind"] == "promise")
        impressions = sum(1 for r in rows if not r["expired"] and r["kind"] == "impression")
        expired = sum(1 for r in rows if r["expired"])
        users = 1 if rows else 0
    return {"level": level(), "total": total, "promises": promises,
            "impressions": impressions, "expired": expired, "users": users,
            "db": str(db_path())}


# ---------------------------------------------------------------- 整理 / 迁移

def merge_daily(now: Optional[float] = None, **kwargs) -> Dict[str, Any]:
    """记忆整理：把零散旧事实合并成「印象档」，过期事件标 expired。

    **只有智能档做**（老板要求：合并属于智能档能力）；off/basic 返回 0 且不写库。
    """
    if not policy.should_merge():
        return {"skipped": True, "reason": f"当前档位 {level()} 不做记忆整理",
                "users": 0, "merged": 0, "expired_events": 0}
    try:
        return store_mod.merge_all(store(), now=now, **kwargs)
    except Exception:  # noqa: BLE001
        log.exception("记忆整理失败")
        return {"skipped": True, "reason": "整理出错（已记日志）",
                "users": 0, "merged": 0, "expired_events": 0}


def import_legacy(legacy: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """把老李清菡记忆库读进新库（**只读旧库**）。任何档位都可手动调用。"""
    res = store_mod.import_legacy(store(), legacy=legacy, now=now)
    if res.get("ok"):
        log.info("老记忆库导入完成：%s → 新增 %d 条（跳过重复 %d 条，涉及 %d 人）",
                 res.get("legacy"), res.get("imported", 0), res.get("skipped", 0),
                 res.get("users", 0))
    elif res.get("reason") != "旧库不存在":
        log.warning("老记忆库导入未完成：%s（%s）", res.get("reason"), res.get("legacy"))
    return res


def migrate_if_needed(now: Optional[float] = None) -> Dict[str, Any]:
    """第一次以「智能」档启动时导入老库；做完写标记，之后是 no-op。

    标记存在**新库**的 meta 表里，所以换库/删库会重新导一次（幂等，不会重复计数）。
    """
    out: Dict[str, Any] = {"ran": False, "reason": ""}
    if not is_smart():
        out["reason"] = f"当前档位 {level()}，不做老库导入（只在智能档首次启动时导）"
        return out
    try:
        if store().meta_get(MIGRATION_META_KEY):
            out["reason"] = "之前已经导过"
            return out
    except Exception:  # noqa: BLE001
        log.exception("读迁移标记失败")
        return out
    legacy = legacy_db_path()
    if not legacy.is_file():
        out["reason"] = f"没有老库（{legacy}）"
        try:
            store().meta_set(MIGRATION_META_KEY, f"skipped:{int(time.time())}")
        except Exception:  # noqa: BLE001
            pass
        return out
    res = import_legacy(legacy=legacy, now=now)
    out.update(res)
    out["ran"] = bool(res.get("ok"))
    try:
        store().meta_set(MIGRATION_META_KEY,
                         f"imported:{res.get('imported', 0)}@{int(time.time())}")
    except Exception:  # noqa: BLE001
        pass
    return out
