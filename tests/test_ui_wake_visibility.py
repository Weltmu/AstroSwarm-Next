# -*- coding: utf-8 -*-
"""唤醒词在人设两种形态下都要可见（2026-10-12 老板报「找不到了」）。

v1.2.15 把唤醒词收进「预设」面板，切到「自定义」整块隐藏；老板按习惯要能随时
找到它，所以挪到胶囊下面常显。这条用例守住接线：它属于人设卡片、不属于「预设」
面板 —— 两种形态切换都不该把它藏起来。
"""
import tempfile
from pathlib import Path


def test_wake_words_visible_in_both_persona_modes():
    from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.brain_page import BrainPage
    from qbotmanager.ui.pages.common import PageContext

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_wake_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.agent_profile_enabled = False
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    bp = BrainPage(ctx)
    app.processEvents()

    assert bp._persona_panel.isAncestorOf(bp.agent_wake_edit), "唤醒词要在人设卡片里常显"
    assert not bp._agent_panel.isAncestorOf(bp.agent_wake_edit), \
        "唤醒词不能留在「预设」面板里 —— 切自定义整块隐藏就是老板说找不到的原因"
    assert isinstance(bp.btn_wake_generate, QPushButton)
    assert isinstance(bp.chk_wake_auto, QCheckBox)

    # 自定义态：档案面板收起，但唤醒词仍然看得见、能编辑
    bp.set_persona_mode("custom", animate=False)
    app.processEvents()
    assert bp._agent_panel.isHidden(), "自定义态下档案面板应收起"
    assert not bp.agent_wake_edit.isHidden(), "自定义态下唤醒词也要看得见"
    bp.agent_wake_edit.setText("李清菡,学姐")
    assert bp.agent_wake_edit.text() == "李清菡,学姐"

    # 预设态：档案面板展开，唤醒词同样在
    bp.set_persona_mode("preset", animate=False)
    app.processEvents()
    assert not bp._agent_panel.isHidden(), "预设态下档案面板应展开"
    assert not bp.agent_wake_edit.isHidden(), "预设态下唤醒词要看得见"

    bp.deleteLater()
    print("OK ui wake_words_visible_both_modes")


def test_refresh_lists_marks_just_installed_pack(monkeypatch):
    """装完能力包（安装任务结束会调 refresh_lists()）商店卡片要立刻显示「✓ 已装」。

    2026-10-12 老板报："装完插件商店里没显示已安装" —— 以前 refresh_lists() 只刷
    本地三个列表，商店卡片要离开页面再进来才会变。这里把市场加载打成空操作，
    用真实的"装上一个能力包 → 刷新 → 卡片变化"来守这条接线。
    """
    import json

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    monkeypatch.setattr(PluginsPage, "_load_market", lambda self: None, raising=True)

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_mktrefresh_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    pp = PluginsPage(ctx)
    pp._market_entries = {
        "group-manager": {"id": "group-manager", "name": "群管理能力包", "version": "1.0.1",
                          "category": "官方能力包", "description": "禁言、踢人、群公告",
                          "url": "https://astroswarm.cn/plugins/group-manager.zip", "sha256": "x"},
    }
    pp._refresh_market_tags()
    pp._refresh_market_list()
    app.processEvents()

    def installed_mark() -> bool:
        for i in range(pp.market_list.count()):
            item = pp.market_list.item(i)
            if item.data(Qt.UserRole) == "group-manager":
                widget = pp.market_list.itemWidget(item)
                return any("已装" in w.text() for w in widget.findChildren(QLabel))
        raise AssertionError("没找到 group-manager 的卡片")

    assert not installed_mark(), "还没装时不该显示已装"

    pack_dir = Path(s.root) / "tool_packs" / "group-manager"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps({"id": "group-manager", "name": "群管理能力包", "version": "1.0.1",
                    "kind": "tool-pack"}, ensure_ascii=False), encoding="utf-8")

    pp.refresh_lists()
    app.processEvents()
    assert installed_mark(), "装完 refresh_lists() 后商店卡片应显示「✓ 已装」"

    pp.deleteLater()
    print("OK ui market_refresh_after_install")


def test_nonebot_store_marks_installed(monkeypatch):
    """NoneBot 商店列表里已装的要标「✅已安装」（2026-10-12 老板装完看不出装过）。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.bot import write_plugins
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    monkeypatch.setattr(PluginsPage, "_load_market", lambda self: None, raising=True)

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_nbstore_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    write_plugins(s, ["nonebot_plugin_fake"])   # 假装这个已经装过
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    pp = PluginsPage(ctx)
    pp._nb_entries = [
        {"name": "假插件", "version": "1.0.0", "module_name": "nonebot_plugin_fake",
         "project_link": "nonebot-plugin-fake", "is_official": True, "valid": True,
         "desc": "已装的那个"},
        {"name": "没装的插件", "version": "1.0.0", "module_name": "nonebot_plugin_other",
         "project_link": "nonebot-plugin-other", "is_official": False, "valid": True,
         "desc": "没装的那个"},
    ]
    pp._nb_loaded = True
    pp._refresh_nonebot_list()
    app.processEvents()

    texts = [pp.nb_list.item(i).text() for i in range(pp.nb_list.count())]
    assert any("已安装" in t and "fake" in t for t in texts), texts
    assert any("已安装" not in t and "other" in t for t in texts), texts
    pp.deleteLater()
    print("OK ui nonebot_store_installed_mark")
