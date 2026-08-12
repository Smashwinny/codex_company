"""RefreshScheduler 与会话活跃检测测试（纯逻辑，不起 Qt）。"""

from __future__ import annotations

import os
import time

from codex_quota.fetcher import (
    ACTIVE_MS,
    BACKOFF_MAX_MS,
    BUSY_MS,
    HIDDEN_MS,
    RefreshScheduler,
    codex_session_active,
)


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
