# -*- coding: utf-8 -*-
"""站内更新（core/updater.py）单测：校验、复用、清理、空间检查。"""
import hashlib
import os
import tempfile
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qbotmanager.core import updater  # noqa: E402


def _tmpdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


def _fake_setup(root: Path, name="AstroSwarm_Setup.exe", data=b"astro-swarm-payload"):
    p = root / name
    p.write_bytes(data)
    return p, {
        "url": "https://astroswarm.cn/downloads/" + name,
        "filename": name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def test_verify_branches():
    root = _tmpdir("qbm_upd_verify_")
    setup, res = _fake_setup(root)

    assert updater.verify(setup, res) == ""
    print("OK verify_pass")

    bad = dict(res, sha256="0" * 64)
    assert "校验失败" in updater.verify(setup, bad)
    print("OK verify_sha_mismatch")

    bad = dict(res, size=res["size"] + 1)
    assert "大小不对" in updater.verify(setup, bad)
    print("OK verify_size_mismatch")

    missing = root / "nope.exe"
    assert "读取失败" in updater.verify(missing, res)
    print("OK verify_missing")

    # 没有 sha256 的清单（老版本 manifest）：只认大小，不能报错
    assert updater.verify(setup, {"size": res["size"]}) == ""
    print("OK verify_sha_optional")


def test_download_reuses_verified_file():
    """同版本已经下好且校验通过时直接复用，不再发起网络请求。"""
    root = _tmpdir("qbm_upd_reuse_")
    setup, res = _fake_setup(root)
    old = updater.cache_root
    updater.cache_root = lambda: root
    try:
        seen = []
        res_bad_url = dict(res, url="http://127.0.0.1:1/should-not-download.exe")
        got = updater.download(res_bad_url, on_progress=lambda a, b: seen.append((a, b)))
    finally:
        updater.cache_root = old
    assert got == setup, (got, setup)
    assert seen and seen[-1][0] == seen[-1][1] == res["size"]
    print("OK download_reuse")


def test_filename_fallback_from_url():
    assert updater._filename({"filename": "..\\weird\\a.exe"}) == "a.exe"
    assert updater._filename({"url": "https://x.cn/d/AstroSwarm_Setup_1.2.18.exe"}) == \
        "AstroSwarm_Setup_1.2.18.exe"
    assert updater._filename({}).endswith(".exe")
    print("OK filename_fallback")


def test_purge_old_keeps_fresh():
    root = _tmpdir("qbm_upd_purge_")
    old_pkg = root / "AstroSwarm_Setup_1.0.0.exe"
    new_pkg = root / "AstroSwarm_Setup_9.9.9.exe"
    stale_part = root / "AstroSwarm_Setup_1.0.1.exe.part"
    live_part = root / "AstroSwarm_Setup_1.0.2.exe.part"
    log = root / "install-20200101-000000.log"
    for p in (old_pkg, new_pkg, stale_part, live_part, log):
        p.write_bytes(b"x" * 32)
    ancient = time.time() - 10 * 86400
    for p in (old_pkg, log):
        os.utime(p, (ancient, ancient))
    stale = time.time() - 7200
    os.utime(stale_part, (stale, stale))

    old = updater.cache_root
    updater.cache_root = lambda: root
    try:
        updater.purge_old()
    finally:
        updater.cache_root = old

    assert not old_pkg.exists() and not log.exists(), "过期安装包/日志没被清掉"
    assert new_pkg.exists(), "刚下好的安装包被误删"
    assert not stale_part.exists(), "超时的半截下载没被清掉"
    assert live_part.exists(), "正在下载的半截文件被误删"
    print("OK purge_old")


def test_disk_ok_reads_free_space():
    root = _tmpdir("qbm_upd_disk_")
    assert updater.disk_ok(0, root) == ""
    assert "空间不足" in updater.disk_ok(10 ** 18, root)
    print("OK disk_ok")


def test_apply_writes_lock_and_guarded_runner():
    """apply() 要落"正在安装"锁；安装脚本要"等所有安装器退出 → 删锁 → 再拉程序"。"""
    root = _tmpdir("qbm_upd_apply_")
    setup, res = _fake_setup(root)
    restart = root / "AstroSwarm.exe"
    restart.write_bytes(b"MZ")
    seen = {}

    class _FakePopen:
        def __init__(self, args, **kw):
            seen["args"] = args

    old_cache = updater.cache_root
    old_popen = updater.subprocess.Popen
    old_exe = updater.exe_path
    old_dir = updater.install_dir
    updater.cache_root = lambda: root
    updater.exe_path = lambda: restart
    updater.install_dir = lambda: root
    updater.subprocess.Popen = _FakePopen
    try:
        info = updater.apply(res, setup)
    finally:
        updater.cache_root = old_cache
        updater.subprocess.Popen = old_popen
        updater.exe_path = old_exe
        updater.install_dir = old_dir
    assert info.get("ok"), info
    assert seen.get("args", [None])[0] == "powershell"
    lock = root / updater.LOCK_NAME
    assert lock.exists(), "apply() 没有写「正在安装」锁"
    script_path = root / "apply_update.ps1"
    raw = script_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), \
        "ps1 必须带 UTF-8 BOM（不然 5.1 按 ANSI 读，中文路径照样坏）"
    script = raw.decode("utf-8-sig")
    assert "Get-Process -Name 'AstroSwarm_Setup*'" in script, script
    assert "Start-Process -FilePath $setup -Wait" in script, script
    del_idx = script.index(f"Remove-Item -LiteralPath '{lock}'")
    start_idx = script.index(f"Start-Process -FilePath '{restart}'")
    assert del_idx < start_idx, "必须先删锁再重启程序（否则新程序会被自己的锁挡住）"
    print("OK apply_lock_and_runner")


def test_update_pending_needs_lock_and_running_setup():
    """没有锁 → False；有锁但没安装器进程 → False；有锁且有安装器 → True。"""
    root = _tmpdir("qbm_upd_pending_")
    old_cache = updater.cache_root
    old_run = updater._setup_running
    updater.cache_root = lambda: root
    try:
        assert updater.update_pending() is False
        (root / updater.LOCK_NAME).write_text("1", encoding="utf-8")
        updater._setup_running = lambda: True
        assert updater.update_pending() is True
        updater._setup_running = lambda: False
        assert updater.update_pending() is False
    finally:
        updater.cache_root = old_cache
        updater._setup_running = old_run
    print("OK update_pending")


if __name__ == "__main__":
    test_verify_branches()
    test_download_reuses_verified_file()
    test_filename_fallback_from_url()
    test_purge_old_keeps_fresh()
    test_disk_ok_reads_free_space()
    test_apply_writes_lock_and_guarded_runner()
    test_update_pending_needs_lock_and_running_setup()
    print("ALL OK test_updater")
