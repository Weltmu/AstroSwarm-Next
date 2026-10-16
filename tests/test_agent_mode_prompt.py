# -*- coding: utf-8 -*-
"""防线测试：Agent 行为规则必须真的进了系统提示词（老板 2026-10-12 "做！"）。

背景：老板要的是"像 codex / deepseek harness 那样会调工具的机器人"。
第一步的做法是给提示词加【先把事办了，再说话】这一段 —— 这条测试保证：
  ① 规则函数存在、可被配置关掉（agent_mode_enabled）；
  ② 规则确实拼进了 `get_system_prompt`（不然写了等于没写）；
  ③ 规则里点名的是**真实工具名**、且含"别嘴上答应不动手""别编成功"这类硬约束。
"""
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AI = REPO / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
CHAT = AI / "chat.py"
MANAGER = AI / "manager.py"


def _chat_src() -> str:
    return CHAT.read_text(encoding="utf-8")


def test_agent_rules_exist_and_are_wired():
    src = _chat_src()
    assert "def get_agent_rules" in src, "没有 get_agent_rules()"
    # 规则文本存在
    assert "AGENT_RULES" in src
    # 拼进系统提示词（取 get_system_prompt 定义到下一个顶层 def/async def 之间的正文）
    m = re.search(r"\ndef get_system_prompt\(.*?\n(?=\s*(?:async )?def )", src, re.S)
    assert m, "解析 system prompt 失败"
    body = m.group(0)
    assert "get_agent_rules()" in body, "系统提示词里没拼上 agent 规则（写了等于没写）"
    assert "platform_context(msg)" in body, "场景信息不该被挤掉"


def test_agent_rules_have_hard_constraints():
    src = _chat_src()
    for key in ("绝对不要嘴上答应却不动手", "最多 5 步", "绝不许编成功", "别为了显得能干硬去调工具"):
        assert key in src, f"缺少硬约束：{key}"


def test_agent_rules_reference_real_tools():
    src = _chat_src()
    rules = src[src.index("AGENT_RULES"):src.index("def get_agent_rules")]
    runtime = (REPO / "src" / "qbotmanager" / "core" / "agent" / "runtime.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'name="([a-z][a-z0-9_]{2,})"', runtime))
    mentioned = set(re.findall(r"`([a-z][a-z0-9_]{3,})`", rules))
    missing = {n for n in mentioned if n not in registered and not n.startswith(("platform", "text", "qq"))}
    assert not missing, f"规则里点名的工具并不存在：{sorted(missing)}；真实工具：{sorted(registered)}"
    assert "send_to_my_platform" in mentioned, "规则里没点名跨平台发送工具"


def test_agent_mode_toggle_readable():
    src = MANAGER.read_text(encoding="utf-8")
    assert "def is_agent_mode_enabled" in src, "manager 没有 agent_mode 开关"
    assert 'self._data.get("agent_mode_enabled", True)' in src, "agent 模式默认应当是**开**"
    # 规则函数要能读开关
    assert "is_agent_mode_enabled" in _chat_src()


def test_chat_module_still_parses():
    ast.parse(_chat_src())
    ast.parse(MANAGER.read_text(encoding="utf-8"))
