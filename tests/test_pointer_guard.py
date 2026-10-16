# -*- coding: utf-8 -*-
"""安装根指针写入守卫：%TEMP% 下的假根不许覆盖真指针（2026-10-12 实锤修复）。

背景：体检/模拟脚本用 %TEMP% 临时部署调 Settings.save()，指针文件被写成
临时目录（实锤值前缀 qbm_check_clean_*），find_root 兜底再把程序带回假安装根，
日志目录、bot 进程全跑偏。修法：save() 写指针前过 _plausible_root，
口径与 _remember_root 一致（临时目录下的根一律不写）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

if not os.environ.get("QBM_POINTER_DIR"):
    os.environ["QBM_POINTER_DIR"] = str(Path(tempfile.mkdtemp(prefix="qbm_test_ptr_")))

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbotmanager.core import settings as settings_mod  # noqa: E402
from qbotmanager.core.settings import Settings  # noqa: E402


def _patch_pointer(monkeypatch, tmp_path):
    """把指针/最近列表重定向到测试自己的目录，别碰任何真实路径。"""
    ptr = tmp_path / "ptr"
    ptr.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_mod, "POINTER_DIR", ptr)
    monkeypatch.setattr(settings_mod, "POINTER_FILE", ptr / "settings.json")
    monkeypatch.setattr(settings_mod, "RECENT_FILE", ptr / "recent_roots.json")
    return ptr


def test_temp_root_does_not_write_pointer(monkeypatch, tmp_path):
    """%TEMP% 下的根：save() 不许写指针，也不许进最近列表。"""
    ptr = _patch_pointer(monkeypatch, tmp_path)
    root = Path(tempfile.mkdtemp(prefix="qbm_pointer_guard_"))
    Settings(root).save()
    assert not (ptr / "settings.json").exists(), \
        "TEMP 假根写进了指针（find_root 会被带偏）"
    assert not (ptr / "recent_roots.json").exists(), \
        "TEMP 假根写进了最近列表（find_root 兜底会被带偏）"


def test_real_root_writes_pointer(monkeypatch, tmp_path):
    """正经安装根（非 TEMP）：指针照写，行为不回退。"""
    ptr = _patch_pointer(monkeypatch, tmp_path)
    root = Path(tempfile.mkdtemp(prefix="qbm_pointer_guard_"))
    # root 建好之后再改“系统临时目录”，模拟非 TEMP 的安装根
    fake_tmp = tmp_path / "not_system_tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(settings_mod.tempfile, "gettempdir", lambda: str(fake_tmp))
    Settings(root).save()
    pfile = ptr / "settings.json"
    assert pfile.exists(), "非 TEMP 根必须照常写指针（防修复过度）"
    assert json.loads(pfile.read_text(encoding="utf-8"))["root"] == str(root)
