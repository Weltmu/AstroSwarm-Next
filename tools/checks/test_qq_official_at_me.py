# -*- coding: utf-8 -*-
r"""官方 QQ「群里 @ 它」判定：修完必须过这几条。

背景：老板 10-10 晚遇到"官方机器人又不说话了" —— 群状态文件里 last_at_me=false，
说明 @ 判定没命中。根因是 QQ 官方全量群事件的 mention 段不一定带 is_bot/is_you
标记，原来只认标记就漏判。这里用构造事件把各种情形钉死。

直跑：python tests\test_qq_official_at_me.py
"""
import importlib.util
import sys
from pathlib import Path

# 2026-10-11：这个文件从 tests/ 搬到了 tools/checks/，`parent.parent` 从"仓库根"
# 变成了 tools/ → 加载 group_policy.py 直接 FileNotFoundError。改用统一的解析器。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _src import repo_root  # noqa: E402

REPO = repo_root()

# 直接按文件加载 group_policy：走包导入会连带 __init__.py（要 nonebot），
# 而这个模块本身只用标准库，单测里没必要把整个插件栈拉起来。
_POLICY = REPO / "src" / "qbotmanager" / "assets" / "plugins" / "ai" / "group_policy.py"
if not _POLICY.is_file():
    raise SystemExit(f"找不到被测插件文件：{_POLICY}\n"
                     f"（用 --repo <仓库根> 或 QBM_REPO=<仓库根> 指定）")
_spec = importlib.util.spec_from_file_location("qbm_group_policy", _POLICY)
group_policy = importlib.util.module_from_spec(_spec)
sys.modules["qbm_group_policy"] = group_policy
_spec.loader.exec_module(group_policy)

BOT_ID = "12640943656695704951"
OTHER_ID = "AEA7A7F118F25C5ABC5431BCECF98A0D"


class Seg:
    def __init__(self, type_, **data):
        self.type = type_
        self.data = data


class Mention:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class FakeEvent:
    def __init__(self, segs=(), to_me=False, mentions=(), self_id=""):
        self._segs = list(segs)
        self.to_me = to_me
        self.mentions = list(mentions)
        self.self_id = self_id

    def get_message(self):
        return list(self._segs)


def main() -> int:
    bad = []

    def check(label, got, want=True):
        ok = bool(got) is bool(want)
        print(("✓ " if ok else "× ") + label + f"（得到 {got}）")
        if not ok:
            bad.append(label)

    # 1) 老情形：mention 带 is_bot 标记
    ev = FakeEvent([Seg("mention_user", user_id=OTHER_ID, is_bot=True), Seg("text", text="你好")])
    check("@ 的 mention 带 is_bot → 认", group_policy.is_at_me(ev, bot_id=BOT_ID))

    # 2) 新情形（老板遇到的）：mention 只有 user_id，没有标记，但 user_id 就是机器人自己
    ev = FakeEvent([Seg("mention_user", user_id=BOT_ID), Seg("text", text="你好")])
    check("@ 的 mention 只有 user_id=机器人自己（无标记）→ 认", group_policy.is_at_me(ev, bot_id=BOT_ID))

    # 3) @ 的是别人 → 不认
    ev = FakeEvent([Seg("mention_user", user_id=OTHER_ID), Seg("text", text="你好")])
    check("@ 的是别人 → 不认", group_policy.is_at_me(ev, bot_id=BOT_ID), want=False)

    # 4) @-专用事件（GROUP_AT_MESSAGE_CREATE）：to_me 直接 True
    ev = FakeEvent([Seg("text", text="在吗")], to_me=True)
    check("@-专用事件 to_me=True → 认", group_policy.is_at_me(ev, bot_id=BOT_ID))

    # 5) 只在 event.mentions 里带 is_you
    ev = FakeEvent([Seg("text", text="在吗")], mentions=[Mention(id=OTHER_ID, is_you=True)])
    check("mentions 里 is_you → 认", group_policy.is_at_me(ev, bot_id=BOT_ID))

    # 6) 只在 event.mentions 里带机器人 id（无标记）
    ev = FakeEvent([Seg("text", text="在吗")], mentions=[Mention(id=BOT_ID)])
    check("mentions 里 id=机器人自己（无标记）→ 认", group_policy.is_at_me(ev, bot_id=BOT_ID))

    # 7) 空消息不该崩
    ev = FakeEvent([])
    check("空消息不崩、判为不认", group_policy.is_at_me(ev, bot_id=BOT_ID), want=False)

    # 8) mentions_other：@ 别人时仍要认出来（别人 @ 别人不抢话）；
    #    但 @ 机器人自己（哪怕没标记，只靠 id 认出来）**不算 @ 别人** ——
    #    这条是 20:27/20:47 日志里“视为与机器人无关，不回复”的真凶。
    ev = FakeEvent([Seg("mention_user", user_id=OTHER_ID), Seg("text", text="你好")])
    check("mentions_other：@ 别人 → True", group_policy.mentions_other(ev, bot_id=BOT_ID))
    ev = FakeEvent([Seg("mention_user", user_id=BOT_ID), Seg("text", text="你好")])
    check("mentions_other：@ 我自己（无标记，靠 id 认）→ False",
          group_policy.mentions_other(ev, bot_id=BOT_ID), want=False)
    ev = FakeEvent([Seg("mention_user", user_id=BOT_ID, is_bot=True), Seg("text", text="你好")])
    check("mentions_other：@ 我自己（带 is_bot）→ False",
          group_policy.mentions_other(ev, bot_id=BOT_ID), want=False)
    ev = FakeEvent([Seg("text", text="普通消息")])
    check("mentions_other：没 @ 人 → False", group_policy.mentions_other(ev, bot_id=BOT_ID), want=False)

    print()
    if bad:
        print("失败:", bad)
        return 1
    print("官方 QQ @ 判定全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
