# -*- coding: utf-8 -*-
"""飞书通道：不是占位页了 —— 凭证能填、能存、有一步步的开发者后台指引。

现状说明（写在页面上，不糊弄人）：
- 凭证与开关这里已经能配，配置会存进 `config.json`；
- **适配器还没装**（需要 nonebot 的飞书适配器），装好并重启机器人才真正连上；
- 所以状态区会明确写"凭证已填 / 还差适配器"，而不是假装已接入。
"""
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..theme import TEXT_3
from ..widgets import GlassPanel, StatusBadge
from .common import PageContext

GUIDE = (
    "① 打开 飞书开放平台（open.feishu.cn）→ 开发者后台 → 创建「企业自建应用」；\n"
    "② 在「添加应用能力」里加上「机器人」；\n"
    "③ 权限管理里至少开：获取与发送单聊/群聊消息（im:message、im:message.group_at_msg 等）；\n"
    "④ 事件订阅选「使用长连接接收事件」（不用公网地址、不用配 webhook），订阅「接收消息」事件；\n"
    "⑤ 把这里的 App ID / App Secret（事件订阅页还会给 Encrypt Key 与 Verification Token）填进来；\n"
    "⑥ 回到开发者后台「版本管理与发布」创建版本并发布，等企业管理员通过后机器人才能在群里说话。"
)

NOT_INSTALLED_TIP = (
    "适配器（nonebot-adapter-feishu）没装时点上面的「装适配器」，装完重启机器人即可生效；"
    "装它需要联网。"
)


class FeishuPage(QWidget):
    def __init__(self, ctx: PageContext):
        super().__init__()
        self.ctx = ctx
        self._build_ui()
        self.refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("飞书")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.badge = StatusBadge("飞书", "stopped")
        head.addWidget(self.badge)
        outer.addLayout(head)

        panel = GlassPanel()
        form = QFormLayout(panel)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)
        self.chk_enabled = QCheckBox("启用飞书通道（凭证齐全并装好适配器后生效）")
        form.addRow("", self.chk_enabled)
        self.edit_app_id = QLineEdit()
        self.edit_app_id.setPlaceholderText("cli_ 开头的那串")
        form.addRow("App ID", self.edit_app_id)
        self.edit_secret = QLineEdit()
        self.edit_secret.setEchoMode(QLineEdit.Password)
        self.edit_secret.setPlaceholderText("App Secret（只存在本机配置里）")
        form.addRow("App Secret", self.edit_secret)
        self.edit_encrypt = QLineEdit()
        self.edit_encrypt.setPlaceholderText("事件订阅页的 Encrypt Key（可留空）")
        form.addRow("Encrypt Key", self.edit_encrypt)
        self.edit_token = QLineEdit()
        self.edit_token.setPlaceholderText("Verification Token，可留空")
        form.addRow("Verification Token", self.edit_token)
        save_row = QHBoxLayout()
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save)
        save_row.addWidget(self.btn_save)
        self.btn_install = QPushButton("装适配器")
        self.btn_install.setToolTip("需要联网，装完重启机器人生效")
        self.btn_install.clicked.connect(self._install_adapter)
        save_row.addWidget(self.btn_install)
        self.save_hint = QLabel("")
        self.save_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        save_row.addWidget(self.save_hint, 1)
        form.addRow("", save_row)
        outer.addWidget(panel)

        guide = GlassPanel()
        gv = QVBoxLayout(guide)
        gv.setContentsMargins(18, 16, 18, 16)
        gl = QLabel("怎么接（照顺序做就行）")
        gl.setObjectName("sectionTitle")
        gv.addWidget(gl)
        gtext = QLabel(GUIDE)
        gtext.setWordWrap(True)
        gv.addWidget(gtext)
        tip = QLabel(NOT_INSTALLED_TIP)
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        gv.addWidget(tip)
        outer.addWidget(guide)
        outer.addStretch(1)

    # ------------------------------------------------------------ 数据
    def refresh_status(self):
        s = self.ctx.settings
        self.chk_enabled.setChecked(bool(getattr(s, "feishu_enabled", False)))
        self.edit_app_id.setText(str(getattr(s, "feishu_app_id", "") or ""))
        self.edit_secret.setText(str(getattr(s, "feishu_app_secret", "") or ""))
        self.edit_encrypt.setText(str(getattr(s, "feishu_encrypt_key", "") or ""))
        self.edit_token.setText(str(getattr(s, "feishu_verification_token", "") or ""))
        filled = bool(self.edit_app_id.text().strip() and self.edit_secret.text().strip())
        if not filled:
            self.badge.set_status("stopped", "飞书 · 还没填凭证")
        elif not self.chk_enabled.isChecked():
            self.badge.set_status("pending", "飞书 · 凭证已填，开关没开")
        elif self._adapter_installed():
            self.badge.set_status("running", "飞书 · 就绪（重启机器人后连上）")
        else:
            self.badge.set_status("pending", "飞书 · 凭证就绪，还差适配器")
        if hasattr(self, "btn_install"):
            self.btn_install.setEnabled(filled and not self._adapter_installed())

    def _adapter_installed(self) -> bool:
        try:
            from ...core import bot as bot_mod

            return bool(bot_mod.feishu_adapter_installed(self.ctx.settings))
        except Exception:  # noqa: BLE001
            return False

    def _install_adapter(self):
        from ...core import bot as bot_mod

        self.save_hint.setText("正在安装飞书适配器…（要联网，约十几秒）")
        try:
            self._save()
            ok = bot_mod.ensure_feishu_deps(
                self.ctx.settings,
                log=lambda line: self.save_hint.setText(str(line)))
        except Exception as e:  # noqa: BLE001
            self.save_hint.setText(f"安装失败：{e}")
            return
        self.save_hint.setText("适配器装好了，重启机器人后生效" if ok else "适配器没装上，检查网络后重试")
        self.refresh_status()

    def _save(self):
        s = self.ctx.settings
        s.feishu_enabled = bool(self.chk_enabled.isChecked())
        s.feishu_app_id = self.edit_app_id.text().strip()
        s.feishu_app_secret = self.edit_secret.text().strip()
        s.feishu_encrypt_key = self.edit_encrypt.text().strip()
        s.feishu_verification_token = self.edit_token.text().strip()
        try:
            s.save()
        except Exception as e:  # noqa: BLE001
            self.save_hint.setText(f"保存失败：{e}")
            return
        self.save_hint.setText("已保存（重启机器人后生效）")
        self.refresh_status()
