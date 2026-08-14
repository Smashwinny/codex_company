"""Web 服务测试：鉴权、页面、JSON API、payload 映射、端口管理。"""

from __future__ import annotations

import json
import urllib.request

import pytest

from codex_quota.state import ProviderView, ViewState
from codex_quota.web import WebServer, generate_token, lan_ip, views_to_payload
from tests.conftest import codex_snapshot


def make_views():
    views = [ProviderView("codex", "Codex", ViewState(snapshot=codex_snapshot())),
             ProviderView("kimi", "Kimi", ViewState(error="kimi web 无响应"))]
    return views


@pytest.fixture
def server():
    srv = WebServer(make_views, port=0, token="test-token")  # port=0 → OS 分配
    srv.start()
    yield srv
    srv.stop()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class TestAuth:
    def test_root_redirects(self, server):
        req = urllib.request.Request(server.url.replace(f"/t/{server.token}/", "/"))
        # urllib 默认跟随重定向，最终应落在带 token 的页面
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert f"/t/{server.token}" in r.url

    def test_no_token_404(self, server):
        code, _ = get(f"http://127.0.0.1:{server.port}/api/quotas")
        assert code == 404

    def test_wrong_token_404(self, server):
        code, _ = get(f"http://127.0.0.1:{server.port}/t/wrong-token/api/quotas")
        assert code == 404
        code, _ = get(f"http://127.0.0.1:{server.port}/t/wrong-token/")
        assert code == 404


class TestPage:
    def test_html_shell(self, server):
        code, body = get(server.url)
        assert code == 200
        assert "viewport" in body           # 移动端适配
        assert "额度监控" in body
        assert "api/quotas" in body         # 页面会轮询 JSON
        assert "setInterval(load, 30000)" in body  # 30s 自动刷新


class TestApi:
    def test_json_shape(self, server):
        code, body = get(server.url + "api/quotas")
        assert code == 200
        data = json.loads(body)
        assert "server_time" in data
        codex = data["providers"][0]
        assert codex["name"] == "codex"
        assert codex["plan"] == "prolite"
        assert codex["stale"] is False
        # 主桶 + Spark 桶
        assert len(codex["windows"]) == 2
        main, spark = codex["windows"]
        assert main["remaining"] == pytest.approx(9.0)
        assert main["label"] == "本周"
        assert main["bucket"] is None
        assert spark["bucket"] == "GPT-5.3-Codex-Spark"
        kimi = data["providers"][1]
        assert kimi["windows"] == []
        assert kimi["error"] == "kimi web 无响应"


class TestPayload:
    def test_empty_snapshot_fields(self):
        views = [ProviderView("codex", "Codex", ViewState())]
        payload = views_to_payload(views)
        p = payload["providers"][0]
        assert p["windows"] == []
        assert p["fetched_at"] is None
        assert p["stale"] is False


class TestHelpers:
    def test_generate_token_unique(self):
        assert generate_token() != generate_token()
        assert len(generate_token()) >= 16

    def test_lan_ip_returns_ip(self):
        ip = lan_ip()
        parts = ip.split(".")
        assert len(parts) == 4

    def test_url_format(self, server):
        assert server.url.startswith("http://")
        assert f"/t/{server.token}/" in server.url

    def test_port_zero_auto_assign(self, server):
        assert server.port is not None and server.port > 0
