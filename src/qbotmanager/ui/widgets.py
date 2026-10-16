"""可复用 UI 控件：玻璃面板、状态徽章、平滑进度条、空状态。"""
import math
import random

from PySide6.QtCore import (
    QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPalette, QPen,
    QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QCheckBox, QComboBox, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from . import motion
from . import theme as _theme
from .theme import RADIUS, TEXT_2, TEXT_3, status_color


_NOISE_PIX: QPixmap | None = None

# QWIDGETSIZE_MAX：展开到位后放开高度上限，里面的内容之后长高也不会被裁
_MAX_WIDGET_HEIGHT = 16777215


class WheelGuard(QObject):
    """滚轮不吃「没点过」的下拉框 / 数值框 / 滑条，也不被展开的下拉浮层困住。

    页面能滚动时，滚轮扫过下拉框会悄悄改掉筛选或模型，用户看着像程序自己在跳；
    反过来，展开的下拉列表是独立浮层（Qt::Popup），鼠标压在上面时滚轮只滚那张
    列表，页面完全滚不动，得先把鼠标挪开 —— 这两种都算「滚轮被控件吃掉」。

    规矩：
      · 展开的列表自己还有得滚 → 让它滚；
      · 滚到头顶 / 到底、或者列表本来就短 → 收起浮层，这一下滚轮交给页面；
      · 下拉框一律不靠滚轮改值（想改就点开列表点一下）；数值框 / 滑条只在
        拿到焦点（点过）时才允许滚轮改值；
      · 其余滚轮一律转交给光标底下最近的、真的滚得动的滚动区；找不到就吃掉，
        总之不许落到控件值上。
    """

    _GUARDED = (QComboBox, QAbstractSpinBox, QSlider)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel or not isinstance(obj, QWidget):
            return False
        combo, view = self._popup_combo(obj)
        if view is not None:
            return self._popup_wheel(combo, view, event)
        guarded = self._guarded_ancestor(obj)
        if guarded is not None and (
                isinstance(guarded, QComboBox) or not guarded.hasFocus()):
            return self._scroll_page(guarded, event)
        return False

    def _guarded_ancestor(self, obj):
        """滚轮落点可能在这些控件内部的小输入框上（可编辑下拉框、数值框），
        事件会顺着父子链传回控件本体，所以往上找一层再判。"""
        widget = obj
        for _ in range(3):
            if isinstance(widget, self._GUARDED):
                return widget
            widget = widget.parentWidget()
            if widget is None:
                break
        return None

    @staticmethod
    def _popup_combo(obj):
        """光标落在展开的下拉浮层里 → 返回 (下拉框, 展开的列表)，否则 (None, None)。

        浮层是独立的顶层窗口（Qt::Popup），它的 parentWidget 就是那个下拉框。
        """
        window = obj.window()
        if window is obj or not (window.windowFlags() & Qt.WindowType.Popup):
            return None, None
        owner = window.parentWidget()
        while owner is not None:
            if isinstance(owner, QComboBox):
                return owner, owner.view()
            owner = owner.parentWidget()
        return None, None

    def _popup_wheel(self, combo, view, event) -> bool:
        """展开的列表：自己滚得动就自己滚；滚到头 / 没得滚 → 收起浮层，滚轮交给页面。"""
        step = event.angleDelta().y() or event.pixelDelta().y()
        bar = view.verticalScrollBar()
        if step and bar is not None and bar.maximum() > bar.minimum():
            at_end = (bar.value() >= bar.maximum() if step < 0
                      else bar.value() <= bar.minimum())
            if not at_end:
                return False
        if combo is not None:
            combo.hidePopup()
        return self._scroll_page(combo or view, event)

    @staticmethod
    def _scroll_page(widget, event) -> bool:
        """把这一下滚轮交给最近的、真的滚得动的滚动区（滚不动就继续往外找）。"""
        area = widget
        while area is not None:
            if isinstance(area, QAbstractScrollArea):
                bar = area.verticalScrollBar()
                if bar is not None and bar.maximum() > bar.minimum():
                    angle = event.angleDelta().y()
                    notches = angle / 120.0 if angle else event.pixelDelta().y() / 40.0
                    if notches:
                        step = bar.singleStep() or 20
                        bar.setValue(int(bar.value() - round(notches * step * 3)))
                    return True
            area = area.parentWidget()
        return True


class BusyDots(QWidget):
    """长任务在跑时，贴在按钮 / 状态文字后面的三个"呼吸点"。

    以前装了插件、扫依赖、生成插件这几件几十秒的事，界面只是变灰 + 改文字，
    看起来像卡死。这个点在跑的时候一直呼吸，用户一眼就知道"它在干活"。
    设置里关掉动效后仍然显示，只是不再呼吸 —— 状态信息不该因为省动效而消失。
    """

    _PERIOD = motion.DUR_BUSY_LOOP
    _TICK = 33

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(24, 10)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self._place()
        self.show()
        self.raise_()
        if motion.enabled():
            self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._phase = (self._phase + self._TICK / self._PERIOD) % 1.0
        self._place()
        self.update()

    def _place(self):
        """贴着父控件的文字右侧；文字变长/按钮变窄都靠每帧重算兜住。"""
        parent = self.parentWidget()
        if parent is None:
            return
        text = parent.text() if hasattr(parent, "text") else ""
        try:
            width = parent.fontMetrics().horizontalAdvance(text) if text else 0
        except Exception:  # noqa: BLE001 —— 极端字体/空文本不该让界面炸
            width = 0
        if isinstance(parent, QPushButton):
            x = (parent.width() + width) // 2 + 6
        else:
            x = parent.contentsMargins().left() + width + 6
        x = max(0, min(x, parent.width() - self.width() - 2))
        self.move(x, max(0, (parent.height() - self.height()) // 2))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        accent = QColor(_theme.ACCENT)
        for i in range(3):
            phase = (self._phase + i * 0.18) % 1.0
            wave = 0.5 - 0.5 * math.cos(2 * math.pi * phase)   # 0→1→0 呼吸
            p.setBrush(QColor(accent.red(), accent.green(), accent.blue(),
                              int(70 + 165 * wave)))
            p.drawEllipse(QPointF(4 + i * 7, self.height() / 2.0),
                          2.0 + 0.9 * wave, 2.0 + 0.9 * wave)
        p.end()


def set_busy(widget, busy: bool) -> None:
    """长任务开关：busy=True 挂上呼吸点，False 摘掉。可重复调用、可传 None。"""
    if widget is None:
        return
    dots = getattr(widget, "_busy_dots", None)
    if busy:
        if dots is None:
            dots = BusyDots(widget)
            widget._busy_dots = dots
        dots.start()
    elif dots is not None:
        dots.stop()


class FoldSection(QWidget):
    """折叠区：一行 ghost 按钮 + 默认收起的内容（QQ 页 / AI 大脑页共用）。

    按钮太多的页面用它把次要动作收起来，首屏只留主流程；
    展开状态记在自己身上，不被父页面刷新重置。
    """

    def __init__(self, closed_text: str, open_text: str, content: QWidget, parent=None):
        super().__init__(parent)
        self._closed = closed_text
        self._open = open_text
        self._content = content
        self._expanded = False
        self._anim: QVariantAnimation | None = None
        self._effect: QGraphicsOpacityEffect | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.btn = QPushButton(closed_text)
        self.btn.setObjectName("ghost")
        self.btn.clicked.connect(self.toggle)
        # 左对齐的窄条，不要拉成一条占满整行的空框（看起来像输入框）
        v.addWidget(self.btn, 0, Qt.AlignLeft)
        content.setVisible(False)
        content.setMaximumHeight(0)
        v.addWidget(content)

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        self._expanded = expanded
        self.btn.setText(self._open if expanded else self._closed)
        self._animate(expanded)

    # ---------------------------------------------------------- 展开/收起过场
    def _content_opacity(self, value: float) -> None:
        """只在这段过场里挂透明度效果；结束就摘掉，别让面板一直走离屏合成。"""
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self._content)
            self._effect.setOpacity(1.0)
            self._content.setGraphicsEffect(self._effect)
        self._effect.setOpacity(max(0.0, min(1.0, value)))

    def _drop_effect(self) -> None:
        if self._effect is not None:
            self._content.setGraphicsEffect(None)
            self._effect = None

    def _stop_anim(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _settle(self, expanded: bool) -> None:
        self._drop_effect()
        self._content.setMaximumHeight(_MAX_WIDGET_HEIGHT if expanded else 0)
        self._content.setVisible(expanded)

    def _animate(self, expanded: bool) -> None:
        content = self._content
        self._stop_anim()
        if not motion.enabled():
            self._settle(expanded)
            return
        start = content.height() if content.isVisible() else 0
        target = max(1, content.sizeHint().height()) if expanded else 0
        if start == target:
            self._settle(expanded)
            return
        if expanded:
            content.setMaximumHeight(start)
            content.setVisible(True)
            self._content_opacity(start / target)
        anim = QVariantAnimation(self)
        anim.setDuration(motion.DUR_RELEASE if expanded else motion.DUR_COLLAPSE)
        anim.setEasingCurve(motion.EASE_OUT if expanded else motion.EASE_IN)
        anim.setStartValue(float(start))
        anim.setEndValue(float(target))
        anim.valueChanged.connect(
            lambda v: self._on_anim(float(v), target if expanded else 0))
        anim.finished.connect(lambda: self._on_done(expanded))
        self._anim = anim
        anim.start()

    def _on_anim(self, value: float, target: int) -> None:
        self._content.setMaximumHeight(int(value))
        if target:
            self._content_opacity(value / target)

    def _on_done(self, expanded: bool) -> None:
        self._stop_anim()
        self._settle(expanded)


class ToggleSwitch(QCheckBox):
    """椭圆滑块开关：椭圆轨道 + 圆形滑块，点击即时切换。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(24)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(self.palette().color(QPalette.WindowText))
        font = self.font()
        font.setPixelSize(13)
        p.setFont(font)
        text_rect = QRectF(0, 0, max(0, self.width() - 56), self.height())
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        pill = QRectF(self.width() - 48, (self.height() - 20) / 2.0, 40, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_theme.ACCENT if self.isChecked() else "#3A4354"))
        p.drawRoundedRect(pill, 10, 10)
        knob = 14.0
        kx = pill.right() - knob - 3 if self.isChecked() else pill.left() + 3
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(kx, (self.height() - knob) / 2.0, knob, knob))
        p.end()

    def hitButton(self, pos):
        """整块控件都可点击（默认 QCheckBox 只认左侧小方框）。

        滑块画在右侧，若不重写 hitButton，用户点滑块/文字都会“没反应”，
        只剩左侧隐藏的小方框能切换——这是小白模式按钮反复“不好使”的根因。
        """
        return self.rect().contains(pos)


def _noise_pixmap() -> QPixmap:
    """生成颗粒噪声贴图（128x128，低透明度灰色颗粒）。"""
    global _NOISE_PIX
    if _NOISE_PIX is None:
        img = QImage(128, 128, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        rnd = random.Random(7)
        for y in range(128):
            for x in range(128):
                v = rnd.randint(120, 225)
                a = rnd.randint(5, 14)
                img.setPixelColor(x, y, QColor(v, v, v, a))
        _NOISE_PIX = QPixmap.fromImage(img)
    return _NOISE_PIX


def _panel_alpha(base: int) -> int:
    """把「UI 框透明度」设置映射成面板底色 alpha。

    透明度越低（滑条越小）面板越淡，但始终保留可读性下限：
    实测低于约 45% 时深色底压不住背景，列表文字会发灰发糊。
    """
    try:
        op = float(getattr(_theme, "UI_OPACITY", 0.7) or 0.7)
    except (TypeError, ValueError):
        op = 0.7
    op = min(1.0, max(0.35, op))
    scale = 1.0 + (op - 0.70) * 0.5      # 0.35→×0.83，0.70→×1.0，1.00→×1.15
    return max(112, min(248, int(round(base * scale))))


class GlassPanel(QFrame):
    """玻璃容器：支持 默认 / 液态玻璃（鼠标反光）/ 颗粒磨砂（噪声贴图）三种质感。

    由 theme.CURRENT_GLASS 决定绘制方式；液态模式下鼠标所到之处会泛出高光。
    面板全部自绘（半透明底 + 噪声 + 柔光），不做窗口截屏——截屏快照会与
    滚动/异步刷新后的真实内容错位，糊出上一层内容的残影。
    """

    def __init__(self, parent=None, strong=False):
        super().__init__(parent)
        self.setObjectName("glassStrong" if strong else "glass")
        self._strong = strong
        self._hover = QPointF(-1, -1)
        self.setMouseTracking(True)

    def refresh_now(self):
        """主题切换完成后重绘一次（面板自绘，无背景快照需要重建）。"""
        self.update()

    def childEvent(self, event):
        if event.type() == QEvent.ChildAdded and event.child() is not None:
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)
        super().childEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove:
            pos = obj.mapToGlobal(event.position().toPoint())
            self._hover = QPointF(self.mapFromGlobal(pos))
            self.update()
        elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
            self._hover = QPointF(-1, -1)
            self.update()
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event):
        self._hover = QPointF(event.position())
        self.update()

    def leaveEvent(self, event):
        self._hover = QPointF(-1, -1)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        painter.setClipPath(path)
        strong = self._strong
        mode = _theme.CURRENT_GLASS

        if mode == "liquid":
            grad = QLinearGradient(0, 0, 0, self.height())
            grad.setColorAt(0, QColor(255, 255, 255, 36))
            grad.setColorAt(0.45, QColor(17, 22, 34, _panel_alpha(107)))
            grad.setColorAt(1, QColor(10, 14, 22, _panel_alpha(153)))
            painter.fillPath(path, grad)
            # 白色透光 tint（自上而下减弱）
            tint = QLinearGradient(0, 0, 0, self.height())
            tint.setColorAt(0, QColor(255, 255, 255, 34 if strong else 26))
            tint.setColorAt(0.45, QColor(255, 255, 255, 8))
            tint.setColorAt(1, QColor(255, 255, 255, 0))
            painter.fillPath(path, tint)
            # 静态斜向高光（苹果液态玻璃的镜面反光）
            sheen = QLinearGradient(0, 0, self.width(), self.height())
            sheen.setColorAt(0, QColor(255, 255, 255, 42 if strong else 32))
            sheen.setColorAt(0.35, QColor(255, 255, 255, 8))
            sheen.setColorAt(0.6, QColor(255, 255, 255, 0))
            sheen.setColorAt(1, QColor(255, 255, 255, 6))
            painter.fillPath(path, sheen)
            # 鼠标柔光：大范围低光 + 小范围高光
            if self._hover.x() >= 0:
                glow = QRadialGradient(self._hover, 110)
                glow.setColorAt(0, QColor(255, 255, 255, 30))
                glow.setColorAt(0.5, QColor(255, 255, 255, 10))
                glow.setColorAt(1, QColor(255, 255, 255, 0))
                painter.fillPath(path, glow)
                core = QRadialGradient(self._hover, 34)
                core.setColorAt(0, QColor(255, 255, 255, 26))
                core.setColorAt(1, QColor(255, 255, 255, 0))
                painter.fillPath(path, core)
        elif mode == "frosted":
            painter.fillPath(path, QColor(19, 26, 40, _panel_alpha(132 if strong else 120)))
            painter.drawTiledPixmap(self.rect(), _noise_pixmap())
            haze = QLinearGradient(0, 0, 0, self.height())
            haze.setColorAt(0, QColor(255, 255, 255, 22 if strong else 15))
            haze.setColorAt(0.6, QColor(255, 255, 255, 4))
            haze.setColorAt(1, QColor(255, 255, 255, 0))
            painter.fillPath(path, haze)
            # 没有真实背景模糊时，用一层斜向柔光保住玻璃的镜面感
            sheen = QLinearGradient(0, 0, self.width(), self.height())
            sheen.setColorAt(0, QColor(255, 255, 255, 20 if strong else 13))
            sheen.setColorAt(0.45, QColor(255, 255, 255, 4))
            sheen.setColorAt(1, QColor(255, 255, 255, 0))
            painter.fillPath(path, sheen)
        else:
            painter.fillPath(path, QColor(19, 26, 40, _panel_alpha(199 if strong else 158)))

        painter.setClipping(False)
        border_alpha = 82 if mode == "liquid" else (41 if strong else 20)
        painter.setPen(QPen(QColor(255, 255, 255, border_alpha)))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class StatusBadge(QWidget):
    """状态徽章：色点 + 文字。set_status(key) 自动配色。"""

    def __init__(self, text="未知", color_key="unknown", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 12, 5)
        lay.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.label = QLabel(text)
        self.label.setStyleSheet(f"color: {TEXT_2}; font-size: 13px; font-weight: 600;")
        lay.addWidget(self.dot)
        lay.addWidget(self.label)
        self.set_status(color_key, text)

    def set_status(self, color_key, text=None):
        """更新状态点与文字。

        每次界面心跳都会调用它，所以做了两件事：
        1. 颜色没变就不再 setStyleSheet —— 重复刷样式表会触发整棵控件树的样式重算，
           表现就是"界面一顿一顿的"；
        2. 去掉了 box-shadow —— Qt 样式表不支持这个属性，写了只会刷一堆
           "Unknown property box-shadow" 警告（一次跑测试刷过 4000 多条），没有任何视觉效果。
        """
        if text is not None and text != self.label.text():
            self.label.setText(text)
        color = status_color(color_key)
        if color == getattr(self, "_applied_color", None) and text is None:
            return
        self._applied_color = color
        self.dot.setStyleSheet(
            f"background: {color}; border-radius: 4px;"
            "min-width: 9px; max-width: 9px; min-height: 9px; max-height: 9px;"
        )
        self.label.setStyleSheet(f"color: {TEXT_2}; font-size: 13px; font-weight: 600;")


class TaskProgressBar(QProgressBar):
    """平滑过渡进度条：值变化自动动画，-1 显示忙碌不确定态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(8)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.valueChanged.connect(self._apply)

    def _apply(self, v):
        self.setValue(int(v))

    def set_smooth(self, value: int):
        value = max(-1, min(100, int(value)))
        self._anim.stop()
        if not motion.enabled():
            # 关掉动效：忙碌态给一个静止的三成高，其余直接落位
            self.setValue(30 if value == -1 else value)
            return
        if value == -1:
            self._anim.setStartValue(0)
            self._anim.setEndValue(28)
            self._anim.setLoopCount(-1)
            self._anim.start()
        else:
            self._anim.setLoopCount(1)
            self._anim.setStartValue(self.value())
            self._anim.setEndValue(value)
            self._anim.start()


class EmptyState(QWidget):
    """空状态：居中标题 + 提示，避免页面空白。"""

    def __init__(self, title="暂无内容", hint="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        # 坑：原来这里用 lay.setAlignment(Qt.AlignCenter)，子标签只按自身 sizeHint 取宽（很窄），
        # 自动换行的提示会被折成两三行，但最小高度仍按「一行」算。外层空间一紧，Qt 就砍行，
        # 表现是「文字只显示上半截」。改成上下弹簧做垂直居中、标签撑满宽度：
        # 一行放得下就一行，放不下也按真实行数占位。
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(
            f"color: {TEXT_2}; font-size: 15px; font-weight: 600; background: transparent;")
        self.hint = QLabel(hint)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {TEXT_3}; font-size: 12px; background: transparent;")
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addStretch(1)

    def set_content(self, title=None, hint=None):
        if title is not None:
            self.title.setText(title)
        if hint is not None:
            self.hint.setText(hint)
