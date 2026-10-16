#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""源码方式启动星巢（不用打包，改完代码直接生效）。

用法：
  · 双击桌面快捷方式「星巢 Nest（源码试用）」
  · 或命令行： <venv>\pythonw.exe D:\ai\QBotManager\run_source.pyw
  · 也支持 CLI： <venv>\pythonw.exe run_source.pyw --cli info

为什么能这样跑：
  · 程序本身是 Python + PySide6，`.build_venv_py312` 里已经装好依赖；
  · 数据目录走"全局指针"（仍指向 D:\AstroSwarm 星群），所以配置/机器人/账号都是同一套；
  · 用 pythonw 启动 = 无黑框；出错会写日志 + 弹一个能看懂的框（不至于静默消失）。
注意：
  · 它和已安装版**不能同时开**（单实例互斥）——试用前先把已装的星巢关掉；
  · 源码版启动/重启机器人时，会用它自己的插件副本覆盖 `机器人\bot\src\plugins\`，
    所以试用前请先备份那一份（tools\backup_bot_plugins.py 已备好）。
"""
import faulthandler
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent
SRC = REPO / "src"
LOG = Path(tempfile.gettempdir()) / "nest-source.log"


def _log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _alert(title: str, text: str) -> None:
    """没有控制台时也能让人看到错误。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text[:1500], title, 0x10)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    # 先把日志写下去：pythonw（无控制台）跑的时候，万一后面哪一步抛异常，
    # 这份日志就是唯一现场证据。2026-10-11 实测：快捷键用 pythonw 启动时
    # 连日志都没生成 —— 元凶是下面的 faulthandler.enable() 在无控制台时抛异常。
    _log(f"启动：python={sys.executable} argv={sys.argv[1:]}")
    try:
        faulthandler.enable()
    except Exception as e:  # noqa: BLE001 —— pythonw 没有控制台时可能失败，绝不能挡住启动
        _log(f"faulthandler 启用失败（可忽略）：{type(e).__name__}: {e}")
    if not SRC.is_dir():
        _alert("星巢源码启动失败", f"找不到源码目录：{SRC}")
        return 2
    sys.path.insert(0, str(SRC))
    os.environ.setdefault("QBM_SOURCE_RUN", "1")       # 便于代码里区分"源码模式"
    try:
        from qbotmanager.app import main as app_main

        code = app_main() or 0
        _log(f"退出：{code}")
        return int(code)
    except SystemExit as e:                            # CLI 正常退出
        _log(f"SystemExit：{e}")
        return int(getattr(e, "code", 0) or 0)
    except BaseException:                              # noqa: BLE001
        detail = traceback.format_exc()
        _log("崩溃：\n" + detail)
        _alert("星巢源码启动失败",
               "启动时出错，详细信息已写入：\n"
               f"{LOG}\n\n" + detail[-900:])
        return 1


if __name__ == "__main__":
    sys.exit(main())
