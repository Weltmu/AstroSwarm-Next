# -*- coding: utf-8 -*-
r"""给 tests/ 加一道全局隔离：任何测试都不许写到真实的 %APPDATA%\QBotManager。

为什么（2026-10-10 两次事故）：
  · 我第一次跑 UI 冒烟没隔离，把老板真实登录的 account.json 覆盖成测试号；
  · 15:59 又发生一次（Linux 侧用例在本机 pytest 跑，account.save_account 直接落在
    真实的 %APPDATA%\QBotManager\account.json）。
手动逐个改测试不可靠，所以在 conftest 里做**会话级 autouse 隔离**：
  APPDATA / LOCALAPPDATA / ASTROSWARM_DATA_HOME / XDG_* 全部指向临时目录，
  测试想怎么写都碰不到老板的真实数据。测试内部再用 monkeypatch 覆盖也照样生效。
"""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _install_test_stubs():
    """把"这套测试环境缺的第三方模块"补成**完整**替身（导入期就装好）。

    2026-10-15：各测试文件自己 `sys.modules[...] = 替身` 的做法一直在互相踩 ——
    谁先跑谁说了算，后跑的拿到一个**残缺**替身（只有 get_plugin_config_file、
    没有 get_plugin_data_file），于是 `AttributeError: module
    'nonebot_plugin_localstore' has no attribute ...`，而单跑那个文件却是绿的。
    这里在 conftest 里（任何测试模块导入**之前**）装一份完整的：
      · 后续文件若用 `if ... not in sys.modules` 判断，就会直接用这份；
      · 真环境装了真模块时一个字节都不动它。
    """
    ls = sys.modules.get("nonebot_plugin_localstore")
    if ls is None:
        try:
            import nonebot_plugin_localstore as ls  # noqa: F401
        except Exception:  # noqa: BLE001 —— 构建/单测环境没装，属预期
            ls = types.ModuleType("nonebot_plugin_localstore")
            sys.modules["nonebot_plugin_localstore"] = ls
    tmp = Path(tempfile.gettempdir()) / "qbm_test_localstore"
    tmp.mkdir(parents=True, exist_ok=True)
    if not hasattr(ls, "get_plugin_config_file"):
        ls.get_plugin_config_file = lambda name: tmp / ("cfg_" + str(name))
    if not hasattr(ls, "get_plugin_data_file"):
        ls.get_plugin_data_file = lambda name: tmp / ("data_" + str(name))
    if not hasattr(ls, "get_plugin_data_dir"):
        ls.get_plugin_data_dir = lambda: tmp
    # nonebot 本身：真装了就不动；替身则补齐被各文件用到的属性
    nb = sys.modules.get("nonebot")
    if nb is not None:
        if not hasattr(nb, "get_bots"):
            nb.get_bots = lambda: {}
        if not hasattr(nb, "logger"):
            import logging

            nb.logger = logging.getLogger("nonebot")


def _complete_stubs():
    """补全**当前** sys.modules 里那份替身缺的属性。

    每个测试文件都会自己塞一份替身（各自指向不同临时目录，这是故意的），
    谁先跑谁说了算 —— 后跑的拿到的是别人那份（常常缺 `get_plugin_data_file`
    之类的属性），于是 `AttributeError`，而单跑那个文件却是绿的。
    conftest 在每个用例开始前把缺的属性补齐，一次修掉这一类问题（12 个文件不用逐个改）。
    真模块（装了 nonebot-plugin-localstore 的装机）不会缺属性 → 这里是空操作。
    """
    ls = sys.modules.get("nonebot_plugin_localstore")
    if ls is not None:
        tmp = Path(tempfile.gettempdir()) / "qbm_test_localstore"
        tmp.mkdir(parents=True, exist_ok=True)
        if not hasattr(ls, "get_plugin_config_file"):
            ls.get_plugin_config_file = lambda name: tmp / ("cfg_" + str(name))
        if not hasattr(ls, "get_plugin_data_file"):
            ls.get_plugin_data_file = lambda name: tmp / ("data_" + str(name))
        if not hasattr(ls, "get_plugin_data_dir"):
            ls.get_plugin_data_dir = lambda: tmp
    nb = sys.modules.get("nonebot")
    if nb is not None:
        if not hasattr(nb, "get_bots"):
            nb.get_bots = lambda: {}
        if not hasattr(nb, "logger"):
            import logging

            nb.logger = logging.getLogger("nonebot")


_install_test_stubs()


@pytest.fixture(autouse=True)
def _stub_sanity():
    """每个用例前把替身补齐（见 _complete_stubs 的说明）。"""
    _complete_stubs()
    yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_data():
    """把"用户数据目录"整体搬到临时目录（会话开始前就生效）。"""
    tmp = Path(tempfile.mkdtemp(prefix="qbm_test_data_"))
    (tmp / "QBotManager").mkdir(parents=True, exist_ok=True)
    keys = ("APPDATA", "LOCALAPPDATA", "ASTROSWARM_DATA_HOME",
            "XDG_DATA_HOME", "XDG_CONFIG_HOME")
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ[k] = str(tmp)
    yield tmp
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session", autouse=True)
def _no_main_window_hard_exit():
    """关窗的「硬退」闸门在测试里换成空操作（2026-10-16）。

    真实程序里它是设计的一部分：`MainWindow.closeEvent → _begin_close` 挂一个
    60ms 后的 `_hard_exit(0)`，到点直接杀进程（机器人子进程靠作业对象一起回收）。
    但在 pytest 里，这个定时器往往等到**下一条用例**才触发 —— 整个测试进程被
    带走：没有错误、没有汇总行，看起来只是"跑到一半没了"
    （handover §9.3 说「两个文件会让 pytest 静默退出」，根因就是它；
    test_account_profile_sync / test_ui_smoke 都中）。

    这里会话级替换掉：所有 close 窗口的用例都能跑完、关窗后的断言照常成立。
    """
    mw = None
    original = None
    try:
        from qbotmanager.ui import main_window as mw  # noqa: PLC0415

        original = getattr(mw, "_hard_exit", None)
        mw._hard_exit = lambda code=0: None
    except Exception:  # noqa: BLE001 —— 没有 PySide6 的环境跳过（用例本来就跑不了 GUI）
        mw = None
    yield
    if mw is not None and original is not None:
        try:
            mw._hard_exit = original
        except Exception:  # noqa: BLE001
            pass
