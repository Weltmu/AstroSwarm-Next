# -*- coding: utf-8 -*-
"""站内更新：下载官网安装包 → 校验 sha256 → 静默覆盖安装 → 自动重启。

为什么是「重跑安装包」而不是「自己解压覆盖」：
程序目录里有 onedir 的 _internal、离线载荷、uninstall.exe、快捷方式登记，
自己覆盖会把这些拆散；官方安装包本来就是全量覆盖，且已经处理过这些细节。

三条必须守住的规矩：
1. 安装目录要显式传 /DIR 指回当前目录 —— 安装器 UsePreviousAppDir=no，
   不指定就装到默认目录，等于机器上多出一份「假的更新成功」。
2. 不传 --cli deploy —— 安装器靠 %APPDATA%\\QBotManager\\settings.json 这个
   安装根指针判断「已部署过」，指点存在时 ShouldDeployNow 为假，静默安装
   不会重跑部署，用户的机器人目录 / 配置 / 插件原样保留。
3. 先停服务再退出进程，最后自动把程序重新拉起来 —— 用户看到的只有一次重启。

这个模块只负责「下载 + 校验 + 起安装器」，界面提示交给调用方。
"""
import hashlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..constants import APP_NAME

CHUNK = 256 * 1024
TIMEOUT = 30
KEEP_DAYS = 7


def install_dir() -> Path:
    """当前程序所在目录（安装器 {app}）。打包后是 exe 的父目录，开发态是仓库根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def exe_path() -> Path:
    """重启时要用主程序路径；开发态下没有 exe，返回空路径。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path()


def cache_root() -> Path:
    base = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())
    return Path(base) / "AstroSwarm_update"


# 「正在安装更新」锁：apply() 写下它、安装脚本收尾时删掉。新启动的程序看到
# 「锁在 + 还有 AstroSwarm_Setup* 进程」就先别开 —— 2026-10-12 老板实机踩到：
# 两个安装器并发时，第一个装完把程序拉起来，第二个还在覆盖文件 → Qt DLL 崩。
LOCK_NAME = "installing.lock"


def lock_path() -> Path:
    """「正在安装更新」锁文件的位置（和安装包同一个缓存目录）。"""
    return cache_root() / LOCK_NAME


def _write_lock() -> None:
    try:
        lock = lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass          # 锁写不了只是少一层防护，绝不能挡住更新


def _clear_lock() -> None:
    try:
        lock_path().unlink()
    except OSError:
        pass


def _setup_running() -> bool:
    """有没有 AstroSwarm_Setup* 进程在跑。查不到一律按「没有」处理（绝不无限等）。"""
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "if (Get-Process -Name 'AstroSwarm_Setup*' -ErrorAction SilentlyContinue) "
             "{ exit 1 } else { exit 0 }"],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 1
    except Exception:  # noqa: BLE001 —— 探不到就当没有，别把程序挡在门外
        return False


def update_pending() -> bool:
    """安装器是不是还在覆盖文件（给新启动的程序用：还在装就先别开）。"""
    if not lock_path().exists():
        return False
    return _setup_running()


def warn_update_pending() -> None:
    """「正在安装更新」一句话提示（不依赖 Qt：main.py 导入界面之前就能用）。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "星巢正在安装更新，装完会自动打开。\n如果稍后没有自动出现，请在桌面重新打开一次。",
            "星巢 Nest · 正在更新",
            0x00000040,      # MB_OK | MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001
        pass


def _filename(res: dict) -> str:
    name = str(res.get("filename") or "").strip()
    if name:
        return Path(name).name
    tail = str(res.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
    return Path(tail or "AstroSwarm_Setup.exe").name


def _sha256(path: Path, on_progress=None, done: int = 0, total: int = 0):
    h = hashlib.sha256()
    got = done
    with path.open("rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
            got += len(block)
            if on_progress:
                on_progress(got, total)
    return h.hexdigest(), got


def verify(path: Path, res: dict) -> str:
    """校验大小与 sha256。返回空串=通过，否则返回给用户看的原因。"""
    want_sha = str(res.get("sha256") or "").strip().lower()
    want_size = int(res.get("size") or 0)
    try:
        real = path.stat().st_size
    except OSError as e:
        return f"安装包读取失败：{e}"
    if want_size and real != want_size:
        return f"安装包大小不对（应为 {want_size} 字节，实际 {real} 字节）"
    if want_sha:
        got, _ = _sha256(path)
        if got.lower() != want_sha:
            return "安装包校验失败（sha256 不匹配），可能没下完或被篡改"
    return ""


def disk_ok(need: int, target: Path) -> str:
    """目标盘剩余空间够不够（下载 + 解压，按 2 倍估）。"""
    try:
        import shutil
        free = shutil.disk_usage(str(target)).free
    except OSError:
        return ""
    if free < need * 2:
        mb = free / 1024 / 1024
        return f"目标磁盘剩余空间不足（只剩 {mb:.0f} MB），请先清理后再更新"
    return ""


def download(res: dict, on_progress=None, cancel=None) -> Path:
    """下载安装包到临时目录；已下好且校验通过的直接复用，不重复下 300MB。"""
    url = str(res.get("url") or "").strip()
    if not url:
        raise RuntimeError("没有拿到下载地址")
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / _filename(res)
    if target.exists() and not verify(target, res):
        if on_progress:
            on_progress(target.stat().st_size, target.stat().st_size)
        return target

    part = target.with_suffix(target.suffix + ".part")
    try:
        part.unlink()
    except OSError:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}-updater"})
    sha = hashlib.sha256()
    got = 0
    total = int(res.get("size") or 0)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        declared = int(resp.headers.get("Content-Length") or 0)
        if not total:
            total = declared
        with part.open("wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("已取消")
                block = resp.read(CHUNK)
                if not block:
                    break
                f.write(block)
                sha.update(block)
                got += len(block)
                if on_progress:
                    on_progress(got, total)
    if total and got != total:
        try:
            part.unlink()
        except OSError:
            pass
        raise RuntimeError(f"下载中断（收到 {got}/{total} 字节），请重试")
    want_sha = str(res.get("sha256") or "").strip().lower()
    if want_sha and sha.hexdigest().lower() != want_sha:
        try:
            part.unlink()
        except OSError:
            pass
        raise RuntimeError("安装包校验失败（sha256 不匹配），已丢弃，请重试")
    os.replace(part, target)
    return target


def _write_runner(setup: Path, target_dir: Path, restart: Path, log: Path) -> Path:
    r"""写一个「等程序退出 → 静默安装 → 重新启动」的 PowerShell 脚本，脱离本进程独立跑。

    为什么是 PowerShell 而不是批处理（2026-10-12 老板实机实锤）：
    批处理活在 ANSI 世界里，安装路径一带中文（如 `D:\AstroSwarm 星群`），
    `cmd + chcp 65001 + start` 就会把路径编坏 —— 实际后果：更新被装进另一个
    乱码目录（`D:\AstroSwarm 鏄熺兢`），重启那行直接弹「Windows 找不到文件」。
    PowerShell 全程 Unicode，中文 / 空格路径都没问题。

    三条必须守住的规矩（别改回去）：
    1. 脚本写成 **UTF-8 带 BOM** —— Windows PowerShell 5.1 对不带 BOM 的 UTF-8
       会按系统 ANSI 读，中文照样坏；
    2. 拉程序之前先**等所有 AstroSwarm_Setup* 进程退出**（最多 5 分钟）——
       防「两个安装器并发，先装完的把程序拉起来 → 半套 DLL 崩」；
    3. 删掉"正在安装"锁之后才启动程序（否则会被启动保护挡住）。
    """
    script = cache_root() / "apply_update.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path()

    def _q(value) -> str:
        """PowerShell 单引号字符串：里面单个单引号翻倍转义。"""
        return "'" + str(value).replace("'", "''") + "'"

    lines = [
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Start-Sleep -Seconds 3",
        f"$setup = {_q(setup)}",
        f"$target = {_q(target_dir)}",
        f"$logPath = {_q(log)}",
        "$argList = @(",
        "    '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART',",
        """    ('"/DIR="' + $target + '"'), ('"/LOG="' + $logPath + '"')""",
        ")",
        "Start-Process -FilePath $setup -Wait -ArgumentList $argList",
        "# 等并发的安装器也退出：文件还在被覆盖时把程序拉起来会 DLL 崩",
        "$n = 0",
        "while ((Get-Process -Name 'AstroSwarm_Setup*') -and $n -lt 150) {",
        "    Start-Sleep -Seconds 2",
        "    $n += 1",
        "}",
        "Start-Sleep -Seconds 2",
    ]
    if restart:
        lines.append(f"Remove-Item -LiteralPath {_q(lock)} -Force")
        lines.append(f"Start-Process -FilePath {_q(restart)}")
    lines.append(f"Remove-Item -LiteralPath {_q(script)} -Force")
    # encoding='utf-8-sig' 写出带 BOM 的 UTF-8（PowerShell 5.1 才认中文）；
    # newline='' 保留手写 \r\n，别再被换行翻译动一遍。
    with script.open("w", encoding="utf-8-sig", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return script


def apply(res: dict, setup: Path, on_progress=None, cancel=None) -> dict:
    """校验安装包 → 起独立安装进程。返回后调用方应尽快退出本程序。"""
    target_dir = install_dir()
    restart = exe_path()
    reason = verify(setup, res)
    if reason:
        return {"ok": False, "error": reason}
    if not restart.exists():
        return {"ok": False, "error": "找不到主程序，无法自动安装（请到官网下载新版）"}
    log = cache_root() / f"install-{time.strftime('%Y%m%d-%H%M%S')}.log"
    # 先落"正在安装"锁再起安装脚本：期间用户再开程序 / 别的实例想更新，
    # 都会被 update_pending() 挡住（防"文件换一半就把程序拉起来"）。
    _write_lock()
    script = _write_runner(setup, target_dir, restart, log)
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", str(script)],
            cwd=str(cache_root()),
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        _clear_lock()
        return {"ok": False, "error": f"无法启动安装程序：{e}"}
    if on_progress:
        on_progress(1, 1)
    return {
        "ok": True, "dir": str(target_dir), "log": str(log),
        "restart": str(restart), "filename": setup.name,
    }


def purge_old(keep_days: int = KEEP_DAYS) -> int:
    """清掉过期的安装包与安装日志（默认 7 天），别让临时目录越积越大。"""
    root = cache_root()
    if not root.exists():
        return 0
    limit = time.time() - keep_days * 86400
    freed = 0
    for item in root.iterdir():
        try:
            if not item.is_file():
                continue
            if item.name.endswith(".part"):
                if item.stat().st_mtime < time.time() - 3600:
                    freed += item.stat().st_size
                    item.unlink()
                continue
            if item.stat().st_mtime >= limit:
                continue
            freed += item.stat().st_size
            item.unlink()
        except OSError:
            continue
    return freed
