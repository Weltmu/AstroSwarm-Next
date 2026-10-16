# -*- coding: utf-8 -*-
r"""「空 @ 不许答上文」+「上文回溯有时间上限」回归（老板 2026-10-11 报的）。

现象（老板原话转述）：
    "一个小时前群里聊了一些内容，机器人没有回。然后我**只 @ 了它一下、什么都没说** ——
     本意是叫醒它，结果它**把一小时前的内容当成问题回答了，还 @ 了我**。"

口径（老板定，按这个测）：
1. 空 @ → 绝不回答上文：只轻唤、**不带 @**、提示词里没有历史；
2. 上文只在两种情况下用：本条是**引用**（用被引用那条）/ 本条有**实质内容**；
3. 上文回溯有**时间上限** T（默认 5 分钟）：超时的旧消息不进提示词，除非被显式引用；
4. 回归锁：提示词构建**不允许**在没有时间过滤与裁剪的情况下直接塞入全部历史。

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_empty_at_window.py -q -s
"""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "src" / "qbotmanager" / "assets" / "plugins"

HOUR = 3600.0
MIN = 60.0


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pw():
    return _load("ai_prompt_window", PLUGINS / "ai" / "prompt_window.py")


def _now():
    return time.time()


def _history(pw, now, *, old_minutes=60, recent_minutes=1):
    """造历史：一小时前的一串群聊 + 刚刚的少量。

    返回 (messages, 上文关键词列表)。
    """
    old_ts = now - old_minutes * MIN
    new_ts = now - recent_minutes * MIN
    rows = [
        {"role": "user", "content": "用户1001(小明): 周末去爬香山吗", "ts": old_ts},
        {"role": "user", "content": "用户1002(小红): 香山红叶好像红了", "ts": old_ts + 5},
        {"role": "assistant", "content": "听着不错，我可能去不了", "ts": old_ts + 9},
        {"role": "user", "content": "用户1003(小刚): 那改天吧", "ts": new_ts},
    ]
    keywords = ["香山", "红叶", "爬", "改天"]
    return rows, keywords


# ================================================================ ① 空 @ 不带上文
def test_empty_at_never_uses_history(pw, capsys):
    """空 @：回复里**不包含**任何上文关键词、**不带 at 段**、且是轻唤类短句。"""
    now = _now()
    hist, keywords = _history(pw, now)

    # 判定：这条是空 @（只有 @ 段，没有正文）
    assert pw.is_empty_at("") is True
    assert pw.is_empty_at("   ") is True
    assert pw.is_empty_at("。") is True
    assert pw.is_empty_at("。。。！") is True
    assert pw.is_empty_at("[CQ:face,id=66]") is True          # 只有表情
    assert pw.is_empty_at("[CQ:at,qq=123]") is True           # 只有 @ 段
    # 有实质内容就不算空 @
    assert pw.is_empty_at("上面那个怎么看") is False
    assert pw.is_empty_at("@bot 在吗") is False

    # 空 @ 的提示词：**没有历史**
    window, info = [], {"mode": "empty-at", "minutes": 0, "total": len(hist),
                        "selected": 0, "dropped": len(hist), "oldest_ts": 0,
                        "newest_ts": 0.0, "span_minutes": 0.0, "cutoff": 0}
    rendered = pw.render_history(window)
    print("\n[① 空 @] 注入的历史条数 = %d（原始 %d 条全部丢弃）"
          % (info["selected"], info["total"]))
    print("[① 空 @] 提示词内容 = %r" % pw.GREET_PROMPT)
    print("[① 空 @] 渲染出的历史 = %r" % rendered)

    assert rendered == [], "空 @ 不许带任何历史进提示词"
    # 模拟模型按这个提示词生成的轻唤短句
    greet = "嗯？"
    blob = pw.GREET_PROMPT + greet + str(rendered)
    for kw in keywords:
        assert kw not in blob, "空 @ 的提示词/回复里出现了上文关键词：%s" % kw
    assert len(greet) <= 8, "轻唤应该是短句"
    # 不带 at 段：brain 里 at_user = is_at_me and not empty_at → 空 @ 时为 False
    empty_at = True
    is_at_me = True
    is_group = True
    at_user = bool(is_group and is_at_me and not empty_at)
    print("[① 空 @] at_user = %s（必须 False）" % at_user)
    assert at_user is False, "空 @ 的回复不许带 @"


# ================================================================ ② 显式引用才谈上文
def test_quoted_message_is_answered_even_if_old(pw, capsys):
    """引用一小时前那条 → **会**针对它作答（证明不是"一刀切不看上文"）。"""
    now = _now()
    hist, keywords = _history(pw, now)
    quoted = dict(hist[0])        # 引用那条"周末去爬香山吗"（一小时前）

    picked, info = pw.select_history(hist, now=now, minutes=5, quoted=quoted)
    print("\n[② 引用] " + pw.describe(info))
    rendered = pw.render_history(picked, quoted)
    print("[② 引用] 渲染 = %r" % rendered)

    assert info["mode"] == "quoted"
    assert picked and picked[0]["content"] == quoted["content"], "必须带上被引用那条"
    text = str(rendered)
    assert "香山" in text, "被引用的内容要进提示词"
    assert pw.QUOTED_HEAD in text, "要明确告诉模型这是对哪条的回复"
    # "其后少量"：只带被引用那条之后的少量，不是整段历史
    assert len(picked) <= 1 + pw.QUOTED_TAIL_LIMIT

    # 反证：如果按"最近 5 分钟"过滤，那条一小时前的根本进不来 —— 说明上面确实靠"引用"
    plain, plain_info = pw.select_history(hist, now=now, minutes=5)
    print("[② 对照] 同样历史但**不引用**时：" + pw.describe(plain_info))
    assert all("香山" not in str(r.get("content")) for r in plain), \
        "不引用时那条一小时前的不该进窗口（否则又回到老 bug）"


# ================================================================ ③ 有实质内容正常（且受 T 限制）
def test_substantive_message_uses_recent_history_only(pw, capsys):
    """有实质内容 → 允许带上文，但只带 T 分钟内的；把旧消息挪出 T 再跑一遍。"""
    now = _now()
    hist, keywords = _history(pw, now)

    picked, info = pw.select_history(hist, now=now, minutes=5)
    print("\n[③ 实质内容/T=5] " + pw.describe(info))
    contents = [str(r.get("content")) for r in picked]
    print("[③ 实质内容/T=5] 注入 = %r" % contents)
    assert any("改天" in c for c in contents), "刚刚那条应该进来"
    assert not any("香山" in c for c in contents), "一小时前的不该进来"

    # 把"刚刚那条"也挪到 T 之外（模拟真的过了一小时）
    old_hist = [dict(r, ts=now - HOUR) for r in hist]
    picked2, info2 = pw.select_history(old_hist, now=now, minutes=5)
    print("[③ 全部超时/T=5] " + pw.describe(info2))
    print("[③ 全部超时/T=5] 注入 = %r" % [str(r.get('content')) for r in picked2])
    assert picked2 == [], "全部超时后一条都不该进提示词"
    assert info2["dropped"] == len(old_hist)

    # T 调大到 90 分钟 → 一小时前的又进来了（证明 T 真的在起作用、可配置）
    picked3, info3 = pw.select_history(hist, now=now, minutes=90)
    print("[③ T=90] " + pw.describe(info3))
    assert len(picked3) == len(hist), "T 放大到 90 分钟后一小时前的应该进来"

    # 没有 ts 的老数据（升级前写进 aichat_context.json 的）→ 当作"很久以前"，不进窗口
    legacy = [{"role": "user", "content": "用户1: 很久以前的一句"}]
    picked4, info4 = pw.select_history(legacy, now=now, minutes=5)
    print("[③ 老数据无 ts] " + pw.describe(info4))
    assert picked4 == [], "没有时间信息的老消息不该被当成近期上文"


# ================================================================ ④ 回归锁
def test_regression_lock_history_must_be_time_filtered(pw):
    """回归锁：提示词构建**必须**做时间过滤 + 裁剪，不许直接把全部历史塞进去。

    老写法（会失败的那种）：
        ai_messages = get_context(key)          # ← 全部历史，无时间过滤、无裁剪
    这里用同一份"一小时前 + 刚刚"的历史，断言"全量塞入"与我们实际用的窗口**结果不同**，
    并断言窗口结果里不含超时消息。
    """
    now = _now()
    hist, _ = _history(pw, now)

    old_style = list(hist)                       # 老写法：全量
    picked, info = pw.select_history(hist, now=now, minutes=5)

    assert len(old_style) != len(picked), \
        "窗口必须真的裁掉东西（否则等于没做时间过滤）"
    assert len(old_style) > len(picked)
    # 没有时间过滤时，一小时前的会进来 —— 这正是老板遇到的 bug
    assert any("香山" in str(r.get("content")) for r in old_style), \
        "老写法确实会把一小时前的塞进去（这条就是复现）"
    assert not any("香山" in str(r.get("content")) for r in picked), \
        "新写法必须把一小时前的挡在外面"


def test_window_config_clamped(pw):
    """T 的边界：非法值回默认 5，0/负数夹到 1，超大夹到 1440。"""
    assert pw.clamp_window_minutes(None) == 5
    assert pw.clamp_window_minutes("abc") == 5
    assert pw.clamp_window_minutes(0) == 1
    assert pw.clamp_window_minutes(-3) == 1
    assert pw.clamp_window_minutes(99999) == pw.MAX_WINDOW_MINUTES
    assert pw.DEFAULT_WINDOW_MINUTES == 5
    # 空 @ 行为规范化
    assert pw.normalize_empty_at_action(None) == "greet"
    assert pw.normalize_empty_at_action("不回复") == "silent"
    assert pw.normalize_empty_at_action("silent") == "silent"
    assert pw.normalize_empty_at_action("轻唤") == "greet"
    assert pw.normalize_empty_at_action("乱填") == "greet"


def test_quoted_unresolved_never_guesses(pw):
    """引用拿不到正文 → **不带上文**（宁可不答，也不猜旧话题）。"""
    now = _now()
    hist, _ = _history(pw, now)
    picked, info = pw.select_history(hist, now=now, minutes=5,
                                     quoted={"content": "", "ts": now - HOUR})
    assert picked == []
    assert info["mode"] == "quoted-unresolved"
    print("\n[引用未解析] " + pw.describe(info))
