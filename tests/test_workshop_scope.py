# -*- coding: utf-8 -*-
"""工坊只管"自己生成的插件"：官网下载的能力包不许混进来、也不许投稿上传。

2026-10-02 老板实测发现：官网下载的能力包被列进了「插件工坊 → 已装的插件」里，
而且还能走「上传到官网审核」。这条用例把这俩口子焊死。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import os  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QBM_NO_UPDATE", "1")


def _make_pack(root: Path, pid: str, name: str, workshop_meta: bool) -> None:
    """造一个能力包目录；workshop_meta=True 表示它是工坊生成的（有 workshop/<id>.json）。"""
    pack = root / "tool_packs" / pid
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps({
        "id": pid, "name": name, "version": "1.0.0", "kind": "tool-pack",
        "adapters": ["qq"], "tools": [{"name": "ping", "description": "测试"}],
    }, ensure_ascii=False), encoding="utf-8")
    if workshop_meta:
        meta_dir = root / "workshop"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{pid}.json").write_text(json.dumps({
            "id": pid, "name": name, "source": "workshop",
            "installed_at": "2026-10-02 12:00:00", "model": "deepseek-chat",
        }, ensure_ascii=False), encoding="utf-8")


def _page(tmp: Path):
    from PySide6.QtWidgets import QApplication

    from qbotmanager.core.settings import Settings
    from qbotmanager.tasks.task_manager import TaskManager
    from qbotmanager.ui.pages.common import PageContext
    from qbotmanager.ui.pages.workshop_page import WorkshopPage

    app = QApplication.instance() or QApplication([])
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    toasts = []
    ctx = PageContext(s, None, TaskManager(),
                      {"show_toast": lambda *a, **k: toasts.append(a[0] if a else "")})
    page = WorkshopPage(ctx)
    app.processEvents()
    return app, s, page, toasts


def test_installed_list_only_shows_generated_packs():
    from qbotmanager.core.workshop.installer import packs_root

    tmp = Path(tempfile.mkdtemp(prefix="qbm_ws_scope_"))
    app, s, page, _toasts = _page(tmp)
    packs = packs_root(s)
    _make_pack(packs.parent, "official-pack", "官网下载的能力包", workshop_meta=False)
    _make_pack(packs.parent, "made-by-ai", "我让 AI 做的", workshop_meta=True)

    page._refresh_installed()
    app.processEvents()
    texts = [page.installed_list.item(i).text()
             for i in range(page.installed_list.count())]
    joined = "\n".join(texts)
    assert "我让 AI 做的" in joined, texts
    assert "官网下载的能力包" not in joined, f"官网下载的包不该出现在工坊列表里：{texts}"
    assert "另有 1 个官网下载" in page.installed_hint.text(), page.installed_hint.text()


def test_upload_refuses_when_no_generated_pack():
    from qbotmanager.core.workshop.installer import packs_root

    tmp = Path(tempfile.mkdtemp(prefix="qbm_ws_up_"))
    app, s, page, toasts = _page(tmp)
    packs = packs_root(s)
    _make_pack(packs.parent, "official-only", "只有官网包", workshop_meta=False)

    import qbotmanager.ui.pages.workshop_page as wp

    def _boom(*_a, **_k):
        raise AssertionError("没有工坊生成的插件时，不该打开上传对话框")

    real = wp.UploadDialog
    wp.UploadDialog = _boom
    try:
        page._upload()
        app.processEvents()
    finally:
        wp.UploadDialog = real
    assert any("先在上面写需求生成一个" in str(t) for t in toasts), toasts


if __name__ == "__main__":
    test_installed_list_only_shows_generated_packs()
    test_upload_refuses_when_no_generated_pack()
    print("OK workshop scope")
