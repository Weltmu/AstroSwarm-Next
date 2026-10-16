# -*- coding: utf-8 -*-
"""群聊搭话设置（插件里的 interject_*）：界面存进去的值要能读回来，脏值要有兜底。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbotmanager.core import ai_config          # noqa: E402
from qbotmanager.core.settings import Settings  # noqa: E402


def _settings() -> Settings:
    tmp = Path(tempfile.mkdtemp(prefix="qbm_interject_cfg_"))
    s = Settings(tmp)
    s.ensure_dirs()
    return s


def test_defaults_are_off_and_sane():
    """默认关（别在用户没点的情况下自己开口），概率 0.3、间隔 30~90、读 10 条。"""
    s = _settings()
    cfg = ai_config.read_interject(s)
    assert cfg["enabled"] is False
    assert abs(cfg["probability"] - 0.3) < 1e-9
    assert (cfg["interval_min"], cfg["interval_max"]) == (30, 90)
    assert cfg["history_count"] == 10


def test_probability_is_percent_in_ui_ratio_on_disk():
    s = _settings()
    assert ai_config.save_interject(s, {"enabled": True, "probability": 0.45,
                                        "interval_min": 15, "interval_max": 60,
                                        "history_count": 20}) is True
    cfg = ai_config.read_interject(s)
    assert cfg["enabled"] is True
    assert abs(cfg["probability"] - 0.45) < 1e-9
    raw = ai_config.read_manager(s)
    assert raw["interject_enabled"] is True
    assert raw["interject_interval_min"] == 15


def test_reversed_interval_is_swapped():
    """间隔反着填（最小 > 最大）要自动换过来，不然插件永远等不到搭话。"""
    s = _settings()
    ai_config.save_interject(s, {"interval_min": 120, "interval_max": 10})
    cfg = ai_config.read_interject(s)
    assert (cfg["interval_min"], cfg["interval_max"]) == (10, 120), cfg


def test_dirty_values_fall_back():
    """概率越界 / 非数字 / 条数为 0 都得有兜底，不许写进坏配置。"""
    s = _settings()
    ai_config.save_interject(s, {"probability": 9.9, "interval_min": "abc",
                                 "interval_max": 0, "history_count": -3})
    cfg = ai_config.read_interject(s)
    assert cfg["probability"] == 1.0, "超过 1 要夹到 1"
    assert cfg["interval_min"] >= 1 and cfg["interval_max"] >= 1
    assert cfg["history_count"] >= 1
    ai_config.save_interject(s, {"probability": -5})
    assert ai_config.read_interject(s)["probability"] == 0.0


def test_session_defaults_and_roundtrip():
    """免@连续对话：默认 timeout + 30 秒；界面存的值能读回来，秒数夹在 30~300。"""
    s = _settings()
    cfg = ai_config.read_session(s)
    assert cfg["mode"] == "timeout" and cfg["expire_seconds"] == 30, cfg

    assert ai_config.save_session(s, {"mode": "api", "expire_seconds": 120}) is True
    cfg = ai_config.read_session(s)
    assert cfg["mode"] == "api" and cfg["expire_seconds"] == 120

    # 关掉 = mode off（插件里只有 @ 才回）
    assert ai_config.save_session(s, {"mode": "off", "expire_seconds": 30}) is True
    assert ai_config.read_session(s)["mode"] == "off"


def test_session_values_are_clamped():
    s = _settings()
    ai_config.save_session(s, {"mode": "乱填", "expire_seconds": 9999})
    cfg = ai_config.read_session(s)
    assert cfg["mode"] == "timeout", "认不出的模式要回默认 timeout"
    assert cfg["expire_seconds"] == 300, "秒数上限 300"
    ai_config.save_session(s, {"expire_seconds": 1})
    assert ai_config.read_session(s)["expire_seconds"] == 30, "秒数下限 30"


if __name__ == "__main__":
    test_defaults_are_off_and_sane()
    test_probability_is_percent_in_ui_ratio_on_disk()
    test_reversed_interval_is_swapped()
    test_dirty_values_fall_back()
    test_session_defaults_and_roundtrip()
    test_session_values_are_clamped()
    print("OK interject config")
