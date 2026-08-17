"""额度重置通知：检测窗口重置并通过 ntfy 推送到手机。

- ResetWatcher：盯住每个 (provider, 限额桶, 窗口) 的剩余量，
  从 < 99.5% 跳到 ≥ 99.5%（即重置回满）时产生一次事件；只在跳变时触发，不重复
- NtfyNotifier：POST https://ntfy.sh/<topic>，手机装 ntfy App 订阅同名主题即收。
  主题即凭证，自动生成并持久化在 settings.json
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional

from .app_server import QuotaSnapshot

RESET_THRESHOLD = 99.5  # 剩余量跨过此线视为"重置回满"
DEFAULT_NTFY_SERVER = "https://ntfy.sh"


class ResetWatcher:
    """跨刷新记住上次剩余量，检测"回满"跳变。首次见到某窗口不产生事件。"""

    def __init__(self, threshold: float = RESET_THRESHOLD):
        self._threshold = threshold
        self._last: dict[tuple[str, str, str], float] = {}

    def check(self, provider: str, snap: QuotaSnapshot) -> list[str]:
        """返回本次新发生的重置事件描述列表（多数时候为空）。"""
        events: list[str] = []
        for limit in snap.limits:
            bucket = limit.limit_name or limit.limit_id
            for w in (limit.primary, limit.secondary):
                if w is None or w.remaining_percent is None:
                    continue
                key = (provider, bucket, w.label)
                prev = self._last.get(key)
                cur = w.remaining_percent
                if prev is not None and prev < self._threshold <= cur:
                    events.append(f"{provider} · {bucket} · {w.label}")
                self._last[key] = cur
        return events


class NtfyNotifier:
    """ntfy 发布端。推送失败静默返回 False（通知不是关键路径）。"""

    def __init__(self, server: str = DEFAULT_NTFY_SERVER, topic: str = "",
                 timeout: float = 8.0):
        self.server = server.rstrip("/")
        self.topic = topic
        self.timeout = timeout

    @property
    def subscribe_url(self) -> str:
        return f"{self.server}/{self.topic}"

    def publish(self, title: str, body: str, *, priority: str = "high",
                tags: str = "white_check_mark") -> bool:
        if not self.topic:
            return False
        req = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=body.encode("utf-8"),
            headers={
                # ntfy 的 Title header 只支持 ASCII，标题固定英文，中文放正文
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception:
            return False


def notify_resets(notifier: Optional[NtfyNotifier], watcher: ResetWatcher,
                  provider: str, display_name: str, snap: QuotaSnapshot) -> list[str]:
    """检测 + 推送；返回触发的事件列表（供日志/测试）。"""
    events = watcher.check(provider, snap)
    if notifier is not None:
        for event in events:
            notifier.publish(
                "codex-quota",
                f"✅ 额度已重置回 100%：{display_name}（{event}）",
            )
    return events
