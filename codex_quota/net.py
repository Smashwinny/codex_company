"""HTTPS 工具：证书上下文的跨平台统一出口。

Windows / PyInstaller 冻结版下 OpenSSL 默认不读系统证书库（3.13 前），
urllib 直连 https 会 certificate verify failed——用 certifi 的 CA 包兜底
（已列入依赖，PyInstaller 有 certifi 的内置 hook 会打进包）。
"""

from __future__ import annotations

import ssl


def https_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
