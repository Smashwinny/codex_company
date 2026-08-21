"""开机自启：按平台分发，is_enabled()/enable()/disable() API 不变。

- Linux: freedesktop autostart 规范（<config 根>/autostart/codex-quota.desktop），
  Exec 使用当前解释器路径（venv 中的 python 也能正确指回本项目）
- Windows: 注册表 HKCU\\...\\CurrentVersion\\Run\\codex-quota（HKCU 免管理员）；
  python.exe 同目录有 pythonw.exe 就用它——登录自启不弹控制台黑窗
- macOS: LaunchAgent 留待 mac 适配阶段
配置根目录由 sysdirs 分发（XDG_CONFIG_HOME 优先，跨平台）。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from .sysdirs import config_dir

DESKTOP_FILENAME = "codex-quota.desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "codex-quota"


def autostart_dir() -> str:
    """freedesktop autostart 目录 = 配置根目录的上一级 + autostart。"""
    return os.path.join(os.path.dirname(config_dir()), "autostart")


def desktop_entry(exec_cmd: Optional[str] = None) -> str:
    exec_cmd = exec_cmd or f"{sys.executable} -m codex_quota"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=codex-quota\n"
        "Comment=Codex quota floating widget\n"
        f"Exec={exec_cmd}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_enabled(config_home: Optional[str] = None) -> bool:
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform == "darwin":
        return False  # mac 阶段实现 LaunchAgent
    return os.path.isfile(_path(config_home))


def enable(config_home: Optional[str] = None) -> str:
    if sys.platform == "win32":
        return _win_enable()
    if sys.platform == "darwin":
        raise NotImplementedError("mac 自启适配在后续阶段")
    path = _path(config_home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(desktop_entry())
    return path


def disable(config_home: Optional[str] = None) -> None:
    if sys.platform == "win32":
        _win_disable()
        return
    if sys.platform == "darwin":
        raise NotImplementedError("mac 自启适配在后续阶段")
    try:
        os.remove(_path(config_home))
    except FileNotFoundError:
        pass


def _path(config_home: Optional[str]) -> str:
    if config_home is not None:
        return os.path.join(config_home, "autostart", DESKTOP_FILENAME)
    return os.path.join(autostart_dir(), DESKTOP_FILENAME)


# ---------- Windows：注册表 Run 键 ----------

def _win_exec_cmd() -> str:
    """自启命令：优先 pythonw（不弹控制台）；路径含空格必须带引号。"""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        candidate = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(candidate):
            exe = candidate
    return f'"{exe}" -m codex_quota'


def _win_is_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def _win_enable() -> str:
    import winreg

    cmd = _win_exec_cmd()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def _win_disable() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE_NAME)
    except FileNotFoundError:
        pass
