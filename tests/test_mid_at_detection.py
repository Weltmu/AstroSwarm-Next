# -*- coding: utf-8 -*-
"""消息中间 @ 机器人也要认（2026-10-13：先发文件再 @ 她，她不回）。

nonebot v11 自带的 `_check_at_me` 只认**开头/结尾**的 @；`[文件][@机器人][问题]`
这种形态（先发图/文件，再 @ 机器人问一句）to_me 恒 False → 群里被当"没叫我"。
适配器现在再兜一遍 group_policy.is_at_me 的全位置扫描。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
sys.path.insert(0, str(ROOT / "src"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


GP = _load("gp_mid_at_t", AI / "group_policy.py")


class _Evt:
    def __init__(self, segments, self_id="990000001", to_me=False):
        self.message = segments
        self.self_id = self_id
        self.to_me = to_me
        self.raw_message = ""


def test_at_in_middle_is_detected():
    evt = _Evt([
        {"type": "file", "data": {"file_name": "a.pdf"}},
        {"type": "at", "data": {"qq": "990000001"}},
        {"type": "text", "data": {"text": " 看看这个"}},
    ])
    assert GP.is_at_me(evt, bot_id="990000001") is True


def test_at_to_other_is_not_me():
    evt = _Evt([
        {"type": "text", "data": {"text": "hello "}},
        {"type": "at", "data": {"qq": "1350821813"}},
    ])
    assert GP.is_at_me(evt, bot_id="990000001") is False


def test_qq_adapter_uses_group_policy_is_at_me():
    src = (AI / "adapters" / "qq.py").read_text(encoding="utf-8")
    assert "or is_at_me(event, bot_id=" in src, \
        "适配器没有兜'消息中间 @ 机器人'（nonebot 只认开头/结尾）"
