# -*- coding: utf-8 -*-
"""iOS 式开关行 + 折叠分组（2026-10-11 老板定：开关用 iOS 样式；数值项折叠起来）。

设计要点（对齐 Material 3 / iOS HIG 的通用做法，见 deliverables/UI-优化计划-20261011.md）：
  · 轨道 40×22、滑块 18，四角全圆；关=灰、开=品牌蓝；滑块 140ms 缓动；
  · **整行可点**（不用精准戳小方块），悬停整行有底色，键盘 Space/回车可切，聚焦有描边；
  · 七态：关 / 开 / 悬停 / 按下 / 聚焦 / 禁用 / **保存中**（保存中画成半透明，失败再回弹）；
  · 危险开关（会影响群里所有人）走**二次确认弹窗**（老板 2026-10-11 拍板）。

实现策略：**不重写页面的业务逻辑**，而是把已有的 `QCheckBox` **包一层**——
`iOSSwitchRow` 内部持有原复选框（隐藏掉、保留它的信号与状态），只负责画与转发点击。
这样页面里所有 `chk_xxx.toggled.connect(...)` 一律不用改，风险最低。
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QSizePolicy, QVBoxLayout, QWidget,
)

ACCENT = QColor(88, 152, 255)          # 与胶囊控件同一个品牌蓝
TRACK_OFF = QColor(120, 136, 168, 110)
TEXT_MAIN = QColor(226, 232, 242)
TEXT_DIM = QColor(150, 163, 186)
TEXT_WARN = QColor(240, 196, 96)
ROW_HOVER = QColor(255, 255, 255, 14)

THUMB_W, THUMB_H = 40.0, 22.0
KNOB = 18.0


class iOSSwitchRow(QWidget):
    """一行 = 标题（+说明小字）+ iOS 式开关；整行可点；内部持有原 QCheckBox。"""

    changed = Signal(bool)                  # 转发原复选框的 toggled（方便外部直接用）

    def __init__(self, checkbox: QCheckBox, description: str = "", parent=None,
                 danger_confirm: str = "", compact: bool = False):
        super().__init__(parent or checkbox.parentWidget())
        self.chk = checkbox
        self._desc = description or (checkbox.toolTip() or "")
        self._danger = danger_confirm
        self._compact = compact
        self._offset = 1.0 if checkbox.isChecked() else 0.0
        self._press = False
        self._busy = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(checkbox.text())
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumHeight(34 if compact else (48 if self._desc else 38))
        self.setToolTip(checkbox.toolTip() or self._desc)

        checkbox.hide()                     # 原控件只留状态/信号，不再自己显示
        checkbox.toggled.connect(self._on_toggled)

        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---- 滑块位置（0=关，1=开），动画改这个 ----
    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, v: float):
        self._offset = max(0.0, min(1.0, float(v)))
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _on_toggled(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        self.changed.emit(on)

    # ---- 对外：保存中/保存失败回弹 ----
    def set_busy(self, busy: bool):
        self._busy = bool(busy)
        self.update()

    def revert(self, on: bool):
        """保存失败时回到真实状态（不发业务信号，避免死循环）。"""
        blocked = self.chk.blockSignals(True)
        self.chk.setChecked(bool(on))
        self.chk.blockSignals(blocked)
        self._anim.stop()
        self._offset = 1.0 if on else 0.0
        self.update()

    # ---- 交互：整行可点 ----
    def _toggle(self):
        if self.chk.isEnabled() is False:
            return
        want = not self.chk.isChecked()
        if self._danger and want:
            box = QMessageBox(self)
            box.setWindowTitle("确认开启")
            box.setIcon(QMessageBox.Warning)
            box.setText(self.chk.text())
            box.setInformativeText(self._danger)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.button(QMessageBox.Yes).setText("确定开启")
            box.button(QMessageBox.No).setText("取消")
            if box.exec() != QMessageBox.Yes:
                return
        self.chk.toggle()               # 真正的状态变化走原复选框 → 原有逻辑照旧触发

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press = True
            self.setFocus(Qt.MouseFocusReason)
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self._press:
            self._press = False
            self._toggle()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)

    # ---- 画 ----
    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())

        hovered = self.underMouse() and self.chk.isEnabled()
        if hovered or self._press:
            path = QPainterPath()
            path.addRoundedRect(QRectF(control_x_start(), 2, control_w(w), h - 4), 9, 9)
            p.fillPath(path, ROW_HOVER)

        on = self.chk.isChecked()
        # 文本
        left = 12.0 if not self._compact else 2.0
        right_reserve = THUMB_W + 22
        p.setPen(TEXT_MAIN if self.chk.isEnabled() else TEXT_DIM)
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        p.setFont(f)
        title_h = 20.0 if self._desc and not self._compact else h
        p.drawText(QRectF(left, 0, max(10.0, w - left - right_reserve), title_h),
                   Qt.AlignVCenter | Qt.AlignLeft, self.chk.text())
        if self._desc and not self._compact:
            p.setPen(TEXT_DIM)
            f2 = QFont(self.font())
            f2.setPointSizeF(8.6)
            p.setFont(f2)
            p.drawText(QRectF(left, title_h - 3, max(10.0, w - left - right_reserve), h - title_h),
                       Qt.AlignTop | Qt.AlignLeft, self._desc)

        # 开关本体
        tx = w - THUMB_W - 12.0
        ty = (h - THUMB_H) / 2.0
        self._paint_switch(p, tx, ty, on, enabled=self.chk.isEnabled())
        p.end()

    def _paint_switch(self, p: QPainter, x: float, y: float, on: bool, enabled: bool):
        track = QPainterPath()
        track.addRoundedRect(QRectF(x, y, THUMB_W, THUMB_H), THUMB_H / 2, THUMB_H / 2)
        color = ACCENT if on else TRACK_OFF
        if not enabled:
            color = QColor(color.red(), color.green(), color.blue(), 70)
        elif self._busy:
            color = QColor(color.red(), color.green(), color.blue(), 150)
        p.fillPath(track, color)

        kx = x + (THUMB_W - KNOB - 2) * self._offset + 2 * (1 - self._offset) if False else \
            x + 2 + (THUMB_W - KNOB - 4) * self._offset
        ky = y + (THUMB_H - KNOB) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QRectF(kx, ky + 0.8, KNOB, KNOB))
        p.setBrush(QColor(255, 255, 255) if enabled else QColor(230, 230, 230, 180))
        p.drawEllipse(QRectF(kx, ky, KNOB, KNOB))

        if self.hasFocus():
            pen = QPen(QColor(138, 180, 255), 2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(x - 2, y - 2, THUMB_W + 4, THUMB_H + 4),
                              (THUMB_H + 4) / 2, (THUMB_H + 4) / 2)


def control_x_start() -> float:
    return 2.0


def control_w(w: float) -> float:
    return max(10.0, w - 4)


# ------------------------------------------------------------------ 折叠分组
class CollapseGroup(QWidget):
    """可折叠分组：标题行（带当前值摘要）+ 内容区；默认收起，点标题行展开。"""

    def __init__(self, title: str, body: QWidget, summary=None, parent=None,
                 collapsed: bool = True):
        super().__init__(parent)
        self._title = title
        self._summary = summary             # callable -> str，或 None
        self._collapsed = bool(collapsed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.head = _CollapseHeader(title, self)
        lay.addWidget(self.head)
        self.body = body
        lay.addWidget(self.body)
        self.body.setVisible(not self._collapsed)
        self.head.clicked.connect(self.toggle)
        self.refresh_summary()

    def toggle(self):
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        self.head.set_collapsed(self._collapsed)
        self.refresh_summary()

    def refresh_summary(self):
        text = ""
        if callable(self._summary):
            try:
                text = str(self._summary() or "")
            except Exception:  # noqa: BLE001
                text = ""
        elif self._summary:
            text = str(self._summary)
        self.head.set_summary(text)


class _CollapseHeader(QWidget):
    clicked = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._summary = ""
        self._collapsed = True
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(30)
        self.setMouseTracking(True)

    def set_summary(self, text: str):
        self._summary = text or ""
        self.update()

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.underMouse():
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 1, float(self.width()), float(self.height() - 2)), 8, 8)
            p.fillPath(path, ROW_HOVER)
        p.setPen(TEXT_MAIN)
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(10, 0, 180, float(self.height())), Qt.AlignVCenter | Qt.AlignLeft,
                   self._title)
        if self._summary:
            p.setPen(TEXT_DIM)
            f2 = QFont(self.font())
            f2.setPointSizeF(9.0)
            f2.setBold(False)
            p.setFont(f2)
            p.drawText(QRectF(150, 0, float(self.width()) - 180, float(self.height())),
                       Qt.AlignVCenter | Qt.AlignLeft, self._summary)
        # 右侧箭头
        p.setPen(QPen(TEXT_DIM, 1.6))
        cx, cy = float(self.width()) - 14, float(self.height()) / 2
        if self._collapsed:
            p.drawLine(int(cx - 4), int(cy - 5), int(cx + 1), int(cy))
            p.drawLine(int(cx + 1), int(cy), int(cx - 4), int(cy + 5))
        else:
            p.drawLine(int(cx - 5), int(cy - 2), int(cx), int(cy + 3))
            p.drawLine(int(cx), int(cy + 3), int(cx + 5), int(cy - 2))
        p.end()


# ------------------------------------------------------------------ 批量升级
def upgrade_checkboxes(root: QWidget, minimum_row_span: bool = True) -> int:
    """把页面里"独占一行"的 QCheckBox 换成 iOS 开关行（其余保持原样）。

    只升级**自己单独占一行**的复选框（`QVBoxLayout` 里一行一个）；
    像「QQ / 微信 / 飞书 / 纸飞机」那种横排多个的，保持原样不动——
    否则一排放四个整行开关会很怪（那类以后再单独做紧凑样式）。

    2026-10-11 踩坑：一开始用 `layout.replaceWidget(old, new)`，对"不是布局直接项"的控件
    会静默失效 → 新开关行没进布局、浮在 (0,0) 压在标题上。改成显式
    `indexOf → removeWidget → insertWidget`，可靠得多。
    """
    from PySide6.QtWidgets import QVBoxLayout

    count = 0
    for chk in root.findChildren(QCheckBox):
        parent = chk.parentWidget()
        lay = parent.layout() if parent is not None else None
        if not isinstance(lay, QVBoxLayout):
            continue
        idx = lay.indexOf(chk)             # 只处理布局的**直接项**
        if idx < 0:
            continue
        row = iOSSwitchRow(chk, parent=parent)
        lay.removeWidget(chk)              # 取出（控件本身保留，只是不再占位）
        lay.insertWidget(idx, row)         # 原位置换成整行开关
        count += 1
    return count
