# -*- coding: utf-8 -*-
"""启动 / 退出动画：纯色卡片 + 文字字标 + **点阵**（无边框弹窗，不铺满屏幕、不放图标）。

老板 10-10 晚的要求（在原有启动卡基础上加）：
  · 点阵**铺满**加载卡片，点的亮度像**波浪**一样扫过（还会微微偏色）；
  · 卡片**中间下方**用点阵**组成文字**（启动时是「启动中」）；
  · 点阵文字**后面**再放一个**和文字等大**的加载指示，同样由点阵构成 ——
    **明确不要那种圆圈转圈的 low 加载**；
  · 关闭程序的弹窗同样处理，并且**关得更快**：程序窗口先关，剩下的后台静默收尾。

设计纪律（前面踩过坑，别改回去）：
  1. 启动动画必须自己在建主窗口前把事件循环泵起来（play_intro），否则主线程一被占住
     就连第一帧都画不出来；结尾有"定稿停留"逐帧强刷，否则屏幕上留的是半截画面；
  2. **主窗口构建期间 GUI 线程被占满、点阵不会再动** —— 所以波浪要在进入构建前**收敛**
     （幅度收到 0），屏幕上停留的那一帧看起来是"平静的加载画面"，而不是"卡住的动画"；
  3. 关闭流程绝不挂在动画回调上（另有定时器死闸），否则会留下僵尸进程；
  4. 「设置 → 减少界面动效」关掉时：启动动画整段跳过、关闭卡退回静态。
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import (
    Property, QPropertyAnimation, QPointF, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import motion

# ---------------------------------------------------------------- 纯色卡片 + 字标
COLOR_FIELD = QColor(0, 0, 0)            # 卡片纯色底（要换色只改这一行）
COLOR_INK = QColor(255, 255, 255)        # 字标：纯白
COLOR_RULE = QColor(255, 255, 255, 46)   # 细线：低对比
COLOR_EDGE = QColor(255, 255, 255, 18)   # 卡片 1px 描边
COLOR_ACCENT = QColor(61, 107, 255)      # 品牌蓝：只用在"文字后面的进度点"上
WORDMARK = "星巢"                         # 第一行：中文软件名（英雄）
LATIN = "NEST"                           # 第二行：拉丁写法
SUPPORT = "机器人控制台"                   # 支撑行（关闭卡不用）

CARD_W, CARD_H = 520, 300                # 卡片尺寸（不铺满屏幕）
CARD_RADIUS = 16.0

# 卡片内版式（局部坐标）
WORD_SIZE = 50                           # 中文软件名字号
WORD_BASE_Y = 116.0                      # 第一行基线
LATIN_SIZE = 18                          # 第二行字号
LATIN_BASE_Y = 154.0                     # 第二行基线
LATIN_TRACK = 134                        # 字距：Qt 里 100=原样，134=加宽 34%（别写 34）
RULE_Y = 180.0                           # 细线

# ---- 点阵参数
DOT_SPACING = 6.5                        # 点阵间距（密到能让笔画有 2~3 个点，文字才认得出来）
DOT_RADIUS = 1.30                        # 点半径（文字点会略大）
DOT_MARGIN = 12.0                        # 点阵离卡片边
DOT_DENSE = 3.2                          # 文字/进度条区域的加密网格（笔画才有 2~3 个点宽）
DOT_ALPHA_REST = 0.085                   # 静息点的亮度
DOT_ALPHA_WAVE = 0.42                    # 波浪峰值额外加多少
DOT_WAVE_TINT = 0.45                     # 波峰偏多少品牌蓝（0=纯白）
DOT_ALPHA_BAR_EMPTY = 0.16               # 进度条未填充的点
DOT_ALPHA_BAR_FILL = 1.0                 # 进度条已填充的点

# ---- 点阵文字 / 进度条
TEXT_SIZE = 22                           # 状态文字字号（普通文字，点阵只做底纹与进度条）
TEXT_BASELINE = 250.0                    # 文字基线（中间下方）
TEXT_BOX_H = 34.0                        # 文字外框高度（进度条与它等宽）
BAR_PAD_Y = 6.0                          # 进度条比文字框上下各收一点（等大：宽一致）

# ---- 时序（秒）
T_CARD_IN = 0.30                         # 卡片淡入
T_WORD_START = 0.20                      # 字标起步
T_LETTER = 0.16                          # 中文两字错峰
T_LATIN_START = 0.58                     # 拉丁行起步
T_LATIN_LETTER = 0.07
T_LETTER_DUR = 0.58                      # 单字上浮时长
RULE_START = 1.10                        # 细线展开
DOTS_IN = 0.95                           # 点阵整体淡入
DOTS_IN_DUR = 0.55
TEXT_START = 1.35                        # 点阵文字开始逐点亮起
TEXT_DUR = 0.45                          # 文字淡入时长
BAR_START = 1.55                         # 进度点开始填充
BAR_DUR = 1.45                           # 填到 92% 需要这么久（剩下的等主窗口就绪）
BAR_CAP = 0.92
WAVE_SETTLE = 2.55                       # 波浪从这一刻开始收敛（构建前要停下来）
WAVE_SETTLE_DUR = 0.45
INTRO_SECONDS = 3.05                     # 构建主窗口前播多久（播完帧是"平静"的）
MIN_ONSCREEN = 3.20                      # 卡片至少显示这么久
FINISH_MS = 300                          # 主窗口就绪后交叉淡出


def _dbg(msg: str) -> None:
    """诊断开关：QBM_SPLASH_DEBUG=1 时把动画生命周期写进 %TEMP%\\splash_dbg.log。"""
    import os

    if os.environ.get("QBM_SPLASH_DEBUG") != "1":
        return
    try:
        p = Path(os.environ.get("TEMP", ".")) / "splash_dbg.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {time.time():.3f} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _ease_out_expo(t: float) -> float:
    t = _clamp(t)
    return 1.0 if t >= 1.0 else 1.0 - pow(2.0, -10.0 * t)


def _ease_out_cubic(t: float) -> float:
    t = _clamp(t)
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out(t: float) -> float:
    t = _clamp(t)
    return 3 * t * t - 2 * t * t * t


# ================================================================ 点阵
class _DotField:
    """铺满卡片的点阵：波浪亮度 + 由点组成文字 + 文字后面等大的进度点。

    性能：一帧一千多个点，逐点 drawEllipse 太慢；这里按亮度分档
    （亮度量化成 1/24），每档攒成一组用 drawPoints 一次画完。
    """

    def __init__(self, w: float, h: float, spacing: float = DOT_SPACING,
                 margin: float = DOT_MARGIN):
        self.w, self.h = float(w), float(h)
        self.spacing = spacing
        self.margin = margin
        self.points: list[tuple[float, float]] = []
        self.text_pts: set[int] = set()          # 落在文字上的点
        self.text_order: dict[int, float] = {}   # 点在文字里的横向进度（0..1），用于逐点亮起
        self.bar_rect: QRectF | None = None
        self.text_rect: QRectF | None = None
        self._build(None)

    # ---------------------------------------------------------- 点阵构建
    def _grid(self, spacing: float, skip: QRectF | None = None,
              only: QRectF | None = None) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        y = self.margin
        while y <= self.h - self.margin:
            x = self.margin
            while x <= self.w - self.margin:
                if only is not None:
                    if only.contains(x, y):
                        out.append((x, y))
                elif skip is None or not skip.contains(x, y):
                    out.append((x, y))
                x += spacing
            y += spacing
        return out

    def _build(self, box: QRectF | None):
        """box 非空：底纹挖掉这一块，改用密网格铺满它（文字 + 进度条都在这儿）。

        双密度是"点阵文字认得出"的关键：疏网格（6.5px）下 42px 粗体笔画
        只有 1 个点宽，形不成字；密网格（3.2px）才有 2~3 个点宽。
        """
        if box is None:
            self.points = self._grid(self.spacing)
            return
        hole = QRectF(box.left() - self.spacing * 0.6, box.top() - self.spacing * 0.6,
                      box.width() + self.spacing * 1.2, box.height() + self.spacing * 1.2)
        self.points = self._grid(self.spacing, skip=hole) + self._grid(DOT_DENSE, only=hole)

    # ---------------------------------------------------------- 文字掩码
    def set_text(self, text: str, font: QFont, cx: float, baseline: float,
                 box_h: float = TEXT_BOX_H):
        """只算**位置**：文字本身用普通文字画（老板 10-10 否掉了"点组成的字"，说太丑）。

        点阵只负责两件事：① 铺满卡片的波浪底纹；② 文字后面那个"和文字等大"的
        点阵进度条。所以这里只要文字外框 + 进度条矩形，不再采样字形掩码。
        """
        self.text_pts = set()
        self.text_order = {}
        if not text:
            self.bar_rect = self.text_rect = None
            self._build(None)
            return
        fm = QFontMetricsF(font)
        tw = fm.horizontalAdvance(text)
        left = cx - tw / 2.0
        top = baseline - fm.ascent()
        box = QRectF(left - 6, top - 4, tw + 12, box_h)
        self._build(box)
        self.text_rect = box
        # 进度条：和文字**等宽**（老板要求"大小与文字相等"），高度略收，就在文字后面
        self.bar_rect = QRectF(box.left(), box.top() + BAR_PAD_Y,
                               box.width(), max(8.0, box.height() - BAR_PAD_Y * 2))

    # ---------------------------------------------------------- 波浪
    @staticmethod
    def _wave(x: float, y: float, t: float) -> float:
        """斜向行波：0..1。"""
        return 0.5 + 0.5 * math.sin((x * 0.055 + y * 0.085) - t * 2.2)

    def paint(self, p: QPainter, t: float, progress: float, opacity: float = 1.0,
              wave_gain: float = 1.0):
        """画点阵：波浪底纹 + 文字后面等大的进度条（文字不归点阵管，见 set_text）。"""
        if opacity <= 0.002:
            return
        buckets: dict[int, list[QPointF]] = {}
        r_bar = DOT_RADIUS * 0.62
        r_dot = DOT_RADIUS
        fill_x = None
        if self.bar_rect is not None:
            fill_x = self.bar_rect.left() + self.bar_rect.width() * _clamp(progress)

        for x, y in self.points:
            in_bar = self.bar_rect is not None and self.bar_rect.contains(x, y)
            if in_bar:
                a = DOT_ALPHA_BAR_FILL if x <= fill_x else DOT_ALPHA_BAR_EMPTY
                col = COLOR_ACCENT if x <= fill_x else COLOR_INK
                r = r_bar
            else:
                wv = self._wave(x, y, t) * wave_gain
                a = DOT_ALPHA_REST + DOT_ALPHA_WAVE * wv
                # 波峰偏一点品牌蓝 —— "显色像波浪"
                k = wv * wave_gain
                col = QColor(
                    int(255 + (COLOR_ACCENT.red() - 255) * DOT_WAVE_TINT * k),
                    int(255 + (COLOR_ACCENT.green() - 255) * DOT_WAVE_TINT * k),
                    255,
                )
                r = r_dot
            a *= opacity
            aq = int(_clamp(a) * 24)
            if aq <= 0:
                continue
            # 分档键必须把颜色和半径也算进去：进度点（蓝、较小）和底点（白、较大）
            # 亮度可能撞档，混在一组会用错颜色/半径。
            key = (aq, col.rgb(), int(round(r * 10)))
            buckets.setdefault(key, []).append((QPointF(x, y), col, r))  # type: ignore[arg-type]

        for (aq, _rgb, _r10), items in buckets.items():
            col = items[0][1]
            r = items[0][2]
            c = QColor(col)
            c.setAlphaF(min(1.0, aq / 24.0))
            pen = QPen(c)
            pen.setWidthF(r * 2.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawPoints([q for q, _c, _r in items])


DOT_MATRIX = None      # 兼容旧引用：点阵改成每张卡各自一份（见 _CardBase.dot_field）


# ================================================================ 卡片基类
class _CardBase(QWidget):
    """无边框纯色卡片：负责窗口标志、居中、卡片本体、淡入淡出、动画时钟。"""

    def __init__(self, parent=None, splash: bool = True):
        super().__init__(parent)
        # 用普通工具窗，**不置顶**：老板 2026-10-10 反馈"启动卡浮在最上面，把别的窗口都遮住"。
        # 原来用 Qt.SplashScreen | Qt.WindowStaysOnTopHint（= WS_EX_TOPMOST），会盖住所有程序
        # （之前那个"报错框被启动卡盖住、点不着"也是同一个原因）。
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(CARD_W, CARD_H)
        self._t = 0.0
        self._reveal = 1.0
        self._fade = None
        self._outp = 0.0          # 退场：0=正常，1=完全淡掉（画出来的，不依赖窗口透明度）
        # 点阵：每张卡各自一份（启动卡和关闭卡的文字不同，不能共用一份掩码）
        self.dot_field = _DotField(CARD_W, CARD_H)
        # 内部时钟：只要事件循环在转（play_intro / 最小显示循环 / 正常运行）
        # 动画就自己往前走 —— 不依赖任何外部 tick。
        # 看门狗：万一后面哪一步卡住/抛异常，启动卡也不能一直盖在屏幕上关不掉
        # （老板 2026-10-10：报错弹窗被启动卡盖住，点不着也看不清）。
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(25000)
        self._watchdog.timeout.connect(self._watchdog_fire)
        self._watchdog.start()
        self._clock = QTimer(self)
        self._clock.setInterval(16)
        self._clock.timeout.connect(self._tick)
        self._t0 = time.time()
        self._running = False

    # ------------------------------------------------------------ 时钟
    def _start_clock(self):
        self._t0 = time.time()
        self._running = True
        if motion.enabled():
            self._clock.start()

    def _stop_clock(self):
        self._running = False
        try:
            self._clock.stop()
        except Exception:  # noqa: BLE001
            pass

    def _tick(self):
        if not self._running:
            return
        self._t = time.time() - self._t0
        self.update()

    # ------------------------------------------------------------ 通用
    def _watchdog_fire(self):
        """超时兜底：收掉卡片并记一笔，别让它一直挡住别的窗口。"""
        _dbg("启动卡看门狗触发（25s）：自动收起")
        self._stop_clock()
        try:
            self.hide()
        except Exception:  # noqa: BLE001
            pass

    def abort(self):
        """异常路径用：立刻把卡片收掉（让报错框能被看见、被点到）。"""
        try:
            self._watchdog.stop()
        except Exception:  # noqa: BLE001
            pass
        self._stop_clock()
        self._reveal = 1.0
        try:
            self.hide()
            self.deleteLater()
        except Exception:  # noqa: BLE001
            pass

    def _center_on_screen(self):
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        geo = scr.availableGeometry()
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)

    def _text_font(self, size: int = TEXT_SIZE, weight=QFont.Weight.Bold) -> QFont:
        f = QFont()
        f.setFamilies(["Microsoft YaHei UI", "Segoe UI", "Arial"])
        f.setPixelSize(size)
        f.setWeight(weight)
        return f

    def _word_font(self) -> QFont:
        f = self._text_font(WORD_SIZE)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 98.5)
        return f

    def _latin_font(self) -> QFont:
        f = QFont()
        f.setFamilies(["Segoe UI", "Arial", "Microsoft YaHei UI"])
        f.setPixelSize(LATIN_SIZE)
        f.setWeight(QFont.Weight.Medium)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, LATIN_TRACK)
        return f

    def _support_font(self) -> QFont:
        f = QFont()
        f.setFamilies(["Microsoft YaHei UI", "Segoe UI"])
        f.setPixelSize(13)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
        return f

    def _status_font(self) -> QFont:
        """状态文字（启动中 / 正在退出…）：普通文字，别再塞进点阵里。"""
        f = QFont()
        f.setFamilies(["Microsoft YaHei UI", "Segoe UI"])
        f.setPixelSize(TEXT_SIZE)
        f.setWeight(QFont.Weight.Medium)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        return f

    def _draw_status_text(self, p: QPainter, text: str, t: float, start: float,
                          alpha: float = 0.92):
        """淡入 + 轻微上浮（点阵文字已按老板要求换成纯文字）。"""
        k = _ease_out_cubic((t - start) / TEXT_DUR) if motion.enabled() else 1.0
        if k <= 0.001 or not text:
            return
        f = self._status_font()
        fm = QFontMetricsF(f)
        p.setFont(f)
        c = QColor(COLOR_INK)
        c.setAlphaF(min(1.0, alpha * k))
        p.setPen(c)
        top = self.dot_field.text_rect.top() if self.dot_field.text_rect is not None \
            else TEXT_BASELINE - fm.ascent()
        p.drawText(QRectF(0, top + (1.0 - k) * 7.0, self.width(), fm.height() + 8),
                   Qt.AlignHCenter | Qt.AlignTop, text)

    def _draw_word(self, p: QPainter, text: str, font: QFont, base_y: float,
                   start: float, stagger: float, t: float, box_h_pad: float = 10.0):
        """一行字：逐字从下方遮罩里升起（唯一技法）。"""
        p.setFont(font)
        fm = QFontMetricsF(font)
        # 字距只在"整串"上生效：单字的 horizontalAdvance 不含额外字距，
        # 用"前缀宽度差"当每个字的步进，否则带字距的那行会挤成一团。
        total = fm.horizontalAdvance(text)
        x0 = self.width() / 2 - total / 2
        for i, ch in enumerate(text):
            prev = fm.horizontalAdvance(text[:i]) if i else 0.0
            cw = fm.horizontalAdvance(text[:i + 1]) - prev
            k = _ease_out_expo((t - start - i * stagger) / T_LETTER_DUR)
            if k > 0.001:
                x = x0 + prev
                top = base_y - fm.ascent()
                p.save()
                p.setClipRect(QRectF(x - 6, top, cw + 12, fm.height() + box_h_pad))
                dy = (1.0 - k) * fm.height() * 1.30
                c = QColor(COLOR_INK)
                c.setAlphaF(min(1.0, 0.10 + 0.90 * k))
                p.setPen(c)
                p.drawText(QRectF(x, top + dy, cw + 12, fm.height() + box_h_pad),
                           Qt.AlignLeft | Qt.AlignTop, ch)
                p.restore()

    def _paint_card_body(self, p: QPainter):
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), CARD_RADIUS, CARD_RADIUS)
        p.fillPath(path, COLOR_FIELD)
        p.setPen(QColor(COLOR_EDGE))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def paintEvent(self, _ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        cx = w / 2
        t = self._t

        k_card = _ease_out_cubic(self._reveal) if self._reveal < 1.0 else 1.0
        k_out = max(0.0, 1.0 - self._outp)
        if k_card * k_out <= 0.001:
            p.end()
            return
        p.setOpacity(k_card * k_out)
        p.translate(cx, h / 2)
        s = 0.98 + 0.02 * k_card
        p.scale(s, s)
        p.translate(-cx, -h / 2)

        self._paint_card_body(p)
        self._paint_content(p, t)
        p.end()

    def _paint_content(self, p: QPainter, t: float):
        """子类实现：卡片里的东西。"""

    # ------------------------------------------------------------ 过渡
    def _get_outp(self) -> float:
        return self._outp

    def _set_outp(self, v: float):
        self._outp = float(v)
        self.update()

    # 退场用画出来的透明度：离屏/无合成器环境下 windowOpacity 动画不生效，
    # 关闭卡片在测试里就会收不了尾（实测踩过）。
    outp = Property(float, _get_outp, _set_outp)

    def _fade_out(self, ms: int, on_done=None, curve=None):
        dur = motion.duration(ms)
        if dur <= 0:
            if on_done:
                on_done()
            return
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(dur)
        a.setStartValue(self.windowOpacity())
        a.setEndValue(0.0)
        a.setEasingCurve(curve if curve is not None else motion.EASE_IN)
        if on_done:
            a.finished.connect(on_done)
        a.start()
        self._fade = a


# ================================================================ 启动卡
class LaunchSplash(_CardBase):
    """启动：卡片淡入 → 字标逐字上浮 → 细线 → **点阵铺满 + 波浪** →
    点阵文字「启动中」逐点亮起 + 文字后面等大的点阵进度条。"""

    closed = Signal()

    def __init__(self, app_version: str = "", parent=None):
        super().__init__(parent, splash=True)
        self._version = app_version or ""
        self._closing = False
        self._text = "启动中"
        self._progress = 0.0
        self._ready = False
        self.dot_field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)

    # ------------------------------------------------------------ 生命周期
    def begin(self):
        self._center_on_screen()
        try:
            self.setWindowOpacity(1.0)
        except Exception:  # noqa: BLE001
            pass
        self._reveal = 0.0 if motion.enabled() else 1.0
        if not motion.enabled():
            self._t = INTRO_SECONDS
            self._progress = 1.0
        self.show()
        self._start_clock()
        _dbg("begin: show() 完成，点阵时钟已启动")
        return self

    def play_intro(self):
        """把这段揭示**真正播完**再返回（主窗口构建会独占主线程）。

        播完时的画面必须是"平静的"：波浪收敛、文字已亮、进度到 92% ——
        因为接下来构建主窗口时这一帧会停在屏幕上好几秒。
        """
        if not motion.enabled():
            self._t = INTRO_SECONDS
            self._reveal = 1.0
            self._progress = BAR_CAP
            self.update()
            return
        t0 = time.time()
        app = QApplication.instance()
        while True:
            el = time.time() - t0
            self._t = el
            self._reveal = _ease_out_cubic(el / T_CARD_IN)
            self._progress = min(BAR_CAP, _ease_out_cubic((el - BAR_START) / BAR_DUR) * BAR_CAP)
            self.update()
            if app is not None:
                app.processEvents()
            if el >= INTRO_SECONDS:
                break
            time.sleep(0.006)
        self._t = INTRO_SECONDS
        self._reveal = 1.0
        self._progress = BAR_CAP
        self.update()
        if app is not None:
            app.processEvents()
        # 定稿帧停留：逐帧强刷一小会儿，避免屏幕上留下"动到一半"的画面
        hold_until = time.time() + 0.20
        while time.time() < hold_until:
            try:
                self.repaint()
            except Exception:  # noqa: BLE001
                break
            if app is not None:
                app.processEvents()
            time.sleep(0.016)
        _dbg(f"intro 播完 {time.time() - t0:.2f}s（点阵文字 + 进度 {self._progress:.0%}）")

    def set_ready(self):
        """主窗口已就绪：进度补满（衔接淡出）。"""
        self._ready = True
        self._progress = 1.0
        self.update()

    def finish(self, on_done=None):
        if self._closing:
            return
        self._closing = True
        self.set_ready()
        _dbg("finish() 被调用")

        def _after():
            self._stop_clock()
            self.hide()
            self.closed.emit()
            if on_done is not None:
                try:
                    on_done()
                except Exception:  # noqa: BLE001
                    pass
            QTimer.singleShot(0, self.deleteLater)

        self._fade_out(FINISH_MS, on_done=_after, curve=motion.EASE_OUT)

    # ------------------------------------------------------------ 画
    def _paint_content(self, p: QPainter, t: float):
        w = self.width()
        cx = w / 2
        # 点阵先画（在字标下面，亮度低，不抢字标）
        k_dots = _ease_out_cubic((t - DOTS_IN) / DOTS_IN_DUR) if motion.enabled() else 1.0
        # 波浪在构建主窗口前收敛，屏幕上停住的那一帧才是"平静"的
        gain = 1.0 - _ease_in_out((t - WAVE_SETTLE) / WAVE_SETTLE_DUR)
        self.dot_field.paint(p, t, self._progress, opacity=k_dots,
                             wave_gain=max(0.0, gain))
        # 文字：普通文字（老板否掉了点阵字），画在点阵之上
        self._draw_status_text(p, self._text, t, TEXT_START)

        # 字标（英雄）
        self._draw_word(p, WORDMARK, self._word_font(), WORD_BASE_Y,
                        T_WORD_START, T_LETTER, t, box_h_pad=14.0)
        self._draw_word(p, LATIN, self._latin_font(), LATIN_BASE_Y,
                        T_LATIN_START, T_LATIN_LETTER, t)
        k_rule = _ease_out_cubic((t - RULE_START) / 0.5) if motion.enabled() else 1.0
        if k_rule > 0:
            p.setPen(QColor(COLOR_RULE.red(), COLOR_RULE.green(), COLOR_RULE.blue(),
                            int(COLOR_RULE.alpha() * k_rule)))
            p.drawLine(QPointF(cx - 30 * k_rule, RULE_Y), QPointF(cx + 30 * k_rule, RULE_Y))


# ================================================================ 关闭卡
class ClosingCard(_CardBase):
    """退出：同一套点阵 —— 文字（正在退出 / 已退出）+ 文字后面等大的点阵进度条。

    刻意做**短**：老板嫌关得慢（"关快点，实在不行把关闭动画剪短点"）。
    整张卡的生命周期由调用方控制（默认 700ms 左右）。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent, splash=False)
        self._text = text or "正在退出"
        self._progress = 0.0
        self._t = 0.0
        self._field = self.dot_field
        self._field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)

    # ------------------------------------------------------------ 属性（动画用）
    def _get_progress(self) -> float:
        return self._t

    def _set_progress(self, v: float):
        self._t = float(v)
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    # ------------------------------------------------------------ 生命周期
    def set_text(self, text: str):
        """换一句点阵文字（点会重新亮一遍，快速）。"""
        self._text = text or self._text
        self._field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)
        self._t0 = time.time() - TEXT_START   # 让新文字从头亮起
        self.update()

    def show_card(self, text: str | None = None):
        if text:
            self._text = text
            self._field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)
        self._center_on_screen()
        self._t = 0.0
        self._reveal = 1.0
        try:
            self.setWindowOpacity(1.0)
        except Exception:  # noqa: BLE001
            pass
        self.show()
        self._start_clock()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        return self

    def set_progress(self, v: float):
        self._progress = _clamp(v)
        self.update()

    def _paint_content(self, p: QPainter, t: float):
        if motion.enabled():
            # 关闭时的进度点：0.55s 填满（比启动快，因为要尽快退）
            self._progress = min(1.0, _ease_out_cubic(t / 0.55))
        else:
            self._progress = 1.0
        self._field.paint(p, max(0.6, t), self._progress, opacity=1.0, wave_gain=1.0)
        self._draw_status_text(p, self._text, t, 0.05, alpha=0.95)

    # ------------------------------------------------------------ 结束
    def dissolve_and_close(self, ms: int | None = None, on_done=None):
        """快速收尾：点阵淡出即可（不再有长动画；退不退出由死闸保证）。"""
        dur = motion.duration(220 if ms is None else min(int(ms), 260))
        self._stop_clock()
        if dur <= 0:
            self._end(on_done)
            return
        a = QPropertyAnimation(self, b"outp", self)
        a.setDuration(int(dur))
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(motion.EASE_IN)
        a.finished.connect(lambda: self._end(on_done))
        a.start()
        self._dissolve_anim = a

    def _end(self, on_done=None):
        self._stop_clock()
        self.hide()
        if on_done is not None:
            try:
                on_done()
            except Exception:  # noqa: BLE001
                pass
        QTimer.singleShot(0, self.deleteLater)


class UpdateCard(_CardBase):
    """更新进度卡片：和启动卡同一套点阵（底纹波浪 + 纯文字 + 文字后面等大的点阵进度条）。

    老板 2026-10-10：程序内直接下载更新，进度用"和启动弹窗一样的风格"显示。
    """

    def __init__(self, parent=None):
        super().__init__(parent, splash=False)
        self._text = "正在下载更新"
        self._progress = 0.0
        self.dot_field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)

    def show_card(self, text: str | None = None):
        if text:
            self._text = text
            self.dot_field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)
        self._center_on_screen()
        self._t = 0.0
        self._reveal = 1.0
        self._outp = 0.0
        try:
            self.setWindowOpacity(1.0)
        except Exception:  # noqa: BLE001
            pass
        self.show()
        self._start_clock()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        return self

    def set_text(self, text: str):
        """换一句说明（文字重新亮一遍，很快）。"""
        if not text or text == self._text:
            return
        self._text = text
        self.dot_field.set_text(self._text, self._text_font(), CARD_W / 2, TEXT_BASELINE)
        self._t0 = time.time() - TEXT_START
        self.update()

    def set_progress(self, ratio: float):
        self._progress = _clamp(float(ratio))
        self.update()

    def close_card(self):
        self._stop_clock()
        try:
            self.hide()
        except Exception:  # noqa: BLE001
            pass

    def _paint_content(self, p: QPainter, t: float):
        # 波浪一直在走（这里不像启动卡会被主窗口构建阻塞），进度条跟着下载走
        self.dot_field.paint(p, t, self._progress, opacity=1.0, wave_gain=1.0)
        self._draw_status_text(p, self._text, t, 0.05, alpha=0.95)


def fade_window(win: QWidget, start: float, end: float, ms: int, on_done=None):
    """给任意窗口做透明度过渡（失败一律忽略，绝不影响退出流程）。"""
    try:
        dur = motion.duration(ms)
        if dur <= 0 or win is None:
            if on_done:
                on_done()
            return None
        win.setWindowOpacity(float(start))
        anim = QPropertyAnimation(win, b"windowOpacity", win)
        anim.setDuration(dur)
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setEasingCurve(motion.EASE_OUT if end >= start else motion.EASE_IN)
        if on_done:
            anim.finished.connect(on_done)
        anim.start()
        win._splash_fade = anim        # 挂引用防 GC
        return anim
    except Exception:  # noqa: BLE001
        if on_done:
            try:
                on_done()
            except Exception:  # noqa: BLE001
                pass
        return None
