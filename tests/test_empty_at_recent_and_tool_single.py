# -*- coding: utf-8 -*-
"""2026-10-13 修复守卫：空 @ 微窗口（接住"请求 + @ 一下"）+ 本轮动过工具只发一条。

老板实测（服务器 EVA，23:13）：
"你给我点一个吧…甜甜的恋爱的感觉的歌"（没 @，被 ignore）→ "@EVA"（空 @，0 条上文）
→ 只回「在的呢 / 这么晚了呀，哥哥还不睡」→ 之后才补发卡片，而且配文被拆成两条。
两处修：① 空 @ 带 2 分钟微窗口，最近那条像是在等你回应就直接办；
② 本轮动过工具（卡片已单独发过）→ 回复只发一条；空 @ 轻唤同样只发一条。
"""
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
BRAIN = AI / "brain.py"
CHAT = AI / "chat.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PW = _load("pw_recent_test", AI / "prompt_window.py")
RF = _load("rf_single_test", AI / "reply_format.py")


def test_micro_window_constants_sane():
    assert 0 < PW.EMPTY_AT_LOOKBACK_MINUTES <= 3, "微窗口最多 3 分钟，别把旧话题翻出来"
    assert 0 < PW.EMPTY_AT_LOOKBACK_LIMIT <= 6


def test_micro_window_keeps_recent_drops_old():
    now = time.time()
    hist = [
        {"role": "user", "content": "用户1: 一个小时前聊的爬山", "ts": now - 3600},
        {"role": "assistant", "content": "好啊", "ts": now - 3590},
        {"role": "user", "content": "用户1: 你给我点一个吧，想听甜甜的歌", "ts": now - 60},
    ]
    picked, info = PW.select_history(
        hist, now=now, minutes=PW.EMPTY_AT_LOOKBACK_MINUTES,
        limit=PW.EMPTY_AT_LOOKBACK_LIMIT)
    assert len(picked) == 1 and "点一个" in picked[0]["content"], \
        "微窗口要接住刚刚的请求"
    assert all("爬山" not in row["content"] for row in picked), \
        "一小时前的旧话题不能进微窗口"


def test_greet_prompt_recent_says_handle_recent_request():
    p = PW.GREET_PROMPT_RECENT
    assert "办" in p and "旧话题" in p


def test_brain_uses_micro_window_and_greet_variant():
    src = BRAIN.read_text(encoding="utf-8")
    assert "EMPTY_AT_LOOKBACK_MINUTES" in src and "EMPTY_AT_LOOKBACK_LIMIT" in src
    assert "empty-at-recent" in src
    # 2026-10-15：只有"窗口里全是他自己刚说的话"才给"接着办"那版提示词；
    # 回退到群体窗口时给普通轻唤（否则 B 空 @ 一下就会去办 A 提的事）
    assert "GREET_PROMPT_RECENT if empty_at_mine else GREET_PROMPT" in src
    # 轻唤指令要真的发给模型（改前只存上下文，等于没发）
    assert '{"role": "user", "content": greet_text}' in src


def test_brain_does_not_store_greet_into_context():
    src = BRAIN.read_text(encoding="utf-8")
    i = src.index('user_info = f"用户{msg.user_id}')
    block = src[i:i + 500]
    assert "if not empty_at:" in block and "add_message" in block, \
        "空 @ 的轻唤指令不该落进上下文（会污染之后几轮的窗口）"


def test_brain_empty_at_window_prefers_same_sender():
    """空 @ 微窗口要**优先只看这个人自己**的话（2026-10-15）。

    不然：A 在群里问了句话，B 空 @ 一下 → 机器人拿着 A 的问题回 B（看着就是答错人）。
    注意不能一刀切：他自己窗口内没说过话时仍用群体窗口（"接住正在聊的话题"要保留）。
    """
    src = BRAIN.read_text(encoding="utf-8")
    i = src.index("empty-at-recent")
    block = src[i:i + 1400]
    assert "empty_at_same_sender" in block, "空 @ 微窗口没做同发送者过滤"
    assert 'm.get("user_id")' in block, "过滤要按记录里的 user_id 比"
    assert "if _mine:" in block, "过滤后空了要回退群体窗口，别把自己关死"
    # 记录里必须真的存了 user_id —— 不存的话上面那个过滤永远是空
    assert src.count('user_id=str(getattr(msg, "user_id", "") or "")') >= 2, \
        "群/私聊两条 add_message 都要带上发送者"


def test_plan_parts_force_single_merges_with_punctuation():
    parts, info = RF.plan_parts(
        ["怎么啦这是", "给你挑了首《甜甜的》，甜的那种……听完就乖乖去睡吧"],
        force_single=True, force_single_reason="本轮用了工具（卡片/动作已单独发过）")
    assert len(parts) == 1
    assert parts[0].startswith("怎么啦这是。给你挑了首"), parts
    assert info["merged"] and "只发一条" in info["merged_reason"], info
    assert "工具" in info["merged_reason"]


def test_plan_parts_force_single_noop_for_one_part():
    parts, info = RF.plan_parts(["就一句"], force_single=True)
    assert parts == ["就一句"]


def test_brain_and_chat_wire_tool_stats_to_single_send():
    src = BRAIN.read_text(encoding="utf-8")
    assert "stats=_stats" in src and '_stats.get("tool_calls")' in src
    assert "single=_single, single_reason=_single_reason" in src
    assert "force_single=bool(single)" in src
    chat = CHAT.read_text(encoding="utf-8")
    assert chat.count('stats["tool_calls"] = int(stats.get("tool_calls") or 0)') == 2, \
        "两个工具执行入口（首轮 + 收尾轮）都要给 stats 计数"
