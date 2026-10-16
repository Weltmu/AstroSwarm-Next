# -*- coding: utf-8 -*-
"""通用胶囊分段控件：胶囊底 + 圆形滑块跟着选中项滑动（两段互斥）。

来源：原 `brain_page.PersonaSegment`（2026-10-07 老板要的「胶囊按钮切换形态那种」）。
2026-10-11 提取成通用控件，好让 **QQ 通道（官方机器人 / 第三方 OneBot）** 也用同一套外观
——老板要求「下拉框做成胶囊按钮进行切换」。

用法：
    seg = SegmentedPill(("official", "onebot"), ("官方机器人", "第三方 OneBot"))
    seg.changed.connect(on_changed)      # 只在用户操作时发
    seg.set_current("onebot")            # 回填：只挪滑块，不发信号
    key = seg.current()

只用 Qt 自身能力实现（QPainter 画底 + 圆滑块，QPropertyAnimation 让滑块滑过去），不加依赖。
键盘可达：Tab 聚焦，左右方向键 / 回车 / 空格切换。
"""
from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRect, Qt, Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget


class SegmentedPill(QWidget):
    """两段胶囊切换：外观统一，键值由调用方给。"""

    changed = Signal(str)          # 只在**用户操作**时发；回填状态用 set_current，不发信号

    def __init__(self, keys=("a", "b"), labels=("A", "B"), parent=None,
                 accessible_name: str = "", tooltip: str = "", min_width: int = 228):
        super().__init__(parent)
        if len(keys) != 2 or len(labels) != 2:
            raise ValueError("SegmentedPill 目前只支持两段")
        self._KEYS = tuple(str(k) for k in keys)
        self._labels = tuple(str(x) for x in labels)
        self._index = 0
        self._offset = 0.0
        self._hover = -1
        self._syncing = False
        self.setFocusPolicy(Qt.StrongFocus)
        if accessible_name:
            self.setAccessibleName(accessible_name)
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        self.setMinimumWidth(min_width)
        self.setMouseTracking(True)
        self._slide = QPropertyAnimation(self, b"offset", self)
        self._slide.setDuration(160)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---- 圆滑块位置（0.0 = 第一段，1.0 = 第二段）——动画就改这个属性 ----
    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float):
        self._offset = max(0.0, min(1.0, float(value)))
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    # ---- 对外接口 ----
    def current(self) -> str:
        return self._KEYS[self._index]

    def set_current(self, key: str, animate: bool = True):
        """回填用：只挪滑块，不发信号（不会触发切换副作用）。"""
        if key not in self._KEYS:
            return
        index = self._KEYS.index(key)
        if index == self._index:
            self._slide.stop()
            self._set_offset(float(index))
            return
        self._index = index
        self._syncing = True
        try:
            if animate:
                self._slide.stop()
                self._slide.setStartValue(self._offset)
                self._slide.setEndValue(float(index))
                self._slide.start()
            else:
                self._slide.stop()
                self._set_offset(float(index))
        finally:
            self._syncing = False
        self.update()

    def _pick(self, index: int):
        """用户点了某一段 / 按了方向键：动滑块 + 发信号。"""
        if index == self._index:
            return
        key = self._KEYS[index]
        self.set_current(key)
        self.changed.emit(key)

    # ---- 几何 ----
    def _seg_rect(self, index: int) -> QRect:
        pad = 4
        w = (self.width() - pad * 2) / 2.0
        return QRect(int(pad + index * w), pad, int(w), self.height() - pad * 2)

    def _thumb_rect(self) -> QRect:
        pad = 4
        w = (self.width() - pad * 2) / 2.0
        left = pad + self._offset * w
        return QRect(int(left), pad, int(w), self.height() - pad * 2)

    # ---- 交互 ----
    def _index_at(self, x: int) -> int:
        return 0 if x < self.width() / 2 else 1

    def mousePressEvent(self, event):  # noqa: N802 —— Qt 命名
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self._pick(self._index_at(int(event.position().x())))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        hover = self._index_at(int(event.position().x()))
        if hover != self._hover:
            self._hover = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self._pick(0 if self._index == 1 else 1)
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._pick(0 if self._index == 1 else 1)
            event.accept()
            return
        if key == Qt.Key_Home:
            self._pick(0)
            event.accept()
            return
        if key == Qt.Key_End:
            self._pick(1)
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- 画 ----
    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        radius = self.height() / 2.0

        track = QPainterPath()
        track.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), radius, radius)
        painter.fillPath(track, QColor(127, 149, 190, 46))

        thumb = QPainterPath()
        rect = self._thumb_rect()
        thumb.addRoundedRect(float(rect.x()), float(rect.y()),
                             float(rect.width()), float(rect.height()),
                             rect.height() / 2.0, rect.height() / 2.0)
        painter.fillPath(thumb, QColor(88, 152, 255))

        if self.hasFocus():
            ring = QPainterPath()
            ring.addRoundedRect(1.0, 1.0, float(self.width() - 2), float(self.height() - 2),
                                radius, radius)
            pen = painter.pen()
            pen.setColor(QColor(138, 180, 255))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawPath(ring)

        for i, label in enumerate(self._labels):
            selected = i == self._index
            if selected:
                color = QColor(255, 255, 255)
            elif i == self._hover and self.underMouse():
                color = QColor(196, 210, 234)
            else:
                color = QColor(147, 161, 187)
            painter.setPen(color)
            font = painter.font()
            font.setPointSizeF(10.0)
            font.setBold(selected)
            painter.setFont(font)
            painter.drawText(self._seg_rect(i), Qt.AlignCenter, label)
        painter.end()
