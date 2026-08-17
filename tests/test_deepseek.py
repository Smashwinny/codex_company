"""DeepSeek provider 与余额型数据模型测试。

响应 fixture 形状来自 DeepSeek 官方文档（GET /user/balance）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.app_server import QuotaWindow, snapshot_from_dict, snapshot_to_dict
from codex_quota.providers.deepseek import (
    DeepSeekError,
    DeepSeekProvider,
    parse_balance,
)

NOW = 1787000000.0

BALANCE_RESPONSE = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "100.00",
         "granted_balance": "50.00", "topped_up_balance": "50.00"}
    ],
}


class TestParseBalance:
    def test_real_shape(self):
        snap = parse_balance(BALANCE_RESPONSE, now=NOW)
        assert snap.provider == "deepseek"
        w = snap.primary_limit.primary
        assert w.is_balance is True
        assert w.abs_remaining == 100.0
        assert w.abs_unit == "CNY"
        assert w.abs_text == "¥100.00"
        assert w.label == "余额"
        assert w.abs_level == "ok"
        assert w.remaining_percent is None  # 余额型无百分比

    def test_low_balance_levels(self):
        # CNY 阈值：≤20 黄，≤5 红
        for total, level in [("20.00", "warn"), ("19.99", "warn"),
                             ("20.01", "ok"), ("5.00", "crit"), ("100", "ok")]:
            payload = {"balance_infos": [
                {"currency": "CNY", "total_balance": total}]}
            w = parse_balance(payload).primary_limit.primary
            assert w.abs_level == level, total

    def test_usd_symbol(self):
        payload = {"balance_infos": [{"currency": "USD", "total_balance": "10"}]}
        w = parse_balance(payload).primary_limit.primary
        assert w.abs_text == "$10.00"

    def test_missing_infos_raises(self):
        with pytest.raises(DeepSeekError):
            parse_balance({})

    def test_bad_total_becomes_zero(self):
        payload = {"balance_infos": [{"currency": "CNY", "total_balance": "abc"}]}
        assert parse_balance(payload).primary_limit.primary.abs_remaining == 0.0


class TestAbsWindowModel:
    def test_abs_text_unknown_unit(self):
        w = QuotaWindow(abs_remaining=100.0, abs_unit="credits")
        assert w.abs_text == "100.00 credits"

    def test_not_balance_when_percent_present(self):
        w = QuotaWindow(used_percent=50, abs_remaining=10.0, abs_unit="CNY")
        assert w.is_balance is False  # 有百分比就按窗口型

    def test_serialization_round_trip(self):
        snap = parse_balance(BALANCE_RESPONSE, now=NOW)
        snap2 = snapshot_from_dict(snapshot_to_dict(snap))
        w = snap2.primary_limit.primary
        assert w.abs_remaining == 100.0
        assert w.abs_unit == "CNY"
        assert snap2.provider == "deepseek"


class TestFetch:
    @pytest.fixture
    def server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer sk-good":
                    body = b'{"error": "unauthorized"}'
                    self.send_response(401)
                else:
                    body = json.dumps(BALANCE_RESPONSE).encode()
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
        p = DeepSeekProvider(api_key="sk-good", base_url=server)
        snap = p.fetch()
        assert snap.primary_limit.primary.abs_remaining == 100.0

    def test_env_var_key(self, server, monkeypatch):
        monkeypatch.setenv("MY_DS_KEY", "sk-good")
        p = DeepSeekProvider(api_key="$MY_DS_KEY", base_url=server)
        assert p.fetch().provider == "deepseek"

    def test_401_guidance(self, server):
        p = DeepSeekProvider(api_key="sk-bad", base_url=server)
        with pytest.raises(DeepSeekError, match="无效"):
            p.fetch()

    def test_no_key(self):
        with pytest.raises(DeepSeekError, match="未配置"):
            DeepSeekProvider(api_key=None).fetch()

    def test_conn_refused(self):
        p = DeepSeekProvider(api_key="sk-x", base_url="http://127.0.0.1:1", timeout=1)
        with pytest.raises(DeepSeekError, match="无法连接"):
            p.fetch()


class TestAssembly:
    def test_default_providers_with_config(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers

        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        monkeypatch.setattr("codex_quota.providers.kimi.find_kimi_bin",
                            lambda: "/bin/kimi")
        path = str(tmp_path / "providers.toml")
        from codex_quota.providers.config import save_providers_config

        save_providers_config({
            "kimi": {"enabled": False},
            "deepseek": {"type": "deepseek", "enabled": True, "api_key": "sk-x"},
        }, path)
        names = [p.name for p in default_providers(config_path=path)]
        assert "kimi" not in names
        assert "codex" in names
        assert "deepseek" in names

    def test_env_filter_still_applies(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers

        monkeypatch.setenv("CODEX_QUOTA_PROVIDERS", "deepseek")
        path = str(tmp_path / "providers.toml")
        from codex_quota.providers.config import save_providers_config

        save_providers_config({
            "deepseek": {"type": "deepseek", "enabled": True, "api_key": "sk-x"}},
            path)
        names = [p.name for p in default_providers(config_path=path)]
        assert names == ["deepseek"]
