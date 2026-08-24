r"""单实例：QLocalServer/QLocalSocket 跨平台实现 + QLockFile 原子互斥。

第二实例连接成功 → 发消息后退出；首实例收到任意消息即 raise 已有窗口。
- Windows：命名管道 \\.\pipe\<name>，按会话隔离，进程死亡即消失
- POSIX：/tmp 下 unix socket，多用户机器按 uid 区分；崩溃残留 socket 文件
  会导致 listen 失败（AddressInUseError）→ removeServer 后重试一次
- 可用性优先：锁文件损坏/幻影锁（崩溃残留 + PID 复用）/监听失败都不允许
  让应用永远起不来——降级为软互斥或无互斥继续，并记 WARNING 日志
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


class SingleInstance(QObject):
    def __init__(self, name: str = "codex-quota", parent: Optional[QObject] = None):
        super().__init__(parent)
        if not IS_WINDOWS:
            name = f"{name}-{os.getuid()}"  # POSIX 多用户机按用户隔离
        self._name = name
        self._lock: Optional[QLockFile] = None
        self._server: Optional[QLocalServer] = None
        self._on_raise: Optional[Callable[[], None]] = None

    def try_acquire(self) -> bool:
        """True = 可以启动（首实例或降级继续）；False = 已有实例在跑（已通知）。"""
        # QLocalServer 在 Windows 使用命名管道；两个进程完全同时启动时，同名
        # pipe 的多个 server instance 都可能 listen 成功，不能单独充当互斥锁。
        # QLockFile::tryLock(0) 是跨进程原子操作，先用它决定唯一赢家。
        acquired = False
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            self._lock = QLockFile(os.path.join(cache_dir(), f"{self._name}.lock"))
            acquired = self._lock.tryLock(0)
        except OSError as exc:
            # 锁文件不可用（目录只读/磁盘满等）：宁可无锁运行，不可静默锁死
            logger.warning("单实例锁不可用（%s），降级为软互斥继续", exc)
            self._lock = None
            acquired = True

        if not acquired:
            # 并发启动：赢家拿锁后毫秒级就会 listen，轮询等它就位。
            # 超时仍无人能连 = 崩溃残留锁 + PID 复用的"幻影锁"（QLockFile
            # 认为锁仍被持有，但管道那头没有任何实例）——破除，否则用户
            # 每次启动都被静默拒绝且无任何提示。
            for attempt in range(5):
                if self._notify_existing(timeout_ms=500):
                    return False
                if attempt < 4:
                    time.sleep(0.3)
            logger.warning("单实例锁无人能连接（疑似崩溃残留+PID 复用），强制破除")
            self._break_stale_lock()
            try:
                acquired = self._lock is not None and self._lock.tryLock(0)
            except OSError:
                acquired = False
            if not acquired:
                logger.warning("破除后仍拿不到锁，降级为软互斥继续")
                self._lock = None
                acquired = True

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if self._server.listen(self._name):
            return True

        # listen 失败：赢家（或旧版无锁实例）刚完成 listen——先通知它激活窗口
        if self._notify_existing(timeout_ms=500):
            self._release_lock()
            return False
        if not IS_WINDOWS:
            # POSIX 崩溃可能留下 unix socket 文件；确认没有活实例后删掉重试
            QLocalServer.removeServer(self._name)
            if self._server.listen(self._name):
                return True
            if self._notify_existing(timeout_ms=500):
                self._release_lock()
                return False

        # 既 listen 不了也没有可通知的实例：可用性优先——无互斥也要能启动
        logger.warning("单实例监听失败（%s），以无互斥方式继续",
                       self._server.errorString())
        self._release_lock()
        return True

    def _break_stale_lock(self) -> None:
        """幻影锁（锁在但无人能连接）的破处：直接删锁文件。"""
        if self._lock is None:
            return
        try:
            os.remove(self._lock.fileName())
        except OSError as exc:
            logger.warning("破除锁文件失败: %s", exc)

    def _notify_existing(self, timeout_ms: int = 200) -> bool:
        """连接已有实例并请求 raise；连接成功返回 True。"""
        sock = QLocalSocket()
        sock.connectToServer(self._name)
        if sock.waitForConnected(timeout_ms):
            sock.write(b"raise")
            sock.flush()
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return True
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
            conn.deleteLater()
        if self._on_raise is not None:
            self._on_raise()
