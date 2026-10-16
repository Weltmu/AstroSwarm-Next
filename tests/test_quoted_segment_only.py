# -*- coding: utf-8 -*-
r"""回归 ② **只有 @ 段的消息不是「引用」**（2026-10-11 上下文被整批丢掉的那次）。

现象（服务器 `nonebot.log`，群 768272849）：
```
21:46:32 OneBot | Message 1875386618 from 1000000002@[群:768272849] '[at:qq=1437808448] 你是谁'
21:46:32 [brain] @ 但有实义内容 → 按正常提问处理：'你是谁'
21:46:32 [brain] 上文窗口：引用未解析 → **不带上文**（丢弃 20 条）     ← 20 条全丢
21:46:44 OneBot | Message … '[at:qq=1437808448] 知识库截止日期'
21:46:44 [brain] 上文窗口：引用未解析 → **不带上文**（丢弃 20 条）     ← 又是 20 条全丢
```
这两条消息**只有 @ 段，根本没有 reply/quote 段**，却被判成「引用拿不到正文」，
于是上下文被全部丢弃 —— 机器人对所有问题都只能瞎答
（「我在呢，有啥想聊的直接说」就是结果）。

根因（两处叠加）
----------------
1. `adapters/qq.py::_extract_quoted` 在「没有 reply 段」时返回的是 **`{}`（空字典）**；
2. `prompt_window.select_history` 用 **`if quoted is not None:`** 判 —— `{}` 也满足
   `is not None`，于是走进「有引用段但拿不到正文」分支，`return [], info`（丢光上文）。

**空字典被当成了「有引用段」。**

修法
----
* 适配器：没有 `reply` 段 → 明确返回 **`None`**（= 没有引用段），并写日志
  「无 reply 段（只有 @ 段或纯正文）→ 无引用段，正常窗口」；`@` 段一律不算引用。
* `prompt_window.normalize_quoted`：`None` / `{}` / 非 dict → `None`；
  有 dict 但 `content` 为空 → 原样返回（= 有引用段但拿不到正文，**这条规则不能改坏**）。
* `select_history` / `render_history`：一律按**真值**判（`if quoted:`）。
* `brain`：每条消息都打一行「引用段=无（本条只有 @ 段或纯正文，不算引用）→ 正常窗口」。

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_quoted_segment_only.py -q -s
"""
import asyncio
import importlib.util
import json
import sys
import time
import types
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
def pw():
    return _load("ai_prompt_window_quoted", AI / "prompt_window.py")


@pytest.fixture(scope="module")
def qq():
    """按**真实源码**加载 `adapters/qq.py` 的 `_extract_quoted`。

    qq.py 依赖 nonebot 的 onebot v11 适配器（测试环境里没装），所以这里把那几个
    符号和同包的兄弟模块都做成最小替身 —— 被测的 `_extract_quoted` 本身**一行没改**。
    """
    import nonebot  # noqa: F401  真实的 nonebot（有 logger）

    pkg = types.ModuleType("qqpkg")
    pkg.__path__ = [str(AI)]
    sys.modules["qqpkg"] = pkg
    for name in ("image2txt", "manager", "send2root"):
        sys.modules["qqpkg." + name] = types.ModuleType("qqpkg." + name)
    sys.modules["qqpkg.image2txt"].recognize_image = lambda *a, **k: None
    sys.modules["qqpkg.manager"].chat_manager = types.SimpleNamespace(
        is_image_recognition_enabled=lambda: False)
    sys.modules["qqpkg.send2root"].send_error_to_super_users = lambda *a, **k: None
    sys.modules["qqpkg.platform"] = _load("qqpkg_platform", AI / "platform.py")

    onebot = types.ModuleType("nonebot.adapters.onebot.v11")
    onebot.Bot = object
    onebot.MessageEvent = object
    onebot.MessageSegment = types.SimpleNamespace(image=lambda u: ("image", u))

    class _Message:
        def __init__(self, raw=""):
            self.raw = raw

        def extract_plain_text(self):
            return str(self.raw)

    onebot.Message = _Message
    sys.modules.setdefault("nonebot.adapters.onebot",
                           types.ModuleType("nonebot.adapters.onebot"))
    sys.modules["nonebot.adapters.onebot.v11"] = onebot
    return _load("qqpkg.adapters.qq", AI / "adapters" / "qq.py")


# ---------------------------------------------------------------- 假事件 / 假 bot（绝不触网）

class Seg:
    def __init__(self, type_, **data):
        self.type = type_
        self.data = data


class FakeEvent:
    """只带消息段的假事件（对应 OneBot 的 `event.message`）。"""

    def __init__(self, segments):
        self.message = segments


class FakeBot:
    """假 bot：`get_msg` 按预设表返回；没预设就抛（模拟「取不到」）。"""

    def __init__(self, messages=None):
        self.messages = messages or {}
        self.calls = []

    async def get_msg(self, message_id):
        self.calls.append(message_id)
        if message_id not in self.messages:
            raise RuntimeError("消息不存在或平台不返回正文")
        return self.messages[message_id]


def quoted_of(qq, segments, messages=None):
    """跑**真实的** `_extract_quoted`（同步入口，内部 asyncio.run）。"""
    return asyncio.run(qq._extract_quoted(FakeBot(messages), FakeEvent(segments)))


NOW = time.time()
HISTORY = [{"role": "user", "content": "用户1: 之前说的那句 %d" % i, "ts": NOW - 60 + i}
           for i in range(20)]        # 20 条 —— 与老板日志里的「丢弃 20 条」同规模


def _norm(pw, value):
    """brain 侧的引用归一（**生产函数**）。

    改前的 `prompt_window` 还没有 `normalize_quoted`（那是本轮加的），这时原样传下去 ——
    这**正是当年那段代码的行为**（`{}` 直接进 `select_history`，被 `is not None` 判成
    "有引用段"）。这样"改前失败"失败在**行为**上，而不是失败在"函数不存在"上。
    """
    fn = getattr(pw, "normalize_quoted", None)
    return fn(value) if fn is not None else value


def decide(pw, quoted, *, minutes=5, history=None):
    """brain 里那段决策的**复刻**：归一 → 选窗口 → 渲染。"""
    norm = _norm(pw, quoted)
    picked, info = pw.select_history(history if history is not None else HISTORY,
                                     now=NOW, minutes=minutes, quoted=norm)
    ctx_messages = pw.render_history(picked, norm)
    return norm, picked, info, ctx_messages


def _state(pw, value) -> str:
    """引用段状态；改前没有 quoted_state 就按当年那套（None / 非 None）判。"""
    fn = getattr(pw, "quoted_state", None)
    if fn is not None:
        return fn(value)
    return "none" if value is None else "unresolved"


# ================================================================ 必测 ①：只有 @ 段 + 正文
def test_required_1_at_only_message_keeps_context(pw, qq, capsys):
    """① 只有 @ 段 + 正文 → **必须正常带上下文**（不是丢掉）。"""
    # OneBot 对 `[at:qq=1437808448] 你是谁` 给出的段列表：只有一个 at 段
    segments = [Seg("at", qq="1437808448"), Seg("text", text=" 你是谁")]
    quoted = quoted_of(qq, segments)
    print("\n[必测①] `_extract_quoted` 返回 = %r（必须 None = 没有引用段）" % (quoted,))
    assert quoted is None, "@ 段绝不算引用：没有 reply 段就该返回 None"

    norm, picked, info, ctx_messages = decide(pw, quoted)
    print("[必测①] 归一后 quoted = %r，状态 = %s" % (norm, _state(pw, quoted)))
    print("[必测①] " + pw.describe(info))
    assert norm is None
    assert _state(pw, quoted) == "none"
    assert info["mode"] == "recent", "没有引用段就必须走正常窗口"
    assert len(picked) == 20 and info["dropped"] == 0, "20 条上下文一条都不许丢"
    assert len(ctx_messages) == 20, "提示词里必须真的带上这 20 条"
    assert "引用段=无" in pw.describe(info), "日志必须写明「引用段=无 → 正常窗口」"


def test_required_1b_brain_normalizes_empty_dict(pw, capsys):
    """① 的另一半：**老适配器返回的 `{}`** 也不许再被当成引用（回归的直接输入）。"""
    print("\n[必测①b] normalize_quoted({}) = %r" % (_norm(pw, {}),))
    assert _norm(pw, {}) is None, "空字典 = 没有引用段"
    assert _state(pw, {}) == "none"

    # 改前：`quoted={}` 直接进 select_history 的 `if quoted is not None` → 全丢
    picked_old, info_old = pw.select_history(HISTORY, now=NOW, minutes=5, quoted={})
    # 改后：brain 先归一成 None → 正常窗口
    _nrm, picked_new, info_new, _msgs = decide(pw, {})
    print("[必测①b] 直接把 {} 传进 select_history = mode=%s 注入 %d 条 / 丢弃 %d 条"
          % (info_old["mode"], len(picked_old), info_old["dropped"]))
    print("[必测①b] 改后（先归一）              = mode=%s 注入 %d 条 / 丢弃 %d 条"
          % (info_new["mode"], len(picked_new), info_new["dropped"]))
    # 注：select_history 自己也按真值判了，所以现在两条路都不丢 —— 双保险
    assert len(picked_old) == 20, "select_history 自身也按真值判，{} 不该丢上下文"
    assert len(picked_new) == 20
    assert info_new["mode"] == "recent"


# ================================================================ 必测 ②：真有 reply 段且取到正文
def test_required_2_real_reply_segment_uses_quoted_body(pw, qq, capsys):
    """② 真有 reply 段且能取到正文 → **用被引用的那条**。"""
    segments = [Seg("reply", id="12345"), Seg("at", qq="1437808448"),
                Seg("text", text=" 这句是回它的")]
    messages = {12345: {"message": "一小时前那句旧话", "sender": {"nickname": "清秋"}}}
    quoted = quoted_of(qq, segments, messages)
    print("\n[必测②] `_extract_quoted` 返回 = %s" % json.dumps(quoted, ensure_ascii=False))
    assert quoted and quoted["content"] == "清秋：一小时前那句旧话", \
        "必须带上被引用那条的正文"
    assert quoted["message_id"] == "12345"

    norm, picked, info, ctx_messages = decide(pw, quoted)
    print("[必测②] " + pw.describe(info))
    assert _state(pw, quoted) == "resolved"
    assert info["mode"] == "quoted"
    line = pw.describe(info)
    assert "引用段=有" in line and "用被引用的那条" in line
    # 被引用那条进了提示词最前面（话题由它决定）
    assert ctx_messages[0]["role"] == "system"
    assert "清秋：一小时前那句旧话" in ctx_messages[0]["content"]
    assert ctx_messages[0]["content"].startswith(pw.QUOTED_HEAD)
    # **不受 T 限制**：即使它很旧也要带上（这是唯一允许翻旧账的情况）
    old_quoted = {"content": "很久以前那条", "ts": NOW - 3600, "message_id": "1"}
    _n, picked_old, info_old, _m = decide(pw, old_quoted)
    print("[必测②] 一小时前的被引用那条：注入 %d 条，mode=%s"
          % (len(picked_old), info_old["mode"]))
    assert info_old["mode"] == "quoted" and picked_old[0]["content"] == "很久以前那条"


# ================================================================ 必测 ③：有 reply 段但取不到正文
def test_required_3_reply_segment_unresolved_drops_context(pw, qq, capsys):
    """③ 有 reply 段但取不到正文 → **不带上文** + 日志写明原因（这条规则不能改坏）。"""
    segments = [Seg("reply", id="999"), Seg("text", text=" 看看这个")]
    quoted = quoted_of(qq, segments, messages={})       # get_msg 会抛
    print("\n[必测③] `_extract_quoted` 返回 = %s" % json.dumps(quoted, ensure_ascii=False))
    assert quoted is not None, "有 reply 段就必须返回 dict（区别于「没有引用段」的 None）"
    assert quoted["content"] == ""
    assert quoted.get("reason"), "必须写清原因，日志里要能看出为什么丢上下文"

    norm, picked, info, ctx_messages = decide(pw, quoted)
    print("[必测③] " + pw.describe(info))
    assert norm is not None, "有引用段但没正文时不能归一成 None，否则就变成正常窗口了"
    assert _state(pw, quoted) == "unresolved"
    assert info["mode"] == "quoted-unresolved"
    assert picked == [] and ctx_messages == [], "拿不到正文就必须不带上文"
    assert info["dropped"] == 20
    line = pw.describe(info)
    assert "引用段=有（取不到正文）" in line and "不带上文" in line and "原因" in line

    # 有 reply 段、get_msg 成功但正文是空的 → 同样按「取不到正文」处理
    quoted2 = quoted_of(qq, segments, messages={999: {"message": "", "sender": {}}})
    assert quoted2["content"] == "" and quoted2.get("reason")
    _n2, picked2, info2, _m2 = decide(pw, quoted2)
    assert info2["mode"] == "quoted-unresolved" and picked2 == []
    print("[必测③] 空正文变体：%s" % pw.describe(info2))


# ================================================================ 必测 ④：纯正文（无 @ 无引用）
def test_required_4_plain_text_keeps_context(pw, qq, capsys):
    """④ 纯正文（无 @ 无引用）→ 正常带上下文。"""
    segments = [Seg("text", text="卡循环了")]                  # 连 at 段都没有
    quoted = quoted_of(qq, segments)
    print("\n[必测④] `_extract_quoted` 返回 = %r" % (quoted,))
    assert quoted is None

    norm, picked, info, ctx_messages = decide(pw, quoted)
    print("[必测④] " + pw.describe(info))
    assert info["mode"] == "recent"
    assert len(picked) == 20 and len(ctx_messages) == 20

    # 图片段 / 表情段同样不算引用
    for segs in ([Seg("image", file="a.jpg")], [Seg("face", id="66")],
                 [Seg("at", qq="1437808448"), Seg("face", id="66")]):
        assert quoted_of(qq, segs) is None, "非 reply 段一律不算引用"


# ================================================================ 改前 / 改后对照
def test_old_code_path_dropped_context(pw, capsys):
    """**改前失败**的精确复刻：0.2.39 那两行代码，喂老板日志里的输入，上下文全丢。

    老代码（`select_history` 里）：
        `if quoted is not None:`  ← `{}` 也满足
        `    if not body: info["mode"]="quoted-unresolved"; return [], info`
    """
    def old_select_history(quoted):
        rows = list(HISTORY)
        if quoted is not None:                       # ← 老写法
            body = str(quoted.get("content") or "").strip()
            if not body:
                return [], {"mode": "quoted-unresolved", "dropped": len(rows)}
        return rows, {"mode": "recent", "dropped": 0}

    # 适配器改前对「只有 @ 段」的消息返回的就是这个 `{}`
    old_quoted_from_adapter = {}
    picked_old, info_old = old_select_history(old_quoted_from_adapter)
    print("\n============ 改前 / 改后 对照（老板日志那条输入）============")
    print("[改前复刻] 适配器返回 {} → mode=%s 注入 %d 条 / 丢弃 %d 条"
          % (info_old["mode"], len(picked_old), info_old["dropped"]))
    print("[改前复刻] 与生产日志「引用未解析 → 不带上文（丢弃 20 条）」完全一致")

    # 改后：适配器返回 None，brain 归一成 None
    picked_new, info_new = pw.select_history(HISTORY, now=NOW, minutes=5,
                                             quoted=_norm(pw, None))
    print("[改后]     适配器返回 None → mode=%s 注入 %d 条 / 丢弃 %d 条"
          % (info_new["mode"], len(picked_new), info_new["dropped"]))
    assert info_old["mode"] == "quoted-unresolved" and info_old["dropped"] == 20, \
        "这条对照必须能复现改前的「丢光 20 条」"
    assert info_new["mode"] == "recent" and len(picked_new) == 20
    print("结论：改前 失败（丢光 20 条）/ 改后 通过（20 条全带上）")


def test_before_after_contrast(pw, capsys):
    """同一批输入两套判定对照：**改前**判成引用未解析、**改后**走正常窗口。"""
    print("\n================ 改前 / 改后 对照 ================")
    new_unresolved = 0
    for label, value in (("只有 @ 段 + 正文", {}), ("纯正文（无 @ 无引用）", {}),
                         ("适配器老写法给的空字典", {})):
        _n, picked_new, info_new, _m = decide(pw, value)
        if info_new["mode"] == "quoted-unresolved":
            new_unresolved += 1
        print("  %-22s 改后: mode=%-10s 注入 %2d 条 / 丢弃 %d 条"
              % (label, info_new["mode"], len(picked_new), info_new["dropped"]))
    print("  改被判成「引用未解析、丢光上文」的输入 = 0/%d 条（0.2.39 时是全部）" % 3)
    print("  改后 = %d/%d 条" % (new_unresolved, 3))
    assert new_unresolved == 0
    print("  结论：改后「只有 @ 段 / 纯正文 / 空字典」都不再丢上下文")


# ================================================================ 源码级回归锁
def test_source_lock_adapter_returns_none_without_reply_segment():
    """源码级锁：`_extract_quoted` 在**没有 reply 段**时必须 `return None`。"""
    src = (AI / "adapters" / "qq.py").read_text(encoding="utf-8")
    start = src.index("async def _extract_quoted")
    end = src.index("async def build_channel_from_event")
    body = src[start:end]
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "return None" in code, "没有引用段时必须返回 None（不能返回 {}）"
    # 2026-10-11 晚 **更新陈旧断言**（不是回归）：引用段的识别搬进了
    # `group_policy.find_reply_segment`（一份解析，对象段/dict 段/message_id/CQ 兜底都认），
    # 所以这里不再有字面量 `seg.type == "reply"`。锁改成锁"仍然只认 reply 段"
    # ——由 `find_reply_segment` 内部那行 `if t != "reply": continue` 保证。
    assert "find_reply_segment(" in code, "引用判定要走 group_policy.find_reply_segment"
    gp_src = (AI / "group_policy.py").read_text(encoding="utf-8")
    assert 'if t != "reply":' in gp_src, "引用判定必须只认 OneBot 的 reply 段"
    assert "quoted = await _extract_quoted" in src, "适配器没把引用判定接进 ChannelMessage"


def test_source_lock_prompt_window_uses_truthiness():
    """源码级锁：`select_history` / `render_history` 里不许再有 `is not None` 判引用。"""
    src = (AI / "prompt_window.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "if quoted is not None:" not in code, \
        "又用回了 is not None 判引用 —— 空字典会被当成有引用段（这次回归的根因）"
    assert "if quoted:" in code, "引用判定要按真值"
    assert "def normalize_quoted(" in code, "缺少 brain 侧的引用归一函数"


def test_source_lock_brain_logs_quoted_state():
    """源码级锁：引用判定必须写明确日志（老板要求：看得出为什么丢上下文）。"""
    src = (AI / "brain.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "normalize_quoted(" in code, "brain 没有归一引用段"
    assert "_quoted_line(" in code, "brain 没有打「引用段=…」日志"
    assert "引用段=无" in src and "正常窗口" in src, \
        "日志文案里要写明「引用段=无 → 正常窗口」"


def test_hint_injected_by_brain_not_adapter(pw):
    """2026-10-12 收口锁：取不到正文的提醒**由 brain 层注入**，适配器 content 保持空串。

    当天来回翻转过一次：把提醒塞进适配器 `content` 会被下游当成"解析成功"
    （还会把最近 3 条历史当"引用后续"带进上下文）—— 测试③锁住了 content 必须为空，
    这里再锁"提醒只从 brain 层来"，防止第三次翻转。
    """
    qq_src = (AI / "adapters" / "qq.py").read_text(encoding="utf-8")
    # 断言范围只收在 `_extract_quoted` 的函数体里：qq.py 的**图片识别失败**提示
    # （"[他发来一张图片]（识别失败）……如实说一句看不清"）也用了「如实说」三个字，
    # 但它跟引用无关 —— 2026-10-12 首次加这条锁时，整文件断言就是这么被误伤的。
    _start = qq_src.index("async def _extract_quoted")
    _end = qq_src.find("\nasync def ", _start + 1)
    quoted_body = qq_src[_start:_end if _end != -1 else len(qq_src)]
    assert "如实说" not in quoted_body, "提醒文案不许塞进适配器 content（契约：取不到正文 = 空串）"
    # 双保险：提醒原文的指纹（QUOTED_UNRESOLVED_HINT 里那句）整个适配器里都不许出现
    assert "能再说一次吗" not in qq_src, "提醒原文只许 prompt_window 持有，不许进适配器"
    # 提醒常量在 prompt_window，brain 在 quoted_state == unresolved 时注入
    assert isinstance(pw.QUOTED_UNRESOLVED_HINT, str) \
        and "看不到" in pw.QUOTED_UNRESOLVED_HINT, "缺少提示常量"
    brain_src = (AI / "brain.py").read_text(encoding="utf-8")
    assert "QUOTED_UNRESOLVED_HINT" in brain_src, "brain 没有注入提醒"
    assert '"unresolved"' in brain_src, "brain 没有按 quoted_state 判注入"
    # 行为口径：unresolved 时 select_history 依然"不带上文"（提醒只由 brain 加，不进 picked）
    quoted = {"content": "", "reason": "取不到", "message_id": "9"}
    picked, info = pw.select_history(HISTORY, now=NOW, minutes=5, quoted=quoted)
    assert info["quoted_state"] == "unresolved" and picked == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-s"]))
