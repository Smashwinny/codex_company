"""Strict single instance: atomic QLockFile ownership plus local activation IPC.

A missing acknowledgement does not prove the owner is dead. Never remove a
live owner's lock, kill its PID, or start without mutual exclusion. Qt may
recover locks whose owner has actually exited; time alone never expires them.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QLockFile
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .sysdirs import cache_dir

IS_WINDOWS = sys.platform == "win32"

logger = logging.getLogger("codex_quota.single_instance")

RAISE_MSG = b"raise"
ACK_MSG = b"ack"
ACK_TIMEOUT_MS = 2000  # 超时只代表未收到应答，不代表持锁进程已退出


class SingleInstance(QObject):
    def __init__(self, name: str = "codex-quota", parent: Optional[QObject] = None):
        super().__init__(parent)
        if not IS_WINDOWS:
            name = f"{name}-{os.getuid()}"  # POSIX 多用户机按用户隔离
        self._name = name
        self._lock: Optional[QLockFile] = None
        self._server: Optional[QLocalServer] = None
        self._on_raise: Optional[Callable[[], None]] = None
        self._existing_connected = False

    def try_acquire(self) -> bool:
        """Start only while holding the exclusive lock and listening socket."""
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            self._lock = QLockFile(os.path.join(cache_dir(), f"{self._name}.lock"))
            self._lock.setStaleLockTime(0)
            acquired = self._lock.tryLock(0)
        except OSError as exc:
            logger.error("单实例锁不可用，拒绝启动第二份程序: %s", exc)
            self._lock = None
            self._notify_existing()
            return False

        if not acquired:
            for attempt in range(5):
                if self._notify_existing(timeout_ms=500):
                    return False
                if self._existing_connected:
                    break
                if attempt < 4:
                    time.sleep(0.3)
            logger.warning("已有实例持锁但暂未响应；保留原进程，不重复启动")
            return False

        # A previous release or a different cache path may own the same IPC
        # endpoint. Do not unlink it just because its event loop is busy.
        if self._notify_existing():
            self._release_lock()
            return False
        if self._existing_connected:
            logger.warning("已有实例连接未应答，拒绝重复启动")
            self._release_lock()
            return False

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if self._server.listen(self._name):
            return True
        if not IS_WINDOWS and not self._notify_existing():
            if not self._existing_connected:
                QLocalServer.removeServer(self._name)
                if self._server.listen(self._name):
                    return True
        logger.error("无法建立单实例监听，拒绝无互斥启动: %s",
                     self._server.errorString())
        self._release_lock()
        return False

    def _notify_existing(self, timeout_ms: int = 200) -> bool:
        """连接已有实例请求 raise；只有收到 ack 才算"已有健康实例"。"""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if not sock.waitForConnected(timeout_ms):
            return False
        self._existing_connected = True
        sock.write(RAISE_MSG)
        sock.flush()
        sock.waitForBytesWritten(500)
        # 短暂无应答时也保留原进程，不能据此断定它已死。
        if sock.waitForReadyRead(ACK_TIMEOUT_MS):
            ok = bytes(sock.readAll()) == ACK_MSG
            sock.disconnectFromServer()
            return ok
        sock.disconnectFromServer()
        return False

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    def set_raise_callback(self, cb: Callable[[], None]) -> None:
        self._on_raise = cb

    # ---------- 内部 ----------

    def _on_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._handle_message(c))

    def _handle_message(self, conn) -> None:
        """收到 raise → 先回 ack（活性证明），再激活窗口，然后断开。"""
        msg = bytes(conn.readAll())
        if msg == RAISE_MSG:
            conn.write(ACK_MSG)
            conn.flush()
            conn.waitForBytesWritten(1000)
            if self._on_raise is not None:
                self._on_raise()
        conn.disconnectFromServer()
        conn.deleteLater()
