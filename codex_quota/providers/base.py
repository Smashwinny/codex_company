"""Provider 协议与注册。

一个 Provider 是一个用量数据源（Codex、Kimi……）。
QuotaSnapshot.provider 标识来源，HUD 按 provider 分区渲染。

装配：default_providers() 读 providers.toml 应用开关，并挂载配置里的
预设 provider（如 deepseek）；CODEX_QUOTA_PROVIDERS 环境变量可最终过滤。
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from ..app_server import QuotaSnapshot
from .config import load_providers_config


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


def default_providers(config_path: str | None = None) -> list[Provider]:
    from .claude_code import ClaudeCodeProvider, credentials_path
    from .codex import CodexProvider
    from .deepseek import DeepSeekProvider
    from .kimi import KimiProvider, find_kimi_bin
    from .openrouter import OpenRouterProvider

    cfg = load_providers_config(config_path)

    def enabled(name: str) -> bool:
        return bool(cfg.get(name, {}).get("enabled", True))

    providers: list[Provider] = []
    if enabled("codex"):
        providers.append(CodexProvider())
    # 本地凭证存在则自动启用；配置显式声明也加载（无凭证时显示引导错误）
    if enabled("claude") and (credentials_path() is not None
                              or cfg.get("claude", {}).get("type") == "claude"):
        providers.append(ClaudeCodeProvider())
    if enabled("kimi") and find_kimi_bin() is not None:
        providers.append(KimiProvider())

    # 配置中的密钥型预设 provider
    for name, section in cfg.items():
        if not section.get("enabled", True):
            continue
        if section.get("type") == "deepseek":
            providers.append(DeepSeekProvider(
                api_key=section.get("api_key"),
                display_name=section.get("display_name") or "DeepSeek",
            ))
        elif section.get("type") == "openrouter":
            providers.append(OpenRouterProvider(
                api_key=section.get("api_key"),
                display_name=section.get("display_name") or "OpenRouter",
            ))
        elif section.get("type") == "manual":
            from .manual import ManualProvider

            providers.append(ManualProvider.from_section(section))

    filt = os.environ.get("CODEX_QUOTA_PROVIDERS")
    if filt:
        allow = {x.strip() for x in filt.split(",") if x.strip()}
        providers = [p for p in providers if p.name in allow]
    return providers
