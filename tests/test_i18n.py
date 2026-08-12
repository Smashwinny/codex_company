"""i18n 测试。"""

from __future__ import annotations

from codex_quota.i18n import language, set_language, tr


class TestLanguageDetection:
    def test_env_override_en(self, monkeypatch):
        set_language(None)
        monkeypatch.setenv("CODEX_QUOTA_LANG", "en")
        assert language() == "en"

    def test_env_override_zh(self, monkeypatch):
        set_language(None)
        monkeypatch.setenv("CODEX_QUOTA_LANG", "zh")
        assert language() == "zh"

    def test_fallback_to_lang(self, monkeypatch):
        set_language(None)
        monkeypatch.delenv("CODEX_QUOTA_LANG", raising=False)
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        assert language() == "en"
        monkeypatch.setenv("LANG", "zh_CN.UTF-8")
        set_language(None)
        assert language() == "zh"


class TestTr:
    def test_zh_passthrough(self):
        set_language("zh")
        assert tr("本周") == "本周"

    def test_en_translation(self):
        set_language("en")
        assert tr("本周") == "Weekly"
        assert tr("5小时") == "5-hour"
        assert tr("退出") == "Quit"

    def test_template_format(self):
        set_language("en")
        assert tr("{n} 秒前").format(n=5) == "5s ago"
        assert tr("剩 {p}%").format(p="9") == "9% left"
        set_language("zh")
        assert tr("{n} 秒前").format(n=5) == "5 秒前"

    def test_unknown_key_passthrough(self):
        set_language("en")
        assert tr("没有翻译的字符串") == "没有翻译的字符串"

    def test_window_label_i18n(self):
        from codex_quota.app_server import QuotaWindow

        set_language("zh")
        assert QuotaWindow(window_minutes=10080).label == "本周"
        set_language("en")
        assert QuotaWindow(window_minutes=10080).label == "Weekly"
        assert QuotaWindow(window_minutes=300).label == "5-hour"
        assert QuotaWindow(window_minutes=1440).label == "24-hour"
