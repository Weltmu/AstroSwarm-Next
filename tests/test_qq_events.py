# -*- coding: utf-8 -*-
"""日志里的官方事件名要翻成人话，日志页要有「官方通道」筛选。

2026-10-01 老板看到日志里全是 `GROUP_MESSAGE_CREATE` 这种字样 —— 排查"群里 @ 它不回"
时这等于天书。这条是补历史欠账（清单里曾被错误标成"已完成"）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qbotmanager.core import qq_events  # noqa: E402


def test_describe_known_events():
    assert "群里有人 @ 我" in qq_events.describe_event("GROUP_AT_MESSAGE_CREATE")
    assert "全量模式" in qq_events.describe_event("GROUP_MESSAGE_CREATE")
    assert "私聊" in qq_events.describe_event("C2C_MESSAGE_CREATE")
    assert "上线" in qq_events.describe_event("READY")
    # 不认识的别编，原样返回
    assert qq_events.describe_event("SOMETHING_NEW") == "SOMETHING_NEW"
    assert qq_events.describe_event("") == ""


def test_humanize_line_keeps_original_name():
    raw = ("10-01 19:09:09 [SUCCESS] nonebot | QQ 1905694838 | "
           "[EventType.GROUP_MESSAGE_CREATE]: Message ROBOT1.0_xxx")
    out = qq_events.humanize_line(raw)
    assert "群里收到一条消息（全量模式：没 @ 也收）" in out
    assert "GROUP_MESSAGE_CREATE" in out, "原名字要留着，方便上网查"
    assert "EventType.GROUP_MESSAGE_CREATE" not in out

    bare = "10-01 19:09 [INFO] ai | [brain] 收到 qq_official 消息 text=你好"
    assert qq_events.humanize_line(bare) == bare, "没有事件名的行不许动"


def test_bare_event_name_is_translated():
    out = qq_events.humanize_line("处理 GROUP_AT_MESSAGE_CREATE 事件")
    assert "群里有人 @ 我（GROUP_AT_MESSAGE_CREATE）" in out


def test_no_double_translation():
    """回归：不许把翻好的中文再翻一遍（会出现"中文（中文（英文））"套娃）。"""
    out = qq_events.humanize_line("[EventType.GROUP_MESSAGE_CREATE]: 你好")
    assert out.count("群里收到一条消息") == 1, out
    assert out.count("GROUP_MESSAGE_CREATE") == 1, out
    assert "（（" not in out and "））" not in out.replace("（GROUP_MESSAGE_CREATE）", ""), out
    # 两种写法混在一行也只翻一次
    mixed = qq_events.humanize_line("EventType.READY 之后 READY 又来一次")
    assert mixed.count("连接成功，机器人上线了") == 2, mixed
    assert "EventType.连接" not in mixed and "（（" not in mixed, mixed
    # 不认识的英文大写词别乱翻
    assert qq_events.humanize_line("SOMETHING_ELSE 发生了") == "SOMETHING_ELSE 发生了"


def test_is_official_line():
    assert qq_events.is_official_line("[EventType.C2C_MESSAGE_CREATE] ...")
    assert qq_events.is_official_line("[qq_official] 图片识别结果: ...")
    assert qq_events.is_official_line("10-01 19:12 [INFO] nonebot | QQ | Bot 1905694838 connected")
    assert not qq_events.is_official_line("10-01 19:12 [INFO] 部署完成，耗时 3 秒")
    assert not qq_events.is_official_line("")


def test_logs_page_has_official_filter_and_humanizes():
    """日志页：筛选项要在，且渲染出来的是人话。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QBM_NO_UPDATE", "1")
    from PySide6.QtWidgets import QApplication

    from qbotmanager.ui.pages.logs_page import OFFICIAL_FILTER, SOURCES, LogsPage

    app = QApplication.instance() or QApplication([])
    assert OFFICIAL_FILTER in SOURCES, SOURCES

    page = LogsPage.__new__(LogsPage)          # 只测过滤/渲染这两段纯逻辑
    page._src_filter = OFFICIAL_FILTER
    page._lv_filter = "全部"
    assert page._matches("NoneBot", "INFO", "[EventType.GROUP_MESSAGE_CREATE] x") is True
    assert page._matches("NoneBot", "INFO", "部署完成") is False
    page._src_filter = "全部"
    assert page._matches("NoneBot", "INFO", "部署完成") is True
    app.processEvents()


if __name__ == "__main__":
    test_describe_known_events()
    test_humanize_line_keeps_original_name()
    test_bare_event_name_is_translated()
    test_is_official_line()
    test_logs_page_has_official_filter_and_humanizes()
    print("OK qq events")
