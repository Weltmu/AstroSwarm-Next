# -*- coding: utf-8 -*-
"""缓存与废文件自动清理：日志截尾、临时文件、消息缓存、过期 dsh 会话。"""
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ..constants import POINTER_DIR

logger = logging.getLogger("qbotmanager")

_LOG_FILES = ("manager.log", "nonebot.log", "dsh.log", "liqinghan.log",
              "deploy.log", "cleanup.log")
_SUMMARY_LOG = "cleanup.log"
_TMP_SUFFIXES = (".part", ".tmp")
# 子进程/无控制台 CLI 的兜底日志：直接写在 %TEMP%，正常路径下用不到，
# 但打包版被强杀后可能残留，启动清理时顺手收掉。
_TEMP_LOG_NAMES = ("qbotmanager_proc.log", "astroswarm_cli.log")
# 判断 %TEMP%\_MEI* 是不是「我们自己的」解压目录：PyInstaller 会把
# spec 里 datas 的顶层目录原样放进去，我们固定有 appr/ 和 qbotmanager/。
# 别人家 PyInstaller 软件的 _MEI* 不带这个指纹，一律不碰。
_MEI_OWN_MARKERS = ("appr", "qbotmanager")


# 日志默认保留天数（老板 2026-10-03 定：默认只留最近 10 天）
DEFAULT_LOG_KEEP_DAYS = 10
# 归档文件长这样：nonebot.log.2026-10-02 / manager.log.1 / dsh.log.gz
_ARCHIVE_HINTS = (".log.", ".log-", ".log_")


def _is_archived_log(name: str) -> bool:
    """这是不是"轮转出去的旧日志"（区别于正在写的那份）。"""
    low = (name or "").lower()
    if not low.startswith(tuple(f.lower() for f in _LOG_FILES)) and ".log" not in low:
        return False
    return any(hint in low for hint in _ARCHIVE_HINTS) or low.endswith(".log.gz")


def rotate_and_prune_logs(logs_dir: Path,
                          keep_days: int = DEFAULT_LOG_KEEP_DAYS,
                          now: float | None = None) -> dict:
    """日志按天归档 + 只保留最近 N 天，返回 {"rotated": [...], "deleted": [...], "skipped": [...]}。

    两件事：
    1. **归档**：正在写的那份日志如果最后修改时间不是今天（说明跨天了），改名成
       `<名字>.<YYYY-MM-DD>`；新版日志从空文件重新开始，用户按日期找得到。
       机器人正在写这个文件时 Windows 不允许改名 → 记进 skipped，下次再来（不报错）。
    2. **删除**：所有归档日志超过 keep_days 天的一律删掉；正在写的那份从不删
       （体积由 _trim_tail 按大小截断兜底）。
    """
    stats = {"rotated": [], "deleted": [], "skipped": [],
             "keep_days": int(keep_days), "bytes_freed": 0}
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return stats
    now = time.time() if now is None else float(now)
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    cutoff = now - max(1, int(keep_days)) * 86400

    live_names = set(_LOG_FILES)
    try:
        entries = list(logs_dir.iterdir())
    except OSError:
        return stats

    # 1) 先归档跨天的当前日志
    for path in entries:
        if not path.is_file() or path.name not in live_names:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if time.strftime("%Y-%m-%d", time.localtime(mtime)) == today:
            continue
        target = path.with_name(f"{path.name}.{time.strftime('%Y-%m-%d', time.localtime(mtime))}")
        if target.exists():
            continue
        try:
            path.rename(target)
            stats["rotated"].append(target.name)
        except OSError:
            # 大概率是文件正被机器人占用：留着，下一轮再试
            stats["skipped"].append(path.name)

    # 2) 再按天数删归档
    try:
        entries = list(logs_dir.iterdir())
    except OSError:
        return stats
    for path in entries:
        if not path.is_file() or path.name in live_names:
            continue
        if not _is_archived_log(path.name):
            continue
        try:
            mtime = path.stat().st_mtime
            size = path.stat().st_size
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        try:
            path.unlink()
            stats["deleted"].append(path.name)
            stats["bytes_freed"] += size
        except OSError:
            stats["skipped"].append(path.name)
    return stats


def logs_usage(logs_dir: Path) -> dict:
    """日志目录占用（给界面显示"现在占多少"）。"""
    logs_dir = Path(logs_dir)
    total = 0
    count = 0
    if logs_dir.is_dir():
        try:
            for p in logs_dir.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                        count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return {"bytes": total, "files": count, "mb": round(total / 1048576, 1)}


def _trim_tail(path: Path, max_bytes: int) -> None:
    """日志/JSONL 超限时只保留末尾 max_bytes（新日志在尾部）。"""
    try:
        size = path.stat().st_size
        if size <= max_bytes:
            return
        with open(path, "rb") as fh:
            fh.seek(size - max_bytes)
            tail = fh.read()
        with open(path, "wb") as fh:
            fh.write(tail)
    except OSError as e:
        logger.warning("清理 %s 失败: %s", path, e)


def _remove_stale_tmp(directory: Path) -> int:
    """删除目录下的 .part / .tmp 及 *.tmp.* 残留（中断下载/转码产物）。"""
    if not directory.is_dir():
        return 0
    count = 0
    try:
        for p in directory.rglob("*"):
            name = p.name.lower()
            if p.is_file() and (
                name.endswith(_TMP_SUFFIXES) or ".tmp." in name
            ):
                try:
                    p.unlink()
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return count


def _remove_old_sessions(sessions_root: Path, max_days: int) -> int:
    """删除超过保留期的 dsh 会话目录（zstd 压缩消息也会累积）。"""
    if max_days <= 0 or not sessions_root.is_dir():
        return 0
    cutoff = time.time() - max_days * 86400
    count = 0

    try:
        for ws in sessions_root.iterdir():
            if not ws.is_dir():
                continue
            for sid in ws.iterdir():
                try:
                    if _newest_mtime(sid) < cutoff:
                        shutil.rmtree(sid, ignore_errors=True)
                        count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return count


def _remove_stale_zip(directory: Path, max_hours: int) -> int:
    """删除 downloads/plugins 下超过保留期的已下载 zip（安装后无保留价值）。

    保留 24 小时内刚下载的文件，避免与正在进行的下载/安装产生竞争；
    超过保留期的 zip 是安装完成后未清理或中断安装留下的废文件。
    同样适用于 downloads 根目录下的运行时压缩包（Python 嵌入版 / Node
    便携版，合计约 40 MB）：解压完就没用了，留着只占用户磁盘。
    """
    if max_hours <= 0 or not directory.is_dir():
        return 0
    cutoff = time.time() - max_hours * 3600
    count = 0
    try:
        for p in directory.glob("*.zip"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    count += 1
            except OSError:
                pass
    except OSError:
        pass
    return count


def _newest_mtime(path: Path) -> float:
    """目录里最新的修改时间（看内容，不看目录本身）。

    以内容为准：目录自身的 mtime 会因临时文件的增删被刷新，用它判断会把
    长期没消息的会话误判成新的。目录为空或读不到时才退回目录自身时间。
    """
    newest = 0.0
    try:
        for f in path.rglob("*"):
            try:
                newest = max(newest, f.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    if newest <= 0:
        try:
            newest = path.stat().st_mtime
        except OSError:
            newest = 0.0
    return newest


def remove_stale_temp_dirs(temp_root: Path, max_age_hours: float,
                           keep: Path | None = None,
                           patterns: tuple = ("_MEI*",),
                           markers: tuple = ()) -> int:
    """删除系统临时目录里的残留目录，返回成功删除的个数。

    v1.2.16 之前的打包版是 PyInstaller onefile：每次启动都会把自身解压到
    %TEMP%/_MEIxxxxxx，正常退出会自动删除；被任务管理器强杀或崩溃时会留在
    盘上，单个约 450 MB，用户不会自己去清。v1.2.16 起改 onedir 不再产生新残留，
    这段逻辑继续保留，负责清理老版本升级上来的历史残留。
    Inno 安装程序被中断时也会留下 is-*.tmp。

    只删「最后修改时间早于保留期」的目录，并跳过 keep（本进程正在使用的
    sys._MEIPASS），避免误删正在运行的实例。markers 非空时，还要求目录顶层
    存在其中任一名字才处理——用于确认是自己的解压产物，别家软件的 _MEI*
    即使过期也不能动（它可能正开着）。
    """
    if max_age_hours <= 0 or not temp_root.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    keep_res = None
    if keep is not None:
        try:
            keep_res = Path(keep).resolve()
        except OSError:
            keep_res = None
    removed = 0
    for pattern in patterns:
        try:
            candidates = list(temp_root.glob(pattern))
        except OSError:
            continue
        for p in candidates:
            try:
                if not p.is_dir():
                    continue
                if keep_res is not None and p.resolve() == keep_res:
                    continue
                if markers and not any((p / m).exists() for m in markers):
                    continue
                if _newest_mtime(p) >= cutoff:
                    continue
                shutil.rmtree(p, ignore_errors=True)
                if not p.exists():
                    removed += 1
            except OSError:
                pass
    return removed


def prune_pointer_backups(directory: Path, keep: int = 3) -> int:
    """指针目录里的 *.bak_* 会随每次升级累积，只保留最新几份。"""
    if keep < 0 or not directory.is_dir():
        return 0
    try:
        files = [p for p in directory.glob("*.bak_*") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for p in files[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def remove_stale_temp_files(temp_root: Path, max_age_hours: float, names) -> int:
    """删除系统临时目录里过期的兜底日志，返回删除个数。

    正在被别的进程写着的文件在 Windows 上删不掉（Python 打开的文件不带
    共享删除权限），unlink 会抛 PermissionError 并被吞掉，所以活动日志安全。
    """
    if max_age_hours <= 0 or not temp_root.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for name in names:
        p = temp_root / name
        try:
            if not p.is_file() or p.stat().st_mtime >= cutoff:
                continue
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _write_summary_log(settings, summary: dict) -> None:
    """把有内容的清理摘要追加到 logs/cleanup.log。

    桌面端目前没给 "qbotmanager" logger 配 handler（logger.info 不会落盘），
    而客户机上「我的磁盘被清了什么」需要可查，所以自己写一行。
    """
    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(settings.logs_dir / _SUMMARY_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s cleanup %s\n" % (stamp, summary))
    except OSError as e:
        logger.warning("写清理摘要失败: %s", e)


def run(settings,
        max_log_bytes: int = 8 * 1024 * 1024,
        log_keep_days: int | None = None,
        max_message_bytes: int = 16 * 1024 * 1024,
        max_activate_bytes: int = 256 * 1024,
        max_session_days: int = 30,
        max_zip_hours: int = 24,
        appdata_dir: Path | None = None,
        max_mei_hours: float = 6,
        max_temp_log_hours: float = 24,
        temp_dir: Path | None = None) -> dict:
    """执行一轮清理，返回统计摘要（供日志/测试）。"""
    # 日志按天归档 + 只留最近 N 天（默认 10 天；设置里可改）
    if log_keep_days is None:
        log_keep_days = int(getattr(settings, "logs_keep_days", DEFAULT_LOG_KEEP_DAYS)
                            or DEFAULT_LOG_KEEP_DAYS)
    log_stats = rotate_and_prune_logs(settings.logs_dir, log_keep_days)
    capped = 0
    for name in _LOG_FILES:
        p = settings.logs_dir / name
        if p.exists() and p.stat().st_size > max_log_bytes:
            _trim_tail(p, max_log_bytes)
            capped += 1
    msg = settings.bot_dir / "data" / "nonebot_adapter_ilink" / "messages.jsonl"
    if msg.exists() and msg.stat().st_size > max_message_bytes:
        _trim_tail(msg, max_message_bytes)
        capped += 1
    tmp_removed = 0
    for d in (settings.downloads_dir, settings.root / "cache"):
        tmp_removed += _remove_stale_tmp(d)
    appdata = appdata_dir or (
        Path(os.environ.get("APPDATA", "")) / "QBotManager"
        if os.environ.get("APPDATA") else None
    )
    if appdata is not None:
        act = appdata / "activate.log"
        if act.exists() and act.stat().st_size > max_activate_bytes:
            _trim_tail(act, max_activate_bytes)
            capped += 1
    sessions_removed = _remove_old_sessions(
        settings.root / "dsh" / "home" / "sessions", max_session_days
    )
    zip_removed = _remove_stale_zip(settings.downloads_dir, max_zip_hours)
    zip_removed += _remove_stale_zip(
        settings.downloads_dir / "plugins", max_zip_hours
    )
    # 老版本（≤v1.2.15，onefile）每次启动会把自身解压到 %TEMP%\_MEIxxxxxx（约 450 MB），
    # 被强杀或崩溃时留下；v1.2.16 起改 onedir 不再产生新残留，这里继续清历史残留。
    # Inno 中断安装会留下 is-*.tmp。只清超过保留期的，并跳过本进程正在使用的 sys._MEIPASS。
    temp_root = temp_dir or Path(tempfile.gettempdir())
    mei_removed = remove_stale_temp_dirs(
        temp_root,
        max_mei_hours,
        keep=getattr(sys, "_MEIPASS", None),
        patterns=("_MEI*",),
        markers=_MEI_OWN_MARKERS,
    )
    installer_tmp_removed = remove_stale_temp_dirs(
        temp_root,
        max_mei_hours,
        patterns=("is-*.tmp",),
    )
    temp_logs_removed = remove_stale_temp_files(
        temp_root, max_temp_log_hours, _TEMP_LOG_NAMES
    )
    bak_removed = prune_pointer_backups(POINTER_DIR)
    summary = {
        "log_capped": capped,
        "log_rotated": log_stats.get("rotated") or [],
        "log_deleted": log_stats.get("deleted") or [],
        "log_keep_days": log_stats.get("keep_days"),
        "log_bytes_freed": log_stats.get("bytes_freed") or 0,
        "tmp_removed": tmp_removed,
        "sessions_removed": sessions_removed,
        "zip_removed": zip_removed,
        "mei_removed": mei_removed,
        "installer_tmp_removed": installer_tmp_removed,
        "temp_logs_removed": temp_logs_removed,
        "bak_removed": bak_removed,
    }
    if any(summary.values()):
        logger.info("启动清理: %s", summary)
        _write_summary_log(settings, summary)
    return summary
