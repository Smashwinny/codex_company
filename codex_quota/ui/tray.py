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
from ..state import ProviderView, StateStore, key_excluded, toggle_window, window_keys
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


class ChecklistMenu:
    """持久勾选项子菜单：只增量同步，绝不 clear() 重建——aboutToShow 里
    clear() 重建会取消子菜单弹出（表现为"点不开"），菜单开着时重建又会
    把菜单收掉。新桶补建勾选项、消失的桶隐藏、勾选状态对齐配置。"""

    def __init__(self, title: str, hint_text: str, parent: QObject,
                 on_toggle) -> None:
        self.menu = QMenu(title)
        hint = QAction(hint_text, parent)
        hint.setEnabled(False)
        self.menu.addAction(hint)
        self._empty = QAction(tr("（暂无数据）"), parent)
        self._empty.setEnabled(False)
        self.menu.addAction(self._empty)
        self.menu.addSeparator()
        self._parent = parent
        self._on_toggle = on_toggle
        self._actions: dict[str, QAction] = {}  # key → 持久勾选项

    def sync(self, items: list[tuple[str, str]], excludes: set[str]) -> None:
        self._empty.setVisible(not items)
        seen = set()
        for key, label in items:
            seen.add(key)
            a = self._actions.get(key)
            if a is None:
                a = QAction(label, self._parent)
                a.setCheckable(True)
                a.setToolTip(key)
                a.triggered.connect(
                    lambda _=False, k=key: self._on_toggle(k))
                self.menu.addAction(a)
                self._actions[key] = a
            a.setVisible(True)
            a.setChecked(not key_excluded(key, excludes))
        for key, a in self._actions.items():
            if key not in seen:
                a.setVisible(False)


def worst_remaining(views: list[ProviderView],
                    excludes: frozenset[str] = frozenset()) -> Optional[float]:
    """参与取色窗口的最低剩余量；余额 crit/warn 折算为 5/20 参与取色。
    excludes 命中的 "provider:桶:窗口"（或桶级前缀）跳过——
    比如不关心的 Spark 桶耗尽不应拖红图标。"""
    values = []
    for v in views:
        snap = v.state.snapshot
        if snap is None:
            continue
        for limit in snap.limits:
            bucket = limit.limit_name or limit.limit_id
            for w in (limit.primary, limit.secondary):
                if w is None:
                    continue
                if key_excluded(f"{v.name}:{bucket}:{w.label}", excludes):
                    continue
                if w.remaining_percent is not None:
                    values.append(w.remaining_percent)
                elif w.abs_level == "crit":
                    values.append(5.0)
                elif w.abs_level == "warn":
                    values.append(20.0)
    return min(values) if values else None


def summary_lines(views: list[ProviderView]) -> list[str]:
    """菜单/提示中的额度摘要行（按 provider 分组带前缀）。"""
    lines: list[str] = []
    for v in views:
        snap = v.state.snapshot
        if snap is None:
            lines.append(f"{v.display_name} · " + tr("无数据"))
            continue
        for limit in snap.limits:
            prefix = f"{v.display_name}"
            if limit is not snap.primary_limit:
                prefix += f" {limit.limit_name or limit.limit_id}"
            for w in (limit.primary, limit.secondary):
                if w is None:
                    continue
                if w.is_balance:
                    lines.append(f"{prefix} · {w.label} {w.abs_text or '?'}")
                elif w.remaining_percent is not None:
                    lines.append(f"{prefix} · {w.label} " + tr("剩 {p}%").format(p=f"{w.remaining_percent:.0f}"))
                else:
                    lines.append(f"{prefix} · {w.label} " + tr("未知"))
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
        self.action_phone = QAction(tr("复制手机访问地址"), self)
        self.action_phone.setToolTip(tr("手机与电脑同一局域网，浏览器打开即看"))
        self.action_phone.triggered.connect(self._copy_phone_url)
        self.action_push_url = QAction(tr("推送访问地址到手机"), self)
        self.action_push_url.setToolTip(tr("通过 ntfy 推送网页地址，手机点通知直接打开"))
        self.action_push_url.triggered.connect(self._push_phone_url)
        self.action_notify = QAction(tr("手机通知（ntfy）订阅指引"), self)
        self.action_notify.setToolTip(tr("查看/复制订阅主题、命令主题，发送测试推送"))
        self.action_notify.triggered.connect(self._open_notify_guide)
        self.action_wizard = QAction(tr("初始设置 / 环境自检"), self)
        self.action_wizard.triggered.connect(self._open_wizard)
        self.action_providers = QAction(tr("管理额度来源"), self)
        self.action_providers.triggered.connect(self._open_providers)

        # 告警阈值预设子菜单（黄线/红线，选择即生效并持久化；任意值可手写 settings.json）
        # 注意：QMenu(title, parent) 的 parent 必须是 QWidget，QuotaTray 是 QObject，
        # 传 self 会 TypeError——标题单参数构造，生命周期与托盘一致（应用级常驻）
        self._thresh_menu = QMenu(tr("告警阈值"))
        self._thresh_menu.setToolTipsVisible(True)
        self._thresh_actions: list[tuple[QAction, int, int]] = []
        for warn, crit, tag in ((50, 20, tr("敏感")), (30, 10, tr("默认")),
                                (20, 5, tr("宽松"))):
            a = QAction(tr("{w} / {c}（{tag}）").format(w=warn, c=crit, tag=tag), self)
            a.setCheckable(True)
            a.setToolTip(tr("剩余量 ≤ {w}% 显示黄色，≤ {c}% 显示红色").format(w=warn, c=crit))
            a.triggered.connect(lambda _=False, w=warn, c=crit: self._set_thresholds(w, c))
            self._thresh_menu.addAction(a)
            self._thresh_actions.append((a, warn, crit))
        self._thresh_menu.aboutToShow.connect(self._sync_threshold_checks)

        # 两个勾选式子菜单（持久项，见 ChecklistMenu 注释）：
        # 主模型显示=哪些桶参与托盘图标取色；重置提醒=哪些桶回满时推送到手机
        self._color_checks = ChecklistMenu(
            tr("主模型显示"), tr("勾选参与取色，图标按勾选项的最低剩余量变色"),
            self, self._toggle_color_source)
        self._notify_checks = ChecklistMenu(
            tr("重置提醒"), tr("勾选的额度桶回满 100% 时推送手机通知"),
            self, self._toggle_notify_bucket)
        self.action_quit = QAction(tr("退出"), self)
        self.action_quit.triggered.connect(self._app.quit)

        self._menu = QMenu()
        self._menu.addAction(self.action_toggle)
        self._menu.addAction(self.action_refresh)
        self._menu.addAction(self.action_autostart)
        self._menu.addAction(self.action_phone)
        self._menu.addAction(self.action_push_url)
        self._menu.addAction(self.action_notify)
        self._menu.addAction(self.action_wizard)
        self._menu.addAction(self.action_providers)
        self._menu.addMenu(self._thresh_menu)
        self._menu.addMenu(self._color_checks.menu)
        self._menu.addMenu(self._notify_checks.menu)
        self._menu.addSeparator()
        self._summary_anchor = self._menu.addSeparator()  # 摘要行插入到此锚点之前
        self._summary_actions: list[QAction] = []
        self._menu.addAction(self.action_quit)
        self.tray.setContextMenu(self._menu)
        # 菜单弹出前同步显隐文案（用户也可能直接点悬浮窗的 × 隐藏）
        self._menu.aboutToShow.connect(self._sync_toggle_text)

        self.tray.activated.connect(self._on_activated)
        hud.state_changed.connect(self.update_state)
        self.update_state(hud._current_views())
        self._sync_toggle_text()

    def show(self) -> None:
        self.tray.show()

    # ---------- 状态更新 ----------

    def update_state(self, views: list[ProviderView]) -> None:
        excludes = frozenset(
            self._hud._settings.get("tray_color_excludes") or [])
        rem = worst_remaining(views, excludes)
        self.tray.setIcon(make_dot_icon(threshold_color(rem)))
        # 增量同步两个勾选子菜单（不重建，见 ChecklistMenu 注释）
        items = window_keys(views)
        self._color_checks.sync(items, set(excludes))
        self._notify_checks.sync(
            items, set(self._hud._settings.get("notify_excludes") or []))

        lines = summary_lines(views)
        tip = tr("⚡ 额度监控") + "\n" + "\n".join(lines)
        stale = next((v for v in views if v.state.stale), None)
        if stale is not None:
            fresh = StateStore.freshness_text(stale.state.fetched_at)
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

    def _sync_threshold_checks(self) -> None:
        warn = float(self._hud._settings.get("color_warn_threshold"))
        crit = float(self._hud._settings.get("color_crit_threshold"))
        for a, w, c in self._thresh_actions:
            a.setChecked(w == warn and c == crit)

    def _set_thresholds(self, warn: int, crit: int) -> None:
        from .widgets import set_thresholds

        set_thresholds(warn, crit)
        self._hud._settings.set("color_warn_threshold", warn)
        self._hud._settings.set("color_crit_threshold", crit)
        self._hud._apply()  # 重渲染 HUD 并 emit state_changed → 托盘图标同步变色

    # ---------- 勾选子菜单 ----------

    def _toggle_color_source(self, key: str) -> None:
        s = self._hud._settings
        all_keys = [k for k, _ in window_keys(self._hud._current_views())]
        s.set("tray_color_excludes",
              sorted(toggle_window(s.get("tray_color_excludes") or [], key, all_keys)))
        self.update_state(self._hud._current_views())  # 图标立即按新取色集合刷新

    def _toggle_notify_bucket(self, key: str) -> None:
        s = self._hud._settings
        all_keys = [k for k, _ in window_keys(self._hud._current_views())]
        s.set("notify_excludes",
              sorted(toggle_window(s.get("notify_excludes") or [], key, all_keys)))
        # 无视觉变化，不用刷图标；下次检测回满时即按新集合生效

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

    def _copy_phone_url(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        # 有公网隧道优先复制公网地址（任意网络可达），否则局域网地址
        url = (getattr(self._hud, "public_url", None)
               or getattr(self._hud, "web_url", None))
        if url:
            QGuiApplication.clipboard().setText(url)
            tips = [url]
            lan = getattr(self._hud, "web_url", None)
            if lan and lan != url:
                tips.append(lan)
            self.action_phone.setToolTip("\n".join(tips))  # 悬停可见全部地址

    def _push_phone_url(self) -> None:
        """按需把当前访问地址推到 ntfy：想用手机看时点一下，手机点通知直接打开。"""
        url = self._hud.public_url or self._hud.web_url
        notifier = self._hud.notifier
        if url and notifier is not None:
            ok = notifier.publish(
                "codex-quota",
                f"📱 手机访问地址（点通知直接打开）：\n{url}",
                tags="link", click=url)
            tip = url if ok else tr("推送失败（网络或 ntfy 服务不可达）")
        elif notifier is None:
            tip = tr("未开启 ntfy 通知，无法推送")
        else:
            tip = tr("未开启手机访问，没有可推送的地址")
        self.action_push_url.setToolTip(tip)
        self.tray.showMessage(tr("Codex 额度"), tip, QSystemTrayIcon.MessageIcon.Information, 3000)

    def _open_notify_guide(self) -> None:
        notifier = self._hud.notifier
        if notifier is None or not notifier.topic:
            self.action_notify.setToolTip(tr("未开启 ntfy 通知"))
            return
        from .notify_dialog import NotifyGuideDialog

        NotifyGuideDialog(notifier, parent=self._hud).exec()

    def _open_wizard(self) -> None:
        from .wizard import SetupWizardDialog

        SetupWizardDialog(self._hud._settings, parent=self._hud).exec()

    def _open_providers(self) -> None:
        from .providers_dialog import ProvidersDialog

        ProvidersDialog(self._hud, parent=self._hud).exec()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # 左键单击
            self._toggle_hud()
