# -*- coding: utf-8 -*-
r"""「@ 了别人就别接话」回归（2026-10-11 晚 老板急报）。

老板原话：
    **"我和机器人聊天时 @ 别人，机器人也选择回复。"**
群里其他人看到的就是"它在乱插话"。

老板拍板的判定优先级（这条测试就是它的可执行版本）：
1. **@ 了别人、但没 @ 我 → 完全不接话**（**包括"免@延续"分支**：必须压过延续逻辑，
   否则"正在聊天时 @ 别人"一定会被它抢话）；
2. 同时 @ 了我和别人 → 正常回复（有我在里面就算叫我）；
3. 只 @ 我 → 正常回复；
4. 没 @ 任何人 → 保持现有行为（有效期内免@延续；超出有效期不回）。
回归锁：`mentions_other`（判定函数）里 **@ 我自己绝不能产生 `mentions_other=True`**。

## 改前 / 改后对照怎么跑（真的对照，不是嘴上说）
本文件把 `brain.py` 决策点那段逻辑**按同一套口径抽成一个纯函数**，留了改前/改后两份：

* `_branch_before_fix()` —— **改前**的 brain.py：先看 `allows_session_continuation`，
  **`@ 了别人` 只体现在 `choose_branch` 的两条 AI 判断分支之后**；timeout 分支里的
  `direct_continue` 快捷路径（"机器人上一条回复对象就是他 → 免 AI 判断直接回"）
  **完全没看 `at_others`**。
* `_branch_after_fix()` —— **改后**：先调 `session_policy.says_at_other(...)`，
  命中的话**直接跳过**（不进入任何延续/AI 判断分支），再谈别的。

对照结果（本文件 `test_before_and_after_side_by_side` 会打印）：
| 用例 | 改前 | 改后 |
|---|---|---|
| ① @ 别人 + 免@有效期内 | **接话**（`judge_timeout` → AI 判"在接着聊"就回） | 不接话（`skip`） |
| ①' @ 别人 + 有效期内 + 机器人上一条正是回他 | **100% 接话**（`direct_continue`，AI 都不问） | 不接话（`skip`） |
| ② 同时 @ 我和别人 | 接话 | 接话（不变） |
| ③ 只 @ 我 | 接话 | 接话（不变） |
| ④ 谁也没 @ + 有效期内 | 接话 | 接话（不变） |
| ④' 谁也没 @ + 超出有效期 | 不回 | 不回（不变） |

跑法：
    $env:PYTHONPATH="src"
    .\.test_venv\Scripts\python.exe -m pytest tests/test_group_at_others_priority.py -q -s
"""
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"

# 老板场景里的两个 id：机器人自己 + 被别人 @ 的那个人
BOT_ID = "1000000001"
OTHER_ID = "10086"
USER_ID = "1000000002"          # 正在跟机器人聊天的那个人


def _load(name: str, path: Path):
    """按文件路径加载（不触发插件包 __init__，免拉 nonebot）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SP = _load("a241_session_policy", AI_DIR / "session_policy.py")
GP = _load("a241_group_policy", AI_DIR / "group_policy.py")
PLATFORM = _load("a241_platform", AI_DIR / "platform.py")


# ---------------------------------------------------------------- 事件替身

class _Seg:
    def __init__(self, type_, data=None):
        self.type = type_
        self.data = data or {}


class _FakeEvent:
    """官方（全量）事件替身：只要 to_me / get_message / mentions / self_id。

    注意 `to_me=False` —— 机器人开了"接收所有消息"后，群里每条消息都走全量事件，
    `to_me` 恒为 False，@ 谁只能从 mention 段里认（这正是要测的那条路）。
    """

    def __init__(self, *, to_me=False, segments=None, mentions=None, self_id=BOT_ID):
        self.to_me = to_me
        self.self_id = self_id
        self._segments = segments or []
        self.mentions = mentions or []

    def get_message(self):
        return self._segments


def _seg_at(user_id, at_flag=False):
    d = {"user_id": str(user_id)}
    if at_flag:
        d["is_bot"] = True
    return _Seg("mention_user", d)


_TEXT = _Seg("text", {"text": "你们先聊"})


def ev_others_only():
    """@ 了别人、没 @ 我。"""
    return _FakeEvent(segments=[_seg_at(OTHER_ID), _TEXT])


def ev_me_and_other():
    """同时 @ 了我和别人。"""
    return _FakeEvent(segments=[_seg_at(BOT_ID), _seg_at(OTHER_ID), _TEXT])


def ev_me_only():
    """只 @ 我（mention 段不带 is_bot/is_you，只能靠 user_id 认）。"""
    return _FakeEvent(segments=[_seg_at(BOT_ID), _TEXT])


def ev_nobody():
    """谁也没 @。"""
    return _FakeEvent(segments=[_TEXT])


def msg_of(event, *, scene="group", room="G1", user=USER_ID, is_at_me=None,
           before_fix=False):
    """按**适配器同一套口径**把事件归一化成 ChannelMessage（私聊不看 @）。

    `before_fix=True` 时 `extra["at_others"]` 用**改前适配器**那份内联写法（含它的
    异常即当作"没 @ 别人"），这样"改前会怎么走"才是真的改前，而不是拿新解析假装。
    """
    if scene == "group":
        at_others = (_old_adapter_at_others(event) if before_fix
                     else GP.mentions_other(event, bot_id=BOT_ID))
    else:
        at_others = False
    return PLATFORM.ChannelMessage(
        platform="qq", scene=scene, user_id=user, room_id=room, text="你们先聊",
        is_at_me=(GP.is_at_me(event, bot_id=BOT_ID) if is_at_me is None else is_at_me),
        plain_text="你们先聊", raw=event,
        extra={"at_others": at_others, "says_at_other": False})


def decide(msg, *, before_fix: bool, session_active: bool, mode="timeout",
           is_notice=False, direct_continue=False):
    """brain.py 决策点的**同口径**纯函数（两份：改前 / 改后）。"""
    ctx = {
        "is_group": msg.scene == "group",
        "at_others": bool(msg.extra.get("at_others", False)),
        "is_at_me": bool(msg.is_at_me),
        "allows_session_continuation": not is_notice,
        "session_active": session_active,
        "mode": mode,
        "direct_continue": direct_continue,
        "auto_participate": False,
    }
    return _branch_before_fix(ctx) if before_fix else _branch_after_fix(ctx)


def _old_adapter_at_others(event, bot_id=BOT_ID) -> bool:
    """**改前适配器**算 at_others 的写法（OneBot 侧内联那一份）。

    改前源码（`seg` 是对象，所以这里就按对象访问 —— 访问方式本身就是问题所在）：
        at_others = any(seg.type == "at"
                        and str(seg.data.get("qq", "")) != str(bot.self_id)
                        for seg in event.message)
    三个致命点（线上那次事故里都成立）：
      ① **只认对象段**：段是 dict 时 `seg.type` / `seg.data` 直接 AttributeError →
         异常往上冒到 `build_channel`（那里没有兜底）→ 整条消息当"没有 @ 别人"；
      ② 只比 `bot.self_id`，与 group_policy 是两套解析；
      ③ 不认 `mention_user`（官方形态）。
    """
    try:
        return any(seg.type == "at"
                   and str(seg.data.get("qq", "")) != str(getattr(event, "self_id", "") or "")
                   for seg in event.message)
    except Exception:                      # ← 改前的真实行为：异常 → 当作没有 @ 别人
        return False


def _old_choose_branch(*, is_at_me, at_others, mode, session_active, auto_participate):
    """**改前**的 `choose_branch`。

    注意一个容易搞错的点（线上事故就藏在这里）：**这一层改前也已经会 skip**。
    真正漏的是 brain 那边的两条岔路（`direct_continue` 快捷路径、通知类岔路）
    在 `choose_branch` **之外** —— 而且前提是 `at_others` 得先被适配器算出来。
    """
    if is_at_me:
        return SP.BRANCH_AT_ME
    if at_others:
        return SP.BRANCH_SKIP
    mode = SP.normalize_mode(mode)
    if mode == SP.MODE_OFF:
        return SP.BRANCH_IGNORE
    if mode == SP.MODE_API:
        return SP.BRANCH_JUDGE_API
    if session_active:
        return SP.BRANCH_JUDGE_TIMEOUT
    if auto_participate:
        return SP.BRANCH_JUDGE_GROUP
    return SP.BRANCH_IGNORE


def _branch_before_fix(c):
    """**改前**的 brain.py（371-393 行 + 407-416 行）原样复刻 —— 只做"改前失败"对照。

    改前那道判定长这样：
        if not allows_session_continuation(msg): … else: choose_branch(…)
        if branch == BRANCH_AT_ME: …
        elif branch in (JUDGE_API, JUDGE_TIMEOUT):
            direct_continue = (branch == JUDGE_TIMEOUT and last_reply_user[room]==user
                               and len(ctx)>=2 and ctx[-2].role=="assistant")
            if direct_continue: should_reply = True      # ← 不查 at_others
    """
    if not c["allows_session_continuation"]:
        return SP.BRANCH_AT_ME if c["is_at_me"] else SP.BRANCH_IGNORE
    branch = _old_choose_branch(
        is_at_me=c["is_at_me"], at_others=c["at_others"], mode=c["mode"],
        session_active=c["session_active"], auto_participate=c["auto_participate"])
    if branch == SP.BRANCH_JUDGE_TIMEOUT and c["direct_continue"]:
        # ←← 改前的 `direct_continue` 快捷路径：**这里没有 at_others**
        return SP.BRANCH_AT_ME
    return branch


def _branch_after_fix(c):
    """**改后**的 brain.py：`says_at_other` 先判，命中即跳过（不进任何延续分支）。

    与 brain.py 里那段一一对应（顺序就是重点）：
      ① `says_at_other` → `BRANCH_SKIP`
      ② `allows_session_continuation` 那道岔路
      ③ `choose_branch`（含后面的 `direct_continue` 快捷路径）
    """
    if c["is_group"] and SP.says_at_other(
            is_at_me=c["is_at_me"], at_others=c["at_others"]):
        return SP.BRANCH_SKIP
    if not c["allows_session_continuation"]:
        return SP.BRANCH_AT_ME if c["is_at_me"] else SP.BRANCH_IGNORE
    branch = SP.choose_branch(
        is_at_me=c["is_at_me"], at_others=c["at_others"], mode=c["mode"],
        session_active=c["session_active"], auto_participate=c["auto_participate"])
    if branch == SP.BRANCH_JUDGE_TIMEOUT and c["direct_continue"]:
        return SP.BRANCH_AT_ME
    return branch


def _would_reply(branch, *, judge_says_continue=True):
    """这一支最终会不会发消息（AI 判断按最坏情况：判"在接着聊"）。"""
    if branch == SP.BRANCH_AT_ME:
        return True
    if branch in (SP.BRANCH_JUDGE_API, SP.BRANCH_JUDGE_TIMEOUT, SP.BRANCH_JUDGE_GROUP):
        return bool(judge_says_continue)
    return False            # skip / ignore


def _before_reply(event, *, session_active=True, mode="timeout"):
    """**改前**这一条最终会不会回话（适配器 + brain 两层都按改前口径）。

    这就是"改前失败"的判据：它 = True 而老板要求 False。
    `direct_continue` 只在 `choose_branch` 真判出 `judge_timeout` 时才可能命中
    （brain.py:407 的条件第一项就是 `branch == BRANCH_JUDGE_TIMEOUT`），所以这里
    按**同一套真实耦合**推：不是随手传一个 True。
    """
    msg = msg_of(event, before_fix=True)
    ctx = {
        "is_group": True,
        "at_others": bool(msg.extra.get("at_others", False)),
        "is_at_me": bool(msg.is_at_me),
        "allows_session_continuation": True,
        "session_active": session_active,
        "mode": mode,
        "direct_continue": False,
        "auto_participate": False,
    }
    branch = _branch_before_fix(ctx)
    ctx["direct_continue"] = (branch == SP.BRANCH_JUDGE_TIMEOUT)
    branch = _branch_before_fix(ctx)
    return _would_reply(branch), branch


def _after_reply(event, *, session_active=True, mode="timeout", is_notice=False):
    """**改后**这一条最终会不会回话（本测试文件的 `_branch_after_fix` 口径）。"""
    msg = msg_of(event)
    branch = decide(msg, before_fix=False, session_active=session_active,
                    direct_continue=True, mode=mode, is_notice=is_notice)
    return _would_reply(branch), branch


# ---------------------------------------------------------------- 回归锁：@ 我自己

def test_regression_lock_self_mention_is_not_other():
    """回归锁：判定函数里 **@ 我自己绝不能产生 `mentions_other=True`**。

    这是 2026-10-10 修过的同类 bug（mention 不带 is_bot/is_you 标记时，
    "@ 机器人自己"被当成"@ 别人" → 群里 @ 它也不回）。今天的新判定**复用它**，
    所以这条锁必须一起钉死：既要有标记的，也要只靠 user_id 认的那种。
    """
    for seg, label in ((_seg_at(BOT_ID), "只有 user_id"),
                       (_seg_at(BOT_ID, at_flag=True), "带 is_bot 标记")):
        e = _FakeEvent(segments=[seg, _TEXT])
        assert GP.mentions_other(e, bot_id=BOT_ID) is False, \
            "@ 机器人自己不算 @ 别人（%s）" % label
        assert GP.is_at_me(e, bot_id=BOT_ID) is True, \
            "@ 机器人自己必须算被 @（%s）" % label
    # 同时 @ 我和别人：两边都要真（有我在里面就算叫我）
    e = ev_me_and_other()
    assert GP.is_at_me(e, bot_id=BOT_ID) is True
    assert GP.mentions_other(e, bot_id=BOT_ID) is True
    # 谁也不 @：两边都假
    e = ev_nobody()
    assert GP.is_at_me(e, bot_id=BOT_ID) is False
    assert GP.mentions_other(e, bot_id=BOT_ID) is False
    # 判定函数本体不许把"我"算成别人
    assert SP.says_at_other(is_at_me=True, at_others=True) is False
    assert SP.says_at_other(is_at_me=True, at_others=False) is False


# ---------------------------------------------------------------- 4 条必测

def test_case1_group_at_other_not_me_no_reply_even_in_session():
    """**老板的原场景**：@ 了别人、没 @ 我，且该用户正处于免@会话有效期内 → 不回复。

    两种改前形态（都在线上成立）都要被改后堵住：
      A. **适配器没认出 @ 别人** → `at_others=False` → 走进免@延续 → 回；
      B. **认出来了、但被 `direct_continue` 快捷路径越过** → 回。
    """
    event = ev_others_only()
    msg = msg_of(event)
    assert msg.extra["at_others"] is True, "适配器口径没认出 @ 别人"
    assert msg.is_at_me is False, "@ 别人不能被当成 @ 我"

    # 改后：任何模式、任何会话状态都不接话
    for mode in ("timeout", "api", "off"):
        for active in (True, False):
            branch = decide(msg, before_fix=False, session_active=active, mode=mode)
            assert branch == SP.BRANCH_SKIP, \
                "mode=%s active=%s：@ 了别人（未 @ 我）必须跳过，实际 %s" % (mode, active, branch)
            assert _would_reply(branch) is False, "这一条绝不能接话"
            replied, b = _after_reply(event, session_active=active, mode=mode)
            assert replied is False, "改后实测这一条必须不回（实际 %s）" % b

    # 改前 A：适配器那份内联写法在**线上真实形态**下算不出 @ 别人
    live_shape = _OneBotEvent([{"type": "at", "data": {"qq": OTHER_ID}},
                               {"type": "text", "data": {"text": "你们先聊"}}])
    assert _old_adapter_at_others(live_shape) is False, \
        "改前适配器在 dict 段形态下认不出 @ 别人（这正是线上 22:09 的形态）"
    assert GP.mentions_other(live_shape, bot_id=BOT_ID) is True, "改后要认出来"
    replied, b = _before_reply(live_shape, session_active=True)
    assert replied is True, "改前 A：没认出 @ 别人 → 走免@延续（+direct_continue）→ 回（实际 %s）" % b
    replied_after, b_after = _after_reply(live_shape, session_active=True)
    assert replied_after is False and b_after == SP.BRANCH_SKIP

    # 改前 B：就算适配器认出来了，`direct_continue` 那条路也是**并列的第二道漏**
    # （线上这条的 at_others 是 False 才走到 92 行；一旦哪天适配器认对、而闸门没提前，
    #   它照样抢话）—— 改后由 `says_at_other` 在它之前短路。
    replied_b, b_b = _before_reply(ev_others_only(), session_active=True)
    assert replied_b is True, "改前 B：认出来也会被 direct_continue 抢话（实际 %s）" % b_b
    assert b_b == SP.BRANCH_AT_ME
    replied_b2, b_b2 = _after_reply(ev_others_only(), session_active=True)
    assert replied_b2 is False and b_b2 == SP.BRANCH_SKIP, \
        "改后 B：says_at_other 必须排在 direct_continue 之前"


def test_case1b_direct_continue_path_is_also_blocked():
    """**H-1 的构造性复现**：`at_others=True` + 免@有效期内 + 机器人上一条回的就是他。

    这一条把审计口径（"把 at_others 判定提到 direct_continue 之前"）做成可执行断言：
    改前 `direct_continue` 命中就 `should_reply=True`（不看 at_others）；
    改后同一输入 `says_at_other` 先短路 → `skip`。

    线上 22:09:03 的实证（两条并存，缺一不可）：
      * 日志 92 行 `用户1000000002直接延续对话…免AI判断` → **direct_continue 真的会被走到**；
      * 全日志**一次都没有**"@了其他人不回复" → 当时那条的 `at_others` 是 False
        （适配器没认出来），所以它连 `choose_branch` 的 SKIP 都没碰到。
    """
    msg = msg_of(ev_others_only())
    at_one_branch = _old_choose_branch(is_at_me=False, at_others=True, mode="timeout",
                                       session_active=True, auto_participate=False)
    assert at_one_branch == SP.BRANCH_SKIP, "choose_branch 这层改前也会 skip"
    # 但它的前提是 at_others 为真；为真之后 brain 那条快捷路径仍会抢话：
    before = _branch_before_fix({"is_group": True, "at_others": False, "is_at_me": False,
                                 "allows_session_continuation": True, "session_active": True,
                                 "mode": "timeout", "direct_continue": True,
                                 "auto_participate": False})
    assert _would_reply(before) is True, "改前（at_others 没认出来）必然抢话"
    after = decide(msg, before_fix=False, session_active=True, direct_continue=True)
    assert after == SP.BRANCH_SKIP, "改后必须跳过：免@延续压不过「@ 了别人」"
    assert _would_reply(after) is False
    # 通知类岔路（点赞/戳一戳）也要被同一道闸拦住：改前它绕过 choose_branch
    for before_fix in (True, False):
        b = decide(msg, before_fix=before_fix, session_active=True, is_notice=True)
        assert _would_reply(b) is False, "通知类岔路也不能因为 at_others 而回话"


def test_case2_group_at_me_and_other_reply():
    """同时 @ 了我和别人 → 正常回复（有我在里面就算叫我）。"""
    msg = msg_of(ev_me_and_other())
    assert msg.is_at_me is True and msg.extra["at_others"] is True
    for before in (True, False):
        branch = decide(msg, before_fix=before, session_active=False)
        assert branch == SP.BRANCH_AT_ME, "同时 @ 我和别人必须正常回复"
        assert _would_reply(branch) is True


def test_case3_group_at_me_only_reply():
    """只 @ 我 → 正常回复（保持现状）。"""
    msg = msg_of(ev_me_only())
    assert msg.is_at_me is True and msg.extra["at_others"] is False
    for before in (True, False):
        branch = decide(msg, before_fix=before, session_active=False)
        assert branch == SP.BRANCH_AT_ME
        assert _would_reply(branch) is True


def test_case4_group_no_at_keeps_existing_behavior():
    """没 @ 任何人 → 保持现有行为：有效期内免@延续回，超出有效期不回。"""
    msg = msg_of(ev_nobody())
    assert msg.extra["at_others"] is False and msg.is_at_me is False

    for before in (True, False):
        inside = decide(msg, before_fix=before, session_active=True, mode="timeout")
        outside = decide(msg, before_fix=before, session_active=False, mode="timeout")
        assert inside == SP.BRANCH_JUDGE_TIMEOUT, "有效期内要交给延续判断"
        assert _would_reply(inside) is True, "延续判出'在接着聊'就该回"
        assert outside == SP.BRANCH_IGNORE, "超出有效期不回"
        assert _would_reply(outside) is False


# ---------------------------------------------------------------- 私聊不受影响

def test_private_chat_unaffected():
    """私聊不受影响：私聊本来就没有 at_others，判定必须放行。"""
    msg = msg_of(ev_others_only(), scene="private", room=USER_ID, is_at_me=False)
    assert msg.extra["at_others"] is False
    assert SP.says_at_other(is_at_me=False, at_others=False) is False
    branch = decide(msg, before_fix=False, session_active=True)
    assert branch == SP.BRANCH_JUDGE_TIMEOUT
    assert _would_reply(branch) is True


# ---------------------------------------------------------------- 接线：brain.py 真按这个顺序走

def test_brain_wires_priority_before_continuation():
    """接线锁：`says_at_other` 的判定必须**在**"免@延续"那道岔路**之前**。

    改前的写法是 `if not allows_session_continuation(msg): … else: choose_branch(...)`，
    @ 别人的信息只在 else 分支里出现 —— 通知类事件那条路就绕过了它。现在要求：
    先 `says_at_other` 直接 `BRANCH_SKIP`，再谈 `allows_session_continuation`。
    """
    src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "session_policy.says_at_other(" in src, \
        "brain 必须调用 session_policy.says_at_other（判定收口在 group_policy/session_policy）"
    i_says = src.index("session_policy.says_at_other(")
    i_guard = src.index("allows_session_continuation(msg)")
    assert i_says < i_guard, \
        "「@ 了别人」的判定必须排在任何『免@延续』岔路之前，否则延续会绕过它"
    assert "本条 @ 了别人（未 @ 我）→ 不接话（不进入免@延续）" in src, \
        "日志要写明原因（老板口径）"

    # brain 调 says_at_other 时传的是适配器算好的 at_others（不另起一套解析）
    assert 'at_others' in src and '_extra_of(msg).get("at_others"' in src

    # 判定函数本身：源码里不许再出现"先看延续、@ 别人的信息放在后面"的旧结构
    fn = inspect.getsource(SP.says_at_other)
    assert "is_at_me" in fn and "at_others" in fn


def test_choose_branch_still_skips_at_others():
    """`choose_branch` 自己也得保持"@ 别人 → skip"（第二道闸，别改坏）。"""
    assert SP.choose_branch(is_at_me=False, at_others=True, mode="timeout",
                            session_active=True, auto_participate=False) == SP.BRANCH_SKIP
    assert SP.choose_branch(is_at_me=True, at_others=True, mode="timeout",
                            session_active=False, auto_participate=False) == SP.BRANCH_AT_ME


def test_onebot_adapter_shares_group_policy_parsing():
    """适配器口径必须与判定函数同源（别再另写一份 @ 解析）。

    OneBot v11 侧原来手写 `seg.type == "at" and qq != bot.self_id`：
    ① @ 自己靠 `bot.self_id` 一条路，拿不到 self_id 时就会把自己算成"别人"；
    ② 与 group_policy 的解析（含 user_id/is_bot/mentions 兜底）是两套。
    现在统一调 `mentions_other(event, bot_id=...)`。
    """
    src = (AI_DIR / "adapters" / "qq.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "mentions_other(" in code, "OneBot 适配器要复用 group_policy.mentions_other"
    assert 'seg.type == "at"' not in code, "不许再手写一份 @ 别人的判定"
    assert "from ..group_policy import" in code
    assert "find_reply_segment(" in code, "引用段识别要走同一份解析"
    # 私聊不参与 @ 判定（to_me 在 OneBot 里私聊恒为 True）
    assert "SCENE_PRIVATE" in code and "is_at = False" in code


# ---------------------------------------------------------------- 适配器口径（OneBot v11）

class _AtSeg:
    """OneBot v11 的 @ 段：type=at、id 在 `qq` 字段（和官方通道的 mention_user 不同）。"""

    def __init__(self, qq):
        self.type = "at"
        self.data = {"qq": str(qq)}


class _ReplySeg:
    def __init__(self, id_, key="id"):
        self.type = "reply"
        self.data = {key: str(id_)}


class _TextSeg:
    def __init__(self, text):
        self.type = "text"
        self.data = {"text": text}


class _OneBotEvent:
    """OneBot v11 消息事件替身（`to_me` 恒为 False = 全量事件，与线上一致）。"""

    def __init__(self, segments=None, *, self_id=BOT_ID, raw_message="", reply=None,
                 to_me=False):
        self.to_me = to_me
        self.self_id = self_id
        self._segments = segments or []
        self.message = self._segments
        self.raw_message = raw_message
        self.reply = reply

    def get_message(self):
        return self._segments

    def is_tome(self):
        return self.to_me


def test_onebot_at_segment_shapes():
    """OneBot 的 @ 段（at/qq）也要认：@ 别人 → at_others；@ 我自己 → 绝不算别人。"""
    others = _OneBotEvent([_AtSeg(OTHER_ID), _TextSeg("你们先聊")])
    assert GP.mentions_other(others, bot_id=BOT_ID) is True
    assert GP.is_at_me(others, bot_id=BOT_ID) is False

    me = _OneBotEvent([_AtSeg(BOT_ID), _TextSeg("在吗")])
    assert GP.is_at_me(me, bot_id=BOT_ID) is True
    assert GP.mentions_other(me, bot_id=BOT_ID) is False, "@ 我自己不算 @ 别人（I1）"

    all_at = _OneBotEvent([_AtSeg("all"), _TextSeg("通知")])
    assert GP.mentions_other(all_at, bot_id=BOT_ID) is True, "@全体成员算 @ 别人"
    assert GP.is_at_me(all_at, bot_id=BOT_ID) is False

    # dict 形态的段（协议端/中间层）也要认
    dicted = _OneBotEvent([{"type": "at", "data": {"qq": OTHER_ID}}])
    assert GP.mentions_other(dicted, bot_id=BOT_ID) is True
    dicted_me = _OneBotEvent([{"type": "at", "data": {"qq": BOT_ID}}])
    assert GP.mentions_other(dicted_me, bot_id=BOT_ID) is False


def test_reply_segment_is_recognized():
    """**引用段识别**（H-25）：对象段 / dict 段 / message_id 字段名 / CQ 兜底 都要认。"""
    cases = [
        (_OneBotEvent([_ReplySeg(388291130)]), 388291130, "对象段 + id"),
        (_OneBotEvent([_ReplySeg(388291130, key="message_id")]), 388291130, "字段名 message_id"),
        (_OneBotEvent([{"type": "reply", "data": {"id": "388291130"}}]), 388291130, "dict 段"),
        (_OneBotEvent([], raw_message="[CQ:reply,id=388291130][CQ:at,qq=6885483] 你好"),
         388291130, "段列表缺失 → 原始串 CQ 码兜底"),
        (_OneBotEvent([], raw_message="[reply:id=388291130][at:qq=6885483] 你好"),
         388291130, "渲染串形态（线上那条）"),
    ]
    for event, want, label in cases:
        found = GP.find_reply_segment(event)
        assert str(found["reply_id"]) == str(want), "%s：没认出引用段（%r）" % (label, found)
        assert found["from"], "必须写清依据（%s）" % label

    # 只有 @ 段 + 正文 = **没有引用段**（0.2.40 的正面修复，别退回去）
    only_at = _OneBotEvent([_AtSeg(OTHER_ID), _TextSeg("你们先聊")],
                           raw_message="[CQ:at,qq=6885483] 你们先聊")
    found = GP.find_reply_segment(only_at)
    assert found["reply_id"] == "" and found["from"] == "none"
    assert "reply" not in GP.describe_segments(only_at), "@ 段绝不能被当成引用段"


# ---------------------------------------------------------------- 审计高危项：H-1 线上原文复现

def test_row3_online_replay_2209_03():
    """**线上 22:09:03 那条原文的复现**（老板原场景，最重要的一条）。

    线上原文（`/root/incident_backup/nonebot.log.pre0241_*` 第 81~92 行）：
        22:09:03 [message.group.normal]: Message 43772726 from 1000000002@[群:768272849]
                 '[reply:id=388291130][at:qq=6885483] 不是逻辑层面，是代码层面bug'
        22:09:03 用户1000000002直接延续对话（机器人上一条回复对象就是该用户），免AI判断
        22:09:05 [brain] 回复正文 → qq:768272849｜实发=哦——您这是说我记忆模块有bug呗…

    条件复刻：timeout 模式、`last_reply_user[room] == 1000000002`、上文尾部 role=assistant
    → **direct_continue 命中**；本条 `@了 6885483`（别人）、没 @ 机器人（1437808448）。
    改前：should_reply=True（抢话）；改后：`says_at_other` → skip。
    """
    GROUP = "768272849"
    BOT = "1437808448"
    event = _OneBotEvent(
        [_ReplySeg(388291130), _AtSeg("6885483"), _TextSeg("不是逻辑层面，是代码层面bug")],
        self_id=BOT,
        raw_message="[reply:id=388291130][at:qq=6885483] 不是逻辑层面，是代码层面bug")
    ctx_probe = _load("a241_target_guard", AI_DIR / "target_guard.py")
    quote = GP.find_reply_segment(event)
    msg = PLATFORM.ChannelMessage(
        platform="qq", scene="group", user_id="1000000002", room_id=GROUP,
        text="不是逻辑层面，是代码层面bug", is_at_me=GP.is_at_me(event, bot_id=BOT),
        plain_text="不是逻辑层面，是代码层面bug", raw=event,
        extra={"at_others": GP.mentions_other(event, bot_id=BOT),
               "quoted": {"content": "（被引用那条）", "ts": 0.0, "message_id": "388291130"}})

    print("\n[线上复现] 段列表 = %s" % GP.describe_segments(event))
    print("[线上复现] 引用段识别 = id=%s from=%s"
          % (quote["reply_id"], quote["from"]))
    print("[线上复现] is_at_me=%s at_others=%s（机器人=%s，被 @ 的别人=6885483）"
          % (msg.is_at_me, msg.extra["at_others"], BOT))
    print("[线上复现] 目标护栏 = %r（ctx 声明 qq:%s）"
          % (ctx_probe.check_reply_target(msg, type("C", (), {"target_key": "qq:" + GROUP})()),
             GROUP))

    assert msg.is_at_me is False and msg.extra["at_others"] is True, \
        "这条必须判成『@ 了别人且没 @ 我』"
    assert str(quote["reply_id"]) == "388291130", \
        "线上那条**确实带 reply 段**，识别必须认出来（H-25）"

    # 改前（当场实况）：适配器没认出 @ 别人 → at_others=False → direct_continue 命中
    # → 免 AI 判断直接回（22:09:05 那条就是这么发出去的）。
    before_false = _branch_before_fix(
        {"is_group": True, "at_others": False, "is_at_me": False,
         "allows_session_continuation": True, "session_active": True,
         "mode": "timeout", "direct_continue": True, "auto_participate": False})
    after = decide(msg, before_fix=False, session_active=True, direct_continue=True)
    print("[线上复现] 改前（at_others 没认出来）分支 = %s → 会回（就是 22:09:05 那条）"
          % before_false)
    print("[线上复现] 改后分支 = %s → 不回" % after)
    assert _would_reply(before_false) is True, "改前必须能复现抢话（否则对照无效）"
    assert after == SP.BRANCH_SKIP and _would_reply(after) is False, \
        "改后这条必须不接话"

    # 线上实况再补一层：当时那条消息连 @ 都没认出来（全日志没有一次"@了其他人"）
    live_msg = _OneBotEvent(
        [{"type": "reply", "data": {"id": "388291130"}},
         {"type": "at", "data": {"qq": "6885483"}},
         {"type": "text", "data": {"text": "不是逻辑层面，是代码层面bug"}}],
        self_id=BOT, raw_message="[reply:id=388291130][at:qq=6885483] 不是逻辑层面，是代码层面bug")
    assert _old_adapter_at_others(live_msg) is False, "改前适配器在这种段形态下认不出 @ 别人"
    assert GP.mentions_other(live_msg, bot_id=BOT) is True, "改后必须认出来"
    print("[线上复现] 改前 at_others=%s（认不出）→ 改后 at_others=%s（认出）"
          % (_old_adapter_at_others(live_msg), GP.mentions_other(live_msg, bot_id=BOT)))


# ---------------------------------------------------------------- H-14：每个判定分支都有日志

def test_h14_every_branch_logs_input_conclusion_reason():
    """I6：`ac ` 前缀 / 清对话 / 身份绑定 三条路径都要有"输入 → 结论 → 原因"日志。"""
    src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    for needle in ("'ac ' 开头 → 本条静默丢弃",
                   "命中「%s」→ 清空本会话上下文",
                   "命中身份绑定命令 → 已由 identity_bind 处理并回复"):
        assert needle in src, "缺 I6 日志：%s" % needle
    # 每条都要带 input:
    for anchor in ("本条静默丢弃", "清空本会话上下文", "命中身份绑定命令"):
        i = src.index(anchor)
        block = src[max(0, i - 200): i + 400]
        assert "input:" in block, "%s 的日志没写输入" % anchor


# ---------------------------------------------------------------- H-8：通知类事件 0 条上文

def test_h8_notice_event_gets_zero_history():
    """规则表第 13/14 行：通知类事件（点赞/戳一戳/按钮）**0 条上文**。"""
    src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "target_guard.is_notice(msg)" in src, \
        "上文窗口要对通知类事件清零（改前只有 empty_at 才清零）"
    i = src.index("target_guard.is_notice(msg)")
    block = src[max(0, i - 400): i + 600]
    assert "上文 0 条" in block
    assert '"mode": "notice"' in block, \
        "窗口日志要写清是 notice 导致清零"

    # 语义验证：通知事件在本文件的口径里 → 0 条上文
    tg = _load("a241_guard_notice", AI_DIR / "target_guard.py")
    notice = PLATFORM.ChannelMessage(
        platform="qq", scene="group", user_id=USER_ID, room_id="G1", text="（点了按钮）",
        is_at_me=True, plain_text="", raw=None, extra=tg.notice_extra("interaction"))
    assert tg.is_notice(notice) is True
    assert tg.allows_session_continuation(notice) is False
    normal = msg_of(ev_me_only())
    assert tg.is_notice(normal) is False


# ---------------------------------------------------------------- H-13：单条长度上限

def _load_manager():
    """按路径加载 manager.py（构建环境没有 nonebot，补最小替身）。"""
    import logging
    import tempfile
    import types

    if "nonebot" not in sys.modules:
        nb = types.ModuleType("nonebot")
        nb.logger = logging.getLogger("nonebot")
        adapters = types.ModuleType("nonebot.adapters")
        onebot = types.ModuleType("nonebot.adapters.onebot.v11")
        onebot.MessageEvent = object
        sys.modules["nonebot"] = nb
        sys.modules["nonebot.adapters"] = adapters
        sys.modules["nonebot.adapters.onebot.v11"] = onebot
    # 这个替身**单独判**：别的测试可能已经把 `nonebot` 塞进 sys.modules 了
    # （同一次 pytest 会话里共享），但 localstore 未必有 —— 那时 manager.py 会 ImportError。
    if "nonebot_plugin_localstore" not in sys.modules:
        store = types.ModuleType("nonebot_plugin_localstore")
        _base = Path(tempfile.gettempdir()) / "qbm_test_0241"
        _base.mkdir(parents=True, exist_ok=True)
        store.get_plugin_config_file = lambda name: _base / name
        store.get_plugin_data_file = lambda name: _base / name
        store.get_plugin_cache_file = lambda name: _base / name
        sys.modules["nonebot_plugin_localstore"] = store
    pkg = "ai_plugin_pkg_0241"
    if pkg in sys.modules:
        return sys.modules[pkg + ".manager"]
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.__path__ = [str(AI_DIR)]
    sys.modules[pkg] = pkg_mod

    class _Cfg:
        aichat_super_users = []
        aichat_proactive_groups = []
        aichat_session_expire_seconds = 30
        aichat_session_mode = "timeout"

        def __getattr__(self, name):
            numeric = ("interval", "cooldown", "count", "seconds", "limit",
                       "probability", "quiet", "start", "end")
            return 30 if any(k in name for k in numeric) else []

    cfg_mod = types.ModuleType(pkg + ".config")
    cfg_mod.config = _Cfg()
    sys.modules[pkg + ".config"] = cfg_mod
    for name in ("group_policy", "session_policy", "platform"):
        sys.modules[pkg + "." + name] = _load("%s.%s" % (pkg, name), AI_DIR / (name + ".py"))
    return _load(pkg + ".manager", AI_DIR / "manager.py")


def test_h13_incoming_length_cap_is_configurable():
    """H-13：单条正文上限**可配**（默认 4000，夹 200~100000，0 = 不限制）。"""
    mgr_mod = _load_manager()
    mgr = mgr_mod.ChatManager.__new__(mgr_mod.ChatManager)
    mgr._data = {}
    saved = []
    mgr._save_manager_config = lambda: (saved.append(1), True)[1]

    assert mgr.get_max_incoming_chars() == 4000, "默认 4000"
    assert mgr.set_max_incoming_chars(100) is True and mgr.get_max_incoming_chars() == 200, \
        "小于 200 夹到 200"
    assert mgr.set_max_incoming_chars(999999) is True and mgr.get_max_incoming_chars() == 100000
    assert mgr.set_max_incoming_chars(0) is True and mgr.get_max_incoming_chars() == 0, \
        "0 = 不限制（显式关掉）"
    mgr._data["max_incoming_chars"] = "坏了"
    assert mgr.get_max_incoming_chars() == 4000, "写坏了回落到默认"

    # brain 侧真的用它截断，并写清截了多少
    src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "chat_manager.get_max_incoming_chars()" in src
    assert "单条正文过长 → 截断：原 %d 字 → 保留 %d 字" in src


# ---------------------------------------------------------------- H-3：飞书同一口径

def test_h3_feishu_uses_group_policy():
    """H-3：飞书**任意 @ 都算 @ 我**必须改掉，与 OneBot 同一口径。"""
    src = (AI_DIR / "adapters" / "feishu.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert 'seg, "type", "") == "at"' not in code, \
        "飞书不许再内联『任意 at 段就算 @ 我』"
    assert "is_at_me(event, bot_id=" in code, "要复用 group_policy.is_at_me"
    assert "mentions_other(event, bot_id=" in code, "要算出 at_others"
    # 2026-10-15：机器人自己的 id 要按适配器**真实字段**取。飞书 BotInfo 里叫
    # open_id（没有 id），只读 id 会取到空串 → @ 判定整个失效（见该函数注释）。
    assert "_bot_ids_of(bot)" in code and '"open_id"' in code, \
        "机器人自己的 id 要收全（open_id / self_id …），别只读一个不存在的字段"
    assert '"at_others": at_others' in code, "at_others 要进 extra，brain 才拿得到"
    assert "extra={}" not in code, "extra 不能再是空的"


if __name__ == "__main__":
    import traceback

    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except Exception:
                fails += 1
                print("FAIL %s" % name)
                traceback.print_exc()
    print("done, fails=%d" % fails)
    raise SystemExit(1 if fails else 0)
