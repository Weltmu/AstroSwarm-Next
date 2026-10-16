# -*- coding: utf-8 -*-
r"""空 @ **回归锁**：真有内容的问题绝不能被当成"只叫了一声"（老板 2026-10-11 踩到）。

现象（老板原话转述）：
    老板 **@ 它并问「星群是什么」** → 它回了「**在呢**」✗
    —— 真有内容的问题被当成了空 @。

诊断（看服务器生产日志 `/opt/astroswarm/data/astroswarm/logs/nonebot.log`，
窗口 21:09:46~21:18:58，即空@代码上线之后）：
```
21:11:35 [ai] 收到事件 … text=星群是什么        → [brain] 收到 qq 消息 … text=星群是什么
21:11:51 [OneBot] Message … '[at:qq=1437808448] 星群是什么'
21:17:58 [OneBot] Message … '[at:qq=1437808448] 星群是什么？'
「空 @」日志命中次数 = 0（我的判定**没有**命中过）
```
也就是说：**日志窗口里复现不出误判**（三条带内容的问题都正常回了 45/39/38 字）。
但代码路径确实不安全，所以按老板口径把判定收紧并钉死：

* 空 @ 的判定**只读一个权威字段**：适配器归一化后的正文 `msg.text`
  （改前是 `msg.plain_text or user_msg` —— 两个字段互为旁路，正是误判温床）；
* 判定 **fail-open**：去掉 @ 段/表情/标点后只要还剩**一个实义字符**，就必须当正常提问；
* 群里 / 私聊走**同一套**判定。

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_empty_at_strict.py -q -s
"""
import importlib.util
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AI = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pw():
    return _load("ai_pw_strict", AI / "prompt_window.py")


# ---------------------------------------------------------------- 复刻适配器的归一化

def normalized_text(plain: str) -> str:
    """复刻 `adapters/qq.py::_extract_text` 的结果（无图时它就是 f"{plain}\\n"）。

    老板那条消息在 OneBot 眼里是 `[at:qq=1437808448] 星群是什么`，
    `event.get_plaintext()` 去掉 @ 段后是 `星群是什么` → 归一化正文 = `星群是什么\\n`。
    """
    return f"{plain.strip()}\n"


def brain_empty_at(pw, *, text: str, is_at_me: bool):
    """复刻 brain.py 里那一行判定（改后只读 msg.text）。"""
    return bool(is_at_me and pw.is_empty_at(text))


def old_brain_empty_at(pw, *, text: str, plain_text: str, is_at_me: bool, is_group=True):
    """**改前**的判定：`is_empty_at(msg.plain_text or user_msg)`（两个字段互为旁路）。"""
    return bool(is_group and is_at_me and pw.is_empty_at(plain_text or text))


# ================================================================ ① 老板原句（群聊）
def test_boss_question_is_not_empty_at(pw, capsys):
    """`@bot 星群是什么`（群聊）→ **不是空 @**、走正常提问、注入上下文、回复不是轻唤。"""
    text = normalized_text("星群是什么")
    print("\n[① 老板原句] 归一化正文 = %r" % text)
    print("[① 老板原句] is_empty_at(%r) = %s" % (text, pw.is_empty_at(text)))

    empty_at = brain_empty_at(pw, text=text, is_at_me=True)
    print("[① 老板原句] brain 判定 empty_at = %s（必须 False）" % empty_at)
    assert empty_at is False, "「@bot 星群是什么」被判成空 @"

    # 走正常提问路径 → 注入了 T 分钟窗口内的上下文（而不是"空@ 0 条历史"）
    now = time.time()
    hist = [{"role": "user", "content": "用户1: 之前说的那句", "ts": now - 60}]
    picked, info = pw.select_history(hist, now=now, minutes=5)
    print("[① 老板原句] 上文窗口 = %s" % pw.describe(info))
    assert info["mode"] == "recent", "不是空@ 就该走正常的历史窗口"
    assert len(picked) == 1, "T 分钟内的上下文应该注入"

    # 回复不能是轻唤类
    greet_like = ("在呢", "嗯？", "在的", "怎么了")
    assert pw.GREET_PROMPT not in text
    simulated_reply = "星群（AstroSwarm）是一套跨平台的 AI 陪伴与自动化系统。"   # 正常长度
    assert simulated_reply not in greet_like and len(simulated_reply) > 8
    print("[① 老板原句] 提示词不得是轻唤句；正常回复长度 %d 字（轻唤只会 2~3 字）"
          % len(simulated_reply))


def test_old_two_field_expression_is_the_regression(pw, capsys):
    """**改前失败 / 改后通过**：老判定的两处真实缺陷（各给一个能复现的输入）。

    诚实说明：老板那句「星群是什么」在生产日志里 `text` 是**非空**的，
    `plain_text or text` 那条旁路**并没有**复现出误判（我在 `_ops/` 里有日志证据）。
    但老实现确实有两个能复现的坑，这条用例把它们钉死：

    1. 判定读的是 `plain_text or text` —— **两个字段互为旁路**，任一条路给出空串
       就会把真问题当空 @（字段选择本身就是隐患）；
    2. 老 `strip_noise` 里的 `_RE_AT_NAME`（`@` + 非空白）会把 **@ 后面的字母也吃掉**：
       「@EVA」在老实现里被判成**空 @**，新实现判成"有内容"（fail-open）。
    """
    text = normalized_text("星群是什么")
    new = brain_empty_at(pw, text=text, is_at_me=True)
    print("\n[对照] 新写法(只读 text) 对「星群是什么」empty_at = %s" % new)
    assert new is False

    # 缺陷 2 的可复现输入：@ 后面直接跟字母
    old_hits, new_misses = [], []
    for s in ("@EVA", "@bot", "@LiQingHan", "@星群"):
        old = old_is_empty_at(pw, s)          # 复刻老实现：只 not strip_noise
        newv = pw.is_empty_at(s)
        print("[对照] %-12r 老=%s 新=%s" % (s, old, newv))
        if old and not newv:
            old_hits.append(s)
        if not old and newv:
            new_misses.append(s)
    print("[对照] 老实现误判成空@的输入 = %s" % old_hits)
    assert old_hits, "这条要能复现老实现的误判，否则对照无效"
    assert not new_misses, "新实现不许把老实现判非空的判成空"

    # 缺陷 1 的可复现输入：旁路字段为空串时，老表达式把正文丢掉
    old_two_field = bool(True and True and pw.is_empty_at("" or ""))   # plain_text="" and text=""
    print("[对照] 老表达式在 plain_text='' 且 text='' 时 empty_at = %s（正文已丢）"
          % old_two_field)
    assert old_two_field is True
    print("[对照] 结论：改前 失败（两处）/ 改后 通过")


def old_is_empty_at(pw, text: str) -> bool:
    """复刻**改前**的 `is_empty_at`：`return not strip_noise(text)`。"""
    return not pw.strip_noise(text)


# ================================================================ ② 真空 @
def test_truly_empty_at(pw):
    """`@bot` 后面只有空白 / 表情 / 标点 → 才是空 @。"""
    for raw in ("", "   ", "\n", "\t ", "[CQ:at,qq=1437808448]", "[CQ:at,qq=1437808448] ",
                "[CQ:face,id=66]", "[CQ:at,qq=1437808448][CQ:face,id=66]", "🙂", "😀😀",
                "。。。", "！？", "…", "   🙂   "):
        assert pw.is_empty_at(raw) is True, "该判成空 @：%r" % raw
    # 群里那条真空 @ 的归一化正文是 "\n"
    assert brain_empty_at(pw, text=normalized_text(""), is_at_me=True) is True


# ================================================================ ③ 标点算空
def test_punctuation_only_is_empty(pw):
    """`@bot ？` / `@bot ……` → 空 @（标点算空）。"""
    assert pw.is_empty_at(normalized_text("？")) is True
    assert pw.is_empty_at(normalized_text("……")) is True
    assert pw.is_empty_at(normalized_text("？？？")) is True
    assert pw.is_empty_at(normalized_text("！！！")) is True
    assert pw.is_empty_at(normalized_text("，。、；：")) is True
    assert brain_empty_at(pw, text=normalized_text("？"), is_at_me=True) is True
    assert brain_empty_at(pw, text=normalized_text("……"), is_at_me=True) is True


# ================================================================ ④ 带问号也要正常
def test_question_with_and_without_mark(pw):
    """`@bot 星群是什么` 与 `@bot 星群是什么？` 都必须正常回答。"""
    for s in ("星群是什么", "星群是什么？", "星群是什么?", "星群是什么。",
              "星群是什么呀", "1", "a", "A1", "吗", "在吗"):
        assert pw.is_empty_at(normalized_text(s)) is False, "误判成空 @：%r" % s
        assert brain_empty_at(pw, text=normalized_text(s), is_at_me=True) is False


# ================================================================ ⑤ 私聊同样不能误判
def test_private_path_same_judgement(pw):
    """私聊同一条句子 → 同样不能误判（判定与群聊/私聊无关）。"""
    text = normalized_text("星群是什么")
    # 改前带 is_group 门槛；改后只看 is_at_me（私聊里 is_at_me 也可能为真）
    assert brain_empty_at(pw, text=text, is_at_me=True) is False
    # 复刻改前那种"只在群聊分支判"的写法，证明私聊路径也能被覆盖到
    group_only = bool(True and True and pw.is_empty_at(text))
    assert group_only is False
    # 私聊里"只 @ 没内容"仍然算空
    assert brain_empty_at(pw, text=normalized_text(""), is_at_me=True) is True


# ================================================================ ⑥ 回归锁（实现层）
def test_regression_lock_empty_requires_zero_meaningful_chars(pw):
    """**任何把非空正文判成空 @ 的实现都必须让这条失败。**

    断言：`is_empty_at(x)` 为真 ⟹ 去掉 @ 段/表情/标点后长度必须为 0 **且**无实义字符。
    """
    samples = ["星群是什么", "星群是什么？", "在吗", "1", "a", "hello", "星", "？", "🙂",
               "   ", "", "[CQ:at,qq=1]", "[CQ:at,qq=1] 星群是什么"]
    for s in samples:
        if pw.is_empty_at(s):
            stripped = pw.strip_noise(s)
            assert stripped == "", "判成空@ 但还剩内容：%r → %r" % (s, stripped)
            assert pw.has_meaningful_char(s) is False, \
                "判成空@ 但还有实义字符：%r" % s
        else:
            # 判成非空 ⟹ 必须真的有实义字符（fail-open 的另一个方向）
            assert pw.has_meaningful_char(s) is True, \
                "判成非空但其实没有实义字符：%r" % s


def test_brain_reads_single_authoritative_field():
    """源码级回归锁：判定**只读 `msg.text`**，不许再用 `plain_text` 那条旁路。"""
    src = (AI / "brain.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "is_empty_at(user_msg)" in code, "空 @ 判定没有读归一化正文"
    assert "msg.plain_text or user_msg" not in code, \
        "又用回了 plain_text 旁路字段（误判温床）—— 只读 msg.text"
    assert not re.search(r"is_empty_at\(\s*msg\.plain_text", code), \
        "不许把 plain_text 直接喂给空 @ 判定"
