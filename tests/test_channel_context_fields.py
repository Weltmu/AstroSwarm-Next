# -*- coding: utf-8 -*-
"""静态检查：适配器给 ChannelContext 传的字段，数据类里必须都有。

2026-10-03 真实事故：适配器传了 `send_to_target=`，但 `ChannelContext` 忘了声明这个
字段 → 消息一进来就 `TypeError: unexpected keyword argument` → 表现是
"QQ 显示已连接、发消息完全没反应"（而且插件加载是成功的，光看启动日志看不出来）。

这条用例把"适配器用到的字段 ⊆ 数据类字段"焊死，改适配器时忘了同步 platform.py
会立刻红。
"""
import ast
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load_platform():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ai_platform_fields", AI_DIR / "platform.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_platform_fields"] = mod
    spec.loader.exec_module(mod)
    return mod


def _context_kwargs(path: Path) -> list:
    """把文件里所有 ChannelContext(...) 调用的关键字参数名抠出来。"""
    # utf-8-sig：插件里有文件带 BOM，用 utf-8 读会 SyntaxError
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "ChannelContext":
            continue
        for kw in node.keywords:
            if kw.arg:
                found.append((kw.arg, node.lineno))
    return found


def test_adapter_kwargs_exist_on_dataclass():
    plat = _load_platform()
    fields = {f.name for f in dataclasses.fields(plat.ChannelContext)}
    bad = []
    checked = 0
    for path in sorted(AI_DIR.rglob("*.py")):
        for name, lineno in _context_kwargs(path):
            checked += 1
            if name not in fields:
                bad.append(f"{path.name}:{lineno} → {name}")
    assert checked >= 8, f"没扫到 ChannelContext 调用，检查方式可能失效（{checked} 处）"
    assert not bad, "适配器传了数据类里没有的字段（会 TypeError）：" + "; ".join(bad)


def test_group_welcome_context_also_matches():
    """群欢迎里也手工拼了 ChannelContext —— 一起查（它在 group_welcome.py 里）。"""
    plat = _load_platform()
    fields = {f.name for f in dataclasses.fields(plat.ChannelContext)}
    for name, lineno in _context_kwargs(AI_DIR / "group_welcome.py"):
        assert name in fields, f"group_welcome.py:{lineno} → {name}"


if __name__ == "__main__":
    test_adapter_kwargs_exist_on_dataclass()
    test_group_welcome_context_also_matches()
    print("OK channel context fields")
