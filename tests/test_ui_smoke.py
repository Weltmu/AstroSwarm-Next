# -*- coding: utf-8 -*-
"""UI 冒烟测试：主题 QSS 生成、导航项、图标字体加载。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("QBM_POINTER_DIR"):
    os.environ["QBM_POINTER_DIR"] = str(Path(tempfile.mkdtemp(prefix="qbm_test_ptr_")))

from PySide6.QtWidgets import QPushButton as _QPushButton  # noqa: E402

from qbotmanager.ui import theme as theme_mod  # noqa: E402


def test_qss():
    css_cyber = theme_mod.build_qss("glass", "liquid", "cyber")
    css_def = theme_mod.build_qss("glass", "frosted", "default")
    assert "#22D3EE" in css_cyber
    assert css_cyber != css_def
    css_slate = theme_mod.build_qss("glass", "default", "slate")
    assert css_slate != css_def
    print("OK ui smoke")


def test_glass_panel_has_no_screenshot_backdrop():
    """磨砂面板必须是纯自绘：抓窗口截图当底图会造成滚动残影 / 文字发糊。"""
    from qbotmanager.ui import widgets as widgets_mod

    panel = widgets_mod.GlassPanel
    for gone in ("_fast_blur", "_backdrop", "_refresh_backdrop"):
        assert not hasattr(panel, gone), f"GlassPanel 不应再有截屏底图机制：{gone}"
    # 低透明度下也要压得住背景（否则列表文字发灰发糊）
    theme_mod.UI_OPACITY = 0.1
    assert widgets_mod._panel_alpha(120) >= 112
    theme_mod.UI_OPACITY = 1.0
    assert widgets_mod._panel_alpha(120) <= 248
    theme_mod.UI_OPACITY = 0.7
    print("OK glass panel self-drawn")


def test_wizard_background_panel():
    """首次安装向导：图片背景资源存在、内容收进半透明面板、离屏渲染不崩。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.ui import theme as theme_mod
    from qbotmanager.ui.wizard import ASSET_BG, DeployWizard

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme_mod.build_qss())
    assert ASSET_BG.is_file()
    wiz = DeployWizard()
    assert wiz.findChild(object, "wizardPanel") is not None
    assert not wiz._bg.isNull()
    pix = wiz.grab()
    assert not pix.isNull()
    wiz.close()
    print("OK wizard background panel")


def test_theme_switch_keeps_page():
    """切换 UI 质感/主题后，当前页（设置=9）不应跳回首页。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui import theme as theme_mod
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_ui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    win.switch_page("设置")
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["设置"]

    # 切质感框
    sp = win.pages[win._page_index["设置"]]
    idx = sp.glass_combo.findData("liquid")
    if idx >= 0:
        sp.glass_combo.setCurrentIndex(idx)
        app.processEvents()
    assert win.stack.currentIndex() == win._page_index["设置"]
    assert theme_mod.CURRENT_GLASS == "liquid"

    # 强制重建布局（重挂载侧栏 + 页面堆栈），当前页必须保持
    win._apply_layout()
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["设置"]
    win.close()
    print("OK ui theme_switch_keeps_page")


def test_brain_page_agent_profile_controls():
    """AI 大脑页应包含智能体档案控件（开关/档案选择/唤醒词/视觉模型）。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_ui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    bp = win.pages[6]
    assert hasattr(bp, "chk_agent_profile")
    assert hasattr(bp, "agent_profile_combo")
    assert hasattr(bp, "agent_wake_edit")
    assert hasattr(bp, "chk_wake_auto")
    assert hasattr(bp, "agent_owner_edit")
    assert hasattr(bp, "vision_url_edit")
    assert hasattr(bp, "vision_key_edit")
    win.close()
    print("OK ui brain_page_agent_profile_controls")


def test_brain_page_memory_and_kb_controls():
    """AI 大脑页有记忆时间线/导出/删除和本地知识库控件。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_memkb_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()
    brain = win.pages[6]
    assert hasattr(brain, "memory_list")
    assert hasattr(brain, "btn_memory_export")
    assert hasattr(brain, "btn_memory_delete")
    assert hasattr(brain, "btn_kb_add")
    assert hasattr(brain, "kb_list")
    assert hasattr(brain, "btn_persona_guide"), "人设工坊应有「模板说明」入口"
    brain._refresh_kb_list()
    assert brain.kb_list.count() >= 1
    win.close()
    print("OK ui brain_page_memory_kb")


def test_brain_page_auto_restart_on_profile_change():
    """切换智能体档案：**必须重启一次机器人**（2026-10-15 审计 liqinghan/A）。

    2026-10-11 一度改成"只刷热配置、不重启"；后来发现**档案开关是例外** ——
    李清菡插件在 import 期判定 _ENABLED、注册 handler，热配置救不了：
      · 打开档案：程序侧把 QQ/微信交给档案，插件却没注册 → 谁都不回话；
      · 关掉档案：AI 插件热恢复处理 QQ/微信，李清菡 handler 还在 → 双回复。
    所以现在「档案开关 / 档案 id」一变就提交重启任务并明说要重启；
    人设/唤醒词那些仍是热读、不重启（走 _agent_restart_required 那条路）。
    """
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.ui.pages.brain_page import BrainPage
    from qbotmanager.ui.pages.common import PageContext

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_autore_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    submitted = []
    toasts = []

    class FakeTasks:
        def submit(self, *args, **kwargs):
            submitted.append((args, kwargs))

    ctx = PageContext(
        s, None, FakeTasks(),
        {"show_toast": lambda *a: toasts.append(a)})
    bp = BrainPage(ctx)
    assert bp.agent_profile_combo.findData("star_helper") >= 0
    idx = bp.agent_profile_combo.findData("liqinghan")
    assert idx >= 0
    bp.agent_profile_combo.setCurrentIndex(idx)
    bp.chk_agent_profile.setChecked(True)
    bp._save_config()

    assert s.agent_profile_enabled is True, "切到预设应把档案开关写进配置"
    assert submitted, "切档案必须提交重启任务（插件 import 期注册，热读救不了）"
    assert any("重启一次机器人生效" in str(t) for t in toasts), toasts
    bp.deleteLater()
    print("OK ui brain_page_auto_restart_on_profile_change")


def test_plugins_page_restricts_third_party_install():
    """插件页应限制任意第三方安装（PyPI/zip），只保留官方市场占位与本地列表。"""
    from PySide6.QtWidgets import QApplication, QLabel

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_pluginui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    pp = PluginsPage(ctx)

    # 任意安装入口必须禁用或不存在
    if hasattr(pp, "btn_store"):
        assert not pp.btn_store.isEnabled(), "PyPI 安装按钮应禁用"
    if hasattr(pp, "store_edit"):
        assert not pp.store_edit.isEnabled(), "PyPI 包名输入应禁用"
    if hasattr(pp, "btn_zip"):
        assert not pp.btn_zip.isEnabled(), "zip 安装按钮应禁用"
    if hasattr(pp, "zip_edit"):
        assert not pp.zip_edit.isEnabled(), "zip 路径输入应禁用"

    # 官方市场面板存在
    texts = [lbl.text() for lbl in pp.findChildren(QLabel)]
    joined = "\n".join(texts)
    assert "插件商店" in joined, joined

    # 本地插件列表仍可用（重装依赖/打开目录等运维能力保留）
    assert pp.local_list is not None
    pp.deleteLater()
    print("OK ui plugins_page_restricts_third_party_install")


def test_plugins_page_market_panel_exists():
    """插件页应有官方市场面板（分类/列表/安装/刷新）与状态提示。"""
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QListWidget, QPushButton

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_mktui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    pp = PluginsPage(ctx)
    assert isinstance(pp.market_list, QListWidget)
    # 改版后：分类是横排小标签 + 搜索框（原来只有一个"全部分类"下拉，找不到东西）
    assert isinstance(pp.market_search, QLineEdit)
    assert pp.market_search.placeholderText().startswith("搜索插件")
    assert "" in pp._market_tags, pp._market_tags
    assert pp._market_tags[""].text() == "全部"
    assert isinstance(pp.btn_market_install, QPushButton)
    assert isinstance(pp.btn_market_refresh, QPushButton)
    assert isinstance(pp.market_status, QLabel)

    # 模拟市场加载结果：列表应渲染出条目且带插件 id（回归：addItem 返回值陷阱）
    from PySide6.QtCore import QPoint, Qt
    pp._on_market_loaded({"plugins": [{
        "id": "daily-motto", "name": "每日一言", "version": "1.0.0",
        "category": "功能扩展", "description": "每天一句",
        "url": "https://astroswarm.cn/plugins/daily-motto-1.0.0.zip", "sha256": "abc",
    }]})
    # 默认按分类分组：分组标题 + 卡片（标题行没有插件 id）
    ids = [pp.market_list.item(i).data(Qt.UserRole)
           for i in range(pp.market_list.count())]
    assert "daily-motto" in ids, ids
    item = pp.market_list.item(ids.index("daily-motto"))
    assert item.data(Qt.UserRole) == "daily-motto"
    pp.deleteLater()
    print("OK ui plugins_page_market_panel_exists")


def test_plugins_page_developer_mode_enables_zip_install():
    """开发者模式开启后恢复第三方 zip/PyPI 安装入口，默认关闭时禁用。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_devui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    pp = PluginsPage(ctx)
    assert not pp.btn_zip.isEnabled()
    assert not pp.btn_store.isEnabled()
    s.developer_mode = True
    pp._apply_mode()
    assert pp.btn_zip.isEnabled()
    assert pp.btn_store.isEnabled()
    s.developer_mode = False
    pp._apply_mode()
    assert not pp.btn_zip.isEnabled()
    pp.deleteLater()
    print("OK ui plugins_page_developer_mode_enables_zip_install")


def test_plugins_page_all_plugins_free():
    """插件已全部免费：带 tier=member 的历史条目也按免费渲染，页面不留购买入口。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_gateui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    pp = PluginsPage(ctx)
    assert not hasattr(pp, "btn_market_buy"), "商店不该再有「购买能力包」按钮"
    assert not hasattr(pp, "_open_buy"), "商店不该再有跳转付款页的方法"
    pp._entitlements = {
        "plan": "none", "plan_expires_at": 0,
        "full": False, "owned_plugins": [],
    }
    # 构造页面时会异步拉真实市场，回来会把这里的样例列表整个重建，
    # 测试手里那个 QListWidgetItem 就成了野指针（libshiboken 报 already deleted）。
    # 掐掉回调，测试只针对"全部免费"这段逻辑。
    pp._on_market_loaded = lambda *_a, **_k: None
    pp._market_entries = {
        "free-a": {
            "id": "free-a", "name": "免费插件", "version": "1.0.0",
            "category": "功能扩展", "tier": "free", "description": "",
            "url": "https://astroswarm.cn/plugins/free-a.zip", "sha256": "abc",
        },
        "member-b": {
            "id": "member-b", "name": "会员插件", "version": "1.0.0",
            "category": "官方能力包", "tier": "member", "description": "",
            "url": "https://astroswarm.cn/plugins/member-b.zip", "sha256": "abc",
        },
    }
    pp._refresh_market_list()
    from PySide6.QtCore import Qt
    rows = [pp.market_list.item(i) for i in range(pp.market_list.count())]
    entries = [it for it in rows if it.data(Qt.UserRole)]        # 分组标题行没有插件 id
    assert len(entries) == 2, [it.text() for it in rows]
    texts = [it.text() for it in entries]
    assert all("[免费]" in t for t in texts), texts
    assert not any("买断" in t for t in texts), texts
    for it in entries:
        pp.market_list.setCurrentItem(it)
        app.processEvents()
    pp.market_list.setCurrentItem(entries[0])
    app.processEvents()
    pp.deleteLater()
    print("OK ui plugins_all_free")


def test_account_window_top_left():
    """星群账号独立窗口：非模态、默认贴屏幕左上角、含登录控件。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.account_window import AccountWindow
    from qbotmanager.ui.pages.common import PageContext

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_acctwin_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    win = AccountWindow(ctx)
    win.show()
    app.processEvents()
    assert win.x() <= 20 and win.y() <= 20, (win.x(), win.y())
    assert win.acct_email is not None
    assert win.acct_password is not None
    assert win.btn_account.text() in ("登录并同步", "退出账号")
    win.close()
    print("OK ui account_window_top_left")


def test_main_window_account_corner_entry():
    """主界面左上角有星群账号入口，点击打开个人中心窗口（贴左上角）。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_acctcorner_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()
    assert hasattr(win, "btn_account_side")
    win._open_account_window()
    app.processEvents()
    assert win._account_window is not None
    assert win._account_window.isVisible()
    assert win._account_window.x() <= 20 and win._account_window.y() <= 20
    win._account_window.close()
    win.close()
    print("OK ui main_window_account_corner")


def test_llbot_tutorial_bundled():
    """QQ 配置页有接入教程入口；内置教程含免责声明和四张截图。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow
    from qbotmanager.ui.pages.tutorial_dialog import TUTORIAL_DIR, TutorialDialog

    assert (TUTORIAL_DIR / "index.html").is_file()
    pngs = sorted(TUTORIAL_DIR.glob("*.png"))
    assert len(pngs) == 4, [p.name for p in pngs]

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_tut_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()
    rp = win.pages[3]
    assert hasattr(rp, "btn_tutorial")
    assert "接入教程" in rp.btn_tutorial.text()

    dlg = TutorialDialog()
    dlg.show()
    app.processEvents()
    assert "#ffffff" in dlg.browser.styleSheet(), "教程窗口应强制白底（防深色主题黑底）"
    text = dlg.browser.toPlainText()
    assert "重要声明" in text
    assert "违反腾讯用户协议" in text
    assert "星群不提供内嵌式 QQ 通讯协议" in text
    assert "第 4 步" in text
    assert "常见问题" in text
    dlg.close()
    win.close()
    print("OK ui llbot_tutorial_bundled")


def test_settings_page_developer_mode_requires_confirm():
    """开发者模式开启前必须确认免责声明，拒绝则回弹。"""
    from PySide6.QtWidgets import QApplication, QMessageBox

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.settings_page import SettingsPage

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_devset_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    sp = SettingsPage(ctx)
    assert hasattr(sp, "chk_developer_mode")
    orig = QMessageBox.question
    try:
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        sp.chk_developer_mode.setChecked(True)
        assert not sp.chk_developer_mode.isChecked(), "拒绝免责声明后应回弹"
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        sp.chk_developer_mode.setChecked(True)
        assert sp.chk_developer_mode.isChecked(), "确认免责声明后应保持开启"
    finally:
        QMessageBox.question = orig
    sp.deleteLater()
    print("OK ui settings_page_developer_mode_requires_confirm")


def test_primary_disabled_uses_current_accent():
    """主按钮禁用态颜色必须跟随当前皮肤，不能写死默认蓝。"""
    import qbotmanager.ui.theme as theme_mod
    orig_cache = dict(theme_mod._QSS_CACHE)
    try:
        theme_mod._QSS_CACHE.clear()
        qss = theme_mod.build_qss("glass", "default", "midnight")
        assert "rgba(139,92,246,0.35)" in qss, "禁用态应使用当前强调色的半透明"
        assert "rgba(61,107,255,0.35)" not in qss, "禁用态不应写死默认蓝"
    finally:
        theme_mod._QSS_CACHE.clear()
        theme_mod._QSS_CACHE.update(orig_cache)
    print("OK ui primary_disabled_uses_current_accent")


def test_theme_tokens_contrast_and_sidebar():
    """TEXT_3 对比度提升；侧边栏为纯胶囊选中态，不再有左边框混搭。"""
    import qbotmanager.ui.theme as theme_mod
    assert theme_mod.TEXT_3 == "#7E8A9C", "TEXT_3 应提升对比度"
    assert theme_mod.THEMES["slate"]["TEXT_3"] == "#7E8A9C", "slate 皮肤 TEXT_3 应同步"
    orig_cache = dict(theme_mod._QSS_CACHE)
    try:
        theme_mod._QSS_CACHE.clear()
        qss = theme_mod.build_qss("glass")
        assert "border-left: 3px solid" not in qss, "侧边栏不应再有左边框"
        assert "QPushButton:focus" in qss, "按钮应有键盘焦点态"
    finally:
        theme_mod._QSS_CACHE.clear()
        theme_mod._QSS_CACHE.update(orig_cache)
    print("OK ui theme_tokens_contrast_and_sidebar")


def test_beginner_mode_nav():
    """小白模式默认隐藏高级页面，关闭后全部恢复，且首页隐藏飞书/纸飞机卡片。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_beginner_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()

    assert s.beginner_mode is True
    # 本用例只测「小白模式」的显隐：先配好 AI 密钥，免得被「没配密钥只留配置提示」
    # 的门禁挡住（那条另有 test_brain_setup_gate_and_model_combo 专门测）
    from qbotmanager.core import ai_config
    ai_config.save_config(s, api_key="sk-test", api_url="https://api.deepseek.com",
                          model="deepseek-chat")
    brain = win.pages[6]
    brain.load_config()
    app.processEvents()
    # 通道详情页（飞书/微信/QQ/纸飞机）已收进「接入」：本来就不占侧栏一行
    assert win.nav_row_hidden("飞书"), "通道详情页不该单独占一行"
    assert win.nav_row_hidden("插件"), "小白模式应隐藏插件"
    assert win.nav_row_hidden("日志"), "小白模式应隐藏日志"

    home = win.pages[0]
    feishu_card = home._cards_layout.itemAt(2).widget()
    assert feishu_card is not None and feishu_card.isHidden(), "小白模式首页应隐藏飞书卡"

    brain = win.pages[6]
    # 2026-10-07 人设整合：智能体档案 / 人设工坊 并进了「人设与主动聊天」这张卡，
    # 两个旧块不再是独立卡片，也不再由小白模式开关（谁显示由胶囊的预设/自定义决定）；
    # 小白模式要藏的仍是记忆与 MCP 那一块。
    assert not brain._persona_panel.isHidden(), "人设卡片不该被小白模式藏掉"
    assert brain.persona_seg is not None, "人设卡片顶部要有胶囊分段控件"
    assert brain._mem_panel.isHidden(), "小白模式 AI 大脑应隐藏 MCP"
    assert brain.chk_feishu.isHidden(), "小白模式 AI 大脑应隐藏飞书平台开关"

    sp = win.pages[win._page_index["设置"]]
    from qbotmanager.ui.widgets import ToggleSwitch
    assert isinstance(sp.chk_beginner_mode, ToggleSwitch), "小白模式应为椭圆滑块开关"
    assert not hasattr(sp, "chk_nonebot_enabled"), "NoneBot 开关应已移除"
    assert hasattr(sp, "btn_account"), "星群账号应保留单一登录/退出按钮"
    assert not hasattr(sp, "btn_sync"), "手动同步按钮应已移除"
    assert win._account_sync_timer.interval() == 3600000, "激活状态应每小时自动同步"
    assert win._device_check_timer.interval() == 900000, "被踢检测应每 15 分钟执行"

    # 通过设置页滑块走完整 UI 链路切换，验证点击后立即生效
    sp.chk_beginner_mode.setChecked(False)
    app.processEvents()
    # 入口都还在（2026-10-02 第一版藏了插件行，老板找不到，已改回各占一行）
    assert not win.nav_row_hidden("消息中心"), "关闭小白模式应显示消息中心"
    assert not win.nav_row_hidden("插件"), "关闭小白模式应显示插件"
    assert not feishu_card.isHidden(), "关闭小白模式首页应显示飞书卡"
    assert not brain._mem_panel.isHidden(), "关闭小白模式 AI 大脑应显示 MCP"
    assert not brain.chk_feishu.isHidden(), "关闭小白模式 AI 大脑应显示飞书平台开关"

    sp.chk_beginner_mode.setChecked(True)
    app.processEvents()
    assert win.nav_row_hidden("日志"), "重新开启小白模式应再次隐藏日志（收在全局管理里）"
    assert feishu_card.isHidden(), "重新开启小白模式首页应隐藏飞书卡"

    win.close()
    print("OK ui beginner_mode_nav")


def test_run_page_reverse_ws_panel():
    """QQ 页提供反向 WS：默认反向模式、地址可直接复制、模式切换面板联动。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_reverse_ws_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.qq_channel = "onebot"
    s.qq_onebot_mode = "reverse"
    s.qq_onebot_listen_host = "127.0.0.1"
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()

    rp = win.pages[3]
    assert rp.combo_onebot_mode.currentData() == "reverse"
    assert not rp.panel_reverse.isHidden(), "默认应显示反向 WS 面板"
    assert rp.panel_forward.isHidden(), "默认应隐藏正向 WS 面板"
    assert "12111" in rp.edit_reverse_port.text()
    assert rp.edit_reverse_url.text() == "ws://127.0.0.1:12111/onebot/v11/ws"
    assert rp.btn_copy_reverse_url.text() == "复制地址"

    # 切到正向：反向面板隐藏，正向字段保留
    rp.combo_onebot_mode.setCurrentIndex(1)
    app.processEvents()
    assert rp.panel_reverse.isHidden(), "正向模式应隐藏反向面板"
    assert not rp.panel_forward.isHidden(), "正向模式应显示正向面板"
    assert rp.edit_onebot_host.text() == "127.0.0.1"

    # 监听地址改为 0.0.0.0 时，给协议端粘贴的地址仍是本机可连地址
    rp.combo_onebot_mode.setCurrentIndex(0)
    rp.edit_onebot_listen_host.setText("0.0.0.0")
    assert rp.edit_reverse_url.text() == "ws://127.0.0.1:12111/onebot/v11/ws"

    win.close()
    print("OK ui run_page_reverse_ws")


def test_wechat_channel_is_free_for_everyone():
    """微信通道已免费开放（2026-10-01）：界面不再有「开通」按钮，扫码登录直接可用。"""
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_wxfree_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    # 隔离本机真实授权：这个测试要证明"没有任何授权也能用微信通道"
    from qbotmanager.core import license as lic
    orig_license_file = lic.license_file
    lic.license_file = lambda: tmp / "license.json"      # 不存在 = 无授权
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()

    gate = lic.feature_gate()
    assert gate["member"] is True, "没授权也必须能用微信通道"
    assert "免费开放" in gate["reason"], gate

    home = win.pages[0]
    assert not hasattr(home, "wx_pay_btn"), "首页不该再留「开通微信通道」按钮"

    wechat = next(
        (p for p in win.pages if type(p).__name__ == "WechatPage"), None
    )
    assert wechat is not None
    wechat.refresh_status()
    app.processEvents()
    assert wechat.btn_pay.isHidden(), "微信页的「开通」按钮不该出现"
    assert wechat.gate_lock.isHidden(), "不该再显示「未开通」的提示条"
    assert "未开通" not in wechat.qr_box.text(), wechat.qr_box.text()
    assert wechat.btn_qr.isEnabled(), "扫码登录按钮要能用"

    sp = win.pages[win._page_index["设置"]]
    assert "未开通" not in sp.info_labels["license_status"].text(), \
        sp.info_labels["license_status"].text()
    assert "机器码" not in sp.info_labels["license_status"].toolTip()

    lic.license_file = orig_license_file
    win.close()
    print("OK ui wechat_channel_free")


def test_beginner_toggle_survives_appearance_error():
    """外观应用抛异常时，小白模式切换仍必须生效（修复按钮“不好使”）。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_beginner_err_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = True
    s.save()
    tasks = TaskManager()
    win = MainWindow(s, tasks)
    app.processEvents()
    sp = win.pages[win._page_index["设置"]]

    def boom():
        raise RuntimeError("appearance boom")

    sp.ctx.apply_appearance = boom
    sp.chk_beginner_mode.setChecked(False)
    app.processEvents()
    assert s.beginner_mode is False, "外观异常时小白模式仍应保存关闭状态"
    assert not win.nav_row_hidden("插件"), "外观异常时高级页面仍应显示"

    sp.chk_beginner_mode.setChecked(True)
    app.processEvents()
    assert s.beginner_mode is True, "外观异常时重新开启小白模式仍应保存"
    assert win.nav_row_hidden("插件"), "外观异常时重新开启仍应隐藏高级页面"
    win.close()
    print("OK ui beginner_toggle_survives_appearance_error")


def test_toggle_switch_click_area():
    """小白模式滑块：真实鼠标点击右侧滑块必须能切换（防 hitButton 只认左侧小方框）。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from qbotmanager.ui.widgets import ToggleSwitch

    app = QApplication.instance() or QApplication([])
    sw = ToggleSwitch("小白模式")
    sw.resize(220, 28)
    sw.show()
    app.processEvents()
    assert sw.isChecked() is False
    # 点击右侧滑块中心（可见开关的位置，而不是左侧隐藏的小方框）
    QTest.mouseClick(sw, Qt.LeftButton, pos=sw.rect().center() + QPoint(80, 0))
    app.processEvents()
    assert sw.isChecked() is True, "点击滑块本体应能打开小白模式"
    QTest.mouseClick(sw, Qt.LeftButton, pos=sw.rect().center() + QPoint(80, 0))
    app.processEvents()
    assert sw.isChecked() is False, "再次点击滑块本体应能关闭小白模式"
    sw.close()
    print("OK ui toggle_switch_click_area")


def test_access_page_lists_all_channels():
    """接入页：四个通道各一行、状态带文字、未开放的禁用，可展开就地配置。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow
    from qbotmanager.ui.pages.channel_panels import QQChannelPanel, WeChatChannelPanel

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_access_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    win.switch_page("接入")
    app.processEvents()
    ap = win.pages[1]
    collapsed_min = ap.scroll.widget().minimumHeight()
    assert set(ap._rows) == {"qq", "wechat", "feishu", "telegram"}, ap._rows.keys()

    # 未开放的通道：按钮禁用且写着「规划中」，不做成灰的可点按钮
    for key in ("feishu", "telegram"):
        btn = ap._rows[key][1]
        assert btn.text() == "规划中" and not btn.isEnabled(), key

    # 状态必须带文字（不能只有颜色）
    for key in ("qq", "wechat"):
        badge = ap._rows[key][0]
        text = badge.label.text()
        assert text and ("·" in text), (key, text)
        assert any(word in text for word in
                   ("运行中", "已停止", "未连接", "未登录", "已连接", "未安装",
                    "未开通", "规划中")), text

    # 「去配置」跳到真实通道页
    ap._rows["qq"][1].click()
    app.processEvents()
    assert not ap._bodies["qq"].isHidden(), "点「配置」应就地展开，而不是跳页"
    assert isinstance(ap._panels["qq"], QQChannelPanel)
    assert ap._panels["qq"].edit_reverse_url.text() == "ws://127.0.0.1:12111/onebot/v11/ws"
    assert ap._rows["qq"][1].text() == "收起"

    # 展开区里的「打开完整页」才跳页（QQ/微信 独立页仍在）
    ap._links["qq"].click()
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["QQ"]

    ap._rows["wechat"][1].click()
    app.processEvents()
    assert not ap._bodies["wechat"].isHidden(), "微信也应能就地展开"
    assert isinstance(ap._panels["wechat"], WeChatChannelPanel)
    assert ap._bodies["qq"].isHidden(), "一次只展开一条通道"
    ap._links["wechat"].click()
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["微信"]

    # 再点一次收起：展开区高度必须归零、滚动内容回到基线高度
    # （只 setVisible(False) 的话 Qt 会留着展开时的 sizeHint，行间出现大片空白）
    ap._rows["wechat"][1].click()
    app.processEvents()
    assert ap._bodies["wechat"].isHidden() and ap._bodies["wechat"].height() == 0
    assert ap.scroll.widget().minimumHeight() == collapsed_min

    # 小白模式：接入页留着当入口，微信/QQ 页面收起来
    assert "接入" not in MainWindow.ADVANCED_PAGES
    for name in ("微信", "QQ"):
        assert name in MainWindow.ADVANCED_PAGES, name
    win.deleteLater()
    print("OK ui access_page")


def test_brain_page_ai_memory_block():
    """AI 大脑页的内置 AI 全局记忆：能列出、能删一条（留备份）、能清空全局。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core import ai_config, ai_memory
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_aimemui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    # 先造一份记忆（含全局与用户两组），再让页面去读
    ai_memory._save(s, {"global": ["他怕辣", "喜欢美式咖啡"],
                        "users": {"10001": ["住杭州"]}})

    win = MainWindow(s, TaskManager())
    win.switch_page("AI 大脑")
    app.processEvents()
    bp = win.pages[6]
    bp._refresh_ai_memory()
    app.processEvents()

    texts = [bp.ai_mem_list.item(i).text() for i in range(bp.ai_mem_list.count())]
    assert any("他怕辣" in x for x in texts), texts
    assert any("用户 10001" in x for x in texts), texts
    assert "共 3 条" in bp.ai_mem_tip.text(), bp.ai_mem_tip.text()

    # 选中一条删掉（确认框用 monkeypatch 直接放行，避免弹窗卡住测试）
    from PySide6.QtWidgets import QMessageBox

    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        idx = next(i for i, t in enumerate(texts) if "他怕辣" in t)
        bp.ai_mem_list.setCurrentRow(idx)
        bp._delete_ai_memory()
        app.processEvents()
    finally:
        QMessageBox.question = orig

    left = ai_memory.load(s)
    assert left["global"] == ["喜欢美式咖啡"], left
    assert left["users"] == {"10001": ["住杭州"]}, "删全局不能动用户记忆"
    backups = list(ai_config.memory_file(s).parent.glob("aichat_memory.json.bak.*"))
    assert backups, "改动前必须留备份"

    # 清空全局（用户记忆保留）
    orig = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        bp._clear_ai_memory()
        app.processEvents()
    finally:
        QMessageBox.question = orig
    after = ai_memory.load(s)
    assert after["global"] == [] and after["users"] == {"10001": ["住杭州"]}

    win.deleteLater()
    print("OK ui brain_ai_memory")


def test_nav_groups_match_console():
    """侧栏分组与控制台一致（机器人 / 工坊 / 人格 / 设置），「全局管理」是真页面不是占位。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_nav_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()

    assert [t for t, _items in MainWindow.NAV_GROUPS] == ["机器人", "工坊", "人格", "设置"]
    visible = [n for _t, items in MainWindow.NAV_GROUPS for n in items]
    # 2026-10-02 合并后：低频页收进父页（通道→接入、插件→工坊、消息中心/日志/依赖→全局管理）
    assert visible == ["首页", "接入", "插件", "插件工坊", "AI 大脑",
                       "全局管理", "消息中心", "日志", "依赖", "设置"], visible
    assert set(MainWindow.SUB_NAV_PARENT) == {"微信", "QQ", "飞书", "纸飞机"}
    for child, parent in MainWindow.SUB_NAV_PARENT.items():
        assert child in MainWindow.PAGE_ORDER, child
        assert parent in visible, parent
    assert set(MainWindow.PAGE_ORDER) == set(visible) | set(MainWindow.SUB_NAV_PARENT)
    # 组内顺序与控制台一致：工坊组是「插件 / 插件工坊」，设置组第一项是「全局管理」
    assert MainWindow.NAV_GROUPS[0][1] == ("首页", "接入")
    assert MainWindow.NAV_GROUPS[1][1] == ("插件", "插件工坊")
    assert MainWindow.NAV_GROUPS[2][1] == ("AI 大脑",)
    assert MainWindow.NAV_GROUPS[3][1][0] == "全局管理"

    # 页面下标必须与 PAGE_ORDER 一一对应（加页面时最容易被写歪的地方）
    assert len(win.pages) == len(MainWindow.PAGE_ORDER)
    for i, name in enumerate(MainWindow.PAGE_ORDER):
        assert win._page_index[name] == i, name

    win.switch_page("全局管理")
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["全局管理"]
    assert win.nav_lists[3].currentRow() == 0, "选中行要跟着切到「设置」组第一项"

    # 进通道详情页：页面还在，但侧栏高亮归属的「接入」行（不再有独立行）
    win.switch_page("微信")
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["微信"]
    assert win.nav_lists[0].currentRow() == 1, "通道详情页要高亮「接入」那一行"
    assert win.nav_lists[3].currentRow() == -1, "切到机器人组后设置组不该还高亮"

    # 收进父页的页面也要能高亮父页那行（插件 → 工坊）
    win.switch_page("插件")
    app.processEvents()
    assert win.stack.currentIndex() == win._page_index["插件"]
    assert win.nav_lists[1].currentRow() == 0, "插件页要收在「工坊」组里高亮"

    # 一次只有一组能高亮
    win.nav_lists[0].setCurrentRow(0)
    app.processEvents()
    assert win.nav_lists[3].currentRow() == -1, "切回机器人组后设置组不该还高亮"
    assert win.nav_lists[1].currentRow() == -1, "切回机器人组后工坊组不该还高亮"
    assert win.stack.currentIndex() == win._page_index["首页"]
    win.deleteLater()
    print("OK ui nav_groups")


def test_services_page_controls():
    """全局管理页：机器人开关 + DSH 状态行 + 运维细节（端口 / 目录）默认收起。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_services_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    win.switch_page("全局管理")
    app.processEvents()
    sp = win.pages[win._page_index["全局管理"]]

    assert hasattr(sp, "bot_badge") and hasattr(sp, "btn_bot")
    assert hasattr(sp, "dsh_badge")
    assert sp.btn_bot.text() in ("启动", "重启")
    assert "·" in sp.bot_badge.label.text() or sp.bot_badge.label.text(), sp.bot_badge.label.text()

    # 运维细节默认收起；展开后端口 / 目录必须是真值，不写死
    assert not sp.details.isVisibleTo(sp), "运维细节默认要收起"
    assert sp.detail_labels["port"].text() == str(s.nonebot_port)
    assert str(s.logs_dir) in sp.detail_labels["logs_dir"].text()
    assert str(s.bot_dir) in sp.detail_labels["bot_dir"].text()
    assert sp.detail_labels["version"].text().startswith("v")
    sp._toggle_details()
    assert sp.details.isVisibleTo(sp) and "▴" in sp.btn_fold.text()
    sp._toggle_details()
    assert not sp.details.isVisibleTo(sp)
    win.deleteLater()
    print("OK ui services_page")


def test_logs_export_writes_file():
    """日志页：能导出单份日志，也能把「全部」日志尾部拼成一份。"""
    import time as _time

    from PySide6.QtWidgets import QApplication, QFileDialog

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_logs_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    (s.logs_dir / "nonebot.log").write_text("机器人日志一行\n", encoding="utf-8")
    (s.logs_dir / "deploy.log").write_text("部署日志一行\n", encoding="utf-8")

    win = MainWindow(s, TaskManager())
    win.switch_page("日志")
    app.processEvents()
    lp = win.pages[win._page_index["日志"]]
    assert lp.btn_export.isEnabled()

    def _export(label, dest):
        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (str(dest), "日志文件 (*.log)"))
        try:
            lp.src_combo.setCurrentText(label)
            lp._export_logs()
        finally:
            QFileDialog.getSaveFileName = orig
        for _ in range(300):
            if dest.exists():
                break
            _time.sleep(0.02)
        assert dest.exists(), f"{label} 导出没落盘：{dest}"

    one = tmp / "one.log"
    _export("NoneBot", one)
    assert "机器人日志一行" in one.read_text(encoding="utf-8")
    assert "部署日志一行" not in one.read_text(encoding="utf-8")

    every = tmp / "every.log"
    _export("全部", every)
    text = every.read_text(encoding="utf-8")
    assert "机器人日志一行" in text and "部署日志一行" in text
    assert "===== nonebot" in text and "===== deploy" in text
    win.deleteLater()
    print("OK ui logs_export")


def test_message_center_shows_scene_and_room():
    """消息中心：标题能区分「QQ 群 12345 / QQ 好友 67890」，并给出条数与会话总数。"""
    import json as _json

    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_msgcenter_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    ctx_file = s.bot_dir / "data" / "ai" / "aichat_context.json"
    ctx_file.parent.mkdir(parents=True, exist_ok=True)
    ctx_file.write_text(_json.dumps({
        "group_12345": [{"content": "群里说的话"}],
        "67890": [{"content": "私聊里说的话"}],
    }, ensure_ascii=False), encoding="utf-8")

    win = MainWindow(s, TaskManager())
    win.switch_page("消息中心")
    app.processEvents()
    mc = win.pages[win._page_index["消息中心"]]
    mc.refresh()
    app.processEvents()
    texts = [mc.list.item(i).text() for i in range(mc.list.count())]
    assert any("QQ 群 12345" in t for t in texts), texts
    assert any("QQ 好友 67890" in t for t in texts), texts
    assert any("1 条" in t for t in texts), texts
    assert "共 2 个会话" in mc.stat_label.text(), mc.stat_label.text()
    win.deleteLater()
    print("OK ui message_center")


def test_workshop_page_basics():
    """插件工坊页：未配模型时给大白话提示，按钮状态正确，小白模式收起进阶面板。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_ws_page_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()
    wp = win.pages[win._page_index["插件工坊"]]
    assert "强模型" in wp.status_label.text(), wp.status_label.text()
    assert wp.btn_build.isEnabled() is False
    assert wp.btn_install.isEnabled() is False
    assert wp.need_edit.isEnabled() is True
    # 整页可滚动 + 方案/结果框各自内滚（内容长了不能把按钮挤出屏幕）
    from PySide6.QtWidgets import QScrollArea
    assert isinstance(wp.page_scroll, QScrollArea)
    assert isinstance(wp.plan_text.parentWidget().parentWidget(), QScrollArea)
    assert isinstance(wp.result_text.parentWidget().parentWidget(), QScrollArea)
    # 小白模式下"已装/回滚"面板收起，但工坊入口保留（它就是给小白用的）
    assert win.nav_row_hidden("插件工坊") is False
    assert wp.installed_panel.isHidden()
    win.deleteLater()
    print("OK ui workshop_page")


def test_qq_official_channel_restored():
    """QQ 通道恢复官方机器人：两种通道都能选、表单联动、保存写回，接入页同样能切。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_qq_official_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "official"
    s.qq_official_appid = "102000001"
    s.qq_official_token = "tok"
    s.qq_official_secret = "sec"
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()

    rp = win.pages[win._page_index["QQ"]]
    datas = [rp.combo_channel.itemData(i) for i in range(rp.combo_channel.count())]
    assert datas[:2] == ["official", "onebot"], datas
    assert rp.combo_channel.currentData() == "official", "设置里是官方通道就应回显官方"
    assert not rp.panel_official.isHidden(), "官方通道应显示 AppID/Token/AppSecret"
    assert rp.panel_onebot.isHidden(), "官方通道下第三方表单应收起"
    assert rp.edit_official_appid.text() == "102000001"
    assert not rp.dsh_glass.isHidden(), "官方通道下 dsh 卡片应可见"

    # 切到第三方：官方表单收起、OneBot 表单展开，dsh 卡片跟着收起
    rp.combo_channel.setCurrentIndex(datas.index("onebot"))
    app.processEvents()
    assert rp.panel_official.isHidden() and not rp.panel_onebot.isHidden()
    assert rp.dsh_glass.isHidden(), "第三方通道下 dsh 卡片应收起"

    # 切回官方并保存：通道选择与官方凭证写回 settings
    rp.combo_channel.setCurrentIndex(datas.index("official"))
    rp.edit_official_token.setText("tok2")
    rp.btn_save_creds.click()
    app.processEvents()
    assert s.qq_channel == "official"
    assert s.qq_official_token == "tok2"
    assert not rp.dsh_glass.isHidden()

    # 接入页的 QQ 展开区也能切通道（小白模式下 QQ 独立页在侧栏是收起的）
    ap = win.pages[1]
    ap._rows["qq"][1].click()
    app.processEvents()
    panel = ap._panels["qq"]
    assert not panel.panel_official.isHidden(), "默认官方：展开区应直接露官方凭证"
    panel.combo_channel.setCurrentIndex(1)
    app.processEvents()
    app.processEvents()
    assert panel.panel_official.isHidden() and not panel.panel_onebot.isHidden()
    assert ap._bodies["qq"].height() > 0, "切通道后展开区高度要重算，不能塌成 0"

    win.deleteLater()
    print("OK ui qq_official_channel_restored")


def test_access_page_channel_switch_survives_refresh():
    """接入页切「官方机器人」后，周期状态刷新不能把选项顶回第三方。

    用户报的 bug：接入页每 2 秒刷一次状态，刷新里顺手把 QQ 表单按配置文件重灌，
    刚切到官方两秒后自己跳回第三方。修法：用户改过（未保存）就不回显，
    并且周期刷新不再走「重灌表单」那条路。
    """
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_chan_refresh_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.qq_channel = "onebot"
    s.save()
    win = MainWindow(s, TaskManager())
    win.switch_page("接入")
    app.processEvents()
    ap = win.pages[1]
    ap._rows["qq"][1].click()          # 展开 QQ 通道
    app.processEvents()
    panel = ap._panels["qq"]
    assert panel.combo_channel.currentData() == "onebot", "设置里是第三方就回显第三方"

    # 用户手动切到官方（offscreen 下 activated 不一定发，直接标记用户改过）
    panel.combo_channel.setCurrentIndex(panel.combo_channel.findData("official"))
    panel._mark_dirty()
    app.processEvents()
    assert panel.combo_channel.currentData() == "official"
    assert not panel.panel_official.isHidden() and panel.panel_onebot.isHidden()

    # 模拟 2 秒一次的周期刷新：选项必须留在官方
    ap.refresh_status()
    app.processEvents()
    assert panel.combo_channel.currentData() == "official", \
        "周期刷新把用户选的通道顶回去了"

    # 保存后 dirty 清掉，此时再刷新才允许按配置回显（回显就是刚保存的官方）
    panel.btn_save_creds.click()
    app.processEvents()
    assert s.qq_channel == "official"
    assert panel._dirty is False, "保存成功后应清掉未保存标记"
    ap.refresh_status()
    app.processEvents()
    assert panel.combo_channel.currentData() == "official"

    # 收起再展开：这时才按配置重新回显
    ap._rows["qq"][1].click()
    app.processEvents()
    ap._rows["qq"][1].click()
    app.processEvents()
    assert panel.combo_channel.currentData() == "official"

    win.deleteLater()
    print("OK ui access_page_channel_switch_survives_refresh")


def test_run_card_follows_qq_channel_switch():
    """运行页状态卡跟着通道走：切官方后不能残留第三方的反向地址与协议端文案。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_run_card_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "onebot"
    s.qq_onebot_mode = "reverse"
    s.qq_official_appid = "102000777"
    s.qq_official_token = "tok"
    s.qq_official_secret = "sec"
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()
    rp = win.pages[win._page_index["QQ"]]
    rp.refresh_status()
    app.processEvents()
    assert rp.qq_mode.text() == "第三方 OneBot（反向 WS）", rp.qq_mode.text()
    assert rp.qq_appid_label.text() == "反向地址", rp.qq_appid_label.text()
    assert "协议端" in rp.qq_tip.text()
    assert "OneBot" in rp.qq_badge.label.text()

    # 用户切到官方通道并保存：卡片必须立刻换成官方 AppID/状态
    rp.combo_channel.setCurrentIndex(rp.combo_channel.findData("official"))
    rp.btn_save_creds.click()
    app.processEvents()
    assert s.qq_channel == "official"
    rp.refresh_status()
    app.processEvents()
    assert rp.qq_mode.text() == "QQ 官方机器人", rp.qq_mode.text()
    assert rp.qq_appid_label.text() == "AppID", rp.qq_appid_label.text()
    assert rp.qq_appid.text() == "102000777", rp.qq_appid.text()
    assert "等待协议端反向连接" not in rp.qq_state.text(), rp.qq_state.text()
    assert "官方通道不用扫码" in rp.qq_tip.text()
    assert rp.qq_badge.label.text() == "QQ 官方 · 已停止", rp.qq_badge.label.text()

    # 再切回第三方：卡片同样要立即恢复，不能停在官方内容
    rp.combo_channel.setCurrentIndex(rp.combo_channel.findData("onebot"))
    rp.btn_save_creds.click()
    app.processEvents()
    rp.refresh_status()
    app.processEvents()
    assert rp.qq_mode.text().startswith("第三方 OneBot"), rp.qq_mode.text()
    assert rp.qq_appid_label.text() == "反向地址", rp.qq_appid_label.text()
    assert "QQ OneBot" in rp.qq_badge.label.text(), rp.qq_badge.label.text()

    win.deleteLater()
    print("OK ui run_card_follows_qq_channel_switch")


def test_channel_switch_turns_dsh_off():
    """切到第三方通道保存：dsh 自动关闭（勾选框跟着走）、.env 不再写官方凭证。

    这是「随意切换通道」的护栏：以前切到第三方后 dsh 还挂着启用，
    NoneBot 侧又不注册 QQ 适配器，协议端连不上、整条通道哑掉。
    """
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_chan_ui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "official"
    s.dsh_enabled = True
    s.qq_official_appid = "102000001"
    s.qq_official_token = "tok"
    s.qq_official_secret = "sec"
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()

    rp = win.pages[win._page_index["QQ"]]
    assert rp.chk_dsh_enable.isChecked(), "设置里 dsh 是开着的，卡片要回显勾选"
    datas = [rp.combo_channel.itemData(i) for i in range(rp.combo_channel.count())]
    rp.combo_channel.setCurrentIndex(datas.index("onebot"))
    app.processEvents()
    rp.btn_save_creds.click()
    app.processEvents()

    assert s.qq_channel == "onebot"
    assert s.dsh_enabled is False, "切到第三方后 dsh 必须自动关闭"
    assert not rp.chk_dsh_enable.isChecked(), "勾选框要跟着 settings 走"
    assert rp.dsh_glass.isHidden(), "第三方通道下 dsh 卡片收起"
    env_text = (s.bot_dir / ".env").read_text(encoding="utf-8")
    assert "QQ_BOTS=" not in env_text, "第三方通道下不能再写官方凭证"
    adapters = (s.bot_dir / "pyproject.toml").read_text(
        encoding="utf-8").split("adapters = [", 1)[1].split("]", 1)[0]
    assert "nonebot.adapters.onebot.v11" in adapters
    assert "nonebot.adapters.qq" not in adapters
    win.deleteLater()
    print("OK ui channel_switch_turns_dsh_off")


def test_update_button_states():
    """标题栏更新按钮：无新版隐藏 → 发现新版「有新版本」→ 检查中 → 已下好「立即更新」。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_updbtn_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()

    btn = win.titlebar.btn_update
    assert btn.text() == "有新版本", btn.text()
    assert not btn.property("state"), btn.property("state")
    assert btn.isEnabled()
    assert not btn.isVisibleTo(win), "没发现新版本时不该占标题栏"

    win._update_info = {"ok": True, "newer": True, "latest": "9.9.9"}
    win._set_update_button_state(newer=True)
    assert btn.text() == "有新版本", btn.text()
    assert btn.property("state") == "new", btn.property("state")
    assert "9.9.9" in btn.toolTip(), btn.toolTip()
    assert btn.isVisibleTo(win), "发现新版本要露出红色胶囊"

    win._set_update_button_state(busy=True)
    assert btn.text() == "检查中", btn.text()
    assert btn.property("state") == "busy"
    assert not btn.isEnabled(), "检查中不该让用户重复点"

    win._set_update_button_state(ready=True)
    assert btn.text() == "立即更新", btn.text()
    assert btn.property("state") == "ready"
    assert btn.isEnabled()

    win._set_update_button_state()
    assert btn.text() == "有新版本" and not btn.property("state")
    assert not btn.isVisibleTo(win)

    # 已知有新版本时点按钮：直接进更新流程（不重新联网）
    offered = []
    win._offer_update = lambda res: offered.append(res)
    win._update_info = {"ok": True, "newer": True, "latest": "9.9.9"}
    win.check_update_interactive()
    app.processEvents()
    assert offered and offered[0].get("latest") == "9.9.9", offered

    # 测试模式（QBM_NO_UPDATE=1）下点一下只提示，不起线程、不弹框
    win._update_info = {}
    win.check_update_interactive()
    app.processEvents()
    assert not win._update_checking
    assert win._update_dialog is None
    win._update_info = {"ok": True, "newer": True, "latest": "9.9.9"}

    # 进度卡片：下载按百分比，装到一半转"正在安装"（真卡片是点阵 splash，测试里换假的）
    import threading

    class _FakeCard:
        def __init__(self):
            self.progress = None
            self.text = ""

        def set_progress(self, value):
            self.progress = value

        def set_text(self, text):
            self.text = text

        def close_card(self):
            self.text = "<closed>"

    card = _FakeCard()
    win._update_card = card
    win._update_cancel_ev = threading.Event()
    win._update_busy = True
    win._on_update_progress(5 * 1024 * 1024, 10 * 1024 * 1024)
    assert card.progress == 0.5, card.progress
    assert "50%" in card.text, card.text
    win._on_update_progress(0, 0)
    assert card.progress == 1.0 and "正在安装" in card.text, (card.progress, card.text)
    win._cancel_update()
    assert win._update_cancel_ev.is_set(), "取消要把取消事件置位"
    win._close_update_dialog()
    assert win._update_card is None and not win._update_busy
    win.close()
    print("OK ui update_button_states")


def test_dsh_control_buttons():
    """v1.2.8：dsh 卡片自带启停按钮 —— 装完就地启动，不用重启全部服务。"""
    import shutil as _shutil

    from PySide6.QtWidgets import QApplication

    from qbotmanager.core import dsh as dsh_mod
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_dsh_btn_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "official"
    s.qq_official_appid = "102000002"
    s.qq_official_token = "tok"
    s.qq_official_secret = "sec"
    s.dsh_enabled = True
    s.save()
    # 伪造"已安装"：dsh 运行时入口 + qqbot profile 目录
    dsh_mod.dsh_js(s).parent.mkdir(parents=True, exist_ok=True)
    dsh_mod.dsh_js(s).write_text("// stub", encoding="utf-8")
    dsh_mod.profile_dir(s).mkdir(parents=True, exist_ok=True)

    win = MainWindow(s, TaskManager())
    app.processEvents()
    rp = win.pages[win._page_index["QQ"]]
    rp.refresh_status()
    app.processEvents()
    assert rp.btn_dsh_toggle.text() == "启动 dsh", rp.btn_dsh_toggle.text()
    assert rp.btn_dsh_toggle.isEnabled(), "已安装就该能一键启动"
    assert not rp.btn_dsh_stop.isEnabled(), "没在运行就不该能停止"
    assert "启动 dsh" in rp.dsh_state_lbl.text(), rp.dsh_state_lbl.text()

    # 运行中：按钮变「重启 dsh」、停止可用
    rp.ctx.manager.dsh_running = lambda: True
    rp.refresh_status()
    app.processEvents()
    assert rp.btn_dsh_toggle.text() == "重启 dsh"
    assert rp.btn_dsh_stop.isEnabled()

    # 未安装：主按钮变成「安装并启动 dsh」（点一下先装再起，不再弹「请先点安装」）
    rp.ctx.manager.dsh_running = lambda: False
    _shutil.rmtree(dsh_mod.dsh_dir(s), ignore_errors=True)
    rp.refresh_status()
    app.processEvents()
    assert rp.btn_dsh_toggle.text() == "安装并启动 dsh", rp.btn_dsh_toggle.text()
    assert rp.btn_dsh_toggle.isEnabled()
    assert rp.btn_dsh_install.isHidden(), "没装时不再重复放一个安装按钮"
    # 首屏 dsh 动作按钮不超过 3 个（装 / 启 / 停），其余都在折叠区
    shown = [b for b in rp.dsh_glass.findChildren(_QPushButton)
             if b.isVisibleTo(rp.dsh_glass) and b.text().strip()
             and b is not rp.dsh_fold.btn]
    assert len(shown) <= 3, [b.text() for b in shown]

    # 高级选项默认收起：次要按钮（Node.js / 日志）不占首屏
    assert not rp.dsh_fold._expanded
    assert not rp.dsh_fold._content.isVisible()
    rp.dsh_fold.toggle()
    app.processEvents()
    assert rp.dsh_fold._expanded
    assert "▴" in rp.dsh_fold.btn.text()
    rp.dsh_fold.toggle()

    win.deleteLater()
    print("OK ui dsh_control_buttons")


def test_brain_setup_gate_and_model_combo():
    """AI 大脑：没填密钥只留「先填 AI 配置」；保存后展开；模型下拉吃真实列表。

    用户 2026-09-29：①模型预设已过期 —— 改成填完密钥自动拉服务商模型列表；
    ②没配好 AI 之前整页只显示要填配置的提示，配好了再展开。
    """
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_gate_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    win.switch_page("AI 大脑")
    app.processEvents()
    brain = win.pages[win._page_index["AI 大脑"]]

    # ① 没填密钥：只显示接口配置 + 「先填 AI 配置」，人设/记忆先藏起来
    assert brain.is_ai_configured() is False
    assert not brain.notice_panel.isHidden(), "没配密钥时应显示「先填 AI 配置」"
    assert brain._persona_panel.isHidden(), "没配密钥时人设面板不该出现"
    assert brain._mem_panel.isHidden()

    # ② 填好密钥保存 → 整页展开
    brain.ai_key_edit.setText("sk-test-123")
    brain._save_config()
    app.processEvents()
    assert brain.is_ai_configured() is True
    assert brain.notice_panel.isHidden()
    assert not brain._persona_panel.isHidden(), "配好密钥后人设面板要放回来"

    # ③ 模型下拉：上次拉到的列表能直接选，当前模型保持在输入框里
    brain._cache_models("https://api.deepseek.com", ["deepseek-chat", "deepseek-reasoner"])
    brain._fill_model_combo("https://api.deepseek.com", "deepseek-reasoner")
    items = [brain.ai_model_edit.itemText(i) for i in range(brain.ai_model_edit.count())]
    assert "deepseek-chat" in items, items
    assert brain.ai_model_edit.currentText() == "deepseek-reasoner"
    assert s.ai_models_cache.get("https://api.deepseek.com") == [
        "deepseek-chat", "deepseek-reasoner"], s.ai_models_cache

    # ④ 下拉可编辑：手填的模型名也能存进配置（服务商上新不用等软件更新）
    brain.ai_model_edit.setEditText("deepseek-v4-flash-vision-exp")
    brain._save_config()
    app.processEvents()
    assert ai_config.current_config(s).get("model") == "deepseek-v4-flash-vision-exp"

    # ⑤ 把密钥清掉再保存 → 重新回到「只显示要填配置」
    brain.ai_key_edit.setText("")
    brain._save_config()
    app.processEvents()
    assert brain.is_ai_configured() is False
    assert not brain.notice_panel.isHidden()
    assert brain._persona_panel.isHidden()

    win.deleteLater()
    print("OK ui brain_setup_gate_and_model_combo")


def test_workshop_setup_gate():
    """插件工坊：没配生成模型时整页只留「先配生成模型」那张卡。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.core.workshop import config as ws_config
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_ws_gate_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    win.switch_page("插件工坊")
    app.processEvents()
    wp = win.pages[win._page_index["插件工坊"]]

    assert wp.is_model_ready() is False
    assert not wp.notice_panel.isHidden()
    assert wp.panel.isHidden(), "没配模型时生成区不该出现"
    assert wp.plan_panel.isHidden() and wp.result_panel.isHidden()

    ws_config.save(s, {"provider": "deepseek", "api_key": "sk-x",
                       "api_url": "https://api.deepseek.com", "model": "deepseek-chat"})
    wp.refresh_status()
    app.processEvents()
    assert wp.is_model_ready() is True
    assert wp.notice_panel.isHidden()
    assert not wp.panel.isHidden(), "配好模型后生成区要展开"

    win.deleteLater()
    print("OK ui workshop_setup_gate")


def test_wheel_not_trapped_by_dropdowns():
    """滚轮：展开的下拉列表 / 闭合的下拉框都不许卡住页面滚动，也不许偷偷改值。

    三种情形（离屏实测）：
      1. 鼠标压着没展开的下拉框滚 → 页面滚，下拉框的值不动；
      2. 滚展开的短列表（本来就滚不动）→ 列表收起，这一下滚轮落到页面上；
      3. 滚展开的长列表（自己滚得动）→ 列表自己滚，页面不动。
    """
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QScrollArea, QVBoxLayout, QWidget,
    )

    from qbotmanager.ui.widgets import WheelGuard

    app = QApplication.instance() or QApplication([])
    guard = WheelGuard(app)
    app.installEventFilter(guard)

    def wheel(target, notches=-1):
        ev = QWheelEvent(QPointF(4, 4), QPointF(4, 4), QPoint(0, 0),
                         QPoint(0, int(notches * 120)), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(target, ev)
        app.processEvents()

    page = QWidget()
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    body.setMinimumHeight(2400)
    box = QVBoxLayout(body)
    short_combo = QComboBox()
    short_combo.addItems(["全部分类", "官方能力包", "智能体"])
    long_combo = QComboBox()
    long_combo.addItems([f"能力包 {i}" for i in range(80)])
    edit_combo = QComboBox()          # 可编辑：滚轮常落在它内部的输入框上
    edit_combo.setEditable(True)
    edit_combo.addItems(["模型 A", "模型 B", "模型 C"])
    box.addWidget(short_combo)
    box.addWidget(long_combo)
    box.addWidget(edit_combo)
    for _ in range(30):
        box.addWidget(QWidget())
    area.setWidget(body)
    outer = QVBoxLayout(page)
    outer.addWidget(area)
    page.resize(360, 320)
    page.show()
    app.processEvents()

    bar = area.verticalScrollBar()
    assert bar.maximum() > 0, "测试页自己得能滚，否则测不出东西"

    bar.setValue(0)
    wheel(short_combo)
    assert bar.value() > 0, "鼠标压着闭合下拉框时，页面必须照样滚"
    assert short_combo.currentIndex() == 0, "滚轮不许改下拉框的值"

    # 1b) 可编辑下拉框：滚轮落在它内部的输入框上，同样不许拦页面、不许改值
    bar.setValue(0)
    wheel(edit_combo.lineEdit())
    assert bar.value() > 0, "滚轮落在下拉框内部的输入框上也要能滚页面"
    assert edit_combo.currentIndex() == 0, "滚轮不许改可编辑下拉框的值"

    bar.setValue(0)
    short_combo.showPopup()
    app.processEvents()
    view = short_combo.view()
    assert view.window().windowFlags() & Qt.WindowType.Popup, "下拉浮层应当已展开"
    wheel(view.viewport())
    app.processEvents()
    assert not view.window().isVisible(), "短列表滚一下就收起，别把页面卡住"
    assert bar.value() > 0, "收起浮层后这一下滚轮要落到页面上"
    assert short_combo.currentIndex() == 0

    bar.setValue(0)
    long_combo.showPopup()
    app.processEvents()
    long_view = long_combo.view()
    long_view.verticalScrollBar().setValue(0)
    wheel(long_view.viewport())
    app.processEvents()
    assert long_view.verticalScrollBar().value() > 0, "长列表自己得能滚"
    assert bar.value() == 0, "列表自己滚得动时不该动页面"

    app.removeEventFilter(guard)
    page.close()
    print("OK ui wheel guard")


def _wait_until(app, predicate, timeout_s=3.0):
    """等动画真的跑完再断言：整套冒烟一起跑时机器更忙，固定 sleep 会飘。"""
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_motion_switch_controls_everything():
    """设置里「减少界面动效」= 全软件总闸：勾上后折叠区/进度条都直接落位。"""
    import time

    from PySide6.QtWidgets import (
        QApplication, QLabel, QVBoxLayout, QWidget,
    )

    from qbotmanager.ui import motion
    from qbotmanager.ui.widgets import FoldSection, TaskProgressBar

    app = QApplication.instance() or QApplication([])

    body = QWidget()
    lay = QVBoxLayout(body)
    content = QLabel("内容" * 40)
    content.setWordWrap(True)
    sec = FoldSection("展开 ▾", "收起 ▴", content)
    lay.addWidget(sec)
    bar = TaskProgressBar()
    lay.addWidget(bar)
    body.resize(360, 240)
    body.show()
    app.processEvents()

    try:
        # 关掉动效：立刻落位，不留过渡
        motion.set_enabled(False)
        sec.set_expanded(True)
        assert sec._anim is None, "关掉动效后不该还有动画在跑"
        assert content.isVisible() and content.maximumHeight() > 1000
        sec.set_expanded(False)
        assert not content.isVisible() and content.maximumHeight() == 0
        bar.set_smooth(-1)
        assert bar.value() == 30, "忙碌态在关闭动效时给一个静止的三成高"
        bar.set_smooth(66)
        assert bar.value() == 66

        # 打开动效：折叠区走 170ms 过场，结束后放开高度上限
        motion.set_enabled(True)
        sec.set_expanded(True)
        assert sec._anim is not None, "开着动效时应当有过渡"
        assert _wait_until(app, lambda: sec._anim is None), "展开过场应当自己跑完"
        assert content.isVisible() and content.maximumHeight() > 1000
        # 中途反向点一次，最终状态跟最后一次请求一致（不许卡在半路）
        sec.set_expanded(False)
        app.processEvents()
        sec.set_expanded(True)
        assert _wait_until(app, lambda: sec._anim is None)
        assert sec._expanded is True and content.isVisible()
    finally:
        motion.set_enabled(True)
        body.close()
    print("OK motion switch")


def test_toast_motion_and_gate():
    """提示条进出场：动效开时滑上来、退出再滑下去；关掉直接落位，两种情况都不卡半路。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui import motion
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="qbm_toast_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    win = MainWindow(s, TaskManager())
    win.show()
    app.processEvents()

    try:
        motion.set_enabled(False)
        win.show_toast("关掉动效的提示", 3000)
        assert win._toast.isVisible() and win._toast_shift == 0
        win._hide_toast()
        assert not win._toast.isVisible()

        motion.set_enabled(True)
        win.show_toast("开着动效的提示", 3000)
        assert win._toast.isVisible()
        assert win._toast_anim is not None, "开着动效时提示条应该有进场过程"
        assert win._toast_shift > 0, "进场时应当还在往上滑"
        assert _wait_until(app, lambda: win._toast_anim is None), "进场应当自己跑完"
        assert win._toast.isVisible() and win._toast_shift == 0
        win._hide_toast()
        assert win._toast_anim is not None, "退出也应当有过程"
        assert _wait_until(app, lambda: win._toast_anim is None), "退出应当自己跑完"
        assert not win._toast.isVisible() and win._toast_shift == 0
    finally:
        motion.set_enabled(True)
        win.deleteLater()
    print("OK toast motion")


def test_busy_dots_follow_long_tasks():
    """长任务呼吸点：开始转、结束停；关掉动效仍然显示（状态信息不丢）。"""
    from PySide6.QtWidgets import QApplication, QPushButton

    from qbotmanager.ui import motion
    from qbotmanager.ui.widgets import BusyDots, set_busy

    app = QApplication.instance() or QApplication([])
    btn = QPushButton("安装")
    btn.resize(120, 36)
    btn.show()
    app.processEvents()

    motion.set_enabled(True)
    set_busy(btn, True)
    dots = getattr(btn, "_busy_dots", None)
    assert isinstance(dots, BusyDots) and dots.isVisible()
    assert dots._timer.isActive(), "动效开着时应当在呼吸"

    set_busy(btn, False)
    assert not dots.isVisible() and not dots._timer.isActive()

    # 关掉动效：点还在（这是状态信息，不是装饰），只是不再动
    motion.set_enabled(False)
    set_busy(btn, True)
    assert dots.isVisible() and not dots._timer.isActive()
    set_busy(btn, False)

    # 长标题 / 窄按钮都不许把点顶出控件外
    motion.set_enabled(True)
    btn.setText("这是一个很长很长的按钮标题")
    set_busy(btn, True)
    app.processEvents()
    placed = btn._busy_dots
    assert 0 <= placed.x() <= max(0, btn.width() - placed.width())
    set_busy(btn, False)
    motion.set_enabled(True)
    btn.close()
    print("OK busy dots")


def test_qq_card_follows_unsaved_channel_switch():
    """切了「接入方式」还没保存时：卡片和小字也得跟着走，并明确标出「未保存」。

    用户报的坑：切到官方后，状态卡还写着「QQ OneBot · 已停止」和协议端那段小字，
    因为卡片原来只认已保存的配置，而周期刷新又把小字顶回旧文案。
    """
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_qq_unsaved_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "onebot"
    s.qq_onebot_mode = "reverse"
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()
    rp = win.pages[win._page_index["QQ"]]
    rp.refresh_status()
    app.processEvents()
    assert "协议端" in rp.qq_tip.text()

    # 切到官方、故意不点保存，再走一次周期刷新
    rp.combo_channel.setCurrentIndex(rp.combo_channel.findData("official"))
    app.processEvents()
    rp.refresh_status()
    app.processEvents()
    assert s.qq_channel == "onebot", "没点保存就不该动配置"
    assert "官方通道不用扫码" in rp.qq_tip.text(), rp.qq_tip.text()
    assert rp.qq_mode.text() == "QQ 官方机器人", rp.qq_mode.text()
    assert rp.qq_appid_label.text() == "AppID", rp.qq_appid_label.text()
    assert "未保存" in rp.qq_badge.label.text(), rp.qq_badge.label.text()
    assert "还没保存" in rp.qq_state.text(), rp.qq_state.text()
    assert "第三方 OneBot" in rp.qq_state.text(), "要说清现在实际跑的是哪一个"

    # 再切回第三方（同样不保存）：文案跟着回来，未保存标记消失
    rp.combo_channel.setCurrentIndex(rp.combo_channel.findData("onebot"))
    app.processEvents()
    rp.refresh_status()
    app.processEvents()
    assert "协议端" in rp.qq_tip.text(), rp.qq_tip.text()
    assert rp.qq_mode.text().startswith("第三方 OneBot"), rp.qq_mode.text()
    assert "OneBot" in rp.qq_badge.label.text(), rp.qq_badge.label.text()
    assert "还没保存" not in rp.qq_state.text(), rp.qq_state.text()

    win.deleteLater()
    print("OK ui qq_card_follows_unsaved_switch")


def test_qq_card_shows_group_message_mode():
    """QQ 状态卡要显示"收消息模式"：全量 / 仅 @ / 还没收到过。

    这三种在界面上原来完全看不出来，"群里 @ 没反应"就没法解释。
    """
    import json
    import time

    from PySide6.QtWidgets import QApplication

    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_qqmode_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "official"      # 全量/仅@ 是官方通道才有的区别
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()
    rp = win.pages[win._page_index["QQ"]]
    state_file = ai_config.qq_group_state_file(s)
    state_file.parent.mkdir(parents=True, exist_ok=True)

    rp.refresh_status()
    app.processEvents()
    assert "还没收到过" in rp.qq_group_mode.text(), rp.qq_group_mode.text()

    state_file.write_text(json.dumps({"mode": "at_only", "last_ts": time.time()}),
                          encoding="utf-8")
    rp.refresh_status()
    app.processEvents()
    assert "仅 @ 消息" in rp.qq_group_mode.text(), rp.qq_group_mode.text()
    assert "接收所有消息" in rp.qq_tip.text(), "仅@模式要顺手说清怎么开全量"

    state_file.write_text(json.dumps({"mode": "full", "last_ts": time.time()}),
                          encoding="utf-8")
    rp.refresh_status()
    app.processEvents()
    assert rp.qq_group_mode.text().startswith("全量模式"), rp.qq_group_mode.text()
    assert "接收所有消息" not in rp.qq_tip.text(), "已经全量了就别再提示去开"

    # 第三方协议端没有"全量/仅@"之分：这一行要说的是"群里每条都能收到"
    s.qq_channel = "onebot"
    rp.combo_channel.setCurrentIndex(rp.combo_channel.findData("onebot"))
    rp.refresh_status()
    app.processEvents()
    assert "第三方协议端" in rp.qq_group_mode.text(), rp.qq_group_mode.text()

    win.deleteLater()
    print("OK ui qq_group_message_mode")


def test_qq_page_has_diagnose_button():
    """QQ 页要有「官方通道体检」按钮，点开是逐条结论（✗ 需要处理）。"""
    from PySide6.QtWidgets import QApplication, QDialog, QLabel

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_qqdiag_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.qq_channel = "official"
    s.save()
    win = MainWindow(s, TaskManager())
    app.processEvents()
    rp = win.pages[win._page_index["QQ"]]
    assert hasattr(rp, "btn_qq_diag"), "QQ 页应当有体检按钮"
    assert "体检" in rp.btn_qq_diag.text()

    captured = []
    original_exec = QDialog.exec

    def fake_exec(self):
        captured.append([w.text() for w in self.findChildren(QLabel)])
        return 0

    QDialog.exec = fake_exec
    try:
        rp.btn_qq_diag.click()
    finally:
        QDialog.exec = original_exec
    assert captured, "点体检应当弹出窗口"
    lines = "\n".join(captured[0])
    assert "凭" in lines or "凭证" in lines, lines[:200]
    assert any(m in lines for m in ("✓", "✗", "•")), lines[:200]

    win.deleteLater()
    print("OK ui qq_diagnose_button")


def test_brain_group_list_checkboxes():
    """AI 大脑页：把"收到过消息的群"列出来，取消勾选就只允许剩下的群回话。"""
    import json
    import time

    from PySide6.QtWidgets import QApplication

    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_grouplist_ui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.save()
    state_file = ai_config.qq_group_state_file(s)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    state_file.write_text(json.dumps({
        "mode": "full", "last_ts": now,
        "groups": {"群A": {"last_ts": now, "count": 2},
                   "群B": {"last_ts": now - 600, "count": 1}},
    }, ensure_ascii=False), encoding="utf-8")

    win = MainWindow(s, TaskManager())
    win.switch_page("AI 大脑")
    app.processEvents()
    brain = win.pages[win._page_index["AI 大脑"]]

    assert "不限群" in brain.group_list_lbl.text(), brain.group_list_lbl.text()
    assert set(brain._group_checkboxes) == {"群A", "群B"}, brain._group_checkboxes
    assert all(box.isChecked() for box in brain._group_checkboxes.values()), "默认全允许"

    # 取消勾选"群B" → 只允许"群A"（写进机器人侧白名单）
    brain._group_checkboxes["群B"].setChecked(False)
    app.processEvents()
    assert ai_config.allowed_groups(s) == ["群A"], ai_config.allowed_groups(s)
    assert "只允许勾选的 1 个" in brain.group_list_lbl.text(), brain.group_list_lbl.text()

    # 再勾回来 → 回到"不限群"
    brain._group_checkboxes["群B"].setChecked(True)
    app.processEvents()
    assert ai_config.allowed_groups(s) == [], "全勾上就是不限群"

    win.deleteLater()
    print("OK ui brain_group_list")


def test_brain_interject_controls_save_and_reload():
    """AI 大脑页：群聊搭话开关 + 概率/间隔/条数要能存下来并读回来。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_interject_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.save()
    ai_config.save_config(s, api_key="sk-test-123", api_url="https://api.deepseek.com",
                          model="deepseek-chat")

    win = MainWindow(s, TaskManager())
    win.switch_page("AI 大脑")
    app.processEvents()
    brain = win.pages[win._page_index["AI 大脑"]]

    # 默认关（插件侧默认也是 False，别一保存就把它打开）
    assert brain.chk_interject.isChecked() is False
    assert brain.ai_interject_probability.value() == 30
    assert brain.ai_interject_history.value() == 10

    brain.chk_interject.setChecked(True)
    brain.ai_interject_probability.setValue(45)
    brain.ai_interject_min.setValue(15)
    brain.ai_interject_max.setValue(60)
    brain.ai_interject_history.setValue(20)
    brain._save_config()
    app.processEvents()

    saved = ai_config.read_interject(s)
    assert saved["enabled"] is True
    assert abs(saved["probability"] - 0.45) < 1e-6, saved
    assert (saved["interval_min"], saved["interval_max"]) == (15, 60), saved
    assert saved["history_count"] == 20

    # 重开一次页面：控件要回填成已保存的值
    brain.load_config()
    app.processEvents()
    assert brain.chk_interject.isChecked() is True
    assert brain.ai_interject_probability.value() == 45
    assert brain.ai_interject_min.value() == 15

    win.deleteLater()
    print("OK ui brain_interject")


def _market_page(tmp_prefix="qbm_mktcard_"):
    """建一个只装了插件页的测试环境（市场条目由用例自己塞）。"""
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.plugins_page import PluginsPage

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix=tmp_prefix))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
    return app, s, PluginsPage(ctx)


def _market_entries_sample():
    return {
        "eva": {"id": "eva", "name": "EVA · 智能体包", "version": "1.4.2",
                "category": "智能体", "description": "陪聊、扮演角色、按人设回话",
                "url": "https://astroswarm.cn/plugins/eva.zip", "sha256": "a"},
        "reply-rhythm": {"id": "reply-rhythm", "name": "回复节奏能力包", "version": "1.0.0",
                         "category": "官方能力包", "description": "忙的时候晚点回、闲的时候秒回",
                         "url": "https://astroswarm.cn/plugins/rr.zip", "sha256": "b"},
    }


def test_plugins_market_cards_show_description_and_installed_state():
    """卡片要直接把说明写出来（不能只藏在悬停里），已装的显示「✓已装」+「配置」。"""
    import json

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QPushButton

    app, s, pp = _market_page()
    # 造一个"已装"的能力包：tool_packs/<id>/manifest.json
    pack_dir = Path(s.root) / "tool_packs" / "eva"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps({"id": "eva", "name": "EVA · 智能体包", "version": "1.4.2",
                    "kind": "persona-pack"}, ensure_ascii=False), encoding="utf-8")

    pp._market_entries = _market_entries_sample()
    pp._refresh_market_tags()
    pp._refresh_market_list()
    app.processEvents()

    def card_of(entry_id):
        for i in range(pp.market_list.count()):
            item = pp.market_list.item(i)
            if item.data(Qt.UserRole) == entry_id:
                return pp.market_list.itemWidget(item)
        raise AssertionError(f"没找到 {entry_id} 的卡片")

    eva = card_of("eva")
    texts = [w.text() for w in eva.findChildren(QLabel)]
    assert any("陪聊" in t for t in texts), f"说明必须直接显示：{texts}"
    assert "✓ 已装" in texts, texts
    buttons = [b.text() for b in eva.findChildren(QPushButton)]
    assert "配置" in buttons, buttons
    assert "安装" not in buttons, "已装的卡片不该还给「安装」按钮"

    rr = card_of("reply-rhythm")
    rr_buttons = [b.text() for b in rr.findChildren(QPushButton)]
    rr_texts = [w.text() for w in rr.findChildren(QLabel)]
    assert "安装" in rr_buttons and "✓ 已装" not in rr_texts, (rr_buttons, rr_texts)
    assert any("秒回" in t for t in rr_texts), rr_texts

    pp.deleteLater()
    print("OK ui plugins_market_cards")


def test_plugins_market_search_and_category_filter():
    """搜索按名字/说明过滤；分类标签按类别过滤；没有结果时列表清空。"""
    from PySide6.QtCore import Qt

    app, s, pp = _market_page("qbm_mktfilter_")
    pp._market_entries = _market_entries_sample()
    # 页面构造时会异步拉真实市场；结果回来会把样例覆盖掉（这条曾偶发失败）。
    # 这里把回调掐掉，测试只针对"搜索/分类过滤"这两段逻辑。
    pp._on_market_loaded = lambda *_a, **_k: None
    pp._refresh_market_tags()
    pp._refresh_market_list()
    app.processEvents()

    def visible_ids():
        return [pp.market_list.item(i).data(Qt.UserRole)
                for i in range(pp.market_list.count())
                if pp.market_list.item(i).data(Qt.UserRole)]

    assert set(visible_ids()) == {"eva", "reply-rhythm"}, visible_ids()

    # 搜索：名字里的字
    pp.market_search.setText("EVA")
    app.processEvents()
    assert visible_ids() == ["eva"], visible_ids()
    # 搜索：只出现在说明里的字
    pp.market_search.setText("秒回")
    app.processEvents()
    assert visible_ids() == ["reply-rhythm"], visible_ids()
    # 搜不到：列表为空（不再显示一堆无关插件）
    pp.market_search.setText("根本不存在的插件")
    app.processEvents()
    assert visible_ids() == [], visible_ids()
    pp.market_search.setText("")
    app.processEvents()

    # 分类标签过滤
    assert "官方能力包" in pp._market_tags and "智能体" in pp._market_tags
    pp._market_tags["官方能力包"].click()
    app.processEvents()
    assert visible_ids() == ["reply-rhythm"], visible_ids()
    pp._market_tags[""].click()          # 回到「全部」
    app.processEvents()
    assert set(visible_ids()) == {"eva", "reply-rhythm"}, visible_ids()

    pp.deleteLater()
    print("OK ui plugins_market_search_filter")


def test_brain_points_to_session_pack():
    """免@连续对话的参数归「连续对话能力包」：AI 大脑页不再重复放控件，只指路。"""
    from PySide6.QtWidgets import QApplication, QLabel

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_sessionhint_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.beginner_mode = False
    s.save()
    win = MainWindow(s, TaskManager())
    win.switch_page("AI 大脑")
    app.processEvents()
    brain = win.pages[win._page_index["AI 大脑"]]

    assert not hasattr(brain, "chk_session"), "参数已移到能力包，这里不该再有开关"
    texts = [w.text() for w in brain.findChildren(QLabel)]
    assert any("连续对话能力包" in t for t in texts), \
        [t for t in texts if "连续" in t][:3]

    win.deleteLater()
    print("OK ui brain_session_pack_hint")


def test_brain_persona_card_merged_with_segment():
    """人设整合（2026-10-07 老板）：三块并成一张卡 + 胶囊切「预设 / 自定义」。

    老板原话：智能体档案 / 人设工坊（本地）/ 人设与主动聊天 功能重合、设置框离得远，
    要「整合到一个框里」+「胶囊按钮切换形态（胶囊里有圆形图标可以切换）」。

    这条用例钉死四件事：
      1) 只剩一张「人设与主动聊天」卡（旧的 _agent_panel / _workshop_panel 不再是独立卡片，
         而是这张卡里的两段；文档里不许再出现两个旧标题）；
      2) 胶囊正好两段，圆形滑块位置跟着选中的那段走（data 用 offset 表示）；
      3) 切换即互斥：切「预设」停用本地人设 + 打开档案；切「自定义」关掉档案；
      4) 状态行三种口径都对（本地人设《…》/ 预设档案 …/ 手写人设），且切换后有话说明。
    """
    from PySide6.QtWidgets import QApplication, QLabel

    from qbotmanager.core import persona_workshop
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.brain_page import BrainPage, PersonaSegment
    from qbotmanager.ui.pages.common import PageContext

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="qbm_persona_card_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.agent_profile_enabled = False
    s.save()
    toasts = []
    ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: toasts.append(a)})
    bp = BrainPage(ctx)
    app.processEvents()

    # 1) 一张卡：胶囊、形态容器、状态行都在同一张卡里
    seg = bp.persona_seg
    assert isinstance(seg, PersonaSegment), "人设卡片顶部要有胶囊分段控件"
    assert hasattr(bp, "persona_mode_lbl") and hasattr(bp, "persona_switch_hint")
    assert bp._persona_panel is not None
    assert bp._agent_panel is not bp._persona_panel
    assert bp._persona_panel.isAncestorOf(bp._agent_panel), "档案那一块要在人设卡片里"
    assert bp._persona_panel.isAncestorOf(bp._workshop_panel), "本地人设那一块也要在人设卡片里"
    titles = [w.text() for w in bp.findChildren(QLabel) if w.objectName() == "sectionTitle"]
    assert titles.count("人设与主动聊天") == 1, titles
    assert "智能体档案" not in titles and "人设工坊（本地）" not in titles, titles

    # 2) 两段 + 圆形滑块跟着走
    assert seg._labels == ("预设", "自定义"), seg._labels
    bp.set_persona_mode("preset", animate=False)
    assert seg.current() == "preset" and abs(seg.offset - 0.0) < 0.001, seg.offset
    bp.set_persona_mode("custom", animate=False)
    assert seg.current() == "custom" and abs(seg.offset - 1.0) < 0.001, seg.offset
    # 键盘可达：左右方向键能换段（原生分段控件的操作习惯）
    seg.set_current("preset", animate=False)
    seg.setFocus()
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QKeyEvent
    seg.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, _Qt.Key_Right, _Qt.NoModifier))
    assert seg.current() == "custom", "右方向键应切到下一段"

    # 3) 切换即互斥：先在自定义态放一个本地人设并启用
    card = {
        "format": "astroswarm-persona", "version": "1.0", "id": "com.demo.rose",
        "name": "玫瑰", "summary": "测试人设", "kind": "persona-pack", "description": "测试",
        "adapters": ["qq_official"], "min_version": "0.4.0", "permissions": [], "tools": [],
        "identity": {"name": "玫瑰", "worldview": "测试世界"},
        "personality": {"traits": ["冷静"], "tone": "简洁"},
        "speech": {"self_ref": "我"}, "directives": ["保护用户"],
        "boundaries": ["不聊违法内容"], "system_prompt": "",
    }
    import json as _json
    src = tmp / "rose.json"
    src.write_text(_json.dumps(card, ensure_ascii=False), encoding="utf-8")
    persona_workshop.install_persona(s, src)
    bp._refresh_persona_list()
    bp.persona_list.setCurrentRow(0)
    bp._activate_persona()
    app.processEvents()
    assert s.local_persona_id == "com.demo.rose"
    assert seg.current() == "custom"
    assert bp.persona_mode_lbl.text() == "当前生效：本地人设《玫瑰》", bp.persona_mode_lbl.text()

    # 点胶囊「预设」：停用本地人设 + 打开预设档案
    bp.persona_seg._pick(0)          # 相当于用户点了左半段
    app.processEvents()
    assert s.agent_profile_enabled is True, "切到预设应打开智能体档案"
    assert not s.local_persona_id, "切到预设应停用本地人设（互斥）"
    assert bp.persona_mode_lbl.text().startswith("当前生效：预设档案 "), bp.persona_mode_lbl.text()
    assert "已切到预设" in bp.persona_switch_hint.text(), bp.persona_switch_hint.text()
    assert any("预设" in str(t) for t in toasts), toasts

    # 点胶囊「自定义」：关掉预设档案
    bp.persona_seg._pick(1)
    app.processEvents()
    assert s.agent_profile_enabled is False, "切到自定义应关掉预设档案（互斥）"
    assert bp.persona_mode_lbl.text() == "当前生效：手写人设", bp.persona_mode_lbl.text()
    assert "已切到自定义" in bp.persona_switch_hint.text(), bp.persona_switch_hint.text()

    # 4) 老按钮一个都不能少（三块的功能都还在）
    for attr in ("btn_persona_template", "btn_persona_guide", "btn_persona_import",
                 "btn_persona_refresh", "btn_persona_activate", "btn_persona_deactivate",
                 "btn_persona_uninstall", "chk_agent_profile", "agent_profile_combo",
                 "chk_proactive", "ai_proactive_targets", "ai_proactive_groups",
                 "chk_group_chat", "chk_vision", "chk_rich_parse", "chk_self_forge",
                 "chk_cross_platform", "chk_interject"):
        assert hasattr(bp, attr), f"整合后少了控件：{attr}"

    # 5) 自定义态里的「人格设定」输入框 +「保存人设」：与控制台同款，
    #    写的是「设置 → 高级 → 人格设定」**同一个字段**（ai_config.save_personality）
    from qbotmanager.core import ai_config as _ai
    assert hasattr(bp, "personality_edit") and hasattr(bp, "btn_persona_save")
    assert bp._workshop_panel.isAncestorOf(bp.personality_edit), "输入框要在自定义那一段里"
    bp.set_persona_mode("custom", animate=False)
    bp.personality_edit.setPlainText("你是阿星，冷淡又靠谱。")
    bp.btn_persona_save.click()
    app.processEvents()
    assert _ai.read_personality(s) == "你是阿星，冷淡又靠谱。", _ai.read_personality(s)
    assert "人设已保存" in bp.persona_save_hint.text(), bp.persona_save_hint.text()
    # 同一个字段：设置页那个框读出来也是这段（不是第二份存储）
    assert bp._loaded_persona == "你是阿星，冷淡又靠谱。"
    bp.load_config()
    assert bp.personality_edit.toPlainText() == "你是阿星，冷淡又靠谱。", "重新载入应回填同一份"

    bp.deleteLater()
    print("OK ui brain_persona_card_merged_with_segment")


if __name__ == "__main__":
    test_qss()
    test_glass_panel_has_no_screenshot_backdrop()
    test_theme_switch_keeps_page()
    test_brain_page_agent_profile_controls()
    test_brain_page_memory_and_kb_controls()
    test_brain_page_auto_restart_on_profile_change()
    test_plugins_page_restricts_third_party_install()
    test_plugins_page_market_panel_exists()
    test_plugins_market_cards_show_description_and_installed_state()
    test_plugins_market_search_and_category_filter()
    test_plugins_page_developer_mode_enables_zip_install()
    test_plugins_page_all_plugins_free()
    test_account_window_top_left()
    test_main_window_account_corner_entry()
    test_llbot_tutorial_bundled()
    test_settings_page_developer_mode_requires_confirm()
    test_primary_disabled_uses_current_accent()
    test_theme_tokens_contrast_and_sidebar()
    test_beginner_mode_nav()
    test_beginner_toggle_survives_appearance_error()
    test_toggle_switch_click_area()
    test_run_page_reverse_ws_panel()
    test_wechat_channel_is_free_for_everyone()
    test_access_page_lists_all_channels()
    test_qq_official_channel_restored()
    test_access_page_channel_switch_survives_refresh()
    test_run_card_follows_qq_channel_switch()
    test_channel_switch_turns_dsh_off()
    test_brain_page_ai_memory_block()
    test_brain_persona_card_merged_with_segment()
    test_nav_groups_match_console()
    test_services_page_controls()
    test_logs_export_writes_file()
    test_message_center_shows_scene_and_room()
    test_workshop_page_basics()
    test_update_button_states()
    test_dsh_control_buttons()
    test_brain_setup_gate_and_model_combo()
    test_workshop_setup_gate()
    test_wheel_not_trapped_by_dropdowns()
    test_motion_switch_controls_everything()
    test_toast_motion_and_gate()
    test_busy_dots_follow_long_tasks()
    test_qq_card_follows_unsaved_channel_switch()
    test_qq_card_shows_group_message_mode()
    test_qq_page_has_diagnose_button()
    test_brain_group_list_checkboxes()
    test_brain_interject_controls_save_and_reload()
    test_brain_points_to_session_pack()
