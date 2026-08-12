"""当前模型信息：解析 ~/.codex/config.toml 的顶层键。

Python 3.10 没有 tomllib，这里只读取首个 [section] 之前的顶层
`key = "value"` 行（model / model_reasoning_effort / service_tier），
不做完整 TOML 解析。配置文件每次刷新重读，用户改模型即时生效。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .app_server import codex_home

# service_tier 中视为"快速"的取值；模型名含 spark 也算（如 GPT-5.3-Codex-Spark）
FAST_TIERS = {"fast", "priority"}


@dataclass
class ModelInfo:
    model: str
    effort: Optional[str] = None          # low / medium / high / xhigh …
    service_tier: Optional[str] = None    # default / fast / priority …

    @property
    def is_fast(self) -> bool:
        return "spark" in self.model.lower() or (self.service_tier or "").lower() in FAST_TIERS

    @property
    def display(self) -> str:
        """徽章文本：model · effort（tier 非 default 时也带上）。"""
        parts = [self.model]
        if self.effort:
            parts.append(self.effort)
        if self.service_tier and self.service_tier.lower() not in ("default", ""):
            parts.append(self.service_tier)
        return " · ".join(parts)


_KEY_RE = r'^{key}\s*=\s*"([^"]*)"\s*(?:#.*)?$'


def read_model_info(path: Optional[str] = None) -> Optional[ModelInfo]:
    path = path or os.path.join(codex_home(), "config.toml")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    top = text.split("\n[", 1)[0]  # 只取首个 section 之前的顶层区域

    def _get(key: str) -> Optional[str]:
        m = re.search(_KEY_RE.format(key=re.escape(key)), top, re.M)
        return m.group(1) if m else None

    model = _get("model")
    if not model:
        return None
    return ModelInfo(
        model=model,
        effort=_get("model_reasoning_effort"),
        service_tier=_get("service_tier"),
    )
