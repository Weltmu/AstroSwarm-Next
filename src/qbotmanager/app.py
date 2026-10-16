import json
import getpass
import os
import sys
import time
import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from .core import migrate as migrate_mod
from .core import process as process_mod
from .core import cleanup as cleanup_mod
from .constants import APP_VERSION
from .core.settings import Settings
from .tasks.task_manager import TaskManager
from .ui.main_window import MainWindow
from .ui import press_scale as _press_scale  # noqa: F401  # 全局按钮按压缩放反馈
from .ui.widgets import WheelGuard
from .ui.theme import build_qss
from .ui.wizard import DeployWizard


def _selftest(out_path: str) -> int:
    """打包后自检：写入 JSON 结果文件后退出。"""
    results = {"ok": False}
    try:
        from .core.ports import is_port_free
        results["imports"] = "ok"
        results["default_port_free"] = is_port_free(12111)
        results["ok"] = results["imports"] == "ok"
    except Exception as e:  # noqa: BLE001
        results["error"] = repr(e)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return 0 if results["ok"] else 1


def _enable_dpi_aware():
    """强制 Per-Monitor DPI Aware，避免打包版点击坐标偏移导致按钮失灵。"""
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:  # noqa: BLE001
        pass


ERROR_ALREADY_EXISTS = 183


def _single_instance(app, name: str | None = None) -> bool:
    """Windows 命名互斥体单实例：成功返回 True；已有实例返回 False。"""
    # 互斥体前缀保持旧名：升级过渡期与老版本互相排斥，避免双开抢同一安装目录
    name = name or ("QBotManager-" + getpass.getuser())
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)  # 清除残留错误码，避免误判已有实例
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # 创建失败不阻塞启动，避免误伤
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return False
    app._qbm_single_mutex = handle  # 进程生命周期内保持句柄
    return True


def _force_foreground(hwnd):
    """把窗口强制拉到最前（Windows 只让前台进程抢焦点，这里借用前台线程的输入权）。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE：最小化的先还原
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_cur = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_cur:
        attached = bool(user32.AttachThreadInput(tid_cur, tid_fg, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(tid_cur, tid_fg, False)
    if user32.GetForegroundWindow() != hwnd:
        user32.FlashWindow(hwnd, True)  # 兜底：拉不动就闪任务栏图标
        return False
    return True


def _activate_existing_window() -> bool:
    """把已运行实例的主窗口恢复到前台；成功返回 True，找不到窗口返回 False。"""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        current_pid = kernel32.GetCurrentProcessId()
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == current_pid:
                return True
            if ("星巢" in buf.value or "AstroSwarm" in buf.value
                    or "QBotManager" in buf.value):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(_enum_cb, 0)
        if not found:
            return False
        return _force_foreground(found[0])
    except Exception:  # noqa: BLE001
        return False


def _warn_already_running():
    """没能把已有窗口拉到前台时的兜底提示：置顶显示，2.5 秒自动关，不留下卡住的进程。"""
    box = QMessageBox()
    box.setWindowTitle("星巢 Nest")
    box.setIcon(QMessageBox.Information)
    box.setText("星巢已经在运行。")
    box.setInformativeText("没切过去的话，点任务栏上的星巢图标就行。")
    box.setStandardButtons(QMessageBox.Ok)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    QTimer.singleShot(2500, box.accept)
    box.exec()


def run_gui():
    _enable_dpi_aware()
    # 视频后端：优先 FFmpeg（D3D11VA 硬解 + RHI 纹理直通），
    # Windows Media Foundation 在 QQuickWidget 下常走 CPU 回读转换。
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    os.environ.setdefault("QSG_RHI_BACKEND", "d3d11")
    # 高分屏：125%/150% 等小数缩放不四舍五入成整数倍，避免文字被二次缩放发糊
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # 退出作业：程序退出/崩溃时由 Windows 兜底终止全部子进程，杜绝残留
    process_mod.install_exit_job()
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app.setStyleSheet(build_qss())
    # 滚轮别偷偷改下拉框/数值框（页面滚动时鼠标扫过就会改，用户以为是程序乱跳）
    _wheel_guard = WheelGuard(app)
    app.installEventFilter(_wheel_guard)
    app.setApplicationName("AstroSwarm")
    app.setOrganizationName("AstroSwarm")

    if not _single_instance(app):
        # 双击图标时把已有窗口拉前台；拉不动才弹提示（提示会自动消失，不留后台残留进程）
        if not _activate_existing_window():
            _warn_already_running()
        return 0

    root = Settings.find_root()
    if root is None:
        # 自动沿用旧版本安装（配置/插件/登录原地保留，不复制）
        root = migrate_mod.try_adopt_candidate()
    if root is None:
        wizard = DeployWizard()
        if wizard.exec() != QDialog.Accepted:
            return 0
        settings = wizard.settings
    else:
        settings = Settings.load(root)
        if not settings.is_deployed():
            # 半部署：优先沿用候选里的完整旧安装；没有才进向导补齐
            adopted = migrate_mod.try_adopt_candidate(exclude=str(root))
            if adopted is not None:
                settings = Settings.load(adopted)
            else:
                wizard = DeployWizard(root=root)
                if wizard.exec() != QDialog.Accepted:
                    return 0
                settings = wizard.settings

    # 启动动画：纯色卡 + 文字 logo（「减少界面动效」关掉时整段跳过）
    splash = None
    try:
        from .ui import motion as motion_mod
        from .ui.splash import LaunchSplash, fade_window  # noqa: F401

        motion_mod.set_enabled(bool(getattr(settings, "animations_enabled", True)))
        if motion_mod.enabled():
            splash = LaunchSplash(APP_VERSION).begin()
            _splash_t0 = time.time()
            # 关键：这里必须把文字 logo 动画**真正播完**再往下走。
            # 下面建 MainWindow 会独占主线程十几秒，不把动画先演完的话，
            # 卡片窗口虽然"显示"了却一帧都画不出来（屏幕上看不见），
            # 或者停在第一帧、像死机。play_intro() 内部自己泵事件循环。
            splash.play_intro()
    except Exception:  # noqa: BLE001 —— 动画出问题绝不能挡住启动
        splash = None

    # 启动时后台清理日志/临时文件/消息缓存/过期会话，不阻塞主界面
    threading.Thread(
        target=lambda: cleanup_mod.run(settings),
        daemon=True,
    ).start()

    tasks = TaskManager()
    try:
        win = MainWindow(settings, tasks)
    except BaseException as e:  # noqa: BLE001 —— 建主窗口失败要让人看得见原因
        import traceback as _tb
        from pathlib import Path as _P

        # 1) 先把启动卡收掉：不然报错框会被它盖在最下面，点不着也看不清
        try:
            if splash is not None:
                splash.abort()
        except Exception:  # noqa: BLE001
            pass
        # 2) 写崩溃日志（打包版没控制台，这是唯一现场）
        detail = _tb.format_exc()
        try:
            _log = _P(os.environ.get("TEMP", ".")) / "ast nest crash.log"
            _log.write_text(detail, encoding="utf-8")
        except Exception:  # noqa: BLE001
            _log = None
        # 3) 给人看的报错框（可复制）
        try:
            box = QMessageBox()
            box.setWindowTitle("星巢启动失败")
            box.setIcon(QMessageBox.Critical)
            box.setText("程序启动时出错，已停下。")
            box.setInformativeText(
                f"{type(e).__name__}: {e}\n\n"
                + (f"详细信息已写入：{_log}" if _log else "详细信息见程序目录下的日志。"))
            box.setDetailedText(detail[-4000:])
            box.exec()
        except Exception:  # noqa: BLE001
            pass
        return 1
    
    if splash is not None:
        # 主窗口先透明，等启动动画收尾时再淡入（收不掉时下面还有兜底强制恢复）
        try:
            win.setWindowOpacity(0.0)
        except Exception:  # noqa: BLE001
            pass
    win.show()
    if splash is not None:
        # 最短显示：主窗口秒开时也别让动画"一闪而过"（老板反馈显示太短）
        try:
            from .ui.splash import MIN_ONSCREEN as _min_on

            while time.time() - _splash_t0 < _min_on:
                app.processEvents()
                time.sleep(0.01)
        except Exception:  # noqa: BLE001
            pass
        try:
            from .ui.splash import fade_window as _fade

            splash.finish(on_done=lambda: _fade(win, 0.0, 1.0, 240,
                                                on_done=lambda: win.setWindowOpacity(1.0)))
            # 兜底：动画被打断/异常时，1 秒后强制恢复不透明，别让窗口看不见
            QTimer.singleShot(1000, lambda: win.setWindowOpacity(1.0))
        except Exception:  # noqa: BLE001
            try:
                win.setWindowOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass
    return app.exec()


def main():
    argv = sys.argv
    if len(argv) > 1 and argv[1] == "--cli":
        from .core.cli import main as cli_main
        sys.exit(cli_main(argv[2:]))
    if len(argv) > 2 and argv[1] == "--selftest":
        sys.exit(_selftest(argv[2]))
    sys.exit(run_gui())


if __name__ == "__main__":
    main()
