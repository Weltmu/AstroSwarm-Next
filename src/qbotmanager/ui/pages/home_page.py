# -*- coding: utf-8 -*-
"""首页：总览仪表盘 —— 各平台服务状态 + 今日数据 + 最近消息预览 + 快速操作。"""
import logging
import time
import webbrowser

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from ...core import message_store
from ...tasks.workers import StartStopTask
from ..theme import TEXT_3
from ..widgets import EmptyState, GlassPanel, StatusBadge
from .common import PageContext, TaskPanel, make_row


class _ClickablePanel(GlassPanel):
    """整张卡可点：顶掉卡片里的小按钮（点卡片任意位置就进详情）。"""

    clicked = Signal()

    def __init__(self, parent=None, strong=False):
        super().__init__(parent, strong=strong)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802  (Qt 命名)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomePage(QWidget):
    def __init__(self, ctx: PageContext):
        super().__init__()
        self.ctx = ctx
        self._service_tid = None
        self._bot_running = False
        self._build_ui()
        self._connect_tasks()
        self.refresh_status()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        # 首页内容比一屏略高：窗口一矮 Qt 就会去压自动换行的小字行数（表现：文字只显示上半截）。
        # 按其它页面的做法套一层 QScrollArea —— 够高时和原来一模一样，不够高才出滚动条。
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 20, 24, 20)
        # 间距 14 而不是 16：正好让 1280x800 的默认窗口一屏放下（不留滚动条），再小才滚
        outer.setSpacing(14)

        title_row = QHBoxLayout()
        left = QVBoxLayout()
        title = QLabel("首页")
        title.setObjectName("pageTitle")
        sub = QLabel("各平台服务状态总览，一键进入管理")
        sub.setObjectName("pageSub")
        left.addWidget(title)
        left.addWidget(sub)
        title_row.addLayout(left)
        title_row.addStretch(1)
        self.global_badge = StatusBadge("服务状态", "unknown")
        self.last_update = QLabel("—")
        self.last_update.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        title_row.addWidget(self.last_update)
        title_row.addWidget(self.global_badge)
        outer.addLayout(title_row)

        # ---- 三步开箱：① 连 AI → ② 接 QQ → ③ 启动（每步一个按钮，做完自动打勾）----
        # 老板拍板的方向："极致的简化" —— 不砍功能，而是把主流程压成三步一眼看全。
        self.steps_bar = QFrame()
        self.steps_bar.setObjectName("stepsBar")
        self.steps_bar.setStyleSheet(
            "QFrame#stepsBar { background: rgba(255,255,255,0.035);"
            " border: 1px solid rgba(255,255,255,0.09); border-radius: 10px; }"
        )
        sh = QHBoxLayout(self.steps_bar)
        sh.setContentsMargins(14, 10, 14, 10)
        sh.setSpacing(10)
        self.step_labels = {}
        self.step_btns = {}
        for key, text, btn_text in (
            ("ai", "1 连 AI", "去填 Key"),
            ("qq", "2 接 QQ", "去接入"),
            ("run", "3 启动", "启动"),
        ):
            lab = QLabel(f"{text} · 待办")
            lab.setStyleSheet("font-size: 13px;")
            self.step_labels[key] = lab
            sh.addWidget(lab)
            btn = QPushButton(btn_text)
            btn.clicked.connect(lambda _=False, k=key: self._on_step_clicked(k))
            self.step_btns[key] = btn
            sh.addWidget(btn)
            if key != "run":
                sep = QLabel("→")
                sep.setStyleSheet(f"color: {TEXT_3};")
                sh.addWidget(sep)
        sh.addStretch(1)
        # "你想要什么功能" 大输入框：一步进工坊（工坊优先，别让人自己在侧栏里找）
        self.wish_input = QLineEdit()
        self.wish_input.setPlaceholderText("你想要什么功能？一句话，我让 AI 给你做一个插件")
        self.wish_input.setMinimumWidth(260)
        self.wish_input.returnPressed.connect(self._on_wish)
        sh.addWidget(self.wish_input, 1)
        btn_wish = QPushButton("去做插件")
        btn_wish.setObjectName("primary")
        btn_wish.clicked.connect(self._on_wish)
        sh.addWidget(btn_wish)
        outer.addWidget(self.steps_bar)

        # ---- 「下一步」引导：只显示当下最该做的那一件事 ----
        self.next_bar = QFrame()
        self.next_bar.setObjectName("nextStepBar")
        self.next_bar.setStyleSheet(
            "QFrame#nextStepBar { background: rgba(127,176,255,0.10);"
            " border: 1px solid rgba(127,176,255,0.35); border-radius: 10px; }"
        )
        nb = QHBoxLayout(self.next_bar)
        nb.setContentsMargins(14, 10, 14, 10)
        nb.setSpacing(10)
        self.next_text = QLabel("")
        self.next_text.setStyleSheet("font-size: 13px;")
        nb.addWidget(self.next_text, 1)
        self.next_btn = QPushButton("去配置")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._on_next_clicked)
        nb.addWidget(self.next_btn)
        self.next_bar.setVisible(False)
        outer.addWidget(self.next_bar)
        self._next_action = ""

        # ---- 平台状态卡片（QQ / 微信 / 飞书 / 纸飞机） ----
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self._cards_layout = cards
        self._build_qq_card(cards)
        self._build_wechat_card(cards)
        self._build_feishu_card(cards)
        self._build_telegram_card(cards)
        outer.addLayout(cards)

        # ---- 今日数据 + 最近消息 ----
        mid = QHBoxLayout()
        mid.setSpacing(16)

        stats = GlassPanel()
        stv = QVBoxLayout(stats)
        stv.setContentsMargins(16, 14, 16, 16)
        sl = QLabel("今日数据")
        sl.setObjectName("sectionTitle")
        stv.addWidget(sl)
        self.stat_labels = {}
        for key, label in (
            ("messages", "消息总数"),
            ("ai_calls", "AI 调用"),
            ("mcp", "MCP 服务器"),
            ("memory", "记忆条数"),
            ("identity", "身份绑定"),
        ):
            val = QLabel("—")
            val.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
            self.stat_labels[key] = val
            stv.addLayout(make_row(label, val))
        stv.addStretch(1)
        mid.addWidget(stats, 3)

        recent = GlassPanel(strong=True)
        rv = QVBoxLayout(recent)
        rv.setContentsMargins(16, 14, 16, 16)
        rl = QLabel("最近消息")
        rl.setObjectName("sectionTitle")
        rv.addWidget(rl)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentList")
        self.recent_list.setFrameShape(QFrame.NoFrame)
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.recent_list.setWordWrap(True)
        self.recent_list.setFocusPolicy(Qt.NoFocus)
        self.recent_list.setSelectionMode(QListWidget.NoSelection)
        self.recent_list.setStyleSheet(
            "QListWidget#recentList { background: transparent; border: none; }"
            "QListWidget#recentList::item { color: #9FB0C8; font-size: 12px;"
            " padding: 6px 4px; border-bottom: 1px solid rgba(255,255,255,0.05); }"
            "QListWidget#recentList::item:hover { background: rgba(255,255,255,0.04); }"
        )
        rv.addWidget(self.recent_list, 1)
        # 消息预览本身就是入口：点任意一条直接进消息中心（省掉一个按钮）
        self.recent_list.setCursor(Qt.PointingHandCursor)
        self.recent_list.itemClicked.connect(
            lambda _item: self.ctx.switch_page("消息中心"))
        hint_center = QLabel("点上面任意一条 → 进入消息中心")
        hint_center.setStyleSheet(f"color: {TEXT_3}; font-size: 11px;")
        rv.addWidget(hint_center)
        mid.addWidget(recent, 7)
        outer.addLayout(mid, 1)

        # ---- 主操作 + 高级（运维动作默认收起） + 当前任务 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        quick = GlassPanel()
        qv = QVBoxLayout(quick)
        qv.setContentsMargins(16, 14, 16, 16)
        qv.setSpacing(8)
        ql = QLabel("机器人")
        ql.setObjectName("sectionTitle")
        qv.addWidget(ql)
        # 一屏只留一个主操作：运行中就变成「停止全部」，两个按钮不再同屏抢注意力
        self.btn_toggle = QPushButton("启动全部")
        self.btn_toggle.setObjectName("primary")
        self.btn_toggle.clicked.connect(self._on_toggle_clicked)
        qv.addWidget(self.btn_toggle)
        # 一屏只留一个主操作；重启在「全局管理」、.env 在「设置 → 维护」，不再堆在首页
        tip_op = QLabel("一键启停全部通道。")
        tip_op.setToolTip("重启在「全局管理」。")
        tip_op.setWordWrap(True)
        tip_op.setStyleSheet(f"color: {TEXT_3}; font-size: 11px;")
        qv.addWidget(tip_op)
        qv.addStretch(1)
        bottom.addWidget(quick, 4)

        task_glass = GlassPanel(strong=True)
        tv = QVBoxLayout(task_glass)
        tv.setContentsMargins(16, 14, 16, 16)
        tl = QLabel("当前任务")
        tl.setObjectName("sectionTitle")
        tv.addWidget(tl)
        self.task_panel = TaskPanel(self.ctx.tasks)
        self.task_panel.setVisible(False)
        self.task_panel.action_requested.connect(self._on_task_action)
        tv.addWidget(self.task_panel)
        self.task_empty = EmptyState(
            "当前没有正在执行的任务",
            "点击「启动全部」开始，或在插件/依赖页发起安装任务",
        )
        tv.addWidget(self.task_empty, 1)
        bottom.addWidget(task_glass, 6)
        outer.addLayout(bottom)
        self.set_beginner(bool(self.ctx.settings.beginner_mode))

        self.scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.scroll)

    def set_beginner(self, on: bool):
        """小白模式：首页只保留 QQ / 微信两张状态卡，隐藏飞书与纸飞机。"""
        on = bool(on)
        layout = getattr(self, "_cards_layout", None)
        if layout is None:
            return
        for i in (2, 3):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setVisible(not on)

    def _card(self, parent_layout, name, on_click=None):
        card = _ClickablePanel() if on_click else GlassPanel()
        if on_click:
            card.clicked.connect(on_click)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(8)
        badge = StatusBadge(name, "unknown")
        v.addWidget(badge)
        parent_layout.addWidget(card, 1)
        return card, v, badge

    def _build_qq_card(self, cards):
        card, v, self.qq_badge = self._card(
            cards, "QQ 机器人", on_click=lambda: self.ctx.switch_page("接入"))
        self.qq_info = QLabel("—")
        self.qq_info.setWordWrap(True)
        self.qq_info.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        v.addWidget(self.qq_info)
        v.addStretch(1)

    def _build_wechat_card(self, cards):
        card, v, self.wx_badge = self._card(
            cards, "微信 iLink", on_click=lambda: self.ctx.switch_page("接入"))
        self.wx_info = QLabel("—")
        self.wx_info.setWordWrap(True)
        self.wx_info.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        v.addWidget(self.wx_info)
        row = QHBoxLayout()
        # 微信通道已免费开放，不再需要「开通」按钮（2026-10-01 老板拍板）
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)

    def _open_pay(self):
        from ...core import license as lic_mod
        webbrowser.open(lic_mod.PAY_URL)

    def _build_feishu_card(self, cards):
        card, v, self.feishu_badge = self._card(cards, "飞书")
        self.feishu_badge.set_status("stopped", "飞书 · 未接入")
        tip = QLabel("规划中")
        tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        v.addWidget(tip)
        v.addStretch(1)

    def _build_telegram_card(self, cards):
        card, v, self.telegram_badge = self._card(cards, "纸飞机")
        self.telegram_badge.set_status("stopped", "纸飞机 · 未接入")
        tip = QLabel("规划中")
        tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        v.addWidget(tip)
        v.addStretch(1)

    # ------------------------------------------------------------ 任务
    def _connect_tasks(self):
        t = self.ctx.tasks
        t.started.connect(self._on_service_started)
        t.finished.connect(self._on_service_finished)

    def _on_service_started(self, tid, name):
        if name in ("启动服务", "停止服务"):
            self._service_tid = tid
            self.task_panel.bind(tid)
            self.task_panel.setVisible(True)
            self.task_empty.setVisible(False)

    def _on_service_finished(self, tid, status, result):
        if tid == self._service_tid:
            self.task_panel.bind(tid)
        self.refresh_status()

    def _on_task_action(self, action, task_id):
        if action == "cancel":
            self.ctx.tasks.cancel(task_id)
        elif action == "retry":
            self.task_panel.bind(self.ctx.tasks.retry(task_id) or "")
        elif action == "logs":
            self.ctx.open_logs()

    def _start_stop(self, start: bool):
        self.ctx.tasks.submit(
            "启动服务" if start else "停止服务", "service", StartStopTask,
            payload={"start": start}, retryable=True, manager=self.ctx.manager,
        )

    def _on_toggle_clicked(self):
        """主操作：未运行→启动全部；运行中→先二次确认再停止全部。"""
        if self._bot_running:
            if not self._confirm("停止机器人",
                                 "停止后 QQ / 微信都会离线，正在进行的对话会中断。\n\n确定停止吗？"):
                return
        self._start_stop(not self._bot_running)

    def _refresh_next_step(self):
        """状态引导：首页只顶出「现在最该做的那一步」，别的都不抢注意力。"""
        s = self.ctx.settings
        m = self.ctx.manager
        running = bool(m.bot_running() or m.qq_running() or m.dsh_running())
        if running:
            self.next_bar.setVisible(False)
            self._next_action = ""
            return
        summary = message_store.brain_summary(s)
        ai_ok = bool(str(summary.get("model") or "").strip())
        official_missing = (
            str(getattr(s, "qq_channel", "onebot")) == "official"
            and not str(getattr(s, "qq_official_appid", "") or "").strip()
        )
        if not ai_ok:
            text, btn, action = "下一步：先配 AI 大脑（填 API Key，约 30 秒）", "去配置", "brain"
        elif official_missing:
            text, btn, action = "下一步：接 QQ 官方机器人（填 AppID / Token）", "去接入", "access"
        else:
            text, btn, action = "下一步：启动机器人（QQ / 微信 一起上线）", "一键启动", "start"
        self.next_text.setText(text)
        self.next_btn.setText(btn)
        self._next_action = action
        self.next_bar.setVisible(True)

    def _on_next_clicked(self):
        action = getattr(self, "_next_action", "")
        if action == "start":
            self._on_toggle_clicked()
        elif action == "brain":
            self.ctx.switch_page("AI 大脑")
        else:
            self.ctx.switch_page("接入")

    def _on_step_clicked(self, key: str):
        """三步里的按钮：① 去 AI 大脑 ② 去接入 ③ 直接启动。"""
        if key == "ai":
            self.ctx.switch_page("AI 大脑")
        elif key == "qq":
            self.ctx.switch_page("接入")
        else:
            self._on_toggle_clicked()

    def _on_wish(self):
        """"你想要什么功能" → 带着这句话跳到插件工坊（工坊页 refresh 时自动填进需求框）。"""
        text = self.wish_input.text().strip()
        try:
            self.ctx.pending_workshop_prompt = text
            self.ctx.switch_page("插件工坊")
            page = getattr(self.ctx, "page_by_name", None)
            if callable(page):
                target = page("插件工坊")
                if target is not None and hasattr(target, "refresh_status"):
                    target.refresh_status()
        except Exception as e:  # noqa: BLE001 —— 跳页失败不能把首页弄崩
            logging.getLogger("qbotmanager").warning("跳转插件工坊失败: %s", e)

    def _refresh_steps(self):
        """三步各自的状态：✓ 已配好 / · 现在做 / · 待办。"""
        s = self.ctx.settings
        m = self.ctx.manager
        summary = message_store.brain_summary(s)
        model = str(summary.get("model") or "").strip()
        ai_ok = bool(model)
        official = str(getattr(s, "qq_channel", "onebot")) == "official"
        if official:
            qq_ok = bool(str(getattr(s, "qq_official_appid", "") or "").strip())
        else:
            state = m.qq_login_state() or {}
            qq_ok = bool(state.get("connected")) or bool(m.qq_running())
        run_ok = bool(m.bot_running() or m.qq_running() or m.dsh_running())

        def mark(label, text, ok, is_next):
            if ok:
                return f"{text} · ✓ 已配好"
            if is_next:
                return f"{text} · 现在做这个"
            return f"{text} · 待办"

        states = [(("ai"), "1 连 AI", ai_ok), (("qq"), "2 接 QQ", qq_ok),
                  (("run"), "3 启动", run_ok)]
        first_pending = next((k for k, _t, ok in states if not ok), "")
        for key, text, ok in states:
            lab = self.step_labels.get(key)
            if lab is None:
                continue
            lab.setText(mark(lab, text, ok, key == first_pending))
            color = "#34D399" if ok else ("#7FB0FF" if key == first_pending else TEXT_3)
            lab.setStyleSheet(f"font-size: 13px; color: {color};")
            btn = self.step_btns.get(key)
            if btn is not None:
                btn.setEnabled(not ok)
                if key == "run" and not ok:
                    btn.setText("启动")

    def _confirm(self, title: str, text: str) -> bool:
        from PySide6.QtWidgets import QMessageBox

        ret = QMessageBox.question(self, title, text,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ret == QMessageBox.Yes

    # ------------------------------------------------------------ 状态
    def refresh_status(self):
        s = self.ctx.settings
        m = self.ctx.manager
        try:
            self._refresh_steps()
        except Exception as e:  # noqa: BLE001 —— 步骤条算不出来不能影响整页刷新
            logging.getLogger("qbotmanager").warning("首页三步条刷新失败: %s", e)
        bot_on = m.bot_running()
        qq_on = m.qq_running() or m.dsh_running()
        qq_st = m.qq_login_state()
        qq = str(qq_st.get("account") or "未配置")
        self.qq_badge.set_status("running" if qq_on else "stopped",
                                 "QQ · " + ("运行中" if qq_on else "已停止"))
        self.qq_info.setText(f"AppID {qq}\nNoneBot {'运行' if bot_on else '停止'} / "
                             + ("dsh 群聊 AI 运行中"
                                if m.dsh_running()
                                else f"官方通道 {qq_st.get('reason') or '未知'}"))

        from ...core import license as lic_mod

        gate = lic_mod.feature_gate()
        if not gate.get("member", True):
            # 只有把 WECHAT_FREE_FOR_ALL 改回 False 才会走到这里
            self.wx_badge.set_status("stopped", "微信 · 通道已关闭")
            self.wx_info.setText(gate.get("reason") or "微信通道当前不可用")
            wx = {"logged_in": False}
        else:
            wx = m.wechat_status()
            if not wx["installed"]:
                self.wx_badge.set_status("unknown", "微信 · 未安装适配器")
            elif wx.get("connected"):
                self.wx_badge.set_status("connected", "微信 · 已连接")
            elif wx["logged_in"]:
                self.wx_badge.set_status("warning", "微信 · 未连接")
            else:
                self.wx_badge.set_status("stopped", "微信 · 未登录")
            self.wx_info.setText(str(wx["bot_id"] or "未登录") + "\niLink 私聊个人助手")

        any_on = bot_on or qq_on or wx.get("logged_in")
        self.global_badge.set_status("running" if any_on else "stopped",
                                     "服务" + ("运行中" if any_on else "已停止"))
        self.last_update.setText("更新于 " + time.strftime("%H:%M:%S"))
        # 主操作随状态翻转：运行中就是「停止全部」
        self._bot_running = bool(bot_on)
        self.btn_toggle.setText("停止全部" if bot_on else "启动全部")
        self._refresh_next_step()

        summary = message_store.brain_summary(s)
        self.stat_labels["messages"].setText(str(message_store.load_message_count(s)))
        self.stat_labels["ai_calls"].setText("—")
        self.stat_labels["mcp"].setText(str(summary["mcp_servers"]))
        self.stat_labels["memory"].setText(str(summary["memory_global"] + summary["memory_users"]))
        self.stat_labels["identity"].setText(str(summary["identity_binds"]))

        convs = message_store.load_conversations(s, 8)
        self.recent_list.clear()
        if not convs:
            item = QListWidgetItem("暂无消息记录")
            item.setForeground(QColor(127, 127, 127))
            self.recent_list.addItem(item)
        else:
            for c in reversed(convs[-6:]):
                label = message_store.PLATFORM_LABELS.get(c["platform"], c["platform"])
                scene = "群" if c["scene"] == "group" else "私聊"
                text = f"{label}{scene} {c['room']}：{c['last']}"
                item = QListWidgetItem(text)
                item.setToolTip(c["last"])
                self.recent_list.addItem(item)
