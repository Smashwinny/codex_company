"""首启向导：环境检测 + 修复引导 + 可选功能开关。

首次启动（settings.wizard_done 为 False）自动弹出；之后可从托盘菜单
"初始设置 / 环境自检"再次打开。每个缺失项旁边就是"复制命令"按钮，
用户不需要开终端查文档。
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

STATUS_ICON = {OK: "✅", WARN: "➖", FAIL: "❌"}
FG = "#e6edf3"
FG_DIM = "#8b949e"


def should_show_wizard(settings: Settings) -> bool:
    return not bool(settings.get("wizard_done"))


class SetupWizardDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("codex-quota 初始设置"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(f"QDialog {{ background: #0d1117; color: {FG}; }}")

        self._settings = settings
        lay = QVBoxLayout(self)

        lay.addWidget(self._h1(tr("环境检测")))
        self._checks_area = QVBoxLayout()
        self._checks_area.setSpacing(4)
        lay.addLayout(self._checks_area)
        recheck = QPushButton(tr("全部重新检测"))
        recheck.clicked.connect(self._reload_checks)
        lay.addWidget(recheck)
        self._reload_checks()

        lay.addWidget(self._h1(tr("可选功能")))
        self._web_cb = QCheckBox(tr("手机访问（局域网 + 公网隧道）"))
        self._web_cb.setChecked(bool(settings.get("web_enabled")))
        self._notify_cb = QCheckBox(tr("额度重置推送（ntfy）"))
        self._notify_cb.setChecked(bool(settings.get("notify_enabled")))
        lay.addWidget(self._web_cb)
        lay.addWidget(self._notify_cb)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self._skip_btn = QPushButton(tr("跳过"))
        self._skip_btn.clicked.connect(self._on_skip)
        self._done_btn = QPushButton(tr("完成并启动"))
        self._done_btn.setDefault(True)
        self._done_btn.clicked.connect(self._on_finish)
        btns.addWidget(self._skip_btn)
        btns.addWidget(self._done_btn)
        lay.addLayout(btns)
        self.adjustSize()

    # ---------- 构建 ----------

    @staticmethod
    def _h1(text: str) -> QLabel:
        lbl = QLabel(f"<b>{text}</b>")
        lbl.setStyleSheet("font-size: 14px; margin-top: 6px;")
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
        text = QLabel(f"{STATUS_ICON[check.status]} <b>{check.name}</b> · {check.detail}")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {FG};" if check.status != WARN else f"color: {FG_DIM};")
        lay.addWidget(text, stretch=1)
        if check.fix_command:
            btn = QPushButton(tr("复制命令"))
            btn.setToolTip(check.fix_command)
            btn.clicked.connect(lambda _=False, c=check.fix_command, b=btn: self._copy(c, b))
            lay.addWidget(btn)
        return row

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
