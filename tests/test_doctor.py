"""doctor.py 环境自检测试。"""

from __future__ import annotations

import pytest

from codex_quota.app_server import CodexNotFoundError
from codex_quota import doctor
from codex_quota.doctor import FAIL, OK, WARN, has_failures, run_checks


def patch_finders(monkeypatch, codex=None, login=False, kimi=None, cloudflared=None):
    """codex: None=未安装, 否则视为可执行路径。"""
    if codex is None:
        def _raise():
            raise CodexNotFoundError("未找到")
        monkeypatch.setattr("codex_quota.app_server.find_codex_bin", _raise)
    else:
        monkeypatch.setattr("codex_quota.app_server.find_codex_bin", lambda: codex)
    monkeypatch.setattr("codex_quota.app_server.is_logged_in", lambda: login)
    monkeypatch.setattr("codex_quota.providers.kimi.find_kimi_bin", lambda: kimi)
    monkeypatch.setattr("codex_quota.tunnel.find_cloudflared", lambda: cloudflared)


def by_key(items, key):
    return next(i for i in items if i.key == key)


class TestRunChecks:
    def test_all_good(self, monkeypatch):
        patch_finders(monkeypatch, codex="/bin/codex", login=True,
                      kimi="/bin/kimi", cloudflared="/bin/cloudflared")
        items = run_checks(version_of=lambda p: "codex-cli 0.147.0")
        assert not has_failures(items)
        assert all(i.status == OK for i in items)
        assert "0.147.0" in by_key(items, "codex_bin").detail

    def test_codex_missing(self, monkeypatch):
        patch_finders(monkeypatch, codex=None)
        items = run_checks()
        item = by_key(items, "codex_bin")
        assert item.status == FAIL
        assert item.required is True
        assert "npm i -g @openai/codex" in item.fix_command
        assert has_failures(items)

    def test_codex_not_logged_in(self, monkeypatch):
        patch_finders(monkeypatch, codex="/bin/codex", login=False)
        items = run_checks()
        assert by_key(items, "codex_bin").status == OK
        login = by_key(items, "codex_login")
        assert login.status == FAIL
        assert login.fix_command == "codex login"

    def test_optional_missing_are_warn(self, monkeypatch):
        patch_finders(monkeypatch, codex="/bin/codex", login=True,
                      kimi=None, cloudflared=None)
        items = run_checks()
        assert by_key(items, "kimi_bin").status == WARN
        assert by_key(items, "cloudflared").status == WARN
        assert not has_failures(items)  # WARN 不算失败

    def test_version_failure_tolerated(self, monkeypatch):
        patch_finders(monkeypatch, codex="/bin/codex", login=True)
        items = run_checks(version_of=lambda p: None)
        assert by_key(items, "codex_bin").status == OK


def test_default_version_hides_console_on_windows(monkeypatch):
    captured = {}

    class Result:
        stdout = "codex-cli 1.2.3\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(doctor.sys, "platform", "win32")
    monkeypatch.setattr("codex_quota.proc.IS_WINDOWS", True)
    monkeypatch.setattr("codex_quota.proc.run_external", fake_run)

    assert doctor._default_version(r"C:\npm\codex.cmd") == "codex-cli 1.2.3"
    assert captured["argv"][:2] == ["cmd.exe", "/c"]
    assert captured["kwargs"]["creationflags"] & 0x08000000
