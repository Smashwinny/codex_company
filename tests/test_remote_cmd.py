"""手机远程命令（remote_cmd.handle_command）测试。无 Qt 依赖，可无头运行。"""

from __future__ import annotations

from types import SimpleNamespace

from codex_quota.app_server import QuotaWindow
from codex_quota.remote_cmd import handle_command
from codex_quota.settings import Settings
from codex_quota.state import key_excluded, window_keys
from tests.conftest import codex_snapshot


def _views():
    """codex：主限额（本周 + 5小时）+ Spark 桶（本周）。"""
    snap = codex_snapshot()
    main = snap.primary_limit
    main.secondary = QuotaWindow(used_percent=50, window_minutes=300)
    return [SimpleNamespace(name="codex", display_name="Codex",
                            state=SimpleNamespace(snapshot=snap))]


def _settings():
    return Settings()  # conftest 已隔离 XDG 到临时目录


def test_url_command_returns_click():
    body, click = handle_command("url", _views(), _settings(),
                                 url="https://x.trycloudflare.com/t/abc/")
    assert "https://x.trycloudflare.com" in body
    assert click == "https://x.trycloudflare.com/t/abc/"


def test_url_command_without_web():
    body, click = handle_command("地址", _views(), _settings(), url=None)
    assert "未开启" in body and click == ""


def test_list_command_shows_states():
    s = _settings()
    body, _ = handle_command("列表", _views(), s)
    # 主限额两窗 + Spark 一窗，默认全开（图例行以 🔔 开头，排除表头图例）
    assert sum(1 for l in body.splitlines() if l.startswith("🔔")) == 3
    s.set("notify_excludes", ["codex:GPT-5.3-Codex-Spark:本周"])
    body, _ = handle_command("列表", _views(), s)
    assert "🔕 Codex · GPT-5.3-Codex-Spark · 本周" in body


def test_keyword_toggles_only_matching_window():
    s = _settings()
    body, _ = handle_command("codex5", _views(), s)
    assert "🔕" in body and "5小时" in body
    excludes = s.get("notify_excludes")
    main = codex_snapshot().primary_limit
    bucket = main.limit_name or main.limit_id
    assert key_excluded(f"codex:{bucket}:5小时", excludes)      # 5小时已关
    assert not key_excluded(f"codex:{bucket}:本周", excludes)   # 本周不受影响
    assert not key_excluded("codex:GPT-5.3-Codex-Spark:本周", excludes)
    # 再发一次 → 重新打开
    body, _ = handle_command("codex5", _views(), s)
    assert "🔔" in body
    assert not key_excluded(f"codex:{bucket}:5小时", s.get("notify_excludes"))


def test_bucket_keyword_toggles_all_its_windows():
    s = _settings()
    body, _ = handle_command("spark", _views(), s)
    assert "Spark" in body
    assert key_excluded("codex:GPT-5.3-Codex-Spark:本周",
                        s.get("notify_excludes"))


def test_unknown_command_returns_help():
    body, click = handle_command("今天天气", _views(), _settings())
    assert "没认出命令" in body and "kimi5" in body
    assert click == ""


def test_window_keys_match_summary_convention():
    """主限额不带桶名前缀，副桶带（与托盘摘要行的习惯一致）。"""
    labels = [label for _, label in window_keys(_views())]
    assert "Codex · 本周" in labels
    assert "Codex · 5小时" in labels
    assert "Codex · GPT-5.3-Codex-Spark · 本周" in labels


def _zai_views():
    from codex_quota.providers.zai import parse_quota
    snapshot = parse_quota({"code": 200, "data": {"limits": [
        {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 12},
        {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 34},
        {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 56},
    ]}})
    return [SimpleNamespace(name="zai", display_name="GLM (Z.ai)",
                            state=SimpleNamespace(snapshot=snapshot))]


def test_restart_command_dispatches_instead_of_returning_old_url():
    calls = []
    body, click = handle_command(" URLRestartCmd ", [], _settings(),
                                url="https://old.invalid", restart_url=lambda: (
                                    calls.append(True) or "已开始重连"))
    assert calls == [True]
    assert body == "已开始重连" and click == ""


def test_restart_unavailable_and_typo_does_not_restart():
    assert "未开启" in handle_command("urlrestartcmd", [], _settings())[0]
    def forbidden():
        raise AssertionError("非白名单指令不应重启")
    body, _ = handle_command("urlrestartcmd now", [], _settings(), restart_url=forbidden)
    assert "没认出" in body


def test_zai_explicit_switches_are_idempotent_and_scoped():
    views = _views() + _zai_views()
    settings = _settings()
    for command in ("zai5 off", "GLM5 OFF"):
        body, _ = handle_command(command, views, settings)
        assert "已关闭" in body
        assert settings.get("notify_excludes") == ["zai:Coding:5小时"]
    handle_command("zai本周 关闭", views, settings)
    assert key_excluded("zai:Coding:本周", settings.get("notify_excludes"))
    handle_command("zai本月 off", views, settings)
    assert key_excluded("zai:MCP:本月", settings.get("notify_excludes"))
    handle_command("glm on", views, settings)
    assert settings.get("notify_excludes") == []
    handle_command("zai off", views, settings)
    assert len(settings.get("notify_excludes")) == 3
    assert all(k.startswith("zai:") for k in settings.get("notify_excludes"))


def test_zai_enable_one_preserves_other_disabled_windows():
    settings = _settings()
    settings.set("notify_excludes", ["zai"])
    handle_command("zai5 on", _zai_views(), settings)
    excludes = settings.get("notify_excludes")
    assert not key_excluded("zai:Coding:5小时", excludes)
    assert key_excluded("zai:Coding:本周", excludes)
    assert key_excluded("zai:MCP:本月", excludes)


def test_zai_no_data_does_not_modify_settings():
    settings = _settings()
    settings.set("notify_excludes", ["codex"])
    handle_command("zai off", _views(), settings)
    assert settings.get("notify_excludes") == ["codex"]
