"""解析层测试。

fixture 来自 2026-08-12 在本机（codex-cli 0.147.0, prolite 套餐）对
`codex app-server` + account/rateLimits/read 的实测响应，仅修改了时间戳。
"""

from __future__ import annotations

import pytest

from codex_quota.app_server import (
    AppServerError,
    QuotaWindow,
    find_codex_bin,
    parse_rate_limits_response,
)

NOW = 1787000000.0  # 固定 "now"，便于断言倒计时

REAL_RESPONSE = {
    "rateLimits": {
        "limitId": "codex",
        "limitName": None,
        "primary": {
            "usedPercent": 91,
            "windowDurationMins": 10080,
            "resetsAt": NOW + 19003,
        },
        "secondary": None,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "individualLimit": None,
        "spendControlReached": False,
        "planType": "prolite",
        "rateLimitReachedType": None,
    },
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "limitName": None,
            "primary": {"usedPercent": 91, "windowDurationMins": 10080, "resetsAt": NOW + 19003},
            "secondary": None,
            "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
            "planType": "prolite",
        },
        "codex_bengalfox": {
            "limitId": "codex_bengalfox",
            "limitName": "GPT-5.3-Codex-Spark",
            "primary": {"usedPercent": 2, "windowDurationMins": 10080, "resetsAt": NOW + 23873},
            "secondary": None,
            "credits": None,
            "planType": "prolite",
        },
    },
}


class TestParseRealResponse:
    def setup_method(self):
        self.snap = parse_rate_limits_response(REAL_RESPONSE, now=NOW)

    def test_plan_type(self):
        assert self.snap.plan_type == "prolite"
        assert self.snap.fetched_at == NOW

    def test_main_limit(self):
        main = self.snap.primary_limit
        assert main is not None
        assert main.limit_id == "codex"
        assert main.primary.used_percent == 91
        assert main.primary.remaining_percent == pytest.approx(9.0)
        assert main.primary.window_minutes == 10080
        assert main.primary.label == "本周"
        assert main.primary.reset_in_seconds(NOW) == pytest.approx(19003)
        assert main.secondary is None

    def test_by_limit_id_echo_is_skipped(self):
        # rateLimitsByLimitId 中与主 limitId 相同的回声条目不重复出现
        assert len(self.snap.limits) == 2
        ids = [l.limit_id for l in self.snap.limits]
        assert ids == ["codex", "codex_bengalfox"]

    def test_additional_spark_bucket(self):
        spark = self.snap.limits[1]
        assert spark.limit_name == "GPT-5.3-Codex-Spark"
        assert spark.primary.used_percent == 2
        assert spark.credits is None


class TestWindowClassification:
    @pytest.mark.parametrize(
        "minutes,label",
        [(None, "窗口"), (5, "5小时"), (300, "5小时"), (360, "5小时"),
         (1440, "24小时"), (5000, "本周"), (10080, "本周")],
    )
    def test_labels(self, minutes, label):
        assert QuotaWindow(window_minutes=minutes).label == label


class TestRemainingPercent:
    def test_normal(self):
        assert QuotaWindow(used_percent=91).remaining_percent == pytest.approx(9.0)

    def test_zero_used_means_full_remaining(self):
        assert QuotaWindow(used_percent=0).remaining_percent == 100.0

    def test_unknown_used_means_unknown_remaining(self):
        assert QuotaWindow(used_percent=None).remaining_percent is None

    def test_over_100_used_clamps_to_zero(self):
        assert QuotaWindow(used_percent=105).remaining_percent == 0.0


class TestEdgeCases:
    def test_zero_percent_is_preserved(self):
        resp = {"rateLimits": {"limitId": "codex", "planType": "pro",
                               "primary": {"usedPercent": 0, "windowDurationMins": 300,
                                           "resetsAt": NOW + 60}}}
        snap = parse_rate_limits_response(resp, now=NOW)
        w = snap.primary_limit.primary
        assert w.used_percent == 0.0  # 不是 None
        assert w.label == "5小时"

    def test_missing_fields_become_none(self):
        snap = parse_rate_limits_response(
            {"rateLimits": {"limitId": "codex", "primary": {}}}, now=NOW)
        w = snap.primary_limit.primary
        assert w.used_percent is None
        assert w.remaining_percent is None
        assert w.window_minutes is None
        assert w.reset_at is None
        assert w.reset_in_seconds(NOW) is None

    def test_non_numeric_reset_at_tolerated(self):
        snap = parse_rate_limits_response(
            {"rateLimits": {"limitId": "codex",
                            "primary": {"usedPercent": 5, "resetsAt": "soon"}}}, now=NOW)
        assert snap.primary_limit.primary.reset_at is None

    def test_missing_rate_limits_raises(self):
        with pytest.raises(AppServerError, match="rateLimits"):
            parse_rate_limits_response({}, now=NOW)

    def test_no_by_limit_id(self):
        resp = {"rateLimits": {"limitId": "codex", "planType": "free",
                               "primary": {"usedPercent": 10, "windowDurationMins": 300,
                                           "resetsAt": NOW}}}
        snap = parse_rate_limits_response(resp, now=NOW)
        assert len(snap.limits) == 1


class TestFindCodexBin:
    def test_nvm_fallback(self, tmp_path, monkeypatch):
        """PATH 没有 codex 时，回退搜索 ~/.nvm/versions/node/*/bin/codex。"""
        monkeypatch.delenv("CODEX_BIN", raising=False)
        monkeypatch.setattr("shutil.which", lambda _: None)
        fake_home = tmp_path / "home"
        bin_dir = fake_home / ".nvm" / "versions" / "node" / "v20.20.2" / "bin"
        bin_dir.mkdir(parents=True)
        codex = bin_dir / "codex"
        codex.write_text("#!/bin/sh\n")
        codex.chmod(0o755)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fake_home) + p[1:])
        assert find_codex_bin() == str(codex)

    def test_nvm_fallback_prefers_newer_version(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CODEX_BIN", raising=False)
        monkeypatch.setattr("shutil.which", lambda _: None)
        fake_home = tmp_path / "home"
        for ver in ("v20.20.2", "v24.19.0"):
            d = fake_home / ".nvm" / "versions" / "node" / ver / "bin"
            d.mkdir(parents=True)
            (d / "codex").write_text("#!/bin/sh\n")
            (d / "codex").chmod(0o755)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fake_home) + p[1:])
        assert "v24.19.0" in find_codex_bin()

    def test_not_found_anywhere(self, tmp_path, monkeypatch):
        from codex_quota.app_server import CodexNotFoundError

        monkeypatch.delenv("CODEX_BIN", raising=False)
        monkeypatch.setattr("shutil.which", lambda _: None)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) + p[1:])
        with pytest.raises(CodexNotFoundError):
            find_codex_bin()
