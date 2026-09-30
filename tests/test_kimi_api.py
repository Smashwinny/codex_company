"""Kimi API key provider 测试（云端 API 模式，无需本地 kimi CLI）。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.providers.kimi_api import (
    KimiApiError,
    KimiApiProvider,
    parse_kimi_usages,
)

NOW = 1787000000.0

USAGES_RESPONSE = {
    "usage": {"limit": "100", "used": "10", "remaining": "90",
              "resetTime": "2026-10-07T06:32:10.901710Z"},
    "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {"limit": "100", "used": "12", "remaining": "88",
                           "resetTime": "2026-09-30T19:32:10.901710Z"}}],
    "usages": {"limit_5h": {"used_ratio": 0.117452,
                            "reset_time": "2026-09-30T19:32:09Z"},
               "limit_7d": {"used_ratio": 0.102081,
                            "reset_time": "2026-10-07T06:32:10Z"}},
}


class TestParse:
    def test_real_shape_prefers_ratio(self):
        snap = parse_kimi_usages(USAGES_RESPONSE, now=NOW)
        assert snap.provider == "kimi"
        # primary=本周，secondary=5小时；优先 usages.used_ratio（比 limit/100 精确）
        assert snap.primary_limit.primary.window_minutes == 10080
        assert snap.primary_limit.primary.used_percent == pytest.approx(10.2081)
        assert snap.primary_limit.secondary.window_minutes == 300
        assert snap.primary_limit.secondary.used_percent == pytest.approx(11.7452)

    def test_numeric_string_buckets(self):
        # 无 usages 对象时回退 limit/usage 桶；字符串数值需容错
        payload = {
            "usage": {"limit": "1000", "used": "250", "remaining": "750",
                      "resetTime": 1787604800000},
            "limits": [{"detail": {"limit": "100", "used": "50",
                                   "remaining": "50", "resetTime": 1787000300}}],
        }
        snap = parse_kimi_usages(payload, now=NOW)
        assert snap.primary_limit.primary.used_percent == pytest.approx(25.0)
        assert snap.primary_limit.secondary.used_percent == pytest.approx(50.0)
        assert snap.primary_limit.primary.reset_at == pytest.approx(1787604800.0)

    def test_numeric_buckets(self):
        payload = {
            "usage": {"limit": 1000, "used": 250, "remaining": 750,
                      "resetTime": "2026-10-07T06:32:10Z"},
            "limits": [{"detail": {"limit": 100, "used": 12, "remaining": 88,
                                   "resetTime": "2026-09-30T19:32:10Z"}}],
        }
        snap = parse_kimi_usages(payload, now=NOW)
        assert snap.primary_limit.primary.used_percent == pytest.approx(25.0)
        assert snap.primary_limit.secondary.used_percent == pytest.approx(12.0)

    def test_no_windows_raises(self):
        with pytest.raises(KimiApiError, match="限额窗口"):
            parse_kimi_usages({"limits": [], "usage": {}})


class TestFetch:
    @pytest.fixture
    def server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer sk-kimi-good":
                    body = b'{"error": "unauthorized"}'
                    self.send_response(401)
                else:
                    body = json.dumps(USAGES_RESPONSE).encode()
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
        p = KimiApiProvider(api_key="sk-kimi-good", base_url=server)
        assert p.fetch().primary_limit.secondary.used_percent == pytest.approx(11.7452)

    def test_401_guidance(self, server):
        with pytest.raises(KimiApiError, match="无效"):
            KimiApiProvider(api_key="sk-bad", base_url=server).fetch()

    def test_no_key(self):
        with pytest.raises(KimiApiError, match="未配置"):
            KimiApiProvider(api_key=None).fetch()

    def test_api_key_mode_beats_local_cli(self, tmp_path, monkeypatch):
        """providers.toml 配了 api_key 时走云端 API，即使本地装了 kimi CLI。"""
        from codex_quota.providers import default_providers
        from codex_quota.providers.config import save_providers_config

        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        # find_kimi_bin 在 kimi 模块内定义，default_providers 局部导入后调用
        monkeypatch.setattr("codex_quota.providers.kimi.find_kimi_bin",
                            lambda: "/usr/bin/kimi")
        path = str(tmp_path / "providers.toml")
        save_providers_config({
            "kimi": {"enabled": True, "api_key": "sk-x"}}, path)
        provs = default_providers(config_path=path)
        kimi = [p for p in provs if p.name == "kimi"]
        assert len(kimi) == 1
        assert type(kimi[0]).__name__ == "KimiApiProvider"
