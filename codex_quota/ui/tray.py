"""系统托盘：彩色圆点图标 + 右键菜单（辅助形态，主形态是悬浮窗）。

图标圆点颜色取所有限流窗口中的最低剩余量（绿 >30 / 黄 ≤30 / 红 ≤10，无数据灰），
一眼反映最坏状态。GNOME 默认不显示托盘（需 AppIndicator 扩展），
不可用时主程序会回退为"关窗即退出"。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from .. import autostart
from ..i18n import tr
from ..state import StateStore, ViewState
from .widgets import COLOR_UNKNOWN, threshold_color

ICON_SIZE = 64  # 绘制大尺寸让系统自行缩放，保证 HiDPI 清晰


def make_dot_icon(color: QColor, size: int = ICON_SIZE) -> QIcon:
    """透明底 + 彩色实心圆（带深色描边，浅色托盘背景上也可辨）。"""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = size // 10
    p.setPen(QColor(13, 17, 23))
    p.setBrush(color)
    p.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
    p.end()
    return QIcon(pm)


def worst_remaining(state: ViewState) -> Optional[float]:
    """所有窗口中的最低剩余量；无数据返回 None。"""
    snap = state.snapshot
    if snap is None:
        return None
    values = []
    for limit in snap.limits:
        for w in (limit.primary, limit.secondary):
            if w is not None and w.remaining_percent is not None:
                values.append(w.remaining_percent)
    return min(values) if values else None


def summary_lines(state: ViewState) -> list[str]:
    """菜单/提示中的额度摘要行。"""
    snap = state.snapshot
    if snap is None:
        return [tr("无数据")]
    lines: list[str] = []
    for limit in snap.limits:
        prefix = "" if limit is snap.primary_limit else f"{limit.limit_name or limit.limit_id} · "
        for w in (limit.primary, limit.secondary):
            if w is None:
                continue
            rem = w.remaining_percent
            if rem is not None:
                lines.append(f"{prefix}{w.label} " + tr("剩 {p}%").format(p=f"{rem:.0f}"))
            else:
                lines.append(f"{prefix}{w.label} " + tr("未知"))
    return lines or [tr("无数据")]


class QuotaTray(QObject):
    """封装 QSystemTrayIcon，跟随 HUD 的 state_changed 信号更新。"""

    def __init__(self, hud, app, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._hud = hud
        self._app = app

        self.tray = QSystemTrayIcon(make_dot_icon(COLOR_UNKNOWN), parent=self)
        self.tray.setToolTip(tr("Codex 额度"))

        self.action_toggle = QAction(tr("显示悬浮窗"), self)
        self.action_toggle.triggered.connect(self._toggle_hud)
        self.action_refresh = QAction(tr("立即刷新"), self)
        self.action_refresh.triggered.connect(self._hud.refresh)
        self.action_autostart = QAction(tr("开机自启"), self)
        self.action_autostart.setCheckable(True)
        self.action_autostart.setChecked(autostart.is_enabled())
        self.action_autostart.toggled.connect(self._toggle_autostart)
        self.action_quit = QAction(tr("退出"), self)
        self.action_quit.triggered.connect(self._app.quit)

        self._menu = QMenu()
        self._menu.addAction(self.action_toggle)
        self._menu.addAction(self.action_refresh)
        self._menu.addAction(self.action_autostart)
        self._menu.addSeparator()
        self._summary_anchor = self._menu.addSeparator()  # 摘要行插入到此锚点之前
        self._summary_actions: list[QAction] = []
        self._menu.addAction(self.action_quit)
        self.tray.setContextMenu(self._menu)
        # 菜单弹出前同步显隐文案（用户也可能直接点悬浮窗的 × 隐藏）
        self._menu.aboutToShow.connect(self._sync_toggle_text)

        self.tray.activated.connect(self._on_activated)
        hud.state_changed.connect(self.update_state)
        self.update_state(hud._store.state)
        self._sync_toggle_text()

    def show(self) -> None:
        self.tray.show()

    # ---------- 状态更新 ----------

    def update_state(self, state: ViewState) -> None:
        rem = worst_remaining(state)
        self.tray.setIcon(make_dot_icon(threshold_color(rem)))

        lines = summary_lines(state)
        tip = tr("Codex 额度") + " · " + " · ".join(lines)
        if state.stale:
            fresh = StateStore.freshness_text(state.fetched_at)
            tip += tr("（数据陈旧，更新于 {f}）").format(f=fresh)
        self.tray.setToolTip(tip)
        self._rebuild_summary(lines)

    def _rebuild_summary(self, lines: list[str]) -> None:
        for a in self._summary_actions:
            self._menu.removeAction(a)
            a.deleteLater()  # removeAction 不释放对象，防累积
        self._summary_actions = []
        for line in lines:  # 依次插到锚点前，保持传入顺序
            item = QAction(line, self)
            item.setEnabled(False)
            self._menu.insertAction(self._summary_anchor, item)
            self._summary_actions.append(item)

    # ---------- 交互 ----------

    def _sync_toggle_text(self) -> None:
        self.action_toggle.setText(tr("隐藏悬浮窗") if self._hud.isVisible()
                                   else tr("显示悬浮窗"))

    def _toggle_hud(self) -> None:
        if self._hud.isVisible():
            self._hud.hide()
        else:
            self._hud.show()
            self._hud.raise_()
        self._sync_toggle_text()

    def _toggle_autostart(self, checked: bool) -> None:
        if checked:
            autostart.enable()
        else:
            autostart.disable()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # 左键单击
            self._toggle_hud()
