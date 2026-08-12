"""pytest 全局配置：Qt 测试统一用 offscreen 平台，无需真实显示服务器。"""

import os
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 兜底隔离磁盘缓存：避免测试读到真实 ~/.cache/codex-quota/last-good.json
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="codex-quota-test-cache-")
# 固定测试语言为中文（各 en 用例会显式 set_language 覆盖）
os.environ["CODEX_QUOTA_LANG"] = "zh"


@pytest.fixture(autouse=True)
def _isolated_xdg_dirs(tmp_path, monkeypatch):
    """每个测试独立的 XDG 目录与 CODEX_HOME，杜绝缓存/设置/配置跨测试泄漏。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    yield
    from codex_quota.i18n import set_language

    set_language(None)  # 语言覆写不泄漏到下一个测试
