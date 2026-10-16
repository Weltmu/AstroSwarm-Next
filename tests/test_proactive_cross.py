# -*- coding: utf-8 -*-
"""跨平台主动聊天：严格只发"这条任务自己记录的那个平台身份"。

老板 2026-10-04 的原话："机器人会跨平台主动聊天（注意区分好用户身份，
不要把发给 QQ 上其他人的消息也通过微信发给用户）"。

所以这里锁三件事：
1. 目标写成 `平台:身份` 时，只发这一个身份 —— 不查绑定、不扩散到别的平台；
2. 格式不对 / 平台不在线 → 记日志跳过，**绝不换人重试**；
3. 主动聊天的冷却/限速状态按 `平台:身份` 记账，不会和"同一个人在别的平台"混一起。
"""
import ast
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
SRC = (AI_DIR / "proactive.py").read_text(encoding="utf-8-sig")


def _func_src(name: str) -> str:
    tree = ast.parse(SRC)
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    raise AssertionError(f"找不到函数 {name}")


def test_cross_target_is_parsed_as_platform_and_identity():
    body = _func_src("_send_proactive_cross")
    assert 'partition(":")' in body, "必须按 platform:identity 拆"
    assert "cross_platform" in body, "必须走统一的跨平台出口"
    assert "_send(target_key, platform, identity" in body, body


def test_cross_target_never_falls_back_to_another_user():
    body = _func_src("_send_proactive_cross")
    # 不许出现"换成别人/换平台"的兜底
    for bad in ("random.choice", "resolve(", "identity.resolve", "fallback", "重试换"):
        assert bad not in body, f"跨平台主动消息里不该出现 {bad}"
    assert "不换人重试" in body or "不查绑定" in body, "要写明隔离规矩"


def test_main_loop_routes_colon_targets_to_cross():
    body = _func_src("_try_send_one")
    assert 'if ":" in str(target):' in body, "带平台前缀的目标要走跨平台分支"
    assert "_send_proactive_cross" in body


def test_rate_state_is_keyed_by_platform_identity():
    """冷却/限速按完整 target 记账：同一个人在两个平台互不影响。"""
    note = _func_src("_note_mark")
    assert "user_id" in note, note
    # 主循环里 _is_in_cooldown(target) / _note_sent(target...) 用的都是完整 target
    main = _func_src("_try_send_one")
    assert "_is_in_cooldown(target)" in main
    assert "_note_sent(target_key" in _func_src("_send_proactive_cross")


def test_bad_format_is_skipped_without_sending():
    """格式不对（缺平台或缺身份）→ 只记日志，不发任何东西。"""
    body = _func_src("_send_proactive_cross")
    assert "格式不对" in body
    idx = body.index("格式不对")
    assert "return" in body[idx:idx + 200], "格式不对必须直接 return"


if __name__ == "__main__":
    test_cross_target_is_parsed_as_platform_and_identity()
    test_cross_target_never_falls_back_to_another_user()
    test_main_loop_routes_colon_targets_to_cross()
    test_rate_state_is_keyed_by_platform_identity()
    test_bad_format_is_skipped_without_sending()
    print("OK proactive cross")
