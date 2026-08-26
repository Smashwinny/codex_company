"""首启向导：环境检测 + 修复引导 + 可选功能开关。

首次启动（settings.wizard_done 为 False）自动弹出；之后可从托盘菜单
"初始设置 / 环境自检"再次打开。每个缺失项旁边就是"复制命令"按钮，
用户不需要开终端查文档。样式统一走 ui/theme.py 深色主题。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..doctor import FAIL, OK, WARN, run_checks
from ..i18n import tr
from ..settings import Settings
from .theme import DIALOG_STYLE, FG, FG_DIM, style_section, style_subtitle, style_title

STATUS_ICON = {OK: "✅", WARN: "➖", FAIL: "❌"}


def should_show_wizard(settings: Settings) -> bool:
    return not bool(settings.get("wizard_done"))


def _card() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setProperty("card", True)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(8)
    return card, lay


class SetupWizardDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("codex-quota 初始设置"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(DIALOG_STYLE)

        self._settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        # 标题区
        title = QLabel(style_title("⚡ " + tr("codex-quota 初始设置")))
        title.setTextFormat(Qt.TextFormat.RichText)
        subtitle = QLabel(style_subtitle(tr("检测运行环境，缺失项一键复制修复命令")))
        subtitle.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(title)
        lay.addWidget(subtitle)

        # 环境检测卡片
        env_card, env_lay = _card()
        env_lay.addWidget(self._section_label(tr("环境检测")))
        self._checks_area = QVBoxLayout()
        self._checks_area.setSpacing(6)
        env_lay.addLayout(self._checks_area)
        recheck = QPushButton(tr("全部重新检测"))
        recheck.clicked.connect(self._reload_checks)
        env_lay.addWidget(recheck)
        lay.addWidget(env_card)
        self._reload_checks()

        # 可选功能卡片
        feat_card, feat_lay = _card()
        feat_lay.addWidget(self._section_label(tr("可选功能")))
        self._web_cb = QCheckBox(tr("手机访问（局域网 + 公网隧道）"))
        self._web_cb.setChecked(bool(settings.get("web_enabled")))
        self._notify_cb = QCheckBox(tr("额度重置推送（ntfy）"))
        self._notify_cb.setChecked(bool(settings.get("notify_enabled")))
        feat_lay.addWidget(self._web_cb)
        feat_lay.addWidget(self._notify_cb)
        lay.addWidget(feat_card)

        # 按钮区
        btns = QHBoxLayout()
        btns.addStretch(1)
        self._skip_btn = QPushButton(tr("跳过"))
        self._skip_btn.clicked.connect(self._on_skip)
        self._done_btn = QPushButton(tr("完成并启动"))
        self._done_btn.setProperty("primary", True)
        self._done_btn.setDefault(True)
        self._done_btn.clicked.connect(self._on_finish)
        btns.addWidget(self._skip_btn)
        btns.addWidget(self._done_btn)
        lay.addLayout(btns)
        self.adjustSize()

    # ---------- 构建 ----------

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(style_section(text))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        return lbl

    def _reload_checks(self) -> None:
        while self._checks_area.count():
            item = self._checks_area.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for check in run_checks():
            self._checks_area.addWidget(self._make_row(check))
        self.adjustSize()

    def _make_row(self, check) -> QFrame:
        row = QFrame()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        color = FG if check.status != WARN else FG_DIM
        text = QLabel(
            f"{STATUS_ICON[check.status]} <b style='color:{color}'>{check.name}</b>"
            f" <span style='color:{FG_DIM}'>· {check.detail}</span>")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        lay.addWidget(text, stretch=1)
        # codex CLI 缺失：给"自动安装"（免 Node，下载官方独立二进制）按钮
        if check.key == "codex_bin" and check.status == FAIL:
            install_btn = QPushButton(tr("自动安装"))
            install_btn.setToolTip(tr("从 GitHub 下载官方独立二进制（免装 Node.js）"))
            install_btn.clicked.connect(
                lambda _=False, b=install_btn: self._install_codex(b))
            lay.addWidget(install_btn)
        # codex 已装未登录：给"去登录"按钮（可见终端里跑 codex login）
        if check.key == "codex_login" and check.status == FAIL:
            login_btn = QPushButton(tr("去登录"))
            login_btn.setToolTip(tr("打开终端窗口运行 codex login（浏览器授权）"))
            login_btn.clicked.connect(
                lambda _=False, b=login_btn: self._login_codex(b))
            lay.addWidget(login_btn)
        if check.fix_command:
            btn = QPushButton(tr("复制命令"))
            btn.setToolTip(check.fix_command)
            btn.clicked.connect(lambda _=False, c=check.fix_command, b=btn: self._copy(c, b))
            lay.addWidget(btn)
        return row

    def _install_codex(self, btn: QPushButton) -> None:
        """后台线程下载安装 codex CLI，完成后重新检测。"""
        import threading

        from ..bootstrap import BootstrapError, install_codex_cli

        btn.setEnabled(False)
        btn.setText(tr("下载中…"))

        def _work():
            try:
                path = install_codex_cli()
                self._after_install(path, None)
            except BootstrapError as exc:
                self._after_install(None, str(exc))

        def _safe_work():
            try:
                _work()
            except Exception as exc:  # 兜底，防线程静默死
                self._after_install(None, str(exc))

        threading.Thread(target=_safe_work, daemon=True).start()

    def _after_install(self, path, error) -> None:
        from PyQt6.QtCore import QTimer

        def _apply():
            from ..app_server import reset_codex_bin_cache

            reset_codex_bin_cache()  # 装好了，让发现逻辑重新找
            self._reload_checks()
            if error:
                from PyQt6.QtWidgets import QMessageBox

                QMessageBox.warning(self, tr("自动安装失败"), str(error))

        QTimer.singleShot(0, _apply)  # 回主线程操作 UI

    def _login_codex(self, btn: QPushButton) -> None:
        from ..app_server import find_codex_bin
        from ..bootstrap import open_login_terminal

        try:
            path = find_codex_bin()
        except Exception:
            btn.setText(tr("先安装"))
            return
        if open_login_terminal(path):
            btn.setText(tr("已打开终端"))
        else:
            btn.setText(tr("请手动运行 codex login"))

    @staticmethod
    def _copy(command: str, btn: QPushButton) -> None:
        QGuiApplication.clipboard().setText(command)
        btn.setText(tr("已复制"))

    # ---------- 结果 ----------

    def _on_skip(self) -> None:
        self._settings.set("wizard_done", True)
        self.accept()

    def _on_finish(self) -> None:
        web = self._web_cb.isChecked()
        self._settings.set("web_enabled", web)
        self._settings.set("tunnel_enabled", web)  # 隧道随手机访问开关
        self._settings.set("notify_enabled", self._notify_cb.isChecked())
        self._settings.set("wizard_done", True)
        self.accept()
