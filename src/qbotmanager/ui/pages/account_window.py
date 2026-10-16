# -*- coding: utf-8 -*-
"""星群账号独立窗口：邮箱登录/退出/认领本机，默认定位屏幕左上角。

与设置页解耦：窗口内自行完成 登录 → 认领本机 → 写授权 的全流程，
任何状态变化通过 account_changed 信号通知主程序刷新。
"""
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ...core import account
from ...core import license as lic_mod
from ..hand_font import hand_qss
from ..theme import TEXT_3
from .common import Worker


class AccountWindow(QDialog):
    account_changed = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._avatar_busy = False
        self._signup_mode = False     # 当前是不是「注册」形态（只有服务端说未注册才为真）
        self._code_timer = None
        self._code_left = 0
        self.setWindowTitle("星群账号")
        self.setModal(False)
        self.setMinimumWidth(380)
        self._build_ui()
        self._refresh_status()
        self._move_top_left()
        # 打开窗口就顺手拉一次最新资料（官网刚换的头像 / 昵称立刻跟过来）。
        # 以前只有主窗口每 5 分钟对一次：用户刚在官网换完头像，回到客户端打开这里
        # 看到的还是旧的 —— 2026-10-07 用户反馈"和官网不一样"，就是卡在这个时间差。
        self._sync_profile_on_open()

    # ------------------------------------------------------------ 定位
    def _move_top_left(self):
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.left() + 12, geo.top() + 12)

    def _sync_profile_on_open(self):
        """开窗即同步：拉 /me，把昵称与头像（服务端那份，1024×1024）落到本机。"""
        if not account.load_account():
            return

        def _done(res):
            # 注意成功判据：refresh_profile 成功时返回的是「账号字典」（没有 ok 字段），
            # 失败才返回 None —— 这里写 res.get("ok") 会把成功判成失败（踩过一次）。
            if isinstance(res, dict):
                self._refresh_status()
                self.account_changed.emit()

        worker = Worker(self)
        worker.finished.connect(_done)
        threading.Thread(
            target=lambda: worker.run(lambda: account.refresh_profile()),
            daemon=True,
        ).start()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title = QLabel("星群账号")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        tip = QLabel("一个账号只能一台设备在线，新设备会踢掉旧的。")
        tip.setToolTip("邮箱登录即授权，无需激活码。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        root.addWidget(tip)
        # ---- 头像：在本机直接换，不用再去官网个人中心 ----
        head = QHBoxLayout()
        head.setSpacing(12)
        self.avatar_preview = QLabel()
        # 显示尺寸与官网个人中心对齐（官网 .me__av 是 96×96；以前这里只有 64，看着就是"比官网小一号"）
        side = int(getattr(account, "AVATAR_PREVIEW_SIDE", 96) or 96)
        self.avatar_preview.setFixedSize(side, side)
        self.avatar_preview.setAlignment(Qt.AlignCenter)
        head.addWidget(self.avatar_preview)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.avatar_name = QLabel("未登录")
        # 昵称跟官网一致，走手写体（和银行卡卡背签名同款字体）
        self.avatar_name.setStyleSheet(
            "font-weight:600;" + (hand_qss("", 16) or "font-size:14px;"))
        col.addWidget(self.avatar_name)
        self.avatar_tip = QLabel("登录后可在本机直接换头像")
        self.avatar_tip.setWordWrap(True)
        self.avatar_tip.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        col.addWidget(self.avatar_tip)
        self.btn_avatar = QPushButton("上传头像")
        self.btn_avatar.setObjectName("ghost")
        self.btn_avatar.setMinimumHeight(28)
        self.btn_avatar.setCursor(Qt.PointingHandCursor)
        self.btn_avatar.setVisible(False)
        self.btn_avatar.clicked.connect(self._pick_avatar)
        col.addWidget(self.btn_avatar, 0, Qt.AlignLeft)
        # 手动同步：官网刚换的头像/昵称，点一下立刻拉过来，不用等主窗口那 5 分钟一次
        self.btn_sync = QPushButton("同步头像")
        self.btn_sync.setObjectName("ghost")
        self.btn_sync.setMinimumHeight(28)
        self.btn_sync.setCursor(Qt.PointingHandCursor)
        self.btn_sync.setVisible(False)
        self.btn_sync.clicked.connect(self._sync_now)
        col.addWidget(self.btn_sync, 0, Qt.AlignLeft)
        head.addLayout(col, 1)
        root.addLayout(head)
        self.acct_email = QLineEdit()
        # 现在邮箱没注册的话，程序里能直接注册（2026-10-06「登录即注册」）
        self.acct_email.setPlaceholderText("邮箱")
        self.acct_email.textEdited.connect(self._on_email_edited)
        self.acct_password = QLineEdit()
        self.acct_password.setPlaceholderText("密码")
        self.acct_password.setEchoMode(QLineEdit.Password)
        self.acct_password.returnPressed.connect(self._on_account_button)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.acct_email, 1)
        row.addWidget(self.acct_password, 1)
        root.addLayout(row)

        # ---- 验证码行（只有「这个邮箱还没注册」时才出现）----
        self.code_row = QWidget()
        code_lay = QHBoxLayout(self.code_row)
        code_lay.setContentsMargins(0, 0, 0, 0)
        code_lay.setSpacing(6)
        self.acct_code = QLineEdit()
        self.acct_code.setPlaceholderText("邮箱验证码（6 位）")
        self.acct_code.setMaxLength(6)
        self.acct_code.returnPressed.connect(self._on_account_button)
        self.btn_send_code = QPushButton("发送验证码")
        self.btn_send_code.setObjectName("ghost")
        self.btn_send_code.setMinimumHeight(28)
        self.btn_send_code.setCursor(Qt.PointingHandCursor)
        self.btn_send_code.clicked.connect(self._send_signup_code)
        code_lay.addWidget(self.acct_code, 1)
        code_lay.addWidget(self.btn_send_code)
        self.code_row.setVisible(False)
        root.addWidget(self.code_row)

        self.acct_hint = QLabel("")
        self.acct_hint.setWordWrap(True)
        self.acct_hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        self.acct_hint.setVisible(False)
        root.addWidget(self.acct_hint)
        self.acct_status = QLabel("未登录")
        self.acct_status.setWordWrap(True)
        self.acct_status.setStyleSheet(f"color: {TEXT_3}; font-size: 12px;")
        root.addWidget(self.acct_status)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.btn_account = QPushButton("登录并同步")
        self.btn_account.setObjectName("primary")
        self.btn_account.setMinimumHeight(32)
        self.btn_account.clicked.connect(self._on_account_button)
        btns.addWidget(self.btn_account)
        btns.addStretch(1)
        root.addLayout(btns)

    # ------------------------------------------------------------ 状态
    def _refresh_status(self):
        acc = account.load_account()
        if acc and self._signup_mode:
            self._set_signup_mode(False)   # 已登录就不该停在注册形态
        self.btn_account.setText("退出账号" if acc else "登录并同步")
        self.acct_status.setText(f"已登录：{acc.get('email')}" if acc else "未登录")
        self._refresh_avatar()

    # ------------------------------------------------------------ 头像
    def _refresh_avatar(self):
        """刷新头像预览与昵称（登录、本机上传、官网换头像后都会走这里）。"""
        acc = account.load_account()
        try:
            from ..avatar import avatar_pixmap

            side = int(getattr(account, "AVATAR_PREVIEW_SIDE", 96) or 96)
            self.avatar_preview.setPixmap(avatar_pixmap(side, 0.0, acc))
        except Exception:  # noqa: BLE001
            self.avatar_preview.clear()
        self.avatar_name.setText(
            (account.display_name(acc) or "星群用户") if acc else "未登录")
        self.btn_avatar.setVisible(bool(acc))
        self.btn_sync.setVisible(bool(acc))
        if not acc:
            self.avatar_tip.setText("登录后可在本机直接换头像")
        elif not self._avatar_busy:
            self.avatar_tip.setText("支持 JPG / PNG / WEBP，自动裁方并压缩")

    def _sync_now(self):
        """手动同步一次：官网改完昵称/头像，点这个立刻生效。"""
        if not account.load_account():
            self.acct_status.setText("先登录，再同步")
            return
        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("同步中…")

        def _done(res):
            self.btn_sync.setEnabled(True)
            self.btn_sync.setText("同步头像")
            # 同上：成功返回账号字典（无 ok 字段），失败返回 None
            if isinstance(res, dict):
                self._refresh_status()
                self.account_changed.emit()
                self.avatar_tip.setText("已从服务器同步最新昵称与头像")
            else:
                self.avatar_tip.setText("同步失败，检查网络后再点一次")

        worker = Worker(self)
        worker.finished.connect(_done)
        threading.Thread(
            target=lambda: worker.run(lambda: account.refresh_profile()),
            daemon=True,
        ).start()

    def _pick_avatar(self):
        if self._avatar_busy:
            return
        acc = account.load_account()
        if not acc:
            self.acct_status.setText("请先登录，再上传头像")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头像图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        self._avatar_busy = True
        self.btn_avatar.setEnabled(False)
        self.btn_avatar.setText("上传中…")
        self.avatar_tip.setText("正在压缩并上传，请稍候…")
        worker = Worker(self)
        worker.finished.connect(self._on_avatar_uploaded)
        threading.Thread(
            target=lambda: worker.run(lambda: self._upload_avatar(path)),
            daemon=True,
        ).start()

    @staticmethod
    def _upload_avatar(path: str) -> dict:
        acc = account.load_account()
        if not acc:
            return {"ok": False, "detail": "登录状态丢失，请重新登录"}
        data_url, err = account.avatar_data_url(path)
        if not data_url:
            return {"ok": False, "detail": err}
        res = account.upload_avatar(str(acc["token"]), data_url)
        if isinstance(res, dict) and res.get("ok"):
            # 存服务端裁好的那张，保证客户端和官网显示完全一致
            account.save_avatar(str(res.get("avatar") or data_url))
        return res

    def _on_avatar_uploaded(self, res):
        self._avatar_busy = False
        self.btn_avatar.setEnabled(True)
        self.btn_avatar.setText("上传头像")
        if isinstance(res, dict) and res.get("ok"):
            self._refresh_avatar()
            self.avatar_tip.setText("头像已更新（本机与官网同步）")
            self.account_changed.emit()
            try:
                self.ctx.show_toast("头像已更新", 2600)
            except Exception:  # noqa: BLE001
                pass
            return
        detail = ""
        if isinstance(res, dict):
            detail = res.get("detail") or res.get("error") or res.get("reason") or ""
        if isinstance(detail, list):
            detail = "图片不符合要求"
        self.avatar_tip.setText("上传失败：" + (str(detail)[:120] or "请稍后再试"))

    # ------------------------------------------------------------ 登录 / 注册
    def _on_email_edited(self, _text):
        """改了邮箱就退回"登录"形态：免得把上一个邮箱的验证码状态带过去。"""
        if self._signup_mode:
            self._set_signup_mode(False)
            self.acct_status.setText("未登录")

    def _set_signup_mode(self, on: bool):
        """切到/退出「注册」形态：只影响界面，真正的判定在服务端。"""
        self._signup_mode = bool(on)
        self.code_row.setVisible(self._signup_mode)
        self.acct_hint.setVisible(self._signup_mode)
        if self._signup_mode:
            self.acct_hint.setText("这个邮箱还没注册，填验证码和密码即可注册（密码至少 8 位）。")
            self.btn_account.setText("注册并登录")
            self.acct_code.setFocus()
        else:
            self.btn_account.setText("登录并同步")
            self.acct_code.clear()

    def _on_account_button(self):
        if account.load_account():
            self._logout_account()
        elif self._signup_mode:
            self._register_account()
        else:
            self._login_account()

    def _login_account(self):
        email = self.acct_email.text().strip()
        pwd = self.acct_password.text()
        if not email or not pwd:
            self.acct_status.setText("请填写邮箱和密码")
            return
        self.acct_status.setText("登录中...")
        worker = Worker(self)
        worker.finished.connect(self._on_account_login)
        threading.Thread(
            target=lambda: worker.run(lambda: account.login(email, pwd)),
            daemon=True,
        ).start()

    def _on_account_login(self, res):
        if res.get("ok"):
            self._set_signup_mode(False)
            self.acct_status.setText(
                f"已登录：{res.get('email')}（{self._plan_tag(res.get('plan'))}）")
            self.btn_account.setText("退出账号")
            self._refresh_avatar()
            self._claim_device()
            return
        # 唯一的注册入口：服务端明确说「这个邮箱未注册（404）」。
        # 密码错（401）永远走这里之外的失败分支，不会误注册已注册邮箱。
        if account.needs_register(res):
            self._set_signup_mode(True)
            self.acct_status.setText("这个邮箱还没注册：填验证码和密码，点「注册并登录」。")
            return
        status = int(res.get("status") or 0)
        detail = res.get("detail") or res.get("reason") or "登录失败"
        if isinstance(detail, list):
            detail = "邮箱或密码格式不正确"
        if status == 429:
            self.acct_status.setText("试得有点多：服务端让等 15 分钟再试（换密码前先确认邮箱没写错）")
        elif int(res.get("status") or 0) == 401 or "密码错误" in str(detail):
            self.acct_status.setText("密码不对。忘了密码去官网登录页点「忘记密码」重设。")
        else:
            self.acct_status.setText("登录失败：" + str(detail))

    # ------------------------------------------------------------ 注册
    def _send_signup_code(self):
        email = self.acct_email.text().strip()
        if not email:
            self.acct_status.setText("先填邮箱，再发验证码")
            return
        self.btn_send_code.setEnabled(False)
        self.btn_send_code.setText("发送中…")
        self.acct_status.setText("正在发送验证码到 " + email + " …")

        def _done(res):
            if isinstance(res, dict) and res.get("ok"):
                self._start_code_countdown(60)
                self.acct_status.setText("验证码已发到 " + email + "（5 分钟内有效）")
                return
            detail = (res or {}).get("detail") or (res or {}).get("error") or "发送失败"
            if isinstance(detail, list):
                detail = "邮箱格式不正确"
            self.btn_send_code.setEnabled(True)
            self.btn_send_code.setText("发送验证码")
            self.acct_status.setText("验证码发送失败：" + str(detail))

        worker = Worker(self)
        worker.finished.connect(_done)
        threading.Thread(
            target=lambda: worker.run(lambda: account.send_code(email)),
            daemon=True,
        ).start()

    def _start_code_countdown(self, seconds: int):
        self._code_left = int(seconds)
        self.btn_send_code.setEnabled(False)
        self.btn_send_code.setText(f"重新发送（{self._code_left}s）")
        if self._code_timer is None:
            self._code_timer = QTimer(self)
            self._code_timer.timeout.connect(self._tick_code_countdown)
        self._code_timer.start(1000)

    def _tick_code_countdown(self):
        self._code_left -= 1
        if self._code_left <= 0:
            if self._code_timer is not None:
                self._code_timer.stop()
            self.btn_send_code.setEnabled(True)
            self.btn_send_code.setText("发送验证码")
            return
        self.btn_send_code.setText(f"重新发送（{self._code_left}s）")

    def _register_account(self):
        email = self.acct_email.text().strip()
        code = self.acct_code.text().strip()
        pwd = self.acct_password.text()
        if not email:
            self.acct_status.setText("请填写邮箱")
            return
        if not (code.isdigit() and len(code) == 6):
            self.acct_status.setText("验证码是 6 位数字，看看邮件里的那串")
            return
        if len(pwd) < 8:
            self.acct_status.setText("密码至少 8 位")
            return
        self.acct_status.setText("正在注册...")
        self.btn_account.setEnabled(False)
        worker = Worker(self)
        worker.finished.connect(self._on_account_registered)
        threading.Thread(
            target=lambda: worker.run(lambda: account.register(email, code, pwd)),
            daemon=True,
        ).start()

    def _on_account_registered(self, res):
        self.btn_account.setEnabled(True)
        if res.get("ok"):
            email = str(res.get("email") or self.acct_email.text().strip())
            self._set_signup_mode(False)
            self.acct_status.setText(f"注册成功，已登录：{email}")
            self.btn_account.setText("退出账号")
            self._refresh_avatar()
            self.account_changed.emit()
            self._claim_device()
            return
        # 服务端 409 = 这邮箱其实已经注册过（比如同事刚注册 / 两次点击）。
        # 这时只说"已注册，请直接登录"，绝不重复注册。
        status = int(res.get("status") or 0)
        detail = res.get("detail") or res.get("error") or ""
        if isinstance(detail, list):
            detail = "邮箱、验证码或密码格式不对"
        if status in (400, 409) and "已注册" in str(detail):
            self._set_signup_mode(False)
            self.acct_status.setText("这个邮箱已经注册过了，直接点「登录并同步」。")
            return
        self.acct_status.setText("注册失败：" + (str(detail) or "请稍后再试"))

    # ------------------------------------------------------------ 认领本机
    def _claim_device(self):
        acc = account.load_account()
        if not acc:
            self.acct_status.setText("登录状态丢失，请重新登录")
            return
        mid = lic_mod.machine_code()
        self.acct_status.setText("正在认领本机并同步授权...")
        worker = Worker(self)
        worker.finished.connect(self._on_claim_device)
        threading.Thread(
            target=lambda: worker.run(lambda: account.claim_device(acc["token"], mid)),
            daemon=True,
        ).start()

    def _on_claim_device(self, res):
        if res.get("ok"):
            key = res.get("license") or ""
            if key:
                act = lic_mod.activate(key)
                if not act.get("ok"):
                    self.acct_status.setText("授权生效失败：" + str(act.get("reason")))
                    return
            plan = res.get("plan")
            lic_mod.save_plan(plan, res.get("plan_expires_at") or 0,
                              res.get("owned_plugins"),
                              sig=res.get("entitlement_sig") or "",
                              machine_id=res.get("entitlement_machine") or lic_mod.machine_code())
            txt = f"已登录并绑定本机（{self._plan_tag(plan)}）"
            if res.get("kicked_machine"):
                txt += "，旧设备已下线"
            self.acct_status.setText(txt)
            self.account_changed.emit()
            # claim 的响应里不一定带权益签名，而「有没有签名」决定权益是否生效，
            # 所以认领后再按权威接口同步一次（拿不到就保持现状，不覆盖）。
            self._sync_entitlement_from_me()
            return
        detail = res.get("detail") or res.get("reason") or "认领失败"
        if isinstance(detail, list):
            detail = "认领信息有误"
        self.acct_status.setText("认领失败：" + str(detail))

    def _sync_entitlement_from_me(self):
        """认领后补一次权益同步：以 /api/account/me 为准补上签名。"""
        acc = account.load_account()
        if not acc:
            return
        worker = Worker(self)
        worker.finished.connect(self._on_me_refreshed)
        threading.Thread(
            target=lambda: worker.run(lambda: account.me(acc["token"])),
            daemon=True,
        ).start()

    def _on_me_refreshed(self, res):
        if not isinstance(res, dict) or not res.get("ok"):
            return
        sig = str(res.get("entitlement_sig") or "")
        if not sig:
            # 服务器没下发签名：保持认领时写下的那份，绝不把已有签名覆盖成空
            return
        plan = str(res.get("plan") or "none")
        lic_mod.save_plan(plan, res.get("plan_expires_at") or 0,
                          res.get("owned_plugins"), sig=sig,
                          machine_id=res.get("entitlement_machine")
                          or lic_mod.machine_code())
        self.acct_status.setText(
                f"已登录并绑定本机（{self._plan_tag(plan)}）· 权益已同步")
        self._refresh_avatar()
        self.account_changed.emit()

    @staticmethod
    def _plan_tag(plan) -> str:
        """档位说明。微信通道已免费开放（2026-10-01），这里只说账号档位本身。"""
        from ...core import plans as plans_mod

        name = str(plan or "").lower()
        if plans_mod.allows_all(name):
            return "永久档"
        if name in plans_mod.MEMBER_PLANS:
            return "月费档"
        if name == "trial":
            return "试用中"
        return "未登录 · 通道全部可用"

    # ------------------------------------------------------------ 退出
    def _logout_account(self):
        account.clear_account()
        self._set_signup_mode(False)
        self.acct_password.clear()
        self._refresh_status()
        self.account_changed.emit()
