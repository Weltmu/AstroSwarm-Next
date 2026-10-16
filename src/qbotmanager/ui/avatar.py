# -*- coding: utf-8 -*-
"""头像渲染：本地 avatar.jpg → 圆形 QPixmap；没上传过头像时用官网同款默认头像。

官网个人中心换的头像会随 /me 同步到 %APPDATA%/QBotManager/avatar.jpg，
服务端已把它裁成正方形并缩到 1024×1024，这里只负责切圆角和缩放。
没有头像文件时不能画成另一种样子——必须和官网 space/space.js 的
AstroUser.initialAvatar 完全一致（同色相渐变 + 首字母），否则两边看起来是两个头像。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap,
)

from ..core import account as account_mod

# 官网默认头像参数（space/space.js: AstroUser.initialAvatar）
FALLBACK_CHAR = "星"
AVATAR_FONT_FAMILIES = [
    "Segoe UI", "system-ui", "Microsoft YaHei", "PingFang SC", "sans-serif",
]


def avatar_hue(name: str) -> int:
    """与官网 initialAvatar 完全同款的取色哈希：h = (h*31 + charCode) >>> 0。"""
    h = 0
    for ch in str(name or ""):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % 360


def avatar_letter(name: str) -> str:
    """官网字幕规则：首个字符大写；名字为空时用「星」。"""
    text = str(name or "").strip()
    return text[:1].upper() if text else FALLBACK_CHAR


def avatar_name(acc: dict | None) -> str:
    """官网 AstroUser.initialAvatar 的名字链：昵称 → 签名 → 邮箱（原文，不截前缀）。"""
    if not acc:
        return ""
    return str(acc.get("nickname") or acc.get("signature") or acc.get("email") or "").strip()


def _screen_dpr() -> float:
    """当前屏幕缩放比（100% 缩放=1.0）；拿不到就退回 1.0，宁可不放大也别画两遍。"""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        scr = app.primaryScreen() if app is not None else None
        if scr is not None:
            dpr = float(scr.devicePixelRatio())
            if dpr > 0:
                return dpr
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def _hsl(hue: float, sat: float, light: float) -> QColor:
    """浏览器 hsl(h, s%, l%) → QColor（H 0-360 循环）。"""
    c = QColor()
    c.setHslF((hue % 360) / 360.0, sat, light)
    return c


def _default_pixmap(size: int, dpr: float, name: str, logged_in: bool) -> QPixmap:
    """官网同款默认头像：色相渐变圆底 + 白色首字母（未登录时更淡，避免像已登录）。"""
    name = str(name or "").strip()
    hue = avatar_hue(name)
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    rect = QRectF(0, 0, size, size)
    grad = QLinearGradient(0, 0, size, size)
    c1 = _hsl(hue, 0.60, 0.52)
    c2 = _hsl(hue + 42, 0.56, 0.32)
    if not logged_in:
        c1.setAlpha(130)
        c2.setAlpha(130)
    grad.setColorAt(0, c1)
    grad.setColorAt(1, c2)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(rect)
    font = QFont()
    try:
        font.setFamilies(AVATAR_FONT_FAMILIES)
    except Exception:  # noqa: BLE001
        font.setFamily(AVATAR_FONT_FAMILIES[0])
    font.setPixelSize(max(1, round(size * 0.46)))
    try:
        font.setWeight(QFont.Weight.DemiBold)
    except Exception:  # noqa: BLE001
        font.setBold(True)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255, 235 if logged_in else 190))
    p.drawText(QRectF(0, size * 0.03, size, size), Qt.AlignCenter, avatar_letter(name))
    p.end()
    return pm


def avatar_pixmap(size: int = 22, dpr: float = 0.0, acc: dict | None = None,
                  zoom: float = 1.0, shift_y: float = 0.0) -> QPixmap:
    """圆形头像。acc 传 None 时现读账号；文件缺失/损坏一律回退官网同款默认头像。

    dpr 传 0（默认）时按当前屏幕的真实缩放比走：写死 2.0 的话，在 100% 缩放的屏幕上
    会先按 2 倍画、再被 Qt 缩回一半显示 —— 画两遍，头像就是因此发糊。

    缩小走「先缩到最终显示尺寸的 4 倍、再面积平均到最终尺寸」：
    浏览器（官网个人中心）缩图就是这种两段式 + 面积平均，一步 bilinear 缩小会发糊，
    两边看起来才不一致（2026-10-07 用户反馈"和官网清晰度不一样"）。

    zoom / shift_y：仅为兼容旧调用保留，默认值（1.0 / 0）就是与官网完全相同的
    「整幅缩进圆」构图；任何放大/位移都会让客户端与官网画面不一致
    （2026-10-07 像素实测：侧栏加 zoom≈1.19+上移后与官网差异 31.7，去掉后 2.6）。
    """
    if dpr <= 0:
        dpr = _screen_dpr()
    if acc is None:
        try:
            acc = account_mod.load_account()
        except Exception:  # noqa: BLE001
            acc = None
    src = QPixmap()
    if acc:
        try:
            path = account_mod.avatar_file()
            if path.exists():
                src = QPixmap(str(path))
        except Exception:  # noqa: BLE001
            src = QPixmap()
    if src.isNull():
        return _default_pixmap(size, dpr, avatar_name(acc), bool(acc))

    target = max(1, round(size * dpr))
    # zoom：先按 1.12 之类的比例放大再裁，等于"把画面推近一点"
    big = max(1, round(target * max(1.0, float(zoom))))
    # 两段式缩小（和浏览器一致）：原图 → 最终尺寸的 4 倍 → 面积平均到最终尺寸。
    # 原图本身就比 4 倍小的时候直接一步到位，别放大再缩。
    mid = min(max(big * 4, big), max(src.width(), src.height()))
    if mid < max(src.width(), src.height()):
        src = src.scaled(mid, mid, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    scaled = src.scaled(big, big, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    # 关键：scaled 是「物理像素」（big = size*dpr），必须声明自己的密度，
    # 否则 drawPixmap 会把「物理像素数」当逻辑像素数画：
    # dpr=1.25 时画面被放大 1.25 倍、只剩左上角 80%（2026-10-07 用户实测"还是放大"的根因）。
    scaled.setDevicePixelRatio(dpr)

    pm = QPixmap(target, target)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(clip)
    # shift_y 为正 = 画面下移（露更多下方/肩部）。
    # 偏移必须按「画布尺寸 - 画面尺寸」算，自动是负的（画面比画布大时往左上偏）；
    # 以前写成 (scaled/2 - size/2) 是正号，放大后画面会被推到右下、左侧露白（2026-10-07 踩过）。
    # drawPixmap 的坐标是逻辑像素：先换算中心偏移，再交给 Qt 自动乘 dpr。
    dx = (size - scaled.width() / dpr) / 2
    dy = (size - scaled.height() / dpr) / 2 + float(shift_y)
    p.drawPixmap(int(round(dx)), int(round(dy)), scaled)
    p.end()
    return pm
