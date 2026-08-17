"""providers.toml 配置加载/写回（provider 开关与密钥）。

格式（TOML 子集：节 + 标量键值，Python 3.10 无 tomllib 故手写极简解析）：

    [providers.kimi]
    enabled = false

    [providers.deepseek]
    type = "deepseek"
    display_name = "DeepSeek"
    api_key = "sk-..."            # 或 "$DEEPSEEK_API_KEY" 引用环境变量

密钥安全：支持 "$ENV_VAR" 引用（不落盘明文）；写回后 chmod 600。
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from ..settings import default_settings_path

_SECTION_RE = re.compile(r"^\[providers\.([A-Za-z0-9_-]+)\]\s*$")
_KV_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*(?:#.*)?$')


def default_config_path() -> str:
    return os.path.join(os.path.dirname(default_settings_path()), "providers.toml")


def _parse_value(raw: str) -> Any:
    if raw in ("true", "false"):
        return raw == "true"
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"')
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw  # 容错：当裸字符串


def _format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"' + str(v).replace('"', '\\"') + '"'


def load_providers_config(path: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """读 providers.toml → {name: {key: value}}。文件不存在/损坏返回 {}。"""
    path = path or default_config_path()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    cfg: dict[str, dict[str, Any]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1)
            cfg.setdefault(current, {})
            continue
        m = _KV_RE.match(line)
        if m and current is not None:
            cfg[current][m.group(1)] = _parse_value(m.group(2))
    return cfg


def save_providers_config(cfg: dict[str, dict[str, Any]],
                          path: Optional[str] = None) -> str:
    """写回 providers.toml（含密钥，chmod 600）。"""
    path = path or default_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# codex-quota provider 配置（可由托盘“管理额度来源”编辑）", ""]
    for name, section in cfg.items():
        lines.append(f"[providers.{name}]")
        for key, value in section.items():
            lines.append(f"{key} = {_format_value(value)}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.chmod(path, 0o600)
    return path


def resolve_secret(value: Any) -> Optional[str]:
    """解析密钥："$ENV_VAR" → 环境变量值（未设置返回 None）；其余原样返回。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("$"):
        return os.environ.get(value[1:]) or None
    return value
