"""单实例：QLocalServer/QLocalSocket 跨平台实现。

第二实例连接成功 → 发消息后退出；首实例收到任意消息即 raise 已有窗口。
- Windows：命名管道 \\.\pipe\<name>，按会话隔离，进程死亡即消失
- POSIX：/tmp 下 unix socket，多用户机器按 uid 区分；崩溃残留 socket 文件
  会导致 listen 失败（AddressInUseError）→ removeServer 后重试一次
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

IS_WINDOWS = sys.platform == "win32"


class SingleInstance(QObject):
    def __init__(self, name: str = "codex-quota", parent: Optional[QObject] = None):
        super().__init__(parent)
        if not IS_WINDOWS:
            name = f"{name}-{os.getuid()}"  # POSIX 多用户机按用户隔离
        self._name = name
        self._server: Optional[QLocalServer] = None
        self._on_raise: Optional[Callable[[], None]] = None

    def try_acquire(self) -> bool:
        """True = 首个实例（已 listen）；False = 已有实例在跑（已通知其 raise）。"""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if sock.waitForConnected(200):
            sock.write(b"raise")
            sock.flush()
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return False

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(self._name):
            # POSIX 陈旧 socket 残留：删掉重试一次；仍失败则放行（宁可双开不锁死）
            QLocalServer.removeServer(self._name)
            self._server.listen(self._name)
        return True

    def set_raise_callback(self, cb: Callable[[], None]) -> None:
        self._on_raise = cb

    # ---------- 内部 ----------

    def _on_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.deleteLater()
        if self._on_raise is not None:
            self._on_raise()
