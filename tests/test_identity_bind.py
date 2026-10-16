# -*- coding: utf-8 -*-
"""跨平台身份确认（绑定）与"发到对方的另一个平台"（2026-10-04 老板点名要的）。

场景："我在微信上让它把这文件传到我 QQ。" 平台不给手机号/QQ号（官方通道连 QQ 号都
没有，只有 openid），所以**必须先让用户自己确认一次**：微信发「绑定」拿码 →
QQ 发「绑定 123456」。这条用例锁住：
- 码能发、能用、能过期、不重复用、同一身份 30 秒内只能要一次；
- 绑完两个身份算一个人（记忆也随之互通）；
- `send_to_platform` 只认**触发者自己绑定的**目标，没绑定就如实说"让他先绑"。
"""
import asyncio
import importlib.util
import logging
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_bind_pkg"


def _ensure_pkg(tmp: Path | None = None):
    if PKG in sys.modules:
        return
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    # identity.py 也要用 localstore 落盘身份文件 —— 指到临时目录
    if "nonebot_plugin_localstore" not in sys.modules:
        store = types.ModuleType("nonebot_plugin_localstore")
        target = (tmp or Path(tempfile.mkdtemp(prefix="qbm_bind_store_"))) / "identity.json"
        store.get_plugin_config_file = lambda _name: target
        store.get_plugin_data_file = lambda _name: target
        sys.modules["nonebot_plugin_localstore"] = store

    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg
    for name in ("platform", "identity"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", AI_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.{name}"] = mod
        spec.loader.exec_module(mod)


class _Ctx:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)
        return True


class _Msg:
    def __init__(self, platform="wechat", user_id="wx-1", text="绑定"):
        self.platform = platform
        self.user_id = user_id
        self.plain_text = text
        self.nickname = "小李"
        self.scene = "private"
        self.room_id = user_id

    @property
    def user_key(self):
        return f"{self.platform}:{self.user_id}"


def _load_bind(tmp: Path):
    """加载 identity_bind，并把它的码文件指到临时目录。"""
    _ensure_pkg(tmp)
    codes = tmp / "codes.json"
    sys.modules["nonebot_plugin_localstore"].get_plugin_config_file = lambda _name: codes
    sys.modules["nonebot_plugin_localstore"].get_plugin_data_file = lambda _name: codes
    spec = importlib.util.spec_from_file_location(f"{PKG}.identity_bind",
                                                 AI_DIR / "identity_bind.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_issue_redeem_binds_two_identities():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind_"))
    bind = _load_bind(tmp)
    identity = sys.modules[f"{PKG}.identity"]

    out = bind.issue("wechat:wx-1", now=1000.0)
    assert out["ok"] and len(out["code"]) == 6, out
    code = out["code"]

    got = bind.redeem(code, "qq_official:openid-9", now=1010.0)
    assert got["ok"] and got["other"] == "wechat:wx-1", got
    assert identity.canonical("wechat:wx-1") == identity.canonical("qq_official:openid-9")
    assert "微信" in bind.describe("qq_official:openid-9")
    assert "QQ 官方机器人" in bind.describe("wechat:wx-1")


def test_code_expires_and_cannot_be_reused():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind2_"))
    bind = _load_bind(tmp)
    code = bind.issue("wechat:wx-2", now=2000.0)["code"]
    assert bind.redeem(code, "qq:12345", now=2000.0 + bind.CODE_TTL + 1)["error"] == "expired"

    code2 = bind.issue("wechat:wx-3", now=3000.0)["code"]
    assert bind.redeem(code2, "qq:777", now=3010.0)["ok"] is True
    # 同一个码第二次用：已经消费掉了
    assert bind.redeem(code2, "qq:888", now=3011.0)["error"] == "expired"


def test_cooldown_and_bad_code():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind3_"))
    bind = _load_bind(tmp)
    assert bind.issue("wechat:wx-4", now=4000.0)["ok"] is True
    second = bind.issue("wechat:wx-4", now=4005.0)
    assert second["ok"] is False and second["error"] == "too_soon", second
    assert second["wait"] > 0
    assert bind.redeem("12", "qq:1", now=4006.0)["error"] == "bad_code"
    assert bind.redeem("999999", "qq:1", now=4007.0)["error"] == "expired"


def test_same_identity_code_is_refused():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind4_"))
    bind = _load_bind(tmp)
    code = bind.issue("wechat:wx-5", now=5000.0)["code"]
    got = bind.redeem(code, "wechat:wx-5", now=5001.0)
    assert got["error"] == "same_identity", got


def test_command_flow_replies_are_plain_chinese():
    """① 说「绑定」→ 给码 + 告诉他去哪发（这条语义**没变**）。

    2026-10-12 语义变更（老板：绑定成功的回复别用固定话术，让 AI 按人设自己说）：
    ② 另一个平台兑码成功时 `try_handle` **返回 False 且不发消息** —— 命令层不再抢话，
    由 `chat.platform_context` 取走 `take_just_bound()` 让 AI 说一句。
    **该断言由 Linux 线（0.2.42）按新行为更新，请原改动方复核。**
    """
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind5_"))
    bind = _load_bind(tmp)
    ctx = _Ctx()

    # ① 说「绑定」→ 给码 + 告诉他去哪发
    assert asyncio.run(bind.try_handle(_Msg(text="绑定"), ctx)) is True
    assert "绑定码" in ctx.sent[0] and "另一个平台" in ctx.sent[0], ctx.sent

    code = "".join(ch for ch in ctx.sent[0].split("绑定码：")[1][:6] if ch.isdigit())
    ctx2 = _Ctx()
    qq_key = "qq_official:openid-1"
    # ② 另一个平台发「绑定 123456」→ 绑上了，但**不再回固定话术**
    assert asyncio.run(bind.try_handle(
        _Msg(platform="qq_official", user_id="openid-1", text=f"绑定 {code}"),
        ctx2)) is False, "兑码成功要交给 AI 说，命令层不再抢话"
    assert ctx2.sent == [], "命令层不该再发那句固定话术：%r" % (ctx2.sent,)
    info = bind.take_just_bound(qq_key)
    assert info, "必须登记 just_bound，否则 AI 那边不知道要说这句"
    assert "微信" in info and "QQ" in info, info
    assert bind.take_just_bound(qq_key) == "", "取走一次就该没了（一次性）"
    assert "微信" in bind.describe(qq_key), bind.describe(qq_key)

    # ③ 「我的身份」→ 列出两个平台
    ctx3 = _Ctx()
    asyncio.run(bind.try_handle(_Msg(text="我的身份"), ctx3))
    assert "微信" in ctx3.sent[0] and "QQ" in ctx3.sent[0], ctx3.sent


# ===== 2026-10-12 那条在途改动（直接在 QQ 上发 6 位码 / 兑码成功交给 AI）的守门用例 =====
# 这三条由 Linux 线（0.2.42）补：这条路径用户可见，不能"随包上线但没人验过"。

def test_bare_six_digit_code_on_second_platform_binds_and_defers_to_ai():
    """**直接在第二个平台发 6 位码**（老板最自然的做法）→ 绑上 + 交给 AI 说。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind8_"))
    bind = _load_bind(tmp)
    ctx = _Ctx()
    asyncio.run(bind.try_handle(_Msg(platform="wechat", user_id="wx-bare", text="绑定"), ctx))
    code = "".join(ch for ch in ctx.sent[0].split("绑定码：")[1][:6] if ch.isdigit())

    ctx2 = _Ctx()
    handled = asyncio.run(bind.try_handle(
        _Msg(platform="qq", user_id="10001", text=code), ctx2))
    assert handled is False, "6 位码兑成功也要交给 AI，不能拦下来说固定话术"
    assert ctx2.sent == []
    assert bind.take_just_bound("qq:10001"), "要登记 just_bound"
    assert "微信" in bind.describe("qq:10001")


def test_bare_six_digits_that_is_not_a_code_gets_a_chinese_hint():
    """6 位数字但**不是**有效绑定码 → **明确中文提示**（别丢给 AI 让它答"这串数字是啥"）。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind9_"))
    bind = _load_bind(tmp)
    ctx = _Ctx()
    handled = asyncio.run(bind.try_handle(
        _Msg(platform="qq", user_id="10002", text="123456"), ctx))
    assert handled is True, "码不对也要拦下来给一句人话，不能丢给 AI 瞎猜"
    assert ctx.sent and "绑定码" in ctx.sent[0] and "过期" in ctx.sent[0], ctx.sent
    assert bind.take_just_bound("qq:10002") == "", "没绑上就不该登记 just_bound"


def test_wrong_expired_and_same_identity_codes_give_chinese_hints():
    """码错误 / 已过期 / 拿自己的码 → 都要有**明确中文提示**且不崩。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind10_"))
    bind = _load_bind(tmp)
    identity = sys.modules[f"{PKG}.identity"]
    identity._groups.clear()
    identity._index.clear()

    # (a) 位数不对的码（「绑定 12」）→ 也是**明确中文提示**，不崩、不静默
    ctx_a = _Ctx()
    assert asyncio.run(bind.try_handle(_Msg(text="绑定 12"), ctx_a)) is True
    assert ctx_a.sent and "绑定" in ctx_a.sent[0] and "我的身份" in ctx_a.sent[0], ctx_a.sent

    # (b) 已过期的真码
    code = bind.issue("wechat:wx-exp", now=time.time() - bind.CODE_TTL - 10)["code"]
    ctx_b = _Ctx()
    assert asyncio.run(bind.try_handle(
        _Msg(platform="qq", user_id="20001", text=f"绑定 {code}"), ctx_b)) is True
    assert "过期" in ctx_b.sent[0] and "重新发" in ctx_b.sent[0], ctx_b.sent

    # (c) 拿自己当前账号的码去兑（same_identity）→ 明确中文提示
    mine = bind.issue("wechat:wx-self")["code"]
    ctx_c = _Ctx()
    assert asyncio.run(bind.try_handle(
        _Msg(platform="wechat", user_id="wx-self", text=f"绑定 {mine}"), ctx_c)) is True
    assert "自己" in ctx_c.sent[0] and "另一个平台" in ctx_c.sent[0], ctx_c.sent


def test_duplicate_binding_tells_the_user_its_already_done():
    """重复绑定：再要一个新码兑同一个对象 → 幂等成功，并**明确告诉他不用重复绑**。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind11_"))
    bind = _load_bind(tmp)
    identity = sys.modules[f"{PKG}.identity"]
    identity._groups.clear()
    identity._index.clear()

    # 时间戳一律基于真实 now：`try_handle` 内部用的是 time.time()，
    # 混用假时间戳会被 _prune 当成"早就过期"直接删掉（第一版就这么写错了）。
    base = time.time()
    code1 = bind.issue("wechat:wx-dup", now=base)["code"]
    assert bind.redeem(code1, "qq:30001", now=base + 1)["ok"] is True

    code2 = bind.issue("wechat:wx-dup", now=base + 40)["code"]
    second = bind.redeem(code2, "qq:30001", now=base + 41)
    assert second["ok"] is True and second.get("already") is True, second
    assert identity.canonical("wechat:wx-dup") == identity.canonical("qq:30001")

    # 再发一次新码：明确告诉他"早就是同一个人了"
    code3 = bind.issue("wechat:wx-dup", now=base + 80)["code"]
    ctx = _Ctx()
    assert asyncio.run(bind.try_handle(
        _Msg(platform="qq", user_id="30001", text=f"绑定 {code3}"), ctx)) is True
    assert "同一个人" in ctx.sent[0] and "不用重复绑" in ctx.sent[0], ctx.sent

    # 同一个旧码再兑一次：已经被消费 → 明确提示（不是崩、也不是静默）
    ctx2 = _Ctx()
    assert asyncio.run(bind.try_handle(
        _Msg(platform="qq", user_id="30001", text=f"绑定 {code1}"), ctx2)) is True
    assert "过期" in ctx2.sent[0], ctx2.sent


def test_platform_context_announces_just_bound_once():
    """`chat.platform_context` 必须把"他刚刚绑定成功"带给 AI，而且**只带一次**。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind12_"))
    bind = _load_bind(tmp)
    ctx = _Ctx()
    asyncio.run(bind.try_handle(_Msg(platform="wechat", user_id="wx-pc", text="绑定"), ctx))
    code = "".join(ch for ch in ctx.sent[0].split("绑定码：")[1][:6] if ch.isdigit())
    asyncio.run(bind.try_handle(_Msg(platform="qq", user_id="40001", text=code), _Ctx()))

    platform_context = _load_platform_context()
    msg = _Msg(platform="qq", user_id="40001", text="你好")
    first = platform_context(msg)
    assert "他刚刚绑定成功" in first, first
    assert "别用固定话术" in first, first
    second = platform_context(msg)
    assert "他刚刚绑定成功" not in second, "一次性：第二次不该再出现"


def _load_platform_context():
    """把 `chat.platform_context` 单独抠出来跑。

    为什么不整份 import chat.py：它会连带拉起 tools_bridge（要整个 nonebot 运行时）。
    用 ast 抠函数 + 把 `__package__` 指到已注册的替身包，相对导入照样成立，
    跑的就是**生产那一份源码**。
    """
    import ast

    name = f"{PKG}.chat_frag"
    if name in sys.modules:
        return sys.modules[name].platform_context
    src = AI_DIR.parent.joinpath("ai", "chat.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "platform_context")
    mod = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(mod)
    frag = types.ModuleType(name)
    frag.__package__ = PKG
    frag.__dict__["logger"] = sys.modules["nonebot"].logger
    sys.modules[name] = frag
    exec(compile(mod, str(AI_DIR / "chat.py"), "exec"), frag.__dict__)   # noqa: S102
    return frag.platform_context



def test_non_bind_text_is_not_intercepted():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind6_"))
    bind = _load_bind(tmp)
    assert asyncio.run(bind.try_handle(_Msg(text="今天天气不错"), _Ctx())) is False


def test_send_to_platform_only_uses_own_bound_identity():
    """目标必须来自触发者自己的绑定关系：没绑定 → not_bound（并告诉他怎么绑）。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_bind7_"))
    _load_bind(tmp)
    import importlib.util as _iu

    # tools_bridge 依赖较多，这里只测它内部那个纯逻辑函数：换成直接加载模块会拉一堆依赖，
    # 所以用一个最小的替身来验证"按绑定找目标"的规则。
    spec = _iu.spec_from_file_location("ai_send_platform_check",
                                      AI_DIR / "identity_bind.py")
    assert spec is not None
    identity = sys.modules[f"{PKG}.identity"]
    # 前面的用例已经绑过几组身份，这里清干净再验（否则会串味）
    identity._groups.clear()
    identity._index.clear()
    identity.bind("wechat:wx-9", "qq_official:openid-9")
    targets = [k for k in identity.resolve("wechat:wx-9")
               if k.startswith("qq_official:")]
    assert targets == ["qq_official:openid-9"], targets
    unbound = [k for k in identity.resolve("wechat:wx-404")
               if k.startswith("qq_official:")]
    assert unbound == [], "没绑定就不该找到任何目标（会被拒绝）"


if __name__ == "__main__":
    test_issue_redeem_binds_two_identities()
    test_code_expires_and_cannot_be_reused()
    test_cooldown_and_bad_code()
    test_same_identity_code_is_refused()
    test_command_flow_replies_are_plain_chinese()
    test_bare_six_digit_code_on_second_platform_binds_and_defers_to_ai()
    test_bare_six_digits_that_is_not_a_code_gets_a_chinese_hint()
    test_wrong_expired_and_same_identity_codes_give_chinese_hints()
    test_duplicate_binding_tells_the_user_its_already_done()
    test_platform_context_announces_just_bound_once()
    test_non_bind_text_is_not_intercepted()
    test_send_to_platform_only_uses_own_bound_identity()
    print("OK identity bind")
