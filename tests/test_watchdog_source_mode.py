# -*- coding: utf-8 -*-
"""看门狗认"源码直跑"（2026-10-13 老板："后台时不时弹星巢已经在运行"）。

根因：源码版客户端是 pythonw.exe，老逻辑只认安装目录里的 AstroSwarm.exe →
每 5 分钟判一次"客户端未运行"，反复去拉安装版 → 撞单实例 → 反复弹提示。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_watchdog():
    spec = importlib.util.spec_from_file_location("watchdog_srcmode",
                                                  ROOT / "tools" / "watchdog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_source_run_app_is_recognized():
    wd = _load_watchdog()
    src = (123, r"C:\Python312\pythonw.exe", "pythonw",
           r'"C:\Python312\pythonw.exe" "D:\ai\QBotManager\run_source.pyw"')
    assert wd._is_source_app(src) is True
    # 安装版 exe 不是"源码版"
    assert wd._is_source_app(
        (1, r"D:\AstroSwarm 星群\AstroSwarm.exe", "AstroSwarm", "")) is False
    # 编辑器里打开过这个文件（argv 里带 run_source.pyw）不能算客户端在跑
    assert wd._is_source_app(
        (2, r"C:\Code.exe", "Code", r"C:\Code.exe D:\ai\QBotManager\run_source.pyw")) is False


def test_installed_app_must_live_in_install_dir():
    wd = _load_watchdog()
    install = Path(r"D:\AstroSwarm 星群")
    assert wd._is_installed_app(
        (1, r"D:\AstroSwarm 星群\AstroSwarm.exe", "AstroSwarm", ""), install) is True
    assert wd._is_installed_app(
        (2, r"D:\other\AstroSwarm.exe", "AstroSwarm", ""), install) is False


def test_app_launch_prefers_source_when_configured(tmp_path):
    wd = _load_watchdog()
    py = tmp_path / "pythonw.exe"
    script = tmp_path / "run_source.pyw"
    py.write_text("x", encoding="utf-8")
    script.write_text("x", encoding="utf-8")
    p = wd.launch_mode_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mode": "source", "launcher": [str(py), str(script)]}),
                 encoding="utf-8")
    assert wd.app_launch_cmd(tmp_path) == [str(py), str(script)]
    # 启动文件不在了 → 退化回安装版 exe（重装/挪目录也不会把看门狗搞瘸）
    script.unlink()
    assert wd.app_launch_cmd(tmp_path) == [str(tmp_path / "AstroSwarm.exe")]


def test_app_launch_defaults_to_installed_exe(tmp_path):
    wd = _load_watchdog()
    assert wd.app_launch_cmd(tmp_path) == [str(tmp_path / "AstroSwarm.exe")]


def _write_source_pref(wd, tmp_path):
    py = tmp_path / "pythonw.exe"
    script = tmp_path / "run_source.pyw"
    py.write_text("x", encoding="utf-8")
    script.write_text("x", encoding="utf-8")
    p = wd.launch_mode_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mode": "source", "launcher": [str(py), str(script)]}),
                 encoding="utf-8")
    return py, script


def test_bot_launch_follows_source_preference(tmp_path):
    """2026-10-15 修：**机器人**启动也必须跟客户端一样尊重"源码直跑"。

    旧实现写死 `AstroSwarm.exe --cli start`（= 打包分支）→
    `ensure_agent_runtime()` 从 `_internal/appr/qbotmanager` 取 core/ 同步过去，
    于是每自动拉起一次机器人，就把源码版刚改好的工坊/agent 代码覆盖回
    安装版的旧副本（copytree 保留源 mtime，时间戳上完全看不出来）。
    """
    wd = _load_watchdog()
    py, script = _write_source_pref(wd, tmp_path)
    py_cli = tmp_path / "python.exe"
    py_cli.write_text("x", encoding="utf-8")

    cmd, cwd, mode = wd.bot_launch_cmd(tmp_path)
    # 源码模式用 python.exe（不是 pythonw）—— CLI 的 print 要能被捕获回日志
    assert cmd == [str(py_cli), "-X", "utf8", str(script), "--cli", "start"]
    assert cwd == str(tmp_path)
    assert mode == "源码直跑"


def test_bot_launch_without_source_pref_uses_installed_exe(tmp_path):
    wd = _load_watchdog()
    cmd, cwd, mode = wd.bot_launch_cmd(tmp_path)
    assert cmd == [str(tmp_path / "AstroSwarm.exe"), "--cli", "start"]
    assert cwd == str(tmp_path)
    assert mode == "安装版"


def test_bot_launch_degrades_when_launcher_missing(tmp_path):
    """源码启动文件被删/挪走后必须退回安装版，不能把看门狗搞瘸。"""
    wd = _load_watchdog()
    _py, script = _write_source_pref(wd, tmp_path)
    script.unlink()
    _cmd, _cwd, mode = wd.bot_launch_cmd(tmp_path)
    assert mode == "安装版"


def test_bot_launch_uses_pythonw_when_python_exe_absent(tmp_path):
    """只有 pythonw.exe（没有 python.exe）时退回 pythonw，别直接崩。"""
    wd = _load_watchdog()
    py, _script = _write_source_pref(wd, tmp_path)
    cmd, _cwd, mode = wd.bot_launch_cmd(tmp_path)
    assert cmd[0] == str(py)
    assert mode == "源码直跑"
