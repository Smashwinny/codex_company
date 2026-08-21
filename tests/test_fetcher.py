"""RefreshScheduler 与会话活跃检测测试（纯逻辑，不起 Qt）。"""

from __future__ import annotations

import os
import time

import pytest

from codex_quota.fetcher import (
    ACTIVE_MS,
    BACKOFF_MAX_MS,
    BUSY_MS,
    HIDDEN_MS,
    RefreshScheduler,
    codex_session_active,
)
from tests.conftest import FakeProvider, codex_snapshot


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """重试间隔打桩为零——测行为，不测等待（fetcher 失败后会 sleep 3s 重试）。"""
    import codex_quota.fetcher as fetcher_mod

    monkeypatch.setattr(fetcher_mod.time, "sleep", lambda *_: None)


class TestScheduler:
    def test_visible_idle(self):
        s = RefreshScheduler()
        assert s.next_interval_ms(session_active=False) == ACTIVE_MS

    def test_visible_busy(self):
        s = RefreshScheduler()
        assert s.next_interval_ms(session_active=True) == BUSY_MS

    def test_hidden(self):
        s = RefreshScheduler()
        s.set_visible(False)
        assert s.next_interval_ms(session_active=True) == HIDDEN_MS

    def test_backoff_progression(self):
        s = RefreshScheduler()
        expected = [30_000, 60_000, 120_000, 240_000]
        for want in expected:
            s.on_failure()
            assert s.next_interval_ms(session_active=False) == want

    def test_backoff_capped(self):
        s = RefreshScheduler()
        for _ in range(20):
            s.on_failure()
        assert s.next_interval_ms(session_active=False) == BACKOFF_MAX_MS

    def test_backoff_overrides_visibility(self):
        s = RefreshScheduler()
        s.set_visible(False)
        s.on_failure()
        assert s.next_interval_ms(session_active=False) == 30_000

    def test_success_resets(self):
        s = RefreshScheduler()
        s.on_failure()
        s.on_failure()
        s.on_success()
        assert s.next_interval_ms(session_active=False) == ACTIVE_MS


class TestSessionActive:
    def test_no_sessions_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert codex_session_active() is False

    def test_fresh_session_file(self, tmp_path, monkeypatch):
        d = tmp_path / "sessions" / "2026" / "08"
        d.mkdir(parents=True)
        (d / "rollout.jsonl").write_text("{}")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert codex_session_active() is True

    def test_old_session_file(self, tmp_path, monkeypatch):
        d = tmp_path / "sessions"
        d.mkdir()
        f = d / "rollout.jsonl"
        f.write_text("{}")
        old = time.time() - 3600
        os.utime(f, (old, old))
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert codex_session_active() is False


class TestMultiProviderFetch:
    """QuotaFetcher 聚合：单 provider 失败不影响其他。"""

    def test_partial_failure(self, qapp):
        pytest.importorskip("PyQt6")
        from codex_quota.fetcher import QuotaFetcher

        providers = [
            FakeProvider("codex", "Codex", snapshot=codex_snapshot()),
            FakeProvider("kimi", "Kimi", error="kimi web 启动超时"),
        ]
        fetcher = QuotaFetcher(providers)
        ok, bad = [], []
        fetcher.succeeded.connect(lambda name, s: ok.append(name))
        fetcher.failed.connect(lambda name, e: bad.append((name, e)))
        fetcher.run()  # 直接同步调用 run，不起线程
        assert ok == ["codex"]
        assert bad == [("kimi", "kimi web 启动超时")]

    def test_all_fail_no_crash(self, qapp):
        pytest.importorskip("PyQt6")
        from codex_quota.fetcher import QuotaFetcher

        fetcher = QuotaFetcher([FakeProvider("codex", error="x")])
        bad = []
        fetcher.failed.connect(lambda name, e: bad.append(name))
        fetcher.run()
        assert bad == ["codex"]


class FlakyProvider(FakeProvider):
    """前 fail_times 次抛错，之后返回正常快照。"""

    def __init__(self, name="codex", fail_times=1):
        super().__init__(name, snapshot=codex_snapshot())
        self._fail_times = fail_times
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("flaky")
        return self._snapshot


class TestFetcherRetry:
    """同周期重试：首次失败隔 3s（已打桩）再试一次，之后才上报失败。"""

    def test_retry_recovers(self, qapp):
        pytest.importorskip("PyQt6")
        from codex_quota.fetcher import QuotaFetcher

        p = FlakyProvider(fail_times=1)
        fetcher = QuotaFetcher([p])
        ok, bad = [], []
        fetcher.succeeded.connect(lambda name, s: ok.append(name))
        fetcher.failed.connect(lambda name, e: bad.append(name))
        fetcher.run()
        assert ok == ["codex"] and bad == []
        assert p.calls == 2

    def test_retry_exhausted_fails_once(self, qapp):
        pytest.importorskip("PyQt6")
        from codex_quota.fetcher import QuotaFetcher

        p = FlakyProvider(fail_times=99)  # 永远失败
        fetcher = QuotaFetcher([p])
        ok, bad = [], []
        fetcher.succeeded.connect(lambda name, s: ok.append(name))
        fetcher.failed.connect(lambda name, e: bad.append(name))
        fetcher.run()
        assert ok == [] and bad == ["codex"]  # 两次都败，只上报一次
        assert p.calls == 2


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
