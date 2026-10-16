# -*- coding: utf-8 -*-
"""防线测试：插件里 import 的 `qbotmanager.*` 必须都在 PyInstaller spec 的 appr/ 清单里。

来历（Linux 线 2026-10-11 交接）：装机版里 `appr/` 是**显式清单**拷贝；插件 `ai/brain.py`
新增 `from qbotmanager.core.memory import policy`，而 spec 漏了 `core/memory`
→ **装机版记忆三档静默失效**（被插件 try/except 吞掉）、日志脱敏少两项。
这类"只在装机版坏、源码跑一切正常"的问题，只有这条检查兜得住：
`test_core.py` 里那条 `test_packaged_appr_bundle_covers_ai_imports()` 就是为它加的。
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "AstroSwarm.spec"
PLUGINS = REPO / "src" / "qbotmanager" / "assets" / "plugins"

_IMPORT = re.compile(r"(?:from|import)\s+(qbotmanager(?:\.\w+)*)")


def _plugin_imports() -> dict:
    need = {}
    for f in PLUGINS.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _IMPORT.finditer(text):
            need.setdefault(m.group(1), set()).add(f.name)
    return need


def _spec_listed() -> set:
    spec = SPEC.read_text(encoding="utf-8", errors="replace")
    listed = set()
    for m in re.finditer(
            r"\(['\"]src/qbotmanager/([^'\"]+)['\"]\s*,\s*['\"]appr/qbotmanager/", spec):
        name = m.group(1)
        if name.endswith(".py"):
            name = name[:-3]
        if name.endswith("__init__"):          # core/__init__.py 就是 core 包
            name = name[:-len("__init__")].rstrip("/")
        listed.add(name.replace("/", "."))
    return listed


def _covered(mod: str, listed: set) -> bool:
    rel = mod[len("qbotmanager."):] if mod.startswith("qbotmanager.") else mod
    if rel == "qbotmanager":
        return True
    parts = rel.split(".")
    for i in range(len(parts), 0, -1):
        if ".".join(parts[:i]) in listed:
            return True
    return any(rel.startswith(k + ".") for k in listed)


def test_spec_covers_every_plugin_import():
    """插件用到的每个 qbotmanager 模块，spec 的 appr/ 清单里都得有。"""
    need = _plugin_imports()
    assert need, "没扫到任何插件 import，路径可能不对"
    listed = _spec_listed()
    missing = {m: sorted(files) for m, files in need.items() if not _covered(m, listed)}
    assert not missing, (
        "以下模块被插件 import，但 PyInstaller spec 的 appr/ 清单里没有 → 装机版会静默失效：\n"
        + "\n".join(f"  {m}  <- {', '.join(files)}" for m, files in sorted(missing.items()))
        + "\n修法：在 AstroSwarm.spec 的 datas 里加 "
          "('src/qbotmanager/<path>', 'appr/qbotmanager/<path>')。")


def test_spec_lists_memory_and_agent_packages():
    """点名几条最容易漏的（memory 是 2026-10-11 真出过事的那个）。

    2026-10-15 更新：spec 的 appr/ 清单已从**逐个文件点名**改成**整目录复制**
    （`('src/qbotmanager/core', 'appr/qbotmanager/core')`）—— 点名那条老路
    每新增一个 core 模块就漏一次（v1.2.9 漏 agent_profile、10-04 漏 settings/workshop、
    10-11 漏 memory、10-12 漏 assets/plugins/ai，每次症状都是"网关连上了却不回消息"）。

    所以这里两条一起锁：
      ① 必须存在整目录的 `core`（这是**更强**的保证：以后新增模块自动带上）；
      ② 点名的这几个照旧必须被覆盖（用与上面同口径的 `_covered()`，
         精确列出或列出上级目录都算数）。
    """
    listed = _spec_listed()
    assert "core" in listed, (
        "spec 的 appr/ 清单里必须有整目录的 core —— 又退回逐个点名了？"
        "那意味着以后每新增一个 core 模块都要记得改 spec，漏一次就是一次线上事故。")
    for name in ("qbotmanager.core.memory", "qbotmanager.core.agent",
                 "qbotmanager.core.workshop", "qbotmanager.core.agent_profile",
                 "qbotmanager.core.settings", "qbotmanager.core.knowledge_base"):
        assert _covered(name, listed), f"spec 的 appr/ 清单缺少 {name}"
