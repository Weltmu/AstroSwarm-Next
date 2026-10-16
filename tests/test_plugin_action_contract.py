# -*- coding: utf-8 -*-
"""契约防线：插件里的"动作 / 工具"必须自洽（2026-10-12 一整天踩坑总结）。

三条检查（静态，不需要 nonebot）：
  ① `_ACTIONS` 表里引用到的**所有函数名**（含 lambda 里转指的那些），
     都必须在该字典**之前**定义过 —— 今天就是"函数写在表后面"导致 NameError，
     整个 ai 插件加载失败，表现是"连上了但不回话"。
  ② 给模型看的工具（`_tool("名字", …)`）都必须能在 `_ACTIONS` 里找到实现。
  ③ `_ACTIONS` 里有、但没给模型看的动作，必须显式写进"内部动作"名单，
     逼作者每次加动作都表个态（而不是默认藏着）。
"""
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AI = REPO / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
TOOLS = AI / "tools_bridge.py"

# 故意不给模型看的内部动作（新加动作要显式写进来）
INTERNAL_ACTIONS = {
    "send_message", "recall_message", "mute_user", "mute_all",
    "http_request", "data_write", "data_read",
    "send_image", "send_file", "send_voice", "send_music_card", "send_buttons",
    "send_to", "send_to_platform", "send_file_to_platform", "my_platforms",
    "list_friend_requests", "approve_friend",
    "compress_image", "download_file",
    "expand_link", "fetch_text", "reminder_add", "memory_write", "memory_read",
}


def _split_actions_table() -> tuple:
    """返回 (表之前的源码, _ACTIONS 字典体的源码, 表整体之后的源码)。"""
    src = TOOLS.read_text(encoding="utf-8")
    at = src.index("\n_ACTIONS = {")
    head = src[:at]
    body_start = src.index("{", at)
    depth, i = 0, body_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return head, src[body_start:i + 1], src[i + 1:]


def _action_entries() -> dict:
    """{动作名: 这一行引用的所有函数名}（含 lambda 里转指的函数）。"""
    _, body, _ = _split_actions_table()
    out = {}
    for line in body.splitlines():
        m = re.match(r'\s+"([a-z_]+)":\s*(.+?),?\s*$', line)
        if not m:
            continue
        name, rhs = m.group(1), m.group(2)
        out[name] = set(re.findall(r"([A-Za-z_]\w*)\s*\(", rhs)) | {
            w for w in re.findall(r"\b(_act_\w+|_act\w*)\b", rhs)}
    return out


def _defined_names(src: str) -> dict:
    tree = ast.parse(src)
    defs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, node.lineno)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    defs.setdefault(tgt.id, node.lineno)
    return defs


def test_actions_defined_before_the_table():
    """① 表里引用到的函数，定义必须在表之前（否则 NameError → 整个插件加载失败）。"""
    head, _, _ = _split_actions_table()
    entries = _action_entries()
    assert entries, "没解析出 _ACTIONS 的条目，检查解析"
    missing = []
    for action, funcs in entries.items():
        for fn in funcs:
            if fn in ("lambda", "dict", "str", "int", "len", "bool"):
                continue
            if not re.search(rf"def {re.escape(fn)}\(", head):
                missing.append(f"{action} → {fn}")
    assert not missing, (
        "这些动作引用的函数，定义在 _ACTIONS 之后（会 NameError、插件整包加载失败）：\n"
        + "\n".join("  " + m for m in missing))


def test_every_exposed_tool_has_an_action():
    """② 给模型看的每个工具，都得在 _ACTIONS 里有实现。"""
    src = TOOLS.read_text(encoding="utf-8")
    exposed = set(re.findall(r'_tool\("([a-z_]+)"', src))
    assert exposed, "没解析出任何 _tool(...) 声明"
    actions = set(_action_entries())
    missing = sorted(exposed - actions)
    assert not missing, ("这些工具声明给了模型，但 _ACTIONS 里没有实现（模型调了必报错）：\n"
                         + "\n".join("  " + n for n in missing))


def test_internal_actions_are_declared():
    """③ 没给模型看的动作，必须显式写进 INTERNAL_ACTIONS 白名单。"""
    actions = set(_action_entries())
    exposed = set(re.findall(r'_tool\("([a-z_]+)"', TOOLS.read_text(encoding="utf-8")))
    undeclared = sorted(actions - exposed - INTERNAL_ACTIONS)
    assert not undeclared, (
        "这些动作既没给模型看、也没写进 INTERNAL_ACTIONS（新加动作请明确表个态）：\n"
        + "\n".join("  " + n for n in undeclared))


def test_plugin_files_all_compile():
    """④ 全插件树语法体检（今天我自己带进去两次语法错）。"""
    import py_compile

    bad = []
    for f in AI.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        try:
            py_compile.compile(str(f), doraise=True)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{f.name}: {str(e).splitlines()[-1][:80]}")
    assert not bad, "有文件编译不过：\n" + "\n".join("  " + b for b in bad)
