# -*- coding: utf-8 -*-
r"""防线：插件里 import 的 `qbotmanager.core.X` 必须在 PyInstaller spec 的 appr/ 清单里。

为什么需要（Linux 线 2026-10-11 交接）：装机版里 `appr/` 是**显式清单**拷贝，
插件 `brain.py` 新增了 `from qbotmanager.core.memory import policy`，而 spec 只打了
`__init__/knowledge_base/agent_profile/settings/workshop/agent` —— **漏了 memory**
→ 装机版记忆功能静默失效（被插件的 try/except 吞掉），日志脱敏也少两项。
这类"只在装机版坏"的问题，靠这条检查兜住。
"""
import re
import sys
from pathlib import Path

REPO = Path(r"D:\ai\QBotManager")
PLUGINS = REPO / "src" / "qbotmanager" / "assets" / "plugins"
SPEC = REPO / "AstroSwarm.spec"
SRC = REPO / "src" / "qbotmanager"

# 1) 插件里用到的 qbotmanager.* 模块
pat = re.compile(r"(?:from|import)\s+(qbotmanager(?:\.\w+)*)")
need = {}
for f in PLUGINS.rglob("*.py"):
    if "__pycache__" in f.parts:
        continue
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        continue
    for m in pat.finditer(text):
        need.setdefault(m.group(1), set()).add(f.name)

print(f"插件里引用的 qbotmanager 模块：{len(need)} 个")
for mod in sorted(need):
    print(f"  {mod:38s} <- {', '.join(sorted(need[mod])[:3])}")

# 2) spec 的 appr/ 清单里都有谁
#    清单两种写法都要支持：
#      ('src/qbotmanager/core/agent_profile.py', 'appr/qbotmanager/core')   ← 单文件
#      ('src/qbotmanager/core/memory',           'appr/qbotmanager/core/memory') ← 整个目录
spec = SPEC.read_text(encoding="utf-8", errors="replace")
listed = set()
for m in re.finditer(r"\(['\"]src/qbotmanager/([^'\"]+)['\"]\s*,\s*['\"]appr/qbotmanager/", spec):
    name = m.group(1)
    if name.endswith(".py"):
        name = name[:-3]
    if name.endswith("__init__"):      # core/__init__.py 就是 core 包本身
        name = name[:-len("__init__")].rstrip("/")
    listed.add(name.replace("/", "."))
print(f"\nspec 的 appr/ 清单：{len(listed)} 条")
for k in sorted(listed):
    print("  ", k)

# 3) 找缺口：插件 import 的模块，在清单里能不能找到（支持"清单给了整目录"的情况）
#    listed 里的条目是相对 `qbotmanager/` 的（如 core.agent_profile），插件 import 的模块名带前缀
#    （qbotmanager.core.agent_profile）—— 比对前把前缀去掉。
def covered(mod: str) -> bool:
    rel = mod[len("qbotmanager."):] if mod.startswith("qbotmanager.") else mod
    if rel == "qbotmanager":
        return True
    parts = rel.split(".")
    for i in range(len(parts), 0, -1):
        if ".".join(parts[:i]) in listed:
            return True
    return any(rel.startswith(k + ".") for k in listed)


missing = {m: files for m, files in need.items() if not covered(m)}
print("\n=== 缺口（插件要用、但清单里没有）===")
if missing:
    for m, files in sorted(missing.items()):
        print(f"  ✗ {m}   （来自 {', '.join(sorted(files))}）")
else:
    print("  无 ✓")

# 4) 额外体检：src 里存在的 core 子模块，但清单里没有（只提示，不强制）
core_dirs = sorted(p.name for p in (SRC / "core").iterdir()
                   if p.is_dir() and (p / "__init__.py").is_file() and p.name != "__pycache__")
core_files = sorted(p.stem for p in (SRC / "core").glob("*.py") if p.stem != "__init__")
print("\n=== 参考：src/qbotmanager/core 下有、清单里没有的（只提示）===")
for name in core_dirs:
    if f"core.{name}" not in listed:
        print(f"  · core/{name}/  （目录，未进 appr）")
for name in core_files:
    if f"core.{name}" not in listed:
        print(f"  · core/{name}.py")

sys.exit(1 if missing else 0)
