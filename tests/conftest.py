"""pytest 全局配置：Qt 测试统一用 offscreen 平台，无需真实显示服务器。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
