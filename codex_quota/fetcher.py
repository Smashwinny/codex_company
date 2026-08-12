"""取数线程：把阻塞的 app-server 查询放到 QThread，结果经 signal 回主线程。

每次刷新新建一个线程实例（QThread 不可重启），由 HUD 持有引用防止被 GC。
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from .app_server import AppServerClient, AppServerError, QuotaSnapshot


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
