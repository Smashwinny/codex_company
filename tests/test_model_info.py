"""模型信息解析与徽章渲染测试。"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")  # _badge_html 无 Qt 依赖，但 HUD 用例需要

from codex_quota.model_info import ModelInfo, read_model_info

SAMPLE_CONFIG = '''
model = "gpt-5.6-sol"
model_reasoning_effort = "low"
service_tier = "default"

[projects."/home/hulk"]
trust_level = "trusted"

[profiles.other]
model = "should-not-be-read"
'''


def write_config(tmp_path, monkeypatch, text=SAMPLE_CONFIG):
    home = tmp_path / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))


class TestReadModelInfo:
    def test_real_shape_config(self, tmp_path, monkeypatch):
        write_config(tmp_path, monkeypatch)
        info = read_model_info()
        assert info.model == "gpt-5.6-sol"
        assert info.effort == "low"
        assert info.service_tier == "default"
        assert info.is_fast is False

    def test_section_keys_not_read(self, tmp_path, monkeypatch):
        write_config(tmp_path, monkeypatch)
        info = read_model_info()
        assert info.model != "should-not-be-read"

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
        assert read_model_info() is None

    def test_no_model_key(self, tmp_path, monkeypatch):
        write_config(tmp_path, monkeypatch, 'model_reasoning_effort = "high"\n')
        assert read_model_info() is None

    def test_fast_by_spark_name(self):
        assert ModelInfo("GPT-5.3-Codex-Spark").is_fast is True

    def test_fast_by_tier(self):
        assert ModelInfo("gpt-5.6", service_tier="fast").is_fast is True
        assert ModelInfo("gpt-5.6", service_tier="priority").is_fast is True
        assert ModelInfo("gpt-5.6", service_tier="default").is_fast is False

    def test_display(self):
        assert ModelInfo("gpt-5.6-sol", "low", "default").display == "gpt-5.6-sol · low"
        assert ModelInfo("gpt-5.6-sol", "low", "fast").display == "gpt-5.6-sol · low · fast"
        assert ModelInfo("gpt-5.6-sol").display == "gpt-5.6-sol"


class TestBadge:
    def test_fast_badge_has_bolt_and_style(self):
        from codex_quota.ui.hud import BADGE_FAST_STYLE, BADGE_NORMAL_STYLE, _badge_html

        fast = _badge_html(ModelInfo("GPT-5.3-Codex-Spark", "high"))
        assert fast.startswith("⚡")
        assert "#f85149" in fast  # high → 红
        normal = _badge_html(ModelInfo("gpt-5.6-sol", "low"))
        assert not normal.startswith("⚡")
        assert "#3fb950" in normal  # low → 绿
        assert BADGE_FAST_STYLE != BADGE_NORMAL_STYLE

    def test_html_escaped(self):
        from codex_quota.ui.hud import _badge_html

        assert "<b>" not in _badge_html(ModelInfo("<b>evil</b>"))


class TestHudBadge:
    @pytest.fixture
    def hud(self, qapp, monkeypatch):
        from codex_quota.ui.hud import FloatingHud

        monkeypatch.setattr(FloatingHud, "refresh", lambda self: None)
        w = FloatingHud()
        yield w
        w.close()
        w.deleteLater()

    @pytest.fixture(scope="session")
    def qapp(self):
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        yield app

    def test_badge_hidden_without_config(self, hud):
        hud._update_model_badge()
        assert hud._model_badge.isHidden()

    def test_badge_shows_model(self, hud, tmp_path, monkeypatch):
        write_config(tmp_path, monkeypatch)
        hud._update_model_badge()
        assert not hud._model_badge.isHidden()
        assert "gpt-5.6-sol" in hud._model_badge.text()
        assert "low" in hud._model_badge.text()

    def test_badge_fast_style(self, hud, tmp_path, monkeypatch):
        from codex_quota.ui.hud import BADGE_FAST_STYLE

        write_config(tmp_path, monkeypatch,
                     'model = "GPT-5.3-Codex-Spark"\nservice_tier = "fast"\n')
        hud._update_model_badge()
        assert hud._model_badge.styleSheet() == BADGE_FAST_STYLE
        assert "⚡" in hud._model_badge.text()
