# -*- coding: utf-8 -*-
"""界面文案体检：乱码 / 占位符泄漏 / 串味称呼 / 叠字，一个都不许留在界面上。

2026-10-01 老板在「AI 大脑」看到一行「你同学 openid」（那是李清菡人设里对老板的称呼），
当场一句"这啥玩意啊"。这条用例把 tools/audit_ui_text.py 的扫描结果钉死：界面文案体检必须是 0 命中。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit_ui_text.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("audit_ui_text", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_ui_text"] = mod
    spec.loader.exec_module(mod)
    return mod


def _scan(mod):
    hits = []
    for target in mod.TARGETS:
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            if "__pycache__" in path.parts:
                continue
            for line, text, tail in mod.iter_strings(path):
                for name, pattern, why in mod.RULES:
                    found = pattern.search(text)
                    if not found:
                        continue
                    if name == "占位符" and mod.is_template_format(text, tail):
                        continue
                    hits.append(f"{path.name}:{line} [{name}] {found.group(0)} :: {text[:50]}")
    return hits


def test_no_garbled_ui_text():
    mod = _load_tool()
    hits = _scan(mod)
    assert not hits, "界面文案有问题：\n" + "\n".join(hits)


def test_owner_openid_label_is_not_persona_role():
    """回归：那一行要用「你的…」这种界面称呼，不能出现人设里的称呼。

    2026-10-03 文案精简：openid 属开发词，标签由「你的 openid」压成
    「你的会话标识」（占位符里说明去哪儿复制），本用例跟着改期望值。
    """
    src = (ROOT / "src" / "qbotmanager" / "ui" / "pages" / "brain_page.py").read_text(
        encoding="utf-8")
    assert 'QLabel("你的会话标识")' in src, "管理员会话标识那一行的文案被改坏了"
    assert "你同学 openid" not in src


def test_audit_rules_actually_catch_problems():
    """反向验证：体检规则本身要能抓到坏文案（不然它就是个摆设）。"""
    mod = _load_tool()
    for bad in ("你同学 openid", "纯功能锟斤拷", "TODO 填这里", "你你好的"):
        assert any(pattern.search(bad) for _, pattern, _ in mod.RULES), bad
    assert mod.is_template_format("已登录：%s", " % email") is True
    assert mod.is_template_format("已登录：%s", "（请检查）") is False


if __name__ == "__main__":
    test_no_garbled_ui_text()
    test_owner_openid_label_is_not_persona_role()
    test_audit_rules_actually_catch_problems()
    print("OK ui text audit")
