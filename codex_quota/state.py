"""状态存储：当前快照、上次成功快照（含磁盘缓存）、错误状态与新鲜度。

磁盘缓存：成功快照写入 $XDG_CACHE_HOME/codex-quota/last-good.json
（默认 ~/.cache/codex-quota/），保留 24 小时。重启应用或查询持续失败时
仍可展示最近一次成功的数据并标注"陈旧"。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from .app_server import QuotaSnapshot, snapshot_from_dict, snapshot_to_dict
from .sysdirs import cache_dir

CACHE_MAX_AGE_S = 24 * 3600  # 超过 24 小时的缓存视为无效


def default_cache_path(provider: str = "codex") -> str:
    """每个 provider 一个缓存文件；codex 沿用 last-good.json（向后兼容）。
    目录由 sysdirs.cache_dir() 按平台分发。"""
    filename = "last-good.json" if provider == "codex" else f"last-good-{provider}.json"
    return os.path.join(cache_dir(), filename)


@dataclass
class ViewState:
    """UI 消费的状态：要么有快照可显示，要么只有错误信息。"""

    snapshot: Optional[QuotaSnapshot] = None
    stale: bool = False        # True 表示展示的是上次成功的旧数据
    error: Optional[str] = None
    refreshing: bool = False

    @property
    def fetched_at(self) -> Optional[float]:
        return self.snapshot.fetched_at if self.snapshot else None


@dataclass
class ProviderView:
    """一个 provider 的展示视图（托盘聚合用）。"""

    name: str
    display_name: str
    state: ViewState


def window_keys(views: list[ProviderView]) -> list[tuple[str, str]]:
    """所有限流窗口的 (key, 展示标签)，key 形如 "codex:GPT-5.3-Codex-Spark:本周"。

    窗口级粒度（"5小时"/"本周"是桶内的窗口而非独立桶），供勾选菜单
    （主模型显示 / 重置提醒）与手机远程命令共用；无数据的 provider 不出现。
    """
    items: list[tuple[str, str]] = []
    for v in views:
        snap = v.state.snapshot
        if snap is None:
            continue
        for limit in snap.limits:
            bucket = limit.limit_name or limit.limit_id
            # 主限额不重复桶名（与托盘摘要行的展示习惯一致）
            prefix = (v.display_name if limit is snap.primary_limit
                      else f"{v.display_name} · {bucket}")
            for w in (limit.primary, limit.secondary):
                if w is None:
                    continue
                items.append((f"{v.name}:{bucket}:{w.label}",
                              f"{prefix} · {w.label}"))
    return items


def key_excluded(key: str, excludes) -> bool:
    """排除匹配：窗口键完全相等，或被旧版桶级条目（"provider:桶"）前缀覆盖。"""
    return any(key == ex or key.startswith(ex + ":") for ex in excludes)


def toggle_window(excludes, key: str, all_keys: list[str]) -> set:
    """切换某窗口的排除状态，返回新集合。

    被桶级前缀条目覆盖时，把该桶条目拆成兄弟窗口逐项排除（保持兄弟状态不变），
    再放开目标窗口——保证"只开这一个"语义精确。
    """
    excludes = set(excludes)
    if key_excluded(key, excludes):
        for ex in list(excludes):
            if key == ex:
                excludes.discard(ex)
            elif key.startswith(ex + ":"):
                excludes.discard(ex)
                for k2 in all_keys:
                    if k2.startswith(ex + ":") and k2 != key:
                        excludes.add(k2)
    else:
        excludes.add(key)
    return excludes


class StateStore:
    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path or default_cache_path()
        self._last_good: Optional[QuotaSnapshot] = None
        self.state = ViewState()

    # ---------- 磁盘缓存 ----------

    def load_cached(self, now: Optional[float] = None) -> ViewState:
        """启动时调用：读入 24h 内的缓存快照并标记为陈旧。文件损坏/过期则忽略。"""
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                snap = snapshot_from_dict(json.load(f))
        except (OSError, ValueError, KeyError, TypeError):
            return self.state
        if not snap.limits:
            return self.state
        age = (now if now is not None else time.time()) - snap.fetched_at
        if age > CACHE_MAX_AGE_S:
            return self.state
        self._last_good = snap
        self.state = ViewState(snapshot=snap, stale=True, error=None, refreshing=False)
        return self.state

    def _write_cache(self, snap: QuotaSnapshot) -> None:
        """原子写入（临时文件 + rename），失败静默——缓存只是优化，不是关键路径。"""
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._cache_path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot_to_dict(snap), f)
            os.replace(tmp, self._cache_path)
        except OSError:
            pass

    # ---------- 状态迁移 ----------

    def begin_refresh(self) -> ViewState:
        self.state.refreshing = True
        return self.state

    def on_success(self, snap: QuotaSnapshot) -> ViewState:
        self._last_good = snap
        self._write_cache(snap)
        self.state = ViewState(snapshot=snap, stale=False, error=None, refreshing=False)
        return self.state

    def on_error(self, message: str) -> ViewState:
        # 有旧快照则降级展示陈旧数据，没有则纯错误态
        self.state = ViewState(
            snapshot=self._last_good,
            stale=self._last_good is not None,
            error=message,
            refreshing=False,
        )
        return self.state

    # ---------- 展示辅助 ----------

    @staticmethod
    def freshness_text(fetched_at: Optional[float], now: Optional[float] = None) -> str:
        from .i18n import tr

        if fetched_at is None:
            return tr("无数据")
        age = max(0.0, (now if now is not None else time.time()) - fetched_at)
        if age < 60:
            return tr("{n} 秒前").format(n=int(age))
        if age < 3600:
            return tr("{n} 分钟前").format(n=int(age // 60))
        return tr("{n} 小时前").format(n=int(age // 3600))
