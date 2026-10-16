# -*- coding: utf-8 -*-
r"""回归 ① **JSON 外壳绝不发进群**（2026-10-11 老板在群里看到垃圾输出的那次）。

现象（老板给的服务器日志，群 768272849，21:46:32~21:47:08）：
```
[brain] 回复正文 → qq:768272849｜带@=是｜{   "reply": [     "嗯 有啥要聊的直接说 我在"   ] }
```
模型返回的是结构化结果 `{"reply": [...]}`。**要求**：必须解析出 `reply` 数组再逐条发送；
解析失败要走"纯文本兜底"，**绝不能把 JSON 外壳（大括号、`"reply"`、方括号）发出去**。

根因（改前，`reply_format.reply_segments` 的兜底分支）
------------------------------------------------------
老代码最后一行是 `return [fallback.strip()]` —— **把原文整条发出去**。原文本身就是
`{"reply": [...]}` 的壳时，壳就上群了。三条可复现的触发路径（都在下面的用例里）：

1. **JSON 被截断**：`{"reply": ["a", "b"` —— `json.loads` 必失败，直接发壳；
2. **`reply` 解析出来了但没内容**：`{"reply": []}` / `{"reply": [1,2]}` ——
   `_clean_segments` 返回空 → 落到同一个兜底 → 发壳；
3. **壳里混了解说词**：`好的：{"reply": ["a"] 后面还有半句`。

而且**出站清洗拦不住它**：0.2.39 的 `tool_marker` 只剥工具调用标记，不认 JSON 外壳；
`StreamCleaner` 会把 `{`/`"`/`[` 原样放行。

⚠ 还有一条容易被忽略的：老测试 `tests/test_reply_format.py::test_other_shapes_are_tolerated`
把这个泄漏**写成了断言**（`reply_segments('{"reply": [坏掉的') == ['{"reply": [坏掉的']`）——
所以"改前失败"这条对照是真实存在的，不是编的。

改法（`reply_format.reply_segments`）
-------------------------------------
兜底分两条路：**看得出是回复壳** → 只抠正文（抠不出就返回 `[]`，宁可不发）；
不是壳（模型直接说了句人话）→ 原样发，老行为一个字不变。
另外解析**之前**先剥一遍工具调用标记 —— 模型常把标记塞进 JSON 字符串里且**引号不转义**，
不先剥掉外层 JSON 必然解析失败。

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_reply_shell_guard.py -q -s
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AI = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rf():
    return _load("ai_reply_format_shell", AI / "reply_format.py")


@pytest.fixture(scope="module")
def tm():
    return _load("ai_tool_marker_shell", AI / "tool_marker.py")


# ---------------------------------------------------------------- 生产口径的复刻

def _clean(tm, segments) -> list:
    """出站清洗（**生产函数**）。

    改前的 `tool_marker` 还没有 `clean_segments`（那是本轮抽出来的），
    这时按 `brain._reply_in_segments` 当年那段循环复刻一遍 —— 这样"改前失败"
    失败在**行为**上（外壳漏出去），而不是失败在"函数不存在"上。
    """
    fn = getattr(tm, "clean_segments", None)
    if fn is not None:
        return fn(segments)[0]
    cleaner = tm.StreamCleaner()
    out = []
    for seg in segments or []:
        safe = cleaner.feed(seg)
        if safe.strip():
            out.append(safe.strip())
    tail = cleaner.flush()
    if tail.strip():
        out.append(tail.strip())
    return out


def wire(rf, tm, raw: str) -> list:
    """**按生产顺序**算出"真正发出去的那几段"。

    与 `brain._reply_in_segments` 逐步对应：
        1. `reply_format.reply_segments(raw)`  —— 解析 reply 数组 / 兜底；
        2. 出站清洗 —— 每个分片过一遍 `StreamCleaner`。
    两个都是**生产代码里那个函数本身**，不是测试里另写一份。
    """
    return _clean(tm, rf.reply_segments(raw))


def old_wire(rf, tm, raw: str) -> list:
    """**改前**的 `reply_format.reply_segments`（逐行复刻 0.2.39 那份），用于改前对照。"""
    text = str(raw or "")
    if not text.strip():
        return []
    data = rf.parse_reply_json(text)
    if isinstance(data, dict) and "reply" in data:
        value = data["reply"]
        if isinstance(value, list):
            segs = [s.strip() for s in value if isinstance(s, str) and s.strip()]
            if segs:
                return _clean(tm, segs)
        elif isinstance(value, str) and value.strip():
            return _clean(tm, [value.strip()])
    if isinstance(data, list):
        segs = [s.strip() for s in data if isinstance(s, str) and s.strip()]
        if segs:
            return _clean(tm, segs)
    fallback = rf.strip_code_fence(text)
    if not fallback:
        return []
    parsed = rf.parse_reply_json(fallback)
    if isinstance(parsed, dict):
        for key in ("content", "text", "message", "answer"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return _clean(tm, [value.strip()])
    return _clean(tm, [fallback.strip()])


SHELL_TOKENS = ('{', '}', '[', ']', '"reply"', "'reply'", '"content"')


def assert_no_shell(segments, where: str):
    """发出去的东西里**不许有 JSON 外壳的任何零件**。"""
    for seg in segments:
        for token in SHELL_TOKENS:
            assert token not in seg, \
                "%s：发出去的正文里带 JSON 外壳零件 %r → %r" % (where, token, seg)
        # 也不能是"剥剩的标点壳"
        assert not re.match(r"^[\s{}\[\]()\"',:.\-]+$", seg), \
            "%s：发出去的是标点壳 %r" % (where, seg)


# ================================================================ 必测 ①：合法 JSON
def test_required_1_valid_json_reply_array(rf, tm, capsys):
    """① 合法 `{"reply": ["a","b"]}` → 发出**两条纯文本**、不含任何括号。"""
    raw = '{"reply": ["a", "b"]}'
    out = wire(rf, tm, raw)
    print("\n[必测①] 模型原始 = %s" % raw)
    print("[必测①] 实发 = %s" % json.dumps(out, ensure_ascii=False))
    assert out == ["a", "b"], "必须逐条发出 reply 数组，而不是整条 JSON"
    assert_no_shell(out, "必测①")

    # 老板日志里那种"美化过、带换行"的 JSON 形态（40 字那条就是它）
    pretty = ('{\n  "reply": [\n    "嗯 有啥要聊的直接说 我在"\n  ]\n}')
    assert len(pretty) == 40, "这条要和服务器日志里的 40 字对得上"
    out2 = wire(rf, tm, pretty)
    print("[必测①] 老板日志那条（40 字美化 JSON）实发 = %s"
          % json.dumps(out2, ensure_ascii=False))
    assert out2 == ["嗯 有啥要聊的直接说 我在"]
    assert_no_shell(out2, "必测①(日志原样)")

    # 多段 + 空串 + ```json 外壳，都要照旧
    assert wire(rf, tm, '```json\n{"reply": ["甲", "乙", ""]}\n```') == ["甲", "乙"]


# ================================================================ 必测 ②：混了工具调用标记
def test_required_2_tool_marker_mixed_in_json(rf, tm, capsys):
    """② JSON 里混了工具调用标记 → **标记与外壳都不出现**。"""
    # (a) 标记在 JSON 字符串里、引号**转义正确**
    escaped = ('{"reply": ["<tool_call>{\\"name\\": \\"get_weather\\"}</tool_call>", '
               '"正常一句"]}')
    out_a = wire(rf, tm, escaped)
    print("\n[必测②a 转义正确] 实发 = %s" % json.dumps(out_a, ensure_ascii=False))
    assert out_a == ["正常一句"], "标记段必须被剥掉，只剩人话"
    assert_no_shell(out_a, "必测②a")
    assert "<tool_call" not in "".join(out_a)

    # (b) 标记里的引号**没转义**（模型最常见的写法）——
    #     这正是"解析前先剥标记"存在的理由：不剥的话外层 JSON 必炸，只能走兜底
    unescaped = ('{"reply": ["<tool_call>{"name": "get_weather"}</tool_call>", '
                 '"正常一句"]}')
    out_b = wire(rf, tm, unescaped)
    print("[必测②b 引号未转义] 实发 = %s" % json.dumps(out_b, ensure_ascii=False))
    assert out_b == ["正常一句"], "未转义的标记也不能把外壳或标记带出去"
    assert_no_shell(out_b, "必测②b")
    assert "<tool_call" not in "".join(out_b) and "get_weather" not in "".join(out_b)

    # (c) 整条回复只有标记 → 不发标记，改说一句人话
    only = '{"reply": ["<tool_call>{"name": "x"}</tool_call>"]}'
    out_c = wire(rf, tm, only)
    print("[必测②c 只有标记] 实发 = %s" % json.dumps(out_c, ensure_ascii=False))
    assert out_c == [], "剥完没有正文就该返回空（调用方会改说人话）"
    assert_no_shell(out_c, "必测②c")

    # (d) 特殊 token 形态
    special = '{"reply": ["<|tool_call|>{\\"name\\": \\"x\\"}<|/tool_call|>", "人话"]}'
    out_d = wire(rf, tm, special)
    print("[必测②d 特殊 token] 实发 = %s" % json.dumps(out_d, ensure_ascii=False))
    assert out_d == ["人话"]
    assert_no_shell(out_d, "必测②d")


# ================================================================ 必测 ③：非法 JSON
def test_required_3_invalid_json_never_leaks_shell(rf, tm, capsys):
    """③ 非法 JSON（半截 / 多余文字）→ 走纯文本兜底、**不泄漏外壳**。"""
    cases = [
        ('{"reply": ["a", "b"', "半截（max_tokens 用尽）"),
        ('{"reply": ["只有一句"', "半截、只有一句"),
        ('好的：{"reply": ["在的"] 后面还有半句', "壳周围有解说词"),
        ('```json\n{"reply": ["甲"], "x"', "围栏 + 半截"),
        ('["一", "二"', "顶层数组半截"),
        ('{"reply": []}', "reply 是空数组"),
        ('{"reply": [1, 2]}', "reply 元素不是字符串"),
        ('{"reply": [""]}', "reply 只有空串"),
    ]
    print()
    for raw, why in cases:
        out = wire(rf, tm, raw)
        print("[必测③] %-22s 原始=%-40s 实发=%s"
              % (why, raw.replace("\n", "\\n"), json.dumps(out, ensure_ascii=False)))
        assert_no_shell(out, "必测③(%s)" % why)
        # 半截但能抠出正文的，必须把正文抠出来（不是简单丢掉）
        if "只有一句" in raw:
            assert out == ["只有一句"], why
        if "在的" in raw:
            assert out == ["在的"], why
    print("[必测③] 以上 %d 条，实发正文里 **一条外壳零件都没有**" % len(cases))


def test_required_3b_plain_text_still_goes_out(rf, tm, capsys):
    """③ 的另一半：**不是壳**的纯文本必须照旧发出去（别把"防泄漏"修成"不说话"）。"""
    for raw in ("在的，怎么了？", "  我不太确定，可能吧  ", "```\n我在呢\n```",
                "嗯 有啥要聊的直接说 我在"):
        out = wire(rf, tm, raw)
        print("[必测③b] %-28r → %s" % (raw, json.dumps(out, ensure_ascii=False)))
        assert out == [raw.strip().strip("`").strip()], "人话必须原样发出"
    # 其它形状照旧
    assert wire(rf, tm, '{"reply": "就一句"}') == ["就一句"]
    assert wire(rf, tm, '["一", "二"]') == ["一", "二"]
    assert wire(rf, tm, '{"content": "正文"}') == ["正文"]
    assert wire(rf, tm, '好的，这是回复：\n{"reply": ["在的", "怎么了"]}\n希望有帮助') \
        == ["在的", "怎么了"]
    assert wire(rf, tm, "") == []
    assert wire(rf, tm, "   ") == []


# ================================================================ 改前 / 改后对照
def test_before_after_contrast(rf, tm, capsys):
    """**改前失败 / 改后通过** 的对照（同一批输入，两条实现各跑一遍）。"""
    leaks = ['{"reply": ["a", "b"', '{"reply": []}', '{"reply": [1, 2]}',
             '{"reply": [""]}', '["一", "二"']
    print("\n================ 改前 / 改后 对照 ================")
    old_leaked, new_leaked = [], []
    for raw in leaks:
        before = old_wire(rf, tm, raw)
        after = wire(rf, tm, raw)
        before_bad = any(t in "".join(before) for t in ("{", "}", '"reply"', "["))
        after_bad = any(t in "".join(after) for t in ("{", "}", '"reply"', "["))
        if before_bad:
            old_leaked.append(raw)
        if after_bad:
            new_leaked.append(raw)
        print("  原始=%-22s 改前=%-26s 改后=%s"
              % (raw, json.dumps(before, ensure_ascii=False),
                 json.dumps(after, ensure_ascii=False)))
    print("  改前把外壳发出去的输入 = %d/%d 条" % (len(old_leaked), len(leaks)))
    print("  改后把外壳发出去的输入 = %d/%d 条" % (len(new_leaked), len(leaks)))
    assert old_leaked, "这条对照必须能复现改前的泄漏，否则对照无效"
    assert not new_leaked, "改后一条都不许泄漏外壳"
    print("  结论：改前 失败（%d 条泄漏）/ 改后 通过（0 条泄漏）" % len(old_leaked))


# ================================================================ 源码级回归锁
def test_source_lock_brain_pipes_every_segment_through_cleaner():
    """源码级锁：`brain._reply_in_segments` 仍**逐段**过 StreamCleaner，且用上了新兜底。"""
    src = (AI / "brain.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "tool_marker.clean_segments(" in code, \
        "出站清洗被绕过了：每一段都必须过一遍标记清洗"
    assert "reply_format.reply_segments(" in code, "回复整形链路被绕过了"
    assert "原文取不出正文" in code, \
        "缺少「壳抠不出正文 → 不发壳」的兜底分支"
    # 出站日志必须打**实发正文**，不许再打模型原始输出（今天就是被它误导的）
    assert "实发=" in code, "「回复正文」日志没有打实发正文"


def test_source_lock_reply_format_never_returns_raw_envelope():
    """源码级锁：`reply_segments` 的兜底里**必须有**壳判定，不能直接 return 原文。"""
    src = (AI / "reply_format.py").read_text(encoding="utf-8")
    assert "looks_like_envelope" in src, "兜底缺少「是不是回复壳」的判定"
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "if looks_like_envelope(text):" in code, \
        "壳判定没有接进 reply_segments 的兜底路径"


# ================================================================ 2026-10-16 复核补漏
def test_tool_call_shapes_without_name_key_never_leak(rf, tm, capsys):
    """**不带 name 的调用形状**也必须按"壳"处理（对抗性复核 A1/A2）。

    复核发现：工具调用键只认 arguments/function 那几个时，
      · `web_search\\n{"query": "今天天气"}`（裸词 + query 键）
      · `{"tool": "web_search"}`
    会被判成"不是壳"→ JSON 原文（连工具名带参数）发进群。
    """
    cases = [
        'web_search\n{"query": "今天天气"}',
        '{"tool": "web_search"}',
        "{'tool_name': 'qweather', 'input': '北京'}",
    ]
    for raw in cases:
        out = wire(rf, tm, raw)
        joined = " ".join(out)
        for banned in ("query", "web_search", "tool", "input", "qweather", "{", "}"):
            assert banned not in joined, (raw, out)
    print("OK tool_call_shapes_without_name_key")


def test_bare_reply_key_without_braces_salvaged(rf, tm, capsys):
    """`'reply': ['答案']`（连大括号都省了）判成壳 → 抠出正文，原文不许发。"""
    out = wire(rf, tm, "'reply': ['今天周四']")
    assert out == ["今天周四"], out
    print("OK bare_reply_key")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-s"]))
