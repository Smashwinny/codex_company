"""provider 管理对话框测试（offscreen）。"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from codex_quota.providers.config import load_providers_config, save_providers_config
from codex_quota.providers.deepseek import parse_balance
from codex_quota.ui.providers_dialog import ProvidersDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeHud:
    def __init__(self):
        self.reloaded = 0

    def reload_providers(self):
        self.reloaded += 1


class _FakeRunner(QObject):
    """同步假测试线程：sk-good 成功，其余失败。"""

    done = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, provider_cls, api_key, parent=None):
        super().__init__(parent)
        self._key = api_key

    def isRunning(self):
        return False

    def start(self):
        if self._key == "sk-good":
            self.done.emit(parse_balance({"balance_infos": [
                {"currency": "CNY", "total_balance": "88.00"}]}))
        else:
            self.error.emit("API key 无效（401），请检查")
        self.finished.emit()


@pytest.fixture
def dialog(qapp, monkeypatch):
    monkeypatch.setattr("codex_quota.ui.providers_dialog._TestRunner", _FakeRunner)
    hud = FakeHud()
    dlg = ProvidersDialog(hud)
    yield dlg, hud
    dlg.close()
    dlg.deleteLater()


class TestDialog:
    def test_initial_state_defaults(self, dialog):
        dlg, _hud = dialog
        assert dlg._codex_cb.isChecked() is True
        assert dlg._kimi_cb.isChecked() is True
        assert dlg._claude_cb.isChecked() is True
        # 无配置时密钥型默认关
        assert dlg._rows["deepseek"].cb.isChecked() is False
        assert dlg._rows["openrouter"].cb.isChecked() is False

    def test_initial_state_from_config(self, qapp, monkeypatch):
        save_providers_config({
            "kimi": {"enabled": False},
            "claude": {"enabled": False},
            "deepseek": {"type": "deepseek", "enabled": True, "api_key": "sk-x"},
            "openrouter": {"type": "openrouter", "enabled": True, "api_key": "sk-y"},
        })
        monkeypatch.setattr("codex_quota.ui.providers_dialog._TestRunner", _FakeRunner)
        dlg = ProvidersDialog(FakeHud())
        assert dlg._kimi_cb.isChecked() is False
        assert dlg._claude_cb.isChecked() is False
        assert dlg._rows["deepseek"].cb.isChecked() is True
        assert dlg._rows["deepseek"].key.text() == "sk-x"
        assert dlg._rows["openrouter"].cb.isChecked() is True
        assert dlg._rows["openrouter"].key.text() == "sk-y"
        dlg.close()
        dlg.deleteLater()

    def test_save_writes_config_and_reloads(self, dialog):
        dlg, hud = dialog
        dlg._kimi_cb.setChecked(False)
        dlg._rows["deepseek"].cb.setChecked(True)
        dlg._rows["deepseek"].key.setText("sk-new")
        dlg._rows["openrouter"].cb.setChecked(True)
        dlg._rows["openrouter"].key.setText("$OR_KEY")
        QTest.mouseClick(next(b for b in dlg.findChildren(
            type(dlg._rows["deepseek"].test_btn)) if b.text() == "保存"),
            Qt.MouseButton.LeftButton)
        cfg = load_providers_config()
        assert cfg["kimi"]["enabled"] is False
        assert cfg["claude"]["enabled"] is True
        assert cfg["deepseek"] == {"type": "deepseek", "enabled": True,
                                   "display_name": "DeepSeek", "api_key": "sk-new"}
        assert cfg["openrouter"]["api_key"] == "$OR_KEY"
        assert hud.reloaded == 1

    def test_save_removes_unchecked_key_providers(self, qapp, monkeypatch):
        save_providers_config({
            "deepseek": {"type": "deepseek", "enabled": True, "api_key": "sk-x"},
            "openrouter": {"type": "openrouter", "enabled": True, "api_key": "sk-y"},
        })
        monkeypatch.setattr("codex_quota.ui.providers_dialog._TestRunner", _FakeRunner)
        hud = FakeHud()
        dlg = ProvidersDialog(hud)
        dlg._rows["deepseek"].cb.setChecked(False)
        dlg._rows["openrouter"].cb.setChecked(True)
        dlg._save()
        cfg = load_providers_config()
        assert "deepseek" not in cfg
        assert "openrouter" in cfg
        dlg.close()
        dlg.deleteLater()

    def test_test_button_success(self, dialog):
        dlg, _hud = dialog
        row = dlg._rows["deepseek"]
        row.key.setText("sk-good")
        QTest.mouseClick(row.test_btn, Qt.MouseButton.LeftButton)
        assert "✅" in row.result.text()
        assert "88.00" in row.result.text()
        assert row.test_btn.isEnabled()

    def test_test_button_failure(self, dialog):
        dlg, _hud = dialog
        row = dlg._rows["openrouter"]
        row.key.setText("sk-bad")
        QTest.mouseClick(row.test_btn, Qt.MouseButton.LeftButton)
        assert "❌" in row.result.text()
