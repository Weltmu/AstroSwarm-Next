# -*- coding: utf-8 -*-
r"""用**真实模块**验群禁言（不再抠源码拼桩，那种验法本身太脆）。

做法：进机器人目录、nonebot.init()、导入真的 `src.plugins.ai.tools_bridge`，
把 get_bots 换成假连接，直接调真身 `_act_mute`。

跑法：<机器人>\python\python.exe tools\verify_mute_real.py
"""
import asyncio
import sys
from pathlib import Path

BOT_DIR = Path(r"D:\AstroSwarm 星群\机器人\bot")
sys.path.insert(0, str(BOT_DIR))
import os  # noqa: E402

os.chdir(BOT_DIR)
os.environ["LOCALSTORE_USE_CWD"] = "true"   # 和机器人本体一样，绕开 localstore 的调用者检测

RESULTS = []


def check(label, ok, extra=""):
    RESULTS.append((label, ok, extra))
    print(("✓ " if ok else "✗ ") + label + ("" if ok else f"   ← {extra}"))


async def main():
    import nonebot

    nonebot.init()
    # 让 nonebot 自己加载插件（localstore 要靠调用者插件判定目录，手写 import 会报
    # Cannot detect caller plugin）
    nonebot.load_plugin("src.plugins.ai")
    import src.plugins.ai.tools_bridge as tb  # noqa: E402

    class Rec:
        def __init__(self):
            self.calls = []

    class OneBotBot:
        __module__ = "nonebot.adapters.onebot.v11.bot"

        def __init__(self, rec, roles):
            self._rec = rec
            # 默认把"喊话人"当成管理员（真实场景：老板就是群主/管理员）。
            # 用例⑧ 会用 6885483 喊话 —— 他不在表里，就是普通成员，正好验"路人被拒"。
            self._roles = {"1000000002": "admin", **(roles or {})}

        async def call_api(self, action, **params):
            self._rec.calls.append((action, params))
            if action == "get_group_member_info":
                uid = str(params.get("user_id"))
                return {"role": self._roles.get(uid, "member"),
                        "nickname": "用户" + uid, "card": ""}
            return {"status": "ok", "retcode": 0}

    class Ctx:
        def __init__(self, platform="qq", user_id="1000000002"):
            self.platform = platform
            self.group_id = "768272849"
            self.user_id = user_id

    fatal = []

    # ① 禁言群主 → 必须被拦住，且不能真调禁言接口
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {"12345": "owner", "999": "admin"})}
    r = await tb._act_mute({"minutes": 5, "user_id": "12345"}, Ctx(), False)
    print("① 禁言群主 →", r, "｜调用:", rec.calls)
    check("禁言群主被拦住（target_is_owner）", r.get("error") == "target_is_owner", str(r))
    check("拦住后没有真调 set_group_ban",
          not any(c[0] == "set_group_ban" for c in rec.calls), str(rec.calls))

    # ② 禁言管理员 → 也该拦住
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {"999": "admin"})}
    r2 = await tb._act_mute({"minutes": 5, "user_id": "999"}, Ctx(), False)
    print("② 禁言管理员 →", r2)
    check("禁言管理员被拦住（target_is_admin）", r2.get("error") == "target_is_admin", str(r2))

    # ③ 禁言普通成员 → 正常调 set_group_ban，时长=秒
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {})}
    r3 = await tb._act_mute({"minutes": 5, "user_id": "12345"}, Ctx(), False)
    print("③ 禁言普通成员 →", r3, "｜调用:", rec.calls)
    check("普通成员正常禁言", bool(r3.get("ok")), str(r3))
    check("调的是 set_group_ban 且 300 秒",
          any(c[0] == "set_group_ban" and c[1].get("duration") == 300 for c in rec.calls),
          str(rec.calls))

    # ④ 全员禁言 → set_group_whole_ban，并登记自动解除
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {})}
    r4 = await tb._act_mute({"minutes": 1}, Ctx(), True)
    print("④ 全员禁言 →", r4, "｜调用:", rec.calls)
    check("全员禁言走 set_group_whole_ban",
          any(c[0] == "set_group_whole_ban" for c in rec.calls), str(rec.calls))
    check("返回里说明会自动解除", "自动解除" in str(r4.get("note", "")), str(r4))

    # ⑧ 普通成员喊「全员禁言」→ 必须拒绝
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {"3636775360": "admin"})}
    r8 = await tb._act_mute({"minutes": 10}, Ctx("qq", user_id="6885483"), True)
    print("⑧ 路人喊全员禁言 →", r8, "｜调用:", rec.calls)
    check("不是管理员喊的必须拒绝（asker_not_admin）",
          r8.get("error") == "asker_not_admin", str(r8))
    check("拒绝时没有真去禁言", not any(c[0] == "set_group_whole_ban" for c in rec.calls),
          str(rec.calls))

    # ⑨ 群主喊 → 放行
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {"1000000002": "owner"})}
    r9 = await tb._act_mute({"minutes": 10}, Ctx("qq", user_id="1000000002"), True)
    print("⑨ 群主喊全员禁言 →", r9)
    check("群主喊的放行", bool(r9.get("ok")), str(r9))

    # ⑥ 解除禁言：minutes=0 必须真去**解除**，不能再禁上
    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {})}
    r6 = await tb._act_mute({"minutes": 0}, Ctx(), True)
    print("⑥ 全员解除 →", r6, "｜调用:", rec.calls)
    check("minutes=0 走解除（enable=False）",
          any(c[0] == "set_group_whole_ban" and c[1].get("enable") is False for c in rec.calls),
          str(rec.calls))
    check("解除不排自动解禁", "自动解除" not in str(r6.get("note", "")), str(r6))

    rec = Rec()
    tb.get_bots = lambda: {"b": OneBotBot(rec, {"12345": "owner"})}
    r7 = await tb._act_mute({"minutes": 0, "user_id": "12345"}, Ctx(), False)
    print("⑦ 单人解除（对方是群主）→", r7, "｜调用:", rec.calls)
    check("单人解除 duration=0",
          any(c[0] == "set_group_ban" and c[1].get("duration") == 0 for c in rec.calls),
          str(rec.calls))
    check("解除不该被身份护栏拦住", r7.get("error") not in ("target_is_owner", "target_is_admin"),
          str(r7))

    # ⑤ 没有连接 → 如实说（不假装成功）
    tb.get_bots = lambda: {}
    tb._qq_bot = lambda: None
    r5 = await tb._act_mute({"minutes": 5}, Ctx("qq_official"), True)
    print("⑤ 没有连接 →", r5)
    check("没有连接时如实报不支持", r5.get("error") == "unsupported_by_channel", str(r5))

    bad = [x for x in RESULTS if not x[1]]
    print()
    print(f"结论：{len(RESULTS) - len(bad)}/{len(RESULTS)} 项通过"
          + ("" if not bad else " ✗ 未通过：" + "；".join(x[0] for x in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
