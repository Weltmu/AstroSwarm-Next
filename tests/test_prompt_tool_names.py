# -*- coding: utf-8 -*-
"""防线测试：**提示词里提到的工具名，必须真的存在**。

来历（2026-10-12）：跨平台发送一直"发不出去"，查到最后是提示词让模型调
`send_to_platform`，而真实工具叫 `send_to_my_platform` —— **差一个词**，
模型找不到这个工具，只能回"我没法往QQ发消息"。功能没坏，是**契约对不上**。

工具名从三处收集（都是真实注册的地方）：
  · `core/agent/runtime.py`（主运行时：跨平台发送等）
  · `core/agent_profile.py` + `liqinghan/tools.py`（档案插件自己的工具）
  · 各插件的 `ToolSpec(name=...)`
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "qbotmanager"
PLUGINS = SRC / "assets" / "plugins"

TOOL_SOURCES = [
    SRC / "core" / "agent" / "runtime.py",
    SRC / "core" / "agent_profile.py",
    PLUGINS / "liqinghan" / "tools.py",
]
NAME_RE = re.compile(r'name="([a-z][a-z0-9_]{2,})"')

# 提示词里"这是让你调的工具名"的几种写法
PROMPT_PATTERNS = [
    re.compile(r"用\s*`?(?P<name>[a-z][a-z0-9_]{3,})`?\s*(?:工具|动作)"),
    re.compile(r"调(?:用)?\s*`?(?P<name>[a-z][a-z0-9_]{3,})`?\s*(?:工具|动作)"),
    re.compile(r"^\s*\|\s*(?P<name>[a-z][a-z0-9_]{3,})\s*\|", re.MULTILINE),
]

NOT_TOOLS = {
    "reply", "json", "markdown", "true", "false", "none", "null", "ok", "error",
    "text", "image", "voice", "file", "url", "http", "https", "qq", "wechat",
    "platform", "group", "private", "c2c", "appid", "token", "secret", "user",
}
# 内部动作名/权限表里的键：形如 "send_to_platform": "send_message"
MAPPING_LINE = re.compile(r'^\s*"[a-z_]+"\s*:')


def registered_tool_names() -> set:
    names = set()
    for path in TOOL_SOURCES:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            names |= set(NAME_RE.findall(text))
            # 档案插件用自己的小工厂声明工具：_f("remember", "说明", {...}, handler)
            names |= set(re.findall(r'_f\(\s*"([a-z][a-z0-9_]{2,})"', text))
    for f in PLUGINS.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        names |= set(re.findall(r"ToolSpec\(\s*name=\"([a-z][a-z0-9_]{2,})\"", text))
        names |= set(re.findall(r'_f\(\s*"([a-z][a-z0-9_]{2,})"', text))
    return names


def prompt_mentioned_names() -> dict:
    found = {}
    for f in PLUGINS.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for pat in PROMPT_PATTERNS:
            for m in pat.finditer(text):
                name = m.group("name")
                if name in NOT_TOOLS:
                    continue
                found.setdefault(name, set()).add(f.name)
    return found


def test_prompt_tool_names_exist():
    registered = registered_tool_names()
    assert registered, "没解析出任何工具名，检查正则或文件位置"
    mentioned = prompt_mentioned_names()
    missing = {n: sorted(fs) for n, fs in mentioned.items() if n not in registered}
    assert not missing, (
        "提示词里提到、但运行时并没有注册的工具（模型会找不到、只能拒答）：\n"
        + "\n".join(f"  {n}  <- {', '.join(fs)}" for n, fs in sorted(missing.items()))
        + "\n真实工具名：" + ", ".join(sorted(registered))
    )


def test_cross_platform_send_uses_real_name():
    """点名今天出事的那个：提示词里必须写 send_to_my_platform。

    允许保留 `send_to_platform` 的地方只有**内部实现**：
    函数名 `_act_send_to_platform`、映射表键 `"send_to_platform": ...`、注释与文档。
    """
    assert "send_to_my_platform" in registered_tool_names()
    bad = []
    for f in PLUGINS.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "send_to_platform" not in line or "my_platform" in line:
                continue
            stripped = line.strip()
            if (stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'")
                    or stripped.startswith("def ") or stripped.startswith("async def ")
                    or "_act_" in line or "_ACTIONS" in line or MAPPING_LINE.match(line)
                    or '"action"' in line or "PERM" in line):
                continue
            bad.append(f"{f.name}:{n}: {stripped[:90]}")
    assert not bad, "还有地方把工具名写成不存在的 send_to_platform：\n  " + "\n  ".join(bad)
