"""手动余额 provider 测试。"""

from __future__ import annotations

import pytest

from codex_quota.providers.config import (
    load_providers_config,
    save_providers_config,
)
from codex_quota.providers.manual import ManualError, ManualProvider


class TestManualProvider:
    def test_fetch(self):
        p = ManualProvider(display_name="DeepSeek（手动）", balance=23.5,
                           unit="CNY", updated_at=1787000000.0)
        snap = p.fetch()
        assert snap.provider == "manual"
        assert snap.plan_type is None  # 名称走 display_name，避免标题重复
        assert snap.fetched_at == 1787000000.0  # 新鲜度=填写时间
        w = snap.primary_limit.primary
        assert w.is_balance
        assert w.abs_text == "¥23.50"

    def test_no_balance_guidance(self):
        with pytest.raises(ManualError, match="尚未填写"):
            ManualProvider().fetch()

    def test_from_section(self):
        p = ManualProvider.from_section({
            "type": "manual", "enabled": True,
            "display_name": "DeepSeek（手动）", "balance": 100, "unit": "USD",
            "updated_at": 1787000000,
        })
        assert p.display_name == "DeepSeek（手动）"
        snap = p.fetch()
        assert snap.primary_limit.primary.abs_text == "$100.00"

    def test_from_section_junk_tolerated(self):
        p = ManualProvider.from_section({"type": "manual", "balance": "abc"})
        assert p._balance is None
        assert p._updated_at is None


class TestAssembly:
    def test_manual_in_default_providers(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers

        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        monkeypatch.setattr("codex_quota.providers.deepseek.read_dsh_api_key",
                            lambda path=None: None)  # 无 dsh → 手动可用
        path = str(tmp_path / "providers.toml")
        save_providers_config({
            "manual": {"type": "manual", "enabled": True, "balance": 50,
                       "unit": "CNY", "updated_at": 1787000000}}, path)
        providers = default_providers(config_path=path)
        manual = next(p for p in providers if p.name == "manual")
        assert manual.fetch().primary_limit.primary.abs_text == "¥50.00"

    def test_manual_suppressed_when_dsh_present(self, tmp_path, monkeypatch):
        """检测到 dsh 凭证时，手动余额自动停用（DeepSeek 走自动查询）。"""
        from codex_quota.providers import default_providers

        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        monkeypatch.setattr("codex_quota.providers.deepseek.read_dsh_api_key",
                            lambda path=None: "sk-dsh")
        path = str(tmp_path / "providers.toml")
        save_providers_config({
            "manual": {"type": "manual", "enabled": True, "balance": 50,
                       "unit": "CNY", "updated_at": 1787000000}}, path)
        names = [p.name for p in default_providers(config_path=path)]
        assert "manual" not in names
        assert "deepseek" in names  # dsh 自动启用


class TestDialogManual:
    @pytest.fixture
    def dialog(self, qapp, monkeypatch):
        from codex_quota.ui.providers_dialog import ProvidersDialog

        # 默认隔离 dsh 检测（本机真实存在，会禁用手动行）
        monkeypatch.setattr("codex_quota.ui.providers_dialog.read_dsh_api_key",
                            lambda: None)

        class FakeHud:
            reloaded = 0

            def reload_providers(self):
                self.reloaded += 1

        hud = FakeHud()
        dlg = ProvidersDialog(hud)
        yield dlg, hud
        dlg.close()
        dlg.deleteLater()

    @pytest.fixture(scope="session")
    def qapp(self):
        pytest.importorskip("PyQt6")
        from PyQt6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])

    def test_manual_disabled_with_hint_when_dsh_detected(self, qapp, monkeypatch):
        """dsh 凭证存在：手动余额勾选框禁用 + 提示原因（透明度显示）。"""
        from codex_quota.ui.providers_dialog import ProvidersDialog

        monkeypatch.setattr("codex_quota.ui.providers_dialog.read_dsh_api_key",
                            lambda: "sk-dsh")

        class FakeHud:
            def reload_providers(self):
                pass

        dlg = ProvidersDialog(FakeHud())
        assert dlg._manual_cb.isChecked() is False
        assert dlg._manual_cb.isEnabled() is False
        assert "已自动停用" in dlg._manual_hint.text()
        dlg.close()
        dlg.deleteLater()

    def test_manual_enabled_with_hint_without_dsh(self, dialog):
        """无 dsh 凭证：手动余额可勾选，提示可手填。"""
        dlg, _hud = dialog
        assert dlg._manual_cb.isEnabled() is True
        assert "可勾选" in dlg._manual_hint.text()

    def test_save_with_dsh_disables_manual_keeps_values(self, qapp, monkeypatch):
        from codex_quota.ui.providers_dialog import ProvidersDialog

        monkeypatch.setattr("codex_quota.ui.providers_dialog.read_dsh_api_key",
                            lambda: "sk-dsh")
        save_providers_config({"manual": {
            "type": "manual", "enabled": True, "display_name": "DeepSeek（手动）",
            "balance": 50, "unit": "CNY", "updated_at": 1000.0}})

        class FakeHud:
            def reload_providers(self):
                pass

        dlg = ProvidersDialog(FakeHud())
        dlg._save()
        m = load_providers_config()["manual"]
        assert m["enabled"] is False
        assert m["balance"] == 50  # 值保留，dsh 消失后可恢复
        dlg.close()
        dlg.deleteLater()

    def test_save_manual_writes_updated_at(self, dialog):
        from codex_quota.providers.config import load_providers_config

        dlg, hud = dialog
        dlg._manual_cb.setChecked(True)
        dlg._manual_name.setText("DeepSeek（手动）")
        dlg._manual_amount.setText("23.5")
        dlg._save()
        cfg = load_providers_config()
        m = cfg["manual"]
        assert m["type"] == "manual"
        assert m["balance"] == 23.5
        assert m["unit"] == "CNY"
        assert isinstance(m["updated_at"], float)  # 写入当前时间
        assert hud.reloaded == 1

    def test_save_empty_amount_keeps_previous(self, dialog):
        from codex_quota.providers.config import load_providers_config, save_providers_config

        dlg, _hud = dialog
        save_providers_config({"manual": {
            "type": "manual", "enabled": True, "display_name": "DeepSeek（手动）",
            "balance": 50, "unit": "CNY", "updated_at": 1000.0}})
        dlg.close()
        from codex_quota.ui.providers_dialog import ProvidersDialog

        hud = type("H", (), {"reload_providers": lambda self: None})()
        dlg2 = ProvidersDialog(hud)
        dlg2._manual_cb.setChecked(True)
        dlg2._manual_amount.setText("")  # 不填 → 保留旧值
        dlg2._save()
        m = load_providers_config()["manual"]
        assert m["balance"] == 50
        assert m["updated_at"] == 1000.0
        dlg2.close()
        dlg2.deleteLater()

    def test_uncheck_removes_section(self, dialog):
        dlg, _hud = dialog
        dlg._manual_cb.setChecked(True)
        dlg._manual_amount.setText("10")
        dlg._save()
        from codex_quota.ui.providers_dialog import ProvidersDialog

        dlg3 = ProvidersDialog(type("H", (), {"reload_providers": lambda self: None})())
        dlg3._manual_cb.setChecked(False)
        dlg3._save()
        from codex_quota.providers.config import load_providers_config

        assert "manual" not in load_providers_config()
        dlg3.close()
        dlg3.deleteLater()
