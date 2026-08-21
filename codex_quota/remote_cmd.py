"""手机远程命令：ntfy 命令主题收到的文本 → 动作 + 回复正文。

纯逻辑、无 Qt 依赖（在监听线程里运行，也能无头测试）。支持的命令
（大小写/空格不敏感）：
- "url" / "地址"：回推当前访问地址
- "列表" / "list" / "状态"：回推各限流窗口的重置提醒开关状态
- 其他关键词：匹配 provider 名 + 窗口标签/数字（如 kimi5、codex本周）
  或桶名片段（如 spark），切换匹配窗口的重置提醒开关并回推新状态
"""

from __future__ import annotations

import re
from typing import Optional

from .state import ProviderView, key_excluded, toggle_window, window_keys

_WORD_RE = re.compile(r"[a-z0-9一-鿿]+")


def _norm(s: str) -> str:
    return re.sub(r"[\s·\-_]+", "", s.lower())


def _match_windows(text: str, views: list[ProviderView]) -> list[tuple[str, str]]:
    """关键词 → 命中的 (key, 标签) 列表。

    规则：provider 命中 + 标签/数字命中（"kimi5"→ Kimi 的 5小时），
    或桶名片段（≥4 字符的词，如 spark）单独命中 → 该桶全部窗口。
    """
    ntext = _norm(text)
    matches: list[tuple[str, str]] = []
    for v in views:
        snap = v.state.snapshot
        if snap is None:
            continue
        provider_hit = _norm(v.name) in ntext or _norm(v.display_name) in ntext
        for limit in snap.limits:
            bucket = limit.limit_name or limit.limit_id
            # 桶名按原始分隔符拆词（"GPT-5.3-Codex-Spark"→gpt/5/3/codex/spark）；
            # 去掉 provider 名本身，防止 "codex" 片段全场命中
            parts = [p for p in _WORD_RE.findall(bucket.lower())
                     if len(p) >= 4 and p != _norm(v.name)]
            bucket_hit = any(p in ntext for p in parts)
            for w in (limit.primary, limit.secondary):
                if w is None:
                    continue
                digits = "".join(ch for ch in w.label if ch.isdigit())
                label_hit = (_norm(w.label) in ntext
                             or bool(digits) and digits in ntext)
                if (provider_hit and label_hit) or bucket_hit:
                    prefix = (v.display_name if limit is snap.primary_limit
                              else f"{v.display_name} · {bucket}")
                    matches.append((f"{v.name}:{bucket}:{w.label}",
                                    f"{prefix} · {w.label}"))
    return matches


def handle_command(msg: str, views: list[ProviderView], settings,
                   url: Optional[str] = None) -> tuple[str, str]:
    """处理一条手机命令，返回 (回复正文, 点击跳转URL)。正文空串 = 不回复。"""
    text = _norm(msg)
    if "url" in text or "地址" in text:
        if url:
            return (f"📱 手机访问地址（点通知直接打开）：\n{url}", url)
        return ("手机访问未开启，没有可用地址", "")

    if any(k in text for k in ("列表", "list", "状态")):
        excludes = set(settings.get("notify_excludes") or [])
        items = window_keys(views)
        if not items:
            return ("暂无额度数据", "")
        lines = [f"{'🔕' if key_excluded(k, excludes) else '🔔'} {label}"
                 for k, label in items]
        return ("重置提醒状态（🔔=推送 / 🔕=不推）：\n" + "\n".join(lines), "")

    matches = _match_windows(msg, views)
    if not matches:
        items = window_keys(views)
        known = " / ".join(label for _, label in items) or "（暂无数据）"
        return ("🤔 没认出命令。可用：\n"
                "· url — 获取访问地址\n"
                "· 列表 — 查看重置提醒开关\n"
                "· 关键词切换提醒，如 kimi5、spark、codex本周\n"
                f"当前窗口：{known}", "")

    excludes = set(settings.get("notify_excludes") or [])
    all_keys = [k for k, _ in window_keys(views)]
    lines = []
    for key, label in matches:
        was_on = not key_excluded(key, excludes)
        excludes = toggle_window(excludes, key, all_keys)
        lines.append(f"{'🔕' if was_on else '🔔'} {label}："
                     f"重置提醒已{'关闭' if was_on else '开启'}")
    settings.set("notify_excludes", sorted(excludes))
    return ("\n".join(lines), "")
