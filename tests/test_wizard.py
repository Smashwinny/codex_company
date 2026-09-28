"""首启向导测试（offscreen）。"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton

from codex_quota.settings import Settings
from codex_quota.ui.wizard import SetupWizardDialog, should_show_wizard
from tests.test_doctor import patch_finders


@pytest.fixture
def settings(tmp_path):
    return Settings(path=str(tmp_path / "settings.json"))


@pytest.fixture
def wizard(qapp, settings, monkeypatch):
    patch_finders(monkeypatch, codex="/bin/codex", login=False)
    monkeypatch.setattr("codex_quota.doctor._default_version", lambda p: "v1")
    w = SetupWizardDialog(settings)
    yield w
    w.close()
    w.deleteLater()


class TestShouldShow:
    def test_first_run(self, settings):
        assert should_show_wizard(settings) is True

    def test_after_done(self, settings):
        settings.set("wizard_done", True)
        assert should_show_wizard(settings, []) is False

    def test_failures_show_again_unless_customer_snoozed(self, settings):
        from codex_quota.doctor import CheckItem, FAIL

        settings.set("wizard_done", True)
        failed = [CheckItem("codex_bin", "Codex CLI", True, FAIL, "missing")]
        assert should_show_wizard(settings, failed) is True
        settings.set("codex_setup_snoozed", True)
        assert should_show_wizard(settings, failed) is False


class TestWizard:
    def test_rows_rendered(self, wizard):
        texts = [w.text() for w in wizard.findChildren(
            QPushButton) if w.toolTip()]
        # codex 未登录 → 有"复制命令"按钮，tooltip 是修复命令
        assert any("codex login" in t for t in
                   [b.toolTip() for b in wizard.findChildren(QPushButton)])

    def test_checkboxes_from_settings(self, wizard, settings):
        cbs = wizard.findChildren(QCheckBox)
        assert wizard._web_cb.isChecked() is True     # 默认开
        assert wizard._notify_cb.isChecked() is True

    def test_finish_saves_settings(self, wizard, settings):
        wizard._web_cb.setChecked(False)
        wizard._notify_cb.setChecked(True)
        QTest.mouseClick(wizard._done_btn, Qt.MouseButton.LeftButton)
        fresh = Settings(path=settings._path)
        assert fresh.get("wizard_done") is True
        assert fresh.get("web_enabled") is False
        assert fresh.get("tunnel_enabled") is False  # 随 web 开关
        assert fresh.get("notify_enabled") is True

    def test_skip_marks_done_only(self, wizard, settings):
        QTest.mouseClick(wizard._skip_btn, Qt.MouseButton.LeftButton)
        fresh = Settings(path=settings._path)
        assert fresh.get("wizard_done") is True
        assert fresh.get("codex_setup_snoozed") is True
        assert fresh.get("web_enabled") is True  # 未改动

    def test_copy_button(self, wizard):
        btn = next(b for b in wizard.findChildren(QPushButton)
                   if b.toolTip() == "codex login")
        QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
        assert QGuiApplication.clipboard().text() == "codex login"
        assert btn.text() == "已复制"

    def test_recheck_reloads(self, wizard, monkeypatch):
        # 模拟用户完成登录后再点"全部重新检测" → ❌ 应变 ✅
        monkeypatch.setattr("codex_quota.app_server.is_logged_in", lambda: True)
        monkeypatch.setattr("codex_quota.doctor._default_login_status",
                            lambda _path: True)
        recheck = next(b for b in wizard.findChildren(QPushButton)
                       if "重新检测" in b.text())
        QTest.mouseClick(recheck, Qt.MouseButton.LeftButton)
        # 旧行是 deleteLater 删除的，先处理延迟删除事件再断言
        from PyQt6.QtCore import QCoreApplication, QEvent

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not any(b.toolTip() == "codex login"
                       for b in wizard.findChildren(QPushButton))

    def test_install_result_signal_returns_to_ui_thread(self, wizard,
                                                        monkeypatch, qapp):
        import threading

        called = []
        monkeypatch.setattr(wizard, "_after_install",
                            lambda path, error: called.append((path, error)))
        # 重连到可观测 slot；从 Python 工作线程 emit，Qt 主线程必须收到。
        wizard._install_bridge.finished.disconnect()
        wizard._install_bridge.finished.connect(wizard._after_install)
        t = threading.Thread(
            target=lambda: wizard._install_bridge.finished.emit("codex.exe", None))
        t.start()
        t.join()
        for _ in range(20):
            qapp.processEvents()
            if called:
                break
            QTest.qWait(10)
        assert called == [("codex.exe", None)]
