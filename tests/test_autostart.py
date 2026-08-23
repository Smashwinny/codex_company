"""autostart Windows 注册表分支测试（mock winreg，不碰真实注册表）。"""

from __future__ import annotations

import sys

import pytest

from codex_quota import autostart


class FakeKey:
    """winreg key 句柄替身：支持 with 协议。"""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = 0x80000001
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    def __init__(self):
        self.store: dict[str, str] = {}

    def OpenKey(self, root, sub, reserved=0, access=0):
        assert root == self.HKEY_CURRENT_USER
        assert sub == autostart.RUN_KEY
        return FakeKey(self.store)

    def CreateKeyEx(self, root, sub, reserved=0, access=0):
        assert root == self.HKEY_CURRENT_USER
        assert sub == autostart.RUN_KEY
        return FakeKey(self.store)

    def QueryValueEx(self, key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, vtype, value):
        assert vtype == self.REG_SZ
        key.store[name] = value

    def DeleteValue(self, key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]


@pytest.fixture
def win32_env(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    return fake


class TestWinRegistry:
    def test_enable_writes_run_key_with_pythonw(self, win32_env, monkeypatch,
                                                tmp_path):
        # 模拟 venv 布局：Scripts/python.exe + pythonw.exe 并存
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").touch()
        (scripts / "pythonw.exe").touch()
        monkeypatch.setattr(autostart.sys, "executable",
                            str(scripts / "python.exe"))

        cmd = autostart.enable()
        assert win32_env.store[autostart.RUN_VALUE_NAME] == cmd
        assert "pythonw.exe" in cmd               # 自启用 pythonw，不弹黑窗
        assert cmd.startswith('"') and '"' in cmd  # 路径带引号（防空格）
        assert cmd.endswith("-m codex_quota")
        assert autostart.is_enabled() is True

    def test_enable_falls_back_to_python_exe(self, win32_env, monkeypatch,
                                             tmp_path):
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").touch()  # 无 pythonw
        monkeypatch.setattr(autostart.sys, "executable",
                            str(scripts / "python.exe"))
        assert "python.exe" in autostart.enable()

    def test_frozen_exe_has_no_python_module_args(self, win32_env,
                                                   monkeypatch, tmp_path):
        exe = tmp_path / "CodexQuota.exe"
        exe.touch()
        monkeypatch.setattr(autostart.sys, "executable", str(exe))
        monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)

        cmd = autostart.enable()
        assert cmd == f'"{exe}"'
        assert "-m codex_quota" not in cmd

    def test_disable_idempotent(self, win32_env):
        autostart.enable()
        autostart.disable()
        assert autostart.is_enabled() is False
        autostart.disable()  # 再删一次不炸

    def test_is_enabled_default_false(self, win32_env):
        assert autostart.is_enabled() is False

    def test_stale_pythonw_value_is_not_enabled(self, win32_env,
                                                 monkeypatch, tmp_path):
        exe = tmp_path / "CodexQuota.exe"
        exe.touch()
        monkeypatch.setattr(autostart.sys, "executable", str(exe))
        monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
        win32_env.store[autostart.RUN_VALUE_NAME] = (
            r'"C:\old-source\.venv\Scripts\pythonw.exe" -m codex_quota')

        assert autostart.is_enabled() is False
