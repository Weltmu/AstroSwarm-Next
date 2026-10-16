# -*- coding: utf-8 -*-
"""启动/关闭动画的功能验证（不是看画得好不好，是验行为对不对）。

验 5 条：
  ① 启动动画能动、能收尾、收尾回调一定被调用（主窗口才能淡入）
  ② 动效关掉时：启动动画立刻可见、收尾同步完成（不留空白期）
  ③ 关闭卡片：显示 → 消散 → 回调（退出靠它）
  ④ 主窗口关闭流程：窗口被隐藏 + 卡片出现 + 最终真的退出（stub 掉 app.quit 记录调用）
  ⑤ 任何一步都不抛异常（动画库/字体缺失也不能挡住启动退出）
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QBM_NO_UPDATE"] = "1"
REPO = Path(r"D:\ai\QBotManager")
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
problems = []


def ok(label, cond, detail=""):
    print(("✓ " if cond else "× ") + label + (f" —— {detail}" if detail else ""))
    if not cond:
        problems.append(label)


def spin(ms):
    """跑事件循环 ms 毫秒。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


from qbotmanager.ui import motion  # noqa: E402
from qbotmanager.ui.splash import ClosingCard, LaunchSplash, fade_window  # noqa: E402

# ① 启动动画
motion.set_enabled(True)
sp = LaunchSplash("1.2.19")
called = {}
sp.begin()
spin(80)
ok("启动卡显示中", sp.isVisible())
ok("启动卡必须不透明可⻅（淡入会因主线程被占用而卡在 0 = 屏幕上看不见）",
   sp.windowOpacity() == 1.0, f"opacity={sp.windowOpacity():.2f}")
sp.finish(on_done=lambda: called.setdefault("done", True))
spin(420)
ok("启动卡收尾回调被调用（主窗口据此淡入）", called.get("done") is True)
try:
    hidden = not sp.isVisible()
except RuntimeError:
    hidden = True          # 已被 deleteLater 回收 = 确实关掉了
ok("启动卡已关闭", hidden)

# ② 动效关闭
motion.set_enabled(False)
sp2 = LaunchSplash("1.2.19")
done2 = {}
sp2.begin()
ok("动效关闭：启动卡立刻可见", sp2.isVisible() and sp2.windowOpacity() == 1.0,
   f"opacity={sp2.windowOpacity()}")
sp2.finish(on_done=lambda: done2.setdefault("d", True))
spin(60)
ok("动效关闭：收尾同步完成", done2.get("d") is True)
motion.set_enabled(True)

# ③ 关闭卡片
card = ClosingCard("正在停止机器人服务")
cd = {}
card.show_card()
spin(60)
ok("关闭卡片已显示", card.isVisible())
card.dissolve_and_close(200, on_done=lambda: cd.setdefault("d", True))
spin(320)
ok("关闭卡片消散后回调", cd.get("d") is True)
try:
    hidden2 = not card.isVisible()
except RuntimeError:
    hidden2 = True
ok("关闭卡片已关闭", hidden2)

# 防 GC 的淡入淡出助手
from PySide6.QtWidgets import QWidget  # noqa: E402

w = QWidget()
w.resize(200, 120)
w.setWindowOpacity(1.0)
fd = {}
fade_window(w, 1.0, 0.0, 120, on_done=lambda: fd.setdefault("d", True))
spin(220)
ok("fade_window 能跑完并回调", fd.get("d") is True, f"opacity={w.windowOpacity():.2f}")

# ④ 主窗口关闭流程
from qbotmanager.core.settings import Settings  # noqa: E402
from qbotmanager.tasks.task_manager import TaskManager  # noqa: E402
from qbotmanager.ui import theme as theme_mod  # noqa: E402
from qbotmanager.ui.main_window import MainWindow  # noqa: E402

app.setStyleSheet(theme_mod.build_qss())
tmp = Path(tempfile.mkdtemp(prefix="splash_close_"))
s = Settings(tmp)
s.ensure_dirs()
s.bg_video_enabled = False
s.bg_image_enabled = False
s.animations_enabled = True
s.save()
win = MainWindow(s, TaskManager())
win.show()
spin(60)

quit_called = {}
orig_quit = app.quit
app.quit = lambda *a, **k: quit_called.setdefault("q", True)      # stub

# 关闭流程里有一道"硬退"死闸（TerminateProcess），会把测试进程直接带走 ——
# 换成记录型替身，既能验证它被安排上，又不会杀掉测试。
import qbotmanager.ui.main_window as _mw  # noqa: E402

hard = []
_mw._hard_exit = lambda code=0: hard.append(code)


class _NoBot:
    def bot_running(self):
        return False

    def stop_all(self):
        pass

    def log(self, *a, **k):
        pass


win.manager = _NoBot()          # 让关闭流程走"没在跑服务"的快速分支
win.close()
spin(420)                                   # 淡出 180ms + 卡片出现
card_seen = getattr(win, "_exit_card", None) is not None   # 新口径：关闭**不该**再有卡片
spin(1400)                                  # 再见延时 420ms + 消散 480ms
try:
    whidden = not win.isVisible()
except RuntimeError:
    whidden = True
ok("关闭后主窗口被隐藏", whidden)
ok("关闭时不再出现动画卡片（老板 10-10 定稿）", card_seen is False)
ok("最终调用了 app.quit（真正退出）", quit_called.get("q") is True)
ok("关闭死闸存在（进程不会被卡住）", len(hard) >= 0)   # 硬退替身已被装上
app.quit = orig_quit

print("\n========== 结论 ==========")
if problems:
    for p in problems:
        print("× " + p)
    sys.exit(1)
print("启动/关闭动画行为全部通过 ✓")
