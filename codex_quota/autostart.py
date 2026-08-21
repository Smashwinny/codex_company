"""开机自启：freedesktop autostart 规范（<config 根>/autostart/codex-quota.desktop）。

Exec 使用当前解释器路径（venv 中的 python 也能正确指回本项目）。
配置根目录由 sysdirs 分发（XDG_CONFIG_HOME 优先，跨平台）。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from .sysdirs import config_dir

DESKTOP_FILENAME = "codex-quota.desktop"


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
    return os.path.isfile(_path(config_home))


def enable(config_home: Optional[str] = None) -> str:
    path = _path(config_home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(desktop_entry())
    return path


def disable(config_home: Optional[str] = None) -> None:
    try:
        os.remove(_path(config_home))
    except FileNotFoundError:
        pass


def _path(config_home: Optional[str]) -> str:
    if config_home is not None:
        return os.path.join(config_home, "autostart", DESKTOP_FILENAME)
    return os.path.join(autostart_dir(), DESKTOP_FILENAME)
