"""GUI 自绘组件。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

# 与 CLI 一致的三档阈值（按剩余量）：绿 >30 / 黄 ≤30 / 红 ≤10
COLOR_OK = QColor("#3fb950")
COLOR_WARN = QColor("#d29922")
COLOR_CRIT = QColor("#f85149")
COLOR_UNKNOWN = QColor("#8b949e")
COLOR_TRACK = QColor("#30363d")


def threshold_color(remaining_percent: Optional[float]) -> QColor:
    if remaining_percent is None:
        return COLOR_UNKNOWN
    if remaining_percent <= 10:
        return COLOR_CRIT
    if remaining_percent <= 30:
        return COLOR_WARN
    return COLOR_OK


def abs_level_color(level: Optional[str]) -> QColor:
    """余额型告警等级着色（crit/warn/ok）。"""
    return {"crit": COLOR_CRIT, "warn": COLOR_WARN, "ok": COLOR_OK}.get(
        level or "", COLOR_UNKNOWN)


class QuotaBar(QWidget):
    """圆角进度条，填充部分表示剩余额度。remaining 为 None 时显示灰色空槽（未知）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._remaining: Optional[float] = None
        self.setFixedHeight(10)
        self.setMinimumWidth(120)

    def set_remaining(self, remaining_percent: Optional[float]) -> None:
        self._remaining = remaining_percent
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        radius = rect.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_TRACK)
        p.drawRoundedRect(rect, radius, radius)
        if self._remaining is not None and self._remaining > 0:
            ratio = min(max(self._remaining, 0), 100) / 100
            fill = rect.adjusted(0, 0, 0, 0)
            fill.setWidth(int(rect.width() * ratio))
            # 进度极小时保证圆角不超出填充宽度
            r = min(radius, fill.width() / 2)
            p.setBrush(threshold_color(self._remaining))
            p.drawRoundedRect(fill, r, r)
        p.end()
