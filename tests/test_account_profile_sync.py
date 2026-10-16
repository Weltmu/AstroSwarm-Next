# -*- coding: utf-8 -*-
"""账号资料自动同步（老板 2026-10-06：「换完官网头像换了，但是软件客户端没变」）。

覆盖四件事：
  1) `/api/account/me` 回来的昵称与头像真的写进本机 —— 注意真实的 /me **没有 ok 字段**，
     老代码拿 `ok` 当门槛，于是每一次成功回收都被丢掉，这是"客户端一直不变"的根因；
  2) 拉失败 / 未登录一律静默：不发请求、不抛错、旧头像原样留着；
  3) 主窗口拉到新头像后，侧栏账号胶囊真的换了文字与像素（头像文件换了要重新读盘，
     不是拿着缓存的 QPixmap），设置页账号区也被通知刷新；
  4) 定时器是 60 秒一跳，关窗时停干净。

跑法（两种都行）：
  .build_venv_py314/Scripts/python.exe -m pytest tests/test_account_profile_sync.py -q
  .build_venv_py312/Scripts/python.exe tests/test_account_profile_sync.py     # 这个环境没装 pytest
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("QBM_POINTER_DIR"):
    os.environ["QBM_POINTER_DIR"] = str(Path(tempfile.mkdtemp(prefix="qbm_test_ptr_")))

import base64  # noqa: E402

try:
    import pytest
except ImportError:      # pragma: no cover - .build_venv_py312 里只有 PySide6
    pytest = None


def _fixture(fn):
    """有 pytest 就注册成 fixture；没有也照原样返回（文件末尾的直跑分支自己喂参数）。"""
    return pytest.fixture(fn) if pytest is not None else fn


# 两段内容不同的最小 JPEG（都带完整头尾，能当文件字节用）
AVATAR_A = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
AVATAR_B = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300ffd9")


def _data_url(blob: bytes, mime: str = "image/jpeg") -> str:
    return "data:%s;base64,%s" % (mime, base64.b64encode(blob).decode())


def _png_bytes(rgb, size: int = 64) -> bytes:
    """现画一张纯色 PNG：给"头像文件换了要重新读盘"那条用例用（QPixmap 读得出来的真图）。

    ⚠ 两个坑都踩过，别再改回去：
      1) 颜色必须走 QColor(r, g, b)：`QImage.fill("#3366FF")` 在 PySide6 里会直接把进程踩崩
         （字符串被当成像素值解释），不是异常是崩溃；
      2) QByteArray 必须自己拿住：`QBuffer(QByteArray())` 里那个临时对象会被回收，
         留下悬垂指针，`img.save(buf, ...)` 同样直接访问越界。
    """
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor(*rgb))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    ok = img.save(buf, "PNG")
    buf.close()
    assert ok, "PNG 生成失败"
    return bytes(data)


BLUE = (0x33, 0x66, 0xFF)
RED = (0xFF, 0x33, 0x66)


def _make_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@_fixture
def app():
    return _make_app()


@_fixture
def account_dir(monkeypatch, tmp_path):
    """把 %APPDATA% 挪到临时目录：account.json / avatar.jpg 都落在沙盒里，不碰真机数据。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from qbotmanager.core import account

    root = account.account_file().parent
    assert root == tmp_path / "QBotManager"
    return root


@_fixture
def no_hard_exit(monkeypatch):
    """把关闭窗口时的「硬退」闸门换成记录器。

    ⚠ 这里是 handover §9.3 说的"pytest 跑着跑着静默退出"的根源：
    `MainWindow.closeEvent → _begin_close` 会挂 `QTimer.singleShot(60, _hard_exit)`
    （老板定的关闭时序：关窗 60ms 后进程必须消失，机器人子进程靠作业对象回收）。
    对真实程序这是**正确行为**，但在测试里它会连 pytest 进程一起带走
    （exit code 0、没有错误输出、没有汇总行 —— 看起来像"跑到一半没了"）。
    凡是会 `win.close()` 的用例都要挂这个 fixture。
    """
    from qbotmanager.ui import main_window as mw

    calls = []
    monkeypatch.setattr(mw, "_hard_exit", lambda code=0: calls.append(code))
    return calls


def _make_window(app, tmp_path):
    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.main_window import MainWindow

    s = Settings(Path(tempfile.mkdtemp(prefix="qbm_cfg_", dir=str(tmp_path))))
    s.ensure_dirs()
    s.bg_video_enabled = False
    s.bg_image_enabled = False
    s.save()
    return MainWindow(s, TaskManager())


def _icon_digest(btn):
    """账号胶囊上那张头像的像素指纹：文件没换它就不该变，换了就必须变。

    只取几个像素点，不走 constBits()（那个 memoryview 会吊在临时 QImage 上，
    在 PySide6 里是个能直接踩崩进程的悬垂指针）。
    """
    pm = btn.icon().pixmap(36, 36)
    assert not pm.isNull()
    img = pm.toImage()
    assert not img.isNull()
    return tuple(
        img.pixelColor(x, y).getRgb()
        for x, y in ((18, 18), (14, 18), (22, 18), (18, 14), (18, 22))
    )


def _icon_center(btn):
    pm = btn.icon().pixmap(36, 36)
    assert not pm.isNull()
    return pm.toImage().pixelColor(18, 18)


# ---------------------------------------------------------------- 核心：refresh_profile
def test_refresh_profile_pulls_nickname_and_avatar(monkeypatch, account_dir):
    """真实的 /me 只回资料字段、没有 ok —— 昵称与头像都要落到本机。"""
    from qbotmanager.core import account

    account.save_account({"email": "li@astroswarm.cn", "token": "t0", "login_at": 1.0})
    assert account.save_avatar(_data_url(AVATAR_A))
    seen = {}

    def fake_me(token):
        seen["token"] = token
        # 账号服务的真实响应形状：email/nickname/signature/plan/avatar，**没有 ok**
        return {
            "email": "li@astroswarm.cn",
            "nickname": "清秋",
            "signature": "做个能用的东西",
            "plan": "permanent",
            "avatar": _data_url(AVATAR_B),
        }

    monkeypatch.setattr(account, "me", fake_me)
    out = account.refresh_profile()

    assert seen["token"] == "t0"
    assert out and out.get("nickname") == "清秋"
    assert account.load_account()["nickname"] == "清秋", "新昵称要写回本机"
    assert account.load_account()["signature"] == "做个能用的东西"
    assert account.avatar_file().read_bytes() == AVATAR_B, "新头像要落盘，界面才有得读"


def test_refresh_profile_downloads_avatar_when_it_is_a_url(monkeypatch, account_dir):
    """avatar 也可能是站内路径 / https 地址（官网那种写法）：下回来存好。"""
    from qbotmanager.core import account

    account.save_account({"email": "li@astroswarm.cn", "token": "t0"})

    class _Resp:
        def __init__(self, blob):
            self._blob = blob

        def read(self, n=-1):
            return self._blob

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        assert "/uploads/avatar.jpg" in getattr(req, "full_url", str(req))
        return _Resp(AVATAR_B)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(account, "me", lambda token: {
        "email": "li@astroswarm.cn", "nickname": "清秋", "avatar": "/uploads/avatar.jpg",
    })

    assert account.refresh_profile() is not None
    assert account.avatar_file().read_bytes() == AVATAR_B


def test_refresh_profile_is_silent_when_server_fails(monkeypatch, account_dir):
    """服务端报错 / 网络不通：旧头像原样留着，不抛错（界面上不弹红字）。"""
    from qbotmanager.core import account

    account.save_account({"email": "li@astroswarm.cn", "token": "t0"})
    account.save_avatar(_data_url(AVATAR_A))

    monkeypatch.setattr(account, "me", lambda token: {"ok": False, "detail": "HTTP 500"})
    assert account.refresh_profile() is None
    assert account.avatar_file().read_bytes() == AVATAR_A

    def boom(token):
        raise RuntimeError("连接超时")

    monkeypatch.setattr(account, "me", boom)
    assert account.refresh_profile() is None, "网络异常必须被吞掉"
    assert account.avatar_file().read_bytes() == AVATAR_A, "失败不能把旧头像弄丢"


def test_refresh_profile_sends_nothing_when_logged_out(monkeypatch, account_dir):
    """未登录 / 没 token：一次请求都不发。"""
    from qbotmanager.core import account

    def boom(*a, **k):
        raise AssertionError("未登录时不该发任何请求")

    monkeypatch.setattr(account, "me", boom)
    assert account.refresh_profile() is None, "没有账号文件时直接返回"

    account.save_account({"email": "x@y.z", "token": ""})
    assert account.refresh_profile() is None, "有账号文件但没有 token 也不发"


# ---------------------------------------------------------------- 主窗口：界面真的更新
def test_main_window_pull_updates_sidebar_avatar_and_settings(
    app, monkeypatch, tmp_path, account_dir, no_hard_exit,
):
    """拉到新昵称/头像后：侧栏胶囊立刻换字换图（重新读盘），设置页账号区被通知刷新。"""
    from qbotmanager.core import account
    from qbotmanager.ui.main_window import ACCOUNT_PROFILE_POLL_MS

    account.save_account({"email": "li@astroswarm.cn", "token": "t0", "nickname": "旧名字"})
    assert account.save_avatar(_data_url(_png_bytes(BLUE), "image/png"))

    win = _make_window(app, tmp_path)
    try:
        assert ACCOUNT_PROFILE_POLL_MS == 60 * 1000
        assert win._account_profile_timer.interval() == 60 * 1000, "60 秒一跳"
        assert win._account_profile_timer.isActive()

        btn = win._account_buttons[0]
        before_text = btn.text()
        before_pixels = _icon_digest(btn)
        assert before_text == "旧名字"

        settings_page = win.pages[win._page_index["设置"]]
        refreshed = []
        original = settings_page._refresh_account_status
        settings_page._refresh_account_status = lambda: (refreshed.append(1), original())[1]

        def fake_me(token):
            return {
                "email": "li@astroswarm.cn", "nickname": "新名字",
                "avatar": _data_url(_png_bytes(RED), "image/png"),
            }

        monkeypatch.setattr(account, "me", fake_me)
        win._pull_account_profile(force=True)          # 后台线程，别阻塞界面

        deadline = time.time() + 10
        while time.time() < deadline and btn.text() != "新名字":
            app.processEvents()
            time.sleep(0.05)
        app.processEvents()

        assert btn.text() == "新名字", "侧栏账号胶囊的昵称要跟着换"
        assert _icon_digest(btn) != before_pixels, (
            "头像像素必须换（文件换了要重新读盘，不能用缓存的 QPixmap）")
        center = _icon_center(btn)
        assert center.red() > 150 and center.green() < 120, (
            "胶囊上的头像应该是刚换上去的那张（红），拿到旧的就是读了缓存的 QPixmap：%r"
            % (center.getRgb(),))
        assert account.avatar_file().read_bytes() == _png_bytes(RED), "新头像已落盘"
        assert refreshed, "设置页账号区要被通知刷新"
    finally:
        win._stop_profile_timer()
        win.close()


def test_main_window_pull_sends_nothing_when_logged_out(app, monkeypatch, tmp_path, account_dir,
                                                        no_hard_exit):
    """主窗口那一跳同理：未登录时一次都不打（离线也不打）。"""
    from qbotmanager.core import account

    called = []
    monkeypatch.setattr(account, "refresh_profile", lambda acc=None: called.append(1))

    win = _make_window(app, tmp_path)
    try:
        assert account.load_account() is None, "这个用例里本来就没登录"
        win._pull_account_profile(force=True)
        win._pull_account_profile()
        time.sleep(0.4)
        assert called == [], "未登录不该发请求"
    finally:
        win._stop_profile_timer()
        win.close()


def test_profile_timer_stops_on_close(app, monkeypatch, tmp_path, account_dir, no_hard_exit):
    """关窗口时把定时器停干净：退出后不再有任何一次自动同步。"""
    win = _make_window(app, tmp_path)
    assert win._account_profile_timer.isActive()
    win.close()
    app.processEvents()
    assert not win._account_profile_timer.isActive(), "关窗后定时器必须停"
    win._stop_profile_timer()                      # 再停一次也不能炸


# ---------------------------------------------------------------- 没有 pytest 时的直跑入口
_MISSING = object()


class _MiniMonkeyPatch:
    """pytest 的 monkeypatch 最小替身：只有这个文件用到的 setattr / setenv / 自动还原。"""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=_MISSING):
        import importlib
        if isinstance(target, str):
            # 字符串形式（pytest 那种 "模块.属性"）：第二个位置参数才是新值
            mod_name, _, attr = target.rpartition(".")
            target, name, value = importlib.import_module(mod_name), attr, name
        old = getattr(target, name, _MISSING)
        self._undo.append(("attr", (target, name, old)))
        setattr(target, name, value)

    def setenv(self, name, value):
        old = os.environ.get(name, _MISSING)
        self._undo.append(("env", (name, old)))
        os.environ[name] = str(value)

    def undo(self):
        for kind, payload in reversed(self._undo):
            if kind == "env":
                name, old = payload
                if old is _MISSING:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
                continue
            target, name, old = payload
            if old is _MISSING:
                try:
                    delattr(target, name)
                except AttributeError:
                    pass
            else:
                setattr(target, name, old)
        self._undo.clear()


def _run_standalone():
    import inspect
    import logging

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)   # 直跑时把静默吞掉的日志也打出来
    app = _make_app()
    # 直跑同样要把「硬退」闸门换掉，否则跑到关窗那条整个进程就被带走了（见 no_hard_exit）
    try:
        from qbotmanager.ui import main_window as _mw
        _mw._hard_exit = lambda code=0: None
    except Exception:  # noqa: BLE001 —— 拿不到就按原样跑，最坏是这条之后停住
        pass
    names = [n for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    bad = 0
    for name in names:
        mp = _MiniMonkeyPatch()
        tmp = Path(tempfile.mkdtemp(prefix="qbm_selftest_"))
        kwargs = {}
        params = inspect.signature(globals()[name]).parameters
        if "app" in params:
            kwargs["app"] = app
        if "monkeypatch" in params:
            kwargs["monkeypatch"] = mp
        if "tmp_path" in params:
            kwargs["tmp_path"] = tmp
        if "account_dir" in params:
            mp.setenv("APPDATA", str(tmp))
            kwargs["account_dir"] = tmp / "QBotManager"
        try:
            globals()[name](**kwargs)
            print("  [OK]   %s" % name)
        except Exception:  # noqa: BLE001 —— 直跑就是为了把失败原样打出来
            bad += 1
            import traceback
            print("  [FAIL] %s" % name)
            traceback.print_exc()
        finally:
            mp.undo()
    print("直跑结束：%d 条，失败 %d 条" % (len(names), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
