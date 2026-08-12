"""Kimi provider 测试。

fixture 来自 2026-08-12 本机（kimi-code 0.35.0）对
`kimi web` + GET /api/v1/oauth/usage 的实测响应。
"""

from __future__ import annotations

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.providers.kimi import (
    KimiError,
    KimiProvider,
    find_kimi_bin,
    parse_kimi_usage,
)

NOW = 1786500000.0

KIMI_RESPONSE = {
    "code": 0,
    "msg": "success",
    "data": {
        "kind": "ok",
        "summary": {
            "window": {"duration": 1, "unit": "week"},
            "used": 13,
            "limit": 100,
            "reset_at": "2026-08-19T06:32:10.901710Z",
        },
        "limits": [
            {
                "window": {"duration": 5, "unit": "hour"},
                "used": 67,
                "limit": 100,
                "reset_at": "2026-08-12T15:32:10.901710Z",
            }
        ],
        "extra_usage": None,
    },
    "request_id": "01KZV7002K59TXYXK7PVC6G69J",
}


class TestParseKimiUsage:
    def setup_method(self):
        self.snap = parse_kimi_usage(KIMI_RESPONSE, model="kimi-code/k3", now=NOW)

    def test_provider_and_model(self):
        assert self.snap.provider == "kimi"
        assert self.snap.plan_type == "kimi-code/k3"
        assert self.snap.fetched_at == NOW

    def test_weekly_primary(self):
        w = self.snap.primary_limit.primary
        assert w.used_percent == pytest.approx(13.0)
        assert w.remaining_percent == pytest.approx(87.0)
        assert w.window_minutes == 10080
        assert w.label == "本周"
        # 2026-08-19T06:32:10.901710Z
        assert w.reset_at == pytest.approx(1787121130.901710, abs=1)

    def test_hourly_secondary(self):
        w = self.snap.primary_limit.secondary
        assert w is not None
        assert w.used_percent == pytest.approx(67.0)
        assert w.window_minutes == 300
        assert w.label == "5小时"

    def test_kind_not_ok_raises(self):
        with pytest.raises(KimiError):
            parse_kimi_usage({"data": {"kind": "error"}}, now=NOW)

    def test_missing_data_raises(self):
        with pytest.raises(KimiError):
            parse_kimi_usage({}, now=NOW)

    def test_bad_reset_tolerated(self):
        bad = json.loads(json.dumps(KIMI_RESPONSE))
        bad["data"]["summary"]["reset_at"] = "soon"
        snap = parse_kimi_usage(bad, now=NOW)
        assert snap.primary_limit.primary.reset_at is None

    def test_no_limits_secondary_none(self):
        payload = {"data": {"kind": "ok",
                            "summary": {"window": {"duration": 1, "unit": "week"},
                                        "used": 1, "limit": 100, "reset_at": None},
                            "limits": []}}
        snap = parse_kimi_usage(payload, now=NOW)
        assert snap.primary_limit.secondary is None


class TestFindKimiBin:
    def test_env_override(self, tmp_path, monkeypatch):
        fake = tmp_path / "kimi"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("KIMI_BIN", str(fake))
        assert find_kimi_bin() == str(fake)

    def test_env_override_bad_path_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KIMI_BIN", str(tmp_path / "nonexistent"))
        monkeypatch.setattr("shutil.which", lambda _: None)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "nope"))
        assert find_kimi_bin() is None


class TestKimiHttpFetch:
    """用本地 http.server 模拟 kimi web，验证 fetch 全流程（含 auth 模型名）。"""

    @pytest.fixture
    def server(self):
        token = "test-token"

        class Handler(BaseHTTPRequestHandler):
            def _send(self, payload, code=200):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("Authorization") != f"Bearer {token}":
                    self._send({"error": "unauthorized"}, 401)
                    return
                if self.path == "/api/v1/oauth/usage":
                    self._send(KIMI_RESPONSE)
                elif self.path == "/api/v1/auth":
                    self._send({"code": 0, "data": {"default_model": "kimi-code/k3"}})
                else:
                    self._send({"error": "not found"}, 404)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_port}", token
        srv.shutdown()

    def test_fetch_happy_path(self, server):
        base_url, token = server
        p = KimiProvider(base_url=base_url, token=token)
        snap = p.fetch()
        assert snap.provider == "kimi"
        assert snap.plan_type == "kimi-code/k3"
        assert snap.primary_limit.primary.used_percent == pytest.approx(13.0)
        p.close()  # 注入模式无进程，应无操作

    def test_fetch_auth_failure_tolerated(self, server):
        base_url, token = server

        # auth 之外的端点正常；把 auth 搞坏 → plan_type 为 None 但快照正常
        class Provider(KimiProvider):
            def _get_json(self, path):
                if path == "/api/v1/auth":
                    raise OSError("boom")
                return super()._get_json(path)

        snap = Provider(base_url=base_url, token=token).fetch()
        assert snap.plan_type is None
        assert snap.primary_limit.primary.used_percent == pytest.approx(13.0)


class TestKimiProcessLifecycle:
    @pytest.fixture
    def fake_kimi(self, tmp_path):
        """假 kimi：打印 Local/Token 行后睡眠（模拟 kimi web 常驻）。"""
        script = tmp_path / "kimi"
        script.write_text(
            "#!/bin/sh\n"
            'echo "  Local:    http://127.0.0.1:9999/#token=fake-token-123"\n'
            'echo "  Token:    fake-token-123"\n'
            "sleep 300\n"
        )
        script.chmod(0o755)
        return str(script)

    def test_token_parsed_and_close_kills(self, fake_kimi):
        p = KimiProvider(kimi_bin=fake_kimi, startup_timeout=10)
        p._ensure_server()
        assert p._token == "fake-token-123"
        assert p._base_url is not None and p._base_url.startswith("http://127.0.0.1:")
        proc = p._proc
        assert proc.poll() is None  # 保活中
        p.close()
        assert proc.poll() is not None  # 已被杀死

    def test_ensure_server_idempotent(self, fake_kimi):
        p = KimiProvider(kimi_bin=fake_kimi, startup_timeout=10)
        p._ensure_server()
        proc = p._proc
        p._ensure_server()  # 不重复拉起
        assert p._proc is proc
        p.close()

    def test_no_token_raises(self, tmp_path):
        script = tmp_path / "kimi"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(0o755)
        p = KimiProvider(kimi_bin=str(script), startup_timeout=3)
        with pytest.raises(KimiError, match="Token"):
            p._ensure_server()

    def test_missing_binary(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KIMI_BIN", str(tmp_path / "nonexistent"))
        monkeypatch.setattr("shutil.which", lambda _: None)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "nope"))
        p = KimiProvider()
        with pytest.raises(KimiError, match="kimi"):
            p._ensure_server()
