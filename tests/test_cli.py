"""CLI 渲染层测试（不触网、不 spawn 进程）。"""

from __future__ import annotations

import json

from codex_quota.app_server import parse_rate_limits_response
from codex_quota.cli import (
    _bar,
    _color_flag,
    _fmt_countdown,
    error_hint,
    render_text,
    run_cli,
    snapshot_to_dict,
)
from tests.test_parse import NOW, REAL_RESPONSE


def snap():
    return parse_rate_limits_response(REAL_RESPONSE, now=NOW)


class TestRenderText:
    def test_full_output(self):
        text = render_text(snap())
        assert "prolite" in text
        assert "本周" in text
        assert "剩 9%" in text          # 已用 91% → 剩余 9%
        assert "剩 98%" in text        # Spark 已用 2% → 剩余 98%
        assert "GPT-5.3-Codex-Spark" in text
        assert "更新于" in text

    def test_empty_limits(self):
        empty = parse_rate_limits_response(
            {"rateLimits": {"limitId": "codex", "primary": {}}}, now=NOW)
        empty.limits.clear()
        assert render_text(empty) == "Codex：无数据"


class TestHelpers:
    def test_bar(self):
        # 填充部分表示剩余额度
        assert _bar(0) == "░" * 20
        assert _bar(100) == "█" * 20
        assert _bar(50) == "█" * 10 + "░" * 10
        assert _bar(None) == "?" * 20
        assert _bar(150) == "█" * 20  # 超界截断

    def test_color_thresholds(self):
        # 按剩余量：绿 >30 / 黄 ≤30 / 红 ≤10
        assert _color_flag(100) == "🟢"
        assert _color_flag(31) == "🟢"
        assert _color_flag(30) == "🟡"
        assert _color_flag(11) == "🟡"
        assert _color_flag(10) == "🔴"
        assert _color_flag(0) == "🔴"
        assert _color_flag(None) == "?"

    def test_countdown(self):
        assert _fmt_countdown(None) == "重置时间未知"
        assert _fmt_countdown(-1) == "即将重置"
        assert _fmt_countdown(600) == "10 分后重置"
        assert _fmt_countdown(3600 + 120) == "1 小时 2 分后重置"
        assert _fmt_countdown(86400 + 7200) == "1 天 2 小时后重置"

    def test_json_serializable(self):
        d = snapshot_to_dict(snap())
        payload = json.dumps(d, ensure_ascii=False)
        assert '"plan_type": "prolite"' in payload


class TestErrorHint:
    def test_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        hint = error_hint("未找到 codex 可执行文件。…")
        assert "安装 Codex CLI" in hint

    def test_not_logged_in(self, tmp_path, monkeypatch):
        # CODEX_HOME 下没有 auth.json → 引导登录
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        hint = error_hint("app-server 响应超时（8 秒）")
        assert "codex login" in hint

    def test_timeout_when_logged_in(self, tmp_path, monkeypatch):
        (tmp_path / "auth.json").write_text("{}")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        hint = error_hint("app-server 响应超时（8 秒）")
        assert "稍后" in hint

    def test_generic_error_no_hint(self, tmp_path, monkeypatch):
        (tmp_path / "auth.json").write_text("{}")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        assert error_hint("jsonrpc-error: weird") is None


class TestRunCliErrors:
    def test_codex_not_found(self, monkeypatch, capsys):
        monkeypatch.setenv("CODEX_BIN", "/nonexistent/codex")
        code = run_cli([])
        assert code == 2
        assert "CODEX_BIN" in capsys.readouterr().err
