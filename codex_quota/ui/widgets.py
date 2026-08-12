"""GUI 自绘组件。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

# 与 CLI 一致的三档阈值：绿 <70 / 黄 <90 / 红 ≥90
COLOR_OK = QColor("#3fb950")
COLOR_WARN = QColor("#d29922")
COLOR_CRIT = QColor("#f85149")
COLOR_UNKNOWN = QColor("#8b949e")
COLOR_TRACK = QColor("#30363d")


def threshold_color(used_percent: Optional[float]) -> QColor:
    if used_percent is None:
        return COLOR_UNKNOWN
    if used_percent >= 90:
        return COLOR_CRIT
    if used_percent >= 70:
        return COLOR_WARN
    return COLOR_OK


class QuotaBar(QWidget):
    """圆角进度条。used_percent 为 None 时显示灰色空槽（未知）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._used: Optional[float] = None
        self.setFixedHeight(10)
        self.setMinimumWidth(120)

    def set_used(self, used_percent: Optional[float]) -> None:
        self._used = used_percent
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        radius = rect.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_TRACK)
        p.drawRoundedRect(rect, radius, radius)
        if self._used is not None and self._used > 0:
            ratio = min(max(self._used, 0), 100) / 100
            fill = rect.adjusted(0, 0, 0, 0)
            fill.setWidth(int(rect.width() * ratio))
            # 进度极小时保证圆角不超出填充宽度
            r = min(radius, fill.width() / 2)
            p.setBrush(threshold_color(self._used))
            p.drawRoundedRect(fill, r, r)
        p.end()
