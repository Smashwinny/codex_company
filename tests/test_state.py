"""磁盘缓存与 StateStore 状态迁移测试。"""

from __future__ import annotations

import json

from codex_quota.app_server import parse_rate_limits_response
from codex_quota.state import CACHE_MAX_AGE_S, StateStore
from tests.test_parse import NOW, REAL_RESPONSE


def snap():
    return parse_rate_limits_response(REAL_RESPONSE, now=NOW)


def make_store(tmp_path):
    return StateStore(cache_path=str(tmp_path / "codex-quota" / "last-good.json"))


class TestDiskCache:
    def test_round_trip(self, tmp_path):
        store = make_store(tmp_path)
        store.on_success(snap())

        fresh = make_store(tmp_path)
        state = fresh.load_cached(now=NOW + 60)
        assert state.snapshot is not None
        assert state.stale is True
        assert state.snapshot.plan_type == "prolite"
        assert state.snapshot.primary_limit.primary.used_percent == 91
        assert len(state.snapshot.limits) == 2  # 附加桶也还原
        assert state.snapshot.limits[1].limit_name == "GPT-5.3-Codex-Spark"

    def test_expired_cache_ignored(self, tmp_path):
        store = make_store(tmp_path)
        store.on_success(snap())
        fresh = make_store(tmp_path)
        state = fresh.load_cached(now=NOW + CACHE_MAX_AGE_S + 1)
        assert state.snapshot is None

    def test_corrupt_file_ignored(self, tmp_path):
        store = make_store(tmp_path)
        path = tmp_path / "codex-quota" / "last-good.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert store.load_cached(now=NOW).snapshot is None

    def test_empty_limits_ignored(self, tmp_path):
        store = make_store(tmp_path)
        path = tmp_path / "codex-quota" / "last-good.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"fetched_at": NOW, "plan_type": None, "limits": []}))
        assert store.load_cached(now=NOW).snapshot is None

    def test_missing_file_ok(self, tmp_path):
        assert make_store(tmp_path).load_cached(now=NOW).snapshot is None

    def test_cache_file_is_valid_json(self, tmp_path):
        store = make_store(tmp_path)
        store.on_success(snap())
        data = json.loads((tmp_path / "codex-quota" / "last-good.json").read_text())
        assert data["plan_type"] == "prolite"
        assert data["limits"][0]["primary"]["used_percent"] == 91.0


class TestStateTransitions:
    def test_error_without_cache_is_pure_error(self, tmp_path):
        store = make_store(tmp_path)
        state = store.on_error("boom")
        assert state.snapshot is None
        assert state.stale is False
        assert state.error == "boom"

    def test_error_after_success_keeps_stale_snapshot(self, tmp_path):
        store = make_store(tmp_path)
        store.on_success(snap())
        state = store.on_error("boom")
        assert state.snapshot is not None
        assert state.stale is True
        assert state.error == "boom"

    def test_success_clears_error(self, tmp_path):
        store = make_store(tmp_path)
        store.on_error("boom")
        state = store.on_success(snap())
        assert state.stale is False
        assert state.error is None
