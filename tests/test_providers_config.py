"""providers.toml 配置加载/写回测试。"""

from __future__ import annotations

import os
import stat

import pytest

from codex_quota.providers.config import (
    load_providers_config,
    resolve_secret,
    save_providers_config,
)


class TestRoundTrip:
    def test_save_load(self, tmp_path):
        path = str(tmp_path / "providers.toml")
        cfg = {
            "kimi": {"enabled": False},
            "deepseek": {"type": "deepseek", "enabled": True,
                         "display_name": "DeepSeek", "api_key": "sk-abc"},
        }
        save_providers_config(cfg, path)
        assert load_providers_config(path) == cfg

    def test_file_mode_600(self, tmp_path):
        path = str(tmp_path / "providers.toml")
        save_providers_config({"kimi": {"enabled": True}}, path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_missing_file(self, tmp_path):
        assert load_providers_config(str(tmp_path / "nope.toml")) == {}


class TestParsing:
    def test_scalars_and_comments(self, tmp_path):
        path = tmp_path / "providers.toml"
        path.write_text(
            "# 注释\n"
            "[providers.kimi]\n"
            "enabled = false  # 行尾注释\n"
            "\n"
            "[providers.deepseek]\n"
            'type = "deepseek"\n'
            "interval = 300\n"
            'api_key = "$DS_KEY"\n'
        )
        cfg = load_providers_config(str(path))
        assert cfg["kimi"]["enabled"] is False
        assert cfg["deepseek"]["interval"] == 300
        assert cfg["deepseek"]["api_key"] == "$DS_KEY"

    def test_corrupt_tolerated(self, tmp_path):
        path = tmp_path / "providers.toml"
        path.write_text("{{{ not toml at all\n[providers.x]\nbad line here\n")
        cfg = load_providers_config(str(path))
        assert cfg.get("x") == {} or cfg == {"x": {}}


class TestResolveSecret:
    def test_env_ref(self, monkeypatch):
        monkeypatch.setenv("DS_KEY", "sk-real")
        assert resolve_secret("$DS_KEY") == "sk-real"

    def test_env_ref_missing(self, monkeypatch):
        monkeypatch.delenv("DS_KEY", raising=False)
        assert resolve_secret("$DS_KEY") is None

    def test_plain(self):
        assert resolve_secret("sk-plain") == "sk-plain"

    def test_empty(self):
        assert resolve_secret("") is None
        assert resolve_secret(None) is None
