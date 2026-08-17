"""OpenRouter provider 测试。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.providers.openrouter import (
    OpenRouterError,
    OpenRouterProvider,
    parse_credits,
)

NOW = 1787000000.0

CREDITS_RESPONSE = {"data": {"total_credits": 10.0, "total_usage": 4.0}}


class TestParseCredits:
    def test_real_shape(self):
        snap = parse_credits(CREDITS_RESPONSE, now=NOW)
        assert snap.provider == "openrouter"
        w = snap.primary_limit.primary
        assert w.used_percent == pytest.approx(40.0)
        assert w.remaining_percent == pytest.approx(60.0)
        assert w.abs_remaining == pytest.approx(6.0)
        assert w.abs_unit == "USD"
        assert w.abs_text == "$6.00"
        assert w.is_balance is False  # 有百分比，按窗口型展示

    def test_zero_total(self):
        snap = parse_credits({"data": {"total_credits": 0, "total_usage": 0}})
        w = snap.primary_limit.primary
        assert w.used_percent is None
        assert w.abs_remaining == 0.0

    def test_overuse_clamps_remaining(self):
        snap = parse_credits({"data": {"total_credits": 5, "total_usage": 8}})
        assert snap.primary_limit.primary.abs_remaining == 0.0

    def test_missing_data_raises(self):
        with pytest.raises(OpenRouterError):
            parse_credits({})


class TestFetch:
    @pytest.fixture
    def server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer sk-or-good":
                    body = b'{"error": "unauthorized"}'
                    self.send_response(401)
                else:
                    body = json.dumps(CREDITS_RESPONSE).encode()
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

    def test_happy_path(self, server):
        p = OpenRouterProvider(api_key="sk-or-good", base_url=server)
        assert p.fetch().primary_limit.primary.remaining_percent == pytest.approx(60.0)

    def test_401_guidance(self, server):
        with pytest.raises(OpenRouterError, match="无效"):
            OpenRouterProvider(api_key="sk-bad", base_url=server).fetch()

    def test_no_key(self):
        with pytest.raises(OpenRouterError, match="未配置"):
            OpenRouterProvider(api_key=None).fetch()

    def test_assembly_from_config(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers
        from codex_quota.providers.config import save_providers_config

        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        path = str(tmp_path / "providers.toml")
        save_providers_config({
            "openrouter": {"type": "openrouter", "enabled": True, "api_key": "sk-x"}},
            path)
        names = [p.name for p in default_providers(config_path=path)]
        assert "openrouter" in names
