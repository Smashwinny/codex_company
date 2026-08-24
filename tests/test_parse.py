"""解析层测试。

fixture 来自 2026-08-12 在本机（codex-cli 0.147.0, prolite 套餐）对
`codex app-server` + account/rateLimits/read 的实测响应，仅修改了时间戳。
"""

from __future__ import annotations

import pytest
import sys

import codex_quota.app_server as app_server
from codex_quota.app_server import (
    AppServerClient,
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


class TestWindowsAppServerSpawn:
    def test_create_no_window_flag(self, monkeypatch):
        captured = {}

        class FakeProc:
            stdout = ()

            def poll(self):
                return 0

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProc()

        monkeypatch.setattr(app_server.sys, "platform", "win32")
        monkeypatch.setattr(app_server.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(AppServerClient, "_send", staticmethod(lambda *_: None))
        monkeypatch.setattr(
            AppServerClient, "_await_response",
            lambda *_args, **_kwargs: {
                "result": {"rateLimits": {"limitId": "codex", "primary": {}}}
            })

        AppServerClient(codex_bin=r"C:\npm\codex.cmd").read_rate_limits()

        flags = captured["kwargs"]["creationflags"]
        assert flags & getattr(app_server.subprocess, "CREATE_NO_WINDOW", 0x08000000)

    def test_reap_kills_cmd_process_tree(self, monkeypatch):
        from codex_quota import proc as proc_utils

        called = []

        class FakeProc:
            def poll(self):
                return None

        monkeypatch.setattr(app_server.sys, "platform", "win32")
        monkeypatch.setattr(
            proc_utils, "kill_tree",
            lambda process, timeout=3: called.append((process, timeout)),
        )
        process = FakeProc()
        AppServerClient._reap(process)
        assert called == [(process, 1)]


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程组/执行位语义，Windows 分支由 test_proc 等注入式测试覆盖")
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


@pytest.mark.skipif(sys.platform == "win32", reason="在 POSIX 上模拟 win32 分支逻辑")
class TestWindowsCodexDiscovery:
    """Windows 下 codex 安装位置兜底：npm 自定义 prefix / 默认 npm 目录 / 独立安装包。"""

    @pytest.fixture(autouse=True)
    def _win_env(self, monkeypatch, tmp_path):
        from codex_quota import app_server

        monkeypatch.setattr(app_server.sys, "platform", "win32")
        monkeypatch.delenv("CODEX_BIN", raising=False)
        monkeypatch.setattr(app_server.shutil, "which", lambda _name: None)
        monkeypatch.setattr(app_server, "_npm_prefix_cache",
                            app_server._NPM_PREFIX_UNSET)
        return tmp_path

    def test_appdata_npm_default(self, monkeypatch, tmp_path):
        from codex_quota.app_server import find_codex_bin

        monkeypatch.setenv("APPDATA", str(tmp_path))
        npm_dir = tmp_path / "npm"
        npm_dir.mkdir()
        (npm_dir / "codex.cmd").write_text("@echo off\n")
        assert find_codex_bin() == str(npm_dir / "codex.cmd")

    def test_npm_custom_prefix(self, monkeypatch, tmp_path):
        import subprocess as sp

        from codex_quota import app_server, proc
        from codex_quota.app_server import find_codex_bin

        prefix = tmp_path / "custom-prefix"
        prefix.mkdir()
        (prefix / "codex.cmd").write_text("@echo off\n")
        calls = []

        def fake_run_external(argv, **kw):
            calls.append(argv)
            return sp.CompletedProcess(argv, 0, stdout=str(prefix) + "\n")

        # npm 在 PATH（供 prefix 查询），codex 不在
        monkeypatch.setattr(app_server.shutil, "which",
                            lambda name: "/usr/bin/npm" if name == "npm" else None)
        monkeypatch.setattr(proc, "run_external", fake_run_external)
        monkeypatch.setenv("APPDATA", str(tmp_path / "empty"))
        assert find_codex_bin() == str(prefix / "codex.cmd")
        # prefix 查询结果被缓存，第二次兜底不再跑 npm
        assert len(calls) == 1
        find_codex_bin()
        assert len(calls) == 1

    def test_programs_codex_exe(self, monkeypatch, tmp_path):
        from codex_quota.app_server import find_codex_bin

        monkeypatch.setenv("APPDATA", str(tmp_path / "empty-roam"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        exe = tmp_path / "Programs" / "codex" / "codex.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("MZ")
        assert find_codex_bin() == str(exe)

    def test_npm_query_failure_tolerated(self, monkeypatch, tmp_path):
        from codex_quota import app_server, proc
        from codex_quota.app_server import CodexNotFoundError, find_codex_bin

        monkeypatch.setattr(app_server.shutil, "which",
                            lambda name: "/usr/bin/npm" if name == "npm" else None)

        def boom(*_a, **_kw):
            raise OSError("npm 起不来")

        monkeypatch.setattr(proc, "run_external", boom)
        monkeypatch.setenv("APPDATA", str(tmp_path / "empty"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty2"))
        with pytest.raises(CodexNotFoundError):
            find_codex_bin()
