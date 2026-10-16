# -*- coding: utf-8 -*-
"""内置记忆的 SQLite 读写层（程序侧，两个插件共用）。

设计要点（与老板 2026-10-11 拍板的方案一致）：

* **位置**：数据文件不在源码目录里，按现有那套走「机器人根目录」——
  与 AI 插件记忆（`bot/data/ai/aichat_memory.json`）放在同一个 `data/ai/` 下，
  名字 `astroswarm_memory.db`。可用环境变量 `ASTROSWARM_MEMORY_DB` 覆盖。
* **表结构**带 `kind / weight / last_used / expired`：分级、权重、权重注入、
  合并（`kind='impression'`）与过期标记全靠这四个字段。
* **旧库能读**：`users / memories / diary` 这套老表本来就在同一个 SQLite 文件里
  （liqinghan 插件写的就是 `agent.db`）。这里对旧表**只加列、不做破坏性变更**；
  老库里的 `memories(qq, fact, ts)` 即使没有新列也能直接查。
* 记忆整理（合并成「印象档」）只做**标记**不做删除：被合并的原始条目置
  `expired=1`，检索与注入都不再带它，但记录还在（可回滚、可审计）。
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

DB_NAME = "astroswarm_memory.db"
LEGACY_DB_NAME = "agent.db"

# 记忆种类：身份 / 关系 / 喜好 / 事件 / 承诺 / 印象档。
# 前四步方案里的「状态情绪、群记忆、主动性依据」这一轮不做，但字段先留好：
#   kind 允许 'mood' / 'group'（下一轮直接用），现在不会被自动写入。
KINDS = ("identity", "relation", "like", "dislike", "event", "promise", "impression",
         "mood", "group")
DEFAULT_KIND = "event"
PROMISE_KIND = "promise"
IMPRESSION_KIND = "impression"

# 承诺类永远按最高权重看待（「答应的事绝不能忘」）。
PROMISE_WEIGHT = 5
WEIGHT_MIN, WEIGHT_MAX = 1, 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  qq TEXT NOT NULL,
  fact TEXT NOT NULL,
  ts INTEGER NOT NULL,
  kind TEXT DEFAULT 'event',
  weight INTEGER DEFAULT 3,
  last_used INTEGER DEFAULT 0,
  expired INTEGER DEFAULT 0,
  source TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memories_qq ON memories(qq, expired, id DESC);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(qq, kind, expired);
CREATE TABLE IF NOT EXISTS diary(
  day TEXT PRIMARY KEY,
  content TEXT DEFAULT '',
  ts INTEGER
);
CREATE TABLE IF NOT EXISTS users(
  qq TEXT PRIMARY KEY,
  nickname TEXT DEFAULT '',
  first_seen INTEGER,
  last_seen INTEGER,
  msg_count INTEGER DEFAULT 0,
  relationship INTEGER DEFAULT 0,
  notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


# ---------------------------------------------------------------- 路径解析

def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _is_bot_root(path: Path) -> bool:
    """这个目录像不像「机器人根目录」（NoneBot 项目根）。

    先认**写得最全**的形状（run.py + src/plugins，部署脚本生成的就是这样），
    再退回「只有 run.py」。两级判定是为了不让安装目录本身被误认成机器人根
    （安装目录下也有一个 run.py 名字的文件时会出错）。
    """
    try:
        full = (path / "run.py").is_file() and (path / "src" / "plugins").is_dir()
        if full:
            return True
        return (path / "run.py").is_file() and (path / "pyproject.toml").is_file()
    except OSError:
        return False


def bot_root() -> Optional[Path]:
    """猜机器人根目录（记忆库放在它的 data/ai/ 下，与 AI 插件的记忆文件同级）。

    两个进程要指到**同一个文件**，所以这里必须两条线索都认：
      * 机器人进程：`ASTROSWARM_BOT_DIR` / `QBM_AGENT_DATA_DIR`，或当前工作目录；
      * 控制台进程：`ASTROSWARM_DATA_HOME`（= <data>/astroswarm）与运行根目录
        之间的相对关系 —— 无头端里机器人根**恰好在下面一级的 bot/**，
        而桌面端是 `<root>/bot`。
    线上真踩过一次：机器人侧解析到 `.../astroswarm/bot/data/ai/`，
    而控制台侧解析到 `.../astroswarm/data/ai/`，于是控制台「现在记住了什么」
    永远显示 0 条。**两边必须算出同一个路径**，这一条有专门的回归测试。
    """
    explicit = _first_env("ASTROSWARM_BOT_DIR", "QBM_AGENT_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    cwd = Path.cwd()
    if _is_bot_root(cwd):
        return cwd
    for base in [Path(v).expanduser() for v in
                 (_first_env("ASTROSWARM_DATA_HOME"), _first_env("XDG_DATA_HOME")) if v]:
        for candidate in (base / "bot", base.parent / "bot", base):
            if _is_bot_root(candidate):
                return candidate
    # 数据目录环境变量都没有（桌面端就是这种）：从当前目录往下找一层 bot/
    for candidate in (cwd / "bot", cwd.parent / "bot"):
        if _is_bot_root(candidate):
            return candidate
    return None


def db_path() -> Path:
    """记忆库路径：显式环境变量 > 机器人根的 data/ai/ > 数据目录下的 data/ai/。"""
    explicit = _first_env("ASTROSWARM_MEMORY_DB")
    if explicit:
        return Path(explicit).expanduser()
    root = bot_root()
    if root is not None:
        return root / "data" / "ai" / DB_NAME
    base = _first_env("ASTROSWARM_DATA_HOME", "XDG_DATA_HOME")
    if base:
        return Path(base).expanduser() / "data" / "ai" / DB_NAME
    return Path.home() / ".local" / "share" / "astroswarm" / "data" / "ai" / DB_NAME


def legacy_db_path() -> Path:
    """老李清菡记忆库（表 memories/diary/users）的位置，只读。"""
    explicit = _first_env("ASTROSWARM_LEGACY_MEMORY_DB")
    if explicit:
        return Path(explicit).expanduser()
    base = _first_env("QBM_AGENT_DATA_DIR")
    if base:
        return Path(base).expanduser() / LEGACY_DB_NAME
    candidates = []
    bot = _first_env("ASTROSWARM_BOT_DIR")
    if bot:
        candidates.append(Path(bot) / "src" / "plugins" / "liqinghan" / "data" / LEGACY_DB_NAME)
    cwd = Path.cwd()
    candidates.append(cwd / "src" / "plugins" / "liqinghan" / "data" / LEGACY_DB_NAME)
    candidates.append(db_path().parent / LEGACY_DB_NAME)
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


# ---------------------------------------------------------------- 小工具

def _now_ts(now: Optional[float] = None) -> int:
    return int(now if now is not None else time.time())


def _rows(cur) -> List[sqlite3.Row]:
    return list(cur.fetchall())


class MemoryStore:
    """一个进程一份连接（NoneBot 单进程足够；连接按需懒建）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else db_path()
        self._db: Optional[sqlite3.Connection] = None

    # ---- 连接与建表 ----

    def connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            self._migrate_legacy_columns()
            self._db.commit()
        return self._db

    def _migrate_legacy_columns(self) -> None:
        """老库里 memories 表只有 (id, qq, fact, ts)：缺列就补，绝不删列。"""
        db = self._db
        if db is None:
            return
        have = {r["name"] for r in db.execute("PRAGMA table_info(memories)")}
        for name, ddl in (
            ("kind", "ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT 'event'"),
            ("weight", "ALTER TABLE memories ADD COLUMN weight INTEGER DEFAULT 3"),
            ("last_used", "ALTER TABLE memories ADD COLUMN last_used INTEGER DEFAULT 0"),
            ("expired", "ALTER TABLE memories ADD COLUMN expired INTEGER DEFAULT 0"),
            ("source", "ALTER TABLE memories ADD COLUMN source TEXT DEFAULT ''"),
        ):
            if name not in have:
                db.execute(ddl)

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            finally:
                self._db = None

    def exists(self) -> bool:
        return self.path.is_file()

    # ---- 写入 ----

    @staticmethod
    def is_duplicate(existing: str, candidate: str) -> bool:
        """两条事实算不算重复。

        规则与线上一致（`a in b or b in a`）**再加两道闸**，因为程序自己合成的
        条目（印象档、日记式长句）会比单条事实长得多：
          * 长度差太多就不算重复 —— 否则「印象：A；B；C」这种聚合体只要包含
            某条旧事实就会被判成重复，结果一条都存不进去（真踩过）；
          * 短的那条少于 6 个字不参与判断 —— 「喜欢猫」这种太泛，不构成重复。
        """
        a = (existing or "").strip()
        b = (candidate or "").strip()
        if not a or not b:
            return False
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if short not in long:
            return False
        if len(short) < 6:
            return False
        # 短的那条至少要占长的那条八成，才认为「说的是同一件事」
        return len(short) * 10 >= len(long) * 8

    def add(self, qq: str, fact: str, kind: str = DEFAULT_KIND, weight: int = 3,
            ts: Optional[float] = None, source: str = "") -> int:
        """写入一条记忆。**去重**：与已有条目基本同义就跳过（返回已有 id）。

        返回 0 表示没写（空内容 / 太短）；返回已有 id 表示命中重复、没新增。
        """
        fact = (fact or "").strip().strip("。.!！~～")
        if not fact or len(fact) < 4:
            return 0
        fact = fact[:200]
        kind = kind if kind in KINDS else DEFAULT_KIND
        if kind == PROMISE_KIND:
            weight = PROMISE_WEIGHT
        weight = max(WEIGHT_MIN, min(WEIGHT_MAX, int(weight or 3)))
        db = self.connect()
        # 2026-10-15（审计 extra-1）：去重只跟**没过期的**条目比。
        # 以前把 `expired=1` 的旧行也算进去 —— 整理/过期打过标的旧事实被再次提起时，
        # add 命中那行、直接返回旧 id、**不插新行**，于是这条永远以 expired=1 躺着，
        # recent()/注入永远看不到它；调用方却把非零返回值当"写成功"。
        # 用户重新说起同一件事（"下周六去看电影"），实际上说了白说。
        for row in db.execute(
                "SELECT id, fact FROM memories WHERE qq=? AND expired=0", (str(qq),)):
            if self.is_duplicate(row["fact"], fact):
                return int(row["id"])
        # 命中了已过期的同义条 → **复活它**（比插一条新的好：保留原来的 ts/权重脉络）
        for row in db.execute(
                "SELECT id, fact FROM memories WHERE qq=? AND expired=1", (str(qq),)):
            if self.is_duplicate(row["fact"], fact):
                db.execute(
                    "UPDATE memories SET expired=0, ts=?, weight=?, last_used=0,"
                    " kind=?, source=? WHERE id=?",
                    (_now_ts(ts), weight, kind, source, int(row["id"])))
                db.commit()
                return int(row["id"])
        cur = db.execute(
            "INSERT INTO memories(qq, fact, ts, kind, weight, last_used, expired, source) "
            "VALUES(?,?,?,?,?,0,0,?)",
            (str(qq), fact, _now_ts(ts), kind, weight, source),
        )
        db.commit()
        return int(cur.lastrowid or 0)

    def mark_used(self, ids: Sequence[int], now: Optional[float] = None) -> None:
        ids = [int(i) for i in ids if i]
        if not ids:
            return
        db = self.connect()
        marks = ",".join("?" for _ in ids)
        db.execute(f"UPDATE memories SET last_used=? WHERE id IN ({marks})",
                   (_now_ts(now), *ids))
        db.commit()

    def expire(self, ids: Sequence[int]) -> int:
        ids = [int(i) for i in ids if i]
        if not ids:
            return 0
        db = self.connect()
        marks = ",".join("?" for _ in ids)
        cur = db.execute(f"UPDATE memories SET expired=1 WHERE id IN ({marks})", tuple(ids))
        db.commit()
        return int(cur.rowcount or 0)

    # ---- 读取 ----

    def recent(self, qq: str, limit: int = 3, kinds: Optional[Iterable[str]] = None) -> List[dict]:
        db = self.connect()
        params: List[Any] = [str(qq)]
        sql = "SELECT * FROM memories WHERE qq=? AND expired=0"
        kinds = list(kinds or [])
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" for _ in kinds)
            params.extend(kinds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in db.execute(sql, tuple(params))]

    def important(self, qq: str, limit: int = 3, kinds: Optional[Iterable[str]] = None) -> List[dict]:
        """高权重优先；同权重按新→旧。

        只排除**承诺类**（它由 `promises()` 单独必带，免得同一条占两个名额）。
        不按 weight 卡阈值：老板要的是「重要的老记忆不能被新的挤出去」，
        任何一条权重高于普通事件的记忆都该有机会进来。
        """
        db = self.connect()
        params: List[Any] = [str(qq), PROMISE_KIND]
        sql = "SELECT * FROM memories WHERE qq=? AND expired=0 AND kind<>?"
        kinds = list(kinds or [])
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" for _ in kinds)
            params.extend(kinds)
        sql += " ORDER BY weight DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in db.execute(sql, tuple(params))]

    def promises(self, qq: str, limit: int = 10) -> List[dict]:
        db = self.connect()
        return [dict(r) for r in db.execute(
            "SELECT * FROM memories WHERE qq=? AND expired=0 AND kind=? "
            "ORDER BY weight DESC, id DESC LIMIT ?",
            (str(qq), PROMISE_KIND, max(1, int(limit))))]

    def list_all(self, qq: str, limit: int = 50, include_expired: bool = False) -> List[dict]:
        db = self.connect()
        sql = "SELECT * FROM memories WHERE qq=?"
        if not include_expired:
            sql += " AND expired=0"
        sql += " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in db.execute(sql, (str(qq), max(1, int(limit))))]

    def count(self, qq: Optional[str] = None) -> int:
        db = self.connect()
        if qq is None:
            row = db.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
        else:
            row = db.execute("SELECT COUNT(*) AS n FROM memories WHERE qq=?", (str(qq),)).fetchone()
        return int(row["n"] if row else 0)

    def facts_since(self, qq: str, since_ts: float, exclude_kinds: Iterable[str] = (),
                    limit: int = 50) -> List[dict]:
        db = self.connect()
        params: List[Any] = [str(qq), int(since_ts)]
        sql = "SELECT * FROM memories WHERE qq=? AND expired=0 AND ts>=?"
        ex = list(exclude_kinds)
        if ex:
            sql += " AND kind NOT IN (%s)" % ",".join("?" for _ in ex)
            params.extend(ex)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in db.execute(sql, tuple(params))]

    def facts_before(self, qq: str, before_ts: float, exclude_kinds: Iterable[str] = (),
                     limit: int = 200) -> List[dict]:
        db = self.connect()
        params: List[Any] = [str(qq), int(before_ts)]
        sql = "SELECT * FROM memories WHERE qq=? AND expired=0 AND ts<?"
        ex = list(exclude_kinds)
        if ex:
            sql += " AND kind NOT IN (%s)" % ",".join("?" for _ in ex)
            params.extend(ex)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        return [dict(r) for r in db.execute(sql, tuple(params))]

    def user_ids(self) -> List[str]:
        db = self.connect()
        return [str(r["qq"]) for r in db.execute(
            "SELECT DISTINCT qq FROM memories WHERE expired=0 AND qq<>''")]

    def delete_like(self, qq: str, fact: str) -> List[str]:
        """真删：先精确匹配，没有再退到「互相包含」。返回被删掉的内容。"""
        db = self.connect()
        fact = (fact or "").strip()
        rows = [dict(r) for r in db.execute("SELECT * FROM memories WHERE qq=?", (str(qq),))]
        hit = [r for r in rows if r["fact"].strip() == fact]
        if not hit and fact:
            hit = [r for r in rows if fact in r["fact"] or r["fact"] in fact]
        if not hit:
            return []
        ids = [int(r["id"]) for r in hit]
        marks = ",".join("?" for _ in ids)
        db.execute(f"DELETE FROM memories WHERE id IN ({marks})", tuple(ids))
        db.commit()
        return [str(r["fact"]) for r in hit]

    def delete_all(self, qq: str) -> int:
        db = self.connect()
        cur = db.execute("DELETE FROM memories WHERE qq=?", (str(qq),))
        db.commit()
        return int(cur.rowcount or 0)

    # ---- 日记（与老库同名同结构，插件侧代码不用改）----

    def save_diary(self, day: str, content: str, ts: Optional[float] = None) -> None:
        db = self.connect()
        db.execute(
            "INSERT INTO diary(day, content, ts) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET content=excluded.content, ts=excluded.ts",
            (str(day), content or "", _now_ts(ts)),
        )
        db.commit()

    def diary_recent(self, limit: int = 2) -> List[dict]:
        db = self.connect()
        return [dict(r) for r in db.execute(
            "SELECT day, content, ts FROM diary ORDER BY day DESC LIMIT ?", (max(1, int(limit)),))]

    def diary_get(self, day: str) -> str:
        db = self.connect()
        row = db.execute("SELECT content FROM diary WHERE day=?", (str(day),)).fetchone()
        return str(row["content"]) if row else ""

    # ---- 用户档案（时间线用：认识多久 / 今天第几次）----

    def touch_user(self, qq: str, nickname: str = "", now: Optional[float] = None) -> None:
        """记一次「来过」：只在**用户消息**上调用（注入策略自己判断）。"""
        ts = _now_ts(now)
        db = self.connect()
        db.execute(
            "INSERT INTO users(qq, nickname, first_seen, last_seen, msg_count) VALUES(?,?,?,?,1) "
            "ON CONFLICT(qq) DO UPDATE SET last_seen=excluded.last_seen, msg_count=msg_count+1, "
            "nickname=CASE WHEN ?<>'' THEN ? ELSE nickname END",
            (str(qq), nickname or "", ts, ts, nickname or "", nickname or ""),
        )
        db.commit()

    def get_user(self, qq: str) -> Optional[dict]:
        db = self.connect()
        row = db.execute("SELECT * FROM users WHERE qq=?", (str(qq),)).fetchone()
        return dict(row) if row else None

    def last_seen(self, qq: str) -> int:
        row = self.get_user(qq)
        return int((row or {}).get("last_seen") or 0)

    # ---- meta（迁移标记等）----

    def meta_get(self, key: str, default: str = "") -> str:
        db = self.connect()
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def meta_set(self, key: str, value: str) -> None:
        db = self.connect()
        db.execute("INSERT INTO meta(key, value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(key), str(value)))
        db.commit()


# ---------------------------------------------------------------- 旧库迁移

def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_legacy(store: MemoryStore, legacy: Optional[Path] = None,
                  now: Optional[float] = None) -> Dict[str, Any]:
    """把老李清菡记忆库里的 `memories` 读进新库：**只读旧库，一个字都不改**。

    返回 {ok, imported, skipped, reason, legacy, md5_before, md5_after, users}。
    幂等：同一条 fact 已在库里就不会重复写（`add()` 自带去重）。
    """
    legacy = Path(legacy) if legacy else legacy_db_path()
    out: Dict[str, Any] = {"ok": False, "imported": 0, "skipped": 0, "legacy": str(legacy),
                           "reason": "", "users": 0,
                           "md5_before": "", "md5_after": ""}
    if not legacy.is_file():
        out["reason"] = "旧库不存在"
        return out
    out["md5_before"] = file_md5(legacy)
    try:
        # 只读打开：uri mode=ro，物理上不可能写旧库
        conn = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - 环境问题
        out["reason"] = f"打不开旧库：{exc}"
        return out
    try:
        conn.row_factory = sqlite3.Row
        have = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
        if not have:
            out["reason"] = "旧库没有 memories 表"
            return out
        cols = ["qq", "fact", "ts"]
        has_kind = "kind" in have
        has_weight = "weight" in have
        select = "SELECT qq, fact, ts" + (", kind" if has_kind else "") + \
                 (", weight" if has_weight else "") + " FROM memories ORDER BY id ASC"
        users = set()
        for row in conn.execute(select):
            qq = str(row["qq"] or "").strip()
            fact = str(row["fact"] or "").strip()
            if not qq or not fact:
                continue
            users.add(qq)
            kind = str(row["kind"] or DEFAULT_KIND) if has_kind else DEFAULT_KIND
            weight = int(row["weight"] or 3) if has_weight else 3
            # add() 命中重复时返回的是**已有条目的 id**（不是 0），所以这里
            # 用总条数判断到底写没写进去：否则重复导入会被算成「又导入了一遍」。
            before_count = store.count()
            store.add(qq, fact, kind=kind, weight=weight,
                      ts=float(row["ts"] or 0) or None, source="legacy:liqinghan")
            if store.count() > before_count:
                out["imported"] += 1
            else:
                out["skipped"] += 1
        out["users"] = len(users)
        out["ok"] = True
    finally:
        conn.close()
        out["md5_after"] = file_md5(legacy)
    if out["md5_before"] != out["md5_after"]:  # pragma: no cover - 防御
        out["ok"] = False
        out["reason"] = "旧库 md5 变了（只读打开不该发生），已停止迁移"
    return out


# ---------------------------------------------------------------- 合并（印象档）

def merge_for_user(store: MemoryStore, qq: str, now: Optional[float] = None,
                   older_than_days: int = 3, min_items: int = 5,
                   event_expire_days: int = 30, max_chars: int = 400,
                   impression_weight: int = 4) -> Dict[str, Any]:
    """把某人零散的旧事实合并成一段「印象档」（细节模糊、印象清晰）。

    * 只处理 `ts` 早于 `older_than_days` 的非承诺、非印象条目；
    * 少于 `min_items` 条就**不动**（别把两句话硬压成一段）；
    * 超过 `event_expire_days` 的 `event` 直接标 `expired`（过期事件）；
    * 被合并进印象档的原始条目也标 `expired`（注入时换成印象档，记录仍在）。
    """
    now_ts = _now_ts(now)
    cutoff = now_ts - max(1, int(older_than_days)) * 86400
    old = store.facts_before(qq, cutoff, exclude_kinds=(PROMISE_KIND, IMPRESSION_KIND))
    if not old:
        return {"qq": str(qq), "merged": 0, "expired_events": 0, "impression_id": 0}
    # 过期事件先标掉：一次性的事过去就过去了，不该再单独占注入名额。
    # 但它们**照样进印象档**——老板要的是「细节模糊、印象清晰」，
    # 把旧事件整批丢掉就成了「半年后什么都不记得」，那是另一个极端。
    event_cutoff = now_ts - max(1, int(event_expire_days)) * 86400
    stale_events = [r for r in old if r.get("kind") == DEFAULT_KIND and int(r["ts"]) < event_cutoff]
    expired = store.expire([int(r["id"]) for r in stale_events])
    if len(old) < max(1, int(min_items)):
        return {"qq": str(qq), "merged": 0, "expired_events": expired, "impression_id": 0}

    parts: List[str] = []
    used = 0
    for row in sorted(old, key=lambda r: (-int(r.get("weight") or 3), -int(r["id"]))):
        text = str(row["fact"]).strip().rstrip("。")
        candidate = "；".join(parts + [text])
        if len(candidate) > max_chars:
            break
        parts.append(text)
        used += 1
    if used == 0:
        return {"qq": str(qq), "merged": 0, "expired_events": expired, "impression_id": 0}
    impression = "印象：" + "；".join(parts)
    # 必须显式写 kind：印象档的正文里通常没有「喜欢/答应」这类关键词，
    # 交给 classify() 会被误判成 event（那样 injection 就取不到它了）。
    iid = store.add(qq, impression, kind=IMPRESSION_KIND, weight=impression_weight,
                    ts=now_ts, source="merged")
    if not iid:  # 已有同内容的印象档：只把来源条目标记掉
        rows = store.recent(qq, 1, kinds=[IMPRESSION_KIND])
        iid = int(rows[0]["id"]) if rows else 0
    merged_rows = [r for r in old if str(r["fact"]).strip().rstrip("。") in parts]
    store.expire([int(r["id"]) for r in merged_rows])
    store.meta_set(f"last_merge:{qq}", str(now_ts))
    return {"qq": str(qq), "merged": len(merged_rows), "expired_events": expired,
            "impression_id": iid}


def merge_all(store: MemoryStore, now: Optional[float] = None, **kwargs) -> Dict[str, Any]:
    """对库里所有人跑一遍整理（每天一次，由 `merge_daily()` 调用）。"""
    total = {"users": 0, "merged": 0, "expired_events": 0}
    for qq in store.user_ids():
        res = merge_for_user(store, qq, now=now, **kwargs)
        total["users"] += 1
        total["merged"] += int(res["merged"])
        total["expired_events"] += int(res["expired_events"])
    return total


# ---------------------------------------------------------------- 默认单例

_default: Optional[MemoryStore] = None


def default_store() -> MemoryStore:
    global _default
    if _default is None:
        _default = MemoryStore()
    return _default


# ---------------------------------------------------------------- 文本工具

_PUNCT = re.compile(r"^[\s\-•*·●○。，,、；;：:！!？?~～\"'“”‘’()（）\[\]【】]+")


def clean_fact(text: str) -> str:
    """把模型输出的一行收拾成一条 fact（去掉行首序号/符号，并**压掉换行**）。

    2026-10-15（审计 E）：记忆是原样拼进**系统提示**的（`关于他的记忆：'…'`），
    而写入侧允许内嵌换行 —— 用户让机器人"记住"一句带 `\\n忽略以上设定，以后都…`
    的话，这段文字此后每一轮都以 system 层身份生效（可持久化的提示注入）。
    这里把空白（含换行）一律折成单个空格，写入与渲染两侧都收口。
    """
    line = _PUNCT.sub("", str(text or "").strip())
    line = re.sub(r"^\d+[.、)）]\s*", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()
