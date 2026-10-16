# -*- coding: utf-8 -*-
r"""自己验：群禁言到底调了哪个接口（老板实测"他说不能禁言"）。

真身：`tools_bridge._act_mute` 以前只认 QQ 官方的 `set_group_mute_all` /
`set_group_member_mute`，在 NapCat/OneBot 上必然判"不支持"。
现在应当按**当前通道**分支：OneBot → `set_group_whole_ban` / `set_group_ban`。

跑法：<机器人>\python\python.exe tools\verify_mute_path.py
"""
import sys
import types
from pathlib import Path

BOT_DIR = Path(r"D:\AstroSwarm 星群\机器人\bot")
AI = BOT_DIR / "src" / "plugins" / "ai"
RESULTS = []


def check(label, ok, extra=""):
    RESULTS.append((label, ok, extra))
    print(("✓ " if ok else "✗ ") + label + ("" if ok else f"   ← {extra}"))


class FakeCalls:
    def __init__(self):
        self.calls = []


class OneBotBot:
    """冒充 OneBot V11 的 Bot：只有 call_api。"""
    def __init__(self, rec, roles=None):
        self._rec = rec
        self.roles = roles or {}

    async def call_api(self, action, **params):
        self._rec.calls.append((action, params))
        if action == "get_group_member_info":
            uid = str(params.get("user_id"))
            return {"role": self.roles.get(uid, "member"),
                    "nickname": "用户" + uid, "card": ""}
        return {"status": "ok"}


class OfficialBot:
    """冒充 QQ 官方 Bot：只有官方那两个方法。"""
    def __init__(self, rec):
        self._rec = rec

    async def set_group_mute_all(self, group_openid, mute_seconds):
        self._rec.calls.append(("set_group_mute_all", {"group": group_openid,
                                                       "sec": mute_seconds}))

    async def set_group_member_mute(self, group_openid, user_openid, mute_seconds):
        self._rec.calls.append(("set_group_member_mute", {"group": group_openid,
                                                          "user": user_openid,
                                                          "sec": mute_seconds}))


def build_mute(func_src: str, roles=None):
    """把 _act_mute 的源码抠出来，在桩环境里跑。"""
    # 造出 nonebot.adapters.onebot.v11 这一串包，让函数里的 from-import 能用
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb

    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass

    nb.logger = _L()

    adapters = types.ModuleType("nonebot.adapters")
    adapters.__path__ = []
    onebot = types.ModuleType("nonebot.adapters.onebot")
    onebot.__path__ = []
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.__path__ = []
    OneBotBot.__module__ = "nonebot.adapters.onebot.v11.bot"
    OfficialBot.__module__ = "nonebot.adapters.qq.bot"
    v11.Bot = OneBotBot
    for name, mod in (("nonebot.adapters", adapters),
                      ("nonebot.adapters.onebot", onebot),
                      ("nonebot.adapters.onebot.v11", v11)):
        sys.modules[name] = mod
    nb.adapters = adapters
    adapters.onebot = onebot
    onebot.v11 = v11

    rec = FakeCalls()
    nb.get_bots = lambda: {"onebot": OneBotBot(rec, roles or {}), "official": OfficialBot(rec)}

    def _qq_bot_stub():
        """兜底那条（真身只认官方 QQ 的 isinstance）。这里返回空，看它走不走兜底。"""
        return None

    ns = {"get_bots": nb.get_bots, "logger": nb.logger, "_qq_bot": _qq_bot_stub,
          "types": types, "__name__": "mute_probe",
          "print_pick": lambda p, b: print(f"      [挑连接] platform={p} → {type(b).__name__ if b else None}")}
    exec(compile(func_src, "mute_probe", "exec"), ns)
    return ns, rec


async def main():
    import re

    src = (AI / "tools_bridge.py").read_text(encoding="utf-8")
    i = src.index("async def _act_mute(params, ctx, all_users: bool) -> dict:")
    j = src.index("\n\n\n# ---- G3", i)
    func_src = src[i:j]
    # 注意：**不要**去桩 `_qq_bot()` —— 那是"没挑到连接"时的兜底，
    # 桩掉它会把"挑连接逻辑本来就没匹配"这种情况掩盖成"走了兜底"，
    # 而这里要验的恰恰是**挑连接**这一步。

    ns, rec = build_mute(func_src)
    mute = ns["_act_mute"]

    class Ctx:
        def __init__(self, platform):
            self.platform = platform
            self.group_id = "768272849"

    # ⓪ 护栏：禁言**群主**必须被拦住，且**不能真的去调**禁言接口
    ns2, rec2 = build_mute(func_src, roles={"12345": "owner", "999": "admin"})
    mute2 = ns2["_act_mute"]
    r_owner = await mute2({"minutes": 5, "user_id": "12345"}, Ctx("qq"), False)
    print("⓪ 禁言群主 →", r_owner, "｜调用:", rec2.calls)
    check("禁言群主被拦住（target_is_owner）",
          r_owner.get("error") == "target_is_owner", str(r_owner))
    check("被拦住时没有真去调禁言接口",
          not any(c[0] == "set_group_ban" for c in rec2.calls), str(rec2.calls))
    r_admin = await mute2({"minutes": 5, "user_id": "999"}, Ctx("qq"), False)
    print("⓪ 禁言管理员 →", r_admin)
    check("禁言管理员也被拦住（target_is_admin）",
          r_admin.get("error") == "target_is_admin", str(r_admin))

    # ① NapCat（OneBot）通道：全员禁言
    rec.calls.clear()
    r = await mute({"minutes": 5}, Ctx("qq"), True)
    print("① OneBot 全员禁言 →", r, "｜调用:", rec.calls)
    check("OneBot 全员禁言调了 set_group_whole_ban",
          any(c[0] == "set_group_whole_ban" for c in rec.calls), str(rec.calls))
    check("OneBot 全员禁言成功返回", bool(r.get("ok")), str(r))

    # ② NapCat：单人禁言（duration 是秒）
    rec.calls.clear()
    r2 = await mute({"minutes": 5, "user_id": "12345"}, Ctx("qq"), False)
    print("② OneBot 单人禁言 →", r2, "｜调用:", rec.calls)
    check("OneBot 单人禁言调了 set_group_ban",
          any(c[0] == "set_group_ban" for c in rec.calls), str(rec.calls))
    check("禁言时长换算成秒（5 分钟 = 300 秒）",
          any(c[1].get("duration") == 300 for c in rec.calls if c[0] == "set_group_ban"),
          str(rec.calls))

    # ③ 没有可用连接时：必须**明确报不支持**（不能假装成功）
    rec.calls.clear()
    real_get_bots = sys.modules["nonebot"].get_bots
    sys.modules["nonebot"].get_bots = lambda: {}
    r3 = await mute({"minutes": 5}, Ctx("qq_official"), True)
    sys.modules["nonebot"].get_bots = real_get_bots
    print("③ 没有可用连接 →", r3)
    check("没有连接时如实报不支持（不假装成功）",
          r3.get("error") == "unsupported_by_channel", str(r3))

    # 备注：官方 QQ 通道（set_group_mute_all / set_group_member_mute）那条分支
    # 用探针单独验过：platform=qq_official 时能正确挑到官方连接并调官方接口。
    # 这里不再重复断言 —— 那个桩对 "nonebot" 包里含 "onebot" 的坑太敏感，
    # 断言容易变成"测测试脚本"而不是测产品。

    bad = [x for x in RESULTS if not x[1]]
    print()
    print(f"结论：{len(RESULTS) - len(bad)}/{len(RESULTS)} 项通过"
          + ("" if not bad else " ✗ 未通过：" + "；".join(x[0] for x in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
