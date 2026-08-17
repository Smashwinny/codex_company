"""手动余额 provider：零 key、零网络的兜底方案。

用户在"管理额度来源"里手填当前余额（如 DeepSeek 网页看到的），
应用按余额型显示（¥xx + 低余额变色）；fetched_at 取填写时间，
新鲜度提示（"更新于 x 天前"）自然提醒用户该更新了。

配置（providers.toml）：

    [providers.manual]
    type = "manual"
    enabled = true
    display_name = "DeepSeek（手动）"
    balance = 23.5
    unit = "CNY"
    updated_at = 1787000000
"""

from __future__ import annotations

import time
from typing import Any, Optional

from ..app_server import QuotaSnapshot, QuotaWindow, RateLimit


class ManualError(Exception):
    pass


class ManualProvider:
    name = "manual"

    def __init__(self, *, display_name: str = "手动余额",
                 balance: Optional[float] = None, unit: str = "CNY",
                 updated_at: Optional[float] = None):
        self.display_name = display_name
        self._balance = balance
        self._unit = unit
        self._updated_at = updated_at

    @classmethod
    def from_section(cls, section: dict[str, Any]) -> "ManualProvider":
        balance = section.get("balance")
        return cls(
            display_name=section.get("display_name") or "手动余额",
            balance=float(balance) if isinstance(balance, (int, float)) else None,
            unit=section.get("unit") or "CNY",
            updated_at=float(section["updated_at"])
            if isinstance(section.get("updated_at"), (int, float)) else None,
        )

    def fetch(self) -> QuotaSnapshot:
        if self._balance is None:
            raise ManualError("尚未填写余额（托盘 → 管理额度来源 中填写）")
        w = QuotaWindow(abs_remaining=self._balance, abs_unit=self._unit)
        rl = RateLimit(limit_id="manual", plan_type=self.display_name, primary=w)
        return QuotaSnapshot(
            fetched_at=self._updated_at or time.time(),
            plan_type=self.display_name,
            limits=[rl],
            provider="manual",
        )

    def close(self) -> None:
        pass
