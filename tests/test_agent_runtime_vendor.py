# -*- coding: utf-8 -*-
"""机器人侧 qbotmanager 同步：必须是"整目录"，不能是点名清单。

背景（2026-10-03 真实事故）：`core/web_fetch.py` 是新加的，插件要 import 它，
但同步逻辑只抄了一份写死的文件清单 → 机器人目录里没这个文件 → **ai 插件整个
导入失败** → 网关连上了、QQ 显示已连接，可是发消息一点反应都没有，日志里也不
出现"收到事件"。同类事故 v1.2.8 已经发生过一次（漏 core/agent_profile.py）。

这条用例锁住两件事：
1. 同步后机器人目录里的 core/*.py 与源码里的**文件名集合一致**（新文件自动带上）；
2. 插件目录里不依赖任何"只存在于桌面端"的新文件（插件自己带 web_fetch）。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qbotmanager.core import bot as bot_mod  # noqa: E402
from qbotmanager.core.settings import Settings  # noqa: E402

SRC_CORE = ROOT / "src" / "qbotmanager" / "core"


def test_agent_runtime_copies_whole_core_tree():
    tmp = Path(tempfile.mkdtemp(prefix="qbm_vendor_"))
    s = Settings(tmp)
    s.ensure_dirs()
    bot_mod.ensure_agent_runtime(s)

    copied = {p.name for p in (s.bot_dir / "qbotmanager" / "core").glob("*.py")}
    expected = {p.name for p in SRC_CORE.glob("*.py")}
    missing = expected - copied
    assert not missing, f"这些 core 文件没同步到机器人目录：{sorted(missing)[:8]}"
    # 子包也要在
    assert (s.bot_dir / "qbotmanager" / "core" / "agent" / "runtime.py").exists()


def test_plugin_carries_its_own_web_fetch():
    """插件里的取网页实现必须随插件走（不能指望机器人目录有桌面端的新文件）。"""
    ai_dir = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
    assert (ai_dir / "web_fetch.py").exists(), "插件要自带 web_fetch.py"
    src = (ai_dir / "tools_bridge.py").read_text(encoding="utf-8")
    assert "from . import web_fetch" in src, "插件应该用相对导入拿自带的 web_fetch"
    assert "from qbotmanager.core import web_fetch" not in src, "别再依赖机器人目录的 core 拷贝"


def test_core_has_no_leftover_web_fetch():
    """core 里那份已挪进插件，别留两份（两份就会慢慢不一致）。"""
    assert not (SRC_CORE / "web_fetch.py").exists(), "core/web_fetch.py 应该已经挪走"


if __name__ == "__main__":
    test_agent_runtime_copies_whole_core_tree()
    test_plugin_carries_its_own_web_fetch()
    test_core_has_no_leftover_web_fetch()
    print("OK agent runtime vendoring")
