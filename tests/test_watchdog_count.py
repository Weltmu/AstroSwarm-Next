# -*- coding: utf-8 -*-
"""看门狗防疯狂重启计数（2026-10-12）：只算机器人动作，客户端启动不能占预算。

实测背景：客户端反复启停攒了 11 条"→ 启动它"，把机器人重启预算（4/小时）耗光，
机器人死后看门狗一直拒绝拉起（"已动作 11 次 → 本次不重启"）。
"""
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_watchdog():
    spec = importlib.util.spec_from_file_location("watchdog_t",
                                                  ROOT / "tools" / "watchdog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _line(ago_sec: float, msg: str) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - ago_sec))
    return f"{stamp} {msg}"


def _write_log(tmp_path: Path, lines) -> None:
    log = tmp_path / "logs" / "watchdog.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_client_starts_do_not_consume_restart_budget(tmp_path):
    wd = _load_watchdog()
    lines = [_line(60 * (i + 1), "客户端未运行 → 启动它") for i in range(11)]
    lines.append(_line(300, "机器人未运行 → 拉起"))
    lines.append(_line(295, "拉起机器人：退出码=0 ok"))
    _write_log(tmp_path, lines)
    assert wd.recent_restart_count(tmp_path) == 1


def test_old_and_malformed_lines_ignored(tmp_path):
    wd = _load_watchdog()
    lines = [
        _line(4000, "机器人未运行 → 拉起"),             # 超一小时：不算
        _line(4000, "拉起机器人：退出码=0 old"),         # 超一小时：不算
        "这行没有时间戳 拉起机器人：退出码=0",             # 时间戳坏：不算
        _line(120, "拉起机器人失败：TimeoutExpired x"),   # 失败也算一次动作
    ]
    _write_log(tmp_path, lines)
    assert wd.recent_restart_count(tmp_path) == 1


def test_no_log_file_counts_zero(tmp_path):
    wd = _load_watchdog()
    assert wd.recent_restart_count(tmp_path / "nonexistent") == 0
