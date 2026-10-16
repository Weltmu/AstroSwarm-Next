#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""星巢看门狗：保证"程序在跑、机器人在线"，长期无人值守也不会静默躺平。

为什么需要它（2026-10-11 老板要部署到别人电脑上，问"能不能长久运行"）：
  · 程序里**没有**机器人崩溃自动拉起的机制；`auto_start_services` / `auto_launch_on_boot`
    默认还是关的；
  · 机器人（NoneBot）是独立进程，一旦半夜崩了、或网络抖动导致掉线，
    没有人会去点"重启" → 第二天发现一整天没回消息。
本脚本由"任务计划程序"每 N 分钟跑一次（默认 5 分钟），只做三件事：
  1. 客户端没在跑 → 启动它；
  2. 客户端在跑、机器人没在跑 → 拉起机器人（见 `bot_launch_cmd`：源码直跑偏好下
     走 `run_source.pyw --cli start`，否则走 `AstroSwarm.exe --cli start`）；
  3. 每次动作都写日志；同一小时内**机器人重启**次数超限就只记录、不重启（防疯狂重启）。

用法：
  python tools/watchdog.py --check                 # 只看状态，不动手
  python tools/watchdog.py                         # 体检 + 需要时拉起
  python tools/watchdog.py --install               # 装成计划任务（每 5 分钟，开机自启）
  python tools/watchdog.py --uninstall             # 卸载计划任务
  python tools/watchdog.py --install-dir "D:\xxx"  # 指定安装目录（默认自动找）
  python tools/watchdog.py --set-source "<pythonw.exe>" "<run_source.pyw>"
                                                   # 客户端改用"源码直跑"启动（记住偏好）
  python tools/watchdog.py --set-installed         # 改回安装版 exe 启动

2026-10-13 修（老板："后台时不时弹出窗口显示星巢已经在运行"）：
  源码直跑时客户端是 pythonw.exe（不在安装目录里），老逻辑只认 AstroSwarm.exe →
  每 5 分钟判一次"客户端未运行"→ 反复去拉安装版 → 撞单实例 → 后台反复弹提示。
  现在：命令行里带 run_source.pyw 的进程也算"客户端在跑"；真需要拉起时，按
  logs/launch_mode.json 记住的方式启动（源码 or 安装版，默认安装版）。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TASK_NAME = "AstroSwarm-Watchdog"
MAX_ACTIONS_PER_HOUR = 4          # 一小时内最多动作几次（防疯狂重启）
DEFAULT_INSTALL = Path(r"D:\AstroSwarm 星群")
APP_EXE = "AstroSwarm.exe"
BOT_HINT = "机器人"                # 机器人 python 在这个子目录下
SOURCE_MARK = "run_source.pyw"    # 源码直跑的启动脚本名（认进程用）
LAUNCH_MODE_FILE = "launch_mode.json"   # logs/ 下记住"客户端怎么启"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------ 基础工具
def log_path(install: Path) -> Path:
    d = install / "logs"
    return d / "watchdog.log"


def log(install: Path, msg: str, quiet: bool = False) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    if not quiet:
        print(line, flush=True)
    try:
        p = log_path(install)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file() and p.stat().st_size > 2 * 1024 * 1024:      # 简单轮转，别长太大
            p.replace(p.with_suffix(".log.1"))
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001 —— 日志失败绝不能影响主流程
        pass


def find_install(explicit: str | None) -> Path | None:
    """找安装目录：显式参数 > 环境变量 > 默认可疑路径 > 注册表 Run 键。"""
    if explicit:
        p = Path(explicit)
        return p if (p / APP_EXE).is_file() else None
    env = os.environ.get("ASTROSWARM_INSTALL")
    if env and (Path(env) / APP_EXE).is_file():
        return Path(env)
    if (DEFAULT_INSTALL / APP_EXE).is_file():
        return DEFAULT_INSTALL
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                if "AstroSwarm" in name or "星巢" in name:
                    exe = Path(str(value).strip('"'))
                    if exe.is_file():
                        return exe.parent
    except Exception:  # noqa: BLE001
        pass
    return None


def process_snapshot() -> list:
    """列出进程的 (pid, exe路径, 进程名, 命令行)。"""
    # 路径走 Get-Process.Path：CIM 的 ExecutablePath 对本用户进程常返回 null，会误判"未运行"
    # 关键：先把控制台输出编码设成 UTF-8。否则中文安装路径（如 D:\AstroSwarm 星群）会按 GBK
    # 解出来变成乱码，路径匹配全失效 ——"客户端在跑也被判成没跑"（2026-10-11 实测踩到）。
    ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
          "Get-Process | Select-Object Id,ProcessName,Path | ConvertTo-Json -Compress")
    paths = {}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW)
        data = json.loads(r.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for x in data:
            paths[int(x.get("Id") or 0)] = (str(x.get("Path") or ""),
                                            str(x.get("ProcessName") or ""))
    except Exception:  # noqa: BLE001
        paths = {}
    # 命令行另取一趟（CIM 的 CommandLine 可用；用来认"源码直跑"的 pythonw 实例）
    cmds = {}
    cmd_ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine "
              "| ConvertTo-Json -Compress")
    try:
        r2 = subprocess.run(["powershell", "-NoProfile", "-Command", cmd_ps],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW)
        data2 = json.loads(r2.stdout or "[]")
        if isinstance(data2, dict):
            data2 = [data2]
        for x in data2:
            cmds[int(x.get("ProcessId") or 0)] = str(x.get("CommandLine") or "")
    except Exception:  # noqa: BLE001
        cmds = {}
    return [(pid, path, name, cmds.get(pid, "")) for pid, (path, name) in paths.items()]


def _is_installed_app(proc, install: Path) -> bool:
    """安装版客户端：exe 叫 AstroSwarm.exe，且路径在安装目录里。"""
    path = str(proc[1] or "") if len(proc) > 1 else ""
    return bool(path) and Path(path).name.lower() == APP_EXE.lower() \
        and str(install).lower() in path.lower()


def _is_source_app(proc) -> bool:
    """源码直跑客户端：进程名是 python(w).exe，命令行里认得出 run_source.pyw。

    2026-10-13 修：不认它的话，看门狗每 5 分钟就以为"客户端没在跑"，
    反复去拉安装版 exe → 撞单实例 → 后台反复弹「星巢已经在运行」。
    """
    name = str(proc[2] or "").lower() if len(proc) > 2 else ""
    cmd = str(proc[3] or "") if len(proc) > 3 else ""
    return "python" in name and SOURCE_MARK.lower() in cmd.lower()


def state(install: Path) -> dict:
    procs = process_snapshot()
    app = [p for p in procs
           if _is_installed_app(p, install) or _is_source_app(p)]
    bot = [p for p in procs if p[1] and "python" in Path(p[1]).name.lower()
           and str(install / BOT_HINT).lower() in p[1].lower()]
    return {"app": app, "bot": bot, "total": len(procs)}


def launch_mode_path(install: Path) -> Path:
    return install / "logs" / LAUNCH_MODE_FILE


def load_source_launcher(install: Path) -> list:
    """logs/launch_mode.json 里记住的"源码直跑"启动命令；没配/文件没了返回 []。"""
    try:
        d = json.loads(launch_mode_path(install).read_text(encoding="utf-8"))
        if str(d.get("mode")) != "source":
            return []
        cmd = [str(x) for x in (d.get("launcher") or []) if str(x).strip()]
        if len(cmd) >= 2 and Path(cmd[0]).is_file() and Path(cmd[1]).is_file():
            return cmd
    except Exception:  # noqa: BLE001
        pass
    return []


def app_launch_cmd(install: Path) -> list:
    """客户端该用什么命令拉起：优先源码直跑（如果记住过），否则安装版 exe。"""
    return load_source_launcher(install) or [str(install / APP_EXE)]


def start_app(install: Path) -> bool:
    try:
        subprocess.Popen(app_launch_cmd(install), cwd=str(install),
                         creationflags=CREATE_NO_WINDOW)
        return True
    except Exception as e:  # noqa: BLE001
        log(install, f"启动客户端失败：{type(e).__name__} {e}")
        return False


def bot_launch_cmd(install: Path) -> tuple[list, str, str]:
    """机器人该用什么命令拉起 —— **和客户端一样尊重"源码直跑"偏好**。

    ⚠ 2026-10-15 修（症状：源码版里改好的 core/，在机器人上"没生效"）：
    机器人启动时，客户端代码会跑 `ensure_agent_runtime()`，把整个 `core/` 子包同步到
    `机器人\\bot\\qbotmanager`（AI 插件靠 `from qbotmanager.core...` 导入它）。
    问题是这个同步**分两个来源**：
      · 源码直跑 → 取仓库 `src/qbotmanager`（永远最新）；
      · 安装版 exe → 取打包时烘焙进 `_internal\\appr\\qbotmanager` 的副本（只在打包时刷新）。
    而本函数的旧实现**写死**走 `AstroSwarm.exe --cli start`，即永远走安装版分支。
    后果：源码版里刚改好的工坊生成器 / agent 工具，**每被自动拉起一次机器人就被
    覆盖回安装版的旧代码**；且 copytree 保留源文件 mtime，时间戳上完全看不出来。

    返回 (命令, 工作目录, 模式说明)。
    """
    src = load_source_launcher(install)          # [pythonw.exe, run_source.pyw]
    if src:
        py = Path(src[0])
        # CLI 要拿输出：优先 python.exe（pythonw 没有控制台，捕获不到 print）
        py_cli = py.with_name("python.exe")
        launcher = py_cli if py_cli.is_file() else py
        script = Path(src[1])
        # 工作目录跟着 run_source.pyw 走，与桌面快捷方式「星巢 Nest（源码试用）」一致
        return ([str(launcher), "-X", "utf8", str(script), "--cli", "start"],
                str(script.parent), "源码直跑")
    return ([str(install / APP_EXE), "--cli", "start"], str(install), "安装版")


def start_bot(install: Path) -> bool:
    cmd, cwd, mode = bot_launch_cmd(install)
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, creationflags=CREATE_NO_WINDOW)
        ok = r.returncode == 0
        log(install, f"拉起机器人（{mode}）：退出码={r.returncode} {(r.stdout or '').strip()[-80:]}")
        return ok
    except Exception as e:  # noqa: BLE001
        log(install, f"拉起机器人失败（{mode}）：{type(e).__name__} {e}")
        return False


def recent_restart_count(install: Path, now: float | None = None) -> int:
    """最近一小时里"拉起机器人"的动作次数（防疯狂重启闸门用）。

    只算机器人动作。**别把客户端行（"客户端未运行 → 启动它"）算进来**：
    2026-10-12 实测，客户端反复启停攒了 11 条 → 机器人重启预算被耗光 →
    机器人死了一直拉不起来（"已动作 11 次 → 本次不重启"）。
    """
    recent = 0
    cutoff = (time.time() if now is None else now) - 3600
    try:
        p = log_path(install)
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
                try:
                    ts = time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
                except Exception:  # noqa: BLE001
                    continue
                if ts >= cutoff and "拉起机器人" in line:
                    recent += 1
    except Exception:  # noqa: BLE001
        pass
    return recent


# ------------------------------------------------------------------ 计划任务
def install_task(install: Path) -> int:
    python = Path(sys.executable)
    # 优先用 pythonw（无黑框）；没有就用当前 python
    exe = python.with_name("pythonw.exe") if python.with_name("pythonw.exe").is_file() else python
    script = Path(__file__).resolve()
    ps = f'''
$ErrorActionPreference = "Stop"
$act = New-ScheduledTaskAction -Execute "{exe}" -Argument '-X utf8 "{script}" --install-dir "{install}"' -WorkingDirectory "{install}"
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$rep = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "{TASK_NAME}" -Action $act -Trigger $trg,$rep -Settings $set -Force | Out-Null
(Get-ScheduledTask -TaskName "{TASK_NAME}").State
'''
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    print((r.stdout or "").strip() or (r.stderr or "").strip()[-300:])
    return r.returncode


def uninstall_task() -> int:
    ps = (f'Unregister-ScheduledTask -TaskName "{TASK_NAME}" -Confirm:$false -ErrorAction SilentlyContinue;'
          f'"done"')
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    print("已卸载计划任务" if r.returncode == 0 else (r.stderr or "")[-300:])
    return r.returncode


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="星巢看门狗：保证客户端与机器人在线")
    ap.add_argument("--check", action="store_true", help="只体检，不动手")
    ap.add_argument("--install", action="store_true", help="装成计划任务（每 5 分钟）")
    ap.add_argument("--uninstall", action="store_true", help="卸载计划任务")
    ap.add_argument("--install-dir", default=None, help="安装目录（默认自动查找）")
    ap.add_argument("--quiet", action="store_true", help="不打印，只写日志（计划任务里用）")
    ap.add_argument("--set-source", nargs=2, metavar=("PYTHONW", "RUN_SOURCE_PY"),
                    help="记住：客户端改用源码直跑启动（写 logs/launch_mode.json）")
    ap.add_argument("--set-installed", action="store_true",
                    help="恢复用安装版 exe 启动（清掉源码直跑偏好）")
    args = ap.parse_args()

    if args.uninstall:
        return uninstall_task()

    install = find_install(args.install_dir)
    if install is None:
        print("没找到星巢安装目录（客户端 exe）。用 --install-dir 指定，或设环境变量 ASTROSWARM_INSTALL。")
        return 2

    if args.set_source:
        py, script = args.set_source
        if not (Path(py).is_file() and Path(script).is_file()):
            print("路径不存在：", py, script)
            return 2
        p = launch_mode_path(install)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"mode": "source",
                                 "launcher": [str(Path(py)), str(Path(script))],
                                 "updated_at": int(time.time())},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        print("已记住：客户端用源码直跑启动（", py, "→", script, "）")
        return 0
    if args.set_installed:
        try:
            launch_mode_path(install).unlink()
        except FileNotFoundError:
            pass
        print("已恢复：客户端用安装版 exe 启动")
        return 0

    if args.install:
        return install_task(install)

    st = state(install)
    log(install, f"体检：客户端 {'在跑' if st['app'] else '未运行'} | "
                 f"机器人 {'在跑' if st['bot'] else '未运行'}", quiet=args.quiet)

    if args.check:
        return 0 if (st["app"] and st["bot"]) else 1

    acted = False
    if not st["app"]:
        log(install, "客户端未运行 → 启动它", quiet=args.quiet)
        acted = start_app(install)
        time.sleep(20)                      # 给它起来的时间，机器人随后由下一次体检处理
    elif not st["bot"]:
        # 防疯狂重启：读日志算最近一小时"机器人重启"次数（客户端启动不计入）
        recent = recent_restart_count(install)
        if recent >= MAX_ACTIONS_PER_HOUR:
            log(install, f"机器人未运行，但一小时内已动作 {recent} 次 → 本次不重启（防疯狂重启）",
                quiet=args.quiet)
        else:
            log(install, "机器人未运行 → 拉起", quiet=args.quiet)
            acted = start_bot(install)

    return 0 if acted or (st["app"] and st["bot"]) else 1


if __name__ == "__main__":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)   # 中文别乱码
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
