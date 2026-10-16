# -*- coding: utf-8 -*-
"""微信二维码：**不自动刷新**。

2026-10-03 老板实测发现：二维码只要一直没扫，界面每隔几分钟就自动重启一次 NoneBot
去换新码 —— 后台反复重启既费内存又刷日志。现在改成一动不动，只提示"过期了点一下"。

这条用例把这个行为焊死：二维码放旧了也必须只提示、不许自己重启。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QBM_NO_UPDATE", "1")

PANEL = ROOT / "src" / "qbotmanager" / "ui" / "pages" / "channel_panels.py"


def test_no_auto_refresh_code_left():
    """源码里不许再有"自动刷新二维码"这条路（它等于定时重启机器人）。"""
    src = PANEL.read_text(encoding="utf-8")
    assert "_auto_refresh_qr" not in src, "自动刷新二维码的实现应该已经删掉"
    assert "正在自动刷新" not in src, "别留自动刷新的文案"
    assert "不会自动刷新" in src, "要明确告诉用户不会自动刷新"


def test_old_qr_only_shows_hint_not_restart():
    """二维码放旧了：只提示，不调 _restart_bot。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.channel_panels import WeChatChannelPanel
    from qbotmanager.ui.pages.common import PageContext

    app = QApplication.instance() or QApplication([])
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="qbm_qr_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()

    class _Mgr:
        def wechat_status(self):
            # 面板要读的字段都给全（少一个就 KeyError，那是面板自己的假设）
            return {"logged_in": False, "connected": False, "installed": True,
                    "bot_id": "", "login_time": 0, "login_status": "waiting",
                    "reason": "等待扫码",
                    "qrcode_url": "https://liteapp.weixin.qq.com/q/ABCDEF?qrcode=deadbeef"}

        def wechat_qr_url(self):
            return "https://liteapp.weixin.qq.com/q/ABCDEF?qrcode=deadbeef"

        def bot_running(self):
            return True

        def __getattr__(self, name):        # 别的调用一律给个无害值
            return lambda *a, **k: None

    ctx = PageContext(s, _Mgr(), TaskManager(), {"show_toast": lambda *a, **k: None})
    panel = WeChatChannelPanel(ctx)
    restarts = []
    panel._restart_bot = lambda *a, **k: restarts.append((a, k))
    panel._is_member = lambda: True

    # 第一次进来：拿到二维码
    panel.refresh_status()
    app.processEvents()
    assert panel._qr_url_cache.endswith("deadbeef"), panel._qr_url_cache

    # 把"展示时间"推到 10 分钟前，再刷新几次 —— 一次都不许自动重启
    panel._qr_shown_at = time.time() - 600
    for _ in range(5):
        panel.refresh_status()
        app.processEvents()
    assert restarts == [], f"二维码放旧了也不许自动重启机器人：{restarts}"
    assert "刷新二维码" in panel.qr_tip.text(), panel.qr_tip.text()


if __name__ == "__main__":
    test_no_auto_refresh_code_left()
    test_old_qr_only_shows_hint_not_restart()
    print("OK wechat qr manual")
