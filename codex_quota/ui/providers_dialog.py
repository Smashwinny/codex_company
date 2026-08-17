"""额度来源管理对话框：provider 开关 + API key 录入 + 测试连接。

保存写回 providers.toml 并热重载（hud.reload_providers），不用重启应用。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..providers.config import load_providers_config, save_providers_config
from ..providers.deepseek import DeepSeekProvider
from .theme import DIALOG_STYLE, FG_DIM


class _TestRunner(QThread):
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self._key = api_key

    def run(self):
        try:
            snap = DeepSeekProvider(api_key=self._key).fetch()
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.done.emit(snap)


class ProvidersDialog(QDialog):
    def __init__(self, hud, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("管理额度来源"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(DIALOG_STYLE)
        self._hud = hud
        self._runner: Optional[_TestRunner] = None

        cfg = load_providers_config()
        lay = QVBoxLayout(self)

        # --- 内置 provider 开关 ---
        self._codex_cb = QCheckBox(tr("Codex（本地 codex CLI）"))
        self._codex_cb.setChecked(bool(cfg.get("codex", {}).get("enabled", True)))
        self._kimi_cb = QCheckBox(tr("Kimi（本地 kimi CLI）"))
        self._kimi_cb.setChecked(bool(cfg.get("kimi", {}).get("enabled", True)))
        lay.addWidget(self._codex_cb)
        lay.addWidget(self._kimi_cb)

        # --- DeepSeek ---
        ds = cfg.get("deepseek", {})
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #30363d;")
        lay.addWidget(sep)
        self._ds_cb = QCheckBox("DeepSeek")
        self._ds_cb.setChecked(bool(ds.get("enabled", True)) and bool(ds))
        lay.addWidget(self._ds_cb)

        key_row = QHBoxLayout()
        self._ds_key = QLineEdit()
        self._ds_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ds_key.setPlaceholderText(tr("API key（sk-… 或 $环境变量）"))
        self._ds_key.setText(str(ds.get("api_key") or ""))
        self._test_btn = QPushButton(tr("测试连接"))
        self._test_btn.clicked.connect(self._test_deepseek)
        key_row.addWidget(self._ds_key, stretch=1)
        key_row.addWidget(self._test_btn)
        lay.addLayout(key_row)
        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)
        lay.addWidget(self._test_result)

        # --- 按钮 ---
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("保存"))
        save.setProperty("primary", True)
        save.setDefault(True)
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)
        self.adjustSize()

    # ---------- 测试连接 ----------

    def _test_deepseek(self) -> None:
        if self._runner is not None and self._runner.isRunning():
            return
        key = self._ds_key.text().strip()
        self._test_result.setStyleSheet(f"color: {FG_DIM};")
        self._test_result.setText(tr("测试中…"))
        self._test_btn.setEnabled(False)
        self._runner = _TestRunner(key, parent=self)
        self._runner.done.connect(self._on_test_ok)
        self._runner.error.connect(self._on_test_err)
        self._runner.finished.connect(lambda: self._test_btn.setEnabled(True))
        self._runner.start()

    def _on_test_ok(self, snap) -> None:
        w = snap.primary_limit.primary
        self._test_result.setStyleSheet("color: #3fb950;")
        self._test_result.setText(tr("✅ 连接成功：余额 {t}").format(t=w.abs_text or "?"))

    def _on_test_err(self, message: str) -> None:
        self._test_result.setStyleSheet("color: #f85149;")
        self._test_result.setText(f"❌ {message}")

    # ---------- 保存 ----------

    def _save(self) -> None:
        cfg = load_providers_config()
        cfg["codex"] = {"enabled": self._codex_cb.isChecked()}
        cfg["kimi"] = {"enabled": self._kimi_cb.isChecked()}
        key = self._ds_key.text().strip()
        if self._ds_cb.isChecked():
            cfg["deepseek"] = {
                "type": "deepseek",
                "enabled": True,
                "display_name": "DeepSeek",
                "api_key": key,
            }
        else:
            cfg.pop("deepseek", None)
        save_providers_config(cfg)
        self._hud.reload_providers()
        self.accept()
