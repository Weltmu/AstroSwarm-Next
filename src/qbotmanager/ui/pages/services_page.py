# -*- coding: utf-8 -*-
"""全局管理（服务）：机器人进程启停 + 运维细节（端口 / 目录 / 日志位置）。

对应无头端控制台的「全局管理」页，口径一致：

- 这一页只回答「服务在不在跑」+ 每行一个动作；
- 端口 / 机器人目录 / 日志路径这些运维细节全部收进「运维细节」折叠区，默认不占版面；
- 停止是危险动作：放在折叠区里、用次要按钮样式，并且必须二次确认。

控制台那边这些动作走 /api/services/{start,stop,restart}，桌面端走同一套 task 系统
（StartStopTask / RestartBotTask），所以两边的状态文案与确认话术保持一致。
"""
import os
import sys
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ...constants import APP_VERSION
from ...tasks.workers import RestartBotTask, StartStopTask
from ..theme import TEXT_3
from ..widgets import GlassPanel, StatusBadge
from .common import PageContext, TaskPanel, Worker, make_row

_FOLD_CLOSED = "运维细节：端口 / 目录 / 日志路径　▾"
_FOLD_OPEN = "运维细节：端口 / 目录 / 日志路径　▴"


class ServicesPage(QWidget):
    def __init__(self, ctx: PageContext):
        super().__init__()
        self.ctx = ctx
        self._service_tid = None
        self._bot_running = False
        self._machine_id = ""
        self._details_open = False
        self._build_ui()
        self._connect_tasks()
        self.refresh_status()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)

        title = QLabel("全局管理")
        title.setObjectName("pageTitle")
        sub = QLabel("服务状态与开关。")
        sub.setObjectName("pageSub")
        outer.addWidget(title)
        outer.addWidget(sub)

        panel = GlassPanel(strong=True)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(16, 14, 16, 16)
        pv.setSpacing(14)
        pv.addLayout(self._build_bot_row())
        pv.addLayout(self._build_dsh_row())
        outer.addWidget(panel)

        self.task_panel = TaskPanel(self.ctx.tasks)
        self.task_panel.setVisible(False)
        self.task_panel.action_requested.connect(self._on_task_action)
        outer.addWidget(self.task_panel)

        outer.addWidget(self._build_details())
        outer.addStretch(1)

    def _build_bot_row(self):
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        name = QLabel("机器人")
        name.setStyleSheet("font-size: 14px;")
        meta = QLabel("QQ / 微信 共用一个进程，重启会短暂离线约 10 秒")
        meta.setWordWrap(True)
        meta.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        left.addWidget(name)
        left.addWidget(meta)
        row.addLayout(left, 1)
        self.bot_badge = StatusBadge("状态未知", "unknown")
        row.addWidget(self.bot_badge)
        self.btn_bot = QPushButton("启动")
        self.btn_bot.setObjectName("primary")
        self.btn_bot.clicked.connect(self._on_bot_button)
        row.addWidget(self.btn_bot)
        return row

    def _build_dsh_row(self):
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        name = QLabel("DSH 智能体")
        name.setStyleSheet("font-size: 14px;")
        meta = QLabel("本机的群聊智能体通道")
        meta.setWordWrap(True)
        meta.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        left.addWidget(name)
        left.addWidget(meta)
        row.addLayout(left, 1)
        self.dsh_badge = StatusBadge("状态未知", "unknown")
        row.addWidget(self.dsh_badge)
        return row

    def _build_details(self):
        """运维细节折叠区：默认收起，展开后是端口/路径 + 一组次要入口。"""
        box = QFrame()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.btn_fold = QPushButton(_FOLD_CLOSED)
        self.btn_fold.setObjectName("ghost")
        self.btn_fold.clicked.connect(self._toggle_details)
        v.addWidget(self.btn_fold, 0, Qt.AlignLeft)

        self.details = GlassPanel()
        dv = QVBoxLayout(self.details)
        dv.setContentsMargins(16, 14, 16, 16)
        dv.setSpacing(8)
        self.detail_labels = {}
        for key, label in (("port", "端口"), ("bot_dir", "机器人目录"),
                           ("logs_dir", "日志目录"), ("version", "版本 / 机器码")):
            val = QLabel("—")
            val.setWordWrap(True)
            val.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
            self.detail_labels[key] = val
            dv.addLayout(make_row(label, val))
        # ---- 日志保留（默认 10 天，超期自动删）----
        log_row = QHBoxLayout()
        log_row.setSpacing(8)
        log_tip = QLabel("日志保留")
        log_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        log_row.addWidget(log_tip)
        self.spin_keep_days = QSpinBox()
        self.spin_keep_days.setRange(1, 365)
        self.spin_keep_days.setSuffix(" 天")
        self.spin_keep_days.setValue(int(getattr(self.ctx.settings, "logs_keep_days", 10) or 10))
        self.spin_keep_days.setToolTip("超期的旧日志自动删，正在写的不删。")
        self.spin_keep_days.valueChanged.connect(self._save_keep_days)
        log_row.addWidget(self.spin_keep_days)
        self.log_usage = QLabel("—")
        self.log_usage.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        log_row.addWidget(self.log_usage, 1)
        btn_clean_logs = QPushButton("立刻清理旧日志")
        btn_clean_logs.setObjectName("ghost")
        btn_clean_logs.clicked.connect(self._clean_logs_now)
        log_row.addWidget(btn_clean_logs)
        dv.addLayout(log_row)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        for text, handler in (            ("打开机器人目录", lambda: self._open_path(self.ctx.settings.bot_dir)),
            ("打开日志目录", lambda: self._open_path(self.ctx.settings.logs_dir)),
            ("实时日志", lambda: self.ctx.switch_page("日志")),
            ("消息中心", lambda: self.ctx.switch_page("消息中心")),
            ("依赖管理", lambda: self.ctx.switch_page("依赖")),
            ("接入配置", lambda: self.ctx.switch_page("接入")),
        ):
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.clicked.connect(handler)
            btns.addWidget(b)
        # 危险动作：次要样式 + 只有真的在跑时才出现（没跑就没必要「停止」）
        self.btn_stop = QPushButton("停止机器人")
        self.btn_stop.setObjectName("ghost")
        self.btn_stop.clicked.connect(self._stop_bot)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        dv.addLayout(btns)
        self.details.setVisible(False)
        v.addWidget(self.details)
        return box

    # ------------------------------------------------------------ 交互
    def _toggle_details(self):
        # 展开状态自己记着：details.isVisible() 在「当前不在这一页」时恒为 False，
        # 用它判断会出现「点收起反而又展开」。
        self._details_open = not self._details_open
        self.details.setVisible(self._details_open)
        self.btn_fold.setText(_FOLD_OPEN if self._details_open else _FOLD_CLOSED)

    def _save_keep_days(self, value: int):
        """日志保留天数：10 天是默认值（老板定的）。"""
        try:
            self.ctx.settings.logs_keep_days = max(1, min(int(value or 10), 365))
            self.ctx.settings.save()
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("保存日志保留天数失败：" + str(e))
            return
        self._refresh_log_usage()

    def _refresh_log_usage(self):
        from ...core import cleanup as cleanup_mod

        try:
            info = cleanup_mod.logs_usage(self.ctx.settings.logs_dir)
            keep = int(getattr(self.ctx.settings, "logs_keep_days", 10) or 10)
            self.log_usage.setText(f"现在占 {info['mb']} MB（{info['files']} 个文件）· 只留最近 {keep} 天")
        except Exception:  # noqa: BLE001
            self.log_usage.setText("—")

    def _clean_logs_now(self):
        from ...core import cleanup as cleanup_mod

        try:
            stats = cleanup_mod.rotate_and_prune_logs(
                self.ctx.settings.logs_dir,
                int(getattr(self.ctx.settings, "logs_keep_days", 10) or 10))
        except Exception as e:  # noqa: BLE001
            self.ctx.show_toast("清理失败：" + str(e))
            return
        freed = round((stats.get("bytes_freed") or 0) / 1048576, 2)
        msg = f"删了 {len(stats.get('deleted') or [])} 个旧日志，回收 {freed} MB"
        if stats.get("skipped"):
            msg += "（有几个正被占用，下次重启后再删）"
        self.ctx.show_toast(msg)
        self._refresh_log_usage()
    def _open_path(self, path):
        """打开目录：Windows 用资源管理器，其余平台退回系统默认方式。"""
        target = str(path)
        try:
            if hasattr(os, "startfile"):        # Windows
                os.startfile(target)            # noqa: S606
            else:
                import subprocess

                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, target])
        except OSError:
            self.ctx.show_toast("打不开这个目录：" + target)

    def _on_bot_button(self):
        if self._bot_running:
            self._restart_bot()
        else:
            self._start_stop(True)

    def _start_stop(self, start: bool):
        self.ctx.tasks.submit(
            "启动服务" if start else "停止服务", "service", StartStopTask,
            payload={"start": start}, retryable=True, manager=self.ctx.manager,
        )

    def _stop_bot(self):
        if not self._confirm(
                "停止机器人",
                "停止后 QQ / 微信 都会离线，机器人不再回复任何消息。\n\n确定停止吗？"):
            return
        self._start_stop(False)

    def _restart_bot(self):
        if not self._confirm(
                "重启机器人",
                "大约 10 秒内 QQ / 微信 会短暂离线，正在进行的对话会中断。\n\n确定重启吗？"):
            return
        self.ctx.tasks.submit(
            "重启机器人", "service", RestartBotTask,
            payload={"reset_wechat": False}, retryable=True, manager=self.ctx.manager,
        )

    def _confirm(self, title: str, text: str) -> bool:
        from PySide6.QtWidgets import QMessageBox

        ret = QMessageBox.question(self, title, text,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ret == QMessageBox.Yes

    # ------------------------------------------------------------ 任务
    def _connect_tasks(self):
        t = self.ctx.tasks
        t.started.connect(self._on_service_started)
        t.finished.connect(self._on_service_finished)

    def _on_service_started(self, tid, name):
        if name in ("启动服务", "停止服务", "重启机器人"):
            self._service_tid = tid
            self.task_panel.bind(tid)
            self.task_panel.setVisible(True)

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

    # ------------------------------------------------------------ 状态
    def refresh_status(self):
        self._refresh_log_usage()
        s = self.ctx.settings
        m = self.ctx.manager
        try:
            running = bool(m.bot_running())
        except Exception:  # noqa: BLE001 —— 管理器没就绪时不要把页面刷崩
            running = False
        self._bot_running = running
        if running:
            self.bot_badge.set_status("running", "运行中")
            self.btn_bot.setText("重启")
        elif s.is_deployed():
            self.bot_badge.set_status("stopped", "已停止（已部署）")
            self.btn_bot.setText("启动")
        else:
            self.bot_badge.set_status("unknown", "未部署（点启动会用部署脚本装好）")
            self.btn_bot.setText("启动")
        self.btn_stop.setVisible(running)
        try:
            dsh_on = bool(m.dsh_running())
        except Exception:  # noqa: BLE001
            dsh_on = False
        self.dsh_badge.set_status("running" if dsh_on else "unknown",
                                  "运行中" if dsh_on else "未运行（按需开启）")
        self.detail_labels["port"].setText(str(s.nonebot_port))
        self.detail_labels["bot_dir"].setText(str(s.bot_dir))
        self.detail_labels["logs_dir"].setText(str(s.logs_dir))
        if not self._machine_id:
            self._start_machine_id_probe()
        mid = f" · {self._machine_id[:8]}…" if self._machine_id else ""
        self.detail_labels["version"].setText(f"v{APP_VERSION}{mid}")

    def _start_machine_id_probe(self):
        """机器码在后台算：它要起一次 PowerShell 采硬件序列号，实测约 2 秒。

        以前是在这里同步等它，等于每次开机都白等 2 秒（还会把启动动画冻住）。
        现在先把版本号显示出来，算完再由信号回主线程补上后 8 位。
        """
        if getattr(self, "_machine_probe", None) is not None:
            return
        worker = Worker(self)
        worker.finished.connect(self._on_machine_id)
        self._machine_probe = worker          # 挂引用，别被回收

        def _work():
            try:
                from ...core import license as lic_mod

                return lic_mod.machine_code()
            except Exception:  # noqa: BLE001 —— 采不到就不显示，不影响任何功能
                return ""

        threading.Thread(target=lambda: worker.run(_work), daemon=True).start()

    def _on_machine_id(self, val):
        self._machine_id = val if isinstance(val, str) else ""
        if not self._machine_id:
            return
        try:
            self.detail_labels["version"].setText(f"v{APP_VERSION} · {self._machine_id[:8]}…")
        except Exception:  # noqa: BLE001
            pass
