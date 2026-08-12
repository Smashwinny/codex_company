"""托盘模块测试（offscreen；不要求系统托盘真实可用）。"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from codex_quota.app_server import parse_rate_limits_response
from codex_quota.state import StateStore, ViewState
from codex_quota.ui.tray import (
    QuotaTray,
    make_dot_icon,
    summary_lines,
    worst_remaining,
)
from codex_quota.ui.widgets import COLOR_UNKNOWN
from tests.test_parse import NOW, REAL_RESPONSE


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def snap():
    return parse_rate_limits_response(REAL_RESPONSE, now=NOW)


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
    def test_picks_minimum(self):
        state = ViewState(snapshot=snap())
        # 主桶剩 9%，Spark 剩 98% → 最坏是 9%
        assert worst_remaining(state) == pytest.approx(9.0)

    def test_no_data(self):
        assert worst_remaining(ViewState()) is None

    def test_all_unknown(self):
        s = snap()
        for limit in s.limits:
            limit.primary.used_percent = None
        assert worst_remaining(ViewState(snapshot=s)) is None


class TestSummaryLines:
    def test_lines(self):
        lines = summary_lines(ViewState(snapshot=snap()))
        assert lines == ["本周 剩 9%", "GPT-5.3-Codex-Spark · 本周 剩 98%"]

    def test_no_data(self):
        assert summary_lines(ViewState()) == ["无数据"]


class TestQuotaTray:
    @pytest.fixture
    def tray(self, qapp, monkeypatch):
        # 不触发真实取数
        from codex_quota.ui.hud import FloatingHud

        monkeypatch.setattr(FloatingHud, "refresh", lambda self: None)
        hud = FloatingHud()
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
        assert texts[-1] == "退出"
        # 初始无数据 → 摘要一行
        assert "无数据" in texts

    def test_update_state_rebuilds_summary(self, tray):
        t, hud = tray
        hud._apply(hud._store.on_success(snap()))
        # 校验真实菜单顺序（含分隔符过滤后）
        texts = [a.text() for a in t._menu.actions() if not a.isSeparator()]
        assert texts == ["显示悬浮窗", "立即刷新", "开机自启",
                         "本周 剩 9%", "GPT-5.3-Codex-Spark · 本周 剩 98%",
                         "退出"]
        assert all(not a.isEnabled() for a in t._summary_actions)
        assert "剩 9%" in t.tray.toolTip()

    def test_autostart_toggle(self, tray):
        from codex_quota import autostart

        t, _hud = tray
        assert autostart.is_enabled() is False
        t.action_autostart.setChecked(True)
        assert autostart.is_enabled() is True
        t.action_autostart.setChecked(False)
        assert autostart.is_enabled() is False

    def test_tooltip_marks_stale(self, tray):
        t, hud = tray
        hud._store.on_success(snap())
        hud._apply(hud._store.on_error("boom"))
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
        hud._apply(hud._store.on_success(snap()))
        img = t.tray.icon().pixmap(64, 64).toImage()
        center = img.pixelColor(32, 32)
        # 剩 9% ≤ 10 → 红色
        assert center.red() > 200 and center.green() < 100
