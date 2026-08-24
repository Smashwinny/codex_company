"""托盘模块测试（offscreen；不要求系统托盘真实可用）。"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from codex_quota.state import ProviderView, ViewState
from codex_quota.ui.tray import (
    QuotaTray,
    make_dot_icon,
    summary_lines,
    worst_remaining,
)
from tests.conftest import FakeProvider, codex_snapshot
from tests.test_parse import NOW, REAL_RESPONSE


def snap():
    return codex_snapshot()


def view(state: ViewState, name="codex", display="Codex") -> ProviderView:
    return ProviderView(name=name, display_name=display, state=state)


class TestDotIcon:
    def test_center_pixel_matches_color(self):
        icon = make_dot_icon(QColor("#ff0000"), size=64)
        img = icon.pixmap(64, 64).toImage()
        center = img.pixelColor(32, 32)
        assert center.red() > 200 and center.green() < 60

    def test_transparent_corners(self):
        icon = make_dot_icon(QColor("#ff0000"), size=64)
        img = icon.pixmap(64, 64).toImage()
        assert img.pixelColor(0, 0).alpha() == 0


class TestWorstRemaining:
    def test_picks_minimum_across_providers(self):
        # codex 主桶剩 9%（Spark 98%）+ kimi 剩 50% → 最坏 9%
        kimi_snap = snap()
        kimi_snap.provider = "kimi"
        kimi_snap.primary_limit.primary.used_percent = 50
        views = [view(ViewState(snapshot=snap())),
                 view(ViewState(snapshot=kimi_snap), "kimi", "Kimi")]
        assert worst_remaining(views) == pytest.approx(9.0)

    def test_no_data(self):
        assert worst_remaining([view(ViewState())]) is None

    def test_all_unknown(self):
        s = snap()
        for limit in s.limits:
            limit.primary.used_percent = None
        assert worst_remaining([view(ViewState(snapshot=s))]) is None


class TestSummaryLines:
    def test_lines(self):
        lines = summary_lines([view(ViewState(snapshot=snap()))])
        assert lines == ["Codex · 本周 剩 9%",
                         "Codex GPT-5.3-Codex-Spark · 本周 剩 98%"]

    def test_multi_provider(self):
        kimi_snap = snap()
        kimi_snap.provider = "kimi"
        kimi_snap.limits = [kimi_snap.limits[0]]  # kimi 无附加桶
        views = [view(ViewState(snapshot=snap())),
                 view(ViewState(snapshot=kimi_snap), "kimi", "Kimi")]
        lines = summary_lines(views)
        assert lines[-1].startswith("Kimi · ")

    def test_no_data(self):
        assert summary_lines([view(ViewState())]) == ["Codex · 无数据"]


class TestQuotaTray:
    @pytest.fixture
    def tray(self, qapp, monkeypatch):
        # 不触发真实取数
        from codex_quota.ui.hud import FloatingHud

        monkeypatch.setattr(FloatingHud, "refresh", lambda self: None)
        hud = FloatingHud(providers=[FakeProvider("codex", "Codex")])
        t = QuotaTray(hud, qapp)
        yield t, hud
        hud.close()
        hud.deleteLater()

    def test_menu_structure(self, tray):
        t, _hud = tray
        texts = [a.text() for a in t._menu.actions() if not a.isSeparator()]
        assert texts[0] in ("隐藏悬浮窗", "显示悬浮窗")
        assert texts[1] == "立即刷新"
        assert texts[2] == "开机自启"
        assert texts[3] == "复制手机访问地址"
        assert texts[4] == "推送访问地址到手机"
        assert texts[5] == "手机通知（ntfy）订阅指引"
        assert texts[6] == "初始设置 / 环境自检"
        assert texts[7] == "管理额度来源"
        assert texts[8] == "告警阈值"
        assert texts[9] == "主模型显示"
        assert texts[10] == "重置提醒"
        assert texts[-1] == "退出"
        # 初始无数据 → 摘要一行
        assert any("无数据" in x for x in texts)

    def test_update_state_rebuilds_summary(self, tray):
        t, hud = tray
        hud._stores["codex"].on_success(snap())
        hud._apply()
        # 校验真实菜单顺序（含分隔符过滤后）
        texts = [a.text() for a in t._menu.actions() if not a.isSeparator()]
        assert texts == ["显示悬浮窗", "立即刷新", "开机自启", "复制手机访问地址",
                         "推送访问地址到手机", "手机通知（ntfy）订阅指引",
                         "初始设置 / 环境自检", "管理额度来源", "告警阈值", "主模型显示",
                         "重置提醒",
                         "Codex · 本周 剩 9%", "Codex GPT-5.3-Codex-Spark · 本周 剩 98%",
                         "退出"]
        assert all(not a.isEnabled() for a in t._summary_actions)
        assert "剩 9%" in t.tray.toolTip()

    def test_tooltip_marks_stale(self, tray):
        t, hud = tray
        hud._stores["codex"].on_success(snap())
        hud._stores["codex"].on_error("boom")
        hud._apply()
        assert "数据陈旧" in t.tray.toolTip()

    def test_toggle_hud(self, tray):
        t, hud = tray
        assert not hud.isVisible()
        t._toggle_hud()
        assert hud.isVisible()
        assert t.action_toggle.text() == "隐藏悬浮窗"
        t._toggle_hud()
        assert not hud.isVisible()
        assert t.action_toggle.text() == "显示悬浮窗"

    def test_icon_reflects_worst_remaining(self, tray):
        t, hud = tray
        hud._stores["codex"].on_success(snap())
        hud._apply()
        img = t.tray.icon().pixmap(64, 64).toImage()
        center = img.pixelColor(32, 32)
        # 剩 9% ≤ 10 → 红色
        assert center.red() > 200 and center.green() < 100

    def test_autostart_toggle(self, tray, monkeypatch):
        from codex_quota import autostart

        t, _hud = tray
        state = {"enabled": False}
        monkeypatch.setattr(
            autostart, "is_enabled", lambda: state["enabled"])
        monkeypatch.setattr(
            autostart, "enable", lambda: state.update(enabled=True))
        monkeypatch.setattr(
            autostart, "disable", lambda: state.update(enabled=False))

        assert state["enabled"] is False
        t.action_autostart.setChecked(True)
        assert state["enabled"] is True
        t.action_autostart.setChecked(False)
        assert state["enabled"] is False

    def test_autostart_failure_rolls_back_without_escaping(self, tray, monkeypatch):
        from codex_quota import autostart

        t, _hud = tray
        t.action_autostart.setChecked(False)
        monkeypatch.setattr(
            autostart, "enable", lambda: (_ for _ in ()).throw(
                PermissionError("registry denied")))
        t.action_autostart.setChecked(True)
        assert t.action_autostart.isChecked() is False
        assert "registry denied" in t.action_autostart.toolTip()
