# -*- coding: utf-8 -*-
"""啰嗦文案体检：把界面上"太长/像开发自述"的可见文案列出来。

判定：
- 单个可见字符串 > 24 字（中文按字算）；
- 含"以前/原来/旧版/曾经/自 X 起"这类历史沿革（用户不关心）；
- 含"注意：/提醒："开头的长句（多半能砍一半）；
- 含明显开发词（config.json / objectName / CLI / ac 指令 / 字段名）。
只读扫描，不改任何文件。
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "qbotmanager" / "ui"

SKIP_HINTS = ("test",)
HISTORY = ("以前", "原来", "旧版", "曾经", "历史上", "自 v", "自 20", "老版本", "过去")
DEVWORDS = ("config.json", "objectName", "CLI", "ac 指令", "NoneBot", "JSON", "字段",
            "接口", "参数名", "openid", "webhook", "token", "API")
LONG = 24


def _chinese_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _is_visible_string(node: ast.AST) -> bool:
    """粗略判断：出现在 QLabel/QCheckBox/QPushButton/setToolTip 这类调用里的字符串。"""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def scan_file(path: Path) -> list:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", "") or getattr(func, "id", "")
        if name not in ("QLabel", "QCheckBox", "QPushButton", "setToolTip", "setText",
                        "setPlaceholderText", "QGroupBox", "addTab", "QRadioButton"):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if not _is_visible_string(arg):
                continue
            text = arg.value.strip()
            if _chinese_len(text) < 6:
                continue
            reasons = []
            if _chinese_len(text) > LONG:
                reasons.append(f"长({_chinese_len(text)}字)")
            if any(word in text for word in HISTORY):
                reasons.append("历史沿革")
            if text.startswith(("注意：", "提醒：", "说明：")) and _chinese_len(text) > 18:
                reasons.append("长提示")
            if any(word in text for word in DEVWORDS):
                reasons.append("开发词")
            if reasons:
                hits.append((path.name, node.lineno, text, reasons))
    return hits


def main() -> None:
    files = sorted(p for p in UI.rglob("*.py") if p.is_file())
    all_hits = []
    for path in files:
        all_hits += scan_file(path)
    by_file = {}
    for name, lineno, text, reasons in all_hits:
        by_file.setdefault(name, []).append((lineno, text, reasons))
    print(f"界面文件 {len(files)} 个，命中 {len(all_hits)} 条\n")
    for name in sorted(by_file, key=lambda n: -len(by_file[n])):
        items = by_file[name]
        print(f"== {name}（{len(items)} 条）")
        for lineno, text, reasons in items[:12]:
            short = text if len(text) <= 60 else text[:58] + "…"
            print(f"   {lineno:>5}  [{'/'.join(reasons)}] {short}")
        if len(items) > 12:
            print(f"   …还有 {len(items) - 12} 条")
    print()
    print("按类型统计：")
    counter = {}
    for _n, _l, _t, reasons in all_hits:
        for r in reasons:
            counter[r] = counter.get(r, 0) + 1
    for key, value in sorted(counter.items(), key=lambda kv: -kv[1]):
        print(f"   {key}: {value}")


if __name__ == "__main__":
    sys.exit(main())
