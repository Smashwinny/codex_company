"""Claude Code provider 测试。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.providers.claude_code import (
    ClaudeCodeError,
    ClaudeCodeProvider,
    credentials_path,
    parse_usage,
)

NOW = 1787000000.0

USAGE_RESPONSE = {
    "five_hour": {"utilization": 0.35, "resets_at": "2026-08-17T18:00:00.000000Z"},
    "seven_day": {"utilization": 62, "resets_at": "2026-08-19T14:32:00.000000Z"},
}


def write_credentials(tmp_path, monkeypatch, token="test-token"):
    home = tmp_path / "claude-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": token, "expiresAt": 9999999999999}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return str(home / ".credentials.json")


class TestCredentialsPath:
    def test_found(self, tmp_path, monkeypatch):
        path = write_credentials(tmp_path, monkeypatch)
        assert credentials_path() == path

    def test_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
        assert credentials_path() is None


class TestParseUsage:
    def test_real_shape(self):
        snap = parse_usage(USAGE_RESPONSE, now=NOW)
        assert snap.provider == "claude"
        main = snap.primary_limit
        # five_hour 0.35 分数 → 35%
        assert main.primary.used_percent == pytest.approx(35.0)
        assert main.primary.window_minutes == 300
        assert main.primary.label == "5小时"
        # seven_day 62 直接百分比
        assert main.secondary.used_percent == pytest.approx(62.0)
        assert main.secondary.window_minutes == 10080
        assert main.secondary.label == "本周"

    def test_empty_raises(self):
        with pytest.raises(ClaudeCodeError):
            parse_usage({})

    def test_partial_ok(self):
        snap = parse_usage({"five_hour": {"utilization": 0.5}})
        assert snap.primary_limit.primary.used_percent == 50.0
        assert snap.primary_limit.secondary is None


class TestFetch:
    @pytest.fixture
    def server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if (self.headers.get("Authorization") != "Bearer test-token"
                        or self.headers.get("anthropic-beta") != "oauth-2025-04-20"):
                    body = b'{"error": "unauthorized"}'
                    self.send_response(401)
                else:
                    body = json.dumps(USAGE_RESPONSE).encode()
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_port}"
        srv.shutdown()

    def test_happy_path(self, server, tmp_path, monkeypatch):
        creds = write_credentials(tmp_path, monkeypatch)
        p = ClaudeCodeProvider(credentials=creds, base_url=server)
        snap = p.fetch()
        assert snap.primary_limit.primary.used_percent == pytest.approx(35.0)

    def test_401_expired_guidance(self, server, tmp_path, monkeypatch):
        creds = write_credentials(tmp_path, monkeypatch, token="bad-token")
        p = ClaudeCodeProvider(credentials=creds, base_url=server)
        with pytest.raises(ClaudeCodeError, match="过期"):
            p.fetch()

    def test_no_credentials_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
        with pytest.raises(ClaudeCodeError, match="未找到"):
            ClaudeCodeProvider().fetch()

    def test_credentials_missing_token(self, tmp_path):
        bad = tmp_path / ".credentials.json"
        bad.write_text("{}")
        with pytest.raises(ClaudeCodeError, match="accessToken"):
            ClaudeCodeProvider(credentials=str(bad)).fetch()

    def test_assembly_with_credentials(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers

        write_credentials(tmp_path, monkeypatch)
        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        names = [p.name for p in default_providers(
            config_path=str(tmp_path / "none.toml"))]
        assert "claude" in names

    def test_assembly_disabled(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers
        from codex_quota.providers.config import save_providers_config

        write_credentials(tmp_path, monkeypatch)
        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        path = str(tmp_path / "providers.toml")
        save_providers_config({"claude": {"enabled": False}}, path)
        names = [p.name for p in default_providers(config_path=path)]
        assert "claude" not in names
