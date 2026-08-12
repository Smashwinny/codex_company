"""FloatingHud：无边框、置顶、半透明、可拖动的悬浮窗。

布局（对应 DESIGN.md §5）：
    标题栏：⚡ Codex 额度 · 套餐      ⟳(刷新) ×(关闭)
    每个限流窗口一行：标签 + 进度条+百分比 + 重置倒计时
    附加限额桶（如 Spark）带分隔标题
    底部：数据新鲜度（更新于 x 前 / 陈旧标记 / 错误提示）

刷新：QTimer 每 60s 触发 QuotaFetcher（QThread）；底部倒计时每 30s 重排文本。
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Optional

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..app_server import QuotaSnapshot, QuotaWindow
from ..cli import error_hint
from ..fetcher import QuotaFetcher, RefreshScheduler
from ..state import StateStore, ViewState
from .widgets import QuotaBar, threshold_color

REFRESH_INTERVAL_MS = 60_000   # 活跃期自动刷新
TICK_INTERVAL_MS = 30_000      # 倒计时/新鲜度文本重排

BG = QColor(13, 17, 23, 230)   # 半透明深色底
FG = "#e6edf3"
FG_DIM = "#8b949e"


def _countdown_text(w: QuotaWindow, now_ts: float) -> str:
    secs = w.reset_in_seconds(now_ts)
    if secs is None:
        return "重置时间未知"
    if secs <= 0:
        return "即将重置"
    total = int(secs)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        text = f"{days} 天 {hours} 小时后重置"
    elif hours:
        text = f"{hours} 小时 {mins} 分后重置"
    else:
        text = f"{mins} 分后重置"
    if w.reset_at is not None:
        t = dt.datetime.fromtimestamp(w.reset_at).astimezone()
        text += f"（{t:%m-%d %H:%M}）"
    return text


class _WindowRow(QFrame):
    """一行限流窗口：标签 / 进度条+百分比 / 倒计时。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.label = QLabel()
        self.label.setStyleSheet(f"color: {FG}; font-weight: bold;")
        self.bar = QuotaBar()
        self.pct = QLabel()
        self.pct.setStyleSheet(f"color: {FG};")
        self.countdown = QLabel()
        self.countdown.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.label)
        top.addStretch(1)
        top.addWidget(self.bar, stretch=3)
        top.addWidget(self.pct)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)
        lay.addLayout(top)
        lay.addWidget(self.countdown)

    def bind(self, w: QuotaWindow, now_ts: float) -> None:
        rem = w.remaining_percent
        self.label.setText(w.label)
        self.bar.set_remaining(rem)
        if rem is None:
            self.pct.setText("?")
            self.pct.setStyleSheet(f"color: {FG_DIM};")
        else:
            self.pct.setText(f"剩 {rem:.0f}%")
            self.pct.setStyleSheet(f"color: {threshold_color(rem).name()};")
        self.countdown.setText(_countdown_text(w, now_ts))


class FloatingHud(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("codex-quota")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 不出现在任务栏
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(280)

        self._store = StateStore()
        self._scheduler = RefreshScheduler()
        self._fetcher: Optional[QuotaFetcher] = None
        self._drag_pos: Optional[QPoint] = None
        self._rows: list[tuple[QWidget, object]] = []  # (row_widget, QuotaWindow) 供 tick 重排

        self._build_ui()

        # 启动即展示 24h 内的缓存（标陈旧），不阻塞等待首次查询
        cached = self._store.load_cached()
        if cached.snapshot is not None:
            self._apply(cached)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._scheduler.next_interval_ms())
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self._retick)
        self._tick_timer.start()

        self.refresh()

    # ---------- UI 搭建 ----------

    def _build_ui(self) -> None:
        self._title = QLabel("⚡ Codex 额度")
        self._title.setStyleSheet(f"color: {FG}; font-weight: bold;")
        self._refresh_btn = QToolButton()
        self._refresh_btn.setText("⟳")
        self._refresh_btn.setToolTip("立即刷新")
        self._refresh_btn.setStyleSheet(f"color: {FG}; border: none; font-size: 14px;")
        self._refresh_btn.clicked.connect(self.refresh)
        self._close_btn = QToolButton()
        self._close_btn.setText("×")
        self._close_btn.setStyleSheet(f"color: {FG_DIM}; border: none; font-size: 14px;")
        self._close_btn.clicked.connect(self.close)

        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(0, 0, 0, 0)
        title_bar.addWidget(self._title)
        title_bar.addStretch(1)
        title_bar.addWidget(self._refresh_btn)
        title_bar.addWidget(self._close_btn)

        self._content = QVBoxLayout()
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(2)

        self._footer = QLabel("加载中…")
        self._footer.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(4)
        root.addLayout(title_bar)
        root.addLayout(self._content)
        root.addWidget(self._footer)

    def _clear_content(self) -> None:
        self._rows.clear()
        while self._content.count():
            item = self._content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---------- 数据流 ----------

    def refresh(self) -> None:
        if self._fetcher is not None and self._fetcher.isRunning():
            return  # 上一次查询还在进行，不叠加
        self._store.begin_refresh()
        self._refresh_btn.setEnabled(False)
        self._fetcher = QuotaFetcher(parent=self)
        self._fetcher.succeeded.connect(self._on_success)
        self._fetcher.failed.connect(self._on_error)
        self._fetcher.finished.connect(self._on_fetch_done)
        self._fetcher.start()

    def _on_fetch_done(self) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_timer.setInterval(self._scheduler.next_interval_ms())

    def _on_success(self, snap: QuotaSnapshot) -> None:
        self._scheduler.on_success()
        self._apply(self._store.on_success(snap))

    def _on_error(self, message: str) -> None:
        self._scheduler.on_failure()
        self._apply(self._store.on_error(message))

    def _apply(self, state: ViewState) -> None:
        self._clear_content()
        snap = state.snapshot
        now_ts = snap.fetched_at if snap else dt.datetime.now().timestamp()

        if snap is None:
            hint = error_hint(state.error or "")
            text = f"⚠ {state.error or '无数据'}"
            if hint:
                text += f"\n💡 {hint}"
            body = QLabel(text)
            body.setWordWrap(True)
            body.setStyleSheet(f"color: {FG_DIM};")
            self._content.addWidget(body)
        else:
            plan = f" · {snap.plan_type}" if snap.plan_type else ""
            self._title.setText(f"⚡ Codex 额度{plan}")
            main = snap.primary_limit
            if main is not None:
                self._add_window_row(main.primary, now_ts)
                if main.secondary is not None:
                    self._add_window_row(main.secondary, now_ts)
                if main.credits is not None and main.credits.has_credits:
                    c = main.credits
                    balance = "无限" if c.unlimited else (c.balance or "?")
                    lbl = QLabel(f"信用额度余额: {balance}")
                    lbl.setStyleSheet(f"color: {FG_DIM};")
                    self._content.addWidget(lbl)
            for extra in snap.limits[1:]:
                sep = QLabel(f"── {extra.limit_name or extra.limit_id} ──")
                sep.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
                self._content.addWidget(sep)
                self._add_window_row(extra.primary, now_ts)
                if extra.secondary is not None:
                    self._add_window_row(extra.secondary, now_ts)

        self._update_footer(state)
        self.adjustSize()

    def _add_window_row(self, w: QuotaWindow, now_ts: float) -> None:
        row = _WindowRow()
        row.bind(w, now_ts)
        self._rows.append((row, w))
        self._content.addWidget(row)

    def _update_footer(self, state: Optional[ViewState] = None) -> None:
        state = state or self._store.state
        fresh = StateStore.freshness_text(state.fetched_at)
        if state.stale:
            text = f"⚠ 数据陈旧（更新于 {fresh}）：{state.error}"
        elif state.error:
            text = f"⚠ {state.error}"
        else:
            text = f"更新于 {fresh}"
        self._footer.setText(text)

    def _retick(self) -> None:
        """30s tick：重排倒计时与新鲜度，不触发网络查询。"""
        now_ts = dt.datetime.now().timestamp()
        for row, w in self._rows:
            row.countdown.setText(_countdown_text(w, now_ts))
        self._update_footer()

    # ---------- 外观与交互 ----------

    def showEvent(self, event) -> None:  # noqa: N802
        self._scheduler.set_visible(True)
        self._refresh_timer.setInterval(self._scheduler.next_interval_ms())
        # 重新显示时若数据已过期，立即补一次刷新
        fetched = self._store.state.fetched_at
        if fetched is None or time.time() - fetched > REFRESH_INTERVAL_MS / 1000:
            self.refresh()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._scheduler.set_visible(False)
        self._refresh_timer.setInterval(self._scheduler.next_interval_ms())
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BG)
        p.drawRoundedRect(self.rect(), 10, 10)
        p.end()
        super().paintEvent(event)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
