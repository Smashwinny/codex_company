"""provider 管理对话框测试（offscreen）。"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from codex_quota.providers.config import load_providers_config, save_providers_config
from codex_quota.ui.providers_dialog import ProvidersDialog
from tests.conftest import codex_snapshot


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
    """同步假测试线程。"""

    done = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, api_key, parent=None):
        super().__init__(parent)
        self._key = api_key

    def isRunning(self):
        return False

    def start(self):
        if self._key == "sk-good":
            snap = codex_snapshot()
            from codex_quota.providers.deepseek import parse_balance

            snap = parse_balance({"balance_infos": [
                {"currency": "CNY", "total_balance": "88.00"}]})
            self.done.emit(snap)
        else:
            self.error.emit("DeepSeek API key 无效（401），请检查")
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
        assert dlg._ds_cb.isChecked() is False  # 无 deepseek 配置时默认关

    def test_initial_state_from_config(self, qapp, monkeypatch):
        save_providers_config({
            "kimi": {"enabled": False},
            "deepseek": {"type": "deepseek", "enabled": True, "api_key": "sk-x"},
        })
        monkeypatch.setattr("codex_quota.ui.providers_dialog._TestRunner", _FakeRunner)
        dlg = ProvidersDialog(FakeHud())
        assert dlg._kimi_cb.isChecked() is False
        assert dlg._ds_cb.isChecked() is True
        assert dlg._ds_key.text() == "sk-x"
        dlg.close()
        dlg.deleteLater()

    def test_save_writes_config_and_reloads(self, dialog):
        dlg, hud = dialog
        dlg._kimi_cb.setChecked(False)
        dlg._ds_cb.setChecked(True)
        dlg._ds_key.setText("sk-new")
        QTest.mouseClick(next(b for b in dlg.findChildren(
            type(dlg._test_btn)) if b.text() == "保存"), Qt.MouseButton.LeftButton)
        cfg = load_providers_config()
        assert cfg["kimi"]["enabled"] is False
        assert cfg["deepseek"]["api_key"] == "sk-new"
        assert cfg["deepseek"]["type"] == "deepseek"
        assert hud.reloaded == 1

    def test_save_removes_deepseek_when_unchecked(self, qapp, monkeypatch):
        save_providers_config({
            "deepseek": {"type": "deepseek", "enabled": True, "api_key": "sk-x"}})
        monkeypatch.setattr("codex_quota.ui.providers_dialog._TestRunner", _FakeRunner)
        hud = FakeHud()
        dlg = ProvidersDialog(hud)
        dlg._ds_cb.setChecked(False)
        dlg._save()
        assert "deepseek" not in load_providers_config()
        dlg.close()
        dlg.deleteLater()

    def test_test_button_success(self, dialog):
        dlg, _hud = dialog
        dlg._ds_key.setText("sk-good")
        QTest.mouseClick(dlg._test_btn, Qt.MouseButton.LeftButton)
        assert "✅" in dlg._test_result.text()
        assert "88.00" in dlg._test_result.text()
        assert dlg._test_btn.isEnabled()

    def test_test_button_failure(self, dialog):
        dlg, _hud = dialog
        dlg._ds_key.setText("sk-bad")
        QTest.mouseClick(dlg._test_btn, Qt.MouseButton.LeftButton)
        assert "❌" in dlg._test_result.text()
