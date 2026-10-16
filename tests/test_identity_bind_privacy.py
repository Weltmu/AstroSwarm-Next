# -*- coding: utf-8 -*-
"""绑定码**绝不能在群里发**（2026-10-15 修，隐私事故）。

改前的行为：不管在群里还是私聊，发「绑定」都会把 6 位码**直接发回当前会话**。
于是群里任何一个人打一句「绑定」，全群 5 分钟内都能拿这个码去私聊机器人：

    identity.bind(码) → 把**自己的号**和群里那个人的身份合并（双向并组）

合并之后：两边记忆互相可见、`发到我的 QQ/微信` 按对方身份走 —— 记忆串到别人身上，
而且当事人完全看不出来（`describe` 只会说"你绑定了 QQ、微信"）。

顺带修第二件：群里谁随口一个 6 位数（"666666"、验证码、数学题）以前会被绑定流程
**劫持**，当场冒出一句系统话术"这个绑定码不对或已经过期了……"，那条消息也不会
交给 AI。现在群里的失败一律不吭声、不吞消息，照常走 AI。

跑法：
    .\\.build_venv_py314\\Scripts\\python.exe -m pytest tests/test_identity_bind_privacy.py -q
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
AI = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"

_TMP = Path(tempfile.mkdtemp(prefix="qbm_bind_priv_"))
_MOD = None


def mod():
    global _MOD
    if _MOD is not None:
        return _MOD

    nb = sys.modules.get("nonebot") or types.ModuleType("nonebot")
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    sys.modules["nonebot"] = nb

    # 码存到临时目录 —— **绝不碰线上** aichat_identity_codes.json
    st = types.ModuleType("nonebot_plugin_localstore")
    st.get_plugin_config_file = lambda n: _TMP / n
    st.get_plugin_data_file = lambda n: _TMP / n
    sys.modules["nonebot_plugin_localstore"] = st

    pkg = types.ModuleType("ibpkg")
    pkg.__path__ = [str(AI)]
    sys.modules["ibpkg"] = pkg
    spec = importlib.util.spec_from_file_location("ibpkg.platform", AI / "platform.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["ibpkg.platform"] = m
    spec.loader.exec_module(m)

    spec = importlib.util.spec_from_file_location("ibpkg.identity_bind", AI / "identity_bind.py")
    _MOD = importlib.util.module_from_spec(spec)
    sys.modules["ibpkg.identity_bind"] = _MOD
    spec.loader.exec_module(_MOD)
    return _MOD


class _Msg:
    def __init__(self, scene="group", text="绑定", user="990000003", platform="qq"):
        self.scene = scene
        self.plain_text = text
        self.text = text
        self.user_id = user
        self.platform = platform

    @property
    def user_key(self):
        return "%s:%s" % (self.platform, self.user_id)


class _Ctx:
    def __init__(self):
        self.texts = []

    async def send_text(self, t):
        self.texts.append(str(t))

    async def send_text_at(self, t):
        self.texts.append(str(t))


def run(msg):
    ctx = _Ctx()
    handled = asyncio.run(mod().try_handle(msg, ctx))
    return handled, ctx.texts


# ================================================================ 核心：群里不发码
def test_group_ask_never_leaks_the_code():
    """群里发「绑定」→ 只给一句提示，**绝不能出现 6 位数字**。"""
    handled, texts = run(_Msg(scene="group", text="绑定"))
    joined = " ".join(texts)
    print("\n[群里要码] 回复 = %r" % joined)
    assert handled is True, "群里该回一句提示（免得用户以为没反应）"
    assert texts, "群里应该有提示"
    assert re.search(r"\d{6}", joined) is None, "绑定码被发到群里了：%r" % joined
    assert "私聊" in joined, "要告诉用户去私聊拿"


def test_private_ask_still_returns_a_code():
    """私聊发「绑定」→ 照旧拿得到码（功能不能被安全修复搞坏）。"""
    handled, texts = run(_Msg(scene="private", text="绑定", user="pv1"))
    joined = " ".join(texts)
    print("\n[私聊要码] 回复 = %r" % joined[:120])
    assert handled is True
    assert re.search(r"\d{6}", joined), "私聊里应该拿到 6 位码"


def test_group_code_redeem_success_still_works():
    """在私聊拿到码、去**群里**发它 —— 兑换本身仍应成功（不能拦过头）。

    兑换不泄露任何东西（得先有码），所以不该按场景拦；成功时返回 False 交给 AI 说。
    """
    m = mod()
    code = m.issue("qq:990000003")["code"]
    handled, texts = run(_Msg(scene="group", text=code, user="990000009"))
    print("\n[群里兑换] handled=%s texts=%r" % (handled, texts))
    assert handled is False, "兑换成功应交给 AI 说（返回 False），而不是回固定话术"
    assert texts == [], "不该在群里插话"


# ================================================================ 群里 6 位数不再劫持
def test_random_6_digits_in_group_is_not_hijacked():
    """群里随口一个 6 位数 → 不吞消息、不冒系统话术。"""
    handled, texts = run(_Msg(scene="group", text="666666", user="randomguy"))
    print("\n[群里 666666] handled=%s texts=%r" % (handled, texts))
    assert handled is False, "群里随便一个 6 位数不该被绑定流程劫持"
    assert texts == [], "不该在群里冒出系统话术"


def test_wrong_code_in_private_still_tells_the_user():
    """私聊里码不对 → 照旧明确回一句（老板 2026-10-12 就是因为这个卡住的）。"""
    handled, texts = run(_Msg(scene="private", text="000001", user="pv2"))
    joined = " ".join(texts)
    print("\n[私聊错码] 回复 = %r" % joined[:120])
    assert handled is True and texts, "私聊里码不对必须回话"
    assert "过期" in joined or "不对" in joined


def test_identity_query_still_works_everywhere():
    """「我的身份」是纯查询（不含秘密），群聊私聊都该能问。"""
    handled, texts = run(_Msg(scene="group", text="我的身份", user="g1"))
    assert handled is True and texts


# ================================================================ 源码级锁
def test_source_lock_code_issue_is_private_only():
    src = (AI / "identity_bind.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "SCENE_PRIVATE" in code, "没有按场景区分 —— 码又会发进群"
    assert "is_private" in code, "缺少私聊判定"
    # 发码那段必须在 is_private 为真之后
    i_guard = code.index('if not is_private:')
    i_issue = code.index('result = issue(user_key)')
    assert i_guard < i_issue, "发码没有先过私聊闸门 —— 群里还能拿到码"
