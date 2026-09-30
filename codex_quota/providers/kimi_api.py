"""Kimi For Coding 额度 provider（API key 预设类型，无需本地 kimi CLI）。

GET https://api.kimi.com/coding/v1/usages（Authorization: Bearer <API key>）
端点与 cc-switch query_kimi / sub2api 同款（实测 /coding/usages 无 /v1 → 404）。

响应（实测）：
{
  "usage": {"limit": "100", "used": "10", "remaining": "90",
            "resetTime": "2026-10-07T06:32:10.901710Z"},
  "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
              "detail": {"limit": "100", "used": "12", "remaining": "88", ...}}],
  "usages": {"limit_5h": {"used_ratio": 0.117, "reset_time": "..."},
             "limit_7d": {"used_ratio": 0.102, "reset_time": "..."}}
}

- 5 小时窗：优先 usages.limit_5h（used_ratio 0-1 精确值），回退 limits[].detail（首个）
- 每周窗：优先 usages.limit_7d，回退 usage
- limit/used/remaining 实测序列化为字符串，解析需容错；resetTime 兼容 Unix
  秒/毫秒数字与 ISO8601 字符串
- 与本地 KimiProvider 同 name="kimi"：providers.toml 的 [providers.kimi] 配了
  api_key 时优先走本类，否则回退本地 CLI（装了才启用）

api_key 支持 "$ENV_VAR" 引用环境变量。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit
from .config import resolve_secret
from .kimi import _parse_iso8601

USAGES_URL = "https://api.kimi.com/coding/v1/usages"
WINDOW_5H_MINUTES = 300
WINDOW_WEEK_MINUTES = 10080


class KimiApiError(Exception):
    pass


def _parse_reset(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value > 0:
        ms = float(value)
        return ms / 1000.0 if ms >= 1_000_000_000_000 else ms
    return _parse_iso8601(value) if isinstance(value, str) else None


def _num(value: Any) -> Optional[float]:
    """Kimi 实测把数值序列化成字符串（"limit":"100"），需容错转换。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _window_from_bucket(bucket: Any, minutes: int) -> Optional[QuotaWindow]:
    if not isinstance(bucket, dict):
        return None
    limit, remaining = _num(bucket.get("limit")), _num(bucket.get("remaining"))
    if limit is None or remaining is None or limit <= 0:
        return None
    used = max(0.0, limit - remaining)
    return QuotaWindow(
        used_percent=used / limit * 100,
        window_minutes=minutes,
        reset_at=_parse_reset(bucket.get("resetTime")),
    )


def _window_from_ratio(bucket: Any, minutes: int) -> Optional[QuotaWindow]:
    """usages.limit_5h / limit_7d 形态：used_ratio 为 0-1 的小数。"""
    if not isinstance(bucket, dict):
        return None
    ratio = _num(bucket.get("used_ratio"))
    if ratio is None:
        return None
    return QuotaWindow(
        used_percent=max(0.0, ratio * 100),
        window_minutes=minutes,
        reset_at=_parse_reset(bucket.get("reset_time")),
    )


def parse_kimi_usages(payload: dict[str, Any], now: Optional[float] = None) -> QuotaSnapshot:
    """把 /coding/v1/usages 的响应映射为 QuotaSnapshot。"""
    # 5 小时窗：优先 usages.limit_5h（used_ratio 精确），回退 limits[].detail
    five_h = _window_from_ratio((payload.get("usages") or {}).get("limit_5h"),
                                WINDOW_5H_MINUTES)
    if five_h is None:
        for item in payload.get("limits") or []:
            if isinstance(item, dict) and isinstance(item.get("detail"), dict):
                five_h = _window_from_bucket(item["detail"], WINDOW_5H_MINUTES)
                if five_h is not None:
                    break
    # 每周窗：优先 usages.limit_7d，回退 usage
    weekly = _window_from_ratio((payload.get("usages") or {}).get("limit_7d"),
                                WINDOW_WEEK_MINUTES)
    if weekly is None:
        weekly = _window_from_bucket(payload.get("usage"), WINDOW_WEEK_MINUTES)
    if five_h is None and weekly is None:
        raise KimiApiError("Kimi 额度接口未返回任何限额窗口")

    # 与本地 Kimi 分区一致：primary=本周，secondary=5小时
    primary = weekly or five_h or QuotaWindow()
    secondary = five_h if weekly is not None else None
    rl = RateLimit(limit_id="kimi", plan_type=None, primary=primary,
                   secondary=secondary)
    return QuotaSnapshot(
        fetched_at=now if now is not None else time.time(),
        plan_type=None,
        limits=[rl],
        provider="kimi",
    )


class KimiApiProvider:
    name = "kimi"

    def __init__(self, api_key: Optional[str] = None, *,
                 display_name: str = "Kimi",
                 base_url: str = USAGES_URL, timeout: float = 8.0):
        self._api_key = api_key
        self.display_name = display_name
        self._base_url = base_url
        self._timeout = timeout

    def fetch(self) -> QuotaSnapshot:
        key = resolve_secret(self._api_key)
        if not key:
            raise KimiApiError("未配置 Kimi API key（providers.toml 的 [providers.kimi] 中填写）")
        req = urllib.request.Request(
            self._base_url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise KimiApiError("Kimi API key 无效（401/403），请检查") from exc
            raise KimiApiError(f"Kimi 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise KimiApiError("Kimi 接口无法连接（检查网络）") from exc
        return parse_kimi_usages(payload)

    def close(self) -> None:
        pass  # 无长驻资源
