"""Provider 协议与注册。

一个 Provider 是一个用量数据源（Codex、Kimi……）。
QuotaSnapshot.provider 标识来源，HUD 按 provider 分区渲染。
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from ..app_server import QuotaSnapshot


@runtime_checkable
class Provider(Protocol):
    name: str          # 机器标识："codex" / "kimi"
    display_name: str  # 展示名："Codex" / "Kimi"

    def fetch(self) -> QuotaSnapshot:
        """取一次用量快照。失败抛异常，由调度层兜底。"""
        ...

    def close(self) -> None:
        """释放资源（如 kimi web 保活进程）。应用退出时调用。"""
        ...


def default_providers() -> list[Provider]:
    """按环境装配可用 provider；CODEX_QUOTA_PROVIDERS="codex,kimi" 可过滤。"""
    from .codex import CodexProvider
    from .kimi import KimiProvider, find_kimi_bin

    providers: list[Provider] = [CodexProvider()]
    if find_kimi_bin() is not None:
        providers.append(KimiProvider())

    filt = os.environ.get("CODEX_QUOTA_PROVIDERS")
    if filt:
        allow = {x.strip() for x in filt.split(",") if x.strip()}
        providers = [p for p in providers if p.name in allow]
    return providers
