"""sysdirs 平台目录分发测试。

关键回归：XDG_* 环境变量在所有平台（含 win32/darwin）都最优先——
conftest.py 的测试隔离依赖这一点。
"""

from __future__ import annotations

import os

import pytest

from codex_quota import sysdirs


@pytest.fixture
def _clean_env(monkeypatch, tmp_path):
    """清掉所有相关环境变量，按平台逐一验证。"""
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class TestXdgPriority:
    """XDG env 在所有平台上最优先（conftest 隔离的生命线）。"""

    @pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
    def test_xdg_wins_on_all_platforms(self, monkeypatch, tmp_path, platform):
        monkeypatch.setattr(sysdirs.sys, "platform", platform)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        assert sysdirs.config_dir() == os.path.join(str(tmp_path), "cfg", "codex-quota")
        assert sysdirs.cache_dir() == os.path.join(str(tmp_path), "cache", "codex-quota")


class TestPlatformDefaults:
    def test_linux_defaults(self, monkeypatch, _clean_env):
        monkeypatch.setattr(sysdirs.sys, "platform", "linux")
        home = os.path.expanduser("~")
        assert sysdirs.config_dir() == os.path.join(home, ".config", "codex-quota")
        assert sysdirs.cache_dir() == os.path.join(home, ".cache", "codex-quota")

    def test_win32_defaults(self, monkeypatch, _clean_env):
        monkeypatch.setattr(sysdirs.sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
        # 期望按 os.path.join 语义拼接（真 Windows 上 ntpath 会用反斜杠）
        assert sysdirs.config_dir() == os.path.join(
            r"C:\Users\u\AppData\Roaming", "codex-quota")
        assert sysdirs.cache_dir() == os.path.join(
            r"C:\Users\u\AppData\Local", "codex-quota")

    def test_win32_fallback_without_env(self, monkeypatch, _clean_env):
        monkeypatch.setattr(sysdirs.sys, "platform", "win32")
        home = os.path.expanduser("~")
        assert sysdirs.config_dir() == os.path.join(
            home, "AppData", "Roaming", "codex-quota")
        assert sysdirs.cache_dir() == os.path.join(
            home, "AppData", "Local", "codex-quota")

    def test_darwin_defaults(self, monkeypatch, _clean_env):
        monkeypatch.setattr(sysdirs.sys, "platform", "darwin")
        home = os.path.expanduser("~")
        assert sysdirs.config_dir() == os.path.join(
            home, "Library", "Application Support", "codex-quota")
        assert sysdirs.cache_dir() == os.path.join(
            home, "Library", "Caches", "codex-quota")

    def test_log_path_under_cache(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert sysdirs.log_path() == os.path.join(
            str(tmp_path), "codex-quota", "hud.log")


class TestLinuxByteIdentical:
    """现有调用方切换后 Linux 路径逐字节不变（默认环境、无 XDG 变量）。"""

    def test_settings_path_unchanged(self, _clean_env):
        from codex_quota.settings import default_settings_path

        home = os.path.expanduser("~")
        assert default_settings_path() == os.path.join(
            home, ".config", "codex-quota", "settings.json")

    def test_cache_path_unchanged(self, _clean_env):
        from codex_quota.state import default_cache_path

        home = os.path.expanduser("~")
        assert default_cache_path() == os.path.join(
            home, ".cache", "codex-quota", "last-good.json")
        assert default_cache_path("kimi") == os.path.join(
            home, ".cache", "codex-quota", "last-good-kimi.json")

    def test_providers_toml_unchanged(self, _clean_env):
        from codex_quota.providers.config import default_config_path

        home = os.path.expanduser("~")
        assert default_config_path() == os.path.join(
            home, ".config", "codex-quota", "providers.toml")

    def test_autostart_dir_unchanged(self, _clean_env):
        from codex_quota.autostart import autostart_dir

        home = os.path.expanduser("~")
        assert autostart_dir() == os.path.join(home, ".config", "autostart")
