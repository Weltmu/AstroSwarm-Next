# -*- coding: utf-8 -*-
"""内置记忆模块（qbotmanager.core.memory）的三档行为回归。

跑法（快模式，只跑这一个文件）：
    $env:PYTHONPATH="D:\\ai\\QBotManager\\src"
    .\\.test_venv\\Scripts\\python.exe -m pytest tests/test_builtin_memory.py -q

覆盖老板点名的七条：
  1. 三档行为差异（off 空 / basic 最近3+日记 / smart 最近3+高权重3+承诺+时间线）
  2. **高权重记忆不被挤出**（smart 必带；basic 下可以被挤出 → 证明差异真实存在）
  3. 承诺类在 smart 下必带
  4. 隐私过滤（手机号不落库、不进注入块）
  5. forget 真删
  6. 迁移：造旧结构的小库 → 导入 → 条数对得上、旧库 md5 不变
  7. off 档不产生任何写入
"""
import hashlib
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src")

from qbotmanager.core import memory  # noqa: E402
from qbotmanager.core.memory import policy  # noqa: E402
from qbotmanager.core.memory import memory_store as store_mod  # noqa: E402

QQ = "10001"
HOUR = 3600
DAY = 86400


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    """每个用例一个全新的库；档位默认 basic（= 线上现行为）。"""
    db = tmp_path / "astroswarm_memory.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "basic")
    monkeypatch.setenv("ASTROSWARM_LEGACY_MEMORY_DB", str(tmp_path / "no_such_legacy.db"))
    memory.reset_store(db)
    yield memory
    memory.reset_store(None)


def _rows(db: Path):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM memories ORDER BY id")]
    finally:
        conn.close()


def _write_memory_rows(db: Path, rows):
    """按旧结构（只有 id/qq/fact/ts）直接写库，用来模拟「库里已有的老数据」。"""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS memories("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, qq TEXT, fact TEXT, ts INTEGER)")
        for qq, fact, ts, weight in rows:
            conn.execute("INSERT INTO memories(qq, fact, ts) VALUES(?,?,?)", (qq, fact, ts))
            if weight is not None:
                conn.execute("UPDATE memories SET kind='event', weight=? WHERE id=last_insert_rowid()",
                             (weight,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 1. 三档差异

def test_off_block_is_empty(mem):
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "off"
    assert mem.remember(QQ, "他喜欢喝美式咖啡") == 0
    assert mem.inject_block(QQ) == ""
    assert mem.time_line(QQ) == ""
    assert mem.list_memories(QQ) == []
    assert mem.recall(QQ) == []


def test_basic_injects_recent_three_plus_diary(mem):
    """基础档 = 线上现行为：最近 3 条记忆 + 最近 2 天日记。"""
    for i in range(5):
        mem.remember(QQ, f"第{i}件事值得记下来", ts=time.time() - (5 - i) * HOUR)
    mem.save_diary("2026-10-10", "今天他来找我聊了游戏")
    mem.save_diary("2026-10-11", "今天他心情不太好")
    block = mem.inject_block(QQ)
    assert "第4件事值得记下来" in block and "第2件事值得记下来" in block
    assert "第0件事值得记下来" not in block and "第1件事值得记下来" not in block
    assert block.count("'") == 6 or block.count("件") == 3     # 只有 3 条记忆
    assert "2026-10-11" in block and "2026-10-10" in block
    assert "距上次说话" not in block              # 时间线是智能档才有的
    assert "答应过" not in block                  # 承诺单列也是智能档才有的


def test_smart_injects_recent_important_promise_and_timeline(mem):
    now = time.time()
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"
    mem.remember(QQ, "他妈妈上个月住院了", kind="event", weight=4,
                 ts=now - 20 * DAY)                      # 高权重旧记忆
    mem.remember(QQ, "他答应我周三把书还我", ts=now - 15 * DAY)   # 承诺
    for i in range(3):
        mem.remember(QQ, f"最近说的第{i}件事", ts=now - i * HOUR)
    block = mem.inject_block(QQ, session={"last_ts": int(now - 3 * DAY - 4 * HOUR)},
                             now=now)
    assert "他妈妈上个月住院了" in block            # 高权重 3 条
    assert "他答应我周三把书还我" in block          # 承诺必带
    assert "最近说的第2件事" in block              # 最近 3 条
    assert "距上次说话 3 天 4 小时" in block        # 时间线
    assert "现在 " in block
    assert mem.time_line(QQ, session={"last_ts": int(now - 3 * DAY - 4 * HOUR)},
                         now=now).startswith("距上次说话 3 天 4 小时")


# ---------------------------------------------------------------- 2. 高权重不被挤出

def _seed_basic_scene(db: Path, now: float):
    """1 条 weight=5 的旧记忆 + 5 条新记忆（老板给的那个场景）。"""
    qq = QQ
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS memories("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, qq TEXT, fact TEXT, ts INTEGER, "
                     "kind TEXT DEFAULT 'event', weight INTEGER DEFAULT 3, "
                     "last_used INTEGER DEFAULT 0, expired INTEGER DEFAULT 0, source TEXT DEFAULT '')")
        conn.execute("INSERT INTO memories(qq, fact, ts, kind, weight) VALUES(?,?,?,?,?)",
                     (qq, "他妈妈住院了需要人陪", int(now - 30 * DAY), "event", 5))
        for i in range(5):
            conn.execute("INSERT INTO memories(qq, fact, ts, kind, weight) VALUES(?,?,?,?,?)",
                         (qq, f"新新鲜事第{i}条记下来", int(now - i * HOUR), "event", 3))
        conn.commit()
    finally:
        conn.close()


def test_high_weight_not_evicted_in_smart_but_may_be_in_basic(mem, tmp_path):
    now = time.time()
    db = Path(os.environ["ASTROSWARM_MEMORY_DB"])
    mem.store().connect()          # 先建表，再往同一个库里塞老数据
    _seed_basic_scene(db, now)

    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "basic"
    basic_block = mem.inject_block(QQ, now=now)
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"
    smart_block = mem.inject_block(QQ, now=now)

    # smart 必须出现高权重那条；basic 下被 5 条新的挤出（差异是真的）
    assert "他妈妈住院了需要人陪" in smart_block, smart_block
    assert "他妈妈住院了需要人陪" not in basic_block, basic_block
    # 两档都带最近 3 条（最新的那条是「第4条」：种子按 i 递增、时间倒着走）
    assert "新新鲜事第4条记下来" in basic_block and "新新鲜事第4条记下来" in smart_block
    assert "新新鲜事第4条记下来" in smart_block


# ---------------------------------------------------------------- 3. 承诺必带

def test_promise_always_present_in_smart(mem):
    now = time.time()
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"
    mem.remember(QQ, "他答应下周一帮我带早饭", ts=now - 40 * DAY)
    for i in range(6):
        mem.remember(QQ, f"后来又说的事第{i}件", ts=now - i * HOUR)
    block = mem.inject_block(QQ, now=now)
    assert "他答应下周一帮我带早饭" in block
    assert "绝不能忘" in block
    rows = [r for r in _rows(Path(os.environ["ASTROSWARM_MEMORY_DB"]))
            if "带早饭" in r["fact"]]
    assert rows and rows[0]["kind"] == "promise" and rows[0]["weight"] == 5


# ---------------------------------------------------------------- 4. 隐私过滤

def test_privacy_filter_keeps_number_out_of_db_and_block(mem):
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"
    mem.remember(QQ, "他的手机号是13812345678，有事打这个")
    rows = _rows(Path(os.environ["ASTROSWARM_MEMORY_DB"]))
    joined = " ".join(r["fact"] for r in rows)
    assert "13812345678" not in joined
    assert "[已隐去手机号]" in joined
    block = mem.inject_block(QQ)
    assert "13812345678" not in block
    # 身份证 / 银行卡 / 详细住址同样不落库
    mem.remember(QQ, "身份证 11010519900307761X 也发过去了")
    mem.remember(QQ, "银行卡 6222021234567890123 记一下")
    mem.remember(QQ, "他家在北京市海淀区中关村路 27 号 3 单元")
    joined = " ".join(r["fact"] for r in _rows(Path(os.environ["ASTROSWARM_MEMORY_DB"])))
    for secret in ("11010519900307761X", "6222021234567890123", "中关村路 27 号"):
        assert secret not in joined, joined
    hits = policy.sanitize("电话13900001111 身份证11010519900307761X 卡6222021234567890123")
    assert set(hits[1]) >= {"phone", "idcard", "bankcard"}


# ---------------------------------------------------------------- 5. forget 真删

def test_forget_really_deletes(mem):
    mem.remember(QQ, "他喜欢喝美式咖啡")
    mem.remember(QQ, "他讨厌香菜")
    assert len(mem.list_memories(QQ)) == 2
    removed = mem.forget(QQ, "他讨厌香菜")
    assert removed == ["他讨厌香菜"]
    left = [r["fact"] for r in mem.list_memories(QQ)]
    assert left == ["他喜欢喝美式咖啡"]
    assert all("香菜" not in r["fact"] for r in _rows(Path(os.environ["ASTROSWARM_MEMORY_DB"])))
    # 清空全部
    assert mem.forget(QQ) and mem.list_memories(QQ) == []


# ---------------------------------------------------------------- 6. 迁移

def test_legacy_import_counts_and_keeps_old_db_untouched(tmp_path, monkeypatch):
    legacy = tmp_path / "agent.db"
    conn = sqlite3.connect(str(legacy))
    try:
        conn.executescript(
            "CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT, qq TEXT, "
            "fact TEXT, ts INTEGER);"
            "CREATE TABLE diary(day TEXT PRIMARY KEY, content TEXT, ts INTEGER);"
            "CREATE TABLE users(qq TEXT PRIMARY KEY, nickname TEXT, first_seen INTEGER, "
            "last_seen INTEGER, msg_count INTEGER, relationship INTEGER, notes TEXT);")
        for i in range(7):
            conn.execute("INSERT INTO memories(qq, fact, ts) VALUES(?,?,?)",
                         (QQ, f"老库里的第{i}条事实", int(time.time()) - i * DAY))
        conn.execute("INSERT INTO diary(day, content, ts) VALUES('2026-10-01','老日记',1)")
        conn.commit()
    finally:
        conn.close()
    before = hashlib.md5(legacy.read_bytes()).hexdigest()

    db = tmp_path / "new.db"
    monkeypatch.setenv("ASTROSWARM_MEMORY_DB", str(db))
    monkeypatch.setenv("ASTROSWARM_MEMORY_LEVEL", "smart")
    monkeypatch.setenv("ASTROSWARM_LEGACY_MEMORY_DB", str(legacy))
    memory.reset_store(db)

    res = memory.import_legacy()
    assert res["ok"] is True, res
    assert res["imported"] == 7
    assert res["md5_before"] == res["md5_after"] == before
    assert hashlib.md5(legacy.read_bytes()).hexdigest() == before, "旧库被改了！"
    assert memory.store().count() == 7
    # 幂等：再导一次不会翻倍
    again = memory.import_legacy()
    assert again["imported"] == 0 and again["skipped"] == 7
    assert memory.store().count() == 7
    # migrate_if_needed：第一次智能档启动导，第二次不再导
    memory.reset_store(db)
    memory.store().connect()
    memory.store().meta_set("legacy_import:liqinghan", "")      # 清标记模拟首次
    first = memory.migrate_if_needed()
    assert first["ran"] is True and first["imported"] == 0      # 幂等：都已在库里
    assert memory.migrate_if_needed()["reason"] == "之前已经导过"
    memory.reset_store(None)


def test_migrate_skipped_in_basic(mem):
    """基础档不触发老库导入（只有智能档首次启动导）。"""
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "basic"
    res = memory.migrate_if_needed()
    assert res["ran"] is False and "basic" in res["reason"]


# ---------------------------------------------------------------- 7. off 零写入

def test_off_writes_nothing(mem, tmp_path):
    db = Path(os.environ["ASTROSWARM_MEMORY_DB"])
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "off"
    mem.remember(QQ, "这条不该被写进去")
    mem.remember(QQ, "这条也不该", kind="promise", weight=5)
    mem.save_diary("2026-10-11", "今天的日记不该写")
    mem.touch(QQ, "某人")
    res = mem.merge_daily()
    assert res.get("skipped") is True
    assert not db.exists(), "off 档居然创建了库文件"
    assert mem.list_memories(QQ) == [] and mem.diary_recent() == []

    # 库里本来有数据时：off 档跑一轮，行数一条不变
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"
    mem.remember(QQ, "先写一条正常的")
    n_before = len(_rows(db))
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "off"
    mem.remember(QQ, "off 期间这条不该进")
    mem.touch(QQ, "x")
    mem.save_diary("2026-10-12", "也不该写")
    n_after = len(_rows(db))
    assert n_before == n_after == 1
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"


# ---------------------------------------------------------------- 附加：合并 / 档位说明

def test_merge_daily_makes_impression_only_in_smart(mem):
    now = time.time()
    for i in range(8):
        mem.remember(QQ, f"零散旧事实第{i}条讲的是同一件事", ts=now - (10 + i) * DAY)
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "basic"
    assert mem.merge_daily(now=now).get("skipped") is True
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "smart"
    res = mem.merge_daily(now=now)
    assert res["merged"] >= 5, res
    rows = _rows(Path(os.environ["ASTROSWARM_MEMORY_DB"]))
    impressions = [r for r in rows if r["kind"] == "impression"]
    assert impressions and impressions[0]["fact"].startswith("印象：")
    # 被并进印象档的原条目标了 expired，但记录还在（不删）
    assert any(r["expired"] for r in rows)
    assert len(rows) == 9


def test_level_hints_cover_three_levels(mem):
    info = mem.level_info()
    assert [o["value"] for o in info["options"]] == ["off", "basic", "smart"]
    assert all(o["hint"] for o in info["options"])
    assert info["options"][1]["hint"].startswith("基础")
    # 环境变量缺省 = basic
    os.environ.pop("ASTROSWARM_MEMORY_LEVEL", None)
    assert mem.level() == "basic"
    os.environ["ASTROSWARM_MEMORY_LEVEL"] = "乱填的"
    assert mem.level() == "basic"


def test_policy_helpers():
    assert store_mod.clean_fact("- 他喜欢咖啡") == "他喜欢咖啡"
    assert store_mod.clean_fact("1. 他喜欢咖啡") == "他喜欢咖啡"
    assert policy.classify("他答应我明天还书") == "promise"
    assert policy.classify("他妈妈住在老家") == "relation"
    assert policy.classify("他喜欢吃辣") == "like"
    assert policy.classify("印象：A；B") == "impression"
    assert policy.human_gap(3 * DAY + 4 * HOUR) == "3 天 4 小时"
    assert policy.human_gap(90) == "1 分钟"


def test_bot_root_and_db_path_agree_across_process_roles(tmp_path, monkeypatch):
    """机器人进程与控制台进程必须算出**同一个**库路径。

    线上真踩过：机器人侧 `.../data/astroswarm/bot/data/ai/x.db`、
    控制台侧 `.../data/astroswarm/data/ai/x.db`，控制台「现在记住了什么」永远 0 条。
    这里把两种启动方式都造出来，断言结果一致。
    """
    data_home = tmp_path / "data" / "astroswarm"          # = ASTROSWARM_DATA_HOME
    bot = data_home / "bot"                               # 无头端：机器人根在下一级
    (bot / "src" / "plugins").mkdir(parents=True)
    (bot / "run.py").write_text("# nonebot\n", encoding="utf-8")
    (bot / "pyproject.toml").write_text("[tool.nonebot]\n", encoding="utf-8")

    monkeypatch.delenv("ASTROSWARM_MEMORY_DB", raising=False)
    monkeypatch.delenv("ASTROSWARM_BOT_DIR", raising=False)
    monkeypatch.delenv("QBM_AGENT_DATA_DIR", raising=False)
    monkeypatch.setenv("ASTROSWARM_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    monkeypatch.chdir(bot)                                # 机器人进程：cwd = 机器人根
    assert store_mod.bot_root() == bot
    bot_side = store_mod.db_path()

    monkeypatch.chdir(data_home.parent)                   # 控制台进程：cwd = /opt/astroswarm
    assert store_mod.bot_root() == bot, store_mod.bot_root()
    console_side = store_mod.db_path()

    assert bot_side == console_side, (bot_side, console_side)
    assert bot_side == bot / "data" / "ai" / store_mod.DB_NAME

    # 桌面端布局：<root>/bot 是机器人根，同样要认出来
    # （桌面端没有 ASTROSWARM_DATA_HOME，所以这里先把它摘掉）
    monkeypatch.delenv("ASTROSWARM_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    desktop = tmp_path / "install"
    (desktop / "bot" / "src" / "plugins").mkdir(parents=True)
    (desktop / "bot" / "run.py").write_text("", encoding="utf-8")
    (desktop / "bot" / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(desktop)
    assert store_mod.bot_root() == desktop / "bot"
    assert store_mod.db_path() == desktop / "bot" / "data" / "ai" / store_mod.DB_NAME

    # 显式给了 bot 目录就以它为准（机器人进程注入的就是它）
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASTROSWARM_BOT_DIR", str(bot))
    assert store_mod.db_path() == bot / "data" / "ai" / store_mod.DB_NAME
