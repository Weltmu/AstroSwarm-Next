# -*- coding: utf-8 -*-
"""日志自动清理：默认只留最近 10 天（老板 2026-10-03 定的）。

规矩：
- 跨天的"正在写的那份"日志会被改名成 `名字.日期` 归档（正被占用就下次再试，不报错）；
- 归档日志超过保留期一律删；**正在写的那份永远不删**（体积由按大小截断兜底）；
- 天数从设置 `logs_keep_days` 来，默认 10，界面上能改。
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qbotmanager.core import cleanup  # noqa: E402


def _touch(path: Path, days_ago: float, text: str = "x") -> None:
    path.write_text(text, encoding="utf-8")
    ts = time.time() - days_ago * 86400
    import os

    os.utime(path, (ts, ts))


def _noon_ts(day_offset: int = 0) -> float:
    """今天（或某天）**中午 12 点**的时间戳。

    2026-10-15 修 flaky：这两个用例以前用 `time.time() - 0.05 天` 当"今天写的"，
    可**午夜后的一小时里**那其实是"昨天 23 点" —— 归档逻辑按 mtime 的**日期**判，
    于是文件被归档、用例变红（凌晨跑 CI 必踩）。现在把基准挪到当天正午，
    并把 `now` 显式传给被调用方，任何时刻跑结果都一样。
    """
    lt = time.localtime(time.time() + day_offset * 86400)
    noon = time.struct_time((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0,
                             lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    return time.mktime(noon)


def _touch_at(path: Path, ts: float, text: str = "x") -> None:
    import os

    path.write_text(text, encoding="utf-8")
    os.utime(path, (ts, ts))


def test_old_archives_deleted_recent_kept():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_logs_"))
    now = _noon_ts()
    _touch_at(tmp / "nonebot.log.2026-01-01", now - 30 * 86400)   # 很旧 → 该删
    _touch_at(tmp / "manager.log.1", now - 20 * 86400)            # 也旧 → 该删
    _touch_at(tmp / "dsh.log.gz", now - 15 * 86400)               # 压缩归档旧 → 该删
    _touch_at(tmp / "deploy.log.2026-10-02", now - 1 * 86400)     # 昨天 → 留着
    _touch_at(tmp / "nonebot.log", now - 3600)                    # 正在写的 → 永不删

    stats = cleanup.rotate_and_prune_logs(tmp, keep_days=10, now=now)
    left = sorted(p.name for p in tmp.iterdir())
    assert "nonebot.log" in left, left
    assert "deploy.log.2026-10-02" in left, left
    for gone in ("nonebot.log.2026-01-01", "manager.log.1", "dsh.log.gz"):
        assert gone not in left, (gone, left)
    assert sorted(stats["deleted"]) == ["dsh.log.gz", "manager.log.1",
                                        "nonebot.log.2026-01-01"], stats
    assert stats["bytes_freed"] > 0 and stats["keep_days"] == 10, stats


def test_cross_day_live_log_is_rotated_with_date():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_logs_rot_"))
    _touch(tmp / "nonebot.log", 2)                  # 两天前最后写过 → 该归档
    stats = cleanup.rotate_and_prune_logs(tmp, keep_days=10)
    names = sorted(p.name for p in tmp.iterdir())
    assert len(names) == 1 and names[0].startswith("nonebot.log."), names
    assert stats["rotated"] and not stats["deleted"], stats


def test_keep_days_one_still_protects_today():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_logs_one_"))
    now = _noon_ts()
    _touch_at(tmp / "nonebot.log", now - 60)          # 今天写的（正午基准）
    _touch_at(tmp / "liqinghan.log.2026-09-01", now - 40 * 86400)
    cleanup.rotate_and_prune_logs(tmp, keep_days=1, now=now)
    left = sorted(p.name for p in tmp.iterdir())
    assert "nonebot.log" in left and "liqinghan.log.2026-09-01" not in left, left


def test_usage_reports_size_and_files():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_logs_use_"))
    (tmp / "a.log").write_text("x" * 2048, encoding="utf-8")
    (tmp / "b.log").write_text("y" * 1024, encoding="utf-8")
    info = cleanup.logs_usage(tmp)
    assert info["files"] == 2 and info["bytes"] == 3072, info


def test_settings_default_is_ten_days():
    from qbotmanager.core.settings import Settings

    s = Settings(Path(tempfile.mkdtemp(prefix="qbm_logs_set_")))
    assert int(getattr(s, "logs_keep_days", 0)) == 10, "默认必须是 10 天"


if __name__ == "__main__":
    test_old_archives_deleted_recent_kept()
    test_cross_day_live_log_is_rotated_with_date()
    test_keep_days_one_still_protects_today()
    test_usage_reports_size_and_files()
    test_settings_default_is_ten_days()
    print("OK log retention")
