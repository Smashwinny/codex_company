"""Settings 与 autostart 测试。"""

from __future__ import annotations

import sys

from codex_quota import autostart
from codex_quota.settings import Settings


class TestSettings:
    def make(self, tmp_path):
        return Settings(path=str(tmp_path / "cfg" / "settings.json"))

    def test_defaults(self, tmp_path):
        s = self.make(tmp_path)
        assert s.get("opacity") == 1.0
        assert s.get("compact") is False
        assert s.get("pos") is None

    def test_round_trip(self, tmp_path):
        s = self.make(tmp_path)
        s.set("opacity", 0.65)
        s.set("compact", True)
        s.set("pos", [100, 200])
        fresh = self.make(tmp_path)
        assert fresh.get("opacity") == 0.65
        assert fresh.get("compact") is True
        assert fresh.get("pos") == [100, 200]

    def test_corrupt_file_falls_back(self, tmp_path):
        p = tmp_path / "cfg" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text("{oops")
        s = self.make(tmp_path)
        assert s.get("opacity") == 1.0

    def test_unknown_keys_ignored(self, tmp_path):
        p = tmp_path / "cfg" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text('{"opacity": 0.5, "evil": true}')
        s = self.make(tmp_path)
        assert s.get("opacity") == 0.5
        assert s.get("evil") is None


class TestAutostart:
    def test_enable_disable_cycle(self, tmp_path):
        cfg = str(tmp_path)
        assert autostart.is_enabled(cfg) is False
        path = autostart.enable(cfg)
        assert autostart.is_enabled(cfg) is True
        content = open(path).read()
        assert "codex-quota" in content
        assert f"{sys.executable} -m codex_quota" in content
        assert "X-GNOME-Autostart-enabled=true" in content
        autostart.disable(cfg)
        assert autostart.is_enabled(cfg) is False

    def test_enable_idempotent(self, tmp_path):
        cfg = str(tmp_path)
        autostart.enable(cfg)
        autostart.enable(cfg)
        assert autostart.is_enabled(cfg) is True

    def test_disable_missing_ok(self, tmp_path):
        autostart.disable(str(tmp_path))  # 不抛异常
