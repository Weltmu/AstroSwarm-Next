# -*- coding: utf-8 -*-
"""发货前把整个插件目录编译一遍。

为什么要有这条：插件的代码是"打包时一起塞进安装包"的，写错了语法在构建环境里
不会有人发现 —— 直到用户机器上机器人起不来（2026-10-01 就踩了一次：
编辑时把 docstring 和 return 挤到同一行）。这里用 py_compile 把每个文件都过一遍。
"""
import py_compile
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI_PLUGIN = ROOT / "src" / "qbotmanager" / "assets" / "plugins"


def test_all_plugin_files_compile():
    files = sorted(p for p in AI_PLUGIN.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, f"没找到插件文件：{AI_PLUGIN}"
    bad = []
    with tempfile.TemporaryDirectory(prefix="qbm_pyc_") as tmp:
        for path in files:
            try:
                py_compile.compile(str(path), cfile=str(Path(tmp) / (path.stem + ".pyc")),
                                   doraise=True)
            except py_compile.PyCompileError as e:
                bad.append(f"{path.relative_to(ROOT)}: {e.msg.strip().splitlines()[-1]}")
    assert not bad, "插件里有语法错误，装到用户机器上会起不来：\n" + "\n".join(bad)
    print(f"    （插件编译通过：{len(files)} 个文件）")


if __name__ == "__main__":
    test_all_plugin_files_compile()
    print("OK plugin syntax")
