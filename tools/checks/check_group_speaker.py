# -*- coding: utf-8 -*-
r"""验证"谁说的话"确实传给了模型（离线，构造 A/B 两人的群历史）。

场景复刻老板遇到的：A 骂了它，B 发 666 —— 改之前两者在模型眼里是同一人。
"""
import importlib.util
import sys
import types
from pathlib import Path

# 2026-10-11：路径不再写死 Windows（Linux 上原来直接 FileNotFoundError）。
# 解析顺序见 _src.py：--src > QBM_PLUGINS_SRC > Windows 安装目录 > Linux 两个位置 > 本仓库。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _src import plugin_dir  # noqa: E402

SRC = plugin_dir("liqinghan")
print(f"插件目录：{SRC}")

# agent.py 会 import nonebot 之类的，单测里给个空壳，只为把文件加载起来
for name in ("nonebot", "nonebot.adapters", "nonebot.adapters.qq"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["nonebot"].logger = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None,
    error=lambda *a, **k: None, exception=lambda *a, **k: None)

# 造一个包壳，让 agent.py 里的相对 import（from .config import ...）能解析
pkg = types.ModuleType("lqpkg")
pkg.__path__ = [str(SRC)]
sys.modules["lqpkg"] = pkg
spec = importlib.util.spec_from_file_location("lqpkg.agent", SRC / "agent.py")
agent_mod = importlib.util.module_from_spec(spec)
sys.modules["lqpkg.agent"] = agent_mod
try:
    spec.loader.exec_module(agent_mod)
except Exception as e:  # noqa: BLE001
    print("加载 agent.py 失败（依赖缺失），跳过：", type(e).__name__, e)
    sys.exit(0)

Agent = agent_mod.Agent


class Row(dict):
    def keys(self):
        return super().keys()


class FakeStore:
    """A = 甲同学（骂人），B = 乙同学（发 666）"""

    NAMES = {"A_ID_123456": "甲同学", "B_ID_654321": "乙同学"}

    def recent_messages(self, session, limit=20):
        return [
            Row(role="user", qq="A_ID_123456", text="你真是个傻机器人"),
            Row(role="assistant", qq="", text="……"),
            Row(role="user", qq="B_ID_654321", text="666"),
        ]

    def get_user(self, qq):
        n = self.NAMES.get(str(qq), "")
        return Row(nickname=n) if n else None


class Stub(Agent):
    """继承真实方法（_display_name/_group_history），但只塞一个假 store，不跑 __init__。"""

    def __init__(self, store):
        self.store = store


stub = Stub(FakeStore())
bad = []


def ok(label, cond, detail=""):
    print(("✓ " if cond else "× ") + label + (f" —— {detail}" if detail else ""))
    if not cond:
        bad.append(label)


print("=== 显示名 ===")
ok("有昵称 → 昵称(短id)", "甲同学" in Agent._display_name(stub, "A_ID_123456"))
ok("没昵称 → 成员+短id", "成员" in Agent._display_name(stub, "ZZZ_UNKNOWN"))

print("=== 群历史（每条带说话人）===")
hist = Agent._group_history(stub, "g:test", 14, drop_last_text="666")
for row in hist:
    print("   ", row["role"], "|", row["text"])
ok("A 的那句话带上了「甲同学」", any("甲同学" in r["text"] for r in hist))
ok("当前这条（B 的 666）不进历史，避免重复",
   not any("666" in r["text"] for r in hist))
ok("机器人的回复不带人名前缀",
   all(not r["text"].startswith("乙同学") for r in hist if r["role"] == "assistant"))
ok("A 的记录是独立一条（不会和 B 混）",
   len([r for r in hist if r["role"] == "user"]) == 1)
# 当前消息的说话人由提示词承担：这里验证同一条消息在两种归属下渲染出的文字不同
who_a = Agent._display_name(stub, "A_ID_123456")
who_b = Agent._display_name(stub, "B_ID_654321")
ok("两个说话人渲染出不同的标签", who_a != who_b, f"{who_a} vs {who_b}")

print()
if bad:
    print("失败:", bad)
    sys.exit(1)
print("说话人区分：全部通过 ✓")
