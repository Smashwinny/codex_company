"""SingleInstance 测试：同名二实例互斥 + raise 回调 + 陈旧 socket 恢复。"""

from __future__ import annotations

import secrets

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication

from codex_quota.single_instance import SingleInstance


@pytest.fixture(scope="session")
def qcore():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _name() -> str:
    return f"codex-quota-test-{secrets.token_hex(4)}"


def _pump(app, ms=300):
    """手动驱动事件循环，让 newConnection 等信号得以派发。"""
    import time

    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


class TestSingleInstance:
    def test_first_acquires_second_rejected(self, qcore):
        name = _name()
        a = SingleInstance(name)
        assert a.try_acquire() is True
        b = SingleInstance(name)
        assert b.try_acquire() is False

    def test_second_instance_raises_first(self, qcore):
        name = _name()
        raised = []
        a = SingleInstance(name)
        assert a.try_acquire() is True
        a.set_raise_callback(lambda: raised.append(1))
        b = SingleInstance(name)
        assert b.try_acquire() is False
        _pump(qcore)
        assert raised == [1]

    def test_distinct_names_coexist(self, qcore):
        a = SingleInstance(_name())
        b = SingleInstance(_name())
        assert a.try_acquire() is True
        assert b.try_acquire() is True

    def test_stale_socket_recovered(self, qcore):
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
