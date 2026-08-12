"""pytest 全局配置：Qt 测试统一用 offscreen 平台，无需真实显示服务器。"""

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 兜底隔离磁盘缓存：避免测试读到真实 ~/.cache/codex-quota/last-good.json
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="codex-quota-test-cache-")


@pytest.fixture(autouse=True)
def _isolated_cache_home(tmp_path, monkeypatch):
    """每个测试独立的 XDG_CACHE_HOME，杜绝缓存文件跨测试泄漏。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
