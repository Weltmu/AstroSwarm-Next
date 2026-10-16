# -*- coding: utf-8 -*-
"""机器人启动前的「AI 插件自检」。

2026-10-03 同一天出了两次"连上了却不回话"：
1. 插件 import 失败（新模块 core/web_fetch.py 没同步到机器人目录）；
2. 适配器给 ChannelContext 传了数据类里没有的字段 → 每条消息 TypeError。
两次的启动日志里都只有普通报错，用户不会去看，只能来问"怎么不回我"。

这条用例保证自检**真的能抓出这两种问题**，而不是写了个摆设。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qbotmanager.core import bot as bot_mod  # noqa: E402
from qbotmanager.core.settings import Settings  # noqa: E402

AI_SRC = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _prepare(tmp: Path, monkeypatch_plugins: bool = True) -> Settings:
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    if monkeypatch_plugins:
        # 把插件先同步到机器人目录（模拟真实部署）
        bot_mod.ensure_ai_plugin(s)
    return s


def test_selfcheck_passes_on_real_plugin():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_check_ok_"))
    s = _prepare(tmp)
    lines = []
    problems = bot_mod.selfcheck_ai_plugin(s, log=lines.append)
    assert problems == [], problems
    assert any("自检通过" in line for line in lines), lines


def test_selfcheck_catches_missing_context_field():
    """把适配器里塞一个数据类没有的字段 —— 自检必须点名报出来。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_check_bad_"))
    s = _prepare(tmp)
    target = s.plugins_dir / "ai" / "adapters" / "qq.py"
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace("ctx = ChannelContext(", "ctx = ChannelContext(\n        no_such_field=1,"),
        encoding="utf-8")

    labels = []
    problems = bot_mod.selfcheck_ai_plugin(s, log=labels.append)
    assert problems, "字段对不上必须报出来"
    assert any("no_such_field" in p for p in problems), problems
    assert any("自检没过" in line for line in labels), labels


def test_selfcheck_catches_syntax_error():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_check_syn_"))
    s = _prepare(tmp)
    bad = s.plugins_dir / "ai" / "broken.py"
    bad.write_text("def oops(:\n    pass\n", encoding="utf-8")
    problems = bot_mod.selfcheck_ai_plugin(s)
    assert any("broken.py" in p for p in problems), problems


def test_selfcheck_reports_missing_plugin_dir():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_check_nodir_"))
    s = Settings(tmp)
    s.ensure_dirs()
    problems = bot_mod.selfcheck_ai_plugin(s)
    assert problems and "不存在" in problems[0], problems


def test_selfcheck_leaves_no_leftover_files():
    """自检编译时产生的临时 .pyc 必须清干净（别污染客户插件目录）。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_check_clean_"))
    s = _prepare(tmp, monkeypatch_plugins=False)
    bot_mod.selfcheck_ai_plugin(s)
    leftovers = [p.name for p in (s.plugins_dir / "ai").rglob("*selfcheck*")]
    assert leftovers == [], leftovers


if __name__ == "__main__":
    test_selfcheck_passes_on_real_plugin()
    test_selfcheck_catches_missing_context_field()
    test_selfcheck_catches_syntax_error()
    test_selfcheck_reports_missing_plugin_dir()
    test_selfcheck_leaves_no_leftover_files()
    print("OK ai plugin selfcheck")
