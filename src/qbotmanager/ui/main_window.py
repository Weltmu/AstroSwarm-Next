"""主窗口：B 样式无边框窗口 + 侧边栏 + 统一任务系统接线 + 动态视频背景。

后台任务全部走 TaskManager（Worker -> Signal -> TaskManager -> UI），
主窗口只负责接线与展示，不直接执行任何耗时操作。
"""
import ctypes
import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QObject, QPropertyAnimation, QRect, QRectF, QSize, QTimer, Qt, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QGuiApplication, QIcon, QLinearGradient, QPainter,
    QPixmap,
    QPainterPath, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from ..constants import APP_VERSION
from ..core import autostart as autostart_mod
from ..core.manager import Manager
from ..core.video_tools import find_default_background
from ..tasks.workers import StartStopTask
from . import motion
from . import theme as theme_mod
from .hand_font import hand_qss
from .icon_font import make_icon
from .image_background import ImageBackground
from .pages.common import PageContext, Worker
from .pages.access_page import AccessPage
from .pages.brain_page import BrainPage
from .pages.deps_page import DepsPage
from .pages.feishu_page import FeishuPage
from .pages.home_page import HomePage
from .pages.logs_page import LogsPage
from .pages.message_center_page import MessageCenterPage
from .pages.plugins_page import PluginsPage
from .pages.run_page import RunPage
from .pages.services_page import ServicesPage
from .pages.settings_page import SettingsPage
from .pages.telegram_page import TelegramPage
from .pages.wechat_page import WechatPage
from .pages.workshop_page import WorkshopPage
from .theme import build_qss
from .video_background import VideoBackground

logger = logging.getLogger("qbotmanager")


# 账号资料（昵称 / 头像）自动同步的节拍：官网或个人中心改完，客户端 60 秒内自己跟上。
# 只在窗口处于前台时走这个表（见 _profile_sync_allowed）——最小化/切走了不空转。
ACCOUNT_PROFILE_POLL_MS = 60 * 1000


# 侧栏 / 顶栏 / 图标栏共用的图标表：一个名字只对应一个语义，互不重复。
# 「接入」原来写的是 plugs-connected —— 图标字体里没有这个字形，渲染出来是空白，
# 这里改成确实存在的字形（arrow-right = 进入通道配置）。
NAV_ICONS = {
    "首页": "home",
    "接入": "arrow-right",
    "微信": "chat-circle-dots",
    "QQ": "chat-circle",
    "飞书": "paper-plane-tilt",
    "纸飞机": "paper-plane",
    "AI 大脑": "brain",
    "插件": "puzzle-piece",
    "插件工坊": "magic-wand",
    "全局管理": "robot",
    "消息中心": "chat-centered",
    "日志": "terminal-window",
    "依赖": "package",
    "设置": "gear-six",
}


class LogBus(QObject):
    """统一日志总线：Manager / 任务 / 外观等来源都汇聚到这里，供日志页订阅。"""
    entry = Signal(str, str, str)   # source, level, msg

    def emit_entry(self, source, level, msg):
        self.entry.emit(source, level, str(msg))


def _screen_dpr() -> float:
    """屏幕真实缩放比（100% 缩放=1.0，150%=1.5）。

    头像这类自绘位图必须按它算尺寸，写死 2.0 的话：在 100% 缩放的屏幕上会先按
    2 倍画、再被 Qt 缩回一半显示 —— 画两遍，看着就糊。
    """
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        scr = app.primaryScreen() if app is not None else None
        if scr is not None:
            dpr = float(scr.devicePixelRatio())
            if dpr > 0:
                return dpr
    except Exception:  # noqa: BLE001
        pass
    return 2.0


class UpdaterSignals(QObject):
    """更新线程 → 界面的信号桥：下载进度 / 完成 / 失败。

    下载跑在后台线程，Qt 控件只能在主线程碰 —— 所有跨线程回调用信号转交。
    """
    progress = Signal(int, int)   # 已下载字节, 总字节
    done = Signal(str)            # 安装包本地路径
    failed = Signal(str)          # 失败原因（给用户看）


class ResizeHandle(QWidget):
    """无边框窗口边缘拖拽手柄：按下并移动时调整 MainWindow 大小。"""

    def __init__(self, win, edge, cursor, parent=None):
        super().__init__(parent or win)
        self._win = win
        self._edge = edge
        self.setCursor(cursor)
        self.setMouseTracking(True)
        self._pressed = False
        self._start_geom = None
        self._start_gpos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._win.isMaximized():
            self._pressed = True
            self._start_geom = self._win.geometry()
            self._start_gpos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._pressed and self._start_geom is not None:
            self._win._apply_resize_edge(
                self._edge, self._start_geom, self._start_gpos,
                event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event):
        self._pressed = False
        event.accept()


class TitleBar(QFrame):
    """自定义标题栏：拖拽移动窗口、双击最大化、最小化/最大化/关闭按钮。"""

    def __init__(self, win, title="星巢 Nest", subtitle=""):
        super().__init__()
        self.setObjectName("titlebar")
        self.setFixedHeight(44)
        self._win = win
        self._drag_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 0, 0)
        lay.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("appTitle")
        self.sub = QLabel(subtitle)
        self.sub.setObjectName("appSub")
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        # 更新入口（版本号右边，标题栏左半区）：只在发现新版本时出现，
        # 红色胶囊写「有新版本」，点一下就在软件里下好、装好、重启，
        # 不用再去官网重新下载；没有新版本时整块隐藏，不占视线。
        self.btn_update = QPushButton("有新版本")
        self.btn_update.setObjectName("winUpdate")
        self.btn_update.setProperty("class", "winbtn")
        self.btn_update.setFixedHeight(26)
        self.btn_update.setMinimumWidth(84)
        self.btn_update.setFocusPolicy(Qt.NoFocus)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.setToolTip("发现新版本，点击在软件里直接更新")
        self.btn_update.clicked.connect(
            lambda: getattr(win, "check_update_interactive", lambda: None)())
        self.btn_update.setVisible(False)
        lay.addWidget(self.btn_update, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        for text, obj_name, handler in (
            ("—", "winMin", win.showMinimized),
            ("□", "winMax", win.toggle_max),
            ("✕", "winClose", win.close),
        ):
            btn = QPushButton(text)
            btn.setObjectName(obj_name)
            btn.setProperty("class", "winbtn")
            btn.setFixedSize(46, 36)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(handler)
            lay.addWidget(btn)

    # ---- 拖拽移动 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and (event.buttons() & Qt.LeftButton):
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._win.toggle_max()


def _hard_exit(code: int = 0) -> None:
    """立刻结束进程，不跑 Python / Qt 收尾。

    Windows：为什么不用 os._exit —— 在 Qt 定时器回调里调它会 access violation
    （faulthandler 实测，退出码 0xC0000005，屏幕上看不出区别但会留下崩溃记录）。
    TerminateProcess 从回调里调是安全的，退出码可控；
    机器人等子进程在 Windows 作业对象里（KILL_ON_JOB_CLOSE），会跟着一起回收。

    Linux（2026-10-11 同步）：`kernel32.dll` 不存在，必须走 `os._exit(0)`；
    那边没有 Windows 的 access violation 问题，无头端/桌面端都是安全的。
    """
    if not sys.platform.startswith("win"):
        try:
            os._exit(int(code))
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32.dll")
        # 必须声明类型：默认 restype 是 32 位 int，会把 64 位的进程句柄截断，
        # TerminateProcess 于是静默失败（返回 0、不抛异常）—— 实测踩过。
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        if k32.TerminateProcess(k32.GetCurrentProcess(), int(code)):
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        os._exit(int(code))       # 兜底：至少把进程带走
    except Exception:  # noqa: BLE001
        pass



def _dbg(msg: str) -> None:
    r"""诊断日志：转发到 splash 的调试日志（QBM_SPLASH_DEBUG=1 时写到 %TEMP%\splash_dbg.log）。

    2026-10-10 踩的坑：托盘初始化里直接用了 `_dbg` 但本模块没定义（只在 closeEvent 里
    局部 import 过）→ NameError → 启动即崩，而打包版没有控制台，现场只看到"启动卡卡住、
    后面有个报错框被盖住"。
    """
    try:
        from .splash import _dbg as _splash_dbg

        _splash_dbg(msg)
    except Exception:  # noqa: BLE001
        pass

class MainWindow(QWidget):
    account_kicked = Signal(str)
    account_profile_changed = Signal()

    # 侧栏分组与控制台（无头端）的 NAV_GROUPS 一一对应：机器人 / 工坊 / 人格 / 设置。
    # 2026-10-02 按老板拍板的"极致简化"合并分组；**但入口不藏**：
    # 第一版把「插件」收进工坊组里，老板当场找不到（"插件导航按钮没了"），
    # 所以现在只按概念分组，每一项依旧各占一行（通道详情页除外，它们本来就在「接入」里）。
    NAV_GROUPS = (
        ("机器人", ("首页", "接入")),
        ("工坊", ("插件", "插件工坊")),
        ("人格", ("AI 大脑",)),
        ("设置", ("全局管理", "消息中心", "日志", "依赖", "设置")),
    )
    # 收进父页的页面 → 归属的侧栏项（不单独占一行，但页面还在 stack 里，下标不变）
    SUB_NAV_PARENT = {"微信": "接入", "QQ": "接入", "飞书": "接入", "纸飞机": "接入"}
    # 页面索引顺序：必须与 self.pages 顺序一一对应，与控制台一致（新增页插在该在的位置）。
    # 通道详情页排在「接入」后面，位置与旧版完全一致 —— 老的页面下标映射不用迁移。
    PAGE_ORDER = ("首页", "接入", "微信", "QQ", "飞书", "纸飞机",
                  "AI 大脑", "插件", "插件工坊",
                  "全局管理", "消息中心", "日志", "依赖", "设置")
    # 小白模式（默认开启）下隐藏这些入口，可在「设置」里关掉
    ADVANCED_PAGES = ("微信", "QQ", "飞书", "纸飞机", "消息中心", "插件", "日志", "依赖")
    SEPARATOR = "— 全局管理 —"
    RESIZE_MARGIN = 8          # 窗口边缘可拖拽区宽度（px）
    MIN_WINDOW_W = 960         # 窗口最小宽
    MIN_WINDOW_H = 640         # 窗口最小高

    def __init__(self, settings, tasks):
        super().__init__()
        self.settings = settings
        self.tasks = tasks
        # 界面动效总闸：设置 →「减少界面动效」勾上就不做过渡（状态信息照旧）
        motion.set_enabled(bool(getattr(settings, "animations_enabled", True)))
        self._closing = False
        # 账号资料同步：一次只跑一个（焦点来回切时不会攒出一堆并列请求）
        self._profile_pull_busy = False
        # 同步导航高亮时禁止回调再跳页：通道详情页没有自己的行/图标，
        # 高亮归属的「接入」会触发 currentRowChanged，不拦就会被拽回接入页
        self._nav_syncing = False
        self.setMinimumSize(self.MIN_WINDOW_W, self.MIN_WINDOW_H)
        self._exit_stop_tid = None
        self._exit_poll = None
        self._exit_dialog = None
        self._exit_card = None      # 关闭动画卡片（splash.ClosingCard）
        self._tray = None           # 系统托盘图标（最小化时缩到这里）
        self._tray_hint_shown = False

        self.setWindowTitle("星巢 Nest")
        self.setMinimumSize(1080, 700)
        self.resize(1280, 800)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        # 透明窗口 + 抗锯齿圆角绘制：区域遮罩（setMask）有锯齿，改用透明合成
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 统一日志总线：Manager 实时日志 + 任务日志
        self.log_bus = LogBus(self)
        self.manager = Manager(
            settings,
            on_log=lambda line: self.log_bus.emit_entry("管理器", "INFO", line),
        )
        self.tasks.log.connect(
            lambda tid, lv, msg: self.log_bus.emit_entry("任务", lv, msg))

        # 背景层（先添加 = 位于底层）
        self.img_bg = ImageBackground(self)
        self.img_bg.setVisible(False)
        self.bg = VideoBackground(self)
        self.bg.loadFailed.connect(self._on_bg_load_failed)
        self.bg.fallbackNotice.connect(self._on_bg_fallback)
        self.bg.playbackRequested.connect(self._on_bg_playback_requested)
        self._applied_skin = None
        self._account_buttons = []
        self._account_window = None
        # 站内更新：检查结果 / 已下好的安装包 / 更新过程中正在使用的资源
        self._update_info = {}
        self._update_checking = False
        self._update_ready = None
        self._pending_update = None
        self._update_dialog = None
        self._update_bar = None
        self._update_tip = None
        self._update_card = None      # 更新进度用的点阵卡片（splash.UpdateCard）
        self._update_sig = None
        self._update_cancel_ev = None
        self._update_cancel_btn = None
        self._update_close_btn = None
        self._update_busy = False
        self._update_last_ui = 0.0

        self._build_ui()
        self._refresh_account_buttons()
        self._create_resize_handles()
        self.apply_appearance()
        self._center_on_screen()

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(2000)
        self.watch_timer.timeout.connect(self._on_tick)
        self.watch_timer.start()
        # 启动后自动检查新版本（后台线程，不阻塞界面）
        self._update_prompted = False
        QTimer.singleShot(2500, self._start_update_check)
        self._set_update_button_state()
        # 启动那次之外，每 24 小时再自动查一次（有新版就把标题栏按钮点亮）
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(30 * 60 * 1000)   # 30 分钟一次（原来 24 小时）
        self._update_timer.timeout.connect(self._start_update_check)
        self._update_timer.start()
        # 星群账号激活状态：登录后每小时自动同步一次
        self._account_sync_timer = QTimer(self)
        self._account_sync_timer.setInterval(3600 * 1000)
        self._account_sync_timer.timeout.connect(self._hourly_account_sync)
        self._account_sync_timer.start()
        QTimer.singleShot(10000, self._hourly_account_sync)
        # 账号一设备一登录：每 15 分钟检测本机是否已被其他设备顶下线
        self.account_kicked.connect(self._on_account_kicked)
        self._device_check_timer = QTimer(self)
        self._device_check_timer.setInterval(15 * 60 * 1000)
        self._device_check_timer.timeout.connect(self._account_device_check)
        self._device_check_timer.start()
        QTimer.singleShot(5000, self._account_device_check)
        # 昵称/头像官网本机都能改：启动后拉一次，之后每 60 秒对一次；
        # 窗口重新获得焦点时也立刻对一次（刚从浏览器个人中心切回来就能看到新头像）。
        # 走 _pull_account_profile 里的"只在前台跑"判断，后台/最小化时一次都不打。
        self.account_profile_changed.connect(self._on_profile_synced)
        self._account_profile_timer = QTimer(self)
        self._account_profile_timer.setInterval(ACCOUNT_PROFILE_POLL_MS)
        self._account_profile_timer.timeout.connect(self._pull_account_profile)
        self._account_profile_timer.start()
        QTimer.singleShot(4000, lambda: self._pull_account_profile(force=True))
        QTimer.singleShot(800, self._maybe_auto_start)

    def _hourly_account_sync(self):
        """后台自动同步激活状态到星群账号（每小时一次）。"""
        import threading

        from ..core import account, license as lic_mod
        acc = account.load_account()
        if not acc:
            return
        st = lic_mod.status()
        if not st.get("activated"):
            return

        def _sync():
            try:
                data = account.sync(
                    acc["token"], st["machine_id"], st.get("key", ""),
                    st.get("expires_at") or 0)
                if data.get("ok"):
                    # 同步服务器 plan / 已登记插件，保持插件商店权益最新
                    # 没签名就不写：这次同步的响应不带 entitlement_sig 时，
                    # 原来的写法会把本机已生效的权益抹成空签名（当场掉回未开通）
                    lic_mod.save_entitlements_from_account(data)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_sync, daemon=True).start()

    def _account_device_check(self):
        """检测本机是否仍是账号活跃设备；被顶下线则清空本机授权。"""
        import threading

        from ..core import account, license as lic_mod
        acc = account.load_account()
        if not acc:
            return

        def _check():
            try:
                # 机器码要起一次 PowerShell 采硬件序列号（约 2 秒）：原来在启动主线程算，
                # 每次开机白等 2 秒还会把启动动画冻住；这里只在后台线程用得到，挪进来算。
                mid = lic_mod.machine_code()
                data = account.check_device(acc["token"], mid)
                if data.get("ok") and data.get("active") is False:
                    lic_mod.clear_license()
                    account.clear_account()
                    self.account_kicked.emit("该账号已在其他设备登录，本机已下线")
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_check, daemon=True).start()

    def _on_account_kicked(self, msg):
        self.show_toast(msg, 4000)
        for page in self.pages:
            if hasattr(page, "_refresh_account_status"):
                try:
                    page._refresh_account_status()
                except Exception:  # noqa: BLE001
                    pass
        self._refresh_account_buttons()

    # ------------------------------------------------------------ 左上角账号入口
    # 官网个人中心：昵称、邮箱、绑定设备在这里改；头像本机「设置 → 账号」也能传
    ACCOUNT_URL = "https://astroswarm.cn/account.html"

    def _make_account_button(self):
        """侧边栏左上角账号入口：圆形头像 + 昵称（没登录显示默认头像 + 待登录）。"""
        btn = QPushButton()
        btn.setObjectName("ghost")
        btn.setProperty("class", "accountPill")
        btn.setCursor(Qt.PointingHandCursor)
        # 头像尺寸必须和「设置 → 账号」窗口里那张一致（都取 AVATAR_PREVIEW_SIDE=96，
        # 与官网个人中心 .me__av 的 96px 对齐）。以前这里是 36：同一张图缩到 36 后
        # 脸小到看不清，跟官网一比就像"取景不一样"（2026-10-07 用户连着反馈三次）。
        # 侧栏头像用 AVATAR_SIDEBAR_SIDE（44），不是账号窗口那个 96：
        # 侧栏固定 180 宽，96 的圆放进去比例失调、会把导航整列顶下去
        # （2026-10-07 用户反馈"头像咋还变大了"）。96 只用在账号窗口里。
        from ..core import account as account_mod

        side = int(getattr(account_mod, "AVATAR_SIDEBAR_SIDE", 44) or 44)
        btn.setIconSize(QSize(side, side))
        # 按钮必须比图标高出一圈内边距，否则 Qt 会把图标上下裁掉一条
        # （2026-10-07 用户连续投诉"头像缺一块"：按钮 82px、图标 64 + 内边距 20 = 需要 84px，
        #  差 2px 就把发梢和下巴切掉了）。+34 留足余量，并且竖直方向不许被布局压扁。
        btn.setMinimumHeight(side + 34)
        btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # 宽度必须锁死在侧栏可用宽里：侧栏固定 180、左右各 10 边距 = 可用 160。
        # 不锁的话昵称一长（或某些字号/字体组合下）按钮会被撑到 250+ 冲出侧栏 ——
        # 2026-10-07 交付前自检在 1180×720 下抓到的就是这个（按钮 256 > 侧栏 180）。
        # 锁死宽度后 Qt 会自动省略号截断文字，头像尺寸不受影响。
        btn.setFixedWidth(max(120, 180 - 20))
        btn.setText("")          # 文本由 _fill_account_button 填，这里先清空避免影响 sizeHint
        btn.clicked.connect(self._open_account_center)
        self._account_buttons.append(btn)
        self._fill_account_button(btn)
        return btn

    def _account_snapshot(self):
        """读一次本地账号；未登录 / 文件损坏一律当没登录。"""
        try:
            from ..core import account
            return account.load_account()
        except Exception:  # noqa: BLE001
            return None

    def _fill_account_button(self, btn, acc=None):
        """把头像 + 昵称贴到按钮上（已登录 / 未登录的头像亮度不同）。"""
        from ..core import account
        from .avatar import avatar_pixmap
        if acc is None:
            acc = self._account_snapshot()
        if acc:
            name = account.display_name(acc) or "星群用户"
            tip = ("星群账号 · %s\n点击打开官网个人中心（改昵称 / 绑定设备）\n"
                   "换头像：本机「设置 → 账号」里可以直接上传") % (
                str(acc.get("email") or name))
        else:
            name = "待登录"
            tip = "未登录 · 点击用配对码绑定这台电脑（也可用邮箱密码登录）"
        try:
            # 整幅缩进圆 = 与官网同构图（官网 object-fit:cover 正方形图 → 内切圆）。
            # 圆大小不改变可见画面的比例，任何额外放大/位移都会把"和官网不一样"重现
            # （2026-10-07 像素实测：加 zoom≈1.19 + 上移 5px 时与官网圆差异 31.7，
            #   去掉后 2.6，即逐像素一致；不要再加 zoom / shift_y）。
            side = int(getattr(account, "AVATAR_SIDEBAR_SIDE", 44) or 44)
            btn.setIcon(QIcon(avatar_pixmap(side, _screen_dpr(), acc)))
        except Exception as e:  # noqa: BLE001
            # 兜底也要留痕：以前这里静默吞异常，结果头像变成齿轮图标还查不出原因
            # （2026-10-07 踩过：account_mod 写错导致 NameError 被吃掉）
            logger.warning("账号头像加载失败，退回占位图标: %s", e)
            btn.setIcon(make_icon("gear", "#A7B1C2", 16))
        btn.setText(name)
        # 昵称的手写体样式写在主题里（QPushButton[class="accountPill"] 那一条）。
        # 这里既不能 setFont（会被主题里的 `* { font-family }` 压掉），
        # 也不能给按钮挂组件级样式表（Qt 的气泡是按钮的子控件，会把气泡字体一起带跑，
        # 2026-10-06 用户反馈"气泡字看不清"就是这个原因）。
        btn.setToolTip(tip)

    def _refresh_account_buttons(self):
        """刷新左上角账号按钮的头像与昵称（登录/退出、官网改名后都会走这里）。"""
        acc = self._account_snapshot()
        for btn in self._account_buttons:
            self._fill_account_button(btn, acc)

    def _open_account_center(self):
        """点左上角头像：已登录 -> 开官网个人中心；未登录 -> 配对码绑定这台电脑。

        2026-10-01 用户反馈：「网站已经登录账号了，客户端点登录绑不上」——
        原来只开网页，网页登录和客户端之间没有回传通道。现在走官方配对码：
        客户端申请 6 位码 -> 浏览器打开（已登录的直接输码）-> 客户端轮询拿 token。
        """
        acc = self._account_snapshot()
        if acc and acc.get("token"):
            QDesktopServices.openUrl(QUrl(self.ACCOUNT_URL))
            self.show_toast("已打开官网个人中心；本机「设置 → 账号」也能直接换头像", 3200)
            return
        self._start_device_pairing()

    def _start_device_pairing(self):
        """未登录时的绑定流程：配对码 + 官网授权，成功后本机自动登录。"""
        import threading

        from ..core import account
        from ..core import license as lic_mod

        if getattr(self, "_pair_dialog", None) is not None:
            self._pair_dialog.raise_()
            self._pair_dialog.activateWindow()
            return

        try:
            machine_id = (lic_mod.machine_code() or "").strip()
        except Exception:  # noqa: BLE001
            machine_id = ""
        if len(machine_id) < 8:
            self.show_toast("读不到本机机器码，请改用邮箱密码登录", 3600)
            self._open_account_window()
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("绑定星群账号")
        dlg.setModal(False)
        dlg.setMinimumWidth(400)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)

        tip = QLabel("把下面这串配对码填进官网个人中心即可登录。")
        tip.setToolTip("浏览器已经帮你打开了官网个人中心。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        code_lab = QLabel("…")
        code_lab.setAlignment(Qt.AlignCenter)
        code_lab.setStyleSheet("font-size: 30px; font-weight: 700; letter-spacing: 8px;")
        lay.addWidget(code_lab)

        status = QLabel("正在获取配对码…")
        status.setAlignment(Qt.AlignCenter)
        lay.addWidget(status)

        btns = QHBoxLayout()
        btn_site = QPushButton("打开网页")
        btn_mail = QPushButton("用邮箱密码登录")
        btn_cancel = QPushButton("取消")
        btns.addWidget(btn_site)
        btns.addWidget(btn_mail)
        btns.addWidget(btn_cancel)
        lay.addLayout(btns)

        state = {"code": "", "poll_token": "", "busy": False, "done": False,
                 "dead": False, "left": 0, "workers": []}
        timer = QTimer(dlg)
        timer.setInterval(2000)

        def open_site():
            url = self.ACCOUNT_URL + ("?pair=" + state["code"] if state["code"] else "")
            QDesktopServices.openUrl(QUrl(url))

        def begin():
            state.update(code="", poll_token="", busy=False, done=False,
                         dead=False, left=0)
            code_lab.setText("…")
            status.setText("正在获取配对码…")
            worker = Worker(self)
            state["workers"].append(worker)
            worker.finished.connect(on_code)
            threading.Thread(
                target=lambda: worker.run(lambda: account.device_pair_request(machine_id)),
                daemon=True,
            ).start()

        def on_code(res):
            if state["done"]:
                return
            if not res.get("ok"):
                state["dead"] = True
                status.setText("获取配对码失败：" + str(res.get("detail") or "网络异常"))
                return
            state["code"] = str(res.get("code") or "")
            state["poll_token"] = str(res.get("poll_token") or "")
            state["left"] = int(res.get("expires_in") or 600)
            code_lab.setText(state["code"])
            status.setText("等待网页确认…（10 分钟内有效）")
            timer.start()
            open_site()

        def poll():
            if state["done"] or state["dead"] or state["busy"] or not state["poll_token"]:
                return
            state["busy"] = True
            state["left"] -= 2
            token = state["poll_token"]
            worker = Worker(self)
            state["workers"].append(worker)
            worker.finished.connect(on_poll)
            threading.Thread(
                target=lambda: worker.run(lambda: account.device_pair_poll(token)),
                daemon=True,
            ).start()

        def on_poll(res):
            state["busy"] = False
            if state["done"] or state["dead"]:
                return
            if res.get("ok") and str(res.get("status")) == "approved":
                timer.stop()
                self._finish_device_pairing(res, dlg)
                return
            if str(res.get("status")) in ("expired", "used") or state["left"] <= 0:
                timer.stop()
                state["dead"] = True
                code_lab.setText("——")
                status.setText("配对码已失效，点「打开网页」重新获取")
                return
            if not res.get("ok") and not res.get("status"):
                status.setText("网络不佳，正在重试…")

        def on_site():
            if state["done"]:
                return
            if state["dead"]:
                begin()
            else:
                open_site()

        timer.timeout.connect(poll)
        btn_site.clicked.connect(on_site)
        btn_mail.clicked.connect(lambda: (dlg.reject(), self._open_account_window()))
        btn_cancel.clicked.connect(dlg.reject)

        def on_closed(*_):
            timer.stop()
            state["done"] = True
            if getattr(self, "_pair_dialog", None) is dlg:
                self._pair_dialog = None

        dlg.finished.connect(on_closed)
        self._pair_dialog = dlg
        dlg.show()
        begin()

    def _finish_device_pairing(self, res, dlg=None):
        """配对成功：落盘账号 -> 拉权益 -> 刷新界面；失败只提示，不打断别的流程。"""
        import threading

        from ..core import account
        from ..core import license as lic_mod

        email = str(res.get("email") or "")
        token = str(res.get("token") or "")
        if not token:
            self.show_toast("绑定失败：服务器没有返回登录信息", 3600)
            return
        account.save_account({"email": email, "token": token, "login_at": time.time()})
        account._apply_profile(res)

        def _claim():
            try:
                mid = lic_mod.machine_code()
                data = account.claim_device(token, mid)
                if data.get("ok"):
                    key = data.get("license") or ""
                    if key:
                        lic_mod.activate(key)
                    lic_mod.save_plan(
                        data.get("plan"), data.get("plan_expires_at") or 0,
                        data.get("owned_plugins"),
                        sig=data.get("entitlement_sig") or "",
                        machine_id=data.get("entitlement_machine") or mid)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_claim, daemon=True).start()
        try:
            if dlg is not None:
                dlg.accept()
        except Exception:  # noqa: BLE001
            pass
        self._pair_dialog = None
        self._on_account_changed()
        self.show_toast("绑定成功，已登录：" + (email or "星群账号"), 3600)

    def _on_profile_synced(self):
        """官网 / 个人中心改了昵称或头像：把界面上所有显示账号的地方一起刷一遍。

        侧栏与顶栏的账号胶囊（_refresh_account_buttons）、各页面里的账号区
        （设置页那句「已登录：…」）、以及个人中心窗口里的头像预览 —— 一处都不留旧的。
        avatar_pixmap 每次都重新读盘（没有 QPixmap 缓存），所以头像文件换了这里拿到就是新的。
        """
        self._refresh_account_buttons()
        for page in self.pages:
            if hasattr(page, "_refresh_account_status"):
                try:
                    page._refresh_account_status()
                except Exception:  # noqa: BLE001
                    pass
        win = getattr(self, "_account_window", None)
        if win is not None:
            try:
                win._refresh_avatar()
            except Exception:  # noqa: BLE001
                pass

    def _profile_sync_allowed(self):
        """现在该不该去对一次账号资料：只在窗口处于前台时跑。

        最小化、藏起来、切去别的程序 —— 一律不打请求（后台空转没意义）。
        判断本身出错就当允许，宁可多对一次。
        """
        try:
            if self._closing or self.isMinimized() or not self.isVisible():
                return False
            return bool(self.isActiveWindow())
        except Exception:  # noqa: BLE001
            return True

    def _pull_account_profile(self, force: bool = False):
        """后台拉一次昵称/头像：官网或个人中心改的，这边自动跟上。

        force=True 只给启动那一次用（窗口可能还没拿到焦点，但该读一次）。
        没登录、窗口不在前台 —— 直接返回，一个请求都不发。
        拉失败一律静默：界面上还是上一次的昵称与头像，不弹错、不阻塞。
        """
        import threading

        from ..core import account
        try:
            acc = account.load_account()
        except Exception:  # noqa: BLE001
            return
        if not acc or not str(acc.get("token") or ""):
            return                       # 未登录 / 离线：不发请求
        if not force and not self._profile_sync_allowed():
            return
        if getattr(self, "_profile_pull_busy", False):
            return                       # 上一次还没回来就别再叠一个

        self._profile_pull_busy = True

        def _run():
            try:
                if account.refresh_profile(acc):
                    self.account_profile_changed.emit()
            except Exception:  # noqa: BLE001 —— 网络不通只当没读到，绝不弹错
                pass
            finally:
                self._profile_pull_busy = False

        threading.Thread(target=_run, daemon=True).start()

    def _open_account_window(self):
        """打开个人中心（星群账号）独立窗口，默认贴屏幕左上角。"""
        if getattr(self, "_account_window", None) is None:
            from .pages.account_window import AccountWindow
            self._account_window = AccountWindow(self._page_ctx, parent=self)
            self._account_window.account_changed.connect(self._on_account_changed)
        win = self._account_window
        win._refresh_status()
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_account_changed(self):
        """账号登录/退出后：刷新左上角按钮与各页面状态。"""
        self._refresh_account_buttons()
        for page in self.pages:
            if hasattr(page, "_refresh_account_status"):
                try:
                    page._refresh_account_status()
                except Exception:  # noqa: BLE001
                    pass
            if hasattr(page, "_fill_license_status"):
                try:
                    page._fill_license_status()
                except Exception:  # noqa: BLE001
                    pass

    def _maybe_auto_start(self):
        """按设置自动处理：开机自启动注册表补写 + 启动程序时自动启动全部服务。"""
        if self._closing:
            return
        try:
            if getattr(self.settings, "auto_launch_on_boot", False):
                autostart_mod.set_enabled(True)
            if not getattr(self.settings, "auto_start_services", False):
                return
            if self.manager.bot_running() or self.manager.dsh_running() or self.manager.qq_running():
                return
            self.tasks.submit(
                "启动服务", "service", StartStopTask,
                payload={"start": True}, retryable=True, manager=self.manager,
            )
            self.show_toast("已按设置自动启动全部服务", 2600)
        except Exception as e:  # noqa: BLE001
            self.log_bus.emit_entry("管理器", "ERROR", f"自动启动失败: {e}")

    # ------------------------------------------------------------ 版本检查
    def _set_update_button_state(self, newer=False, busy=False, ready=False):
        """标题栏更新按钮状态：发现新版「有新版本」/ 检查中 / 已下好「立即更新」。

        高亮配色交给 QSS 的动态属性 state（见 theme.build_qss），这里只改
        文案与状态位 —— 直接 setStyleSheet 会把主题皮肤盖掉。
        没有新版本时整块按钮隐藏（不常驻，不占视线）。
        """
        btn = getattr(getattr(self, "titlebar", None), "btn_update", None)
        if btn is None:
            return
        if ready:
            text, tip, state = "立即更新", "新版本已下载完成，点击立即安装并重启", "ready"
        elif busy:
            text, tip, state = "检查中", "正在检查更新…", "busy"
        elif newer:
            latest = str((self._update_info or {}).get("latest") or "")
            text, tip, state = "有新版本", f"发现新版本 {latest}，点击在软件里直接更新", "new"
        else:
            text, tip, state = "有新版本", "", ""
        btn.setEnabled(not busy)
        btn.setText(text)
        btn.setToolTip(tip)
        # 用户要求：只有真发现新版本才出现（自动检查中的那 1~2 秒不闪）
        btn.setVisible(bool(ready or newer))
        if btn.property("state") != state:
            btn.setProperty("state", state)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        btn.update()

    def _start_update_check(self, interactive=False):
        """后台检查新版本；interactive=True 表示用户点了按钮（要明确回话）。"""
        import os as _os
        import threading
        from ..core import update_check

        if _os.environ.get("QBM_NO_UPDATE") == "1":
            if interactive:
                self.show_toast("测试模式下已跳过更新检查", 2600)
            return
        if self._update_checking:
            return
        self._update_checking = True
        if interactive:
            self._set_update_button_state(busy=True)
        worker = Worker(self)
        worker.finished.connect(
            lambda res, it=bool(interactive): self._on_update_result(res, it))

        def _run():
            from ..core import updater
            try:
                updater.purge_old()          # 顺手清掉 7 天前的旧安装包
            except Exception:  # noqa: BLE001
                pass
            # 启动那次也强制联网：吃缓存的话，刚发的版本要等 30 分钟才看得见
            worker.run(lambda: update_check.check(force=True))

        threading.Thread(target=_run, daemon=True).start()

    def _on_update_result(self, res, interactive=False):
        """检查结果回来：点亮按钮 / 回话 / 有新版本就问用户是否现在更新。"""
        self._update_checking = False
        res = res if isinstance(res, dict) else {}
        if res.get("ok"):
            self._update_info = res
        newer = bool(res.get("newer"))
        self._set_update_button_state(newer=newer)
        if not newer:
            if interactive:
                if res.get("ok"):
                    self.show_toast(f"已是最新版 v{APP_VERSION}", 2600)
                else:
                    self.show_toast(
                        "检查更新失败：" + str(res.get("error") or "网络不可用"), 3600)
            return
        if not interactive and self._update_prompted:
            return          # 启动后自动检查只打扰一次，之后只把按钮点亮
        self._update_prompted = True
        self._offer_update(res)

    def check_update_interactive(self):
        """标题栏「↓」：手动检查（已发现新版就直接进更新流程）。"""
        if self._update_busy:
            self.show_toast("正在更新中，请稍候…", 2600)
            return
        if self._update_checking:
            self.show_toast("正在检查更新…", 2000)
            return
        if self._update_info.get("newer"):
            self._offer_update(self._update_info)
            return
        self.show_toast("正在检查更新…", 1800)
        self._start_update_check(interactive=True)

    def _offer_update(self, res):
        """问用户要不要在线更新：下载 → 校验 → 静默覆盖 → 自动重启。"""
        # 防重复触发（2026-10-12：自动提示 + 手动检查能同时弹出两个"发现新版本"，
        # 两边都点"立即更新"就会起两个安装器 → 文件没换完程序被拉起 → DLL 崩）
        if self._update_busy or self._update_ready:
            self.show_toast("正在更新中，请稍候…", 2200)
            return
        latest = str(res.get("latest") or "")
        notes = (res.get("notes") or "").strip()
        size = int(res.get("size") or 0)
        size_txt = f"{size / 1024 / 1024:.0f} MB" if size else "大小未知"
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Information)
        box.setText(f"发现新版本 {latest}\n当前版本 {APP_VERSION}")
        info = f"在线更新包约 {size_txt}，装完自动重启；机器人、插件和配置都会保留。"
        if notes:
            info += "\n\n更新说明：\n" + notes[:600]
        box.setInformativeText(info)
        btn_now = box.addButton("立即更新", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.setDefaultButton(btn_now)
        box.exec()
        # 老板 2026-10-10：更新一律走程序内，不再往外跳浏览器
        if box.clickedButton() is btn_now:
            self._begin_update(res)

    def _begin_update(self, res):
        """后台下载安装包；下完起独立安装进程，本程序停服务后退出。"""
        if self._update_busy or self._update_ready:
            return          # 已经在更新（防重复起安装器，见 _offer_update）
        import threading

        from ..core import updater
        size = int(res.get("size") or 0)
        # 下载落在临时目录、安装写回程序目录：两处都要有地方放
        for root in (updater.cache_root(), updater.install_dir()):
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            reason = updater.disk_ok(size, root)
            if reason:
                QMessageBox.warning(self, "空间不足", reason)
                return
        # 进度用点阵卡片（和启动弹窗同一套视觉）
        try:
            from .splash import UpdateCard

            card = UpdateCard()
            card.show_card(f"正在下载更新 {res.get('latest') or ''}".strip())
            self._update_card = card
        except Exception as e:  # noqa: BLE001
            print("UpdateCard 创建失败:", e)
            self._update_card = None
        self._update_dialog = None
        self._update_tip = None
        self._update_bar = None
        self._update_cancel_btn = None
        self._update_cancel_ev = threading.Event()
        self._update_busy = True
        self._update_last_ui = 0.0

        sig = UpdaterSignals(self)
        sig.progress.connect(self._on_update_progress)
        sig.done.connect(self._on_update_downloaded)
        sig.failed.connect(self._on_update_failed)
        self._update_sig = sig

        def _work():
            try:
                setup = updater.download(
                    res,
                    on_progress=lambda got, total: sig.progress.emit(
                        int(got), int(total)),
                    cancel=self._update_cancel_ev,
                )
            except Exception as e:  # noqa: BLE001
                sig.failed.emit(str(e))
                return
            if self._update_cancel_ev.is_set():
                sig.failed.emit("已取消")
                return
            sig.progress.emit(0, 0)          # 转成「正在安装」不确定进度
            info = updater.apply(res, setup)
            if not info.get("ok"):
                sig.failed.emit(str(info.get("error") or "安装程序启动失败"))
                return
            sig.done.emit(str(info.get("log") or setup))

        threading.Thread(target=_work, daemon=True).start()

    def _on_update_progress(self, got, total):
        """下载进度（后台线程 → 信号 → 这里，Qt 控件只在主线程碰）。"""
        if not self._update_busy:
            return
        card = getattr(self, "_update_card", None)
        if card is None:
            return
        if total <= 0:                       # 进入安装阶段
            card.set_progress(1.0)
            card.set_text("正在安装，程序即将重启")
            return
        now = time.monotonic()
        if got < total and now - self._update_last_ui < 0.1:
            return                           # 每 256KB 一次太密，界面 10 次/秒足够
        self._update_last_ui = now
        pct = min(100, int(got * 100 / max(1, total)))
        card.set_progress(pct / 100.0)
        card.set_text(
            "正在下载更新 · {}%  {:.0f}/{:.0f} MB".format(
                pct, got / 1048576, total / 1048576))

    def _cancel_update(self):
        if self._update_cancel_ev is not None:
            self._update_cancel_ev.set()
        card = getattr(self, "_update_card", None)
        if card is not None:
            try:
                card.set_text("正在取消更新")
            except Exception:  # noqa: BLE001
                pass

    def _close_update_dialog(self):
        card = getattr(self, "_update_card", None)
        if card is not None:
            try:
                card.close_card()
            except Exception:  # noqa: BLE001
                pass
            self._update_card = None
        if self._update_dialog is not None:
            self._update_dialog.close()
        self._update_dialog = None
        self._update_bar = None
        self._update_tip = None
        self._update_cancel_btn = None
        self._update_cancel_ev = None
        self._update_sig = None
        self._update_busy = False

    def _on_update_failed(self, msg):
        self._close_update_dialog()
        self._set_update_button_state(newer=bool(self._update_info.get("newer")))
        if str(msg) == "已取消":
            self.show_toast("已取消更新", 2400)
            return
        from ..core import updater as _upd

        hint = "稍后会自动重试，也可以点标题栏的 ↓ 手动重试。"
        if not _upd.exe_path().exists():
            hint = "当前是开发态（没有安装好的程序），请用安装版或便携版验证这套流程。"
        QMessageBox.warning(self, "更新失败", f"{msg}\n\n{hint}")

    def _on_update_downloaded(self, log):
        """安装包已交给独立进程：停服务、退出，安装完由脚本把程序重新拉起。"""
        self._update_ready = self._update_info.get("latest") or True
        card = getattr(self, "_update_card", None)
        if card is not None:
            try:
                card.set_progress(1.0)
                card.set_text("正在安装，程序即将重启")
                QApplication.processEvents()
            except Exception:  # noqa: BLE001
                pass
        self._update_busy = False
        # 退出前别把卡片收掉（用户要看到"正在安装"），直接走关闭流程
        self._begin_close(stop_all=self.manager.bot_running())

    # ------------------------------------------------------------ UI
    def _setup_tray(self):
        """系统托盘：最小化后缩到托盘，双击回来；菜单里能退出（走原有确认+停机器人）。"""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            icon = QApplication.windowIcon() or self.windowIcon()
            if icon.isNull():
                # 源码运行/没带 exe 图标时，托盘会显示成空白（日志会报 No Icon set）
                try:
                    icon = QIcon(sys.executable)
                except Exception:  # noqa: BLE001
                    icon = QIcon()
            if icon.isNull():
                pm = QPixmap(64, 64)
                pm.fill(Qt.transparent)
                _p = QPainter(pm)
                _p.setRenderHint(QPainter.Antialiasing)
                _p.setBrush(QColor("#3D6BFF"))
                _p.setPen(Qt.NoPen)
                _p.drawEllipse(4, 4, 56, 56)
                _p.setPen(QColor("white"))
                _p.setFont(self.font())
                _p.drawText(pm.rect(), Qt.AlignCenter, "巢")
                _p.end()
                icon = QIcon(pm)
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip(f"星巢 Nest v{APP_VERSION}")
            menu = QMenu()
            act_show = menu.addAction("打开主界面")
            act_show.triggered.connect(self._restore_from_tray)
            act_hide = menu.addAction("最小化到托盘")
            act_hide.triggered.connect(self._hide_to_tray)
            menu.addSeparator()
            act_quit = menu.addAction("退出 星巢")
            act_quit.triggered.connect(self._quit_from_tray)
            tray.setContextMenu(menu)
            tray.activated.connect(self._on_tray_activated)
            tray.show()
            self._tray = tray
            _dbg("托盘图标已启用")
        except Exception as e:  # noqa: BLE001 —— 托盘失败绝不能挡住启动
            self._tray = None
            _dbg("托盘启用失败: " + repr(e))

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:  # noqa: BLE001
            pass

    def _hide_to_tray(self):
        """缩到托盘：窗口隐藏但程序继续跑（机器人不受影响）。"""
        if self._tray is None:
            return
        try:
            self.hide()
        except Exception:  # noqa: BLE001
            return
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            try:
                self._tray.showMessage(
                    "星巢 Nest", "已缩到托盘，双击托盘图标可以打开。",
                    QSystemTrayIcon.MessageIcon.Information, 4000)
            except Exception:  # noqa: BLE001
                pass

    def _quit_from_tray(self):
        """从托盘退出：先把窗口亮出来（确认框要有主窗口挂着），再走正常关闭流程。"""
        self._restore_from_tray()
        QTimer.singleShot(80, self.close)

    def _build_ui(self):
        self._setup_tray()
        self.titlebar = TitleBar(self, "星巢 Nest", f"v{APP_VERSION}")

        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebarWrap")
        self.sidebar.setFixedWidth(180)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)

        # 三组导航比原来的两组高，窗口矮的时候必须能滚：内容装进 QScrollArea，
        # 每个列表按内容定高（内部不再出现第二根滚动条 —— 那会让「插件」「设置」
        # 看起来像被截断了）。
        nav_container = QWidget()
        nav = QVBoxLayout(nav_container)
        # 顶部留 16：星群账号按钮的圆角上沿不再贴着标题栏被裁掉；
        # 左边 10 让放大的头像不顶到侧栏边缘
        nav.setContentsMargins(10, 16, 10, 10)
        nav.setSpacing(5)

        self.btn_account_side = self._make_account_button()
        nav.addWidget(self.btn_account_side)
        # 账号胶囊和「运行 / 能力 / 系统」组名之间留一点呼吸感
        gap = QWidget()
        gap.setFixedHeight(6)
        nav.addWidget(gap)

        # 一组一个列表：组名与控制台 NAV_GROUPS 同名（运行 / 能力 / 系统），
        # 组内顺序也一致 —— 两边看同一套侧栏，用户不用重新找菜单位置。
        self.nav_lists = []
        self._nav_rows = {}
        for gi, (group_title, items) in enumerate(self.NAV_GROUPS):
            lbl = QLabel(group_title)
            lbl.setObjectName("sidebarGroupTitle")
            nav.addWidget(lbl)
            lst = QListWidget()
            lst.setObjectName("sidebar")
            lst.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            for row, name in enumerate(items):
                item = QListWidgetItem(name)
                item.setIcon(make_icon(NAV_ICONS.get(name, "home"), "#A7B1C2", 15))
                # 行高写死 40：样式表默认算出来 58px，13 个菜单项加起来会把侧栏
                # 顶到要滚动（默认窗口高度只有 800）。40 刚好放下 15px 图标 + 14px 文字。
                item.setSizeHint(QSize(0, 40))
                lst.addItem(item)
                self._nav_rows[name] = (gi, row)
            lst.currentRowChanged.connect(
                lambda _row, l=lst: self._on_sidebar_changed(l))
            # 先给一个近似高度，真正的高度在 _sync_sidebar_heights() 里按
            # 「当前可见行 + 主题内边距」重算（小白模式隐藏行后必须跟着变矮）。
            lst.setFixedHeight(40 * lst.count() + 6)
            nav.addWidget(lst)
            self.nav_lists.append(lst)
        # 旧代码/测试沿用的两个别名（运行组、系统组）；能力组是新增的中间一组
        self.sidebar_platform = self.nav_lists[0]
        self.sidebar_skill = self.nav_lists[1]
        self.sidebar_global = self.nav_lists[2]
        nav.addStretch(1)

        sidebar_scroll = QScrollArea()
        self.sidebar_scroll = sidebar_scroll
        sidebar_scroll.setObjectName("sidebarScroll")
        # 滚动区只是「窗口太矮时能滚」，视觉上必须和侧栏一样透，不能出现白底
        sidebar_scroll.setStyleSheet(
            "QScrollArea#sidebarScroll{background:transparent;border:none;}"
            "QScrollArea#sidebarScroll > QWidget > QWidget{background:transparent;}")
        sidebar_scroll.setWidget(nav_container)
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_scroll.viewport().setAutoFillBackground(False)
        nav_container.setAutoFillBackground(False)
        side.addWidget(sidebar_scroll, 1)

        # 页面索引（所有导航共用）
        self._page_index = {n: i for i, n in enumerate(self.PAGE_ORDER)}
        self._apply_nav_mode()

        self.stack = QStackedWidget()
        callbacks = {
            "open_logs": self.open_logs,
            "show_toast": self.show_toast,
            "switch_page": self.switch_page,
            "apply_appearance": self.apply_appearance,
            "apply_skin": self.apply_skin,
            "set_beginner_mode": self.set_beginner_mode,
            "bg_status": self.bg.capability,
        }
        ctx = PageContext(self.settings, self.manager, self.tasks, callbacks)
        self._page_ctx = ctx
        # 顺序必须与 PAGE_ORDER 一一对应（页面下标 = 导航字典里的索引）
        self.pages = [
            HomePage(ctx),                 # 首页
            AccessPage(ctx),               # 接入
            WechatPage(ctx),               # 微信
            RunPage(ctx),                  # QQ
            FeishuPage(ctx),               # 飞书
            TelegramPage(ctx),             # 纸飞机
            BrainPage(ctx),                # AI 大脑
            PluginsPage(ctx),              # 插件
            WorkshopPage(ctx),             # 插件工坊（AI 用大白话生成插件）
            ServicesPage(ctx),             # 全局管理（服务启停 + 运维细节）
            MessageCenterPage(ctx),        # 消息中心
            LogsPage(ctx, self.log_bus),   # 日志
            DepsPage(ctx),                 # 依赖
            SettingsPage(ctx),             # 设置
        ]
        assert len(self.pages) == len(self.PAGE_ORDER), (
            "页面数量与 PAGE_ORDER 不一致：加页面时 NAV_GROUPS 与这里都要改")
        for page in self.pages:
            self.stack.addWidget(page)
        # 启动落在「首页」：等 stack 建好再选行，否则 setCurrentRow 回调
        # _on_sidebar_changed -> _go_page 会去碰还不存在的 self.stack
        self._go_page("首页")

        self.overlay = QWidget(self)
        self._outer = QVBoxLayout(self.overlay)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._outer.addWidget(self.titlebar)
        self._apply_layout()

        # Toast
        self._toast = QLabel(self.overlay)
        self._toast.setObjectName("toast")
        self._toast.hide()
        # 进出场：从底边滑上来 + 淡入，收起时原路退回（10px 位移，够看出方向）
        self._toast_shift = 0
        self._toast_anim = None
        self._toast_fx = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._hide_toast)

        for page in self.pages:
            if hasattr(page, "refresh_status"):
                page.refresh_status()

    def _sync_sidebar_heights(self):
        """按当前可见行重算侧栏列表高度（小白模式/外观切换/缩放后调用）。

        列表 QSS 自带上下内边距（各 10px），这部分会占用控件高度但不属于行
        内容区。写死 ``40*行数`` 会让最后一行差 14px 被裁；隐藏行后也不会
        自动变矮，留下一个空框。
        """
        lists = getattr(self, "nav_lists", None)
        if not lists:
            return
        for lst in lists:
            try:
                vis_rows = [r for r in range(lst.count()) if not lst.isRowHidden(r)]
                need = 0
                for r in vis_rows:
                    hint = lst.sizeHintForRow(r)
                    need += hint if hint > 0 else 40
                # 量当前主题的 chrome（内边距 + 边框）；首次未上样式时按 0 处理，
                # 样式应用后 resizeEvent / _finish_theme_switch 会再校正一次。
                chrome = max(0, lst.height() - lst.viewport().height())
                lst.setFixedHeight(max(0, need + chrome + 2))
            except Exception:  # noqa: BLE001
                pass
        scroll = getattr(self, "sidebar_scroll", None)
        if scroll is not None:
            try:
                # 内容能完整放下时把外滚动条位置归零，保证「星群账号」顶部完整；
                # 放不下（很小的窗口）时也不强制打断用户当前的滚动位置。
                if scroll.verticalScrollBar().maximum() == 0:
                    scroll.verticalScrollBar().setValue(0)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ 页面/导航
    def _apply_layout(self):
        """把侧边栏 + 页面堆栈挂进主区域（只剩极光玻璃一套布局）。"""
        current_page = self.stack.currentIndex()
        while self._outer.count() > 1:
            item = self._outer.takeAt(1)
            w = item.widget()
            if w is not None:
                w.setParent(None)
            elif item.layout() is not None:
                while item.layout().count():
                    it = item.layout().takeAt(0)
                    if it.widget() is not None:
                        it.widget().setParent(None)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar)
        row.addWidget(self.stack, 1)
        self._outer.addLayout(row, 1)
        # 重建后恢复原页面，避免重挂载时跳回首页
        self.stack.setCurrentIndex(min(current_page, max(0, self.stack.count() - 1)))

    def _on_sidebar_changed(self, lst):
        if self._nav_syncing:
            return
        row = lst.currentRow()
        if row < 0:
            return
        gi = self.nav_lists.index(lst)
        items = self.NAV_GROUPS[gi][1]
        if row >= len(items):
            return
        # 只有一组能选中：切这一组时把其余组清空，否则会出现两处同时高亮
        for other in self.nav_lists:
            if other is not lst:
                other.setCurrentItem(None)
        self._go_page(items[row])

    def _apply_nav_mode(self):
        """小白模式：隐藏高级页面（通道页 + 消息中心/插件/日志/依赖）。"""
        beginner = bool(self.settings.beginner_mode)
        for gi, (_title, items) in enumerate(self.NAV_GROUPS):
            lst = self.nav_lists[gi]
            for row, name in enumerate(items):
                lst.setRowHidden(row, beginner and name in self.ADVANCED_PAGES)
        self._sync_sidebar_heights()

    def nav_row_hidden(self, name) -> bool:
        """某个导航项当前是否被小白模式藏起来了（设置页/测试用它，别写死行号）。"""
        loc = self._nav_rows.get(name)
        if loc is None:
            return True
        gi, row = loc
        return bool(self.nav_lists[gi].isRowHidden(row))

    def set_beginner_mode(self, on: bool):
        """切换小白模式：立即生效并保存。"""
        on = bool(on)
        self.settings.beginner_mode = on
        self.settings.save()
        self._apply_nav_mode()
        if on:
            idx = self.stack.currentIndex()
            name = next((n for n, i in self._page_index.items() if i == idx), None)
            if name in self.ADVANCED_PAGES:
                self._go_page("首页")
        for page in self.pages:
            if hasattr(page, "set_beginner"):
                try:
                    page.set_beginner(on)
                except Exception:  # noqa: BLE001
                    pass
        self.show_toast(
            "已开启小白模式（只显示常用功能）" if on else "已关闭小白模式（显示全部功能）",
            2200)

    def _go_page(self, name):
        idx = self._page_index.get(name)
        if idx is None:
            return
        prev = self.stack.currentIndex()
        self.stack.setCurrentIndex(idx)
        self._sync_nav(name)
        if prev != idx:
            self._fade_in_page(self.stack.currentWidget())
        QTimer.singleShot(60, self._refresh_glass_panels)

    def _fade_in_page(self, page):
        """页面切换淡入：规范 = 200ms / ease-out，只做透明度不做位移。

        为什么不做位移：页面里有抓屏玻璃面板（GlassPanel），整个页面平移会让
        抓屏内容对不上、看起来在"抖"。所以这里只淡入。
        动效关掉时直接跳过（`motion.enabled()` 为假）。
        """
        if page is None or not motion.enabled():
            return
        try:
            fx = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(fx)
            anim = QPropertyAnimation(fx, b"opacity", page)
            anim.setDuration(motion.DUR_PAGE)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(motion.EASE_OUT)

            def _done():
                try:
                    # 用完立刻摘掉：常驻的效果器会把页面渲染进离屏位图，
                    # 和玻璃面板的抓屏叠加会变糊、也白吃性能。
                    page.setGraphicsEffect(None)
                except Exception:  # noqa: BLE001
                    pass

            anim.finished.connect(_done)
            anim.start()
            self._page_fade_anim = anim          # 挂引用防 GC
        except Exception:  # noqa: BLE001 —— 动效失败不能影响切页
            pass

    def _refresh_glass_panels(self):
        """页面切换后统一刷新玻璃背景（替代原来的 700ms 周期抓屏）。"""
        for w in self.findChildren(QWidget):
            if w.metaObject().className() == "GlassPanel" and w.isVisible():
                try:
                    w.refresh_now()
                except Exception:  # noqa: BLE001
                    pass

    def _sync_nav(self, name):
        # 通道详情页没有自己的侧栏行 / 图标：高亮它归属的「接入」
        name = self.SUB_NAV_PARENT.get(name, name)
        self._nav_syncing = True
        try:
            loc = self._nav_rows.get(name)
            if loc is not None:
                gi, row = loc
                lst = self.nav_lists[gi]
                if lst.currentRow() != row:
                    # setCurrentRow 会回调 _on_sidebar_changed -> _go_page，
                    # 第二次进来 currentRow 已经相等，不会递归
                    lst.setCurrentRow(row)
                for other in self.nav_lists:
                    if other is not lst:
                        other.setCurrentItem(None)
        finally:
            self._nav_syncing = False

    def switch_page(self, name):
        self._go_page(name)

    def open_logs(self):
        self.switch_page("日志")

    # ------------------------------------------------------------ Toast
    def show_toast(self, msg, duration=2800):
        self._toast.setText(str(msg))
        self._toast.adjustSize()
        self._toast.show()
        self._toast.raise_()
        self._reposition_toast()
        self._animate_toast(True)
        self._toast_timer.start(duration)

    def _hide_toast(self):
        if self._toast.isVisible():
            self._animate_toast(False)
        else:
            self._toast.hide()

    def _stop_toast_anim(self):
        if self._toast_anim is not None:
            self._toast_anim.stop()
            self._toast_anim = None

    def _toast_opacity(self, value: float):
        if self._toast_fx is None:
            self._toast_fx = QGraphicsOpacityEffect(self._toast)
            self._toast.setGraphicsEffect(self._toast_fx)
        self._toast_fx.setOpacity(max(0.0, min(1.0, value)))

    def _drop_toast_effect(self):
        if self._toast_fx is not None:
            self._toast.setGraphicsEffect(None)
            self._toast_fx = None

    def _animate_toast(self, show: bool):
        """提示条进出场：进入 200ms 快起慢落、退出 140ms 慢起快走，同一条路径。"""
        self._stop_toast_anim()
        if not motion.enabled():
            self._toast_shift = 0
            self._drop_toast_effect()
            self._reposition_toast()
            self._toast.setVisible(show)
            return
        if show:
            self._toast.show()
        anim = QVariantAnimation(self)
        anim.setDuration(motion.DUR_TOAST_IN if show else motion.DUR_TOAST_OUT)
        anim.setEasingCurve(motion.EASE_OUT if show else motion.EASE_IN)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(lambda v: self._on_toast_anim(float(v), show))
        anim.finished.connect(lambda: self._on_toast_anim_done(show))
        self._toast_anim = anim
        # 先按起点摆好（否则第一帧会闪在终点位置上），再开跑
        self._on_toast_anim(0.0, show)
        anim.start()

    def _on_toast_anim(self, t: float, show: bool):
        progress = t if show else 1.0 - t
        self._toast_shift = int(round((1.0 - progress) * 10))
        self._reposition_toast()
        self._toast_opacity(progress)

    def _on_toast_anim_done(self, show: bool):
        self._stop_toast_anim()
        self._toast_shift = 0
        if not show:
            self._toast.hide()
            self._drop_toast_effect()

    def _reposition_toast(self):
        self._toast.move(
            (self.width() - self._toast.width()) // 2,
            self.height() - self._toast.height() - 72 + int(getattr(self, "_toast_shift", 0)),
        )

    def paintEvent(self, event):
        """极光玻璃底色：视频/图片背景启用时由上层背景覆盖。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        r = 0 if (self.isMaximized() or self.isFullScreen()) else theme_mod.WINDOW_RADIUS
        if r:
            path.addRoundedRect(rect, r, r)
        else:
            path.addRect(rect)
        p.setClipPath(path)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor(18, 22, 46))
        g.setColorAt(0.55, QColor(24, 22, 58))
        g.setColorAt(1, QColor(10, 13, 24))
        p.fillRect(self.rect(), g)
        for cx, cy, r, c in (
            (self.width() * 0.22, self.height() * 0.15, 340, QColor(88, 101, 242, 70)),
            (self.width() * 0.82, self.height() * 0.28, 300, QColor(139, 92, 246, 60)),
            (self.width() * 0.55, self.height() * 0.85, 360, QColor(56, 189, 248, 46)),
        ):
            rg = QRadialGradient(cx, cy, r)
            rg.setColorAt(0, c)
            rg.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), rg)
        p.end()

    # ------------------------------------------------------------ 背景
    def apply_appearance(self):
        s = self.settings
        self.apply_skin()
        self._sync_sidebar_heights()
        # UI 框透明度变化时强制重建样式触发全局重绘
        op = max(0.1, min(0.95, s.ui_opacity / 100.0))
        if abs(op - theme_mod.UI_OPACITY) > 0.001:
            theme_mod.UI_OPACITY = op
            self._repaint_glass()
        self.bg.set_mask_strength(s.bg_mask_strength)
        self.bg.set_autoplay(s.bg_video_autoplay)
        self.bg.set_loop(s.bg_video_loop)
        self.bg.set_brightness(s.bg_brightness)
        # 图片背景优先于视频
        img_ok = False
        if s.bg_image_enabled and s.bg_image_path:
            self.img_bg.set_image(s.bg_image_path)
            img_ok = self.img_bg.has_image()
        self.img_bg.setVisible(img_ok)
        if img_ok:
            self.bg.set_enabled(False)
            self.bg.set_video(None)
            self.bg.setVisible(False)
            self.manager.log("图片背景: " + str(s.bg_image_path))
            return
        # 源码模式（run_source.pyw）自动关掉动态背景：Qt Quick + ffmpeg/D3D11 这条路在
        # "源码跑"时会让进程**原生崩溃**（access violation，日志停在 QDxgiVSyncService），
        # 装机版没这问题。只影响源码运行，不改用户的设置项。
        if os.environ.get("QBM_SOURCE_RUN") == "1":
            self.bg.set_enabled(False)
            self.bg.set_video(None)
            self.bg.setVisible(False)
            self.manager.log("源码模式：动态背景已自动关闭（避免 Qt Quick 原生崩溃）")
            return
        self.bg.set_enabled(s.bg_video_enabled)
        path = s.bg_video_path or find_default_background(s)
        if not path:
            self.bg.set_video(None)
            self.bg.setVisible(False)
            return
        # Phase 2：set_video 会发出 playbackRequested，由主窗口选档并走后台任务准备/转码
        self.bg.setVisible(True)
        # 路径比较必须统一为 Path 对象（正/反斜杠、大小写都归一），
        # 否则每次外观刷新（含滑条/换档）都会误判路径变化重拉视频。
        path_changed = Path(str(path)) != getattr(self.bg, "_path", None)
        if path_changed:
            self.bg.set_video(str(path))
        if s.bg_video_enabled:
            self.manager.log("动态背景源: " + str(path))

    def _repaint_glass(self):
        """玻璃卡片自绘参数变化时全量重绘。"""
        for w in self.findChildren(QWidget):
            if w.metaObject().className() in ("GlassPanel", "StatusBadge", "TaskProgressBar"):
                w.update()

    def apply_skin(self, force: bool = False):
        """按设置的强调色 / 框质感重建样式表（界面固定极光玻璃）。"""
        theme = self.settings.theme or "glass"
        if theme not in theme_mod.UI_THEMES:
            theme = "glass"
        glass = self.settings.glass_effect or "default"
        accent = self.settings.accent or "default"
        key = (theme, glass, accent)
        if not force and key == self._applied_skin:
            return  # 主题/质感没变就跳过重建，避免拖动滑条时卡顿
        self._applied_skin = key
        theme_mod.CURRENT_GLASS = glass
        theme_mod.SUPPRESS_BACKDROP_REFRESH = True
        # 等样式应用完再统一刷新玻璃背景，避免每个面板各自抓屏导致卡顿
        QTimer.singleShot(180, self._finish_theme_switch)
        try:
            QApplication.instance().setStyleSheet(
                build_qss(theme, glass, accent))
        except Exception:  # noqa: BLE001
            QApplication.instance().setStyleSheet(build_qss("glass", glass))

    def _finish_theme_switch(self):
        theme_mod.SUPPRESS_BACKDROP_REFRESH = False
        self._refresh_glass_panels()
        self._repaint_glass()
        self._sync_sidebar_heights()

    def _on_bg_playback_requested(self, src):
        self.manager.log("动态背景: 直接播放原视频 " + str(src))
        self.bg.play_file(str(src))

    def _on_bg_fallback(self, reason):
        self.log_bus.emit_entry("外观", "WARN", "动态背景: " + str(reason))

    def _on_bg_load_failed(self, desc):
        self.log_bus.emit_entry(
            "外观", "WARN", "背景视频加载失败，已切换静态背景: " + str(desc))
        self.manager.log("背景视频播放失败: " + str(desc))

    def _on_tick(self):
        if self._closing:
            return
        for page in self.pages:
            if hasattr(page, "refresh_status"):
                try:
                    page.refresh_status()
                except Exception as e:  # noqa: BLE001
                    self.log_bus.emit_entry(
                        "管理器", "ERROR", f"{type(page).__name__} 刷新失败: {e}")
    # ------------------------------------------------------------ 窗口
    def toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _center_on_screen(self):
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            geo = scr.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self.bg.set_window_active(not self.isMinimized())
            self._sync_corner_radius()
            # 最小化 → 缩到托盘（老板 2026-10-10 要的"隐藏到托盘"）。
            # 注意：这个类里 changeEvent 只能有一处定义 —— 我先前另加了一份，
            # 被这里覆盖成死代码，最小化根本没缩托盘（自己踩的坑）。
            try:
                if self._tray is not None and self.isMinimized():
                    QTimer.singleShot(0, self._hide_to_tray)
            except Exception:  # noqa: BLE001
                pass
        # 窗口重新被激活（从浏览器个人中心切回来、点任务栏回来）：立刻对一次账号资料，
        # 官网刚换的头像当帧就能看到，不用再等 60 秒那一跳。
        elif event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._pull_account_profile()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_corner_radius()

    def _sync_corner_radius(self):
        """通知各背景层圆角半径（最大化/全屏时变直角），并重绘本窗口。"""
        square = self.isMaximized() or self.isFullScreen()
        r = 0 if square else theme_mod.WINDOW_RADIUS
        if hasattr(self, "titlebar"):
            self.titlebar.setProperty("corner", "square" if square else "round")
            style = self.titlebar.style()
            style.unpolish(self.titlebar)
            style.polish(self.titlebar)
            self.titlebar.update()
        self.bg.set_corner_radius(r)
        self.img_bg.set_corner_radius(r)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bg.setGeometry(self.rect())
        self.img_bg.setGeometry(self.rect())
        self.overlay.setGeometry(self.rect())
        self._layout_resize_handles()
        self._reposition_toast()
        self._sync_corner_radius()
        self._sync_sidebar_heights()

    # ------------------------------------------------------------ 边缘拖拽缩放
    def _create_resize_handles(self):
        """创建 8 个边缘手柄（四边 + 四角），置于 overlay 之上。"""
        cursors = {
            "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
            "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor,
            "topleft": Qt.SizeFDiagCursor, "bottomright": Qt.SizeFDiagCursor,
            "topright": Qt.SizeBDiagCursor, "bottomleft": Qt.SizeBDiagCursor,
        }
        self._resize_handles = {
            name: ResizeHandle(self, name, cur, parent=self.overlay)
            for name, cur in cursors.items()
        }
        self._layout_resize_handles()
        for h in self._resize_handles.values():
            h.raise_()

    def _layout_resize_handles(self):
        if not hasattr(self, "_resize_handles"):
            return
        r = self.overlay.rect()
        t = self.RESIZE_MARGIN
        c = t * 2
        pos = {
            "left": (0, 0, t, r.height()),
            "right": (r.width() - t, 0, t, r.height()),
            "top": (0, 0, r.width(), t),
            "bottom": (0, r.height() - t, r.width(), t),
            "topleft": (0, 0, c, c),
            "topright": (r.width() - c, 0, c, c),
            "bottomleft": (0, r.height() - c, c, c),
            "bottomright": (r.width() - c, r.height() - c, c, c),
        }
        for name, h in self._resize_handles.items():
            x, y, w, hgt = pos[name]
            h.setGeometry(x, y, w, hgt)
        for h in self._resize_handles.values():
            h.raise_()

    def _apply_resize_edge(self, edge, start_geom, start_gpos, gpos):
        """按边缘方向把窗口从 start_geom 调整到 gpos（含最小尺寸保护）。"""
        g = QRect(start_geom)
        dx = gpos.x() - start_gpos.x()
        dy = gpos.y() - start_gpos.y()
        e = edge
        if "left" in e:
            g.setLeft(g.left() + dx)
        if "right" in e:
            g.setRight(g.right() + dx)
        if "top" in e:
            g.setTop(g.top() + dy)
        if "bottom" in e:
            g.setBottom(g.bottom() + dy)
        if g.width() < self.MIN_WINDOW_W:
            if "left" in e:
                g.setLeft(g.right() - self.MIN_WINDOW_W)
            else:
                g.setWidth(self.MIN_WINDOW_W)
        if g.height() < self.MIN_WINDOW_H:
            if "top" in e:
                g.setTop(g.bottom() - self.MIN_WINDOW_H)
            else:
                g.setHeight(self.MIN_WINDOW_H)
        self.setGeometry(g)

    def nativeEvent(self, eventType, message):
        """Windows 原生命中测试：让系统直接处理边缘/顶角缩放，精确且不受子控件遮挡影响。"""
        try:
            if isinstance(eventType, bytes) and b"windows_generic_MSG" in eventType:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    if self.isMaximized() or self.isFullScreen():
                        return False, 0
                    dpr = self.devicePixelRatioF() or 1.0
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    g = self.geometry()
                    left = int(g.left() * dpr)
                    top = int(g.top() * dpr)
                    right = int(g.right() * dpr)
                    bottom = int(g.bottom() * dpr)
                    m = int(self.RESIZE_MARGIN * dpr)
                    c = int(m * 2)
                    hit = 0
                    if x <= left + c and y <= top + c:
                        hit = 13  # HTTOPLEFT
                    elif x >= right - c and y <= top + c:
                        hit = 14  # HTTOPRIGHT
                    elif x <= left + c and y >= bottom - c:
                        hit = 16  # HTBOTTOMLEFT
                    elif x >= right - c and y >= bottom - c:
                        hit = 17  # HTBOTTOMRIGHT
                    elif x <= left + m:
                        hit = 10  # HTLEFT
                    elif x >= right - m:
                        hit = 11  # HTRIGHT
                    elif y <= top + m:
                        hit = 12  # HTTOP
                    elif y >= bottom - m:
                        hit = 15  # HTBOTTOM
                    if hit:
                        return True, hit
        except Exception:  # noqa: BLE001
            pass
        return False, 0

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, event):
        from .splash import _dbg
        _dbg(f'closeEvent 进入 (closing={self._closing})')
        if self._closing:
            event.accept()
            return
        self.watch_timer.stop()
        try:
            running = self.manager.bot_running()
        except Exception as e:  # noqa: BLE001
            _dbg('bot_running() 抛异常: ' + repr(e))
            running = False
        _dbg(f'bot_running={running}')
        if running:
            ret = QMessageBox.question(
                self, "退出 星巢",
                "机器人服务仍在运行，关闭程序将停止全部服务。\n\n"
                "确定退出吗？",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if ret == QMessageBox.Cancel:
                event.ignore()
                self.watch_timer.start()
                return
        event.accept()
        _dbg('closeEvent: 已 accept，进入 _begin_close')
        self._begin_close(stop_all=running)

    def _stop_profile_timer(self):
        """账号资料的定时器停干净：关窗/退出后不再有任何一次自动同步。"""
        try:
            if getattr(self, "_account_profile_timer", None) is not None:
                self._account_profile_timer.stop()
        except Exception:  # noqa: BLE001
            pass

    def _begin_close(self, stop_all):
        """关闭流程（老板 2026-10-10 定稿：**不要关闭动画卡片**）。

        时序：
          0ms   主窗口立刻关闭（不淡出、不出卡片）
          0ms   后台线程静默停服务（不阻塞、不弹窗）
          300ms 硬退：进程消失，机器人等子进程由作业对象一起回收
        老板原话：「关闭不要动画卡片吧，直接窗口关闭后台静默关机」。
        """
        from .splash import _dbg as _d

        _d("T0 进入 _begin_close（无卡片模式）")
        self._closing = True
        # 关掉 Qt 的「最后一个窗口关闭就退出」，退出统一由下面那道闸负责
        try:
            _app = QApplication.instance()
            if _app is not None:
                _app.setQuitOnLastWindowClosed(False)
        except Exception:  # noqa: BLE001
            pass

        # ---- 0) 硬时限先挂上：不看任何回调脸色，到点必退
        # （实测：关闭路径上做重活会把 GUI 线程占住、定时器全被推迟，所以闸门排在最前面。）
        def _hard():
            _d("T6 硬退")
            _hard_exit(0)

        # 注意：这次是"先杀干净再退"，不是"马上退" —— 所以闸门放到 5 秒，
        # 但下面的强杀通常 0.3~1 秒就回来了。
        QTimer.singleShot(5000, _hard)
        QTimer.singleShot(9000, _hard)               # 双保险

        try:
            if self._tray is not None:
                self._tray.hide()
        except Exception:  # noqa: BLE001
            pass

        # ---- 1) 立刻关窗口（不等淡出、不出卡片）
        try:
            self.hide()
        except Exception:  # noqa: BLE001
            pass
        _d("T1 主窗口已关闭（无卡片）")
        self._stop_profile_timer()

        # ---- 2) 同步、快速地强杀机器人进程（绝不能丢后台：退出会把线程带走）
        # 2026-10-10 老板实测："前端关了，QQ 群里还能说话" —— 就是丢后台被带走造成的。
        if stop_all:
            try:
                res = self.manager.kill_all_fast()
                _d(f"已强杀机器人进程：{res.get('killed')}")
            except Exception as e:  # noqa: BLE001
                _d("强杀机器人失败: " + repr(e))
        # 杀完就退：别干等上面那道 5 秒闸门（实测那样关窗要 6.5 秒）
        QTimer.singleShot(60, _hard)

        # ---- 3) 让事件循环也收一下（真正送走进程仍靠上面的硬时限）
        try:
            _app = QApplication.instance()
            if _app is not None:
                QTimer.singleShot(120, _app.quit)
        except Exception:  # noqa: BLE001
            pass

    def _force_exit(self):
        """死闸（保留给异常路径调用）：无论如何把进程结束掉。"""
        try:
            _app = QApplication.instance()
            if _app is not None:
                _app.quit()
        except Exception:  # noqa: BLE001
            pass
        _hard_exit(0)

    def _really_quit(self):
        """保留的兼容出口：不再有卡片，直接送走进程。"""
        from .splash import _dbg as _d

        _d("T5 _really_quit（无卡片模式：直接退）")
        if self._exit_dialog is not None:
            self._exit_dialog.close()
            self._exit_dialog = None
        _hard_exit(0)
