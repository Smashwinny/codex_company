"""Codex provider：包装 AppServerClient（app-server JSON-RPC，即查即毁）。"""

from __future__ import annotations

from ..app_server import AppServerClient, QuotaSnapshot


class CodexProvider:
    name = "codex"
    display_name = "Codex"

    def __init__(self, timeout: float = 8.0):
        self._timeout = timeout

    def fetch(self) -> QuotaSnapshot:
        snap = AppServerClient(timeout=self._timeout).read_rate_limits()
        snap.provider = self.name
        return snap

    def close(self) -> None:
        pass  # 无长驻资源
