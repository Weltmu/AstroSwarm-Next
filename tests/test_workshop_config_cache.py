# -*- coding: utf-8 -*-
"""工坊配置读取：加缓存之后，界面心跳不再每次读盘。

背景：主界面每 2 秒把每个页面 refresh 一遍（`main_window._on_tick`），
工坊页的 refresh 会读 <机器人根>/workshop/config.json —— py-spy 抓现场时
主线程正卡在这个 read_text 上。慢盘 / 杀软扫描时会一顿一顿。
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbotmanager.core.settings import Settings            # noqa: E402
from qbotmanager.core.workshop import config as ws_config  # noqa: E402


def _settings() -> Settings:
    tmp = Path(tempfile.mkdtemp(prefix="qbm_wscfg_"))
    s = Settings(tmp)
    s.ensure_dirs()
    return s


def test_cache_hit_avoids_disk_read():
    s = _settings()
    assert ws_config.save(s, {"provider": "deepseek", "api_key": "sk-x"})["provider"] == "deepseek"
    ws_config.load(s)                       # 预热缓存
    original = Path.read_text
    box = {"n": 0}

    def counting(self, *a, **k):
        box["n"] += 1
        return original(self, *a, **k)

    Path.read_text = counting
    try:
        for _ in range(50):
            cfg = ws_config.load(s)
            assert cfg["provider"] == "deepseek"
    finally:
        Path.read_text = original
    assert box["n"] == 0, f"命中缓存时不该再读盘，实际读了 {box['n']} 次"


def test_external_change_is_picked_up():
    """外部改文件（手写 / 无头端改）必须能立刻看到，不能被缓存挡住。"""
    s = _settings()
    ws_config.save(s, {"provider": "deepseek", "api_key": "sk-x"})
    assert ws_config.load(s)["provider"] == "deepseek"

    path = ws_config.config_file(s)
    path.write_text(json.dumps(
        {"provider": "moonshot", "api_key": "sk-y", "model": "kimi-k3"}),
        encoding="utf-8")
    assert ws_config.load(s)["provider"] == "moonshot", "改过文件就该重读"

    # 文件被删：回到空配置，而不是继续给旧值
    path.unlink()
    assert ws_config.load(s) == {}


def test_returned_dict_is_a_copy():
    """调用方改返回值不许污染缓存。"""
    s = _settings()
    ws_config.save(s, {"provider": "deepseek", "api_key": "sk-x"})
    first = ws_config.load(s)
    first["provider"] = "被改坏了"
    assert ws_config.load(s)["provider"] == "deepseek"


def test_cache_is_cheaper_than_reading(tmp=None):
    """量一下：200 次读盘 vs 200 次命中缓存。"""
    s = _settings()
    ws_config.save(s, {"provider": "deepseek", "api_key": "sk-x", "model": "deepseek-chat"})
    path = ws_config.config_file(s)

    t0 = time.perf_counter()
    for _ in range(200):
        path.read_text(encoding="utf-8")
    raw = time.perf_counter() - t0

    ws_config.load(s)
    t0 = time.perf_counter()
    for _ in range(200):
        ws_config.load(s)
    cached = time.perf_counter() - t0
    print(f"    200 次真实读盘 {raw * 1000:.1f} ms  vs  200 次命中缓存 {cached * 1000:.1f} ms")
    assert cached < raw, "缓存必须比真读盘快"


if __name__ == "__main__":
    test_cache_hit_avoids_disk_read()
    test_external_change_is_picked_up()
    test_returned_dict_is_a_copy()
    test_cache_is_cheaper_than_reading()
    print("OK workshop config cache")
