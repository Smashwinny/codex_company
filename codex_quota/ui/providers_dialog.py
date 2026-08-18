"""额度来源管理对话框：provider 开关 + API key 录入 + 测试连接。

密钥型 provider（deepseek/openrouter）由 KEY_PROVIDER_SPECS 声明式定义，
新增同类 provider 只需在 providers/ 实现类并在装配处注册。
保存写回 providers.toml 并热重载（hud.reload_providers），不用重启应用。
"""

from __future__ import annotations

import time
from typing import Optional, Type

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from ..providers.deepseek import DeepSeekProvider, read_dsh_api_key
from ..providers.openrouter import OpenRouterProvider
from ..state import StateStore
from .theme import DIALOG_STYLE, FG_DIM, style_section

# 密钥型 provider 声明：新增同类服务在此处加一行 + base.py 装配一行
KEY_PROVIDER_SPECS = [
    {"type": "deepseek", "label": "DeepSeek", "cls": DeepSeekProvider},
    {"type": "openrouter", "label": "OpenRouter", "cls": OpenRouterProvider},
]


class _TestRunner(QThread):
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, provider_cls: Type, api_key: str, parent=None):
        super().__init__(parent)
        self._cls = provider_cls
        self._key = api_key

    def run(self):
        try:
            snap = self._cls(api_key=self._key).fetch()
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.done.emit(snap)


class KeyProviderRow(QFrame):
    """一行密钥型 provider：勾选 + API key 输入 + 测试连接 + 结果。"""

    def __init__(self, spec: dict, section: dict, parent_dialog: QDialog):
        super().__init__(parent_dialog)
        self.spec = spec
        self._dialog = parent_dialog
        self._runner: Optional[_TestRunner] = None

        self.cb = QCheckBox(spec["label"])
        self.cb.setChecked(bool(section.get("enabled", True)) and bool(section))

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(tr("API key（sk-… 或 $环境变量）"))
        self.key.setText(str(section.get("api_key") or ""))

        self.test_btn = QPushButton(tr("测试连接"))
        self.test_btn.clicked.connect(self._test)
        self.result = QLabel("")
        self.result.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.cb)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, stretch=1)
        key_row.addWidget(self.test_btn)
        lay.addLayout(key_row)
        lay.addWidget(self.result)

    def _test(self) -> None:
        if self._runner is not None and self._runner.isRunning():
            return
        self.result.setStyleSheet(f"color: {FG_DIM};")
        self.result.setText(tr("测试中…"))
        self.test_btn.setEnabled(False)
        self._runner = _TestRunner(self.spec["cls"], self.key.text().strip(), parent=self)
        self._runner.done.connect(self._on_ok)
        self._runner.error.connect(self._on_err)
        self._runner.finished.connect(lambda: self.test_btn.setEnabled(True))
        self._runner.start()

    def _on_ok(self, snap) -> None:
        w = snap.primary_limit.primary
        text = w.abs_text or (
            tr("剩 {p}%").format(p=f"{w.remaining_percent:.0f}")
            if w.remaining_percent is not None else "?")
        self.result.setStyleSheet("color: #3fb950;")
        self.result.setText(tr("✅ 连接成功：余额 {t}").format(t=text))

    def _on_err(self, message: str) -> None:
        self.result.setStyleSheet("color: #f85149;")
        self.result.setText(f"❌ {message}")


class ProvidersDialog(QDialog):
    def __init__(self, hud, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("管理额度来源"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(DIALOG_STYLE)
        self._hud = hud

        cfg = load_providers_config()
        lay = QVBoxLayout(self)

        # --- 本地 provider 开关 ---
        local_label = QLabel(style_section(tr("本地工具")))
        from PyQt6.QtCore import Qt as _Qt
        local_label.setTextFormat(_Qt.TextFormat.RichText)
        lay.addWidget(local_label)
        self._codex_cb = QCheckBox(tr("Codex（本地 codex CLI）"))
        self._codex_cb.setChecked(bool(cfg.get("codex", {}).get("enabled", True)))
        self._kimi_cb = QCheckBox(tr("Kimi（本地 kimi CLI）"))
        self._kimi_cb.setChecked(bool(cfg.get("kimi", {}).get("enabled", True)))
        self._claude_cb = QCheckBox(tr("Claude Code（本地登录凭证）"))
        self._claude_cb.setChecked(bool(cfg.get("claude", {}).get("enabled", True)))
        lay.addWidget(self._codex_cb)
        lay.addWidget(self._kimi_cb)
        lay.addWidget(self._claude_cb)

        # --- 密钥型 provider ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #30363d;")
        lay.addWidget(sep)
        key_label = QLabel(style_section(tr("云端服务（API key）")))
        key_label.setTextFormat(_Qt.TextFormat.RichText)
        lay.addWidget(key_label)
        self._rows: dict[str, KeyProviderRow] = {}
        for spec in KEY_PROVIDER_SPECS:
            section = cfg.get(spec["type"], {})
            row = KeyProviderRow(spec, section, self)
            # dsh 凭证自动检测：无显式配置但 dsh 里有 key → 默认启用并提示
            if (spec["type"] == "deepseek" and not section.get("api_key")
                    and read_dsh_api_key()):
                row.cb.setChecked(True)
                row.result.setText(tr("✓ 已检测到 dsh 凭证，key 留空即可自动使用"))
                row.result.setStyleSheet(f"color: {FG_DIM};")
            self._rows[spec["type"]] = row
            lay.addWidget(row)

        # --- 手动余额（免 key） ---
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #30363d;")
        lay.addWidget(sep2)
        manual_label = QLabel(style_section(tr("手动余额（免 key）")))
        manual_label.setTextFormat(_Qt.TextFormat.RichText)
        lay.addWidget(manual_label)
        m = cfg.get("manual", {})
        self._manual_cb = QCheckBox(tr("手动余额（适合网页版用户，定期手填）"))
        self._manual_cb.setChecked(bool(m.get("enabled", True)) and bool(m))
        lay.addWidget(self._manual_cb)
        m_row = QHBoxLayout()
        self._manual_name = QLineEdit(str(m.get("display_name") or "DeepSeek（手动）"))
        self._manual_name.setPlaceholderText(tr("显示名称"))
        self._manual_amount = QLineEdit(
            "" if m.get("balance") is None else str(m.get("balance")))
        self._manual_amount.setPlaceholderText(tr("当前余额，如 23.5"))
        self._manual_amount.setMaximumWidth(140)
        self._manual_unit = QComboBox()
        self._manual_unit.addItems(["CNY", "USD", "credits"])
        self._manual_unit.setCurrentText(str(m.get("unit") or "CNY"))
        self._manual_unit.setMaximumWidth(90)
        m_row.addWidget(self._manual_name, stretch=1)
        m_row.addWidget(self._manual_amount)
        m_row.addWidget(self._manual_unit)
        lay.addLayout(m_row)
        updated = m.get("updated_at")
        info = (tr("上次填写：{t}").format(t=StateStore.freshness_text(float(updated)))
                if isinstance(updated, (int, float)) else tr("从未填写"))
        self._manual_info = QLabel(info)
        self._manual_info.setProperty("dim", True)
        lay.addWidget(self._manual_info)

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

    # ---------- 保存 ----------

    def _save(self) -> None:
        cfg = load_providers_config()
        cfg["codex"] = {"enabled": self._codex_cb.isChecked()}
        cfg["kimi"] = {"enabled": self._kimi_cb.isChecked()}
        cfg["claude"] = {"enabled": self._claude_cb.isChecked()}
        for spec in KEY_PROVIDER_SPECS:
            row = self._rows[spec["type"]]
            if row.cb.isChecked():
                cfg[spec["type"]] = {
                    "type": spec["type"],
                    "enabled": True,
                    "display_name": spec["label"],
                    "api_key": row.key.text().strip(),
                }
            else:
                # 写显式禁用标记而非删除：否则 dsh 自动检测会把它加回来
                cfg[spec["type"]] = {"type": spec["type"], "enabled": False}
        # 手动余额：填写新金额则刷新 updated_at；留空保留旧值
        if self._manual_cb.isChecked():
            prev = cfg.get("manual", {})
            amount_text = self._manual_amount.text().strip()
            try:
                balance: Optional[float] = float(amount_text) if amount_text else None
            except ValueError:
                balance = None
            if balance is not None:
                updated_at = time.time()
            else:
                balance = prev.get("balance") if isinstance(
                    prev.get("balance"), (int, float)) else None
                updated_at = prev.get("updated_at")
            cfg["manual"] = {
                "type": "manual",
                "enabled": True,
                "display_name": self._manual_name.text().strip() or "手动余额",
                "balance": balance,
                "unit": self._manual_unit.currentText(),
                "updated_at": updated_at,
            }
        else:
            cfg.pop("manual", None)
        save_providers_config(cfg)
        self._hud.reload_providers()
        self.accept()
