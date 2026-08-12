"""HUD 冒烟测试：offscreen 平台下实例化、应用快照、错误降级。

运行方式：QT_QPA_PLATFORM=offscreen pytest tests/test_hud.py
（pytest.ini 中已设置该环境变量）
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from codex_quota.app_server import parse_rate_limits_response
from codex_quota.state import StateStore
from codex_quota.ui.hud import FloatingHud, _countdown_text
from codex_quota.ui.widgets import QuotaBar, threshold_color
from tests.test_parse import NOW, REAL_RESPONSE


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def hud(qapp, monkeypatch):
    # 冒烟测试不触发真实取数：把 refresh 替换为 no-op
    monkeypatch.setattr(FloatingHud, "refresh", lambda self: None)
    w = FloatingHud()
    yield w
    w.close()
    w.deleteLater()


def snap():
    return parse_rate_limits_response(REAL_RESPONSE, now=NOW)


class TestHudSmoke:
    def test_initial_state(self, hud):
        assert "Codex" in hud._title.text()

    def test_apply_snapshot(self, hud):
        hud._apply(hud._store.on_success(snap()))
        assert "prolite" in hud._title.text()
        # 主窗口一行 + Spark 一行
        assert len(hud._rows) == 2
        assert "剩 9%" in hud._rows[0][0].pct.text()  # 已用 91% → 剩余 9%
        assert "更新于" in hud._footer.text()

    def test_error_without_history(self, hud):
        hud._apply(hud._store.on_error("app-server 响应超时（8 秒）"))
        assert "超时" in hud._footer.text()

    def test_error_with_history_marks_stale(self, hud):
        hud._store.on_success(snap())
        hud._apply(hud._store.on_error("no-response"))
        assert "数据陈旧" in hud._footer.text()
        assert len(hud._rows) == 2  # 旧数据仍在

    def test_retick_updates_countdown(self, hud):
        hud._apply(hud._store.on_success(snap()))
        before = hud._rows[0][0].countdown.text()
        hud._retick()
        # 文本结构不变（同一时刻），至少不崩且仍是倒计时格式
        assert "重置" in before
        assert "重置" in hud._rows[0][0].countdown.text()


class TestWidgets:
    def test_threshold_colors(self):
        # 按剩余量：绿 >30 / 黄 ≤30 / 红 ≤10
        assert threshold_color(None) != threshold_color(50)
        assert threshold_color(50).name() == "#3fb950"
        assert threshold_color(20).name() == "#d29922"
        assert threshold_color(5).name() == "#f85149"

    def test_quota_bar_paint_offscreen(self, qapp):
        bar = QuotaBar()
        bar.set_remaining(9)
        bar.resize(200, 10)
        pix = bar.grab()  # 触发 paintEvent，不崩即通过
        assert not pix.isNull()

    def test_quota_bar_none_and_over(self, qapp):
        bar = QuotaBar()
        bar.set_remaining(None)
        bar.resize(200, 10)
        assert not bar.grab().isNull()
        bar.set_remaining(150)
        assert not bar.grab().isNull()


class _FakeFetcher(QObject):
    """同步发信号的假取数线程：start() 立即 emit 成功。"""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self._snapshot = snapshot

    def isRunning(self):
        return False

    def start(self):
        self.succeeded.emit(self._snapshot)
        self.finished.emit()


class TestInteractions:
    """真实 refresh 路径（不 mock refresh 本身），仅替换取数线程。"""

    @pytest.fixture
    def live_hud(self, qapp, monkeypatch):
        monkeypatch.setattr(
            "codex_quota.ui.hud.QuotaFetcher",
            lambda parent=None: _FakeFetcher(snap(), parent),
        )
        w = FloatingHud()
        yield w
        w.close()
        w.deleteLater()

    def test_constructor_applies_snapshot(self, live_hud):
        assert len(live_hud._rows) == 2
        assert "剩 9%" in live_hud._rows[0][0].pct.text()

    def test_refresh_button_refetches(self, live_hud):
        assert live_hud._refresh_btn.isEnabled()
        QTest.mouseClick(live_hud._refresh_btn, Qt.MouseButton.LeftButton)
        assert len(live_hud._rows) == 2  # 重新构建
        assert live_hud._refresh_btn.isEnabled()  # finished 后恢复可用
        assert "更新于" in live_hud._footer.text()

    def test_close_button_hides(self, live_hud):
        live_hud.show()
        QTest.mouseClick(live_hud._close_btn, Qt.MouseButton.LeftButton)
        assert not live_hud.isVisible()

    def test_compact_toggle(self, live_hud):
        assert len(live_hud._rows) == 2  # 主行 + Spark
        live_hud.set_compact(True)
        assert len(live_hud._rows) == 1          # 附加桶隐藏
        assert live_hud._rows[0][0].countdown.isHidden()  # 倒计时也收起
        live_hud.set_compact(False)
        assert len(live_hud._rows) == 2
        assert not live_hud._rows[0][0].countdown.isHidden()

    def test_compact_persists(self, live_hud):
        live_hud.set_compact(True)
        assert live_hud._settings.get("compact") is True

    def test_double_click_toggles_compact(self, live_hud):
        live_hud.mouseDoubleClickEvent(None)
        assert live_hud._compact is True
        live_hud.mouseDoubleClickEvent(None)
        assert live_hud._compact is False

    def test_opacity_clamps_and_persists(self, live_hud):
        # Qt 内部按 8bit 存储透明度，断言用 abs=0.01 容差
        live_hud.set_opacity(0.1)
        assert live_hud.windowOpacity() == pytest.approx(0.3, abs=0.01)   # 下限
        live_hud.set_opacity(1.5)
        assert live_hud.windowOpacity() == pytest.approx(1.0, abs=0.01)   # 上限
        live_hud.set_opacity(0.75)
        assert live_hud._settings.get("opacity") == pytest.approx(0.75)

    def test_restore_position(self, live_hud):
        live_hud._settings.set("pos", [123, 456])
        live_hud.restore_position()
        assert (live_hud.x(), live_hud.y()) == (123, 456)

    def test_restore_position_ignores_junk(self, live_hud):
        live_hud.move(50, 60)
        live_hud._settings.set("pos", "not-a-pos")
        live_hud.restore_position()
        assert (live_hud.x(), live_hud.y()) == (50, 60)


class TestCountdownText:
    def test_formats(self, snap=None):
        from codex_quota.app_server import QuotaWindow

        assert _countdown_text(QuotaWindow(), NOW) == "重置时间未知"
        assert _countdown_text(QuotaWindow(reset_at=NOW - 5), NOW) == "即将重置"
        assert "分后重置" in _countdown_text(QuotaWindow(reset_at=NOW + 600), NOW)
        assert "小时" in _countdown_text(QuotaWindow(reset_at=NOW + 7200), NOW)
        assert "天" in _countdown_text(QuotaWindow(reset_at=NOW + 90000), NOW)


class TestStateStore:
    def test_freshness(self):
        assert StateStore.freshness_text(None) == "无数据"
        assert StateStore.freshness_text(NOW, now=NOW + 30) == "30 秒前"
        assert StateStore.freshness_text(NOW, now=NOW + 300) == "5 分钟前"
        assert StateStore.freshness_text(NOW, now=NOW + 7200) == "2 小时前"
