# -*- coding: utf-8 -*-
r"""用真实模块验"群身份"（老板：他看不见群管理或群主是谁）。

真的 `_group_roles` 应当说出：我在本群是什么、说话的人是什么、群主和管理员是谁。
跑法：<机器人>\python\python.exe tools\verify_group_roles.py
"""
import asyncio
import os
import sys
from pathlib import Path

BOT_DIR = Path(r"D:\AstroSwarm 星群\机器人\bot")
sys.path.insert(0, str(BOT_DIR))
os.chdir(BOT_DIR)
os.environ["LOCALSTORE_USE_CWD"] = "true"

RESULTS = []


def check(label, ok, extra=""):
    RESULTS.append((label, ok, extra))
    print(("✓ " if ok else "✗ ") + label + ("" if ok else f"   ← {extra}"))


class FakeBot:
    self_id = "3636775360"

    async def call_api(self, action, **params):
        if action == "get_group_member_info":
            uid = str(params.get("user_id"))
            role = {"3636775360": "admin", "1000000002": "owner", "888": "member"}.get(uid, "member")
            return {"role": role, "nickname": {"1000000002": "清秋"}.get(uid, "某人"),
                    "card": ""}
        if action == "get_group_member_list":
            return [{"user_id": 1000000002, "nickname": "清秋", "role": "owner"},
                    {"user_id": 3636775360, "nickname": "可奈", "role": "admin"},
                    {"user_id": 888, "nickname": "路人", "role": "member"}]
        return {}


async def main():
    import nonebot

    nonebot.init()
    nonebot.load_plugin("src.plugins.ai")
    import src.plugins.ai.adapters.qq as qq

    line = await qq._group_roles(FakeBot(), "768272849", "1000000002")
    print("群身份一行 →", line)
    check("说出了我在本群是管理员", "你在本群是管理员" in line, line)
    check("说出了说话的人是群主", "群主" in line and "正跟你说话的他" in line, line)
    check("列出了群主是谁", "清秋(1000000002)" in line, line)
    check("列出了管理员名单", "管理员=" in line and "可奈(3636775360)" in line, line)
    check("带上了「别对群主下手」的提醒", "不能被管理员禁言" in line, line)

    # 普通成员说话时不强调身份，但仍能知道群主是谁
    line2 = await qq._group_roles(FakeBot(), "768272849", "888")
    print("普通成员那条 →", line2)
    check("普通成员不标身份但群主仍在", "群主=清秋(1000000002)" in line2, line2)

    bad = [x for x in RESULTS if not x[1]]
    print()
    print(f"结论：{len(RESULTS) - len(bad)}/{len(RESULTS)} 项通过"
          + ("" if not bad else " ✗ 未通过：" + "；".join(x[0] for x in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
