"""Claude Code 用量 provider（预设类型）。

数据源（与 ai-fuelgauge 同一路径）：
- 凭证：$CLAUDE_CONFIG_DIR/.credentials.json 或 ~/.claude/.credentials.json
  中的 claudeAiOauth.accessToken（只读取 token 用于请求头，不做他用）
- 接口：GET https://api.anthropic.com/api/oauth/usage
  请求头 anthropic-beta: oauth-2025-04-20
- 返回 five_hour / seven_day 两个窗口：utilization（0–1 或 0–100 均兼容）
  + resets_at（ISO8601）
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA = "oauth-2025-04-20"


class ClaudeCodeError(Exception):
    pass


def credentials_path() -> Optional[str]:
    """定位 Claude Code 凭证文件；不存在返回 None。"""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    path = os.path.join(base, ".credentials.json")
    return path if os.path.isfile(path) else None


def _read_token(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        token = (data.get("claudeAiOauth") or {}).get("accessToken")
    except (OSError, ValueError) as exc:
        raise ClaudeCodeError(f"Claude Code 凭证读取失败: {exc}") from exc
    if not token:
        raise ClaudeCodeError("Claude Code 凭证中缺少 accessToken（请重新登录 claude）")
    return token


def _parse_iso8601(ts: Any) -> Optional[float]:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _utilization_to_percent(value: Any) -> Optional[float]:
    """utilization 可能是 0–1 分数或 0–100 百分比，统一成百分比。"""
    if not isinstance(value, (int, float)):
        return None
    return float(value) * 100 if value <= 1 else float(value)


def _window(entry: Any, minutes: int) -> Optional[QuotaWindow]:
    if not isinstance(entry, dict):
        return None
    return QuotaWindow(
        used_percent=_utilization_to_percent(entry.get("utilization")),
        window_minutes=minutes,
        reset_at=_parse_iso8601(entry.get("resets_at")),
    )


def parse_usage(payload: dict[str, Any], now: Optional[float] = None) -> QuotaSnapshot:
    primary = _window(payload.get("five_hour"), 300)      # 5 小时窗
    secondary = _window(payload.get("seven_day"), 10080)  # 7 天窗
    if primary is None and secondary is None:
        raise ClaudeCodeError("Claude Code 用量接口返回异常（缺少 five_hour/seven_day）")
    rl = RateLimit(limit_id="claude", plan_type="claude",
                   primary=primary or QuotaWindow(), secondary=secondary)
    return QuotaSnapshot(
        fetched_at=now if now is not None else time.time(),
        plan_type="claude",
        limits=[rl],
        provider="claude",
    )


class ClaudeCodeProvider:
    name = "claude"
    display_name = "Claude Code"

    def __init__(self, *, credentials: Optional[str] = None,
                 base_url: str = USAGE_URL, timeout: float = 8.0):
        self._credentials = credentials
        self._base_url = base_url
        self._timeout = timeout

    def fetch(self) -> QuotaSnapshot:
        path = self._credentials or credentials_path()
        if path is None:
            raise ClaudeCodeError("未找到 Claude Code 登录凭证（请先运行 claude 登录）")
        token = _read_token(path)
        req = urllib.request.Request(
            self._base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": ANTHROPIC_BETA,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ClaudeCodeError("Claude Code 登录已过期，请重新运行 claude login") from exc
            raise ClaudeCodeError(f"Claude Code 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ClaudeCodeError("Claude Code 接口无法连接（检查网络）") from exc
        return parse_usage(payload)

    def close(self) -> None:
        pass
