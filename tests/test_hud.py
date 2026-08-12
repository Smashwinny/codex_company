"""HUD 冒烟测试：offscreen 平台下实例化、应用快照、错误降级、多 provider 分区。

运行方式：QT_QPA_PLATFORM=offscreen pytest tests/test_hud.py
（conftest.py 中已设置该环境变量）
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from codex_quota.app_server import parse_rate_limits_response
from codex_quota.state import StateStore
from codex_quota.ui.hud import FloatingHud, _countdown_text
from codex_quota.ui.widgets import QuotaBar, threshold_color
from tests.conftest import FakeProvider, codex_snapshot
from tests.test_parse import NOW, REAL_RESPONSE


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def snap():
    return codex_snapshot()


@pytest.fixture
def hud(qapp, monkeypatch):
    # 冒烟测试不触发真实取数：把 refresh 替换为 no-op
    monkeypatch.setattr(FloatingHud, "refresh", lambda self: None)
    w = FloatingHud(providers=[FakeProvider("codex", "Codex")])
    yield w
    w.close()
    w.deleteLater()


class TestHudSmoke:
    def test_initial_state(self, hud):
        assert "额度监控" in hud._title.text()

    def test_apply_snapshot(self, hud):
        hud._stores["codex"].on_success(snap())
        hud._apply()
        header_texts = [h.text() for h in hud._section_headers]
        assert any("Codex" in t and "prolite" in t for t in header_texts)
        # 主窗口一行 + Spark 一行
        assert len(hud._rows) == 2
        assert "剩 9%" in hud._rows[0][0].pct.text()
        assert "更新于" in hud._footer.text()

    def test_error_without_history(self, hud):
        hud._stores["codex"].on_error("app-server 响应超时（8 秒）")
        hud._apply()
        assert "超时" in hud._footer.text()

    def test_error_with_history_marks_stale(self, hud):
        hud._stores["codex"].on_success(snap())
        hud._stores["codex"].on_error("no-response")
        hud._apply()
        assert "数据陈旧" in hud._footer.text()
        assert len(hud._rows) == 2  # 旧数据仍在

    def test_retick_updates_countdown(self, hud):
        hud._stores["codex"].on_success(snap())
        hud._apply()
        before = hud._rows[0][0].countdown.text()
        hud._retick()
        # 文本结构不变（同一时刻），至少不崩且仍是倒计时格式
        assert "重置" in before
        assert "重置" in hud._rows[0][0].countdown.text()

    def test_multi_provider_sections(self, hud, tmp_path):
        kimi_snap = snap()
        kimi_snap.provider = "kimi"
        kimi_snap.plan_type = "kimi-code/k3"
        hud._providers.append(FakeProvider("kimi", "Kimi"))
        hud._stores["kimi"] = StateStore(cache_path=str(tmp_path / "kimi.json"))
        hud._stores["codex"].on_success(snap())
        hud._stores["kimi"].on_success(kimi_snap)
        hud._apply()
        header_texts = [h.text() for h in hud._section_headers]
        assert any("Codex" in t for t in header_texts)
        assert any("Kimi" in t and "k3" in t for t in header_texts)
        assert len(hud._rows) == 4  # 每个 provider 两行

    def test_one_provider_failure_isolated(self, hud, tmp_path):
        hud._providers.append(FakeProvider("kimi", "Kimi"))
        hud._stores["kimi"] = StateStore(cache_path=str(tmp_path / "kimi.json"))
        hud._stores["codex"].on_success(snap())
        hud._stores["kimi"].on_error("kimi web 启动超时")
        hud._apply()
        assert len(hud._rows) == 2  # codex 分区正常
        header_texts = [h.text() for h in hud._section_headers]
        assert any("Kimi" in t for t in header_texts)  # kimi 分区仍在（显示错误）


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
    """同步发信号的假取数线程：start() 立即对每个 provider emit 成功。"""

    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, providers, timeout=8.0, parent=None):
        super().__init__(parent)
        self._providers = providers

    def start(self):
        for p in self._providers:
            s = p.fetch()
            self.succeeded.emit(p.name, s)
        self.finished.emit()


class TestInteractions:
    """真实 refresh 路径（不 mock refresh 本身），仅替换取数线程。"""

    @pytest.fixture
    def live_hud(self, qapp, monkeypatch):
        monkeypatch.setattr("codex_quota.ui.hud.QuotaFetcher", _FakeFetcher)
        w = FloatingHud(providers=[FakeProvider("codex", "Codex", snapshot=snap())])
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

    def test_refresh_after_delete_later_no_crash(self, live_hud):
        """回归：fetcher deleteLater 后再次刷新不得访问悬垂引用（曾致崩溃）。"""
        # 构造函数已 refresh 一次；处理延迟删除事件，模拟事件循环真正删掉 fetcher
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        live_hud.refresh()  # 旧代码：isRunning() 访问已删除 C++ 对象 → RuntimeError
        assert len(live_hud._rows) == 2
        # 再来一轮，确保状态机稳定
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        live_hud.refresh()
        assert len(live_hud._rows) == 2

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
