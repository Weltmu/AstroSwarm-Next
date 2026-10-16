"""插件管理：官方插件市场 + 本地插件列表与依赖重装。

默认只允许安装官方市场插件（manifest 校验 + SHA256）；第三方 PyPI / zip
安装仅在「设置 → 开发者模式」开启后恢复，并由用户自行承担兼容与合规责任。
"""
import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from ...core import account
from ...core import license as lic_mod
from ...core import nonebot_registry
from ...core import plugin_market
from ...core import tool_packs
from ...core.plugins import (
    detect_dependencies_from_zip,
    list_local_plugins,
    list_store_plugins,
)
from ...tasks.workers import (
    CheckDepsTask, InstallMarketPackTask, InstallMarketPluginTask,
    InstallPluginTask, RestartBotTask,
)
from ..theme import TEXT_3
from ..widgets import GlassPanel, set_busy
from ..widgets_switch import iOSSwitchRow
from ...core import ai_config
from .common import DepsConfirmDialog, PageContext, TaskPanel, Worker

INSTALL_TASK_NAMES = ("安装插件", "安装市场插件", "安装依赖", "检查插件依赖")

# 「高级（开发者）」折叠区标题（收起 / 展开两态）
_ADVANCED_TITLE = "高级（开发者）　▾"


class _NoTextRowDelegate(QStyledItemDelegate):
    """只画背景 / 选中态，不画文字。

    市场列表每行都用 setItemWidget 塞了整行控件（文字 + 安装按钮），
    列表项自己再画一遍文字就会两层错位叠在一起（糊字）。文字统一交给行内 QLabel。
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""


_ADVANCED_OPEN = "收起高级（开发者）　▴"

_PERM_LABELS = {
    "send_message": "发送消息",
    "group_admin": "群管理（禁言/撤回/全员禁言）",
    "timer": "定时任务",
    "memory": "记忆读写",
    "network": "网络访问",
    "media": "媒体处理（图片/语音）",
}


def _detail_of(res) -> str:
    """账号服务的报错字段名不统一（detail / reason / error），统一取一句话。"""
    if not isinstance(res, dict):
        return "服务器没有返回内容"
    detail = res.get("detail") or res.get("reason") or res.get("error") or "未知错误"
    if isinstance(detail, list):
        return "格式不正确"
    return str(detail)


class PluginsPage(QWidget):
    def __init__(self, ctx: PageContext):
        super().__init__()
        self.ctx = ctx
        self._task_tid = None
        self._market_entries = {}
        self._nb_entries = []
        self._nb_loaded = False
        self._entitlements = lic_mod.entitlements()
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect_tasks()
        self.refresh_lists()
        self._load_market()

    # ------------------------------------------------------------ UI
    @staticmethod
    def _note(text: str) -> QLabel:
        """说明文字：统一 12px 灰色（TEXT_3），不要用正文样式抢注意力。"""
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        return lbl

    # ------------------------------------------------------------ 本机能力清单
    def _build_capability_panel(self):
        """本机能调用的能力：可搜索 + 每个一个开关（关掉 = 模型看不见它）。

        2026-10-12 老板：「插件页面看不见好友申请那个功能」「加一个能搜索还带开关的」。
        —— 插件页只管可安装的插件/能力包，而"加好友"这种是**内置插件里的工具**，
        所以另开这一节，把"本机能调什么"摊开给他看。
        """
        panel = GlassPanel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        title = QLabel("本机能力（可搜索 · 可开关）")
        title.setObjectName("sectionTitle")
        v.addWidget(title)
        v.addWidget(self._note(
            "这是机器**现在真的能调**的能力（内置工具 + 内置插件里的动作 + 已装能力包）。"
            "关掉某个开关，机器人就不再用它——模型那边直接看不见。"))

        self.cap_search = QLineEdit()
        self.cap_search.setPlaceholderText("搜索能力：输名字、说明或来源里的字都行")
        self.cap_search.setClearButtonEnabled(True)
        self.cap_search.textChanged.connect(lambda _t: self._refresh_capability_list())
        v.addWidget(self.cap_search)

        self.cap_count = QLabel("")
        self.cap_count.setObjectName("pageSub")
        v.addWidget(self.cap_count)

        self.cap_box = QWidget()
        self.cap_rows = QVBoxLayout(self.cap_box)
        self.cap_rows.setContentsMargins(0, 0, 0, 0)
        self.cap_rows.setSpacing(2)
        v.addWidget(self.cap_box)

        self._refresh_capability_list()
        return panel

    def _refresh_capability_list(self):
        """按搜索词重画能力行（能力不多，直接重建最省事也最不容易出状态错）。"""
        if not hasattr(self, "cap_rows"):
            return
        while self.cap_rows.count():
            item = self.cap_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        keyword = (self.cap_search.text() if hasattr(self, "cap_search") else "").strip().lower()
        try:
            items = ai_config.list_capabilities(self.ctx.settings)
        except Exception as e:  # noqa: BLE001 —— 清单取不到不能让这一页崩
            items = []
            self.cap_count.setText(f"能力清单读取失败：{e}")
        if keyword:
            items = [it for it in items
                     if keyword in it["name"].lower()
                     or keyword in str(it.get("desc") or "").lower()
                     or keyword in str(it.get("source") or "").lower()]
        on = sum(1 for it in items if it.get("enabled"))
        self.cap_count.setText(f"共 {len(items)} 项，开启 {on} 项"
                              + ("（已按搜索词过滤）" if keyword else ""))
        for it in items:
            chk = QCheckBox(it["name"])
            chk.setChecked(bool(it.get("enabled")))
            bits = [str(it.get("desc") or "")]
            if it.get("source"):
                bits.append(f"来源：{it['source']}")
            if it.get("permission"):
                bits.append(f"需要权限：{it['permission']}")
            chk.toggled.connect(
                lambda on_, name=it["name"]: self._on_capability_toggled(name, on_))
            self.cap_rows.addWidget(iOSSwitchRow(chk, description=" · ".join(b for b in bits if b)))

    def _on_capability_toggled(self, name: str, enabled: bool):
        try:
            ai_config.set_tool_enabled(self.ctx.settings, name, enabled)
            self.ctx.show_toast(("已开启：" if enabled else "已关闭：") + name)
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast(f"保存失败：{e}")
        self._refresh_capability_list()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        # 间距只用 8/12/16/24：块内 8~16，块之间 24（外围 ≥ 内部 × 1.5）
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(24)

        # ---- 标题 / 副标题 ----
        head = QWidget()
        hv = QVBoxLayout(head)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(8)
        title = QLabel("插件管理")
        title.setObjectName("pageTitle")
        sub = QLabel("装插件、管插件都在这里。")
        sub.setObjectName("pageSub")
        sub.setWordWrap(True)
        hv.addWidget(title)
        hv.addWidget(sub)
        outer.addWidget(head)
        outer.addWidget(self._build_capability_panel())

        # ---- 插件商店：列表 + 一行操作（本页唯一主按钮「安装」）----
        store = GlassPanel()
        sv = QVBoxLayout(store)
        sv.setContentsMargins(16, 16, 16, 16)
        sv.setSpacing(12)
        sl = QLabel("插件商店")
        sl.setObjectName("sectionTitle")
        sv.addWidget(sl)
        sv.addWidget(self._note(
            "插件全部免费、开源（随主仓库），点卡片上的「安装」即可。"
            "只有代部署、后续维护这类服务才收费，不用也不影响。"))
        # 搜索：名字或说明里的字都能搜（插件一多，靠翻列表找太慢）
        self.market_search = QLineEdit()
        self.market_search.setPlaceholderText("搜索插件：输名字或说明里的字都行")
        self.market_search.setClearButtonEnabled(True)
        self.market_search.textChanged.connect(lambda _t: self._refresh_market_list())
        sv.addWidget(self.market_search)
        # 分类用横排小标签（比下拉直观：一共有哪几类一眼看全）
        self.market_tag_box = QWidget()
        self.market_tags_row = QHBoxLayout(self.market_tag_box)
        self.market_tags_row.setContentsMargins(0, 0, 0, 0)
        self.market_tags_row.setSpacing(6)
        self._market_category = ""          # 当前选中的分类（"" = 全部）
        self._market_tags = {}              # 分类名 -> 按钮（测试与回填用）
        sv.addWidget(self.market_tag_box)
        self.market_list = QListWidget()
        self.market_list.setMinimumHeight(260)
        # 行内已有整行控件（卡片），列表项不要再画一遍文字
        self.market_list.setItemDelegate(_NoTextRowDelegate(self.market_list))
        # 主题给列表项留了 padding，行控件会被内缩（行变矮）→ 行内按钮底部被裁。
        # 这一列是自己画行的，所以把上下 padding 归零，左右留 4px 不贴边。
        self.market_list.setStyleSheet(
            "QListWidget::item { padding: 0px; margin: 0px 4px 4px 4px; }")
        sv.addWidget(self.market_list, 1)
        m_row = QHBoxLayout()
        m_row.setSpacing(8)
        m_row.addStretch(1)
        self.btn_market_refresh = QPushButton("刷新")
        self.btn_market_refresh.setObjectName("ghost")
        self.btn_market_refresh.clicked.connect(self._load_market)
        self.btn_market_install = QPushButton("安装")
        self.btn_market_install.setObjectName("primary")
        self.btn_market_install.clicked.connect(lambda: self._install_market_plugin())
        m_row.addWidget(self.btn_market_refresh)
        m_row.addWidget(self.btn_market_install)
        sv.addLayout(m_row)
        self._refresh_market_tags()          # 先摆上「全部」，拉回清单再补上各分类
        self.market_status = self._note("正在加载插件商店...")
        sv.addWidget(self.market_status)

        # ---- 兑换码 / 权益刷新（与无头端控制台「兑换」走同一个账号服务接口）----
        # 兑换码发的是账号档位（时长/永久），插件本身已全部免费，不需要它解锁插件。
        r_row = QHBoxLayout()
        r_row.setSpacing(8)
        self.redeem_edit = QLineEdit()
        self.redeem_edit.setPlaceholderText("兑换码（活动 / 人工发放的码）")
        self.redeem_edit.returnPressed.connect(self._redeem_code)
        self.btn_redeem = QPushButton("兑换")
        self.btn_redeem.setObjectName("ghost")
        self.btn_redeem.clicked.connect(self._redeem_code)
        self.btn_ent_refresh = QPushButton("刷新权益")
        self.btn_ent_refresh.setObjectName("ghost")
        self.btn_ent_refresh.setToolTip("登录后点这里同步账号档位。")
        self.btn_ent_refresh.clicked.connect(self._refresh_entitlement)
        r_row.addWidget(self.redeem_edit, 1)
        r_row.addWidget(self.btn_redeem)
        r_row.addWidget(self.btn_ent_refresh)
        sv.addLayout(r_row)
        self.ent_status = self._note("")
        sv.addWidget(self.ent_status)
        outer.addWidget(store, 3)

        # ---- 已装能力包：列表 + 详情 / 配置 / 卸载（全是次要按钮）----
        pack = GlassPanel()
        pv = QVBoxLayout(pack)
        pv.setContentsMargins(16, 16, 16, 16)
        pv.setSpacing(12)
        pl = QLabel("已装能力包")
        pl.setObjectName("sectionTitle")
        pv.addWidget(pl)
        pv.addWidget(self._note(
            "全部插件免费。选中后可看详情、改参数；"
            "卸载只影响本机，重启机器人后不再加载。"))
        self.pack_list = QListWidget()
        self.pack_list.setMinimumHeight(120)
        self.pack_list.itemDoubleClicked.connect(lambda _i: self._open_pack_detail())
        pv.addWidget(self.pack_list, 1)
        pack_row = QHBoxLayout()
        pack_row.setSpacing(8)
        self.btn_pack_detail = QPushButton("详情")
        self.btn_pack_detail.setObjectName("ghost")
        self.btn_pack_detail.clicked.connect(self._open_pack_detail)
        self.btn_pack_config = QPushButton("配置")
        self.btn_pack_config.setObjectName("ghost")
        self.btn_pack_config.clicked.connect(self._config_pack)
        self.btn_pack_uninstall = QPushButton("卸载")
        self.btn_pack_uninstall.setObjectName("ghost")
        self.btn_pack_uninstall.clicked.connect(self._uninstall_pack)
        pack_row.addWidget(self.btn_pack_detail)
        pack_row.addWidget(self.btn_pack_config)
        pack_row.addWidget(self.btn_pack_uninstall)
        pack_row.addStretch(1)
        pv.addLayout(pack_row)
        self.pack_status = self._note("正在读取已装能力包...")
        pv.addWidget(self.pack_status)
        # 「已装能力包」提到「插件商店」上面：装完一个包最常干的事是看它 / 调参数，
        # 别让人往下滚。插在标题之后（index 1），保留 2 份弹性，别被商店挤扁。
        outer.insertWidget(1, pack, 2)

        # ---- 高级（开发者）：默认收起，点标题才展开 ----
        outer.addWidget(self._build_advanced_section())

        # ---- 本地插件（列表 + 运维按钮）+ 已安装（pyproject 记录）----
        local = GlassPanel(strong=True)
        lv = QVBoxLayout(local)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(12)
        ll = QLabel("本地插件（src/plugins）")
        ll.setObjectName("sectionTitle")
        lv.addWidget(ll)
        lv.addWidget(self._note(
            "这是插件目录里实际存在的内容。「重装缺失依赖」会先扫描所选插件声明的依赖，"
            "发现缺失后再让你确认安装。"))
        self.local_list = QListWidget()
        self.local_list.setMinimumHeight(160)
        lv.addWidget(self.local_list, 1)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btn_reinstall = QPushButton("重装缺失依赖")
        btn_reinstall.setObjectName("ghost")
        btn_reinstall.setToolTip("扫描缺失依赖并安装（清华镜像）。")
        btn_reinstall.clicked.connect(self._reinstall_deps)
        btn_refresh = QPushButton("刷新列表")
        btn_refresh.setObjectName("ghost")
        btn_refresh.clicked.connect(self.refresh_lists)
        btn_open_dir = QPushButton("打开插件目录")
        btn_open_dir.setObjectName("ghost")
        btn_open_dir.clicked.connect(lambda: os.startfile(str(self.ctx.settings.plugins_dir)))
        self.btn_restart = QPushButton("重启机器人生效")
        self.btn_restart.setObjectName("ghost")
        self.btn_restart.clicked.connect(self._restart_bot)
        btns.addWidget(btn_reinstall)
        btns.addWidget(btn_refresh)
        btns.addWidget(btn_open_dir)
        btns.addStretch(1)
        btns.addWidget(self.btn_restart)
        lv.addLayout(btns)
        lv.addWidget(self._note("已安装（pyproject 记录）"))
        self.store_list = QListWidget()
        self.store_list.setMinimumHeight(120)
        lv.addWidget(self.store_list, 1)
        outer.addWidget(local, 2)

        # ---- 任务面板 ----
        task_glass = GlassPanel()
        tv = QVBoxLayout(task_glass)
        tv.setContentsMargins(16, 16, 16, 16)
        tv.setSpacing(12)
        tl = QLabel("安装任务")
        tl.setObjectName("sectionTitle")
        tv.addWidget(tl)
        self.task_panel = TaskPanel(self.ctx.tasks)
        self.task_panel.setVisible(False)
        self.task_panel.action_requested.connect(self._on_task_action)
        tv.addWidget(self.task_panel)
        outer.addWidget(task_glass)

        self.scroll.setWidget(content)
        root.addWidget(self.scroll)
        self._apply_mode()

    def _build_advanced_section(self) -> GlassPanel:
        """高级（开发者）：第三方 zip / PyPI / NoneBot 在线市场，默认收起。"""
        panel = GlassPanel()
        av = QVBoxLayout(panel)
        av.setContentsMargins(16, 16, 16, 16)
        av.setSpacing(12)
        self._advanced_open = False
        self.btn_advanced = QPushButton(_ADVANCED_TITLE)
        self.btn_advanced.setObjectName("ghost")
        self.btn_advanced.setCheckable(True)
        self.btn_advanced.setCursor(Qt.PointingHandCursor)
        self.btn_advanced.toggled.connect(self._toggle_advanced)
        av.addWidget(self.btn_advanced)

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(24)
        bv.addWidget(self._note(
            "下面这些入口要在「设置 → 开发者模式」开启后才可用；"
            "插件兼容性、安全性与平台协议合规由你自行承担。"))

        # 第三方 zip（开发者模式 + 拖拽）
        zip_block = QWidget()
        zb = QVBoxLayout(zip_block)
        zb.setContentsMargins(0, 0, 0, 0)
        zb.setSpacing(8)
        zh = QLabel("第三方 zip 安装")
        zh.setObjectName("sectionTitle")
        zb.addWidget(zh)
        self.zip_edit = QLineEdit()
        self.zip_edit.setPlaceholderText("插件压缩包路径（开发者模式）")
        z_row = QHBoxLayout()
        z_row.setSpacing(8)
        self.btn_pick = QPushButton("选择 zip ...")
        self.btn_pick.setObjectName("ghost")
        self.btn_pick.clicked.connect(self._pick_zip)
        self.btn_zip = QPushButton("安装")
        self.btn_zip.setObjectName("ghost")
        self.btn_zip.clicked.connect(self._install_zip)
        z_row.addWidget(self.zip_edit, 1)
        z_row.addWidget(self.btn_pick)
        z_row.addWidget(self.btn_zip)
        zb.addLayout(z_row)
        drop_hint = QLabel("⇩ 开发者模式下可把 .zip 拖到本页")
        drop_hint.setAlignment(Qt.AlignCenter)
        drop_hint.setStyleSheet(
            f"color: {TEXT_3}; font-size: 12px; border: 1px dashed rgba(255,255,255,0.18);"
            "border-radius: 8px; padding: 12px;")
        zb.addWidget(drop_hint)
        bv.addWidget(zip_block)

        # PyPI（开发者模式）
        pypi_block = QWidget()
        pb = QVBoxLayout(pypi_block)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(8)
        ph = QLabel("从 PyPI 安装")
        ph.setObjectName("sectionTitle")
        pb.addWidget(ph)
        self.store_edit = QLineEdit()
        self.store_edit.setPlaceholderText("PyPI 包名（开发者模式）")
        p_row = QHBoxLayout()
        p_row.setSpacing(8)
        self.btn_store = QPushButton("安装")
        self.btn_store.setObjectName("ghost")
        self.btn_store.clicked.connect(self._install_store)
        p_row.addWidget(self.store_edit, 1)
        p_row.addWidget(self.btn_store)
        pb.addLayout(p_row)
        pb.addWidget(self._note("不推荐：第三方插件兼容与合规自负。"))
        bv.addWidget(pypi_block)

        # NoneBot 官方插件市场（在线搜索，仅开发者模式可见）
        nb = QWidget()
        nv = QVBoxLayout(nb)
        nv.setContentsMargins(0, 0, 0, 0)
        nv.setSpacing(12)
        nl = QLabel("在线插件市场")
        nl.setObjectName("sectionTitle")
        nv.addWidget(nl)
        nv.addWidget(self._note(
            "在线搜索 nonebot 官方社区开源插件（仅开发者模式可用）。"
            "插件未经星群校验，源码与许可由作者负责，安装前请先阅读声明。"))
        nb_row = QHBoxLayout()
        nb_row.setSpacing(8)
        self.nb_search = QLineEdit()
        self.nb_search.setPlaceholderText("搜索插件名 / 包名 / 作者 / 简介 ...")
        self.nb_search.returnPressed.connect(self._request_nonebot_market)
        self.btn_nb_search = QPushButton("搜索")
        self.btn_nb_search.setObjectName("ghost")
        self.btn_nb_search.clicked.connect(self._request_nonebot_market)
        self.btn_nb_refresh = QPushButton("刷新")
        self.btn_nb_refresh.setObjectName("ghost")
        self.btn_nb_refresh.clicked.connect(self._load_nonebot_market)
        nb_row.addWidget(self.nb_search, 1)
        nb_row.addWidget(self.btn_nb_search)
        nb_row.addWidget(self.btn_nb_refresh)
        nv.addLayout(nb_row)
        self.nb_list = QListWidget()
        self.nb_list.setMinimumHeight(160)
        self.nb_list.itemDoubleClicked.connect(
            lambda _i: self._install_nonebot_plugin())
        self.nb_list.currentItemChanged.connect(
            lambda *_: self._update_nonebot_buttons())
        nv.addWidget(self.nb_list)
        self.nb_status = self._note("进入「开发者模式」后可搜索插件")
        nv.addWidget(self.nb_status)
        nb_bottom = QHBoxLayout()
        nb_bottom.setSpacing(8)
        self.btn_nb_install = QPushButton("安装选中")
        self.btn_nb_install.setObjectName("ghost")
        self.btn_nb_install.setEnabled(False)
        self.btn_nb_install.clicked.connect(self._install_nonebot_plugin)
        nb_bottom.addWidget(self.btn_nb_install)
        nb_bottom.addStretch(1)
        nv.addLayout(nb_bottom)
        self.nb_panel = nb
        bv.addWidget(nb)

        body.setVisible(False)
        av.addWidget(body)
        self._advanced_body = body
        return panel

    def _toggle_advanced(self, expanded=None):
        """展开 / 收起高级区（默认收起；点标题切换）。"""
        show = (not self._advanced_open) if expanded is None else bool(expanded)
        self._advanced_open = show
        self._advanced_body.setVisible(show)
        if self.btn_advanced.isChecked() != show:
            self.btn_advanced.blockSignals(True)
            self.btn_advanced.setChecked(show)
            self.btn_advanced.blockSignals(False)
        self.btn_advanced.setText(_ADVANCED_OPEN if show else _ADVANCED_TITLE)

    def _connect_tasks(self):
        t = self.ctx.tasks
        t.started.connect(self._on_task_started)
        t.finished.connect(self._on_task_finished)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_mode()
        # 每次进入页面刷新本地账号档位（登录/同步后可能已变化）
        self._entitlements = lic_mod.entitlements()
        self._refresh_market_list()
        # 开发者模式下首次进入页面，自动拉取 NoneBot 官方商店
        if bool(getattr(self.ctx.settings, "developer_mode", False)) and not self._nb_loaded:
            self._load_nonebot_market()

    # ------------------------------------------------------------ 模式
    def _apply_mode(self):
        """按开发者模式启用/禁用第三方安装入口（PyPI / zip）。"""
        dev = bool(getattr(self.ctx.settings, "developer_mode", False))
        for w in (self.store_edit, self.btn_store, self.zip_edit,
                  self.btn_pick, self.btn_zip):
            w.setEnabled(dev)
        self.nb_panel.setVisible(dev)

    # ------------------------------------------------------------ 任务
    def _on_task_started(self, tid, name):
        if name in INSTALL_TASK_NAMES:
            self._task_tid = tid
            self.task_panel.bind(tid)
            self.task_panel.setVisible(True)
            # 几十秒的安装过程给个"在跑"的信号，别让用户以为点空了
            set_busy(self.btn_market_install, True)
            set_busy(self.market_status, True)

    def _on_task_finished(self, tid, status, result):
        if tid != self._task_tid:
            return
        set_busy(self.btn_market_install, False)
        set_busy(self.market_status, False)
        self.task_panel.bind(tid)
        self.refresh_lists()
        st = self.ctx.tasks.get(tid)
        name = st.task_name if st else ""
        if status == "success" and name == "检查插件依赖":
            self._on_check_done(result)
        elif status == "success" and name in ("安装插件", "安装依赖"):
            self._prompt_restart("安装完成")
        elif status == "success" and name == "安装市场插件":
            self._prompt_restart("插件安装完成")

    def _on_check_done(self, result):
        missing = result.get("missing") or []
        if not missing:
            self.ctx.show_toast("该插件依赖完整，无需安装")
            return
        ret = QMessageBox.question(
            self, "安装缺失依赖",
            f"发现 {len(missing)} 个缺失依赖：\n\n" +
            "\n".join("• " + d for d in missing) +
            "\n\n是否现在安装？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return
        self.ctx.tasks.submit(
            "安装依赖", "install", InstallPluginTask,
            payload={"kind": "deps", "target": list(missing)},
            retryable=True, settings=self.ctx.settings,
        )

    def _on_task_action(self, action, task_id):
        if action == "cancel":
            self.ctx.tasks.cancel(task_id)
        elif action == "retry":
            new_id = self.ctx.tasks.retry(task_id)
            if new_id:
                self.task_panel.bind(new_id)
        elif action == "logs":
            self.ctx.open_logs()

    # ------------------------------------------------------------ 操作
    def _install_store(self):
        name = self.store_edit.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "请输入插件包名")
            return
        if self.ctx.tasks.has_active("install"):
            QMessageBox.information(self, "提示", "已有安装任务正在执行，请稍候")
            return
        self.ctx.tasks.submit(
            "安装插件", "install", InstallPluginTask,
            payload={"kind": "store", "target": name},
            retryable=True, settings=self.ctx.settings,
        )

    def _pick_zip(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择插件 zip", "", "ZIP 文件 (*.zip)")
        if f:
            self.zip_edit.setText(f)

    def _install_zip(self):
        p = self.zip_edit.text().strip()
        if not p:
            QMessageBox.information(self, "提示", "请先选择 zip 文件")
            return
        if self.ctx.tasks.has_active("install"):
            QMessageBox.information(self, "提示", "已有安装任务正在执行，请稍候")
            return
        deps = []
        try:
            deps = detect_dependencies_from_zip(p)
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("读取 zip 依赖信息失败: " + str(e))
        if deps:
            decision, _never = DepsConfirmDialog(deps, self).decision()
            if decision == "cancel":
                return
            if decision == "skip":
                deps = []
        self.ctx.tasks.submit(
            "安装插件", "install", InstallPluginTask,
            payload={"kind": "zip", "target": p, "deps": deps},
            retryable=True, settings=self.ctx.settings,
        )

    # ------------------------------------------------------------ 兑换码 / 权益
    @staticmethod
    def _account_token() -> str:
        acc = account.load_account() or {}
        return str(acc.get("token") or "")

    def _redeem_code(self):
        """兑换码：只有星群账号服务知道码是真是假，本机只负责转发（与无头端同一接口）。

        兑换成功后必须再拉一次 /me：兑换接口不回签名，而「有没有签名」决定账号档位
        能不能生效，只落 plan 不落签名等于白兑。
        """
        code = self.redeem_edit.text().strip()
        if not code:
            self.ctx.show_toast("请先填兑换码")
            return
        token = self._account_token()
        if not token:
            self.ctx.show_toast("本机还没登录星群账号：先去「账号」里登录，再回来兑换")
            return
        self.btn_redeem.setEnabled(False)
        self.ent_status.setText("正在兑换…")
        set_busy(self.btn_redeem, True)
        set_busy(self.ent_status, True)
        worker = Worker(self)
        worker.finished.connect(self._on_redeemed)
        threading.Thread(
            target=lambda: worker.run(lambda: account.redeem(token, code)),
            daemon=True).start()

    def _on_redeemed(self, res):
        self.btn_redeem.setEnabled(True)
        set_busy(self.btn_redeem, False)
        if not isinstance(res, dict) or res.get("ok") is False:
            set_busy(self.ent_status, False)
            self.ent_status.setText("兑换失败：" + _detail_of(res))
            return
        self.redeem_edit.clear()
        self.ent_status.setText("兑换成功，正在刷新权益…")
        self._refresh_entitlement(silent=True)

    def _refresh_entitlement(self, silent=False):
        """去账号服务重新拉权益（刚买完 / 刚兑换完点这个）。"""
        token = self._account_token()
        if not token:
            self.ent_status.setText("本机还没登录星群账号：先去「账号」里登录")
            return
        self.btn_ent_refresh.setEnabled(False)
        set_busy(self.btn_ent_refresh, True)
        if not silent:
            self.ent_status.setText("正在刷新权益…")
        set_busy(self.ent_status, True)
        worker = Worker(self)
        worker.finished.connect(self._on_entitlement_refreshed)
        threading.Thread(target=lambda: worker.run(lambda: account.me(token)), daemon=True).start()

    def _on_entitlement_refreshed(self, res):
        self.btn_ent_refresh.setEnabled(True)
        set_busy(self.btn_ent_refresh, False)
        set_busy(self.ent_status, False)
        if not isinstance(res, dict) or res.get("ok") is False:
            self.ent_status.setText("权益刷新失败：" + _detail_of(res))
            return
        if not lic_mod.save_entitlements_from_account(res):
            # 服务器这次没下发签名：保持本机现有权益（以前会把签名写成空 -> 掉回未开通）
            self.ent_status.setText("权益已同步，本机权益不变。")
            return
        ent = lic_mod.entitlements()
        owned = ent.get("owned_plugins") or []
        self.ent_status.setText(
            "账号档位已刷新：" + ("、".join(owned) if owned else "无额外档位"))
        self._load_market()

    # ------------------------------------------------------------ 官方市场
    def _load_market(self):
        self.market_status.setText("正在加载插件商店...")
        set_busy(self.market_status, True)
        worker = Worker(self)
        worker.finished.connect(self._on_market_loaded)

        def _run():
            entitlements = lic_mod.entitlements()
            try:
                acc = account.load_account()
                if acc and acc.get("token"):
                    me = account.me(acc["token"])
                    if me.get("ok") is not False and me.get("plan"):
                        # 拉最新权益并写本地，商店立即按最新权益显示。
                        # 走带保护的 helper：服务器没下发签名时不覆盖本机已有权益
                        if lic_mod.save_entitlements_from_account(me):
                            entitlements = lic_mod.entitlements()
            except Exception:  # noqa: BLE001
                pass
            return {
                "plugins": plugin_market.fetch_market(plugin_market.MARKET_DEFAULT_URL),
                "entitlements": entitlements,
            }

        threading.Thread(target=lambda: worker.run(_run), daemon=True).start()

    def _on_market_loaded(self, res):
        set_busy(self.market_status, False)
        plugins = res.get("plugins") or [] if isinstance(res, dict) else []
        self._entitlements = (
            res.get("entitlements") if isinstance(res, dict)
            else lic_mod.entitlements()
        ) or lic_mod.entitlements()
        entries = {}
        categories = set()
        skipped = 0
        for e in plugins:
            if plugin_market.validate_market_plugin(e) is not None:
                skipped += 1
                continue
            entries[str(e["id"])] = e
            categories.add(str(e.get("category") or "未分类"))
        self._market_entries = entries
        # 分类可能变了（市场新上架一类），标签行跟着重建；已选分类若不存在就回到「全部」
        if self._market_category and self._market_category not in categories:
            self._market_category = ""
        self._refresh_market_tags()
        self._refresh_market_list()
        if not entries:
            # 把失败原因（验签失败/不在白名单/网络不通）直说，别只显示"暂不可用"
            why = str(getattr(plugin_market, "LAST_ERROR", "") or "").strip()
            self.market_status.setText(
                f"插件商店加载失败：{why}（可点「刷新」重试）" if why
                else "插件商店暂不可用或暂无内容，可稍后点「刷新」重试")
            return
        acc = account.load_account()
        if acc:
            plan_label = "当前账号：" + (
                "已开通" if self._entitlements.get("full") else "仅 QQ 通道")
        else:
            plan_label = "未登录星群账号（插件不受影响）"
        self.ent_status.setText(plan_label)
        self.market_status.setText(
            f"插件商店已加载：{len(entries)} 个（全部免费）· {plan_label}"
            + (f"（{skipped} 个条目校验未通过已跳过）" if skipped else ""))

    def _installed_ids(self) -> set:
        """已装的能力包 / 插件标识（市场条目按 id 或名字匹配，宽松一点）。"""
        out = set()
        try:
            for item in tool_packs.installed(self.ctx.settings):
                for key in (item.get("id"), item.get("name")):
                    if key:
                        out.add(str(key).strip().lower())
        except Exception:  # noqa: BLE001
            pass
        try:
            for name in list_local_plugins(self.ctx.settings):
                out.add(str(name).strip().lower())
        except Exception:  # noqa: BLE001
            pass
        return out

    # 分类配色：同一个分类永远同一个颜色，用户扫一眼就能分堆
    CATEGORY_COLORS = {
        "官方能力包": "#7C9CFF",
        "智能体": "#C084FC",
        "工具包": "#34D399",
        "功能扩展": "#F59E0B",
    }

    def _category_color(self, category: str) -> str:
        key = str(category or "").strip() or "未分类"
        if key in self.CATEGORY_COLORS:
            return self.CATEGORY_COLORS[key]
        palette = ("#7C9CFF", "#C084FC", "#34D399", "#F59E0B", "#F472B6", "#38BDF8")
        return palette[sum(ord(ch) for ch in key) % len(palette)]

    def _badge(self, text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 8px;"
            "padding: 0px 6px; font-size: 11px;")
        return lbl

    def _market_card(self, eid: str, e: dict, installed: bool) -> QWidget:
        """一张插件卡片：分类色块 + 名称/版本/徽章 + 一句人话说明 + 右侧按钮。

        说明直接显示出来（以前藏在悬停提示里，等于没有）。
        """
        entry_id = str(eid)
        category = str(e.get("category") or "未分类")
        color = self._category_color(category)
        card = GlassPanel()
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)

        icon = QLabel(category[:1] or "包")
        icon.setFixedSize(34, 34)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            f"background: {color}22; color: {color}; border-radius: 8px;"
            "font-size: 15px; font-weight: 600;")
        lay.addWidget(icon)

        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(str(e.get("name") or entry_id))
        name.setStyleSheet("font-size: 13.5px; font-weight: 600;")
        top.addWidget(name)
        version = QLabel("v" + str(e.get("version") or ""))
        version.setStyleSheet(f"color: {TEXT_3}; font-size: 11.5px;")
        top.addWidget(version)
        top.addWidget(self._badge(category, color))
        top.addWidget(self._badge("免费", "#34D399"))
        top.addStretch(1)
        col.addLayout(top)

        desc = str(e.get("description") or "").strip() or f"{category}：装进机器人就能用"
        desc_lbl = QLabel(desc)
        # 必须换行：不换行时这句说明会把卡片撑到一千多像素宽，整列出现横向滚动条，
        # 用户得拖底下的条才能看到右边的「安装」按钮（2026-10-06 用户反馈）。
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        col.addWidget(desc_lbl)
        lay.addLayout(col, 1)
        card.setToolTip(str(e.get("description") or ""))

        detail = QPushButton("详情")
        detail.setObjectName("ghost")
        detail.setMinimumHeight(26)
        detail.clicked.connect(lambda _=False, _id=entry_id: self._show_market_detail(_id))
        lay.addWidget(detail)
        if installed:
            mark = QLabel("✓ 已装")
            mark.setStyleSheet("color: #34D399; font-size: 12.5px; font-weight: 600;")
            lay.addWidget(mark)
            cfg = QPushButton("配置")
            cfg.setObjectName("ghost")
            cfg.setMinimumHeight(26)
            cfg.clicked.connect(lambda _=False, _id=entry_id: self._open_pack_config(_id))
            lay.addWidget(cfg)
        else:
            btn = QPushButton("安装")
            btn.setObjectName("primary")
            btn.setMinimumHeight(26)
            btn.clicked.connect(lambda _=False, _id=entry_id: self._install_market_plugin(_id))
            lay.addWidget(btn)
        return card

    def _show_market_detail(self, eid: str):
        """插件详情：说明、版本、分类、适配通道、权限、装完要不要重启。"""
        e = self._market_entries.get(str(eid or "")) or {}
        if not e:
            self.ctx.show_toast("插件信息已失效，请点「刷新」")
            return
        category = str(e.get("category") or "未分类")
        dlg = QDialog(self)
        dlg.setWindowTitle("插件详情 · " + str(e.get("name") or eid))
        dlg.resize(560, 380)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        head = QLabel(str(e.get("name") or eid))
        head.setStyleSheet("font-size: 15px; font-weight: 600;")
        outer.addWidget(head)
        meta = QLabel(f"{category} · v{e.get('version') or ''} · 免费")
        meta.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        outer.addWidget(meta)
        desc = QLabel(str(e.get("description") or "").strip() or "（这个插件没写说明）")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12.5px;")
        outer.addWidget(desc)

        adapters = e.get("adapters") or []
        if adapters:
            label = QLabel("适配通道：" + "、".join(str(a) for a in adapters))
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
            outer.addWidget(label)
        perms = e.get("permissions") or []
        if perms:
            plabel = QLabel("需要权限：\n" + "\n".join(
                "• " + _PERM_LABELS.get(str(p), str(p)) for p in perms if str(p)))
            plabel.setWordWrap(True)
            plabel.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
            outer.addWidget(plabel)
        restart = QLabel("装完要重启一次机器人才生效（装完软件会提示你）。")
        restart.setWordWrap(True)
        restart.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        outer.addWidget(restart)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        if str(eid).strip().lower() in self._installed_ids() or \
                str(e.get("name") or "").strip().lower() in self._installed_ids():
            row.addWidget(QLabel("这个插件已经装好了"))
        else:
            btn_install = QPushButton("安装")
            btn_install.setObjectName("primary")
            btn_install.clicked.connect(
                lambda _=False, _id=str(eid): (dlg.accept(), self._install_market_plugin(_id)))
            row.addWidget(btn_install)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("ghost")
        btn_close.clicked.connect(dlg.accept)
        row.addWidget(btn_close)
        outer.addLayout(row)
        dlg.exec()

    def _refresh_market_list(self):
        """按搜索词 + 分类过滤，渲染成一排卡片（默认按分类分组）。"""
        keyword = str(self.market_search.text() if hasattr(self, "market_search") else "").strip().lower()
        category = str(getattr(self, "_market_category", "") or "")
        entries = []
        for eid, e in self._market_entries.items():
            if category and str(e.get("category") or "") != category:
                continue
            if keyword:
                haystack = f"{e.get('name') or ''} {e.get('description') or ''} {e.get('id') or ''}".lower()
                if keyword not in haystack:
                    continue
            entries.append((str(eid), e))
        entries.sort(key=lambda kv: (str(kv[1].get("category") or "未分类"), str(kv[1].get("name") or "")))

        installed = self._installed_ids()
        self.market_list.clear()
        grouped = not category and not keyword
        last_category = None
        for eid, e in entries:
            cat = str(e.get("category") or "未分类")
            if grouped and cat != last_category:
                last_category = cat
                header = QWidget()
                hl = QHBoxLayout(header)
                hl.setContentsMargins(6, 6, 6, 2)
                lbl = QLabel(f"{cat} · {sum(1 for _, x in entries if str(x.get('category') or '未分类') == cat)} 个")
                lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 11.5px;")
                hl.addWidget(lbl)
                hl.addStretch(1)
                hitem = QListWidgetItem()
                hitem.setFlags(Qt.NoItemFlags)
                hitem.setSizeHint(header.sizeHint())
                self.market_list.addItem(hitem)
                self.market_list.setItemWidget(hitem, header)

            entry_id = str(eid)
            is_installed = (entry_id.lower() in installed
                            or str(e.get("name") or "").strip().lower() in installed)
            item = QListWidgetItem(
                f"[免费] {e.get('name')}  v{e.get('version')}"
                f"  [{e.get('category') or '未分类'}]")
            item.setData(Qt.UserRole, entry_id)
            tip = str(e.get("description") or "")
            tip += "\n\n可安装：" + plugin_market.plugin_access(e, self._entitlements)["reason"]
            item.setToolTip(tip)
            card = self._market_card(entry_id, e, is_installed)
            item.setSizeHint(card.sizeHint())
            self.market_list.addItem(item)
            self.market_list.setItemWidget(item, card)

    def _refresh_market_tags(self):
        """把分类做成横排小标签（全部 + 实际存在的分类）。"""
        while self.market_tags_row.count():
            item = self.market_tags_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._market_tags = {}
        categories = sorted({str(e.get("category") or "未分类")
                             for e in self._market_entries.values()})
        for name in [""] + categories:
            label = "全部" if not name else name
            btn = QPushButton(label)
            btn.setObjectName("primary" if name == getattr(self, "_market_category", "") else "ghost")
            btn.setMinimumHeight(26)
            btn.clicked.connect(lambda _=False, _c=name: self._pick_market_category(_c))
            self.market_tags_row.addWidget(btn)
            self._market_tags[name] = btn
        self.market_tags_row.addStretch(1)

    def _pick_market_category(self, category: str):
        self._market_category = str(category or "")
        self._refresh_market_tags()
        self._refresh_market_list()


    def _install_market_plugin(self, eid=None):
        """eid 为空时取列表当前选中项；行内「安装」按钮直接把它自己那条传进来。"""
        if eid is None:
            item = self.market_list.currentItem()
            if item is None:
                QMessageBox.information(self, "提示", "请先在官方插件市场选择一个插件")
                return
            eid = str(item.data(Qt.UserRole) or "")
        e = self._market_entries.get(str(eid or ""))
        if not e:
            QMessageBox.information(self, "提示", "插件信息已失效，请点「刷新」")
            return
        err = plugin_market.validate_market_plugin(e)
        if err:
            QMessageBox.warning(self, "校验失败", err)
            return
        perms = e.get("permissions") or []
        if perms:
            labels = [
                "• " + _PERM_LABELS.get(str(p), str(p))
                for p in perms if str(p)
            ]
            ret = QMessageBox.question(
                self, "插件权限确认",
                f"「{e.get('name')}」将使用以下权限：\n\n"
                + "\n".join(labels)
                + "\n\n是否安装？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret != QMessageBox.Yes:
                return
        if self.ctx.tasks.has_active("install"):
            QMessageBox.information(self, "提示", "已有安装任务正在执行，请稍候")
            return
        kind = str(e.get("kind") or "")
        if kind in ("tool-pack", "persona-pack", "behavior-pack"):
            self.ctx.tasks.submit(
                "安装市场插件", "install", InstallMarketPackTask,
                payload={"entry": e, "url": e["url"], "sha256": e["sha256"],
                         "name": e["name"], "version": e["version"]},
                retryable=True, settings=self.ctx.settings,
            )
        else:
            self.ctx.tasks.submit(
                "安装市场插件", "install", InstallMarketPluginTask,
                payload={"url": e["url"], "sha256": e["sha256"],
                         "name": e["name"], "version": e["version"]},
                retryable=True, settings=self.ctx.settings,
            )

    def _uninstall_pack(self):
        item = self.pack_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个已装能力包")
            return
        pid = str(item.data(Qt.UserRole) or "")
        ret = QMessageBox.question(
            self, "卸载能力包",
            f"确定卸载「{item.text()}」吗？卸载后重启机器人即不再加载。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            tool_packs.uninstall_pack(self.ctx.settings, pid)
        except OSError as e:
            self.ctx.show_toast("卸载失败: " + str(e))
            return
        self.refresh_lists()
        self.ctx.show_toast("能力包已卸载，重启机器人后生效")

    def _selected_pack_id(self) -> str:
        item = self.pack_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个已装能力包")
            return ""
        return str(item.data(Qt.UserRole) or "")

    def _open_pack_detail(self):
        """能力包详情：介绍 / 功能 / 参数（参数按 manifest 的 schema 渲染）。"""
        pid = self._selected_pack_id()
        if not pid:
            return
        try:
            detail = tool_packs.pack_detail(self.ctx.settings, pid)
        except ValueError as exc:
            QMessageBox.warning(self, "打开详情失败", str(exc))
            return
        dlg = PackDetailDialog(self.ctx.settings, detail, self)
        dlg.exec()
        if dlg.changed:
            self.refresh_lists()

    def _open_pack_config(self, pid: str = ""):
        """插件商店卡片上的「配置」：找到对应的已装包选中，再打开它的配置窗口。"""
        pid = str(pid or "").strip()
        if pid:
            for attempt in range(2):
                for i in range(self.pack_list.count()):
                    item = self.pack_list.item(i)
                    if str(item.data(Qt.UserRole) or "") == pid:
                        self.pack_list.setCurrentRow(i)
                        self._config_pack()
                        return
                if attempt == 0:
                    self.refresh_lists()      # 刚装完列表可能还没刷新，刷一次再找
        self.ctx.show_toast("没找到这个已装插件，点「刷新」看看")

    def _config_pack(self):
        pid = self._selected_pack_id()
        if not pid:
            return
        pack_dir = Path(self.ctx.settings.root) / "tool_packs" / pid
        bundled_qw = (pack_dir / "bundled" / "qweather" / "manifest.json").exists()
        if pid == "qweather" or bundled_qw:
            # 和风天气要填项目ID/凭据ID/私钥路径，走它自己的专用对话框
            dlg = QWeatherConfigDialog(self.ctx.settings, self)
            if dlg.exec() == QDialog.Accepted:
                self.ctx.show_toast("和风天气配置已保存，重启机器人后生效")
            return
        self._open_pack_detail()

    def _reinstall_deps(self):
        item = self.local_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先在列表中选择一个插件")
            return
        if self.ctx.tasks.has_active("install"):
            QMessageBox.information(self, "提示", "已有安装任务正在执行，请稍候")
            return
        plugin_dir = self.ctx.settings.plugins_dir / item.text()
        self.ctx.tasks.submit(
            "检查插件依赖", "install", CheckDepsTask,
            payload={"mode": "dir", "target": str(plugin_dir)},
            retryable=True, settings=self.ctx.settings,
        )

    def _prompt_restart(self, what: str):
        """装完插件只提示一次，绝不自动重启（重启会打断群里正在进行的对话）。"""
        box = QMessageBox(self)
        box.setWindowTitle(what)
        box.setIcon(QMessageBox.Information)
        box.setText(f"{what}。重启机器人后新插件才会加载。")
        btn_yes = box.addButton("立即重启", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        # 非阻塞弹窗：exec() 会卡住事件循环（自动化测试里没人点就永远停在那）
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.finished.connect(
            lambda _r: self._restart_bot() if box.clickedButton() is btn_yes else None)
        box.open()

    def _restart_bot(self):
        if self.ctx.tasks.has_active("service"):
            QMessageBox.information(self, "提示", "已有服务任务正在执行，请稍候")
            return
        self.ctx.tasks.submit(
            "重启 NoneBot", "service", RestartBotTask,
            retryable=True, manager=self.ctx.manager,
        )

    # ------------------------------------------------------------ NoneBot 市场
    def _request_nonebot_market(self):
        """按搜索词过滤已加载列表；若尚未加载则先拉取。"""
        if self._nb_loaded:
            self._refresh_nonebot_list()
            return
        self._load_nonebot_market()

    def _load_nonebot_market(self):
        if not bool(getattr(self.ctx.settings, "developer_mode", False)):
            return
        self.nb_status.setText("正在连接插件商店...")
        self.btn_nb_search.setEnabled(False)
        self.btn_nb_refresh.setEnabled(False)
        worker = Worker(self)
        worker.finished.connect(self._on_nonebot_market_loaded)

        def _run():
            return {"entries": nonebot_registry.fetch_registry()}

        threading.Thread(target=lambda: worker.run(_run), daemon=True).start()

    def _on_nonebot_market_loaded(self, res):
        self.btn_nb_search.setEnabled(True)
        self.btn_nb_refresh.setEnabled(True)
        err = str(res.get("error")) if isinstance(res, dict) else ""
        if isinstance(res, dict) and isinstance(res.get("entries"), list):
            self._nb_entries = res["entries"]
            self._nb_loaded = bool(self._nb_entries)
        else:
            self._nb_entries = []
            self._nb_loaded = True
        if not self._nb_entries:
            self.nb_status.setText(
                ("插件商店拉取失败: " + err) if err
                else "插件商店不可用或暂无内容，可点「刷新」重试")
            self.nb_list.clear()
            self._update_nonebot_buttons()
            return
        self._refresh_nonebot_list()

    def _refresh_nonebot_list(self):
        q = self.nb_search.text().strip()
        entries = nonebot_registry.search_plugins(self._nb_entries, q)
        # 已装标记：pyproject 记录的商店插件 + src/plugins 目录都算
        # （2026-10-12：老板装完发现列表里看不出哪个已经装过）。
        installed = set()
        try:
            for name in list_store_plugins(self.ctx.settings):
                installed.add(str(name).strip().lower())
            for name in list_local_plugins(self.ctx.settings):
                installed.add(str(name).strip().lower())
        except Exception:  # noqa: BLE001
            pass
        self.nb_list.clear()
        for e in entries:
            ok, hint = nonebot_registry.adapter_compat(e)
            badge = "官方" if e.get("is_official") else "社区"
            is_installed = str(e.get("module_name") or "").strip().lower() in installed
            item = QListWidgetItem(
                f"「{e.get('name')}」 v{e.get('version')}  [{badge}] "
                f"{e.get('project_link')}"
                + ("  ✅已安装" if is_installed else ""))
            item.setData(Qt.UserRole, e.get("module_name"))
            tip = str(e.get("desc") or "（无简介）")
            if is_installed:
                tip += "\n状态: 已安装（再点「安装」= 覆盖更新）"
            tip += f"\n\n作者: {e.get('author') or '未知'}"
            tip += f"\n主页: {e.get('homepage') or '未提供'}"
            tip += "\n兼容性: " + hint
            if not ok:
                tip += "\n⚠ 与当前协议不兼容，可能无法正常运行"
            if not e.get("valid"):
                tip += "\n⚠ 该插件未通过官方测试（valid=false），请谨慎安装"
            item.setToolTip(tip)
            if not ok or not e.get("valid"):
                item.setForeground(QColor("#94A3B8"))
            elif e.get("is_official"):
                item.setForeground(QColor("#34D399"))
            else:
                item.setForeground(QColor("#22D3EE"))
            self.nb_list.addItem(item)
        self.nb_status.setText(
            f"共 {len(entries)} 个结果"
            + ("（输入关键词后回车 / 点「搜索」可过滤）" if q else ""))
        self._update_nonebot_buttons()

    def _update_nonebot_buttons(self):
        self.btn_nb_install.setEnabled(self.nb_list.currentItem() is not None)

    def _install_nonebot_plugin(self):
        item = self.nb_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一个插件")
            return
        mod = str(item.data(Qt.UserRole) or "")
        e = next(
            (x for x in self._nb_entries if x.get("module_name") == mod), None)
        if not e:
            QMessageBox.information(self, "提示", "插件信息已失效，请点「刷新」")
            return
        link = str(e.get("project_link") or mod)
        _ok, hint = nonebot_registry.adapter_compat(e)
        lines = [
            f"「{e.get('name')}」 v{e.get('version')}",
            f"PyPI 包名: {link}",
            f"模块名: {mod}",
            "",
            "安装声明：",
            "· 该插件来自第三方开源商店，未经星群校验。",
            "· 源码、可用性与许可协议（如 MIT / AGPL / GPL）由各自作者负责。",
            "· 星群默认不安装 GPL/AGPL 类插件；商业使用前请自行核对许可证。",
            "· 若协议不兼容或插件未通过官方测试，风险由用户自行承担。",
            "",
            "兼容性: " + hint,
            "安装后需重启机器人才生效。是否继续？",
        ]
        ret = QMessageBox.warning(
            self, "安装声明", "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        if self.ctx.tasks.has_active("install"):
            QMessageBox.information(self, "提示", "已有安装任务正在执行，请稍候")
            return
        self.ctx.tasks.submit(
            "安装插件", "install", InstallPluginTask,
            payload={"kind": "store", "target": link, "module_name": mod},
            retryable=True, settings=self.ctx.settings,
        )

    # ------------------------------------------------------------ 拖拽
    def dragEnterEvent(self, event):
        if not bool(getattr(self.ctx.settings, "developer_mode", False)):
            event.ignore()
            return
        urls = event.mimeData().urls()
        if any(u.isLocalFile() and u.toLocalFile().lower().endswith(".zip") for u in urls):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not bool(getattr(self.ctx.settings, "developer_mode", False)):
            event.ignore()
            return
        for u in event.mimeData().urls():
            if u.isLocalFile() and u.toLocalFile().lower().endswith(".zip"):
                self.zip_edit.setText(u.toLocalFile())
                break
        event.acceptProposedAction()

    # ------------------------------------------------------------ 刷新
    def refresh_lists(self):
        self._apply_mode()
        self.local_list.clear()
        for name in list_local_plugins(self.ctx.settings):
            if name == "ai":
                # 内置 AI 插件不可卸载/不可操作，只在 AI 大脑页关闭
                continue
            if name == "nonebot_adapter_ilink":
                # 内置微信 iLink 适配器（官方通道），不可操作，随机器人启动自动同步
                continue
            if name == "qbm_bridge_client":
                # 内置统一大脑桥客户端（微信 → dsh），不可卸载，随机器人启动自动同步
                continue
            self.local_list.addItem(name)
        self.store_list.clear()
        for name in list_store_plugins(self.ctx.settings):
            self.store_list.addItem(name)
        self.pack_list.clear()
        for p in tool_packs.installed(self.ctx.settings):
            frozen = "（冻结）" if p.get("frozen") else ""
            item = QListWidgetItem(
                f"{p.get('name') or p.get('id')} v{p.get('version') or '?'}{frozen}")
            item.setData(Qt.UserRole, p.get("id") or "")
            tool_names = "、".join(p.get("tools") or [])
            tip = (str(p.get("name") or "") + "\n")
            tip += ("工具：" + tool_names if tool_names else "行为/人设包")
            item.setToolTip(tip)
            self.pack_list.addItem(item)
        count = self.pack_list.count()
        self.pack_status.setText(
            f"共 {count} 个能力包" if count
            else "还没有装能力包；可在上面的插件商店里挑一个装上。")
        # 商店卡片上的「✓ 已装」跟着一起刷新（2026-10-12 老板报："装完商店里
        # 还显示可以安装"）—— 以前装完只刷了下面三个列表，商店卡片要离开页面
        # 再进来才会变，看起来就像"没装上"。
        self._refresh_market_list()
        if getattr(self, "_nb_loaded", False):
            # NoneBot 商店列表也要跟着标「✅已安装」（装完立刻能看到）
            self._refresh_nonebot_list()


class PackDetailDialog(QDialog):
    """能力包详情：介绍 / 功能 / 参数。

    参数来自 manifest 的 config.fields（没有则按 behavior 的键自动生成），
    按 type 渲染成开关 / 数字 / 下拉 / 文本框，保存后写 <能力包目录>/config.json，
    重启机器人生效。schema 与无头端控制台的参数表单一致。
    """

    def __init__(self, settings, detail: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.detail = detail or {}
        self.changed = False
        self._widgets = {}      # key -> (field, 取值函数)
        self.range_boxes = {}   # key -> (最小框, 最大框)，区间参数专用
        self.setWindowTitle("能力包详情")
        self.setMinimumSize(560, 420)
        self.resize(680, 640)
        self._build_ui()

    def _build_ui(self):
        d = self.detail
        # 内容放进滚动区：参数多的时候窗口装不下，Qt 会把自动换行的说明文字压扁
        # （历史反复踩过：文字只显示上半截）。按钮留在下面不跟着滚。
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(16)

        head = QHBoxLayout()
        name = QLabel(str(d.get("name") or d.get("id") or ""))
        name.setObjectName("cardTitle")
        head.addWidget(name)
        meta = " · ".join(x for x in (
            f"v{d.get('version')}" if d.get("version") else "",
            str(d.get("kind") or ""),
            str(d.get("tier") or ""),
        ) if x)
        m = QLabel(meta)
        m.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        head.addWidget(m)
        head.addStretch(1)
        lay.addLayout(head)

        desc = QLabel(str(d.get("description") or "（这个能力包没有写介绍）"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        lay.addWidget(desc)

        feats = d.get("features") or []
        if feats:
            fl = QLabel("功能")
            fl.setObjectName("sectionTitle")
            lay.addWidget(fl)
            for f in feats[:12]:
                line = QLabel("· " + str(f.get("title")) +
                              (f"：{f.get('desc')}" if f.get("desc") else ""))
                line.setWordWrap(True)
                line.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
                lay.addWidget(line)

        fields = d.get("fields") or []
        if not fields:
            pl = QLabel("参数")
            pl.setObjectName("sectionTitle")
            lay.addWidget(pl)
            none = QLabel("这个能力包没有可调参数，装上即用。")
            none.setWordWrap(True)
            none.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
            lay.addWidget(none)
        else:
            # 参数区做成一张玻璃卡片：和页面里的卡片同一套边距/间距，弹窗里也不出戏
            card = GlassPanel(strong=True)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(16, 14, 16, 16)
            cv.setSpacing(10)
            pl = QLabel("参数")
            pl.setObjectName("sectionTitle")
            cv.addWidget(pl)
            cv.addWidget(self._param_note())
            # 参数一块一块竖着排：名字独占一行（自动换行、要多少高给多少高），
            # 下面才是输入框，再下面是那句说明。
            # 以前用 QFormLayout 把名字塞在左列：中文名一换行就被压成一行高，
            # 直接和输入框叠在一起（2026-10-01 老板截图：文字/数字糊成一团）。
            rows = QVBoxLayout()
            rows.setSpacing(14)
            values = d.get("values") or {}
            for field in fields:
                key = str(field.get("key") or "")
                if not key:
                    continue
                block = QWidget()
                bv = QVBoxLayout(block)
                bv.setContentsMargins(0, 0, 0, 0)
                bv.setSpacing(4)
                name = QLabel(self._field_label(field))
                name.setWordWrap(True)
                name.setStyleSheet("font-size: 12.5px;")
                # 英文 key 只留给排查用：鼠标停在参数名上就能看到，界面正文不出现
                name.setToolTip(f"参数 key（内部标识）：{key}")
                bv.addWidget(name)

                value_row = QHBoxLayout()
                value_row.setContentsMargins(0, 0, 0, 0)
                value_row.setSpacing(8)
                widget = self._make_widget(field, values.get(key, field.get("default")))
                value_row.addWidget(widget)
                value_row.addStretch(1)
                bv.addLayout(value_row)

                hint = self._field_hint(field)
                if hint:
                    hint_lbl = QLabel(hint)
                    hint_lbl.setWordWrap(True)
                    hint_lbl.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
                    bv.addWidget(hint_lbl)
                rows.addWidget(block)
            cv.addLayout(rows)
            lay.addWidget(card)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("ghost")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        self.btn_save = QPushButton("保存参数")
        self.btn_save.setObjectName("primary")
        self.btn_save.setEnabled(bool(fields))
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_save)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        root.addLayout(row)

    @staticmethod
    def _param_note() -> QLabel:
        """参数区顶部那句说明：中文名 + 范围，英文 key 只在悬停提示里。"""
        note = QLabel("中文名后面括号是单位，按范围填写即可。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        return note

    @staticmethod
    def _field_label(field: dict) -> str:
        """参数名：中文名 +（单位）+，可填范围 a–b（都跟着 schema 走，界面不写死）。"""
        key = str(field.get("key") or "")
        label = str(field.get("label") or key)
        unit = str(field.get("unit") or "")
        if unit and unit not in label:
            label += f"（{unit}）"
        hint = tool_packs.range_hint(field)
        if hint:
            label += f"，{hint}"
        return label

    @staticmethod
    def _field_hint(field: dict) -> str:
        """参数下面那行人话说明（映射表里带的）。"""
        return str(field.get("help") or "").strip()

    def _make_widget(self, field: dict, value):
        """按 schema 的 type 造控件，并记住「怎么取值」。"""
        key = str(field.get("key"))
        ftype = str(field.get("type") or "text")
        if ftype == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            self._widgets[key] = (field, box.isChecked)
            return box
        if ftype == "range":
            return self._make_range_widget(field, value)
        if ftype == "number":
            step = float(field.get("step") or 1)
            integer = step >= 1 and float(value or 0) == int(float(value or 0))
            box = QSpinBox() if integer else QDoubleSpinBox()
            box.setRange(int(field.get("min", -10 ** 9)) if integer else float(field.get("min", -1e9)),
                         int(field.get("max", 10 ** 9)) if integer else float(field.get("max", 1e9)))
            box.setSingleStep(int(step) if integer else step)
            try:
                box.setValue(int(float(value)) if integer else float(value))
            except (TypeError, ValueError):
                pass
            self._widgets[key] = (field, box.value)
            return box
        if ftype in ("enum", "select"):
            combo = QComboBox()
            for opt in field.get("options") or []:
                if isinstance(opt, dict):
                    combo.addItem(str(opt.get("label") or opt.get("value")), opt.get("value"))
                else:
                    combo.addItem(str(opt), opt)
            idx = combo.findData(value)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._widgets[key] = (field, combo.currentData)
            return combo
        edit = QLineEdit(str(value if value is not None else ""))
        self._widgets[key] = (field, edit.text)
        return edit

    def _make_range_widget(self, field: dict, value):
        """区间参数（例如「忙很久的回复间隔 180~900 秒」）：两个数字框 + 中间一个 ~。

        取值给回 [最小, 最大] 这种列表 —— 和能力包 manifest 里的默认值同一种写法；
        校验（不能反着填、不能超出范围）交给 core.tool_packs.coerce_field 统一做。
        """
        key = str(field.get("key"))
        lo, hi = tool_packs.split_range(value, field)
        step = float(field.get("step") or 1)
        integer = step >= 1
        low = float(field.get("min", 0))
        high = float(field.get("max", 10 ** 9))

        def make_box(v):
            box = QSpinBox() if integer else QDoubleSpinBox()
            box.setRange(int(low) if integer else low, int(high) if integer else high)
            box.setSingleStep(int(step) if integer else step)
            box.setValue(int(v) if integer else float(v))
            box.setMinimumWidth(84)
            return box

        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        box_lo, box_hi = make_box(lo), make_box(hi)
        row.addWidget(box_lo)
        sep = QLabel("~")
        sep.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        row.addWidget(sep)
        row.addWidget(box_hi)
        row.addStretch(1)
        self._widgets[key] = (field, lambda: [box_lo.value(), box_hi.value()])
        self.range_boxes[key] = (box_lo, box_hi)   # 测试/后续联动要用到这两个框
        return wrap

    def _save(self):
        patch = {key: getter() for key, (_f, getter) in self._widgets.items()}
        try:
            res = tool_packs.save_pack_config(self.settings, self.detail.get("id"), patch)
        except (ValueError, RuntimeError, OSError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.changed = bool(res.get("changed"))
        self.accept()



class QWeatherConfigDialog(QDialog):
    """和风天气工具包配置：自动生成 Ed25519 密钥对 + 展示公钥 + 指引控制台。"""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("和风天气 · 配置")
        self.resize(640, 560)
        self._key_path = str(Path(settings.root) / "qweather_ed25519_private.pem")
        cfg = tool_packs.load_qweather_config(settings)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(12)
        tip = QLabel("按下方的 6 步在控制台操作。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        lay.addWidget(tip)

        r0 = QHBoxLayout()
        r0.addWidget(QLabel("API Host"))
        self.edit_host = QLineEdit(str(cfg.get("api_host") or ""))
        self.edit_host.setPlaceholderText("如 xxx.qweatherapi.com")
        r0.addWidget(self.edit_host, 1)
        lay.addLayout(r0)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("项目ID"))
        self.edit_project = QLineEdit(str(cfg.get("project_id") or "3K85YMFKU3"))
        r1.addWidget(self.edit_project, 1)
        lay.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("凭据ID"))
        self.edit_cred = QLineEdit(str(cfg.get("credential_id") or ""))
        self.edit_cred.setPlaceholderText("创建 JWT 凭据后页面显示的 ID")
        r2.addWidget(self.edit_cred, 1)
        lay.addLayout(r2)

        key_row = QHBoxLayout()
        self.btn_gen = QPushButton("生成密钥对")
        self.btn_gen.setObjectName("primary")
        self.btn_gen.clicked.connect(self._gen_keypair)
        self.lbl_key_status = QLabel("未生成")
        self.lbl_key_status.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        key_row.addWidget(self.btn_gen)
        key_row.addWidget(self.lbl_key_status, 1)
        lay.addLayout(key_row)

        self.pub_box = QPlainTextEdit()
        self.pub_box.setReadOnly(True)
        self.pub_box.setPlaceholderText("生成的公钥会显示在这里，整段复制到控制台…")
        self.pub_box.setMaximumHeight(120)
        lay.addWidget(self.pub_box)

        steps = QLabel(
            "1. 控制台 → 设置 → 复制「API Host」填到最上面\n"
            "2. 控制台 → 项目管理 → 记下项目ID\n"
            "3. 添加凭据 → 身份认证选「JSON Web Token」\n"
            "4. 公钥整段（含 BEGIN/END 行）粘贴进去 → 保存\n"
            "5. 保存后显示的「凭据 ID」填到凭据ID框\n"
            "6. 点「保存配置」，然后重启机器人")
        steps.setWordWrap(True)
        steps.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        lay.addWidget(steps)

        btn_row = QHBoxLayout()
        btn_open = QPushButton("打开官方文档")
        btn_open.setObjectName("ghost")
        btn_open.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://dev.qweather.com/docs/configuration/authentication/")))
        btn_save = QPushButton("保存配置")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_open)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_save)
        lay.addLayout(btn_row)

    def _gen_keypair(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
        except Exception:  # noqa: BLE001
            QMessageBox.warning(
                self, "缺少依赖",
                "当前程序缺 cryptography 库，无法生成密钥对。\n"
                "请更新程序后重试（开发预览请先安装 requirements.txt）。")
            return
        priv = Ed25519PrivateKey.generate()
        try:
            Path(self._key_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self._key_path, "wb") as f:
                f.write(priv.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption()))
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                pass
        except OSError as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", "私钥写入失败：" + str(e))
            return
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        self.pub_box.setPlainText(pub_pem)
        self.lbl_key_status.setText("已生成，私钥保存在：" + self._key_path)

    def _save(self):
        api_host = self.edit_host.text().strip()
        project_id = self.edit_project.text().strip()
        credential_id = self.edit_cred.text().strip()
        key_path = self._key_path if os.path.exists(self._key_path) else ""
        try:
            tool_packs.save_qweather_config(
                self.settings, project_id, credential_id, key_path, api_host)
        except OSError as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.accept()
