# -*- coding: utf-8 -*-
"""手写体昵称字体：和官网银行卡卡背签名同款。

字体文件与官网 space/fonts 下完全同源：
  - DancingScript-Regular.ttf   拉丁字母 / 数字
  - LiuJianMaoCao-Regular.ttf   中文
系统里没有这两款字体时回落到手写/楷体系列，不影响显示。
"""
import re
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

# Qt 按列表顺序挑第一个装了的家族；中文会落到柳建毛草，英文落到 Dancing Script
_FAMILIES = ["Dancing Script", "Liu Jian Mao Cao"]
_FALLBACK = ["Segoe Script", "Bradley Hand", "STXingkai", "KaiTi", "楷体"]

# 昵称里出现这些字符（CJK / 全角）时，排版顺序改成中文手写体优先
_CJK_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff01-\uff60]")

_LOADED = False
_LOADED_FAMILIES: set[str] = set()


def ensure_loaded() -> bool:
    """把两款手写体注册进 Qt（只注册一次）；返回是否至少成功一款。"""
    global _LOADED
    if _LOADED:
        return True
    fonts_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    ok = False
    for name in ("DancingScript-Regular.ttf", "LiuJianMaoCao-Regular.ttf"):
        path = fonts_dir / name
        if not path.exists():
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            ok = True
            _LOADED_FAMILIES.update(fams)
    _LOADED = ok
    return ok


def hand_font(pixel_size: int = 0) -> QFont:
    """昵称用的手写体 QFont；pixel_size<=0 时交给样式表决定字号。"""
    ensure_loaded()
    font = QFont()
    font.setFamilies(_FAMILIES + _FALLBACK)
    if pixel_size > 0:
        font.setPixelSize(pixel_size)
    return font


def hand_qss(name: str = "", size_px: int = 14) -> str:
    """按钮/标签用的手写体 QSS 片段（和官网 .hand 同款字体顺序）。

    为什么不用 setFont：全局样式表 `* { font-family: ... }` 会把多家族字体
    压成单一 Latin 家族，中文昵称会变方框。组件级样式表能稳定生效。
    字体顺序与官网 --hand 一致：拉丁走 Dancing Script，中文自动落到柳建毛草。
    字体没装上时返回空串，界面保持系统字体。
    """
    if not ensure_loaded():
        return ""
    fams = list(_FAMILIES) + ["Segoe UI Emoji"] + _FALLBACK
    # 只保证注册成功的手写体在前，其余按可用性兜底（缺失家族 Qt 会自动跳过）
    body = ",".join("'%s'" % f for f in fams)
    return "font-family:%s;font-size:%dpx;" % (body, size_px)
