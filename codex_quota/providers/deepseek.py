"""DeepSeek 余额 provider（预设类型）。

GET https://api.deepseek.com/user/balance（Bearer API key）→
{"is_available": true, "balance_infos": [{"currency": "CNY",
"total_balance": "100.00", ...}]}

余额型数据：无窗口/重置概念，映射为 abs_remaining + abs_unit。

API key 解析链（按优先级）：
1. 配置显式值（或 "$ENV_VAR" 引用）
2. **dsh 凭证文件** ~/.dsh/.credentials.yaml（DeepSeek Harness 首次配置
   后 key 就存在这里——同机同权限域，直接复用，用户零输入）
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit
from .config import resolve_secret

BALANCE_URL = "https://api.deepseek.com/user/balance"
_DSH_KEY_RE = re.compile(r'DEEPSEEK_API_KEY:\s*["\']?([^"\'\s]+)')


class DeepSeekError(Exception):
    pass


def read_dsh_api_key(path: Optional[str] = None) -> Optional[str]:
    """从 dsh（DeepSeek Harness）的凭证文件读取 DEEPSEEK_API_KEY。"""
    path = path or os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            m = _DSH_KEY_RE.search(f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def parse_balance(payload: dict[str, Any], now: Optional[float] = None) -> QuotaSnapshot:
    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        raise DeepSeekError("DeepSeek 余额接口返回异常（缺少 balance_infos）")
    info = infos[0] if isinstance(infos[0], dict) else {}
    try:
        total = float(info.get("total_balance") or 0)
    except (TypeError, ValueError):
        total = 0.0
    w = QuotaWindow(abs_remaining=total, abs_unit=info.get("currency") or "CNY")
    rl = RateLimit(limit_id="deepseek", plan_type="deepseek", primary=w)
    return QuotaSnapshot(
        fetched_at=now if now is not None else time.time(),
        plan_type="deepseek",
        limits=[rl],
        provider="deepseek",
    )


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, api_key: Optional[str] = None, *,
                 display_name: str = "DeepSeek",
                 base_url: str = BALANCE_URL, timeout: float = 8.0):
        self._api_key = api_key
        self.display_name = display_name
        self._base_url = base_url
        self._timeout = timeout

    def fetch(self) -> QuotaSnapshot:
        key = resolve_secret(self._api_key) or read_dsh_api_key()
        if not key:
            raise DeepSeekError("未配置 DeepSeek API key（托盘 → 管理额度来源 中填写）")
        req = urllib.request.Request(
            self._base_url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise DeepSeekError("DeepSeek API key 无效（401），请检查") from exc
            raise DeepSeekError(f"DeepSeek 接口返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DeepSeekError("DeepSeek 接口无法连接（检查网络）") from exc
        return parse_balance(payload)

    def close(self) -> None:
        pass  # 无长驻资源
