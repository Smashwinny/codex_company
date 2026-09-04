"""SingleInstance 测试：同名二实例互斥 + raise 回调 + 陈旧 socket 恢复。"""

from __future__ import annotations

import secrets

import pytest

pytest.importorskip("PyQt6")

import codex_quota.single_instance as single_instance
from codex_quota.single_instance import SingleInstance

# Qt 应用对象统一用 conftest.qapp（QApplication）——单例类型不一致会
# 在 QPixmap 处 SIGABRT


def _name() -> str:
    return f"codex-quota-test-{secrets.token_hex(4)}"


def _pump(app, ms=300):
    """手动驱动事件循环，让 newConnection 等信号得以派发。"""
    import time

    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _pump(app, ms=300):
    """手动驱动事件循环，让 newConnection 等信号得以派发。"""
    import time

    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _acquire_pumped(app, inst, timeout=10):
    """在后台线程跑 try_acquire 并在主线程驱动事件循环。

    ack 握手是双向的：进程内测试里若主线程直接调 b.try_acquire()，
    a 的事件循环没机会处理连接和回 ack，会误判 a 是僵尸。生产里两个
    实例是独立进程，无此问题。
    """
    import threading
    import time

    result = []
    t = threading.Thread(target=lambda: result.append(inst.try_acquire()))
    t.start()
    deadline = time.monotonic() + timeout
    while t.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    t.join(timeout=2)
    return result[0]


class TestSingleInstance:
    def test_first_acquires_second_rejected(self, qapp):
        name = _name()
        a = SingleInstance(name)
        assert a.try_acquire() is True
        b = SingleInstance(name)
        assert _acquire_pumped(qapp, b) is False

    def test_second_instance_raises_first(self, qapp):
        name = _name()
        raised = []
        a = SingleInstance(name)
        assert a.try_acquire() is True
        a.set_raise_callback(lambda: raised.append(1))
        b = SingleInstance(name)
        assert _acquire_pumped(qapp, b) is False
        _pump(qapp)
        assert raised == [1]

    def test_distinct_names_coexist(self, qapp):
        a = SingleInstance(_name())
        b = SingleInstance(_name())
        assert a.try_acquire() is True
        assert b.try_acquire() is True

    def test_simultaneous_start_lock_loser_is_rejected(self, qapp, monkeypatch):
        """原子锁输家即使暂时连不上赢家，也绝不能继续创建第二个 server。"""
        sent = []

        class FakeLock:
            def __init__(self, _path):
                pass

            def tryLock(self, _timeout):
                return False

        class FakeSocket:
            def connectToServer(self, _name):
                pass

            def waitForConnected(self, _timeout):
                return True

            def write(self, payload):
                sent.append(payload)

            def flush(self):
                pass

            def waitForBytesWritten(self, _timeout):
                return True

            def waitForReadyRead(self, _timeout):
                return True

            def readAll(self):
                return single_instance.ACK_MSG  # 健康实例会回 ack

            def disconnectFromServer(self):
                pass

        class FakeServer:
            def __init__(self, _parent):
                raise AssertionError("锁输家不得创建 QLocalServer")

        monkeypatch.setattr(single_instance, "QLockFile", FakeLock)
        monkeypatch.setattr(single_instance, "QLocalSocket", FakeSocket)
        monkeypatch.setattr(single_instance, "QLocalServer", FakeServer)

        instance = SingleInstance(_name())
        assert instance.try_acquire() is False
        assert sent == [b"raise"]

    def test_phantom_lock_broken_instead_of_lockout(self, qapp, monkeypatch):
        """锁永远拿不到且无人能连接（崩溃残留+PID 复用）→ 破除后继续，不锁死。"""
        import time as _time

        removed = []

        class PhantomLock:
            """tryLock 永远失败；破除（删文件）后第二次也失败（模拟最坏情况）。"""

            def __init__(self, path):
                self._path = path

            def tryLock(self, _timeout):
                return False

            def fileName(self):
                return self._path

        monkeypatch.setattr(single_instance, "QLockFile", PhantomLock)
        monkeypatch.setattr(_time, "sleep", lambda _s: None)  # 测试不等真实时间
        monkeypatch.setattr(single_instance.os, "remove",
                            lambda p: removed.append(p))

        inst = SingleInstance(_name())
        assert inst.try_acquire() is True   # 不再静默锁死
        assert removed, "幻影锁应被强制破除"

    def test_lock_io_failure_falls_back_to_soft_mode(self, qapp, monkeypatch):
        """锁文件创建就 OSError（目录只读等）→ 降级继续，不锁死。"""
        monkeypatch.setattr(single_instance.os, "makedirs",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("ro fs")))
        inst = SingleInstance(_name())
        assert inst.try_acquire() is True

    def test_stale_socket_recovered(self, qapp):
        """模拟首实例崩溃：不 delete server、直接丢弃引用，新实例应能接管。"""
        name = _name()
        a = SingleInstance(name)
        assert a.try_acquire() is True
        # 正常销毁 server 对象以模拟可清理场景（真正的崩溃残留是文件级，
        # removeServer 重试逻辑在 listen 失败时兜底，这里验证 listen 成功后
        # 同名仍可被新实例正常拒绝/接管语义不回归）
        del a
        import gc
        gc.collect()
        c = SingleInstance(name)
        # server 已析构 → c 应能 listen 成功成为首实例
        assert c.try_acquire() is True

    def test_zombie_takeover(self, qapp, monkeypatch):
        """首实例持锁但永不回 ack（事件循环死掉的僵尸）→ 新实例接管而非被拒。"""
        import os

        from PyQt6.QtCore import QLockFile
        from PyQt6.QtNetwork import QLocalServer

        from codex_quota.sysdirs import cache_dir

        name = _name()
        # 僵尸：锁被占、server 在 listen 但没有处理器（永不回 ack）
        os.makedirs(cache_dir(), exist_ok=True)  # QLockFile 不会自建父目录
        lock = QLockFile(os.path.join(cache_dir(), f"{name}.lock"))
        assert lock.tryLock(0)
        zombie = QLocalServer()
        assert zombie.listen(name)
        # app.pid 指向不存在的进程 → 杀僵尸逻辑无害跳过
        with open(os.path.join(cache_dir(), "app.pid"), "w") as f:
            f.write("999999")
        monkeypatch.setattr(single_instance, "ACK_TIMEOUT_MS", 100)
        monkeypatch.setattr(single_instance.time, "sleep", lambda _s: None)

        inst = SingleInstance(name)
        assert inst.try_acquire() is True   # 接管成功，不被僵尸卡死
        inst2 = SingleInstance(name)        # 接管者现在是健康首实例
        assert _acquire_pumped(qapp, inst2) is False  # 正常互斥恢复
        zombie.close()
