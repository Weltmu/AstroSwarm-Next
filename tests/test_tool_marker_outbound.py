# -*- coding: utf-8 -*-
"""工具调用标记双向护栏的单测。

对应老板 2026-10-11 的两条要求：
* 出站：模型把工具调用标记写进正文时**一律不发出**，流式**每个分片都要过**；
* 入站：**整条**都是标记的消息不当人话喂模型；混着人话的照常处理。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"

CALL = '<tool_call>{"name": "get_weather", "arguments": {"city": "大连"}}</tool_call>'


def _load():
    spec = importlib.util.spec_from_file_location("ai_tool_marker", AI_DIR / "tool_marker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_tool_marker"] = mod
    spec.loader.exec_module(mod)
    return mod


def _feed_all(tm, pieces):
    """按给定分片喂进 StreamCleaner，返回 (拼起来的结果, cleaner)。"""
    c = tm.StreamCleaner()
    out = "".join(c.feed(p) for p in pieces) + c.flush()
    return out, c


# ===== 出站：完整标记 =====

def test_complete_marker_alone_is_fully_stripped():
    tm = _load()
    clean, removed = tm.clean_outbound(CALL)
    assert removed is True
    assert clean == ""


def test_marker_inside_prose_keeps_the_prose():
    tm = _load()
    clean, removed = tm.clean_outbound("好的，我查一下" + CALL + "稍等")
    assert removed is True
    assert clean == "好的，我查一下稍等"


def test_prose_without_marker_is_untouched():
    tm = _load()
    clean, removed = tm.clean_outbound("在的，怎么了？")
    assert removed is False
    assert clean == "在的，怎么了？"


def test_half_marker_without_closer_is_stripped():
    """半截标记：只有开标签、没有闭合，后面全是参数 —— 一律不发。"""
    tm = _load()
    clean, removed = tm.clean_outbound('你好<tool_call>{"name": "get_weather"')
    assert removed is True
    assert clean == "你好"


def test_pure_marker_variants_all_become_empty():
    tm = _load()
    for raw in (
        CALL,
        "<tool_calls>[]</tool_calls>",
        '<function_call>{"name": "x"}</function_call>',
        '<tool_call>{"name": "x"',
        "<|tool_call|>",
        "<|tool_call|>{}<|/tool_call|>",
        "\u273fFUNCTION\u273fget_weather\u273fARGS\u273f{}",
        "[TOOL_CALLS] [{\"name\": \"x\"}]",
        "to=functions.get_weather {\"city\": \"大连\"}",
    ):
        clean, removed = tm.clean_outbound(raw)
        assert removed is True, raw
        assert clean == "", (raw, clean)


def test_fallback_text_is_human_readable():
    """剥完为空时替它说的那句人话必须存在、且不是空串。"""
    tm = _load()
    assert isinstance(tm.FALLBACK_TEXT, str)
    assert tm.FALLBACK_TEXT.strip()
    # 兜底话本身不能再带标记，否则就是死循环
    assert tm.clean_outbound(tm.FALLBACK_TEXT) == (tm.FALLBACK_TEXT, False)


def test_special_tokens_are_stripped():
    tm = _load()
    clean, removed = tm.clean_outbound("<|im_start|>assistant<|im_end|>我在呢<|endoftext|>")
    assert removed is True
    assert clean == "assistant我在呢"


# ===== 出站：流式分片（标记被切成两半）=====

def test_marker_split_across_two_fragments_is_stripped():
    """**核心用例**：开标签本身被两个分片切成 `<tool_` + `call>...`。"""
    tm = _load()
    pieces = ["<tool_", 'call>{"name": "get_weather"}</tool_call>']
    out, c = _feed_all(tm, pieces)
    assert out == "", out
    assert c.removed is True


def test_marker_payload_split_across_fragments_is_stripped():
    """参数体被切成两半，闭合标记在第二片。"""
    tm = _load()
    pieces = ['<tool_call>{"na', 'me": "x"}</tool_call>']
    out, _ = _feed_all(tm, pieces)
    assert out == "", out


def test_prose_around_split_marker_survives():
    tm = _load()
    pieces = ["好的", "<tool_", 'call>{"name": "x"}</tool_call>', "搞定了"]
    out, _ = _feed_all(tm, pieces)
    assert out == "好的搞定了", out


def test_special_token_split_across_fragments_is_stripped():
    tm = _load()
    pieces = ["<|tool", "_call|>", "我在呢"]
    out, _ = _feed_all(tm, pieces)
    assert out == "我在呢", out


def test_stream_never_leaks_partial_marker():
    """逐字符喂（最极端的分片）也不能漏出任何一段标记。"""
    tm = _load()
    raw = "好的" + CALL + "稍等"
    out, _ = _feed_all(tm, list(raw))       # 一次一个字符
    assert out == "好的稍等", out


def test_stream_matches_one_shot_on_samples():
    """流式结果必须和一次性剥的结果一致（分片不该改变语义）。"""
    tm = _load()
    samples = [
        CALL,
        "在的" + CALL + "怎么了",
        '你好<tool_call>{"name": "x"',
        "<|tool_call|>{}",
        "普通人话，没有标记",
        "\u273fFUNCTION\u273fweather\u273fARGS\u273f{}",
    ]
    for raw in samples:
        one_shot, _ = tm.clean_outbound(raw)
        streamed, _ = _feed_all(tm, list(raw))
        assert streamed.strip() == one_shot, (raw, streamed, one_shot)


# ===== 出站：兜底决定 =====

def test_special_token_block_removes_json_payload():
    """<|tool_call|>{...json...}<|/tool_call|> 必须**整块**删。

    只删两端 token 的话，中间那段 JSON 参数会原样发到群里 —— 这是最难看的一种漏。
    """
    tm = _load()
    raw = '<|tool_call|>{"name": "get_weather", "arguments": {"city": "大连"}}<|/tool_call|>'
    clean, removed = tm.clean_outbound(raw)
    assert removed is True
    assert clean == ""
    assert "大连" not in clean


def test_outbound_text_falls_back_when_nothing_left():
    tm = _load()
    text, fallback, removed = tm.outbound_text(CALL)
    assert fallback is True
    assert removed is True
    assert text == tm.FALLBACK_TEXT


def test_outbound_text_keeps_normal_reply():
    tm = _load()
    assert tm.outbound_text("在的，怎么了？") == ("在的，怎么了？", False, False)


def test_outbound_text_keeps_emoji_only_reply():
    """纯表情是合法回复，**不能**被当成"没话可说"而换成兜底话。"""
    tm = _load()
    text, fallback, _ = tm.outbound_text("😂")
    assert fallback is False
    assert text == "😂"


def test_structural_shell_counts_as_nothing():
    tm = _load()
    assert tm.is_nothing_to_send("") is True
    assert tm.is_nothing_to_send("   ") is True
    assert tm.is_nothing_to_send("{}") is True
    assert tm.is_nothing_to_send("[]") is True
    assert tm.is_nothing_to_send("在的") is False
    assert tm.is_nothing_to_send("😂") is False


# ===== 入站识别 =====

def test_all_marker_message_is_detected():
    """群里另一个机器人整条漏标记 —— 不能当人话喂模型。"""
    tm = _load()
    assert tm.is_all_markers(CALL) is True
    assert tm.is_all_markers("  " + CALL + "  ") is True
    assert tm.is_all_markers("<|tool_call|>{}<|/tool_call|>") is True


def test_message_mixed_with_prose_is_not_all_marker():
    """混着人话的照常处理（返回 False）。"""
    tm = _load()
    assert tm.is_all_markers("你看这个" + CALL) is False
    assert tm.is_all_markers("在吗？") is False
    assert tm.is_all_markers("") is False
    assert tm.is_all_markers("   ") is False
    assert tm.is_all_markers(None) is False


# ===== 稳健性 =====

def test_never_raises_on_weird_input():
    tm = _load()
    for raw in ("", "   ", "<", "<|", "</", "[", "\u273f", "to=", None, 12345,
                "<tool_call>", "</tool_call>", "<|tool_call", "a<|b"):
        tm.strip_markers(raw)
        tm.clean_outbound(raw)
        tm.is_all_markers(raw)
        _feed_all(tm, [str(raw), str(raw)])


def test_angle_bracket_prose_is_preserved():
    """正常人打的 `<` `>` 不能被误伤（比如「1 < 2」「<你好>」）。"""
    tm = _load()
    for raw in ("1 < 2 对吧", "<你好>", "a < b > c", "3<4"):
        clean, removed = tm.clean_outbound(raw)
        assert removed is False, raw
        assert clean == raw, (raw, clean)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print("OK tool marker (%d cases)" % len(fns))


# ================================================================ 裸 JSON 工具调用（2026-10-15）
# 本机真模块集成模拟抓到的真实形态：模型把调用"写"成 JSON 文本（没有标签壳），
# 整串被当人话发进群 —— 工具名、参数、甚至本机路径全广播出去了。

def test_bare_json_tool_call_is_stripped_keeping_prose():
    tm = _load()
    raw = ('好的{"name": "send_file", "arguments": {"path": "D:/secret/.env"}}')
    clean, removed = tm.clean_outbound(raw)
    assert removed is True
    assert "send_file" not in clean and ".env" not in clean, clean
    assert clean == "好的", clean


def test_bare_json_call_only_becomes_fallback():
    tm = _load()
    text, fallback, removed = tm.outbound_text(
        '{"name": "send_to_my_platform", "arguments": {"platform": "qq", "text": "在忙"}}')
    assert removed is True and fallback is True
    assert text == tm.FALLBACK_TEXT
    assert "send_to_my_platform" not in text


def test_tool_call_with_prefix_word_is_stripped():
    """`web_search\n{"query": "..."}` 这种带前缀的也要剥（首字符不是括号）。"""
    tm = _load()
    clean, removed = tm.clean_outbound('web_search\n{"tool": "web_search", "query": "天气"}')
    assert removed is True
    assert "query" not in clean, clean


def test_normal_json_data_survives():
    """用户让打印一段普通 JSON 时不许误删（那是正当内容）。"""
    tm = _load()
    clean, removed = tm.clean_outbound('{"name": "张三", "age": 30}')
    assert removed is False and clean == '{"name": "张三", "age": 30}'


def test_clean_segments_strips_json_call():
    tm = _load()
    out, removed = tm.clean_segments(['好的{"name": "mute_user", "arguments": {"user_id": "1"}}'])
    assert removed is True
    assert out == ["好的"], out
