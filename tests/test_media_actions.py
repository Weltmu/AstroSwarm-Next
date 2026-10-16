# -*- coding: utf-8 -*-
"""G1/G2/G3：媒体动作、主动发到指定目标、重活动作（2026-10-02 补的历史欠账）。

三条规矩必须锁住：
1. 通道不支持 → `unsupported_by_channel`，**绝不假装成功**；
2. 主动发到别处 → 必须过白名单，**没配白名单 = 一个都不许发**；
3. 联网类动作 → 只允许公网 http(s)，内网地址拦掉，长度/大小有上限。
"""
import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_media_pkg"


def _load_bridge():
    """按路径加载插件里的 tools_bridge（构建环境没有 nonebot，补最小替身）。"""
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    if not hasattr(nb, "get_bots"):
        nb.get_bots = lambda: {}
    if not hasattr(nb, "FinishedException"):
        nb.FinishedException = type("FinishedException", (Exception,), {})
    if "nonebot.adapters.onebot.v11" not in sys.modules:
        ob = types.ModuleType("nonebot.adapters.onebot.v11")
        ob.Bot = object
        ob.MessageEvent = object
        ob.Message = object
        ob.MessageSegment = object
        sys.modules.setdefault("nonebot.adapters", types.ModuleType("nonebot.adapters"))
        sys.modules["nonebot.adapters.onebot.v11"] = ob
        store = types.ModuleType("nonebot_plugin_localstore")
        store.get_plugin_config_file = lambda n: Path(tempfile.gettempdir()) / ("qbm_t_" + n)
        store.get_plugin_data_file = lambda n: Path(tempfile.gettempdir()) / ("qbm_t_" + n)
        sys.modules["nonebot_plugin_localstore"] = store

    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(AI_DIR)]
    sys.modules[PKG] = pkg

    # config.py 要 pydantic-settings（构建环境没有）→ 用替身顶上
    class _Cfg:
        aichat_super_users = []
        aichat_proactive_groups = []
        aichat_session_expire_seconds = 30
        aichat_session_mode = "timeout"
        aichat_session_unrelated_limit = 5
        aichat_group_auto_participate = False
        aichat_interject_enabled = False
        aichat_interject_probability = 0.3
        aichat_interject_interval_min = 30
        aichat_interject_interval_max = 90
        aichat_interject_history_count = 10

        def __getattr__(self, name):
            numeric = ("interval", "cooldown", "count", "seconds", "limit",
                       "probability", "quiet", "start", "end")
            return 30 if any(k in name for k in numeric) else []

    cfg_mod = types.ModuleType(f"{PKG}.config")
    cfg_mod.config = _Cfg()
    sys.modules[f"{PKG}.config"] = cfg_mod

    for name in ("memory", "identity", "image_tools", "session_policy",
                 "group_policy", "pack_params"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", AI_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.{name}"] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:            # 某个兄弟模块依赖太多时，给个空壳顶上
            shell = types.ModuleType(f"{PKG}.{name}")
            sys.modules[f"{PKG}.{name}"] = shell

    mgr_spec = importlib.util.spec_from_file_location(f"{PKG}.manager", AI_DIR / "manager.py")
    mgr_mod = importlib.util.module_from_spec(mgr_spec)
    sys.modules[f"{PKG}.manager"] = mgr_mod
    mgr_spec.loader.exec_module(mgr_mod)

    spec = importlib.util.spec_from_file_location(f"{PKG}.tools_bridge", AI_DIR / "tools_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.tools_bridge"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Ctx:
    """最小 ctx：只有 can() 和 channel。"""

    def __init__(self, perms=(), channel=None, scene="group", platform="qq_official"):
        self._perms = set(perms)
        self.channel = channel
        self.scene = scene
        self.platform = platform

    def can(self, perm):
        return perm in self._perms


class _Channel:
    def __init__(self, kinds=("image", "file", "voice", "card"), target_ok=True):
        self.platform = "qq_official"
        self._kinds = set(kinds)
        self.sent = []
        self.target_ok = target_ok
        self.texts = []

    async def send_text(self, text):
        self.texts.append(text)
        return True

    def supports(self, kind):
        return kind in self._kinds

    async def send_media(self, kind, target, *args):
        if kind not in self._kinds:
            return False
        self.sent.append((kind, target, args))
        return True

    async def send_to_target(self, target, text):
        self.sent.append(("target", target, text))
        return self.target_ok


def _stub_public_dns():
    """把域名解析打桩成公网 IP。

    2026-10-15：发媒体前多了一道来源闸（media_guard）—— 公网地址要**解析后**才放行。
    单测不该依赖真实 DNS（离线环境会解析失败 → 被当成内网拦掉），所以打桩。
    """
    wf = sys.modules.get(f"{PKG}.web_fetch") or sys.modules.get("ai_media_pkg.web_fetch")
    if wf is not None:
        wf._resolve_ips = lambda host: ["93.184.216.34"]


def test_media_actions_send_and_report_unsupported():
    br = _load_bridge()
    import asyncio

    _stub_public_dns()
    chan = _Channel()
    ctx = _Ctx(perms=("media",), channel=chan)
    res = asyncio.run(br.do_action("send_image", {"url": "https://example.com/a.jpg", "caption": "看图"}, ctx))
    assert res["ok"] is True and res["action"] == "send_image", res
    assert chan.sent == [("image", "https://example.com/a.jpg", ("看图",))], chan.sent

    asyncio.run(br.do_action("send_voice", {"url": "https://example.com/a.silk"}, ctx))
    assert chan.sent[-1][0] == "voice"

    # 通道不支持 → 如实回报，不假装成功
    narrow = _Channel(kinds=("image",))
    res = asyncio.run(br.do_action("send_voice", {"url": "https://example.com/a.silk"},
                                   _Ctx(perms=("media",), channel=narrow)))
    assert res == {"ok": False, "error": "unsupported_by_channel",
                   "channel": "qq_official"}, res

    # 缺参数
    res = asyncio.run(br.do_action("send_image", {}, ctx))
    assert res["error"] == "missing_url", res


def test_media_actions_need_permission():
    br = _load_bridge()
    import asyncio

    ctx = _Ctx(perms=(), channel=_Channel())
    res = asyncio.run(br.do_action("send_image", {"url": "https://example.com/a.jpg"}, ctx))
    assert res["ok"] is False and res["error"] == "permission_denied", res
    assert res["permission"] == "media"


def test_send_to_requires_whitelist():
    br = _load_bridge()

    class _Mgr:
        targets = []

        def get_send_targets(self):
            return list(self.targets)

    mgr = _Mgr()
    br.chat_manager = mgr            # 替身：只关心白名单逻辑
    import asyncio

    chan = _Channel()
    ctx = _Ctx(perms=("send_message",), channel=chan)

    # 没配白名单 → 一个都不许发
    mgr.targets = []
    res = asyncio.run(br.do_action("send_to", {"target": "group:123", "text": "hi"}, ctx))
    assert res["error"] == "target_whitelist_empty", res
    assert chan.sent == []

    # 不在名单里 → 拒绝，并说明有几个允许
    mgr.targets = ["group:999"]
    res = asyncio.run(br.do_action("send_to", {"target": "group:123", "text": "hi"}, ctx))
    assert res["error"] == "target_not_allowed" and res["allowed_count"] == 1, res
    assert chan.sent == []

    # 在名单里 → 发出去
    mgr.targets = ["group:123"]
    res = asyncio.run(br.do_action("send_to", {"target": "group:123", "text": "hi"}, ctx))
    assert res["ok"] is True and chan.sent == [("target", "group:123", "hi")], (res, chan.sent)


def test_send_to_needs_channel_support():
    br = _load_bridge()
    import asyncio

    class _NoTarget(_Channel):
        send_to_target = None

    ctx = _Ctx(perms=("send_message",), channel=_NoTarget())
    res = asyncio.run(br.do_action("send_to", {"target": "group:1", "text": "x"}, ctx))
    assert res["error"] == "unsupported_by_channel", res


def test_fetch_helpers_are_pure_and_safe():
    br = _load_bridge()
    assert br._safe_file_name("../../etc/passwd") == "passwd"
    assert br._safe_file_name("a/b/c.png") == "c.png"
    assert br._safe_file_name("") == "file"
    assert br._plain_text("<b>你好</b>&amp;世界<script>x()</script>").strip().startswith("你好")
    assert "script" not in br._plain_text("<script>bad</script>ok")


def test_network_actions_block_private_hosts():
    br = _load_bridge()
    import asyncio

    for bad in ("http://127.0.0.1/x", "http://192.168.1.5/x", "file:///etc/passwd",
                "ftp://x/y"):
        res = asyncio.run(br.do_action("expand_link", {"url": bad},
                                       _Ctx(perms=("network",))))
        assert res["ok"] is False, (bad, res)
        assert res["error"] in ("bad_url", "blocked_host"), res


def test_compress_image_reports_missing_file():
    import importlib.util as _iu

    spec = _iu.spec_from_file_location("ai_image_tools_t", AI_DIR / "image_tools.py")
    mod = _iu.module_from_spec(spec)
    sys.modules["ai_image_tools_t"] = mod
    spec.loader.exec_module(mod)
    res = mod.compress_image_file("/no/such/file.jpg")
    assert res["ok"] is False and res["error"] in ("not_found", "unsupported"), res


def test_compress_image_actually_shrinks_a_real_image():
    import importlib.util as _iu

    spec = _iu.spec_from_file_location("ai_image_tools_t2", AI_DIR / "image_tools.py")
    mod = _iu.module_from_spec(spec)
    sys.modules["ai_image_tools_t2"] = mod
    spec.loader.exec_module(mod)
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        print("    （构建环境没有 Pillow，跳过真实压缩）")
        return
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "big.png"
        Image.new("RGB", (2400, 1600), (200, 30, 30)).save(src)
        res = mod.compress_image_file(src, max_side=800, quality=80, out_dir=Path(tmp))
        assert res["ok"] is True, res
        assert res["width"] == 800 and res["height"] == 533, res
        assert Path(res["path"]).exists()
        assert res["bytes"] < res["source_bytes"], res


def test_send_buttons_official_and_fallback():
    """带按钮的消息：官方通道走键盘；不支持的通道降级成文字选项并说实话。"""
    br = _load_bridge()
    import asyncio

    class _BtnChannel(_Channel):
        def __init__(self):
            super().__init__()
            self.buttons = []

        async def send_buttons(self, text, items, per_row=3):
            self.buttons.append((text, items, per_row))
            return True

    ctx = _Ctx(perms=("media",), channel=_BtnChannel())
    res = asyncio.run(br.do_action("send_buttons", {
        "text": "想干嘛？", "buttons": [{"id": "sign", "label": "签到"},
                                       {"id": "rank", "label": "排行"}]}, ctx))
    assert res["ok"] is True and res["clickable"] is True and res["count"] == 2, res
    assert ctx.channel.buttons[0][0] == "想干嘛？", ctx.channel.buttons

    # 通道不支持按钮 → 文字选项 + clickable=false
    plain = _Ctx(perms=("media",), channel=_Channel())
    res2 = asyncio.run(br.do_action("send_buttons", {
        "text": "选一个", "buttons": ["签到", "排行"]}, plain))
    assert res2["ok"] is True and res2["clickable"] is False, res2
    assert "打字回答" in res2["note"], res2
    assert plain.channel.texts and "1. 签到" in plain.channel.texts[0], plain.channel.texts
    assert plain.channel.sent == [], "降级走 send_text，不走 send_media"

    # 参数不全
    res3 = asyncio.run(br.do_action("send_buttons", {"text": "空"}, ctx))
    assert res3["error"] == "missing_text_or_buttons", res3

def test_manager_send_targets_default_empty():
    mgr_mod = sys.modules[f"{PKG}.manager"]
    mgr = mgr_mod.ChatManager.__new__(mgr_mod.ChatManager)
    mgr._data = {}
    assert mgr.get_send_targets() == [], "默认必须是一个都不许发"
    mgr._save_manager_config = lambda: True
    assert mgr.set_send_targets([" group:1 ", "", "user:2"]) is True
    assert mgr.get_send_targets() == ["group:1", "user:2"]


class _MonkeyPatch:
    """pytest 的 monkeypatch 在直跑（python file.py）时没有，给个同款最小替身。"""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


# ================================================================ 2026-10-15 安全修复
def test_download_file_rejects_non_http_and_private_targets():
    """下载文件以前**完全不校验 URL**：`file://` 能把本机文件拷进下载目录、
    内网地址随便打、跳转不复查。现在统一走 web_fetch 那道闸。"""
    br = _load_bridge()
    import asyncio

    ctx = _Ctx(perms=("network",))
    # 这些必须在**发起任何网络请求之前**就被拒（纯本地判定，不依赖网络）
    for bad in ("file:///C:/Windows/win.ini",
                "file:///etc/passwd",
                "ftp://example.com/x",
                "http://127.0.0.1:8080/x",
                "http://169.254.169.254/latest/meta-data/",
                "http://localhost./x",
                "http://[::ffff:127.0.0.1]/x"):
        res = asyncio.run(br._act_download_file({"url": bad}, ctx))
        assert res["ok"] is False, ("没拦住：%s" % bad, res)
        assert res["error"] == "blocked_url", (bad, res)


def test_is_private_host_delegates_to_web_fetch():
    """内网判定只有一份实现（web_fetch），不再各漏各的。

    旧的前缀黑名单漏 `::ffff:127.0.0.1`（IPv4-mapped）与 `localhost.`（尾点）。
    """
    br = _load_bridge()
    for bad in ("localhost", "localhost.", "127.0.0.1", "192.168.1.5",
                "::ffff:127.0.0.1", "169.254.169.254", "10.1.2.3"):
        assert br._is_private_host(bad) is True, "内网判定漏了 %s" % bad


def test_chat_manager_is_lazily_resolved():
    """`send_to` 白名单那条链路在生产必须真的能拿到 chat_manager。

    2026-10-15 修：这个模块**从来没导入过 chat_manager**，`_act_send_to` 里却写着
    `chat_manager.get_send_targets()` —— NameError 被 `except Exception` 吞掉，
    恒定返回 `config_unavailable`。单测用 `br.chat_manager = 替身` 打桩，所以一直全绿。
    现在：模块级留一个可为 None 的符号（打桩仍然有效），真正取用时按需导入。
    """
    src = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "chat_manager = None" in code, "模块级 chat_manager 符号没了 —— 打桩接口被破坏"
    assert "def _manager(" in code, "缺少按需取管理器的函数"
    assert "_manager().get_send_targets()" in code, \
        "白名单仍然直接调 chat_manager.get_send_targets()（NameError 会回来）"
    assert "chat_manager.get_send_targets()" not in code.replace("_manager().get_send_targets()", ""), \
        "还有裸用 chat_manager 的地方"


def test_send_to_still_uses_injected_manager():
    """打桩接口不能被破坏：`br.chat_manager = 替身` 之后必须用替身。"""
    br = _load_bridge()

    class _Mgr:
        def get_send_targets(self):
            return ["group:only"]

    old = br.chat_manager
    br.chat_manager = _Mgr()
    try:
        assert br._manager().get_send_targets() == ["group:only"], \
            "替身没被用上 —— _manager() 绕过了模块级符号"
    finally:
        br.chat_manager = old


def test_official_channel_mute_requires_asker_role():
    """官方通道的禁言**必须看「喊话人」的角色** —— 而角色就在群消息事件里。

    2026-10-15：官方通道此前整块没给群管理权限，理由是"官方 API 查不到喊话人在群里的
    角色"。其实官方**更直接**：`GroupMemberAuthor.member_role`（member/admin/owner）
    是必填字段，适配器已带进 `ctx.extra["member_role"]`，一次接口都不用调。
    权限一给，这道闸就必须在：非群主/管理员一律拒绝，**取不到角色也拒绝**。
    （2026-10-12 实测事故：群里另一个号 @ 它喊「全员禁言」，它照做了。）
    """
    br = _load_bridge()
    import asyncio

    # 走 sys.modules 拿桩模块（替身包没有把子模块挂成属性）
    _ob = sys.modules["nonebot.adapters.onebot.v11"]
    # ⚠ 测试桩陷阱：`_load_bridge` 把 `onebot.v11.Bot` 设成了 `object`，
    # 而 `isinstance(任何东西, object)` 恒为真 → 官方连接会被误判成 OneBot 连接。
    # 真实环境里 Bot 是真类，不会这样。这里换成空类，让 isinstance 正常工作。
    # 2026-10-15：**别直接读 `_ob.Bot`** —— 全套跑时，前面某个测试文件可能已经
    # 用自己的桩顶掉了这个模块（桩里不一定有 `Bot`），于是这里 AttributeError，
    # 而单跑这个文件时却是绿的。测试不该依赖"谁先跑"。
    old_onebot_cls = getattr(_ob, "Bot", None)
    _ob.Bot = type("OneBotV11Bot", (), {})

    class _OfficialBot:
        self_id = "BOT-SELF"

    _OfficialBot.__module__ = "nonebot.adapters.qq.bot"
    old_get_bots = br.get_bots

    def _ctx(role):
        c = _Ctx(perms=("group_admin",))
        c.user_id = "U-ASKER"
        c.nickname = "喊话的人"
        c.extra = {"member_role": role}
        return c

    try:
        br.get_bots = lambda: {"1": _OfficialBot()}

        # ① 普通成员喊「全员禁言」→ 必须拒绝
        res = asyncio.run(br.do_action("mute_all", {"group_id": "G-1"}, _ctx("member")))
        assert res["ok"] is False and res["error"] == "asker_not_admin", res

        # ② 取不到角色（老适配器 / 频道事件）→ 同样拒绝，绝不"谁喊都动手"
        res = asyncio.run(br.do_action("mute_all", {"group_id": "G-1"}, _ctx("")))
        assert res["ok"] is False and res["error"] == "asker_role_unknown", res

        # ③ 群主 → 不该被这道闸拦下（后面发不发得出去是另一回事）
        res = asyncio.run(br.do_action("mute_all", {"group_id": "G-1"}, _ctx("owner")))
        assert res.get("error") not in ("asker_not_admin", "asker_role_unknown"), \
            "群主下令被误拦了：%s" % res
    finally:
        br.get_bots = old_get_bots
        if old_onebot_cls is None:
            try:
                del _ob.Bot
            except AttributeError:
                pass
        else:
            _ob.Bot = old_onebot_cls


def test_official_adapter_carries_member_role():
    """源码锁：官方适配器必须把 `author.member_role` 传下去。

    没有它，`ctx.extra["member_role"]` 永远是空串 → 上面那道闸会把**所有人**
    都判成没有权限，群管理等于不可用。
    """
    src = (AI_DIR / "adapters" / "qq_official.py").read_text(encoding="utf-8")
    assert 'getattr(author, "member_role"' in src, "适配器没读 member_role"
    assert '"member_role": member_role' in src, "member_role 没进 ChannelMessage.extra"
    bridge = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    assert '"member_role"' in bridge, "工具上下文没有转发 member_role"


def test_recall_message_picks_connection_by_channel():
    """撤回动作必须按当前通道挑连接。

    旧实现只认 `_qq_bot()`（官方连接）：纯 OneBot 部署上永远 `unsupported_by_channel`，
    混布下又把 QQ 群号当 `group_openid` 传 → **任何通道都撤不了**。
    """
    src = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    body = src[src.index("async def _act_recall_message"):
               src.index("async def _act_mute")]
    # 去掉注释行与函数 docstring 再判 —— docstring 里会引用 `_qq_bot()` 当"以前这么写"的反例
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    first = code.index('"""')
    second = code.index('"""', first + 3)
    code = code[:first] + code[second + 3:]

    assert "_pick_connection(ctx)" in code, "撤回没走共用的挑连接函数"
    assert 'call_api("delete_msg"' in code, "OneBot 侧没有走 delete_msg"
    assert "delete_group_message" in code and "delete_c2c_message" in code, \
        "官方通道路径丢了"
    assert "_qq_bot()" not in code, "又回到只认官方连接了"


if __name__ == "__main__":
    test_media_actions_send_and_report_unsupported()
    test_media_actions_need_permission()
    test_send_to_requires_whitelist()
    test_send_to_needs_channel_support()
    test_fetch_helpers_are_pure_and_safe()
    test_network_actions_block_private_hosts()
    test_compress_image_reports_missing_file()
    test_compress_image_actually_shrinks_a_real_image()
    test_send_buttons_official_and_fallback()
    test_manager_send_targets_default_empty()
    print("OK media actions")
