"""取数线程与刷新调度。

- QuotaFetcher：把阻塞的 app-server 查询放到 QThread，结果经 signal 回主线程。
  每次刷新新建一个线程实例（QThread 不可重启），由 HUD 持有引用防止被 GC。
- RefreshScheduler：决定下一次自动刷新的间隔——
  可见 60s / 隐藏 180s；检测到 codex 会话活跃（~/.codex/sessions 有新写入）30s；
  连续失败指数退避 30s×2^n，封顶 5min；成功后重置。
"""

from __future__ import annotations

import os
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from .app_server import AppServerClient, AppServerError, QuotaSnapshot, codex_home


class QuotaFetcher(QThread):
    succeeded = pyqtSignal(object)  # QuotaSnapshot
    failed = pyqtSignal(str)

    def __init__(self, timeout: float = 8.0, parent=None):
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        try:
            snap: QuotaSnapshot = AppServerClient(timeout=self._timeout).read_rate_limits()
        except AppServerError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # 兜底：任何意外都不能让线程裸崩
            self.failed.emit(f"未知错误: {exc}")
            return
        self.succeeded.emit(snap)


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
