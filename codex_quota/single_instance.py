r"""单实例：QLocalServer/QLocalSocket 跨平台实现。

第二实例连接成功 → 发消息后退出；首实例收到任意消息即 raise 已有窗口。
- Windows：命名管道 \\.\pipe\<name>，按会话隔离，进程死亡即消失
- POSIX：/tmp 下 unix socket，多用户机器按 uid 区分；崩溃残留 socket 文件
  会导致 listen 失败（AddressInUseError）→ removeServer 后重试一次
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QLockFile
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .sysdirs import cache_dir

IS_WINDOWS = sys.platform == "win32"


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
        """True = 首个实例（已 listen）；False = 已有实例在跑（已通知其 raise）。"""
        # QLocalServer 在 Windows 使用命名管道；两个进程完全同时启动时，同名
        # pipe 的多个 server instance 都可能 listen 成功，不能单独充当互斥锁。
        # QLockFile::tryLock(0) 是跨进程原子操作，先用它决定唯一赢家；本地
        # server 只负责让后续启动者激活已有窗口。
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            self._lock = QLockFile(os.path.join(cache_dir(), f"{self._name}.lock"))
            acquired = self._lock.tryLock(0)
        except OSError:
            acquired = False
        if not acquired:
            # 赢家可能刚拿锁、尚未来得及 listen；尽量通知，但无论通知是否成功
            # 都必须拒绝本实例，才能保持单实例语义。
            self._notify_existing(timeout_ms=500)
            return False

        # 兼容尚未使用 QLockFile 的旧实例：它没有占锁，但可能已在监听。
        if self._notify_existing():
            self._release_lock()
            return False

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if self._server.listen(self._name):
            return True

        # 两个进程可能同时完成上面的首次探测：赢家刚 listen，输家此时才
        # listen 失败。先重新连接赢家，不能把失败忽略后也当成首实例。
        if self._notify_existing(timeout_ms=500):
            self._release_lock()
            return False

        if not IS_WINDOWS:
            # POSIX 崩溃可能留下 unix socket 文件；确认没有活实例后删掉重试。
            QLocalServer.removeServer(self._name)
            if self._server.listen(self._name):
                return True
            # 清理陈旧文件后仍可能恰好输给另一个启动者，再通知一次。
            self._notify_existing(timeout_ms=500)

        # Windows 命名管道随进程死亡自动消失；listen 仍失败意味着通知通道
        # 不可用。释放原子锁并 fail closed，避免未知错误下放行第二个实例。
        self._release_lock()
        return False

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
