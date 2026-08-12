"""状态存储：当前快照、上次成功快照、错误状态与新鲜度。

M2 为纯内存态；M3 将在此之上加磁盘缓存（~/.cache/codex-quota/last-good.json）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .app_server import QuotaSnapshot


@dataclass
class ViewState:
    """UI 消费的状态：要么有快照可显示，要么只有错误信息。"""

    snapshot: Optional[QuotaSnapshot] = None
    stale: bool = False        # True 表示展示的是上次成功的旧数据
    error: Optional[str] = None
    refreshing: bool = False

    @property
    def fetched_at(self) -> Optional[float]:
        return self.snapshot.fetched_at if self.snapshot else None


class StateStore:
    def __init__(self) -> None:
        self._last_good: Optional[QuotaSnapshot] = None
        self.state = ViewState()

    def begin_refresh(self) -> ViewState:
        self.state.refreshing = True
        return self.state

    def on_success(self, snap: QuotaSnapshot) -> ViewState:
        self._last_good = snap
        self.state = ViewState(snapshot=snap, stale=False, error=None, refreshing=False)
        return self.state

    def on_error(self, message: str) -> ViewState:
        # 有旧快照则降级展示陈旧数据，没有则纯错误态
        self.state = ViewState(
            snapshot=self._last_good,
            stale=self._last_good is not None,
            error=message,
            refreshing=False,
        )
        return self.state

    @staticmethod
    def freshness_text(fetched_at: Optional[float], now: Optional[float] = None) -> str:
        if fetched_at is None:
            return "无数据"
        age = max(0.0, (now if now is not None else time.time()) - fetched_at)
        if age < 60:
            return f"{int(age)} 秒前"
        if age < 3600:
            return f"{int(age // 60)} 分钟前"
        return f"{int(age // 3600)} 小时前"
