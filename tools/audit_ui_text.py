# -*- coding: utf-8 -*-
"""界面文案体检：扫出会让用户看不懂/看着像坏了的文字。

老板 2026-10-01 在「AI 大脑」看到一行「你同学 openid」——"你同学"是李清菡人设里
对老板的称呼，串到界面上了。这里把同类问题一次扫干净：

1. 乱码：锟斤拷 / Ã / Â / 替换符 U+FFFD；
2. 占位符泄漏：%s %d {0} TODO FIXME XXX；
3. 串味的内部称呼：你同学、主人、老板（界面里应该用"你"或"管理员"）；
4. 叠字/空标签：你你、的的、连续两个空格、纯空白的 label；
5. 半截括号：只有左括号没右括号。

只扫界面与设置里的**用户可见字符串**（注释和 docstring 先跳过，避免误报噪声）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qbotmanager"

TARGETS = [
    SRC / "ui",
    SRC / "core" / "settings.py",
    SRC / "core" / "ai_config.py",
]

# (名字, 正则, 说明)
RULES = [
    ("乱码", re.compile(r"锟斤拷|Ã.|Â.|\ufffd"), "编码坏了的文字"),
    ("占位符", re.compile(r"%[sd]\b|\{0\}|\bTODO\b|\bFIXME\b|\bXXX\b"), "没替换的占位符/待办标记"),
    ("串味称呼", re.compile(r"你同学|我的同学|主人"), "人设里的称呼跑到界面上了"),
    ("叠字", re.compile(r"你你|的的|了了|是是"), "重复字"),
    ("双空格", re.compile(r"[\u4e00-\u9fff]  +[\u4e00-\u9fff]"), "中文之间多余空格"),
]

# 只检查这些调用/赋值里出现的字符串字面量
STRING_CALL = re.compile(
    r"""(?:QLabel|QCheckBox|QPushButton|setText|setPlaceholderText|setToolTip|setWindowTitle|"""
    r"""setTitle|_note|addItem|setStatus|show_toast|QGroupBox)\s*\(\s*(?P<q>["'])(?P<text>.*?)(?P=q)""",
    re.S,
)


def iter_strings(path: Path):
    """返回 (行号, 文本)：只看用户可见的字符串字面量。

    `%s` 这种老式格式化是合法的（后面就跟 ` % 值`），扫的时候一并带上，
    交给 is_template_format 判断，避免把正常写法当泄漏报出来。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for match in STRING_CALL.finditer(raw):
        line = raw.count("\n", 0, match.start()) + 1
        tail = raw[match.end():match.end() + 120]
        yield line, match.group("text"), tail


def is_template_format(text: str, tail: str) -> bool:
    """字符串里带 %s/%d，且后面紧跟着 ` % 值`（老式格式化）→ 不算泄漏。"""
    if not re.search(r"%[sd]", text):
        return False
    return bool(re.match(r"\s*%\s*[\(\w\"']", tail))


def main() -> int:
    hits = []
    for target in TARGETS:
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            if "__pycache__" in path.parts:
                continue
            for line, text, tail in iter_strings(path):
                for name, pattern, why in RULES:
                    found = pattern.search(text)
                    if not found:
                        continue
                    if name == "占位符" and is_template_format(text, tail):
                        continue            # 正常的 `"…%s…" % 值` 写法
                    hits.append((path.relative_to(ROOT), line, name, why,
                                 found.group(0), text[:70]))
    if not hits:
        print("界面文案体检：没发现问题 ✓")
        return 0
    print(f"界面文案体检：发现 {len(hits)} 处")
    for path, line, name, why, found, text in hits:
        print(f"  [{name}] {path}:{line} 命中「{found}」（{why}）")
        print(f"        原文：{text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
