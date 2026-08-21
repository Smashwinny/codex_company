"""取数线程与刷新调度。

- QuotaFetcher：把阻塞的 app-server 查询放到 QThread，结果经 signal 回主线程。
  每次刷新新建一个线程实例（QThread 不可重启），由 HUD 持有引用防止被 GC。
  单个 provider 失败隔 3s 同周期重试一次——上游（chatgpt.com）瞬时抖动很常见，
  不立即重试的话要等下个 60s 周期，连续几次失败就是几分钟数据陈旧。
- RefreshScheduler：决定下一次自动刷新的间隔——
  可见 60s / 隐藏 180s；检测到 codex 会话活跃（~/.codex/sessions 有新写入）30s；
  连续失败指数退避 30s×2^n，封顶 5min；成功后重置。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from .app_server import codex_home

logger = logging.getLogger("codex_quota.fetcher")

RETRY_DELAY_S = 3.0  # 同周期重试间隔


class QuotaFetcher(QThread):
    succeeded = pyqtSignal(str, object)  # provider name, QuotaSnapshot
    failed = pyqtSignal(str, str)        # provider name, error message

    def __init__(self, providers, timeout: float = 8.0, parent=None):
        super().__init__(parent)
        self._providers = providers
        self._timeout = timeout

    def run(self) -> None:
        # 顺序查询各 provider；单个失败不影响其他
        for p in self._providers:
            t0 = time.monotonic()
            err: Optional[Exception] = None
            for attempt in (1, 2):
                try:
                    snap = p.fetch()
                    err = None
                    break
                except Exception as exc:  # 兜底：任何意外都不能让线程裸崩
                    err = exc
                    if attempt == 1:
                        logger.info("provider %s 首次失败（%.1fs），同周期重试: %s",
                                    p.name, time.monotonic() - t0, exc)
                        time.sleep(RETRY_DELAY_S)
            if err is not None:
                logger.warning("provider %s 查询失败（%.1fs）: %s",
                               p.name, time.monotonic() - t0, err)
                self.failed.emit(p.name, str(err))
                continue
            elapsed = time.monotonic() - t0
            if elapsed > 5:
                logger.info("provider %s 查询缓慢: %.1fs", p.name, elapsed)
            self.succeeded.emit(p.name, snap)


ACTIVE_MS = 60_000        # 窗口可见
HIDDEN_MS = 180_000       # 窗口隐藏/最小化
BUSY_MS = 30_000          # codex 会话活跃（正在写 session 日志）
BACKOFF_BASE_MS = 30_000  # 失败退避起点
BACKOFF_MAX_MS = 300_000  # 退避封顶
SESSION_ACTIVE_WINDOW_S = 300  # sessions 目录 5 分钟内有写入视为活跃


def codex_session_active(now: Optional[float] = None) -> bool:
    """~/.codex/sessions 最近 5 分钟内是否有文件被写入（说明正在跑任务）。"""
    sessions = os.path.join(codex_home(), "sessions")
    try:
        newest = 0.0
        for root, _dirs, files in os.walk(sessions):
            for name in files:
                try:
                    mtime = os.path.getmtime(os.path.join(root, name))
                except OSError:
                    continue
                newest = max(newest, mtime)
        if newest == 0.0:
            return False
    except OSError:
        return False
    return ((now if now is not None else time.time()) - newest) < SESSION_ACTIVE_WINDOW_S


class RefreshScheduler:
    """无状态 UI 依赖的纯逻辑类，便于单测。"""

    def __init__(self) -> None:
        self.visible = True
        self._failures = 0

    def set_visible(self, visible: bool) -> None:
        self.visible = visible

    def on_success(self) -> None:
        self._failures = 0

    def on_failure(self) -> None:
        self._failures += 1

    def next_interval_ms(self, session_active: Optional[bool] = None) -> int:
        if self._failures > 0:
            return min(BACKOFF_BASE_MS * (2 ** (self._failures - 1)), BACKOFF_MAX_MS)
        if not self.visible:
            return HIDDEN_MS
        if session_active is None:
            session_active = codex_session_active()
        return BUSY_MS if session_active else ACTIVE_MS
