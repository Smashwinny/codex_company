"""Z.ai GLM Coding Plan quotas (read-only monitor API, no model requests)."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit
from ..net import https_context
from .config import resolve_secret

QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"


class ZaiError(Exception):
    pass


def parse_quota(payload: dict, now: float | None = None) -> QuotaSnapshot:
    if not isinstance(payload, dict) or payload.get("success") is False or payload.get("code") not in (0, 200):
        raise ZaiError("GLM 额度查询被拒绝，请检查 Z.ai 密钥及 Coding Plan 状态")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("limits"), list):
        raise ZaiError("GLM 额度接口返回格式异常")
    limits = []
    for item in data["limits"]:
        if not isinstance(item, dict) or item.get("type") not in ("TOKENS_LIMIT", "CREDIT_LIMIT", "TIME_LIMIT"):
            continue
        try:
            pct = float(item["percentage"])
            if not math.isfinite(pct) or not 0 <= pct <= 100:
                raise ValueError
            reset = item.get("nextResetTime")
            reset = float(reset) / 1000 if reset is not None else None
            if reset is not None and (not math.isfinite(reset) or reset <= 0):
                raise ValueError
            unit, number = int(item.get("unit", 0)), int(item.get("number", 0))
        except (KeyError, TypeError, ValueError):
            raise ZaiError("GLM 额度数值异常") from None
        kind = item["type"]
        # Monthly classification uses 30 days; countdown uses the API's exact
        # reset timestamp, never this nominal duration.
        minutes = number * {3: 60, 5: 43200, 6: 10080}.get(unit, 0) or None
        name = "Coding" if kind != "TIME_LIMIT" else "MCP"
        limits.append(RateLimit(
            limit_id=f"{kind.lower()}_{unit}_{number}", limit_name=name,
            primary=QuotaWindow(used_percent=pct, window_minutes=minutes, reset_at=reset)))
    if not limits:
        raise ZaiError("GLM 未返回可用额度窗口，请确认 Coding Plan 已生效")
    limits.sort(key=lambda limit: (limit.limit_name != "Coding", limit.primary.window_minutes or 0))
    return QuotaSnapshot(fetched_at=time.time() if now is None else now,
                         plan_type="GLM Coding Plan", limits=limits, provider="zai")


class ZaiProvider:
    name = "zai"

    def __init__(self, api_key=None, *, display_name="GLM (Z.ai)",
                 base_url=QUOTA_URL, timeout=8.0):
        self._api_key = api_key
        self.display_name = display_name
        self._base_url = base_url
        self._timeout = timeout

    def fetch(self) -> QuotaSnapshot:
        key = resolve_secret(self._api_key)
        if not key:
            raise ZaiError("未配置 GLM API key，请在添加额度来源中填写")
        request = urllib.request.Request(self._base_url, headers={
            "Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout, context=https_context()) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ZaiError(f"GLM 额度接口返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError):
            raise ZaiError("GLM 额度接口无法连接，请检查网络") from None
        except (ValueError, UnicodeError):
            raise ZaiError("GLM 额度接口返回格式异常") from None
        return parse_quota(payload)

    def close(self):
        pass
