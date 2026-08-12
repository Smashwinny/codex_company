"""pytest 全局配置：Qt 测试统一用 offscreen 平台，无需真实显示服务器。"""

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 隔离磁盘缓存：避免测试读到真实 ~/.cache/codex-quota/last-good.json
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="codex-quota-test-cache-")
