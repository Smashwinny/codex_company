"""轻量设置持久化（路径由 sysdirs.config_dir() 按平台分发）。

仅存 UI 偏好（透明度、紧凑模式、窗口位置），损坏时回退默认值。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional

from .sysdirs import config_dir


def default_settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


DEFAULTS: dict[str, Any] = {
    "opacity": 1.0,        # 窗口透明度 0.3–1.0
    "compact": False,      # 紧凑模式：只显示主限额行
    "pos": None,           # 窗口位置记忆 [x, y]
    "web_enabled": True,   # 手机访问（局域网 Web 服务）
    "web_port": 8642,      # Web 服务起始端口（冲突自动递增）
    "web_token": None,     # URL 鉴权 token，首次运行生成后持久化
    "tunnel_enabled": True,  # cloudflared 公网隧道（任意网络可访问）
    "notify_enabled": True,  # 额度重置回 100% 时推送手机通知（ntfy）
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": None,      # ntfy 订阅主题，首次运行生成后持久化（主题即凭证）
    "wizard_done": False,    # 首启向导是否已完成
    "color_warn_threshold": 30,  # 黄线：剩余量 ≤ 此百分比显示黄色
    "color_crit_threshold": 10,  # 红线：剩余量 ≤ 此百分比显示红色
    "tray_color_excludes": [],   # 不参与托盘取色的额度桶 ["provider:桶名", ...]
    "notify_excludes": [],       # 不发送重置推送的额度桶 ["provider:桶名", ...]
}


class Settings:
    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or default_settings_path()
        self._data: dict[str, Any] = dict(DEFAULTS)
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for key in DEFAULTS:
                    if key in raw:
                        self._data[key] = raw[key]
        except (OSError, ValueError):
            pass  # 文件不存在或损坏 → 默认值

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self._path)
        except OSError:
            pass  # 设置写不进去只是丢偏好，不影响功能
