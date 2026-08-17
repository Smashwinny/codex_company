"""FloatingHud：无边框、置顶、半透明、可拖动的悬浮窗。

布局（对应 DESIGN.md §5 + M6 多 provider）：
    标题栏：⚡ 额度监控  [模型徽章]      ⟳(刷新) ×(关闭)
    每个 provider 一个分区：● Codex · prolite / ● Kimi · k3
      分区内每个限流窗口一行：标签 + 进度条+百分比 + 重置倒计时
      该 provider 查询失败时分区内联错误（不影响其他分区）
    底部：整体新鲜度（最旧快照时间 / 陈旧标记）

交互：左键拖动；滚轮调透明度（0.3–1.0，持久化）；双击切换紧凑模式
（每 provider 只留主限额行）；位置记忆。刷新：QTimer 按 RefreshScheduler
调度；倒计时每 30s 重排文本。
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import time
from typing import Optional

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QWheelEvent
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
from ..i18n import tr
from ..model_info import ModelInfo, read_model_info
from ..notify import ResetWatcher, notify_resets
from ..settings import Settings
from ..state import ProviderView, StateStore, ViewState, default_cache_path
from .widgets import QuotaBar, threshold_color

REFRESH_INTERVAL_MS = 60_000   # 活跃期自动刷新
TICK_INTERVAL_MS = 30_000      # 倒计时/新鲜度文本重排
OPACITY_STEP = 0.05
OPACITY_MIN = 0.3

BG = QColor(13, 17, 23, 230)   # 半透明深色底
FG = "#e6edf3"
FG_DIM = "#8b949e"

MAX_WIDTH = 420        # 窗口最大宽度，防长错误文本把窗口撑宽
FOOTER_MAX_CHARS = 48  # 页脚单行最大字符数，超出截断
ERROR_MAX_CHARS = 120  # 分区内联错误最大字符数（完整内容放 tooltip）


def _short(text: object, limit: int) -> str:
    s = str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"

# provider 分区标识色
PROVIDER_COLORS = {"codex": "#3fb950", "kimi": "#a371f7"}

# 模型徽章：fast（Spark / fast tier）用实心橙 pill + ⚡；普通模型用灰描边 pill
BADGE_FAST_STYLE = (
    "background-color: #9e6a03; color: #ffe8b0; border: 1px solid #d29922;"
    "border-radius: 8px; padding: 0px 7px; font-weight: bold; font-size: 10px;"
)
BADGE_NORMAL_STYLE = (
    "color: #8b949e; border: 1px solid #30363d;"
    "border-radius: 8px; padding: 0px 7px; font-size: 10px;"
)
EFFORT_COLORS = {"low": "#3fb950", "medium": "#d29922", "high": "#f85149", "xhigh": "#f85149"}

logger = logging.getLogger("codex_quota.hud")


def _badge_html(info: ModelInfo) -> str:
    """徽章富文本：effort 按等级着色，fast 前缀 ⚡。"""
    parts = [html.escape(info.model)]
    if info.effort:
        color = EFFORT_COLORS.get(info.effort.lower(), FG_DIM)
        parts.append(f"<span style='color:{color}'>{html.escape(info.effort)}</span>")
    if info.service_tier and info.service_tier.lower() not in ("default", ""):
        parts.append(html.escape(info.service_tier))
    text = " · ".join(parts)
    return ("⚡ " + text) if info.is_fast else text


def _countdown_text(w: QuotaWindow, now_ts: float) -> str:
    secs = w.reset_in_seconds(now_ts)
    if secs is None:
        return tr("重置时间未知")
    if secs <= 0:
        return tr("即将重置")
    total = int(secs)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        text = tr("{d} 天 {h} 小时后重置").format(d=days, h=hours)
    elif hours:
        text = tr("{h} 小时 {m} 分后重置").format(h=hours, m=mins)
    else:
        text = tr("{m} 分后重置").format(m=mins)
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

    def bind(self, w: QuotaWindow, now_ts: float, show_countdown: bool = True) -> None:
        rem = w.remaining_percent
        self.label.setText(w.label)
        self.bar.set_remaining(rem)
        if rem is None:
            self.pct.setText("?")
            self.pct.setStyleSheet(f"color: {FG_DIM};")
        else:
            self.pct.setText(tr("剩 {p}%").format(p=f"{rem:.0f}"))
            self.pct.setStyleSheet(f"color: {threshold_color(rem).name()};")
        self.countdown.setText(_countdown_text(w, now_ts) if show_countdown else "")
        self.countdown.setVisible(show_countdown)


class FloatingHud(QWidget):
    state_changed = pyqtSignal(object)  # list[ProviderView]；托盘等跟随更新

    def __init__(self, providers=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("codex-quota")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # 不出现在任务栏
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(300)
        self.setMaximumWidth(MAX_WIDTH)

        if providers is None:
            from ..providers import default_providers

            providers = default_providers()
        self._providers = list(providers)
        self._stores = {
            p.name: StateStore(cache_path=default_cache_path(p.name))
            for p in self._providers
        }
        self._scheduler = RefreshScheduler()
        self._settings = Settings()
        self._watcher = ResetWatcher()
        self.notifier = None  # NtfyNotifier，由 __main__ 装配（None=不推送）
        self._fetcher: Optional[QuotaFetcher] = None
        self._any_success = False
        self._drag_pos: Optional[QPoint] = None
        self._rows: list[tuple[_WindowRow, QuotaWindow]] = []  # 供 tick 重排
        self._section_headers: list[QLabel] = []
        self._compact: bool = bool(self._settings.get("compact"))

        self.setWindowOpacity(float(self._settings.get("opacity")))

        self._build_ui()

        # 启动即展示各 provider 24h 内的缓存（标陈旧），不阻塞等待首次查询
        for store in self._stores.values():
            store.load_cached()
        self._apply()

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
        self._title = QLabel(tr("⚡ 额度监控"))
        self._title.setStyleSheet(f"color: {FG}; font-weight: bold;")
        self._model_badge = QLabel()
        self._model_badge.setTextFormat(Qt.TextFormat.RichText)
        self._model_badge.hide()  # 读到配置才显示
        self._refresh_btn = QToolButton()
        self._refresh_btn.setText("⟳")
        self._refresh_btn.setToolTip(tr("立即刷新"))
        self._refresh_btn.setStyleSheet(f"color: {FG}; border: none; font-size: 14px;")
        self._refresh_btn.clicked.connect(self.refresh)
        self._close_btn = QToolButton()
        self._close_btn.setText("×")
        self._close_btn.setStyleSheet(f"color: {FG_DIM}; border: none; font-size: 14px;")
        self._close_btn.clicked.connect(self.close)

        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(0, 0, 0, 0)
        title_bar.addWidget(self._title)
        title_bar.addWidget(self._model_badge)
        title_bar.addStretch(1)
        title_bar.addWidget(self._refresh_btn)
        title_bar.addWidget(self._close_btn)

        self._content = QVBoxLayout()
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(2)

        self._footer = QLabel(tr("加载中…"))
        self._footer.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(4)
        root.addLayout(title_bar)
        root.addLayout(self._content)
        root.addWidget(self._footer)

    def _clear_content(self) -> None:
        self._rows.clear()
        self._section_headers.clear()
        while self._content.count():
            item = self._content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---------- 数据流 ----------

    def refresh(self) -> None:
        if self._fetcher is not None:
            return  # 上一次查询还在进行，不叠加
        self._any_success = False
        self._refresh_btn.setEnabled(False)
        fetcher = QuotaFetcher(self._providers, parent=self)
        fetcher.succeeded.connect(self._on_success)
        fetcher.failed.connect(self._on_error)
        fetcher.finished.connect(self._on_fetch_done)
        fetcher.finished.connect(fetcher.deleteLater)  # 防线程对象累积
        self._fetcher = fetcher
        fetcher.start()

    def _on_fetch_done(self) -> None:
        self._refresh_btn.setEnabled(True)
        if self._any_success:
            self._scheduler.on_success()
        else:
            self._scheduler.on_failure()
        self._refresh_timer.setInterval(self._scheduler.next_interval_ms())
        # fetcher 已 deleteLater，立刻清空引用——
        # 否则下次 refresh 会访问已删除的 C++ 对象导致崩溃
        self._fetcher = None

    def _on_success(self, provider: str, snap: QuotaSnapshot) -> None:
        self._any_success = True
        had_error = self._stores[provider].state.error is not None
        self._stores[provider].on_success(snap)
        if had_error:
            logger.info("provider %s 已恢复", provider)
        display = next((p.display_name for p in self._providers if p.name == provider),
                       provider)
        notify_resets(self.notifier, self._watcher, provider, display, snap)
        self._apply()

    def _on_error(self, provider: str, message: str) -> None:
        self._stores[provider].on_error(message)
        self._apply()

    def _current_views(self) -> list[ProviderView]:
        return [
            ProviderView(name=p.name, display_name=p.display_name,
                         state=self._stores[p.name].state)
            for p in self._providers
        ]

    def _apply(self) -> None:
        self._clear_content()
        self._update_model_badge()  # 每次刷新重读 config.toml，改模型即时生效

        for p in self._providers:
            st = self._stores[p.name].state
            self._add_provider_header(p, st)
            snap = st.snapshot
            if snap is None:
                hint = error_hint(st.error or "")
                text = f"  ⚠ {_short(st.error or tr('无数据'), ERROR_MAX_CHARS)}"
                if hint:
                    text += f"\n  💡 {hint}"
                body = QLabel(text)
                body.setWordWrap(True)
                body.setToolTip(str(st.error or ""))  # 完整错误放 tooltip
                body.setStyleSheet(f"color: {FG_DIM};")
                self._content.addWidget(body)
                continue
            now_ts = snap.fetched_at
            main = snap.primary_limit
            if main is not None:
                self._add_window_row(main.primary, now_ts)
                if main.secondary is not None and not self._compact:
                    self._add_window_row(main.secondary, now_ts)
                if main.credits is not None and main.credits.has_credits and not self._compact:
                    c = main.credits
                    balance = tr("无限") if c.unlimited else (c.balance or "?")
                    lbl = QLabel("  " + tr("信用额度余额: {b}").format(b=balance))
                    lbl.setStyleSheet(f"color: {FG_DIM};")
                    self._content.addWidget(lbl)
            if not self._compact:
                for extra in snap.limits[1:]:
                    sep = QLabel(f"  ── {extra.limit_name or extra.limit_id} ──")
                    sep.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
                    self._content.addWidget(sep)
                    self._add_window_row(extra.primary, now_ts)
                    if extra.secondary is not None:
                        self._add_window_row(extra.secondary, now_ts)

        self._update_footer()
        self.adjustSize()
        self.state_changed.emit(self._current_views())

    def _add_provider_header(self, provider, st: ViewState) -> None:
        color = PROVIDER_COLORS.get(provider.name, FG_DIM)
        plan = ""
        if st.snapshot is not None and st.snapshot.plan_type:
            plan = f" · {st.snapshot.plan_type}"
        header = QLabel(f"<span style='color:{color}'>●</span> "
                        f"{html.escape(provider.display_name)}{html.escape(plan)}")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setStyleSheet(f"color: {FG}; font-weight: bold; font-size: 12px;")
        self._section_headers.append(header)
        self._content.addWidget(header)

    def _add_window_row(self, w: QuotaWindow, now_ts: float) -> None:
        row = _WindowRow()
        row.bind(w, now_ts, show_countdown=not self._compact)
        self._rows.append((row, w))
        self._content.addWidget(row)

    def _update_model_badge(self) -> None:
        info = read_model_info()
        if info is None:
            self._model_badge.hide()
            return
        self._model_badge.setText(_badge_html(info))
        self._model_badge.setStyleSheet(
            BADGE_FAST_STYLE if info.is_fast else BADGE_NORMAL_STYLE)
        self._model_badge.show()

    def _update_footer(self) -> None:
        """整体页脚：最旧快照的新鲜度；任一陈旧/全部失败时标注（超长截断防撑宽窗口）。"""
        views = self._current_views()
        snaps = [v.state for v in views if v.state.snapshot is not None]
        if not snaps:
            first_err = next((v.state.error for v in views if v.state.error), None)
            self._footer.setText(
                f"⚠ {_short(first_err, FOOTER_MAX_CHARS)}" if first_err else tr("无数据"))
            return
        oldest = min(s.fetched_at for s in snaps if s.fetched_at is not None)
        fresh = StateStore.freshness_text(oldest)
        if any(s.stale for s in snaps):
            err = next((s.error for s in snaps if s.error), "")
            self._footer.setText(tr("⚠ 数据陈旧（更新于 {f}）：{e}").format(
                f=fresh, e=_short(err, 20)))
        else:
            self._footer.setText(tr("更新于 {f}").format(f=fresh))

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
        fetched = [s.state.fetched_at for s in self._stores.values()]
        oldest = min((f for f in fetched if f is not None), default=None)
        if oldest is None or time.time() - oldest > REFRESH_INTERVAL_MS / 1000:
            self.refresh()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._scheduler.set_visible(False)
        self._refresh_timer.setInterval(self._scheduler.next_interval_ms())
        self._settings.set("pos", [self.x(), self.y()])
        super().hideEvent(event)

    def restore_position(self) -> None:
        """启动时恢复上次位置（show 之前调用）。"""
        pos = self._settings.get("pos")
        if (isinstance(pos, list) and len(pos) == 2
                and all(isinstance(v, int) for v in pos)):
            self.move(pos[0], pos[1])

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
        if self._drag_pos is not None:
            self._settings.set("pos", [self.x(), self.y()])  # 拖动结束即记忆位置
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self.set_compact(not self._compact)

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        steps = e.angleDelta().y() / 120
        self.set_opacity(self.windowOpacity() + steps * OPACITY_STEP)

    # ---------- 透明度 / 紧凑模式 ----------

    def set_opacity(self, value: float) -> None:
        value = max(OPACITY_MIN, min(1.0, value))
        self.setWindowOpacity(value)
        self._settings.set("opacity", value)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._settings.set("compact", compact)
        self._apply()  # 用当前状态重建内容区
