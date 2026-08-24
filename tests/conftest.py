"""pytest 全局配置：Qt 测试统一用 offscreen 平台，无需真实显示服务器。"""

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 兜底隔离磁盘缓存：避免测试读到真实 ~/.cache/codex-quota/last-good.json
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="codex-quota-test-cache-")
# 固定测试语言为中文（各 en 用例会显式 set_language 覆盖）
os.environ["CODEX_QUOTA_LANG"] = "zh"
# 默认只启用 codex provider，防止测试拉起真实 kimi web 服务器
os.environ["CODEX_QUOTA_PROVIDERS"] = "codex"


class FakeProvider:
    """测试用内存 provider：fetch 返回固定快照或抛错。"""

    def __init__(self, name="codex", display_name=None, snapshot=None, error=None):
        self.name = name
        self.display_name = display_name or name.capitalize()
        self._snapshot = snapshot
        self._error = error
        self.closed = False

    def fetch(self):
        if self._error:
            raise RuntimeError(self._error)
        return self._snapshot

    def close(self):
        self.closed = True


@pytest.fixture(scope="session")
def qapp():
    """session 级唯一 QApplication。

    必须统一走这里：Qt 应用对象是单例，若别处先建了 QCoreApplication
    （非 GUI 子类），`QApplication.instance() or QApplication([])` 会拿到
    错误类型，一碰 QPixmap 直接 SIGABRT（实测 pytest 跨文件组合时复现）。
    """
    from PyQt6.QtWidgets import QApplication

    inst = QApplication.instance()
    assert inst is None or isinstance(inst, QApplication), \
        f"已有非 GUI application 实例: {type(inst).__name__}（Qt 测试统一用 conftest.qapp）"
    return inst or QApplication([])


def codex_snapshot():
    """带 provider 字段的 codex 快照（基于真实响应 fixture）。"""
    from codex_quota.app_server import parse_rate_limits_response
    from tests.test_parse import NOW, REAL_RESPONSE

    snap = parse_rate_limits_response(REAL_RESPONSE, now=NOW)
    snap.provider = "codex"
    return snap


@pytest.fixture(autouse=True)
def _isolated_xdg_dirs(tmp_path, monkeypatch):
    """每个测试独立的 XDG 目录与 CODEX_HOME，杜绝缓存/设置/配置跨测试泄漏。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    yield
    from codex_quota.i18n import set_language

    set_language(None)  # 语言覆写不泄漏到下一个测试
