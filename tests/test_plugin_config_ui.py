# -*- coding: utf-8 -*-
"""插件页「已装能力包」配置弹窗的界面用例（offscreen）。

老板的原话：参数别给一堆英文名，要通俗中文名 + 一句人话；范围/单位写清楚；
「已装能力包」那张卡片提到「插件商店」上面；弹窗风格跟软件其它地方一致。

这里只跑单个文件（不跑整套 test_ui_smoke，那套 4 分钟会跟别人抢 CPU）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("QBM_POINTER_DIR"):
    os.environ["QBM_POINTER_DIR"] = str(Path(tempfile.mkdtemp(prefix="qbm_test_ptr_")))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDoubleSpinBox, QLabel, QMessageBox, QSpinBox,
)

from qbotmanager.core import plugin_market, tool_packs  # noqa: E402
from qbotmanager.core.settings import Settings  # noqa: E402


def _settings() -> Settings:
    tmp = Path(tempfile.mkdtemp(prefix="qbm_packui_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.save()
    return s


def _install_rhythm_pack(s: Settings) -> None:
    """造一个和「回复节奏能力包」同构的包：只有 behavior，键全是英文（截图那种）。"""
    packs = tool_packs.packs_dir(s)
    pack = packs / "reply-rhythm"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps({
        "id": "reply-rhythm", "name": "回复节奏能力包", "version": "1.0.0",
        "kind": "behavior-pack", "description": "真人回复节奏，别秒回。",
        "behavior": {"reply-rhythm": {
            "busy_long_probability": 0.35,
            "busy_long_seconds": [180, 900],
            "busy_short_seconds": [8, 40],
            "idle_probability": 0.1,
            "idle_seconds": [60, 180],
            "some_unknown_key": 7,
        }},
    }, ensure_ascii=False), encoding="utf-8")
    # 用户之前存过的区间值（截图里是 [80, 900]）
    (pack / "config.json").write_text(json.dumps({
        "busy_long_seconds": [80, 900], "idle_probability": 0.2,
    }, ensure_ascii=False), encoding="utf-8")


def _dialog(s: Settings):
    from qbotmanager.ui.pages.plugins_page import PackDetailDialog

    return PackDetailDialog(s, tool_packs.pack_detail(s, "reply-rhythm"))


def test_params_show_chinese_names_not_english_keys():
    app = QApplication.instance() or QApplication([])
    s = _settings()
    _install_rhythm_pack(s)
    dlg = _dialog(s)
    app.processEvents()

    texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    joined = "\n".join(texts)

    # 中文名 + 单位 + 范围都要出现
    assert "「忙很久」的出现概率" in joined, joined
    assert "「忙很久」的回复间隔（秒），可填范围 0–3600" in joined, joined
    assert "空闲时的回复间隔（秒）" in joined, joined
    # 说明那句人话也在
    assert "随机挑一个时间回" in joined, joined
    # 截图里那 5 个英文 key 不许当标题出现（只允许出现在悬停提示里）
    for key in ("busy_long_probability", "busy_long_seconds", "busy_short_seconds",
                "idle_probability", "idle_seconds"):
        assert key not in joined, f"{key} 还露在界面上：{joined}"
    # 表里没有的 key 兜底显示原名
    assert "some_unknown_key" in joined, joined

    tips = [lbl.toolTip() for lbl in dlg.findChildren(QLabel)]
    assert any("busy_long_seconds" in t for t in tips), "英文 key 应留在悬停提示里"

    # 老板最不能忍的就是文字被裁：排完版每段文字都得放得下自己。
    # 注意 offscreen 平台没有字体（Qt 不再自带），sizeHint 对自动换行的标签不准，
    # 所以用布局真正依赖的 heightForWidth 来算"需要多高"。
    def needed_height(lbl):
        if lbl.hasHeightForWidth():
            return lbl.heightForWidth(lbl.width())
        return lbl.sizeHint().height()

    def clipped_texts():
        return [lbl.text() for lbl in dlg.findChildren(QLabel)
                if lbl.text() and lbl.height() < needed_height(lbl) - 1]

    # 两种宽度都要查：窗口窄的时候中文名一定会换行 —— 2026-10-01 老板截图就是
    # 换行后的第二行被压掉、和输入框叠在一起（只在宽窗口测是查不出来的）。
    for width in (680, 560, 420):
        dlg.resize(width, 620)
        app.processEvents()
        bad = clipped_texts()
        assert not bad, f"宽度 {width} 时这些文字被压扁了：{bad}"

    dlg.resize(680, 620)
    app.processEvents()
    assert dlg.scroll.widget().height() >= dlg.scroll.viewport().height() - 1 or \
        dlg.scroll.verticalScrollBar().maximum() == 0, "装不下时要能滚，而不是压扁文字"
    dlg.close()
    print("OK plugin_config: 中文名/说明/范围")


def test_range_values_are_editable_and_validated():
    app = QApplication.instance() or QApplication([])
    s = _settings()
    _install_rhythm_pack(s)
    dlg = _dialog(s)
    app.processEvents()

    # 区间参数渲染成两个数字框，值就是用户之前存的 80~900
    box_lo, box_hi = dlg.range_boxes["busy_long_seconds"]
    assert (box_lo.value(), box_hi.value()) == (80, 900)
    assert set(dlg.range_boxes) == {"busy_long_seconds", "busy_short_seconds", "idle_seconds"}
    assert all(isinstance(box, QSpinBox)
               for pair in dlg.range_boxes.values() for box in pair), "区间用整数框"
    assert len(dlg.findChildren(QDoubleSpinBox)) == 2, "两个概率参数 = 两个小数框"

    # 反着填：要弹中文报错，而且不能写盘
    warnings = []
    original_warn = QMessageBox.warning
    QMessageBox.warning = staticmethod(
        lambda *a, **k: warnings.append(a[2] if len(a) > 2 else ""))
    try:
        box_lo.setValue(900)
        box_hi.setValue(80)
        dlg._save()
    finally:
        QMessageBox.warning = original_warn
    assert warnings and "最小不能大于最大" in warnings[0], warnings
    saved = json.loads(tool_packs.pack_config_path(s, "reply-rhythm").read_text(encoding="utf-8"))
    assert saved["busy_long_seconds"] == [80, 900], "校验失败时不许写盘"

    # 正常改：存成 [最小, 最大] 列表
    box_lo.setValue(100)
    box_hi.setValue(500)
    dlg._save()
    saved = json.loads(tool_packs.pack_config_path(s, "reply-rhythm").read_text(encoding="utf-8"))
    assert saved["busy_long_seconds"] == [100, 500], saved
    print("OK plugin_config: 区间可编辑 + 校验 + 落盘")


def test_pack_card_sits_above_market_card():
    app = QApplication.instance() or QApplication([])
    s = _settings()
    _install_rhythm_pack(s)
    # 别真去连服务器拉商店清单
    original_fetch = plugin_market.fetch_market
    plugin_market.fetch_market = lambda *a, **k: []
    try:
        from qbotmanager.tasks.task_manager import TaskManager
        from qbotmanager.ui.pages.common import PageContext
        from qbotmanager.ui.pages.plugins_page import PluginsPage

        ctx = PageContext(s, None, TaskManager(), {"show_toast": lambda *a: None})
        pp = PluginsPage(ctx)
        app.processEvents()
        outer = pp.scroll.widget().layout()

        def panel_of(title: str):
            for lbl in pp.findChildren(QLabel):
                if lbl.text() == title:
                    return lbl.parentWidget()
            raise AssertionError(f"找不到卡片：{title}")

        pack_panel = panel_of("已装能力包")
        market_panel = panel_of("插件商店")
        pack_idx = outer.indexOf(pack_panel)
        market_idx = outer.indexOf(market_panel)
        assert pack_idx != -1 and market_idx != -1, (pack_idx, market_idx)
        assert pack_idx < market_idx, f"已装能力包应在插件商店上面（{pack_idx} vs {market_idx}）"
        assert pp.pack_list is not None and pp.btn_pack_config is not None
    finally:
        plugin_market.fetch_market = original_fetch
    print("OK plugin_config: 卡片顺序")


if __name__ == "__main__":
    test_params_show_chinese_names_not_english_keys()
    test_range_values_are_editable_and_validated()
    test_pack_card_sits_above_market_card()
    print("OK plugin config ui")
