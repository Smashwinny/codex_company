"""OpenRouter credits provider（预设类型）。

GET https://openrouter.ai/api/v1/credits（Bearer API key）→
{"data": {"total_credits": 10.0, "total_usage": 4.0}}（单位：美元）

既有百分比（已用/总额）又有绝对余额（剩余美元）：
used_percent + abs_remaining 同时填，展示层以百分比进度条为主。
api_key 支持 "$ENV_VAR" 引用环境变量。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit
from ..net import https_context
from .config import resolve_secret

CREDITS_URL = "https://openrouter.ai/api/v1/credits"


class OpenRouterError(Exception):
    pass


def parse_credits(payload: dict[str, Any], now: Optional[float] = None) -> QuotaSnapshot:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise OpenRouterError("OpenRouter credits 接口返回异常（缺少 data）")
    try:
        total = float(data.get("total_credits") or 0)
        used = float(data.get("total_usage") or 0)
    except (TypeError, ValueError) as exc:
        raise OpenRouterError(f"OpenRouter credits 数值解析失败: {exc}") from exc
    used_pct = (used / total * 100) if total > 0 else None
    remaining = max(0.0, total - used) if total > 0 else 0.0
    w = QuotaWindow(used_percent=used_pct, abs_remaining=remaining, abs_unit="USD")
    rl = RateLimit(limit_id="openrouter", plan_type="openrouter", primary=w)
    return QuotaSnapshot(
        fetched_at=now if now is not None else time.time(),
        plan_type="openrouter",
        limits=[rl],
        provider="openrouter",
    )


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: Optional[str] = None, *,
                 display_name: str = "OpenRouter",
                 base_url: str = CREDITS_URL, timeout: float = 8.0):
        self._api_key = api_key
        self.display_name = display_name
        self._base_url = base_url
        self._timeout = timeout

    def fetch(self) -> QuotaSnapshot:
        key = resolve_secret(self._api_key)
        if not key:
            raise OpenRouterError("未配置 OpenRouter API key（托盘 → 管理额度来源 中填写）")
        req = urllib.request.Request(
            self._base_url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=https_context()) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise OpenRouterError("OpenRouter API key 无效（401），请检查") from exc
            raise OpenRouterError(f"OpenRouter 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OpenRouterError("OpenRouter 接口无法连接（检查网络）") from exc
        return parse_credits(payload)

    def close(self) -> None:
        pass
