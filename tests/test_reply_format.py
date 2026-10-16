# -*- coding: utf-8 -*-
"""AI 回复的解析：解析不出 JSON 也必须能把话发出去。

2026-10-01 老板报"群聊 @ 它一个字都不说"，日志里却是"AI 回复 31 字" ——
原因就是这里：提示词要求 `{"reply": [...]}`，模型没照格式回，旧代码直接丢掉整条。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load():
    spec = importlib.util.spec_from_file_location("ai_reply_format", AI_DIR / "reply_format.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_reply_format"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_standard_json_is_split_into_segments():
    rf = _load()
    raw = '{"reply": ["第一段", "第二段", ""]}'
    assert rf.reply_segments(raw) == ["第一段", "第二段"]


def test_fenced_json_is_accepted():
    rf = _load()
    raw = '```json\n{"reply": ["你好呀"]}\n```'
    assert rf.reply_segments(raw) == ["你好呀"]


def test_json_with_explanation_around_it():
    rf = _load()
    raw = '好的，这是回复：\n{"reply": ["在的", "怎么了"]}\n希望有帮助'
    assert rf.reply_segments(raw) == ["在的", "怎么了"]


def test_plain_text_is_sent_instead_of_dropped():
    """这条就是老板踩的坑：模型直接说了句人话，必须照发，不能丢。"""
    rf = _load()
    assert rf.reply_segments("在的，怎么了？") == ["在的，怎么了？"]
    assert rf.reply_segments("  我不太确定，可能吧  ") == ["我不太确定，可能吧"]


def test_code_fenced_plain_text_is_unwrapped():
    rf = _load()
    assert rf.reply_segments("```\n我在呢\n```") == ["我在呢"]


def test_other_shapes_are_tolerated():
    rf = _load()
    rf_mod = rf
    # reply 是字符串而不是列表
    assert rf_mod.reply_segments('{"reply": "就一句"}') == ["就一句"]
    # 顶层就是数组
    assert rf_mod.reply_segments('["一", "二"]') == ["一", "二"]
    # 对象但没有 reply：挑常见的正文字段
    assert rf_mod.reply_segments('{"content": "正文"}') == ["正文"]
    # 空/纯空白：不发
    assert rf.reply_segments("") == []
    assert rf.reply_segments("   ") == []
    # 坏 JSON **但不是回复壳**（纯人话被截断）：照旧当纯文本发出去
    assert rf.reply_segments("我不太确定，可能吧（后面被截断了") \
        == ["我不太确定，可能吧（后面被截断了"]


def test_broken_reply_envelope_never_sends_the_shell():
    """**2026-10-11 回归 ①**：坏 JSON 只要是 `{"reply": ...}` 的壳，就绝不发壳出去。

    改前这一行断言的是 `== ['{"reply": [坏掉的']` —— 也就是把「发壳」当成了正确行为。
    老板在群里看到垃圾输出之后定下的口径是：**解析失败走纯文本兜底，绝不能把 JSON
    外壳发出去**。抠不出正文时返回空列表（调用方改说一句人话）。

    详细的必测矩阵见 `tests/test_reply_shell_guard.py`。
    """
    rf = _load()
    assert rf.reply_segments('{"reply": [坏掉的') == []
    assert rf.reply_segments('{"reply": []}') == []
    assert rf.reply_segments('{"reply": [1, 2]}') == []
    for raw in ('{"reply": [坏掉的', '{"reply": []}', '{"reply": [1, 2]}',
                '{"reply": ["a", "b"'):
        for seg in rf.reply_segments(raw):
            assert "{" not in seg and "}" not in seg and '"reply"' not in seg, \
                "把 JSON 外壳发出去了：%r → %r" % (raw, seg)
    # 半截但抠得出正文 → 把正文抠出来（比"整条丢掉"更好）
    assert rf.reply_segments('{"reply": ["半截但能救"]') == ["半截但能救"]


def test_parse_reply_json_never_raises():
    rf = _load()
    for raw in ("", "   ", "{", "[]", "null", "不是 JSON"):
        rf.parse_reply_json(raw)      # 不抛就算过


if __name__ == "__main__":
    test_standard_json_is_split_into_segments()
    test_fenced_json_is_accepted()
    test_json_with_explanation_around_it()
    test_plain_text_is_sent_instead_of_dropped()
    test_code_fenced_plain_text_is_unwrapped()
    test_other_shapes_are_tolerated()
    test_parse_reply_json_never_raises()
    print("OK reply format")
