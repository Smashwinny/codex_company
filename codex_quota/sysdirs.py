"""平台目录唯一权威：配置/缓存/日志路径按平台分发。

优先级（逐级回退，全平台一致）：
1. XDG_CONFIG_HOME / XDG_CACHE_HOME 环境变量——所有平台都检查，
   这是 tests/conftest.py 测试隔离的生命线，不能只在 Linux 检查
2. 平台默认：
   - Windows: %APPDATA%\\codex-quota（配置）/ %LOCALAPPDATA%\\codex-quota（缓存、日志）
   - macOS:   ~/Library/Application Support/codex-quota / ~/Library/Caches/codex-quota（预留）
   - Linux:   ~/.config/codex-quota / ~/.cache/codex-quota（现状，逐字节不变）

注意：sys.platform 在模块内每次调用时动态读取，测试可 monkeypatch。
"""

from __future__ import annotations

import os
import sys

APP_NAME = "codex-quota"


def _home() -> str:
    return os.path.expanduser("~")


def config_dir() -> str:
    """settings.json / providers.toml 所在目录。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(xdg, APP_NAME)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(_home(), "AppData", "Roaming")
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(_home(), "Library", "Application Support", APP_NAME)
    return os.path.join(_home(), ".config", APP_NAME)


def cache_dir() -> str:
    """last-good 缓存 / hud.log / pidfile 所在目录。"""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return os.path.join(xdg, APP_NAME)
    if sys.platform == "win32":
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(_home(), "AppData", "Local"))
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(_home(), "Library", "Caches", APP_NAME)
    return os.path.join(_home(), ".cache", APP_NAME)


def log_path() -> str:
    """HUD 日志文件（bash 启动器重定向或 pythonw 下应用内守卫都写这里）。"""
    return os.path.join(cache_dir(), "hud.log")
