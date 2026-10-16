# -*- coding: utf-8 -*-
"""QQ 官方生态：免@连续对话（两种模式）+ 别人 @ 别人不抢话。

对应老板 2026-10-01 的需求：
- 免@连续对话要两种判断模式，可在插件配置里选：
  `timeout`（默认，被 @ 之后 N 秒内免 @，超时断开）与 `api`（每条都让 AI 读群聊
  最近记录判断要不要回，判断失败默认不回并写日志）；
- 别人 @ 别人时不抢话（默认行为，不需要开关），且要用与 `group_policy.is_at_me`
  同一套 mention 解析 —— 全量事件（群里每条消息都推过来）下 @ 别人也认得出。

插件本体跑在 bot venv（有 nonebot），构建环境没有 —— 所以判定逻辑抽在
`session_policy.py` / `group_policy.py` 这两个不依赖 nonebot 的文件里，按路径加载即可测。
"""
import asyncio
import importlib.util
import logging
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_manager():
    """按路径加载插件里的 manager.py（构建环境没有 nonebot，补最小替身）。"""
    if "nonebot" not in sys.modules:
        nb = types.ModuleType("nonebot")
        nb.logger = logging.getLogger("nonebot")
        adapters = types.ModuleType("nonebot.adapters")
        onebot = types.ModuleType("nonebot.adapters.onebot.v11")
        onebot.MessageEvent = object
        sys.modules["nonebot"] = nb
        sys.modules["nonebot.adapters"] = adapters
        sys.modules["nonebot.adapters.onebot.v11"] = onebot
        store = types.ModuleType("nonebot_plugin_localstore")
        store.get_plugin_config_file = (
            lambda name: Path(tempfile.gettempdir()) / ("qbm_test_" + name))
        sys.modules["nonebot_plugin_localstore"] = store
    pkg = "ai_plugin_pkg_session"
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.__path__ = [str(AI_DIR)]
    sys.modules[pkg] = pkg_mod

    class _Cfg:
        """插件会读一堆 config 字段，这里按名字给个合理默认值。"""

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
    gp = _load_by_path(pkg + ".group_policy", AI_DIR / "group_policy.py")
    sys.modules[pkg + ".group_policy"] = gp
    sp = _load_by_path(pkg + ".session_policy", AI_DIR / "session_policy.py")
    sys.modules[pkg + ".session_policy"] = sp
    return _load_by_path(pkg + ".manager", AI_DIR / "manager.py")


class _Seg:
    def __init__(self, type_, data=None):
        self.type = type_
        self.data = data or {}


class _FakeEvent:
    """最小官方事件替身：只需要 to_me / get_message / mentions。"""

    def __init__(self, to_me=False, segments=None, mentions=None):
        self.to_me = to_me
        self._segments = segments or []
        self.mentions = mentions or []

    def get_message(self):
        return self._segments


# ---------------------------------------------------------------- 判定模式
def test_config_defaults_and_switch():
    """新增配置项：session_mode 默认 timeout；非法值拒绝，合法值可切。"""
    mgr_mod = _load_manager()
    mgr = mgr_mod.ChatManager.__new__(mgr_mod.ChatManager)
    mgr._data = mgr_mod.ChatManager._get_default_config(mgr)
    mgr._save_manager_config = lambda: True     # 不落盘

    assert mgr.get_session_mode() == "timeout", "默认必须是 timeout 模式"
    assert mgr.set_session_mode("API") is True, "大小写不敏感"
    assert mgr.get_session_mode() == "api"
    assert mgr.set_session_mode("api") is False, "值没变就不用写盘（与其它 setter 一致）"
    assert mgr.get_session_mode() == "api"
    assert mgr.set_session_mode("乱写的") is False, "认不出的模式必须拒绝"
    assert mgr.get_session_mode() == "api", "拒绝后保持原值"


def test_expire_seconds_clamped_30_to_300():
    """免@有效期夹在 30~300 秒（默认 180；2026-10-12 老板要求 30 → 180）。"""
    mgr_mod = _load_manager()
    mgr = mgr_mod.ChatManager.__new__(mgr_mod.ChatManager)
    mgr._data = mgr_mod.ChatManager._get_default_config(mgr)
    mgr._save_manager_config = lambda: True

    assert mgr.get_session_expire_seconds() == 180, "默认 180 秒（不再是 30）"
    assert mgr.set_session_expire_seconds(10) is True, "10 夹成 30，与当前 180 不同 → 要写盘"
    assert mgr.get_session_expire_seconds() == 30, "小于 30 夹到 30"
    assert mgr.set_session_expire_seconds(9999) is True
    assert mgr.get_session_expire_seconds() == 300, "大于 300 夹到 300"
    assert mgr.set_session_expire_seconds(120) is True
    assert mgr.get_session_expire_seconds() == 120
    assert mgr.set_session_expire_seconds(120) is False, "值没变就不用写盘"
    # 手改配置文件写坏了也不许炸
    mgr._data["session_expire_seconds"] = "坏了"
    assert mgr.get_session_expire_seconds() == 30


def test_branch_matrix_two_modes():
    """两种模式的判定流程（纯函数）：timeout 看时间窗，api 每条都判。"""
    sp = _load_by_path("ai_session_policy_t", AI_DIR / "session_policy.py")

    def branch(**kw):
        base = dict(is_at_me=False, at_others=False, mode="timeout",
                    session_active=False, auto_participate=False)
        base.update(kw)
        return sp.choose_branch(**base)

    # 被 @ 永远优先
    assert branch(is_at_me=True, at_others=True) == sp.BRANCH_AT_ME
    # 别人@别人：两种模式都不抢话（默认行为）
    assert branch(at_others=True) == sp.BRANCH_SKIP
    assert branch(at_others=True, mode="api") == sp.BRANCH_SKIP
    assert branch(at_others=True, session_active=True) == sp.BRANCH_SKIP
    # timeout 模式：只有免@会话有效期内才判
    assert branch(mode="timeout", session_active=False) == sp.BRANCH_IGNORE
    assert branch(mode="timeout", session_active=True) == sp.BRANCH_JUDGE_TIMEOUT
    # api 模式：不看时间窗，每条非 @ 消息都交给 AI 判
    assert branch(mode="api", session_active=False) == sp.BRANCH_JUDGE_API
    assert branch(mode="api", session_active=True) == sp.BRANCH_JUDGE_API
    # 旧版随机参与兜底
    assert branch(mode="timeout", auto_participate=True) == sp.BRANCH_JUDGE_GROUP
    # off 模式：关掉免@连续对话 —— 只有被 @ 才回，其余一律不理（在有效期里也不理）
    assert sp.normalize_mode("off") == sp.MODE_OFF
    assert branch(mode="off", session_active=True) == sp.BRANCH_IGNORE
    assert branch(mode="off", auto_participate=True) == sp.BRANCH_IGNORE
    assert branch(mode="off", is_at_me=True) == sp.BRANCH_AT_ME
    assert branch(mode="off", at_others=True) == sp.BRANCH_SKIP


def test_judge_failure_falls_back_to_no_reply():
    """判断失败：默认不回 + 写日志，不许抛出去（也绝不发写死的话术）。"""
    sp = _load_by_path("ai_session_policy_j", AI_DIR / "session_policy.py")
    calls = []

    async def boom(messages, user_id, nickname, last_reply_to=""):
        calls.append(user_id)
        raise RuntimeError("模型超时")

    async def yes(messages, user_id, nickname, last_reply_to=""):
        return True

    async def no(messages, user_id, nickname, last_reply_to=""):
        return "NO"

    ok = asyncio.run(sp.judge_quietly(boom, messages=[], user_id="u1", nickname="小李"))
    assert ok is False, "抛异常必须当不回"
    assert calls == ["u1"]
    assert asyncio.run(sp.judge_quietly(yes, messages=[], user_id="u1")) is True
    assert asyncio.run(sp.judge_quietly(no, messages=[], user_id="u1")) is False


# ---------------------------------------------------------------- @ 别人识别
def test_mentions_other_uses_same_mention_parsing():
    """全量事件里 @ 别人也要认出来（以前按 @-专用事件写，认不出就不抢话失效）。"""
    gp = _load_by_path("ai_group_policy_others", AI_DIR / "group_policy.py")

    bot_seg = _Seg("mention_user", {"user_id": "bot", "is_bot": True})
    other_seg = _Seg("mention_user", {"user_id": "someone", "is_bot": False})
    text_seg = _Seg("text", {"text": "你好"})

    # 只 @ 了别人 → at_others
    assert gp.mentions_other(_FakeEvent(segments=[other_seg])) is True
    # 只 @ 机器人 → 不算 @ 别人（is_at_me 那边已经命中）
    assert gp.mentions_other(_FakeEvent(segments=[bot_seg])) is False
    # 普通聊天 → 没有 @ 别人
    assert gp.mentions_other(_FakeEvent(segments=[text_seg])) is False
    # 原始 mentions 列表兜底：非 bot 的那条就是"别人"
    assert gp.mentions_other(
        _FakeEvent(mentions=[type("M", (), {"bot": False})()])) is True
    assert gp.mentions_other(
        _FakeEvent(mentions=[type("M", (), {"bot": True})()])) is False
    # 事件结构不对时不许抛
    class _Bad:
        to_me = False

        def get_message(self):
            raise RuntimeError("boom")

    assert gp.mentions_other(_Bad()) is False
    # 与 is_at_me 同一套解析：同一条事件两边判定互不干扰
    both = _FakeEvent(segments=[bot_seg, other_seg])
    assert gp.is_at_me(both) is True and gp.mentions_other(both) is True


def test_official_adapter_sets_at_others():
    """官方适配器的 at_others 要用**真实语义**算：@ 别人 ≠ 被 @，@ 自己 = 被 @。

    这条以前只断言源码里有 `mentions_other(event)` 这个字面串。后来适配器改成
    `mentions_other(event, bot_id=_bot_id(bot))`（**必须补 bot id**，否则认不出
    "@ 的是机器人自己"，会把"@ 它"误判成"@ 别人"而不回话），字面串对不上，
    断言就假失败了 —— 这是**陈旧断言**，不是回归。

    所以现在按真实语义测，而且用的是**适配器调的那同一套** `group_policy` 解析：
      * 只 @ 别人 → `is_at_me` 假、`mentions_other` 真（brain 据此不抢话）；
      * 只 @ 机器人自己 → `is_at_me` **真**、`mentions_other` **假**
        —— 这是那个 bug 的根因方向：mention 段**不带** is_bot/is_you 标记时，
        必须靠 `user_id == 机器人 id` 认出来，否则"@ 了它却不理"。
    """
    gp = _load_by_path("ai_group_policy_official", AI_DIR / "group_policy.py")
    BOT = "BOT_OPENID_123"

    def ev(to_me=False, segments=None, mentions=None):
        """官方全量事件替身：**to_me 恒为 False**（开了"接收所有消息"就是这样）。"""
        e = _FakeEvent(to_me=to_me, segments=segments, mentions=mentions)
        e.self_id = BOT
        return e

    other_seg = _Seg("mention_user", {"user_id": "someone_else"})
    # 关键：机器人自己那条 mention **不带** is_bot/is_you，只能靠 user_id 认
    me_by_id = _Seg("mention_user", {"user_id": BOT})
    me_by_flag = _Seg("mention_user", {"user_id": "whatever", "is_bot": True})

    # ① @ 别人 → 不该当成被 @
    e = ev(segments=[other_seg])
    assert gp.is_at_me(e) is False, "@ 别人不能被当成 @ 了机器人"
    assert gp.mentions_other(e) is True, "@ 别人要被认出来（brain 据此不抢话）"

    # ② @ 我自己 → 应当算被 @（那个 bug 的根因方向，别漏）
    for seg, label in ((me_by_id, "只靠 user_id 认"), (me_by_flag, "带 is_bot 标记")):
        e = ev(segments=[seg])
        assert gp.is_at_me(e) is True, "@ 机器人自己必须算被 @（%s）" % label
        assert gp.mentions_other(e) is False, "机器人自己那条不算 @ 别人（%s）" % label

    # ③ 同时 @ 了我和别人 → 被 @ 优先，两条信号都为真
    e = ev(segments=[me_by_id, other_seg])
    assert gp.is_at_me(e) is True and gp.mentions_other(e) is True

    # ④ 谁也没 @ → 两条都为假
    e = ev(segments=[_Seg("text", {"text": "普通聊天"})])
    assert gp.is_at_me(e) is False and gp.mentions_other(e) is False

    # ⑤ 接线（适配器带 nonebot，构建环境导不进来，只能按源码校验）：
    #    extra 里要有 at_others，且算它的时候**必须传 bot_id=**
    src = (AI_DIR / "adapters" / "qq_official.py").read_text(encoding="utf-8")
    assert '"at_others"' in src, "适配器要把 at_others 写进 extra，brain 才拿得到这个信号"
    assert re.search(r"mentions_other\(\s*event\s*,\s*bot_id\s*=", src), (
        "调 mentions_other 必须传 bot_id=…：不传就认不出'@ 的是机器人自己'，"
        "会把 @ 机器人误判成 @ 别人（正是这次要防的那个方向）")


def test_brain_wires_both_modes():
    """brain 必须真的按 session_policy 分支走（而不是留一份老逻辑）。"""
    src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "session_policy.choose_branch(" in src
    assert "BRANCH_JUDGE_API" in src and "BRANCH_JUDGE_TIMEOUT" in src
    assert "session_policy.judge_quietly(" in src
    assert "get_session_mode()" in src


def test_wake_hit_matches_addressing_words():
    """被叫到称呼 = 被@（2026-10-12 老板报：群里第一次喊「奈姐」没有回复）。"""
    sp = _load_by_path("ai_plugin_wake_policy", AI_DIR / "session_policy.py")
    assert sp.wake_hit("奈姐", ["可奈", "奈姐"]) == "奈姐"
    assert sp.wake_hit("奈姐在吗", ["可奈", "奈姐"]) == "奈姐"
    assert sp.wake_hit("他说可奈这人不错", ["可奈"]) == "可奈"
    assert sp.wake_hit("KONA hello", ["kona"]) == "kona"
    assert sp.wake_hit("今天天气不错", ["可奈", "奈姐"]) == ""
    assert sp.wake_hit("", ["奈姐"]) == ""
    assert sp.wake_hit("奈姐", []) == ""
    # 命中后按「被@」用：三种模式（含 off）都直接回
    for mode in ("off", "timeout", "api"):
        assert sp.choose_branch(is_at_me=True, at_others=False, mode=mode,
                                session_active=False,
                                auto_participate=False) == sp.BRANCH_AT_ME


def test_brain_and_client_wire_wake_words():
    """接线回归：brain 要用热配置词表判定；客户端要把词表写进热配置。"""
    brain_src = (AI_DIR / "brain.py").read_text(encoding="utf-8")
    assert "session_policy.wake_hit(" in brain_src, "brain 没调用 wake_hit"
    assert "or bool(wake_word)" in brain_src, "称呼命中没有并进「被@」判定"
    assert "gates.wake_words()" in brain_src, "没从热配置读称呼词表"
    gates_src = (AI_DIR / "gates.py").read_text(encoding="utf-8")
    assert "def wake_words(" in gates_src
    core_bot = (ROOT / "src" / "qbotmanager" / "core" / "bot.py"
                ).read_text(encoding="utf-8")
    assert 'data["wake_words"]' in core_bot, "热配置快照里没有写称呼词表"


if __name__ == "__main__":
    test_config_defaults_and_switch()
    test_expire_seconds_clamped_30_to_300()
    test_branch_matrix_two_modes()
    test_judge_failure_falls_back_to_no_reply()
    test_mentions_other_uses_same_mention_parsing()
    test_official_adapter_sets_at_others()
    test_brain_wires_both_modes()
    test_wake_hit_matches_addressing_words()
    test_brain_and_client_wire_wake_words()
    print("OK ai plugin group session")
