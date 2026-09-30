"""GLM（智谱）provider 测试。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codex_quota.providers.glm import (
    GLMError,
    GLMProvider,
    parse_glm_quota,
)

NOW = 1787000000.0

QUOTA_RESPONSE = {
    "code": 200,
    "msg": "操作成功",
    "data": {
        "limits": [
            {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 7.0,
             "nextResetTime": 1787000300000},   # +5 分钟 → 5 小时窗
            {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 33.0,
             "nextResetTime": 1787604800000},   # +7 天 → 周窗
        ],
        "level": "pro",
    },
    "success": True,
}


class TestParse:
    def test_real_shape(self):
        snap = parse_glm_quota(QUOTA_RESPONSE, now=NOW)
        assert snap.provider == "glm"
        assert snap.plan_type == "pro"
        # primary=本周，secondary=5小时（与 Kimi 分区一致）
        assert snap.primary_limit.primary.window_minutes == 10080
        assert snap.primary_limit.primary.used_percent == pytest.approx(33.0)
        assert snap.primary_limit.primary.remaining_percent == pytest.approx(67.0)
        assert snap.primary_limit.primary.reset_at == pytest.approx(1787604800.0)
        assert snap.primary_limit.secondary.window_minutes == 300
        assert snap.primary_limit.secondary.remaining_percent == pytest.approx(93.0)

    def test_unit_first_classification_not_reset_order(self):
        # 周期末尾：周窗 reset 比 5h 更早——必须仍按 unit 正确分类
        payload = {
            "data": {"level": "pro", "limits": [
                {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 10.0,
                 "nextResetTime": 1787000100000},  # 更早
                {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 20.0,
                 "nextResetTime": 1787604800000},  # 更晚
            ]},
            "code": 200, "success": True,
        }
        snap = parse_glm_quota(payload, now=NOW)
        assert snap.primary_limit.primary.window_minutes == 10080
        assert snap.primary_limit.secondary.window_minutes == 300

    def test_missing_unit_heuristic(self):
        # unit 缺失：无 reset 的归 5h，有 reset 的归 weekly
        payload = {
            "data": {"level": "pro", "limits": [
                {"type": "TOKENS_LIMIT", "percentage": 0.0},
                {"type": "TOKENS_LIMIT", "percentage": 40.0,
                 "nextResetTime": 1787604800000},
            ]},
            "code": 200, "success": True,
        }
        snap = parse_glm_quota(payload, now=NOW)
        assert snap.primary_limit.secondary.window_minutes == 300  # 无 reset → 5h
        assert snap.primary_limit.primary.window_minutes == 10080

    def test_single_old_plan_only_5h(self):
        payload = {
            "data": {"level": "pro", "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 5.0,
                 "nextResetTime": 1787000300000},
            ]},
            "code": 200, "success": True,
        }
        snap = parse_glm_quota(payload, now=NOW)
        assert snap.primary_limit.primary.window_minutes == 300
        assert snap.primary_limit.secondary is None

    def test_credit_limit_fallback(self):
        payload = {
            "data": {"level": "pro", "limits": [
                {"type": "CREDIT_LIMIT", "unit": 6, "percentage": 50.0,
                 "nextResetTime": 1787604800000},
            ]},
            "code": 200, "success": True,
        }
        snap = parse_glm_quota(payload, now=NOW)
        assert snap.primary_limit.primary.remaining_percent == pytest.approx(50.0)

    def test_time_limit_ignored(self):
        payload = {
            "data": {"level": "pro", "limits": [
                {"type": "TIME_LIMIT", "unit": 5, "percentage": 10.0,
                 "nextResetTime": 1789604800000},
            ]},
            "code": 200, "success": True,
        }
        with pytest.raises(GLMError, match="限额窗口"):
            parse_glm_quota(payload)

    def test_not_coding_plan_user(self):
        with pytest.raises(GLMError, match="coding plan"):
            parse_glm_quota({"code": 200, "msg": "当前用户不存在coding plan",
                             "success": False})

    def test_missing_data_raises(self):
        with pytest.raises(GLMError):
            parse_glm_quota({})


class TestFetch:
    @pytest.fixture
    def server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # 智谱端点用裸 key：带 Bearer 前缀的应判无效
                if self.headers.get("Authorization") != "sk-glm-good":
                    body = b'{"error": "unauthorized"}'
                    self.send_response(401)
                else:
                    body = json.dumps(QUOTA_RESPONSE).encode()
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
        p = GLMProvider(api_key="sk-glm-good", base_url=server)
        assert p.fetch().primary_limit.primary.remaining_percent == pytest.approx(67.0)

    def test_401_guidance(self, server):
        with pytest.raises(GLMError, match="无效"):
            GLMProvider(api_key="sk-bad", base_url=server).fetch()

    def test_no_key(self):
        with pytest.raises(GLMError, match="未配置"):
            GLMProvider(api_key=None).fetch()

    def test_team_plan_headers(self, server):
        seen: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen["path"] = self.path
                seen["org"] = self.headers.get("bigmodel-organization", "")
                seen["proj"] = self.headers.get("bigmodel-project", "")
                body = json.dumps(QUOTA_RESPONSE).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = GLMProvider(api_key="sk-glm-good",
                            base_url=f"http://127.0.0.1:{srv.server_port}",
                            organization="org-abc", project="proj-xyz")
            p.fetch()
        finally:
            srv.shutdown()
        assert seen["path"] == "/?type=2"
        assert seen["org"] == "org-abc"
        assert seen["proj"] == "proj-xyz"

    def test_assembly_from_config(self, tmp_path, monkeypatch):
        from codex_quota.providers import default_providers
        from codex_quota.providers.config import save_providers_config

        monkeypatch.delenv("CODEX_QUOTA_PROVIDERS", raising=False)
        path = str(tmp_path / "providers.toml")
        save_providers_config({
            "glm": {"type": "glm", "enabled": True, "api_key": "sk-x"}},
            path)
        names = [p.name for p in default_providers(config_path=path)]
        assert "glm" in names
